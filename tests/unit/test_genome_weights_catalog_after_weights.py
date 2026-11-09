"""Шкала регистрируется в pgs_catalog только после того, как её веса легли в справочник.

Замер 03.11.2026: 26 шкал от 02–05.08 числились в каталоге без единого веса — запись в
каталог коммитилась ДО весов, и прогон, оборвавшийся на весах, оставлял пустую шкалу. Она
считалась «уже скачанной», не скачивалась снова и не судилась отбором по покрытию."""
from __future__ import annotations

import gzip
import sqlite3
from unittest.mock import MagicMock, patch

import pytest

import genome_weights as gw
import pgs_reference

_HEADER = "#pgs_id=PGSX\n#variants_number=2\n"
_COLS = "rsID\thm_chr\thm_pos\teffect_allele\tother_allele\teffect_weight\n"


def _responses(body: str):
    meta = MagicMock()
    meta.raise_for_status = MagicMock()
    meta.json.return_value = {"trait_reported": "T", "ftp_harmonized_scoring_files": {"GRCh38": {"positions": "http://x"}}}
    f = MagicMock()
    f.raise_for_status = MagicMock()
    f.content = gzip.compress(body.encode())
    return [meta, f]


@pytest.fixture
def ref(tmp_path, monkeypatch):
    monkeypatch.setenv("HEALTH_PGS_DB", str(tmp_path / "pgs.db"))
    c = pgs_reference.get_ref_conn()
    yield c
    c.close()


@pytest.mark.unit
def test_no_weights_no_catalog_row(ref):
    body = _HEADER + _COLS + "rs1\t1\t100\t\t\tnot_a_number\n"      # ни одной годной строки
    with patch("requests.get", side_effect=_responses(body)):
        res = gw.download_and_import("PGSX", conn=ref)
    assert res["status"] == "empty_weights"
    assert ref.execute("SELECT COUNT(*) FROM pgs_catalog").fetchone()[0] == 0


@pytest.mark.unit
def test_interrupted_weights_leave_no_catalog_row(ref, monkeypatch):
    body = _HEADER + _COLS + "rs1\t1\t100\tA\tG\t0.1\nrs2\t1\t200\tC\tT\t0.2\n"
    real = ref.executemany

    class Boom(Exception):
        pass

    def exploding(sql, *a, **k):
        if "pgs_weights" in sql:
            raise Boom("обрыв посреди весов")
        return real(sql, *a, **k)

    proxy = MagicMock(wraps=ref)
    proxy.executemany.side_effect = exploding
    proxy.total_changes = 0
    with patch("requests.get", side_effect=_responses(body)), pytest.raises(Boom):
        gw.download_and_import("PGSX", conn=proxy)
    assert ref.execute("SELECT COUNT(*) FROM pgs_catalog").fetchone()[0] == 0


@pytest.mark.unit
def test_good_file_registers_after_weights(ref):
    body = _HEADER + _COLS + "rs1\t1\t100\tA\tG\t0.1\nrs2\t1\t200\tC\tT\t0.2\n"
    with patch("requests.get", side_effect=_responses(body)):
        res = gw.download_and_import("PGSX", conn=ref)
    assert res["status"] == "ok" and res["n_inserted"] == 2
    assert ref.execute("SELECT COUNT(*) FROM pgs_catalog WHERE pgs_id='PGSX'").fetchone()[0] == 1


@pytest.mark.unit
def test_dosage_weights_file_is_unsupported_format(ref):
    """PGS004262 и ещё 7 (03.11): dosage_0/1/2_weight вместо effect_weight."""
    body = _HEADER + "chr_name\tchr_position\teffect_allele\tother_allele\tdosage_0_weight\tdosage_1_weight\tdosage_2_weight\n" \
        + "6\t100\tT\tC\t-0.03\t0.1\t0.25\n"
    with patch("requests.get", side_effect=_responses(body)):
        res = gw.download_and_import("PGSX", conn=ref)
    assert res["status"] == "unsupported_format"
    assert ref.execute("SELECT COUNT(*) FROM pgs_catalog").fetchone()[0] == 0
