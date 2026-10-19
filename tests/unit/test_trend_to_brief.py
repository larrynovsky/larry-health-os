"""Среднее за окно — в бриф, а не сообщением; серия подряд — сообщением (решение владельца 19.10).

19.10 пришло отдельное сообщение «среднее оценки сна за 14 дней ниже личного порога» — картина
за две недели, которая за утро не меняется, а утренний бриф это правило вовсе не читал.
Оракулы идут путём из базы: правило в trend_thresholds → evaluate_safety_limits → джоб бота
и → assemble_cards брифа. Краснеют, если среднее снова пойдёт сообщением, если бриф перестанет
его получать или если серию подряд заглушит тот же предикат.
"""
from datetime import date, timedelta

import pytest

from tests.unit.test_limits_push import _run_job, _send_on, db  # noqa: F401 — фикстура базы

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _oncology_tenant(monkeypatch):
    """Правило 14 дней гейтится состоянием тенанта (condition_key='oncology'); сид ставит гейт
    заново при каждом init_db, который провайдеры брифа зовут по дороге, — подменяем состояние,
    а не правило."""
    import clinical_kb
    monkeypatch.setattr(clinical_kb, "active_conditions", lambda *a, **k: {"oncology"})


def _low_sleep_two_weeks(h, avg_active=1, cons_active=0):
    """14 ночей оценки сна 60 при пороге 65: среднее ниже порога, правила включаем выборочно.
    Однодневные полы оценки сна выключены — проверяется только оконный путь."""
    with h.get_conn() as c:
        c.execute("UPDATE absolute_thresholds SET active=0 WHERE metric='sleep_score'")
        c.execute("UPDATE trend_thresholds SET active=0")
        c.execute("UPDATE trend_thresholds SET active=?, value=65 "
                  "WHERE metric='sleep_score' AND mode='avg'", (avg_active,))
        c.execute("UPDATE trend_thresholds SET active=?, value=65 "
                  "WHERE metric='sleep_score' AND mode='consecutive'", (cons_active,))
        for i in range(1, 15):
            d = str(date.today() - timedelta(days=i))
            c.execute("INSERT OR IGNORE INTO daily_metrics(date) VALUES (?)", (d,))
            c.execute("UPDATE daily_metrics SET sleep_score=60 WHERE date=?", (d,))
        c.commit()


def test_window_average_is_not_a_message(db):  # noqa: F811
    _low_sleep_two_weeks(db)
    from services.recommendations import evaluate_safety_limits
    fired = [f for f in evaluate_safety_limits()["fired"] if "тренд" in f.get("target", "")]
    assert fired, "правило среднего не сработало — тест не проверяет ничего"
    assert all(f["push"] is False for f in fired), "среднее за окно снова идёт сообщением"
    _send_on()
    assert _run_job({}).call_count == 0, "бот отправил сообщение о среднем за окно"


def test_window_average_reaches_the_brief(db):  # noqa: F811
    _low_sleep_two_weeks(db)
    import brief_pipeline
    cards = brief_pipeline.assemble_cards(date.today())
    trend = [c for c in cards if c.provider == "trend"]
    assert [c.semantic_key for c in trend] == ["trend:sleep_score:14d:avg"], \
        "бриф не получил среднее за окно — у правила не осталось пути к человеку"
    assert "Оценка сна" in trend[0].evidence_summary or "Sleep score" in trend[0].evidence_summary


def test_consecutive_series_still_a_message(db):  # noqa: F811
    _low_sleep_two_weeks(db, avg_active=0, cons_active=1)
    from services.recommendations import evaluate_safety_limits
    fired = [f for f in evaluate_safety_limits()["fired"] if "тренд" in f.get("target", "")]
    assert fired and all(f["push"] is True for f in fired), "серию подряд заглушили вместе со средним"


def test_window_average_survives_slot_selection(db):  # noqa: F811
    """Замер 19.10: в общем pulse-слоте карту среднего вытеснял дрейф с severity 0.9 —
    до человека она бы не дошла. У неё свой слот."""
    _low_sleep_two_weeks(db)
    import brief_cards as bc
    import brief_gate as bg
    import brief_pipeline
    trend = [c for c in brief_pipeline.assemble_cards(date.today()) if c.provider == "trend"]
    strong_drift = bc.from_drift({"metric": "active_kcal", "direction": "down", "severity": "critical",
                                  "delta_pct": -70, "streak_days": 20, "current_7d": 1, "baseline_30d": 3})
    chosen = bg.select_slots(trend + [strong_drift])
    assert {c.provider for c in chosen} == {"trend", "drift"}, "дрейф вытеснил среднее из брифа"
