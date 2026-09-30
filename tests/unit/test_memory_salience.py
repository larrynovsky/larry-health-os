"""
tests/unit/test_memory_salience.py — Ф4.5 сигналы салиентности (5 июля).

Устойчивые факты и реплики о симптомах — независимые придуманные примеры.
Они проверяют разные классы и не складываются в историю человека.

RST-риски:
  (A) is_durable_fact — обе стороны: ложный НЕГАТИВ (устойчивый факт истечёт = потеря)
      И ложный ПОЗИТИВ (метрика помечена never-decay = мусор навсегда). Тестируем оба:
      recall на синтетических фактах + precision на sleep/HRV-метриках.
  (B) is_symptom — recall на заново придуманных репликах о симптомах; метрики не путаем за симптом.
"""
from __future__ import annotations

import pytest

import memory_salience as ms

pytestmark = pytest.mark.unit


# ── (A) устойчивые факты — синтетика, по образцу на класс маркеров ─────────────
DURABLE = [
    "Carrier of a pathogenic variant in a lipid-metabolism gene",       # генетика (EN)
    "Генотип по гену лактазы определён, непереносимость подтверждена",  # генетика (RU)
    "Tumor marker panel is part of long-term follow-up",                # онкостатус
    "Remission confirmed at the last specialist review",                # онкостатус
    "Gastroscopy postponed by the gastroenterologist",                  # решение врача
    "Колоноскопия по плану наблюдения — раз в пять лет",                # план наблюдения
]
# метрики — НЕ должны попасть в never-decay (иначе не истекают = мусор)
TRANSIENT_METRICS = [
    "Last night sleep: 6.8 hours with 51 minutes deep sleep",
    "HRV today: 41ms — best metric in 30 days",
    "Deep sleep 62 minutes - best result in last 7 days",
    "Ночь была лучшей за последние 7 дней: 7.4ч сна, readiness 79",
    "VSR 38ms (norm: 35.0ms), readiness 76 — stable nervous system",
]


@pytest.mark.parametrize("t", DURABLE)
def test_durable_fact_recall(t):
    assert ms.is_durable_fact(t), f"ложный НЕГАТИВ (устойчивый факт истечёт!): {t!r}"


@pytest.mark.parametrize("t", TRANSIENT_METRICS)
def test_metrics_not_durable(t):
    assert not ms.is_durable_fact(t), f"ложный ПОЗИТИВ (метрика стала never-decay): {t!r}"


# ── (B) независимо придуманные реплики ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("t", [
    "В учебном примере персонаж описывает изжогу вечером",
    "Вымышленная реплика: меня беспокоит бессонница",
    "Персонаж заметил слабость после прогулки",
    "Пример для классификатора: появилась диарея",
    "Учебный пациент упоминает запор",
    "Герой сценария жалуется на вздутие",
    "Придуманная заметка: нет аппетита к ужину",
    "Персонаж говорит, что у него простуда",
    "В учебном диалоге упоминается грипп",
])
def test_symptom_recall(t):
    assert ms.is_symptom(t), f"симптом не распознан: {t!r}"


@pytest.mark.parametrize("t", TRANSIENT_METRICS)
def test_metric_not_symptom(t):
    assert not ms.is_symptom(t), f"метрику приняли за симптом: {t!r}"


# ── врезка в save_fact ────────────────────────────────────────────────────────
def test_save_fact_flags_durable_fact(db):
    import memory_facts_db as mf, health_db
    fid = mf.save_fact("state", "User has pathogenic GENEX variant", key="genex")
    with health_db.get_conn() as c:
        row = c.execute("SELECT critical_flag FROM memory_facts WHERE id=?", (fid,)).fetchone()
    assert row[0] == 1, "устойчивый факт должен быть never-decay"


def test_save_fact_leaves_metric_decayable(db):
    import memory_facts_db as mf, health_db
    fid = mf.save_fact("state", "HRV today 41ms, deep sleep 62m", key="m1")
    with health_db.get_conn() as c:
        row = c.execute("SELECT critical_flag FROM memory_facts WHERE id=?", (fid,)).fetchone()
    assert row[0] == 0, "транзиентная метрика должна оставаться истекаемой"
