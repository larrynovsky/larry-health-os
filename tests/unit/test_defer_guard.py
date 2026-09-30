"""
tests/unit/test_defer_guard.py — D-guard (finding 4, 5 июля).

DEFER прячет факт, полагая что он ДУБЛИРУЕТ авторитет. Риск: новый медфакт, ещё не в
авторитете (напр. свежая хирургия в анамнезе), был бы спрятан = потерян. Guard:
прятать только если авторитет ДЕМОНСТРАТИВНО содержит; иначе держать видимым.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def _add_proposal(db, fact_id, authority_source, action="DEFER"):
    db.execute(
        "INSERT INTO memory_consolidation_proposals "
        "(fact_id, action, authority_source, status) VALUES (?,?,?, 'pending')",
        (fact_id, action, authority_source),
    )


def test_defer_kept_when_authority_lacks_it(db):
    """patient_profile: containment свободного текста не доказать → факт НЕ прячем."""
    import memory_facts_db as mf
    import memory_consolidation as mc
    mc._ensure_proposals_table()
    fid = mf.save_fact("state", "операция X в 2016", key="surg1")
    _add_proposal(db, fid, "patient_profile")
    res = mc.apply_safe(dry_run=False)
    assert any(f["key"] == "surg1" for f in mf.get_facts("state")), \
        "новый медфакт без подтверждения в авторитете НЕ должен быть спрятан"
    assert res.get("DEFER_kept_no_authority", 0) >= 1


def test_defer_applied_when_genome_has_gene(db):
    """genome: ген реально в genetic_variants → факт-дубль можно спрятать."""
    import memory_facts_db as mf
    import memory_consolidation as mc
    mc._ensure_proposals_table()
    db.add_genetic_variant("rs1801133", gene="MTHFR")
    fid = mf.save_fact("state", "MTHFR variant pathogenic", key="mthfr_dup")
    _add_proposal(db, fid, "genome")
    mc.apply_safe(dry_run=False)
    assert not any(f["key"] == "mthfr_dup" for f in mf.get_facts("state")), \
        "дубль, реально присутствующий в авторитете, → спрятан"


def test_authority_contains_conservative_default(db):
    """Прямой юнит guard'а: неизвестный/profile источник → False (консервативно keep)."""
    import memory_consolidation as mc
    import health_db
    with health_db.get_conn() as c:
        assert mc._authority_contains(c, "patient_profile", "любой диагноз") is False
        assert mc._authority_contains(c, None, "текст") is False
        assert mc._authority_contains(c, "genome", "") is False
