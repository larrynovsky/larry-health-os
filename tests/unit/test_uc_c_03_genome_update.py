"""
UC-C-03 — Genome update monthly: значимые upward движения объясняются.

Источник: USE_CASES.md §3.C → UC-C-03.
Реализация: `genome_update_agent.py`.
Status: `partial`.

Полный e2e — с моком ClinVar + Anthropic. Здесь — smoke + базовая структура.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def test_genome_update_agent_module_imports():
    """Модуль импортируется (после clock-рефактора T-pre.1.4)."""
    import genome_update_agent
    assert genome_update_agent is not None


def test_genome_update_uses_get_today_not_date_today():
    """После рефактора T-pre.1.4 — date.today() в logic заменён на get_today()."""
    from pathlib import Path
    src = (Path(__file__).parents[2] / "genome_update_agent.py").read_text(encoding="utf-8")
    # logic-код использует get_today (не date.today)
    if "date.today()" in src:
        # Проверим — только в __main__?
        main_idx = src.find('if __name__ == "__main__":')
        before_main = src[:main_idx] if main_idx > 0 else src
        assert "date.today()" not in before_main, (
            "date.today() в logic-коде genome_update_agent.py — "
            "помешает тестам на staleness"
        )


def test_run_date_iso_format(clinvar_mock, db, clock):
    """
    Smoke на функцию записи в genome_update_log.
    `run_date` должно быть ISO YYYY-MM-DD.
    """
    clock.set("2026-05-08")
    import health_db
    health_db.save_genome_update_log({
        "run_date": str(clock.today()),
        "variants_checked": 100,
        "variants_changed": 0,
        "changes_json": "[]",
        "narrative": "",
        "sent_to_user": 0,
    })
    row = db.fetchone(
        "SELECT * FROM genome_update_log ORDER BY id DESC LIMIT 1"
    )
    assert row is not None
    assert row["run_date"] == "2026-05-08"
