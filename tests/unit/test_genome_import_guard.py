"""
genome_parser.import_raw_genome — защита от импорта/затирания чужого генома.

Вымышленный донор A не должен получать геном донора B через путь по умолчанию.
Путь обязателен; file_id из заголовка 23andMe хранится в genome_source
и блокирует cross-identity перезапись.
"""
from __future__ import annotations

import pytest

import genome_parser as gp

pytestmark = pytest.mark.unit


def _write_genome(path, file_id, snps):
    lines = [f"# file_id: {file_id}", f"# signature: sig_{file_id}", "#"]
    for rsid, chrom, pos, gt in snps:
        lines.append(f"{rsid}\t{chrom}\t{pos}\t{gt}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


_SNPS_A = [("rs1", "1", "100", "AA"), ("rs2", "1", "200", "CT"), ("rs3", "2", "300", "GG")]
_SNPS_B = [("rs1", "1", "100", "GG"), ("rs2", "1", "200", "TT"), ("rs3", "2", "300", "AA")]


def test_filepath_required():
    with pytest.raises(ValueError):
        gp.import_raw_genome(None)


def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        gp.import_raw_genome(tmp_path / "нет.txt")


def test_parse_identity(tmp_path):
    f = _write_genome(tmp_path / "g.txt", "AAA-111", _SNPS_A)
    ident = gp._parse_source_identity(f)
    assert ident["file_id"] == "AAA-111"
    assert ident["signature"] == "sig_AAA-111"


def test_empty_import_records_identity(db, tmp_path):
    f = _write_genome(tmp_path / "a.txt", "AAA-111", _SNPS_A)
    n = gp.import_raw_genome(f)
    assert n == 3
    import health_db as hdb
    with hdb.get_conn() as c:
        row = c.execute("SELECT file_id FROM genome_source ORDER BY id DESC LIMIT 1").fetchone()
    assert row[0] == "AAA-111", "идентичность донора должна быть записана"


def test_cross_identity_force_blocked(db, tmp_path):
    gp.import_raw_genome(_write_genome(tmp_path / "a.txt", "AAA-111", _SNPS_A))
    fb = _write_genome(tmp_path / "b.txt", "BBB-222", _SNPS_B)
    with pytest.raises(RuntimeError, match="file_id"):
        gp.import_raw_genome(fb, force=True)  # другой человек → отказ


def test_cross_identity_override_allowed(db, tmp_path):
    gp.import_raw_genome(_write_genome(tmp_path / "a.txt", "AAA-111", _SNPS_A))
    fb = _write_genome(tmp_path / "b.txt", "BBB-222", _SNPS_B)
    n = gp.import_raw_genome(fb, force=True, allow_identity_change=True)
    assert n == 3  # явный override разрешает


def test_same_person_force_allowed(db, tmp_path):
    fa = _write_genome(tmp_path / "a.txt", "AAA-111", _SNPS_A)
    gp.import_raw_genome(fa)
    # тот же file_id → force без флага допустим (обновление своего же генома)
    n = gp.import_raw_genome(fa, force=True)
    assert n == 3
