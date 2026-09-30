"""
Ф3 brief_gate — band-anchor + recurrence FSM. Pure unit, позитив-контроли.

RST: доказываем ПОВЕДЕНИЕ, которого happy-path не видит — хроника не сыплется
ежедневно, полоса относительна (не абсолют), ухудшение пробивает кулдаун,
'resolved' только если показывали, FSM идемпотентен (catch-up не удваивает).
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

import brief_cards as bc
import brief_gate as bg

pytestmark = pytest.mark.unit

_SHOW = {"show": True, "reason": "new_alert:first_seen"}
_SUPPRESS = {"show": False, "reason": "suppress_cooldown:2d<14"}


# ── band_position ───────────────────────────────────────────────────────────
def test_band_below_personal_p10():
    assert bg.band_position(list(range(20, 120)), 21)["band"] == "below"


def test_band_within():
    assert bg.band_position(list(range(20, 120)), 70)["band"] == "within"


def test_band_above():
    assert bg.band_position(list(range(20, 120)), 118)["band"] == "above"


def test_band_unknown_on_sparse():
    r = bg.band_position([50, 51, 52], 10)
    assert r["band"] == "unknown" and r["z"] is None


def test_band_relative_not_absolute():
    """Одно абсолютное значение — 'норма' для одного, 'провал' для другого."""
    low_sleeper = [25] * 50 + [30] * 50
    high_sleeper = [80] * 50 + [90] * 50
    assert bg.band_position(low_sleeper, 28)["band"] == "within"
    assert bg.band_position(high_sleeper, 28)["band"] == "below"


# ── fsm_next ────────────────────────────────────────────────────────────────
def _today(present, worse, d):
    return {"present": present, "worse": worse, "date": d}


def test_first_seen_shows():
    r = bg.fsm_next(None, _today(True, False, date(2026, 7, 13)))
    assert r["state"] == "new_alert" and r["show"] is True


def test_chronic_suppressed_within_cooldown():
    """Хроника каждый день → показано 1 раз, дальше молчит внутри кулдауна (анти-PER3)."""
    d0 = date(2026, 7, 1)
    st = bg.fsm_next(None, _today(True, False, d0))
    assert st["show"] is True
    shows = 0
    for i in range(1, 14):
        st = bg.fsm_next(st, _today(True, False, d0 + timedelta(days=i)))
        shows += st["show"]
    assert shows == 0


def test_recap_bounded_then_decay():
    """60 дней подряд присутствует, не хуже → не больше MAX_RECAPS показов (иначе мини-PER3)."""
    d0 = date(2026, 1, 1)
    st = bg.fsm_next(None, _today(True, False, d0))
    shows = []
    for i in range(1, 60):
        st = bg.fsm_next(st, _today(True, False, d0 + timedelta(days=i)))
        if st["show"]:
            shows.append(i)
    assert len(shows) <= bg.MAX_RECAPS


def test_worsening_pierces_cooldown():
    d0 = date(2026, 7, 1)
    st = bg.fsm_next(None, _today(True, False, d0))
    st = bg.fsm_next(st, _today(True, False, d0 + timedelta(days=2)))
    assert st["show"] is False
    st2 = bg.fsm_next(st, _today(True, True, d0 + timedelta(days=3)))
    assert st2["state"] == "worsened" and st2["show"] is True


def test_resolved_only_if_previously_shown():
    r = bg.fsm_next(None, _today(False, False, date(2026, 7, 13)))
    assert r["state"] == "none" and r["show"] is False
    shown = {"state": "new_alert", "last_shown": date(2026, 7, 1), "recaps": 0}
    r2 = bg.fsm_next(shown, _today(False, False, date(2026, 7, 13)))
    assert r2["state"] == "resolved" and r2["show"] is True


def test_fsm_idempotent_for_catch_up():
    prev = {"state": "still_active", "last_shown": date(2026, 7, 1), "recaps": 1}
    t = _today(True, False, date(2026, 7, 10))
    assert bg.fsm_next(prev, t) == bg.fsm_next(prev, t)


# ── two-lane gate + slot budget ─────────────────────────────────────────────
def _card(provider, lane, severity):
    return bc.Card(provider=provider, semantic_key=f"{provider}:x", lane=lane, severity=severity)


def test_gate_routine_trivial_suppressed():
    assert bg.gate_decision(_card("drift", "routine", 0.3), _SHOW)["status"] == "suppressed_gate"


def test_gate_routine_severe_candidate():
    assert bg.gate_decision(_card("genome", "routine", 0.9), _SHOW)["status"] == "candidate"


def test_gate_safety_floor():
    assert bg.gate_decision(_card("safety_net", "safety", 0.3), _SHOW)["status"] == "suppressed_gate"
    assert bg.gate_decision(_card("safety_net", "safety", 0.8), _SHOW)["status"] == "candidate"


def test_gate_suppressed_keeps_reason():
    d = bg.gate_decision(_card("genome", "routine", 0.9), _SUPPRESS)
    assert d["status"] == "suppressed_cooldown" and "cooldown" in d["gate_reason"]


def test_slots_safety_preemptive():
    safety = _card("safety_net", "safety", 0.8)
    chosen = bg.select_slots([safety] + [_card("drift", "routine", 0.9) for _ in range(5)])
    assert safety in chosen


def test_slots_protected_context_not_drowned():
    """5 pulse (severity 0.9) + 1 context (0.4): контекст получает свой слот,
    хотя по общей важности проиграл бы всем pulse. Здоровье не душит контекст."""
    pulses = [_card("drift", "routine", 0.9) for _ in range(5)]
    context = _card("genome", "routine", 0.4)
    cats = [bg.category_of(c) for c in bg.select_slots(pulses + [context])]
    assert cats.count("pulse") == 1
    assert cats.count("context") == 1


def test_slots_env_has_own_slot():
    """Среда (environment) не конкурирует с genome (context) — свой защищённый слот."""
    genome = _card("genome", "routine", 0.9)
    env = _card("env", "routine", 0.45)
    chosen = bg.select_slots([genome, env])
    assert genome in chosen and env in chosen


def test_marine_routed_to_activity_not_environment():
    """Море (sea:*) — категория activity, не конкурирует с UV за environment (2026-07-14)."""
    marine = bc.Card(provider="env", semantic_key="sea:temp:swimmable",
                     lane="routine", severity=0.4)
    assert bg.category_of(marine) == "activity"
    assert bg.category_of(_card("env", "routine", 0.6)) == "environment"
    assert bg.category_of(_card("trail", "routine", 0.5)) == "activity"
    assert bg.category_of(_card("food", "routine", 0.4)) == "food"


def test_activity_and_food_have_own_slots():
    """Запрос владельца: тропа+море (activity) и еда (food) пробиваются, не давятся
    планом (поездка), UV (environment) и геномом (context) — у каждого свой канал."""
    cal    = _card("calendar", "routine", 0.9)   # plan
    trail  = _card("trail", "routine", 0.5)       # activity
    uv     = _card("env", "routine", 0.6)         # environment
    genome = _card("genome", "routine", 0.9)      # context
    food   = _card("food", "routine", 0.35)       # food
    marine = bc.Card(provider="env", semantic_key="sea:temp:swimmable",
                     lane="routine", severity=0.4)  # activity
    chosen = bg.select_slots([cal, trail, uv, genome, food, marine])
    for c in (cal, trail, uv, genome, food, marine):
        assert c in chosen, f"{c.semantic_key} должен показаться в своём слоте"


# ── Э3: классификация категорий (предложение vs измерение) ───────────────────
def test_is_suggestion_food_activity_plan_and_sea():
    assert bg.is_suggestion("food")
    assert bg.is_suggestion("trail")                       # → activity
    assert bg.is_suggestion("calendar")                    # → plan
    assert bg.is_suggestion("env", "sea:temp:swimmable")   # море → activity


def test_is_not_suggestion_for_measurement_channels():
    assert not bg.is_suggestion("drift")        # pulse
    assert not bg.is_suggestion("safety_net")   # safety
    assert not bg.is_suggestion("genome")       # context
    assert not bg.is_suggestion("env")          # environment (UV/жара) — у него есть норма
    assert not bg.is_suggestion("hypotheses")   # lever


def test_category_partition_is_total():
    """Каждая категория, которую МОЖЕТ вернуть _category, отнесена ровно к одному классу
    (предложение | измерение). Новая запись в PROVIDER_CATEGORY без явного отнесения
    роняет тест — молчаливого дрейфа классификации нет (§9/§17)."""
    universe = set(bg.PROVIDER_CATEGORY.values()) | {"context", "activity"}
    assert bg._SUGGESTION_CATEGORIES.isdisjoint(bg._MEASUREMENT_CATEGORIES)
    missing = universe - bg._SUGGESTION_CATEGORIES - bg._MEASUREMENT_CATEGORIES
    assert not missing, f"не классифицированы: {missing}"
    assert universe == bg._SUGGESTION_CATEGORIES | bg._MEASUREMENT_CATEGORIES


# ── Э6: шумовой пол ухудшения (routine порог; safety любой рост) ─────────────
def test_worsened_routine_ignores_subquantum_jitter():
    """UV-дрожь 0.46→0.47 (0.01 < 0.05) — не ухудшение (это были 3 повтора)."""
    assert bg.is_worsened(0.47, 0.46, "routine") is False


def test_worsened_routine_fires_on_real_step():
    """drift 0.3→0.6 — настоящий шаг уровня, пробивает пол."""
    assert bg.is_worsened(0.6, 0.3, "routine") is True


def test_worsened_safety_any_increase_no_floor():
    """safety: рост 0.02 — новость, порог НЕ применяется (пропустить нельзя)."""
    assert bg.is_worsened(0.52, 0.50, "safety") is True
    assert bg.is_worsened(0.52, 0.50, "routine") is False   # тот же рост в routine — дрожь


def test_worsened_none_is_not_worse():
    assert bg.is_worsened(None, 0.5, "routine") is False
    assert bg.is_worsened(0.5, None, "routine") is False


def test_worsened_negative_control_delta_is_load_bearing():
    """ИСПОЛНЕННЫЙ негативный контроль: поднимаем пол выше настоящего скачка — routine-
    ухудшение обязано ПЕРЕСТАТЬ срабатывать, safety остаться. Без этого зелёный неотличим
    от кода, где пол игнорируется (§20)."""
    assert bg.is_worsened(0.6, 0.3, "routine", delta=0.05) is True      # признак жив
    assert bg.is_worsened(0.6, 0.3, "routine", delta=0.40) is False     # пол реально режет
    assert bg.is_worsened(0.6, 0.3, "safety", delta=0.40) is True       # safety мимо пола


# ── select_slots: ранг по relevance при равной severity (квантование env) ────
def test_slots_env_ranked_by_relevance_when_severity_ties():
    """После квантования env severity одинакова (ярлык 0.5), а слот environment=1.
    Побеждает бóльшая relevance (острота), не произвольный тай-брейк по равному ярлыку."""
    import brief_cards as bc
    lo = bc.Card(provider="env", semantic_key="weather:uv:high", lane="routine",
                 severity=0.5, relevance=0.46)
    hi = bc.Card(provider="env", semantic_key="weather:dust:elevated", lane="routine",
                 severity=0.5, relevance=0.60)
    chosen = bg.select_slots([lo, hi])
    keys = {c.semantic_key for c in chosen}
    assert "weather:dust:elevated" in keys and "weather:uv:high" not in keys


def test_slots_relevance_none_falls_back_to_severity():
    """Провайдеры без relevance (не env) ранжируются как раньше — по severity.
    Регрессионный контроль: квантование env не сломало ранг остальных."""
    import brief_cards as bc
    a = bc.Card(provider="drift", semantic_key="drift:a:down", lane="routine", severity=0.6)
    b = bc.Card(provider="drift", semantic_key="drift:b:down", lane="routine", severity=0.3)
    chosen = bg.select_slots([a, b])   # pulse budget=1
    keys = {c.semantic_key for c in chosen}
    assert "drift:a:down" in keys and "drift:b:down" not in keys
