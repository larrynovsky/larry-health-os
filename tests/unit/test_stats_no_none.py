"""get_stats не отдаёт None-значений: пустое среднее — отсутствующий ключ (нить document-intake, 24.09).

Читатели пишут `stats.get('avg_hrv', '—')`; default не срабатывает на None-значение, и у тенанта
без данных в промпты агентов уезжало «None / None» (замер 24.09 на чистом тенанте).
"""
from __future__ import annotations

from datetime import date

import pytest

pytestmark = pytest.mark.unit


def test_empty_period_has_no_none_values(db):
    import metrics_db
    s = metrics_db.get_stats(7, date(2026, 9, 12))
    assert None not in s.values(), s
    assert s.get("avg_hrv", "—") == "—"
    assert s.get("n_days") == 0


def test_filled_period_keeps_values(db):
    db.add_daily_metrics("2026-09-10", sleep_total=7.5, hrv=41.0)
    import metrics_db
    s = metrics_db.get_stats(7, date(2026, 9, 12))
    assert s["avg_hrv"] == 41.0 and s["n_days"] == 1
    assert "avg_steps" not in s          # шагов не было — ключа нет, не None
