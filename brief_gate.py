"""
brief_gate.py — Ф3 анти-повтора утреннего брифа: ЧИСТАЯ логика мозга.

Две вещи, где корректность критична и позитив-контроли обязательны:

1. band_position — «ниже/выше нормы» = относительно ЛИЧНОЙ полосы (trailing-60d
   перцентиль), НЕ абсолюта. На разреженных данных (n<min_obs) → 'unknown', не врём.

2. fsm_next — recurrence FSM per semantic_key. ЧИСТАЯ функция от (prev, today):
   один и тот же вход → один и тот же выход → идемпотентна при catch-up (риск #1).
   Гасит хронику: найдено каждый день → показано день-1, подавлено внутри кулдауна,
   редкий recap на истечении, затем decay (иначе хроника возвращается ежедневно). Ухудшение пробивает
   кулдаун. 'resolved' — ТОЛЬКО если раньше показывали.

ЖИВОЙ за флагом MORNING_BRIEF_GATE (вкл. в проде оба тенанта, 2026-07). Здесь —
только ЧИСТАЯ логика + пороги; персистентность (чтение строки date<today, upsert) —
в brief_state. Пороги конфигурируемы (аргументы), дефолты — из замысла v3 (§8).
"""
from __future__ import annotations

import statistics

# ── Конфигурируемые пороги (дефолты замысла v3 §8) ──────────────────────────
BAND_MIN_OBS = 14      # меньше — данных мало, полоса не считается
BAND_LOW_P = 10        # перцентиль «ниже полосы»
BAND_HIGH_P = 90       # перцентиль «выше полосы»
COOLDOWN_DAYS = 30     # как редко хроника может повторяться (владелец 2026-07-13: не чаще раза в месяц)
MAX_RECAPS = 2         # сколько раз показать recap на истечении кулдауна, потом decay
# Шумовой пол ухудшения (Э6, нить profile-staleness). НЕ клиническая величина, а ГРАНИЦА
# КВАНТА: severity дискретна шагом ~0.1 (0.3→0.4→0.5→0.6 у структурных провайдеров),
# а env (UV/жара) льёт непрерывную severity из сырого индекса (env_context.py:77) и даёт
# суб-квантовую дрожь до 0.03 — три UV-worsened 0.44↔0.47 были повторами. 0.05 лежит в
# зазоре (0.03, 0.1): глушит дрожь, пропускает любой настоящий шаг уровня. Оракул числа —
# сама дискретность severity (§9 класс 3, представление), не дрейфующая норма. Seed-дефолт,
# override — config_db 'brief.worse_severity_delta'. ТОЛЬКО routine: safety не трогаем.
WORSE_SEVERITY_DELTA = 0.05

_SHOWN_STATES = ("new_alert", "still_active", "worsened")


def _percentile(xs: list[float], p: float) -> float:
    """Линейная интерполяция на отсортированном списке (p в 0..100)."""
    k = (len(xs) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(xs) - 1)
    if f == c:
        return xs[f]
    return xs[f] + (xs[c] - xs[f]) * (k - f)


def band_position(series, value, low_p=BAND_LOW_P, high_p=BAND_HIGH_P,
                  min_obs=BAND_MIN_OBS) -> dict:
    """Где `value` относительно личной полосы `series` (trailing окно).
    Возвращает band ∈ {'below','within','above','unknown'} + z + пороги.
    n<min_obs → 'unknown' (уважаем разреженные данные — не «ниже p10» на 3 точках)."""
    xs = sorted(v for v in series if v is not None)
    n = len(xs)
    if n < min_obs or value is None:
        return {"band": "unknown", "n": n, "z": None, "p_low": None, "p_high": None}
    p_low = _percentile(xs, low_p)
    p_high = _percentile(xs, high_p)
    sd = statistics.pstdev(xs)
    z = round((value - statistics.mean(xs)) / sd, 2) if sd else 0.0
    band = "below" if value < p_low else "above" if value > p_high else "within"
    return {"band": band, "n": n, "z": z, "p_low": p_low, "p_high": p_high}


def _st(state, show, reason, last_shown, recaps, days_since_shown=None) -> dict:
    """days_since_shown — сколько дней прошло с ФАКТИЧЕСКОГО показа; заполняется только
    на возврате после resolved. Полосу FSM не знает (это policy), поэтому решение
    «то же самое или новость» принимает gate_decision, которому видна card.lane."""
    return {"state": state, "show": show, "reason": reason,
            "last_shown": last_shown, "recaps": recaps,
            "days_since_shown": days_since_shown}


def fsm_next(prev: dict | None, today: dict,
             cooldown_days: int = COOLDOWN_DAYS, max_recaps: int = MAX_RECAPS) -> dict:
    """ЧИСТЫЙ переход recurrence FSM.

    prev: {state, last_shown(date|None), recaps} — состояние на строке date<today,
          или None (никогда не встречалось).
    today: {present: bool, worse: bool, date: date}.
    Возвращает {state, show, reason, last_shown, recaps} — что записать за today.

    Идемпотентность: чистая функция prev→today. Дважды за ту же дату (catch-up) → тот же
    результат, потому что prev берётся строго ДО today (не из своей же сегодняшней строки).
    """
    p_state = (prev or {}).get("state", "none")
    last_shown = (prev or {}).get("last_shown")
    recaps = (prev or {}).get("recaps", 0)
    present = today["present"]
    worse = today["worse"]
    d = today["date"]

    was_shown = p_state in _SHOWN_STATES

    if not present:
        # «Прошло» говорим только про то, что показали в ЭТОМ эпизоде.
        # new_alert/still_active пишется и карточке, отсеянной порогом или слотом:
        # одного состояния недостаточно для вывода о показе.
        # Факт показа приносит вызывающий (prev.shown_in_episode);
        # без поля остаётся грубый дефолт «показывали хоть когда-то» (last_shown).
        # Ключ всё равно уходит в resolved, иначе JOIN переоткрыл бы его.
        if was_shown and not (prev or {}).get("shown_in_episode", last_shown is not None):
            return _st("resolved", False, "resolved:не_показывали_в_эпизоде", last_shown, recaps)
        if was_shown:
            # last_shown/recaps сохраняем: исчезновение и повторное появление
            # карточки не должны обнулять её кулдаун. Иначе цикл
            # показали → исчезло → resolved → вернулось вновь разрешит показ
            # раньше срока. Разрешение находки не отменяет память о доставке.
            return _st("resolved", True, "resolved:вернулось_в_норму", last_shown, recaps)
        return _st("none", False, "absent:никогда_не_показывали", None, 0)

    if p_state in ("none", None):
        return _st("new_alert", True, "new_alert:first_seen", d, 0)

    if p_state == "resolved":
        # Вернулось после разрешения. Для safety это новость (показатель снова ушёл из
        # нормы), для routine — тот же текст, что человек уже читал. Полосы FSM не знает,
        # поэтому отдаёт возраст показа, а решает gate_decision.
        days = (d - last_shown).days if last_shown else None
        return _st("new_alert", True, "new_alert:вернулось_после_resolve", d, 0,
                   days_since_shown=days)

    if worse:
        return _st("worsened", True, "worsened:severity_up_пробивает_кулдаун", d, 0)

    days = (d - last_shown).days if last_shown else cooldown_days
    if days < cooldown_days:
        return _st("still_active", False, f"suppress_cooldown:{days}d<{cooldown_days}", last_shown, recaps)
    if recaps < max_recaps:
        return _st("still_active", True, "still_active:recap_на_истечении", d, recaps + 1)
    return _st("still_active", False, "suppress_decayed:recaps_исчерпаны", last_shown, recaps)


# ── Two-lane gate + slot budget ─────────────────────────────────────────────
# Мультипликатив relevance×novelty×importance ЗАПРЕЩЁН (занулял бы safety). Две
# независимые полосы; ранг ВНУТРИ полосы, не поперёк. severity = основной сигнал
# допуска (relevance/importance пока = severity; уточнение — позже).
ROUTINE_SEVERITY_THETA = 0.35   # routine ниже — не тащим факт ради живости (N4)
SAFETY_SEVERITY_FLOOR = 0.5     # safety-lane: ниже — не safety-сигнал

# Категория слота по провайдеру. Защищённый слот: у каждой категории свой лимит,
# поэтому здоровье (много pulse/lever) НЕ задушит контекст общей важностью.
PROVIDER_CATEGORY = {
    "safety_net": "safety",
    "drift": "pulse",
    "sleep_agent": "pulse",
    "recovery": "pulse",   # композит восстановления: дедуп со drift/sleep через общий pulse-слот
    "genome": "context",
    "env": "environment",
    "calendar": "plan",
    "trail": "activity",
    "food": "food",
    "hypotheses": "lever",
}
# Свои слоты для «удовольствия/активности» (2026-07-14, запрос владельца): раньше тропа
# конкурировала с поездкой (plan), еда — с геномом (context), море — с UV (environment),
# и почти всегда проигрывали. Теперь: activity (тропа+море) и food — отдельные каналы,
# не давят и не давятся безопасностью/планами. UV/жара/пыль остаются в environment.
SLOT_BUDGET = {"pulse": 1, "lever": 1, "context": 1, "environment": 1, "plan": 1,
               "question": 1, "activity": 2, "food": 1}


# 'resolved' осмыслен у измерительного канала: показатель может вернуться к норме.
# Провайдер-предложение нормы не имеет: исчезновение рекомендации не означает
# улучшения здоровья. Разбиение категорий полное и под тестом
# test_category_partition_is_total: новая категория без явного отнесения роняет тест.
_SUGGESTION_CATEGORIES = frozenset({"food", "activity", "plan"})
_MEASUREMENT_CATEGORIES = frozenset({"safety", "pulse", "context", "environment", "lever"})


def _category(provider: str, semantic_key: str) -> str:
    # Море (sea:*) — провайдер env, но это приглашение к активности, не UV/жара:
    # уводим в activity, чтобы не конкурировало за environment-слот с UV. Единственный
    # дом правила sea→activity — здесь; category_of и is_suggestion зовут его, копии нет.
    if str(semantic_key).startswith("sea:"):
        return "activity"
    return PROVIDER_CATEGORY.get(provider, "context")


def category_of(card) -> str:
    return _category(getattr(card, "provider", ""), getattr(card, "semantic_key", ""))


def is_suggestion(provider: str, semantic_key: str = "") -> bool:
    """Провайдер-предложение — у него нет нормы, к которой можно «вернуться»,
    поэтому 'resolved' для него шум (Э3). Категорию считает _category (общий дом)."""
    return _category(provider, str(semantic_key)) in _SUGGESTION_CATEGORIES


def is_worsened(severity, prev_severity, lane, delta: float = WORSE_SEVERITY_DELTA) -> bool:
    """Ухудшилось ли настолько, чтобы пробить кулдаун (Э6, нить profile-staleness).

    routine: True только если severity выросла на >= delta — шумовой пол против суб-
    квантовой дрожи env (UV 0.46→0.47 — не новость). safety: любой рост — новость,
    порог НЕ применяется (там пропустить нельзя). Нет одного из значений → не ухудшение.
    Чистая функция; delta приходит из config_db у вызывающего (brief_state), seed —
    WORSE_SEVERITY_DELTA."""
    if severity is None or prev_severity is None:
        return False
    if lane == "safety":
        return severity > prev_severity
    return severity - prev_severity >= delta


def gate_decision(card, fsm_result: dict, theta: float = ROUTINE_SEVERITY_THETA,
                  cooldown_days: int = COOLDOWN_DAYS) -> dict:
    """FSM-show + полоса → предварительный статус. 'candidate' = допущена к слоту.
    Две независимые полосы: safety (severity≥floor) и routine (severity≥theta).
    FSM уже реализовал bypass кулдауна (new_alert/worsened/recap → show=True)."""
    if not fsm_result["show"]:
        reason = fsm_result["reason"]
        status = ("suppressed_cooldown"
                  if ("cooldown" in reason or "decayed" in reason)
                  else "suppressed_gate")
        return {"status": status, "gate_reason": reason}
    # Возврат после resolved внутри кулдауна: для routine это тот же текст, который
    # человек уже читал, — молчим. Для safety молчать нельзя: показатель снова ушёл из
    # нормы, и это новость независимо от того, когда мы говорили о нём в прошлый раз.
    _since = fsm_result.get("days_since_shown")
    if _since is not None and card.lane != "safety" and _since < cooldown_days:
        return {"status": "suppressed_cooldown",
                "gate_reason": f"suppress_cooldown_after_resolve:{_since}d<{cooldown_days}"}
    sev = card.severity if card.severity is not None else 1.0
    if card.lane == "safety":
        if sev < SAFETY_SEVERITY_FLOOR:
            return {"status": "suppressed_gate", "gate_reason": f"safety_sev{sev}<{SAFETY_SEVERITY_FLOOR}"}
        return {"status": "candidate", "gate_reason": f"safety:{fsm_result['reason']}"}
    if sev < theta:
        return {"status": "suppressed_gate", "gate_reason": f"sev{sev}<theta{theta}"}
    return {"status": "candidate", "gate_reason": f"routine:{fsm_result['reason']}"}


def select_slots(candidates: list, budget: dict | None = None) -> list:
    """Из candidate-карточек — финальный показ. Safety преэмптивно (все).
    Routine соревнуется ВНУТРИ своей категории по severity, каждая категория —
    свой лимит (защищённый контекст: здоровье не займёт слот контекста)."""
    budget = budget or SLOT_BUDGET
    chosen = [c for c in candidates if c.lane == "safety"]
    by_cat: dict[str, list] = {}
    for c in candidates:
        if c.lane != "safety":
            by_cat.setdefault(category_of(c), []).append(c)
    for cat, cards in by_cat.items():
        # Ранг ВНУТРИ категории — по relevance (континуальная магнитуда), фолбэк на
        # severity. env квантовал severity в дискретный ярлык и перенёс магнитуду в
        # relevance (env_context._quantize_env), поэтому UV/жара/пыль по-прежнему
        # соревнуются за единственный env-слот по остроте, а не по одинаковому ярлыку.
        # Провайдеры без relevance (None) ранжируются как раньше — по severity.
        ranked = sorted(
            cards,
            key=lambda c: (c.relevance if c.relevance is not None else (c.severity or 0)),
            reverse=True)
        chosen.extend(ranked[: budget.get(cat, 0)])
    return chosen
