"""L2 integration: backfill_effect_alleles v4 (genome strand-fix Ф2 + het-palindromic).

Проверяет: extract_ref_alts (ClinVar-first), compute_for_variant, и run()
end-to-end на временной БД с ЗАМОКАННЫМ myvariant (без сети). Ключевые
инварианты: significance/prev_significance не трогаются, идемпотентность,
COALESCE-защита effect_allele от затирания ре-аннотацией, het-palindromic
разрешается системно через compute_for_variant (не пост-процессом).
"""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT))

import pytest

import health_db
import backfill_effect_alleles as bf
from genome_annotator import extract_ref_alts

pytestmark = pytest.mark.integration


def _hit(query, *, clinvar=None, dbsnp=None, vcf=None):
    h = {"query": query}
    if clinvar is not None:
        h["clinvar"] = clinvar
    if dbsnp is not None:
        h["dbsnp"] = dbsnp
    if vcf is not None:
        h["vcf"] = vcf
    return h


# ── extract_ref_alts ─────────────────────────────────────────────────────────

def test_extract_clinvar_first_multiallelic_collapses():
    """rsHET-подобный: 2 хита (G>C без clinvar, G>A с clinvar) → ClinVar
    выигрывает, остаётся один alt A."""
    hits = [
        _hit("rsHET", dbsnp={"ref": "G", "alt": "C"}),
        _hit("rsHET", clinvar={"ref": "G", "alt": "A"}, dbsnp={"ref": "G", "alt": "A"}),
    ]
    ref, alts, source, agg = extract_ref_alts(hits)
    assert (ref, alts, source, agg) == ("G", ["A"], "clinvar", "ok")


def test_extract_source_conflict_on_two_refs():
    hits = [
        _hit("rsX", clinvar={"ref": "G", "alt": "A"}),
        _hit("rsX", clinvar={"ref": "C", "alt": "T"}),
    ]
    _, _, _, agg = extract_ref_alts(hits)
    assert agg == "source_conflict"


def test_extract_no_source():
    assert extract_ref_alts([_hit("rsN")])[3] == "no_source"


def test_extract_dbsnp_fallback_when_no_clinvar():
    ref, alts, source, agg = extract_ref_alts([_hit("rsD", dbsnp={"ref": "C", "alt": "T"})])
    assert (ref, alts, source, agg) == ("C", ["T"], "dbsnp_vcf", "ok")


# ── compute_for_variant ──────────────────────────────────────────────────────

def test_compute_resolved_hetero():
    hits = [_hit("rsHET", clinvar={"ref": "G", "alt": "A"})]
    allele, ref, assembly, status = bf.compute_for_variant("AG", hits)
    assert (allele, ref, assembly, status) == ("A", "G", "GRCh37", "resolved")


def test_compute_palindromic_null():
    """Гомозиготный palindromic (CC) → NULL как прежде, не затронут хет-фиксом."""
    hits = [_hit("rsP", clinvar={"ref": "C", "alt": "G"})]
    allele, ref, assembly, status = bf.compute_for_variant("CC", hits)
    assert allele is None and assembly is None and status == "palindromic"


def test_compute_palindromic_het_resolved():
    """Гетерозиготный palindromic + HGVS в clinical_summary → palindromic_het_resolved."""
    hits = [_hit("rsPAL_HET", clinvar={"ref": "C", "alt": "G"})]
    allele, ref, assembly, status = bf.compute_for_variant(
        "CG", hits,
        clinical_summary="NM_000410.4(HFE):c.187C>G (p.His63Asp)",
    )
    assert allele == "G"
    assert ref == "C"
    assert assembly is None
    assert status == "palindromic_het_resolved"


def test_compute_palindromic_het_resolved_via_ref_fallback():
    """Нет HGVS — complement(ref из hits) → palindromic_het_resolved (FTO-кейс)."""
    hits = [_hit("rsFTO", clinvar={"ref": "T", "alt": "A"})]
    allele, ref, assembly, status = bf.compute_for_variant(
        "TA", hits, clinical_summary=None,
    )
    # ref="T" из hits → complement="A" → "A" in "TA" → resolved
    assert allele == "A"
    assert ref == "T"
    assert assembly is None
    assert status == "palindromic_het_resolved"


def test_compute_palindromic_het_resolved_db_ref_takes_priority():
    """DB ref_allele передаётся явно — берётся он, а не из hits."""
    hits = [_hit("rsX", clinvar={"ref": "C", "alt": "G"})]
    # clinical_summary пустой, ref_allele="C" явно → complement="G" in "CG"
    allele, _, _, status = bf.compute_for_variant(
        "CG", hits, clinical_summary=None, ref_allele="C",
    )
    assert allele == "G"
    assert status == "palindromic_het_resolved"


def test_compute_multiallelic_null():
    hits = [_hit("rsM", clinvar={"ref": "C", "alt": "G"}),
            _hit("rsM", clinvar={"ref": "C", "alt": "T"})]
    allele, _, _, status = bf.compute_for_variant("CC", hits)
    assert allele is None and status == "multiallelic_ambiguous"


# ── run() end-to-end ─────────────────────────────────────────────────────────

def _seed(rsid, genotype, significance):
    health_db.upsert_genetic_variant(rsid, {
        "gene": "X", "genotype": genotype, "significance": significance,
        "conditions": [], "domain_tags": [],
    })


def _seed_with_summary(rsid, genotype, significance, clinical_summary):
    """Seed variant + записать clinical_summary напрямую в БД."""
    _seed(rsid, genotype, significance)
    with health_db.get_conn() as c:
        c.execute(
            "UPDATE genetic_variants SET clinical_summary=? WHERE rsid=?",
            (clinical_summary, rsid),
        )
        c.commit()


def _fake_fetch(_rsids):
    return {
        "rsHET": [_hit("rsHET", clinvar={"ref": "G", "alt": "A"})],          # AG → hetero A
        "rsPAL":     [_hit("rsPAL",     clinvar={"ref": "C", "alt": "G"})],          # CC → palindromic
        "rsMUL":     [_hit("rsMUL",     clinvar={"ref": "C", "alt": "G"}),
                      _hit("rsMUL",     clinvar={"ref": "C", "alt": "T"})],          # multiallelic
        "rsWT":      [_hit("rsWT",      clinvar={"ref": "G", "alt": "A"})],          # GG → wildtype A
    }


def _row(rsid):
    with health_db.get_conn() as c:
        r = c.execute(
            "SELECT effect_allele, ref_allele, assembly, effect_allele_status, "
            "significance, prev_significance FROM genetic_variants WHERE rsid=?",
            (rsid,)).fetchone()
    return dict(r) if r else None


def test_run_end_to_end(db):
    _seed("rsHET", "AG", "Pathogenic")
    _seed("rsPAL", "CC", "Likely pathogenic")
    _seed("rsMUL", "CC", "Pathogenic")
    _seed("rsWT", "GG", "Pathogenic")

    stats = bf.run(fetch=_fake_fetch)

    assert _row("rsHET")["effect_allele"] == "A"
    assert _row("rsHET")["effect_allele_status"] == "resolved"
    assert _row("rsHET")["assembly"] == "GRCh37"
    # palindromic / multiallelic → NULL, но status записан
    assert _row("rsPAL")["effect_allele"] is None
    assert _row("rsPAL")["effect_allele_status"] == "palindromic"
    assert _row("rsMUL")["effect_allele"] is None
    assert _row("rsMUL")["effect_allele_status"] == "multiallelic_ambiguous"
    # wildtype: effect_allele записан (A), но в генотипе его нет → носителя нет
    assert _row("rsWT")["effect_allele"] == "A"
    assert _row("rsWT")["effect_allele_status"] == "resolved"
    assert stats.get("resolved") == 2


def test_run_palindromic_het_resolved(db):
    """run() системно разрешает het-palindromic через clinical_summary."""
    _seed_with_summary(
        "rsPAL_HET", "CG", "Pathogenic",
        "NM_000410.4(HFE):c.187C>G (p.His63Asp)",
    )

    def fake(rsids):
        return {"rsPAL_HET": [_hit("rsPAL_HET", clinvar={"ref": "C", "alt": "G"})]}

    stats = bf.run(fetch=fake)
    r = _row("rsPAL_HET")
    assert r["effect_allele"] == "G"
    assert r["effect_allele_status"] == "palindromic_het_resolved"
    assert stats.get("palindromic_het_resolved") == 1


def test_run_palindromic_het_resolved_via_ref_fallback(db):
    """run() разрешает het-palindromic через complement(ref из hits) при отсутствии HGVS."""
    _seed("rsFTO_HET", "TA", "Pathogenic")  # нет clinical_summary

    def fake(rsids):
        return {"rsFTO_HET": [_hit("rsFTO_HET", clinvar={"ref": "T", "alt": "A"})]}

    stats = bf.run(fetch=fake)
    r = _row("rsFTO_HET")
    assert r["effect_allele"] == "A"
    assert r["effect_allele_status"] == "palindromic_het_resolved"
    assert stats.get("palindromic_het_resolved") == 1


def test_run_does_not_touch_significance(db):
    _seed("rsHET", "AG", "Pathogenic")
    bf.run(fetch=_fake_fetch)
    r = _row("rsHET")
    assert r["significance"] == "Pathogenic"          # значимость не тронута
    assert r["prev_significance"] is None             # история не сдвинута


def test_run_idempotent(db):
    _seed("rsHET", "AG", "Pathogenic")
    s1 = bf.run(fetch=_fake_fetch)
    r1 = _row("rsHET")
    s2 = bf.run(fetch=_fake_fetch)
    r2 = _row("rsHET")
    assert r1 == r2 and s1 == s2


def test_coalesce_protects_effect_allele_from_reannotation(db):
    """После backfill ре-аннотация значимости (upsert без effect_allele)
    НЕ затирает effect_allele в NULL (COALESCE-защита)."""
    _seed("rsHET", "AG", "Pathogenic")
    bf.run(fetch=_fake_fetch)
    assert _row("rsHET")["effect_allele"] == "A"

    # имитация genome_update_agent: меняет значимость, effect_allele не передаёт
    health_db.upsert_genetic_variant("rsHET", {
        "gene": "X", "genotype": "AG", "significance": "Likely pathogenic",
        "conditions": [], "domain_tags": [],
    })
    r = _row("rsHET")
    assert r["effect_allele"] == "A"                  # сохранён
    assert r["effect_allele_status"] == "resolved"     # метаданные сохранены
    assert r["significance"] == "Likely pathogenic"   # значимость обновилась
    assert r["prev_significance"] == "Pathogenic"     # история сдвинулась корректно
