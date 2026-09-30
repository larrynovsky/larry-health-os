"""
UC-C-01 — 23andMe TSV полностью парсится, double-tab не теряется.

Источник: USE_CASES.md §3.C → UC-C-01.
Реализация: `genome_parser.py`.
Status: `implemented`.

Главный исторический баг: TSV строки с двойными табами могли терять колонки.
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


# Независимо придуманный TSV: идентификаторы и позиции — учебные заглушки.
_TSV_SAMPLE = """\
# Invented parser fixture, not a vendor export
# rsid	chromosome	position	genotype
rs910000001	3	101	TT
rs910000002	3	202	GG
rs910000003	4	303	AC
rs910000004	4	404	CT
rs910000005	5	505	GT
rs910000006	6	606	TC
rs910000007	7	707	AA
i910000008	8	808	CG
rs910000009	9	909	--
"""

_TSV_DOUBLE_TAB = "rs1\t1\t100\tAA\nrs2\t\t1\t200\tCC\n"  # Двойной таб (баг)


def _write(tmp_path, content: str) -> Path:
    f = tmp_path / "genome.txt"
    f.write_text(content, encoding="utf-8")
    return f


def test_parser_skips_comment_lines(tmp_path):
    """Строки с # — пропускаются."""
    f = _write(tmp_path, _TSV_SAMPLE)
    from genome_parser import parse_tsv
    rows = parse_tsv(f)

    rsids = {r["rsid"] for r in rows}
    assert "rs910000006" in rsids
    assert all(not r["rsid"].startswith("#") for r in rows)


def test_parser_includes_internal_iIDs(tmp_path):
    """Внутренние iID формата 23andMe тоже сохраняются (UC-C-01)."""
    f = _write(tmp_path, _TSV_SAMPLE)
    from genome_parser import parse_tsv
    rows = parse_tsv(f)
    assert any(r["rsid"] == "i910000008" for r in rows)


def test_parser_keeps_no_call_genotype(tmp_path):
    """`genotype="--"` — валидное значение «no call», не отбрасывается."""
    f = _write(tmp_path, _TSV_SAMPLE)
    from genome_parser import parse_tsv
    rows = parse_tsv(f)
    no_call = [r for r in rows if r["rsid"] == "rs910000009"]
    assert len(no_call) == 1
    assert no_call[0]["genotype"] == "--"


def test_parser_handles_double_tab_format(tmp_path):
    """Double-tab после rsid — исторический формат, должен парситься."""
    f = _write(tmp_path, _TSV_DOUBLE_TAB)
    from genome_parser import parse_tsv
    rows = parse_tsv(f)
    rsids = {r["rsid"] for r in rows}
    assert "rs1" in rsids
    assert "rs2" in rsids  # с двойным табом


def test_parser_count_matches_data_lines(tmp_path):
    """Число строк = строк с rs/i минус комменты, минус skipped."""
    f = _write(tmp_path, _TSV_SAMPLE)
    from genome_parser import parse_tsv
    rows = parse_tsv(f)
    # В sample: 8 rs + 1 i = 9 (no-call genotype="--" тоже валиден).
    assert len(rows) == 9


def test_genome_parser_module_imports():
    """Модуль импортируется и имеет ключевые функции (UC-C-01)."""
    import genome_parser
    assert hasattr(genome_parser, "parse_tsv")
    assert hasattr(genome_parser, "import_raw_genome")
