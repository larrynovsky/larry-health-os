"""
tests/unit/test_palindromic_het_fix.py

Тест fix_palindromic_het.run() — резолюция гетерозиготных палиндромных SNP.
Использует in-memory SQLite, не требует Studio/сети.
"""
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from fix_palindromic_het import run, _parse_alt, _alt_from_ref


# ---------------------------------------------------------------------------
# Юнит: парсинг HGVS
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("summary,expected", [
    ("NM_000410.4(HFE):c.187C>G (p.His63Asp)", "G"),          # HFE H63D
    ("NM_000038.6(APC):c.3920T>A (p.Ile1307Lys)", "A"),        # APC I1307K
    ("NC_000007.14:g.22727026C>G", "G"),                        # genomic notation
    ("NM_001144962.2(NFKBIL1):c.-13+590T>A", "A"),             # 5'UTR intronic
    ("NM_017774.3(CDKAL1):c.371+11642G>C", "C"),               # deep intronic
    ("NM_000113.3(TOR1A):c.646G>C (p.Asp216His)", "C"),        # missense
    ("NM_139075.4(TPCN2):c.1450A>T (p.Met484Leu)", "T"),      # A>T
    ("FTO intron variant; associated with obesity risk", None),  # no HGVS → None
    ("", None),
])
def test_parse_alt(summary, expected):
    assert _parse_alt(summary) == expected


# ---------------------------------------------------------------------------
# Юнит: fallback через ref_allele
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ref,genotype,expected", [
    ("T", "TA", "A"),   # A/T palindromic: ref=T → alt=A, A in TA ✓
    ("A", "AT", "T"),   # A/T palindromic: ref=A → alt=T, T in AT ✓
    ("C", "CG", "G"),   # C/G palindromic: ref=C → alt=G, G in CG ✓
    ("G", "GC", "C"),   # C/G palindromic: ref=G → alt=C, C in GC ✓
    (None, "TA", None), # нет ref — нет результата
    ("T", "GG", None),  # complement(T)=A не в GG → None
])
def test_alt_from_ref(ref, genotype, expected):
    assert _alt_from_ref(ref, genotype) == expected


# ---------------------------------------------------------------------------
# Интеграция: run() обновляет DB
# ---------------------------------------------------------------------------

def _make_db():
    """In-memory DB с минимальной схемой genetic_variants."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE genetic_variants (
            id INTEGER PRIMARY KEY,
            rsid TEXT,
            genotype TEXT,
            clinical_summary TEXT,
            significance TEXT,
            ref_allele TEXT,
            effect_allele TEXT,
            effect_allele_status TEXT
        )
    """)
    return conn


def _insert(conn, id_, rsid, genotype, summary, status="palindromic", ref_allele=None):
    conn.execute(
        "INSERT INTO genetic_variants "
        "(id, rsid, genotype, clinical_summary, effect_allele_status, effect_allele, ref_allele) "
        "VALUES (?, ?, ?, ?, ?, NULL, ?)",
        (id_, rsid, genotype, summary, status, ref_allele),
    )
    conn.commit()


def _row(conn, id_):
    return dict(conn.execute(
        "SELECT effect_allele, effect_allele_status FROM genetic_variants WHERE id=?",
        (id_,)
    ).fetchone())


def test_het_cg_resolved():
    """Синтетический C/G-вариант: CG + c.187C>G → effect_allele=G (ген и rsid вымышлены)."""
    conn = _make_db()
    _insert(conn, 1, "rsSYNTH_CG", "CG",
            "NM_000000.1(GENEY):c.187C>G (p.Ala63Gly)")
    r = run(_conn=conn)
    assert r["updated"] == 1
    assert _row(conn, 1)["effect_allele"] == "G"
    assert _row(conn, 1)["effect_allele_status"] == "palindromic_het_resolved"


def test_het_at_resolved():
    """Синтетический A/T-вариант: AT + c.3920T>A → effect_allele=A (ген и rsid вымышлены)."""
    conn = _make_db()
    _insert(conn, 2, "rsSYNTH_AT", "AT",
            "NM_000000.1(GENEX):c.3920T>A (p.Ile1307Lys)")
    r = run(_conn=conn)
    assert r["updated"] == 1
    assert _row(conn, 2)["effect_allele"] == "A"


def test_homo_palindromic_skipped():
    """Гомозиготный палиндромный (GG) — не трогаем."""
    conn = _make_db()
    _insert(conn, 3, "rsHOMO", "GG",
            "NM_000000.1(GENEY):c.187C>G (p.Ala63Gly)")
    r = run(_conn=conn)
    assert r["updated"] == 0
    assert _row(conn, 3)["effect_allele"] is None
    assert _row(conn, 3)["effect_allele_status"] == "palindromic"


def test_fallback_via_ref_allele():
    """Синтетический интронный вариант: нет HGVS в summary, но ref_allele=T → alt=A (fallback)."""
    conn = _make_db()
    _insert(conn, 4, "rsSYNTH_TA", "TA",
            "GENEY intron variant; descriptive annotation",
            ref_allele="T")
    r = run(_conn=conn)
    assert r["updated"] == 1
    assert _row(conn, 4)["effect_allele"] == "A"
    assert _row(conn, 4)["effect_allele_status"] == "palindromic_het_resolved"
    assert "rsSYNTH_TA" not in r["skipped"]


def test_no_hgvs_no_ref_skipped():
    """Нет HGVS и нет ref_allele → всё равно скипаем."""
    conn = _make_db()
    _insert(conn, 5, "rsUNKNOWN", "TA",
            "some descriptive annotation without HGVS",
            ref_allele=None)
    r = run(_conn=conn)
    assert r["updated"] == 0
    assert "rsUNKNOWN" in r["skipped"]
    assert _row(conn, 5)["effect_allele_status"] == "palindromic"


def test_already_resolved_not_touched():
    """palindromic_het_resolved уже — WHERE фильтрует, run() не трогает."""
    conn = _make_db()
    _insert(conn, 6, "rsSYNTH_CG", "CG",
            "NM_000000.1(GENEY):c.187C>G (p.Ala63Gly)",
            status="palindromic_het_resolved")
    conn.execute(
        "UPDATE genetic_variants SET effect_allele='G' WHERE id=6"
    )
    conn.commit()
    r = run(_conn=conn)
    assert r["updated"] == 0
    assert _row(conn, 6)["effect_allele"] == "G"  # не затёрто


def test_carrier_detection_after_fix():
    """После фикса `effect_allele in genotype` = True для носителя."""
    conn = _make_db()
    _insert(conn, 7, "rsSYNTH_CG", "CG",
            "NM_000000.1(GENEY):c.187C>G (p.Ala63Gly)")
    run(_conn=conn)
    row = dict(conn.execute(
        "SELECT genotype, effect_allele FROM genetic_variants WHERE id=7"
    ).fetchone())
    assert row["effect_allele"] is not None
    assert row["effect_allele"] in row["genotype"]


def test_hgvs_takes_priority_over_ref():
    """HGVS-путь приоритетнее ref_allele, даже если оба доступны."""
    conn = _make_db()
    # HGVS говорит alt=G, ref_allele='C' (complement тоже G) — результат тот же
    _insert(conn, 8, "rsX", "CG",
            "NM_000000.1:c.100C>G",
            ref_allele="C")
    r = run(_conn=conn)
    assert r["updated"] == 1
    assert _row(conn, 8)["effect_allele"] == "G"
