"""§9 поток F — первый срез: клиническая норма читается из absolute_thresholds, не из литерала.

Пинит контур seed→таблица→чтение (get_threshold). Тесты подсказок еды жили вместе с
retired morning_report.py и удалены с ним 30.09.

NB: фикстура `db` сама сеет канонические absolute_thresholds (поток F, 2026-06-28 —
тем же _seed_absolute_thresholds(), что и боевой init_db). Поэтому `_seed_floor` ниже
переопределяет значение через INSERT OR REPLACE, а не вставляет с нуля (иначе коллизия
по UNIQUE(metric, direction, band_label, variant)).
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def _seed_floor(metric: str, value: float, variant: str = "",
                kind: str = "absolute", baseline=None):
    import health_db as hdb
    with hdb.get_conn() as conn:
        # OR REPLACE: фикстура уже засеяла канонические пороги; тест переопределяет нужный.
        conn.execute(
            "INSERT OR REPLACE INTO absolute_thresholds (metric, direction, value, reason_template, "
            "source, source_date, active, kind, baseline, band_label, variant) "
            "VALUES (?, 'floor', ?, '', 'test', '2026-06-28', 1, ?, ?, '', ?)",
            (metric, value, kind, baseline, variant),
        )


def test_get_threshold_reads_table_and_raises_when_missing(db):
    import health_db as hdb
    _seed_floor("readiness", 65.0)
    assert hdb.get_threshold("readiness", "floor") == 65.0
    # §9: отсутствие порога — громкий отказ, не тихий дефолт
    with pytest.raises(KeyError):
        hdb.get_threshold("nonexistent_metric", "floor")


