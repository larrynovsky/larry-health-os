"""Тесты для pgs_discovery.discover_and_import_new_scores.

Check vs Test (RST): проверяем механику фильтрации, дедупликации и delivery
pathway. Правильность биологических данных PGS Catalog — вне scope (нет оракула).

2026-06-27: Block D2 — полный скан каталога + авто-импорт новых scoring файлов.
2026-07-02: pgs_catalog/pgs_weights вынесены в общую reference-БД. Discovery
читает known_ids и пишет веса в _ref (pgs_reference), канон — только
genome_imports/prs_scores. Тесты сеют РЕАЛЬНУЮ изолированную reference-БД.
"""
from __future__ import annotations

import sqlite3
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import pgs_discovery as pgsd
import pgs_reference


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_api_score(pgs_id: str, nv: int = 100, grch38: bool = True) -> dict:
    return {
        "id": pgs_id,
        "trait_reported": f"Trait {pgs_id}",
        "variants_number": nv,
        "ftp_harmonized_scoring_files": {"GRCh38": {"positions": "http://x"}} if grch38 else {},
    }


def _api_page(results: list[dict], next_url=None) -> dict:
    return {"count": len(results), "results": results, "next": next_url}


def _setup_dbs(tmp_path, monkeypatch, known_pgs_ids: list[str], genome_import_id: int = 1):
    """Изолированная reference-БД (known PGS в pgs_catalog) + канон (genome_imports).

    Возвращает канон-соединение (in-memory). Discovery сам откроет reference по
    HEALTH_PGS_DB через pgs_reference.get_ref_conn().
    """
    monkeypatch.setenv("HEALTH_PGS_DB", str(tmp_path / "pgs_ref.db"))
    ref = pgs_reference.get_ref_conn()
    for pid in known_pgs_ids:
        ref.execute(
            "INSERT INTO pgs_catalog (pgs_id, trait_name, downloaded_at) VALUES (?,?,?)",
            (pid, f"Trait {pid}", "2026-01-01"),
        )
    ref.commit()
    ref.close()
    canon = sqlite3.connect(":memory:")
    canon.execute("CREATE TABLE genome_imports (id INTEGER PRIMARY KEY)")
    canon.execute("INSERT INTO genome_imports (id) VALUES (?)", (genome_import_id,))
    canon.commit()
    return canon


# ── tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_skips_existing_pgs(tmp_path, monkeypatch):
    """PGS уже в pgs_catalog (reference) → не импортируется (skipped_existing)."""
    conn = _setup_dbs(tmp_path, monkeypatch, ["PGS000001"])

    with patch("requests.get") as mock_get:
        mock_get.return_value.raise_for_status = MagicMock()
        mock_get.return_value.json.return_value = _api_page([_make_api_score("PGS000001")])

        import_mock = MagicMock(return_value={"status": "ok", "n_inserted": 10})
        monkeypatch.setattr(pgsd.gw, "download_and_import", import_mock)

        result = pgsd.discover_and_import_new_scores(conn)

    import_mock.assert_not_called()
    assert result["skipped_existing"] == 1
    assert result["imported"] == []


@pytest.mark.unit
def test_skips_low_variant_count(tmp_path, monkeypatch):
    """num_variants < 50 → skipped_quality."""
    conn = _setup_dbs(tmp_path, monkeypatch, [])

    with patch("requests.get") as mock_get:
        mock_get.return_value.raise_for_status = MagicMock()
        mock_get.return_value.json.return_value = _api_page(
            [_make_api_score("PGS000099", nv=10)]
        )
        import_mock = MagicMock()
        monkeypatch.setattr(pgsd.gw, "download_and_import", import_mock)

        result = pgsd.discover_and_import_new_scores(conn)

    import_mock.assert_not_called()
    assert result["skipped_quality"] >= 1


@pytest.mark.unit
def test_skips_no_grch38(tmp_path, monkeypatch):
    """Нет GRCh38 harmonized → skipped_quality."""
    conn = _setup_dbs(tmp_path, monkeypatch, [])

    with patch("requests.get") as mock_get:
        mock_get.return_value.raise_for_status = MagicMock()
        mock_get.return_value.json.return_value = _api_page(
            [_make_api_score("PGS000099", nv=500, grch38=False)]
        )
        import_mock = MagicMock()
        monkeypatch.setattr(pgsd.gw, "download_and_import", import_mock)

        result = pgsd.discover_and_import_new_scores(conn)

    import_mock.assert_not_called()
    assert result["skipped_quality"] >= 1


@pytest.mark.unit
def test_imports_new_score_and_runs_phase_h(tmp_path, monkeypatch):
    """Новый score (GRCh38 + nv>=50) → download_and_import вызван + Phase H."""
    conn = _setup_dbs(tmp_path, monkeypatch, [], genome_import_id=7)

    with patch("requests.get") as mock_get:
        mock_get.return_value.raise_for_status = MagicMock()
        mock_get.return_value.json.return_value = _api_page(
            [_make_api_score("PGS000999", nv=500)]
        )

        import_mock  = MagicMock(return_value={"status": "ok", "n_inserted": 50})
        phase_h_mock = MagicMock(return_value={"results": {}, "failed": []})
        monkeypatch.setattr(pgsd.gw, "download_and_import", import_mock)
        monkeypatch.setattr(pgsd.pp, "run", phase_h_mock)

        result = pgsd.discover_and_import_new_scores(conn)

    # writer вызван с pgs_id/trait и соединением reference-БД (не канон)
    import_mock.assert_called_once()
    args, kwargs = import_mock.call_args
    assert args[0] == "PGS000999"
    assert kwargs["trait_name"] == "Trait PGS000999"
    assert isinstance(kwargs["conn"], sqlite3.Connection)
    phase_h_mock.assert_called_once()
    assert result["phase_h_run"] is True
    assert len(result["imported"]) == 1
    assert result["imported"][0]["pgs_id"] == "PGS000999"


@pytest.mark.unit
def test_import_error_does_not_crash_batch(tmp_path, monkeypatch):
    """Ошибка при импорте одного PGS → errors, batch продолжается."""
    conn = _setup_dbs(tmp_path, monkeypatch, [], genome_import_id=1)

    with patch("requests.get") as mock_get:
        mock_get.return_value.raise_for_status = MagicMock()
        mock_get.return_value.json.return_value = _api_page([
            _make_api_score("PGS000A", nv=100),
            _make_api_score("PGS000B", nv=200),
        ])

        def _side(pgs_id, **kw):
            if pgs_id == "PGS000A":
                raise RuntimeError("network timeout")
            return {"status": "ok", "n_inserted": 10}

        monkeypatch.setattr(pgsd.gw, "download_and_import", _side)
        monkeypatch.setattr(pgsd.pp, "run", MagicMock(return_value={"results": {}}))

        result = pgsd.discover_and_import_new_scores(conn)

    assert len(result["errors"]) == 1
    assert result["errors"][0]["pgs_id"] == "PGS000A"
    assert len(result["imported"]) == 1
    assert result["imported"][0]["pgs_id"] == "PGS000B"


@pytest.mark.unit
def test_no_phase_h_if_nothing_imported(tmp_path, monkeypatch):
    """Новых импортов нет И у этого генома всё посчитано — Phase H не вызывается."""
    conn = _setup_dbs(tmp_path, monkeypatch, ["PGS000001"])  # всё уже есть
    monkeypatch.setattr(pgsd.pp, "unscored_models", lambda c, gid: [])

    with patch("requests.get") as mock_get:
        mock_get.return_value.raise_for_status = MagicMock()
        mock_get.return_value.json.return_value = _api_page(
            [_make_api_score("PGS000001")]
        )
        phase_h_mock = MagicMock()
        monkeypatch.setattr(pgsd.pp, "run", phase_h_mock)

        result = pgsd.discover_and_import_new_scores(conn)

    phase_h_mock.assert_not_called()
    assert result["phase_h_run"] is False


@pytest.mark.unit
def test_phase_h_for_models_scored_only_for_another_person(tmp_path, monkeypatch):
    """Справочник общий на установку: модель скачана по данным другого человека, импорта
    сейчас нет, но у ЭТОГО генома балла нет — Phase H обязан пересчитать (28.09)."""
    conn = _setup_dbs(tmp_path, monkeypatch, ["PGS000001"])  # модель уже в справочнике

    with patch("requests.get") as mock_get:
        mock_get.return_value.raise_for_status = MagicMock()
        mock_get.return_value.json.return_value = _api_page(
            [_make_api_score("PGS000001")]
        )
        phase_h_mock = MagicMock(return_value={"results": {}})
        monkeypatch.setattr(pgsd.pp, "run", phase_h_mock)

        result = pgsd.discover_and_import_new_scores(conn)

    phase_h_mock.assert_called_once()
    assert result["imported"] == [] and result["phase_h_run"] is True


def _mock_ref(known_pgs_ids=None):
    """Мок reference-соединения: .backup() перехватываемо (C-тип sqlite3.Connection
    иммутабелен — патчить его метод нельзя), .execute даёт known_ids."""
    ref = MagicMock()
    ref.execute.return_value.fetchall.return_value = [(p,) for p in (known_pgs_ids or [])]
    return ref


@pytest.mark.unit
def test_preopsnap_called_when_candidates_exist(tmp_path, monkeypatch):
    """Pre-op snapshot: backup() reference-БД вызывается когда есть кандидаты."""
    conn = _setup_dbs(tmp_path, monkeypatch, [], genome_import_id=1)
    ref = _mock_ref([])
    monkeypatch.setattr(pgs_reference, "get_ref_conn", lambda: ref)

    with patch("requests.get") as mock_get:
        mock_get.return_value.raise_for_status = MagicMock()
        mock_get.return_value.json.return_value = _api_page(
            [_make_api_score("PGS000777", nv=100)]
        )
        monkeypatch.setattr(pgsd.gw, "download_and_import",
                            MagicMock(return_value={"status": "ok", "n_inserted": 5}))
        monkeypatch.setattr(pgsd.pp, "run", MagicMock(return_value={"results": {}}))
        with patch("sqlite3.connect", return_value=MagicMock()):  # snap_conn
            result = pgsd.discover_and_import_new_scores(conn)

    ref.backup.assert_called_once()       # снапшот reference создан
    assert result["snapshot_error"] is None


@pytest.mark.unit
def test_preopsnap_failure_does_not_block_import(tmp_path, monkeypatch):
    """Если snapshot упал — импорт продолжается, snapshot_error заполнен."""
    conn = _setup_dbs(tmp_path, monkeypatch, [], genome_import_id=1)
    ref = _mock_ref([])
    ref.backup.side_effect = OSError("no space left")
    monkeypatch.setattr(pgs_reference, "get_ref_conn", lambda: ref)

    with patch("requests.get") as mock_get:
        mock_get.return_value.raise_for_status = MagicMock()
        mock_get.return_value.json.return_value = _api_page(
            [_make_api_score("PGS000888", nv=100)]
        )
        monkeypatch.setattr(pgsd.gw, "download_and_import",
                            MagicMock(return_value={"status": "ok", "n_inserted": 5}))
        monkeypatch.setattr(pgsd.pp, "run", MagicMock(return_value={"results": {}}))
        with patch("sqlite3.connect", return_value=MagicMock()):
            result = pgsd.discover_and_import_new_scores(conn)

    assert len(result["imported"]) == 1
    assert result["imported"][0]["pgs_id"] == "PGS000888"
    assert result["snapshot_error"] == "no space left"


@pytest.mark.unit
def test_preopsnap_not_called_when_no_candidates(tmp_path, monkeypatch):
    """Если кандидатов нет (все уже импортированы) — snapshot не создаётся."""
    conn = _setup_dbs(tmp_path, monkeypatch, ["PGS000001"])
    ref = _mock_ref(["PGS000001"])
    monkeypatch.setattr(pgs_reference, "get_ref_conn", lambda: ref)

    with patch("requests.get") as mock_get:
        mock_get.return_value.raise_for_status = MagicMock()
        mock_get.return_value.json.return_value = _api_page(
            [_make_api_score("PGS000001")]
        )
        result = pgsd.discover_and_import_new_scores(conn)

    ref.backup.assert_not_called()
    assert result["snapshot_error"] is None
    assert result["imported"] == []


@pytest.mark.unit
def test_register_adds_pgs_discovery_monthly_job():
    """register() добавляет pgs_discovery_monthly job, day=2."""
    import jobs.scheduled as sched

    jq  = MagicMock()
    app = SimpleNamespace(job_queue=jq)
    sched.register(app)

    monthly_calls = jq.run_monthly.call_args_list
    matched = [
        c for c in monthly_calls
        if c.kwargs.get("name") == "pgs_discovery_monthly"
        and c.kwargs.get("day") == 2
        and c.args and c.args[0] is sched.discover_pgs_monthly
    ]
    assert matched, f"pgs_discovery_monthly не зарегистрирована: {[c.kwargs.get('name') for c in monthly_calls]}"
