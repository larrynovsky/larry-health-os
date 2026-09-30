"""beliefs.py — модель ВЕРЫ: текущая вера системы про volatile-топик (B, хребет-фундамент).

Volatile-топики (локация, travel, свежие метрики) могут копить противоречия:
состояния без supersede и синонимы travel_plans/upcoming_travel/travel_plan одного топика.
Здесь ЕДИНЫЙ резолвер: для топика собрать кандидатов из ВСЕХ источников, ранжировать
(confirmed > свежесть), вернуть ТЕКУЩУЮ веру + demoted-историю (траекторию не стираем).

На этом строятся: C (топик-супёрсид), D (модальность события), E (провенанс задаёт
confirmed), H (транскрипт как источник с confirmed=False). Инспектируемо → тесты бьют в
ВЕРУ, не в срез (анти «тест на прокси»).

Детерминизм: таксономия топиков — закрытый код-набор (структурно, не LLM-на-чтении).
Неподтверждённый факт ниже профиля → конфабуляция current_location=<город из болтовни> проигрывает профилю.
"""
from __future__ import annotations
from dataclasses import dataclass, field

_PROFILE_BASELINE = "0001-01-01"  # профиль — авторитетный, но «старый» → свежий confirmed факт его перекрывает

# Топик: kind ∈ value | event. fact_keys — синонимичные ключи одного топика.
# profile_path — путь в get_profile_context (авторитетный, confirmed). Durable-факты
# (диагноз, ремиссия, операция) НЕ топики — им резолв не нужен.
TOPICS: dict[str, dict] = {
    "location": {"kind": "value",
                 "fact_keys": ("current_location", "location"),
                 "profile_path": ("identity", "location"),
                 "authority_source": "device_gps"},  # GPS (5:00 локально) — единственный авторитет локации; conversation/backfill демот
    "travel": {"kind": "event",
               "fact_keys": ("travel_plans", "upcoming_travel", "travel_plan"),
               "state_patterns": ("рейс", "вылет", "flight", "поездк", "trip",
                                  "аэропорт", "перелёт")},
}


@dataclass
class Candidate:
    value: str
    source: str            # КАТЕГОРИЯ: profile | fact | state | transcript
    valid_from: str
    confirmed: bool
    fact_id: int | None = None
    origin: str | None = None   # DB-провенанс: device_gps | conversation | backfill | profile | arbiter_unverified


@dataclass
class Belief:
    topic: str
    value: str | None
    source: str | None
    valid_from: str | None
    confirmed: bool
    history: list = field(default_factory=list)   # demoted кандидаты (траектория, не стёрта)
    status: str = "n/a"   # event-топики: planned | occurred | cancelled | unknown


# Сигналы модальности события. Безопасный дефолт — planned: НЕ ставим occurred без
# ПОДТВЕРЖДЁННОГО сигнала (иначе воссоздаём баг «вылетел» из плана/догадки).
_CANCEL_SIGNALS = ("не улет", "не полет", "не состоя", "cancel", "отмен", "перенёс",
                   "перенес", "postpone", "дома", "at home", "не в отел")
_OCCUR_SIGNALS = ("улетел", "вылетел", "прилетел", "flew", "arrived", "в отеле", "in hotel")


def _is_confirmed(f: dict) -> bool:
    """Подтверждён ли факт/стейт. arbiter_unverified (E: конфабуляция ассистента) —
    НИКОГДА не confirmed, даже при confirmations>0 → резолвер его демотит."""
    if f.get("source") == "arbiter_unverified":
        return False
    return (f.get("confirmations") or 0) > 0


def _profile_candidate(spec: dict) -> Candidate | None:
    pp = spec.get("profile_path")
    if not pp:
        return None
    try:
        import health_db
        v = health_db.get_profile_context()
        for k in pp:
            v = v.get(k) if isinstance(v, dict) else None
        if v:
            return Candidate(str(v), "profile", _PROFILE_BASELINE, True, origin="profile")
    except Exception:  # silent-ok: профиль недоступен → нет кандидата, не падаем
        pass
    return None


def _fact_candidates(spec: dict) -> list[Candidate]:
    out: list[Candidate] = []
    try:
        import memory_facts_db as mf
        facts = mf.get_facts("fact")   # active only, subject=self
    except Exception:  # silent-ok: память недоступна → нет кандидатов
        return out
    keys = set(spec.get("fact_keys", ()))
    for f in facts:
        if f.get("key") in keys:
            out.append(Candidate(
                value=f.get("value", ""),
                source="fact",
                valid_from=(f.get("valid_from") or "")[:10],
                confirmed=_is_confirmed(f),
                fact_id=f.get("id"),
                origin=f.get("source"),
            ))
    return out


def _state_candidates(spec: dict) -> list[Candidate]:
    """Keyless-стейты, чей текст матчит паттерн топика (детерминировано, но брит­ко — v1)."""
    pats = tuple(p.lower() for p in spec.get("state_patterns", ()))
    if not pats:
        return []
    out: list[Candidate] = []
    try:
        import memory_facts_db as mf
        states = mf.get_facts("state")
    except Exception:  # silent-ok
        return out
    for s in states:
        v = s.get("value") or ""
        if any(p in v.lower() for p in pats):
            out.append(Candidate(v, "state", (s.get("valid_from") or "")[:10],
                                 _is_confirmed(s), s.get("id"), origin=s.get("source")))
    return out


def _resolve_event(topic: str, spec: dict) -> Belief:
    """Event-топик: значение + СТАТУС из свежайших сигналов. Безопасный дефолт planned —
    occurred только при ПОДТВЕРЖДЁННОМ occur-сигнале (не воссоздаём «вылетел» из догадки)."""
    cands = _fact_candidates(spec) + _state_candidates(spec)
    if not cands:
        return Belief(topic, None, None, None, False, status="unknown")
    ranked = sorted(cands, key=lambda c: c.valid_from, reverse=True)  # свежайшее первым
    status = "planned"
    for c in ranked:
        low = c.value.lower()
        if any(s in low for s in _CANCEL_SIGNALS):
            status = "cancelled"
            break
        if c.confirmed and any(s in low for s in _OCCUR_SIGNALS):
            status = "occurred"
            break
    top = ranked[0]
    return Belief(topic, top.value, top.source, top.valid_from, top.confirmed,
                  history=list(ranked[1:]), status=status)


def resolve(topic: str) -> Belief:
    """Текущая вера про топик. value-топик: ранг (confirmed, свежесть). event-топик:
    значение + модальность (planned/occurred/cancelled)."""
    spec = TOPICS[topic]
    if spec.get("kind") == "event":
        return _resolve_event(topic, spec)
    cands: list[Candidate] = []
    pc = _profile_candidate(spec)
    if pc:
        cands.append(pc)
    cands += _fact_candidates(spec)
    if not cands:
        return Belief(topic, None, None, None, False)
    # Авторитет источника: если у топика есть authority_source (напр. location→device_gps),
    # кандидат от него плывёт НАД профилем и confirmed — GPS главнее догадки из разговора.
    # Нет авторитетного кандидата → падаем на (confirmed, свежесть): профиль бьёт конфаб.
    auth = spec.get("authority_source")
    def _rank(c: Candidate):
        is_auth = 1 if (auth and c.origin == auth) else 0
        return (is_auth, 1 if c.confirmed else 0, c.valid_from)
    ranked = sorted(cands, key=_rank, reverse=True)
    top = ranked[0]
    top_is_auth = bool(auth and top.origin == auth)
    return Belief(topic, top.value,
                  (top.origin if top_is_auth else top.source),
                  top.valid_from, top.confirmed or top_is_auth,
                  history=list(ranked[1:]))


def all_beliefs() -> dict[str, Belief]:
    return {t: resolve(t) for t in TOPICS}


def authoritative_keys() -> set[str]:
    """Fact-ключи топиков с ВНЕШНИМ авторитетным источником (напр. location→device_gps).
    Консолидатор их НЕ отдаёт LLM: судьбу решает детерминированный резолвер (выше), а не
    догадка «по свежести» — иначе рождаются SUPERSEDE-карточки типа «город А vs город Б».
    Единый источник знания о том, какие ключи изъять."""
    out: set[str] = set()
    for spec in TOPICS.values():
        if spec.get("authority_source"):
            out.update(spec.get("fact_keys", ()))
    return out


def authoritative_source_for_key(key: str | None) -> str | None:
    """Если ключ принадлежит топику с внешним авторитетом — вернуть ЭТОТ источник
    (current_location/location → device_gps). Иначе None. Гвард записи (L3): такой ключ
    может писать только его источник; конфаб арбитра (source=conversation) отклоняется."""
    if not key:
        return None
    for spec in TOPICS.values():
        a = spec.get("authority_source")
        if a and key in spec.get("fact_keys", ()):
            return a
    return None


_TOPIC_RU = {"location": "локация", "travel": "поездка/перелёт"}
_STATUS_RU = {"planned": "планируется", "cancelled": "отменено/перенесено", "occurred": "состоялось"}
_AUTH_RU = {"device_gps": "по GPS телефона, 5:00 локально"}  # авторитетный источник → показываем его, не голое «подтверждено»


def render_header() -> str:
    """Авторитетная строка ТЕКУЩЕЙ ВЕРЫ для промпта — важнее прошлых реплик и догадок из
    транскрипта (адресует петлю самоусиления БЕЗ мутации истории): единый свежий источник
    по volatile-топикам, модель берёт ЭТО при конфликте с историей."""
    lines = []
    for t, b in all_beliefs().items():
        if TOPICS[t].get("kind") == "event":
            if b.status == "occurred":
                lines.append(f"- {_TOPIC_RU.get(t, t)}: состоялось")
            elif b.status in ("planned", "cancelled"):
                lines.append(f"- {_TOPIC_RU.get(t, t)}: НЕ состоялось (статус: "
                             f"{_STATUS_RU[b.status]}) — НЕ считай, что это произошло")
        elif b.value:
            if b.source in _AUTH_RU:
                lines.append(f"- {_TOPIC_RU.get(t, t)}: {b.value} ({_AUTH_RU[b.source]})")
            else:
                conf = "подтверждено" if b.confirmed else "НЕ подтверждено"
                lines.append(f"- {_TOPIC_RU.get(t, t)}: {b.value} ({conf})")
    if not lines:
        return ""
    return ("ТЕКУЩАЯ ВЕРА о пользователе (АВТОРИТЕТНО и АКТУАЛЬНО; важнее ЛЮБОГО другого "
            "раздела — профиля, фактов, истории диалога, прошлых своих реплик. Если что-либо "
            "в контексте противоречит — верь ЭТОМУ разделу и НЕ повторяй устаревшее):\n"
            + "\n".join(lines))


if __name__ == "__main__":
    for t, b in all_beliefs().items():
        print(f"{t}: {b.value!r} (source={b.source}, confirmed={b.confirmed}, "
              f"from={b.valid_from}); история={[c.value for c in b.history]}")
