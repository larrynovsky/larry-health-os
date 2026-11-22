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
               "calendar": True},   # поездки — ТОЛЬКО из Google Calendar (calendar_client), решение владельца 15.11
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
    status: str = "n/a"   # event-топики: planned (есть поездки в календаре) | unknown
    events: list = field(default_factory=list)    # travel: поездки из календаря с датами (дом планов)


# Все поездки в горизонте календаря (фетчер берёт 30 дней — их единицы, не сотни). 18.11: срез до 6
# прятал купленный рейс 17.12, а шапка «авторитетна» — бот отвечал «рейса нет». Потолок — от разрастания.
_CALENDAR_TRIPS_MAX = 25


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


def _calendar_trips() -> list[dict]:
    """Поездки из календаря (дом планов): ближайшие, с датами. Прошедшие календарь уже отбросил,
    отменённые в Google — фетчер (status=cancelled). Нет календаря → пусто, вера по памяти."""
    try:
        import calendar_client
        evs = calendar_client.get_travel_events()
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("beliefs: календарь недоступен — %s", exc)
        return []
    evs = [e for e in evs if e.get("start_date")]
    return sorted(evs, key=lambda e: e["start_date"])


def _resolve_event(topic: str, spec: dict) -> Belief:
    """Event-топик (поездка): ТОЛЬКО из календаря — решение владельца 15.11 («Убрать»). Заметки памяти
    по словам давали ложное («короткая ночь: везти друзей в аэропорт» → «поездка планируется»,
    «first night at home after flight» → «отменено» для купленной поездки). Статус события —
    по датам календаря (_render_calendar); «состоялось» система не утверждает никогда."""
    events = _calendar_trips()
    return Belief(topic, None, None, None, False, status="planned" if events else "unknown",
                  events=events)


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
_AUTH_RU = {"device_gps": "по GPS телефона, 5:00 локально"}  # авторитетный источник → показываем его, не голое «подтверждено»


def _render_calendar(topic: str, events: list) -> list[str]:
    """Поездки из календаря: даты и статус по датам. Идущая по календарю — НЕ доказательство,
    что вылет состоялся (план ≠ факт), поэтому так и пишем."""
    from _time_inject import get_today
    today = get_today().isoformat()
    out = []
    for e in events[:_CALENDAR_TRIPS_MAX]:
        a, z = e["start_date"], e.get("end_date") or e["start_date"]
        span = _ru_span(a, z)
        st = ("по календарю идёт сейчас — что поездка началась, не подтверждено" if a <= today
              else "планируется")
        out.append(f"- {_TOPIC_RU.get(topic, topic)} по календарю: {e['title']}, {span} — {st}")
    if len(events) > _CALENDAR_TRIPS_MAX:
        out.append(f"- {_TOPIC_RU.get(topic, topic)}: … и ещё {len(events) - _CALENDAR_TRIPS_MAX} позже — "
                   f"спроси query_calendar(travel_only), прежде чем говорить, что поездки нет")
    try:
        import calendar_client
        hz = calendar_client.horizon_date()
    except Exception:  # silent-ok: горизонт — пояснение к списку, без него список остаётся верным
        hz = None
    if hz:
        head = (f"выше — ВСЕ поездки календаря до {hz:%d.%m.%Y}" if 0 < len(events) <= _CALENDAR_TRIPS_MAX
                else f"поездки календаря загружены до {hz:%d.%m.%Y}" if events
                else f"по календарю поездок до {hz:%d.%m.%Y} нет")
        out.append(f"- {_TOPIC_RU.get(topic, topic)}: {head}; позже календарь не загружен — "
                   f"отсутствие поездки там НЕ значит отмену")
    return out


def _ru_span(a: str, z: str) -> str:
    d = lambda s: f"{s[8:10]}.{s[5:7]}.{s[:4]}"
    return d(a) if a == z else f"{d(a)}–{d(z)}"


def render_header() -> str:
    """Авторитетная строка ТЕКУЩЕЙ ВЕРЫ для промпта — важнее прошлых реплик и догадок из
    транскрипта (адресует петлю самоусиления БЕЗ мутации истории): единый свежий источник
    по volatile-топикам, модель берёт ЭТО при конфликте с историей."""
    lines = []
    for t, b in all_beliefs().items():
        if TOPICS[t].get("kind") == "event":
            lines += _render_calendar(t, b.events)
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
