"""
tests/unit/test_genome_characterization.py — характеризационные пины домена
genome в health_db (Поток B рефакторинга, 2026-06-27).
  get_significant_variants, get_variants_by_genes, get_carrier_variants,
  get_snps_batch, upsert_genetic_variant, genome_summary_counts.

behavior-preserving пины: «как есть» ≠ «как правильно».
"""
from __future__ import annotations

import health_db


def test_get_significant_variants_excludes_benign_and_orders_patho_first(db):
    db.add_genetic_variant("rs1", significance="Pathogenic")
    db.add_genetic_variant("rs2", significance="Benign")
    db.add_genetic_variant("rs3", significance="Likely pathogenic")
    rsids = [v["rsid"] for v in health_db.get_significant_variants()]
    assert "rs2" not in rsids
    assert rsids.index("rs1") < rsids.index("rs3")  # Pathogenic раньше Likely


def test_get_variants_by_genes_empty_list_returns_empty(db):
    assert health_db.get_variants_by_genes([]) == []


def test_get_variants_by_genes_filters_by_gene(db):
    db.add_genetic_variant("rsM", gene="MTHFR", significance="Pathogenic")
    db.add_genetic_variant("rsF", gene="FOO", significance="Pathogenic")
    genes = {v["gene"] for v in health_db.get_variants_by_genes(["MTHFR"])}
    assert genes == {"MTHFR"}


def test_get_carrier_variants_requires_resolved_allele_in_genotype(db):
    db.add_genetic_variant("rsA", significance="Pathogenic",
                           effect_allele="A", effect_allele_status="resolved",
                           genotype="AG")
    db.add_genetic_variant("rsB", significance="Pathogenic",
                           effect_allele="T", effect_allele_status="resolved",
                           genotype="GG")  # T нет в генотипе → не носитель
    carriers = [v["rsid"] for v in health_db.get_carrier_variants()]
    assert "rsA" in carriers
    assert "rsB" not in carriers


def test_get_snps_batch_empty_returns_empty(db):
    assert health_db.get_snps_batch([]) == {}


def test_get_snps_batch_returns_genotype_map(db):
    db.execute("INSERT INTO raw_snps (rsid, genotype) VALUES (?, ?)", ("rsX", "AG"))
    assert health_db.get_snps_batch(["rsX"]) == {"rsX": "AG"}


def test_upsert_genetic_variant_insert_appears_in_significant(db):
    health_db.upsert_genetic_variant("rsZ", {"gene": "X", "genotype": "AG",
                                             "significance": "Pathogenic"})
    assert "rsZ" in [v["rsid"] for v in health_db.get_significant_variants()]


def test_genome_summary_counts_returns_dict_with_keys(db):
    result = health_db.genome_summary_counts()
    assert isinstance(result, dict)
    assert "patho_carrier" in result
