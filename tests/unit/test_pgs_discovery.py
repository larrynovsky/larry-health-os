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
    # Снимок reference-БД ложится в <DB_PATH>/../../backups — тест обязан писать в СВОЙ
    # каталог: общий backups/ прогона делил файл снимка той же минуты между тестами, и
    # chmod 600 зеленел, только если соседний тест успел создать файл (флак 30.10).
    import health_db
    monkeypatch.setattr(health_db, "DB_PATH", tmp_path / "data" / "health.db")
    # отбор по покрытию читает каноны тенантов — тест не должен видеть настоящие ~/health*
    monkeypatch.setattr(pgsd, "_evidence_db_paths", lambda: [])
    ref = pgs_reference.get_ref_conn()
    for pid in known_pgs_ids:
        ref.execute(
            "INSERT INTO pgs_catalog (pgs_id, trait_name, downloaded_at) VALUES (?,?,?)",
            (pid, f"Trait {pid}", "2026-01-01"),
        )
        # известная шкала — с весами: каталог без весов докачка теперь снимает как пустую
        ref.execute("INSERT INTO pgs_weights (pgs_id, rsid, effect_allele, effect_weight)"
                    " VALUES (?,?,?,?)", (pid, "rs1", "A", 0.1))
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
        result = pgsd.discover_and_import_new_scores(conn)

    ref.backup.assert_called_once()       # снапшот reference создан
    assert result["snapshot_error"] is None
    # quick_check на mock-справочнике не «ok» → снимок сохранён (02.11: 0 в покое
    # только у ЦЕЛОГО справочника) — и лежит в доме справочника, не в backups/ тенанта
    snaps = list((tmp_path / "snapshots").glob("pgs_reference.before_discovery_*.db"))
    assert len(snaps) == 1 and (snaps[0].stat().st_mode & 0o777) == 0o600, snaps
    assert not list((tmp_path / "backups").glob("pgs_reference*")), "снимок ушёл в backups/ тенанта"


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


# ── 02.11.2026: один писатель, снимок «0 в покое», место вычисляется ─────────
# Инцидент: три запуска discovery на одной машине (бот контейнера, бот партнёра,
# launchd партнёра) снимали по копии общего справочника 16 ГБ в свои backups/ и
# держали по 5 штук; контейнер (справочник :ro) ещё и падал 100 раз на записи.

def _run_one_candidate(tmp_path, monkeypatch, *, ref_ok=True, writable=True, room_ok=True):
    conn = _setup_dbs(tmp_path, monkeypatch, [], genome_import_id=1)
    ref = _mock_ref([])
    monkeypatch.setattr(pgs_reference, "get_ref_conn", lambda: ref)
    monkeypatch.setattr(pgs_reference, "writable", lambda: writable)
    monkeypatch.setattr(pgs_reference, "import_room", lambda n: {
        "ok": room_ok, "free": 1, "need": 2, "ref_size": 1, "models": 1})
    monkeypatch.setattr(pgsd, "_ref_ok", lambda r: ref_ok)
    monkeypatch.setattr(pgsd.pp, "unscored_models", lambda c, gid: ["PGS1"])
    imp = MagicMock(return_value={"status": "ok", "n_inserted": 5})
    phase_h = MagicMock(return_value={"results": {}})
    with patch("requests.get") as mock_get:
        mock_get.return_value.raise_for_status = MagicMock()
        mock_get.return_value.json.return_value = _api_page([_make_api_score("PGS000555")])
        monkeypatch.setattr(pgsd.gw, "download_and_import", imp)
        monkeypatch.setattr(pgsd.pp, "run", phase_h)
        result = pgsd.discover_and_import_new_scores(conn)
    snaps = list((tmp_path / "snapshots").glob("*.db"))
    return SimpleNamespace(result=result, ref=ref, imp=imp, get=mock_get, phase_h=phase_h, snaps=snaps)


@pytest.mark.unit
def test_snapshot_removed_when_reference_intact(tmp_path, monkeypatch):
    r = _run_one_candidate(tmp_path, monkeypatch, ref_ok=True)
    r.ref.backup.assert_called_once()
    assert r.snaps == [], "целый справочник после импорта — снимок обязан исчезнуть"
    assert r.result["errors"] == []


@pytest.mark.unit
def test_snapshot_kept_and_loud_when_reference_damaged(tmp_path, monkeypatch):
    r = _run_one_candidate(tmp_path, monkeypatch, ref_ok=False)
    assert len(r.snaps) == 1
    assert [e["pgs_id"] for e in r.result["errors"]] == ["R2_ref_check"]


@pytest.mark.unit
def test_read_only_reference_neither_imports_nor_snapshots(tmp_path, monkeypatch):
    """Контейнер владельца: справочник :ro → ни каталога, ни копии, ни ошибок — только Phase H."""
    r = _run_one_candidate(tmp_path, monkeypatch, writable=False)
    r.get.assert_not_called()
    r.imp.assert_not_called()
    r.ref.backup.assert_not_called()
    assert r.result["errors"] == [] and r.snaps == []
    r.phase_h.assert_called_once()   # свои баллы по чужим докачкам — считаются


@pytest.mark.unit
def test_no_room_stops_before_snapshot(tmp_path, monkeypatch):
    r = _run_one_candidate(tmp_path, monkeypatch, room_ok=False)
    r.imp.assert_not_called()
    r.ref.backup.assert_not_called()
    assert [e["pgs_id"] for e in r.result["errors"]] == ["R2_guard"]


@pytest.mark.unit
def test_leftover_snapshot_resolved_by_check(tmp_path, monkeypatch):
    """Снимок прошлого прогона: справочник цел → удаляется и импорт идёт; не цел → стоп."""
    (tmp_path / "snapshots").mkdir()
    old = tmp_path / "snapshots" / "pgs_reference.before_discovery_20261002_0700.db"
    old.write_bytes(b"x")
    r = _run_one_candidate(tmp_path, monkeypatch, ref_ok=True)
    assert not old.exists() and r.imp.called

    old.write_bytes(b"x")
    r = _run_one_candidate(tmp_path, monkeypatch, ref_ok=False)
    assert old.exists() and not r.imp.called
    assert [e["pgs_id"] for e in r.result["errors"]] == ["R2_ref_check"]


@pytest.mark.unit
def test_import_room_is_computed_from_reference(tmp_path, monkeypatch):
    """Нужно = справочник + средняя модель × моделей за прогон × 2. Литерала нет."""
    monkeypatch.setenv("HEALTH_PGS_DB", str(tmp_path / "pgs_ref.db"))
    ref = pgs_reference.get_ref_conn()
    for pid in ("PGS1", "PGS2"):
        ref.execute("INSERT INTO pgs_catalog (pgs_id, trait_name, downloaded_at) VALUES (?,?,?)",
                    (pid, pid, "2026-01-01"))
    ref.commit()
    ref.close()
    size = (tmp_path / "pgs_ref.db").stat().st_size
    need = size + (size // 2) * 10 * 2
    import shutil
    for free, ok in ((need, True), (need - 1, False)):
        monkeypatch.setattr(shutil, "disk_usage", lambda p, f=free: SimpleNamespace(free=f))
        room = pgs_reference.import_room(10)
        assert (room["need"], room["ok"]) == (need, ok)


@pytest.mark.unit
def test_room_alarm_names_free_need_and_hanging_snapshots(tmp_path, monkeypatch):
    """Предикат ночного датчика места: молчит при запасе, при нехватке называет числа и
    висящие снимки (их размер — то, что можно освободить)."""
    monkeypatch.setenv("HEALTH_PGS_DB", str(tmp_path / "pgs_ref.db"))
    pgs_reference.get_ref_conn().close()
    (tmp_path / "snapshots").mkdir()
    (tmp_path / "snapshots" / "pgs_reference.before_discovery_20261102_0701.db").write_bytes(b"x" * 10)
    import shutil
    monkeypatch.setattr(shutil, "disk_usage", lambda p: SimpleNamespace(free=10**12))
    assert pgs_reference.room_alarm(100) is None
    monkeypatch.setattr(shutil, "disk_usage", lambda p: SimpleNamespace(free=0))
    msg = pgs_reference.room_alarm(100)
    assert msg and "свободно 0.0 ГБ" in msg and "снимков висит 1" in msg, msg


# ── 02.11.2026: отбор шкал по покрытию (решение владельца «целиком») ─────────
# Замер: на чип-геномах установки 706 из 772 шкал дали <40% и несли ~все 16,5 ГБ;
# из 439 шкал крупнее 1 тыс. вариантов ни одна не дотянула до 40%.

def _tenant_db(path, positions, cov: dict):
    """Канон тенанта для свидетельства: геном из `positions` SNP и шкалы {pid: (m, t)}."""
    c = sqlite3.connect(path)
    c.executescript("CREATE TABLE genome_imports (id INTEGER PRIMARY KEY);"
                    "CREATE TABLE raw_snps (rsid TEXT);"
                    "CREATE TABLE prs_scores (pgs_id TEXT, snps_matched INT, snps_total INT,"
                    " genome_import_id INT);")
    c.execute("INSERT INTO genome_imports (id) VALUES (1)")
    c.executemany("INSERT INTO raw_snps VALUES (?)", [(f"rs{i}",) for i in range(positions)])
    c.executemany("INSERT INTO prs_scores VALUES (?,?,?,1)", [(p, m, t) for p, (m, t) in cov.items()])
    c.commit()
    c.close()
    return path


def _seed_ref(models: dict):
    """Справочник с моделями {pid: число весов}."""
    ref = pgs_reference.get_ref_conn()
    for pid, n in models.items():
        ref.execute("INSERT INTO pgs_catalog (pgs_id, trait_name, num_variants, downloaded_at)"
                    " VALUES (?,?,?,?)", (pid, pid, n, "2026-01-01"))
        ref.executemany("INSERT INTO pgs_weights (pgs_id, rsid, effect_allele, effect_weight)"
                        " VALUES (?,?,?,?)", [(pid, f"rs{i}", "A", 0.1) for i in range(n)])
    ref.commit()
    ref.close()


def _discover(conn, listing):
    with patch("requests.get") as g:
        g.return_value.raise_for_status = MagicMock()
        g.return_value.json.return_value = _api_page(listing)
        imp = MagicMock(return_value={"status": "ok", "n_inserted": 1})
        with patch.object(pgsd.gw, "download_and_import", imp), \
             patch.object(pgsd.pp, "run", MagicMock(return_value={"results": {}})), \
             patch.object(pgsd, "_ref_ok", lambda r: True):
            return pgsd.discover_and_import_new_scores(conn), imp


@pytest.mark.unit
def test_unusable_for_all_tenants_is_pruned_and_never_redownloaded(tmp_path, monkeypatch):
    conn = _setup_dbs(tmp_path, monkeypatch, [])
    _seed_ref({"PGSGOOD": 100, "PGSBAD": 5000, "PGSHALF": 3000})
    a = _tenant_db(tmp_path / "a.db", 50, {"PGSGOOD": (90, 100), "PGSBAD": (100, 5000), "PGSHALF": (2900, 3000)})
    b = _tenant_db(tmp_path / "b.db", 50, {"PGSGOOD": (80, 100), "PGSBAD": (200, 5000), "PGSHALF": (100, 3000)})
    monkeypatch.setattr(pgsd, "_evidence_db_paths", lambda: [a, b])

    res, _ = _discover(conn, [])
    assert res["pruned"] == ["PGSBAD"], res["pruned"]   # PGSHALF полезна одному — остаётся
    ref = pgs_reference.get_ref_conn()
    assert ref.execute("SELECT COUNT(*) FROM pgs_weights WHERE pgs_id='PGSBAD'").fetchone()[0] == 0
    assert ref.execute("SELECT COUNT(*) FROM pgs_weights WHERE pgs_id='PGSHALF'").fetchone()[0] == 3000
    assert set(pgs_reference.rejected(ref)) == {"PGSBAD"}
    ref.close()

    res, imp = _discover(conn, [_make_api_score("PGSBAD", nv=60)])
    imp.assert_not_called()                              # отвергнутая не качается снова
    assert res["skipped_existing"] == 1


@pytest.mark.unit
def test_new_scores_larger_than_proven_bound_are_skipped(tmp_path, monkeypatch):
    conn = _setup_dbs(tmp_path, monkeypatch, [])
    _seed_ref({"PGSGOOD": 120})
    a = _tenant_db(tmp_path / "a.db", 50, {"PGSGOOD": (100, 120)})
    monkeypatch.setattr(pgsd, "_evidence_db_paths", lambda: [a])

    res, imp = _discover(conn, [_make_api_score("PGSSMALL", nv=110),
                                _make_api_score("PGSHUGE", nv=500000)])
    assert res["size_bound"] == 120 and res["skipped_size"] == 1
    assert [c.args[0] for c in imp.call_args_list] == ["PGSSMALL"]


@pytest.mark.unit
def test_bigger_genome_reopens_rejected_and_lifts_bound(tmp_path, monkeypatch):
    """Выход из круга: отказы, вынесенные на геномах меньше нынешнего, пересматриваются."""
    conn = _setup_dbs(tmp_path, monkeypatch, [])
    _seed_ref({"PGSGOOD": 120})
    ref = pgs_reference.get_ref_conn()
    pgs_reference.ensure_writer_tables(ref)
    ref.execute("INSERT INTO pgs_rejected (pgs_id, num_variants, best_coverage_pct, genome_positions,"
                " rejected_at) VALUES ('PGSOLD', 5000, 3.0, 50, '2026-11-02')")
    # отказ по формату на любом геноме остаётся отказом
    ref.execute("INSERT INTO pgs_rejected VALUES ('PGSDOSE', 100, 0, 0, '2026-11-03', 'format')")
    ref.commit()
    ref.close()
    big = _tenant_db(tmp_path / "wgs.db", 80, {"PGSGOOD": (110, 120)})
    monkeypatch.setattr(pgsd, "_evidence_db_paths", lambda: [big])

    res, imp = _discover(conn, [_make_api_score("PGSOLD", nv=5000), _make_api_score("PGSDOSE", nv=100)])
    assert res["size_bound"] is None
    assert [c.args[0] for c in imp.call_args_list] == ["PGSOLD"]


@pytest.mark.unit
def test_consilium_block_carries_only_usable_scores():
    import prs_context
    c = sqlite3.connect(":memory:")
    c.executescript("CREATE TABLE genome_imports (id INTEGER PRIMARY KEY);"
                    "INSERT INTO genome_imports VALUES (1);"
                    "CREATE TABLE prs_scores (pgs_id TEXT, trait_label TEXT, raw_score REAL,"
                    " snps_matched INT, snps_total INT, computed_at TEXT, genome_import_id INT);")
    c.executemany("INSERT INTO prs_scores VALUES (?,?,0.1,?,?,'2026-11-02',1)",
                  [("PGS1", "Хорошая", 90, 100), ("PGS2", "Средняя", 45, 100), ("PGS3", "Плохая", 5, 100)])
    block = prs_context.build_prs_block(c)
    assert "Хорошая" in block and "Средняя" in block and "Плохая" not in block
    assert "Не показано 1 шкал" in block


# ── 02.11.2026: квитанция писателя и датчик «докачка жива» ───────────────────

@pytest.mark.unit
def test_writer_leaves_receipt_with_due_and_reader_does_not(tmp_path, monkeypatch):
    conn = _setup_dbs(tmp_path, monkeypatch, [])
    _seed_ref({"PGSGOOD": 10})
    with patch("requests.get") as g:
        g.return_value.raise_for_status = MagicMock()
        g.return_value.json.return_value = _api_page([])
        with patch.object(pgsd.pp, "run", MagicMock(return_value={"results": {}})):
            pgsd.discover_and_import_new_scores(conn, "2026-12-03T05:00:00+00:00")
    ref = pgs_reference.get_ref_conn()
    rows = ref.execute("SELECT writer, capped, next_due_at FROM discovery_runs").fetchall()
    # писатель — имя корня тенанта (<корень>/data/health.db), без пути
    assert rows == [(tmp_path.name, 0, "2026-12-03T05:00:00+00:00")], rows
    ref.close()

    monkeypatch.setattr(pgs_reference, "writable", lambda: False)   # читатель (:ro)
    with patch.object(pgsd.pp, "run", MagicMock(return_value={"results": {}})):
        pgsd.discover_and_import_new_scores(conn, "2027-01-03T05:00:00+00:00")
    ref = pgs_reference.get_ref_conn()
    assert ref.execute("SELECT COUNT(*) FROM discovery_runs").fetchone()[0] == 1
    ref.close()


@pytest.mark.unit
def test_writer_alarm_red_only_when_capped_run_is_overdue(tmp_path, monkeypatch):
    import datetime as dt
    monkeypatch.setenv("HEALTH_PGS_DB", str(tmp_path / "pgs_ref.db"))
    ref = pgs_reference.get_ref_conn()
    assert pgs_reference.writer_alarm() is None            # таблицы нет — до первого прогона
    pgs_reference.ensure_writer_tables(ref)
    ref.commit()
    assert "ни разу" in pgs_reference.writer_alarm()      # таблица есть, квитанций нет
    ref.execute("INSERT INTO discovery_runs VALUES ('2026-11-02T05:10:00+00:00','health_partner',"
                "100,0,1,'2026-12-03T05:00:00+00:00')")
    ref.commit()
    ref.close()
    utc = dt.timezone.utc
    assert pgs_reference.writer_alarm(dt.datetime(2026, 12, 3, 4, 0, tzinfo=utc)) is None
    msg = pgs_reference.writer_alarm(dt.datetime(2026, 12, 3, 6, 0, tzinfo=utc))
    assert msg and "встала" in msg and "health_partner" in msg


@pytest.mark.unit
def test_hollow_scores_and_orphan_weights_are_cleared(tmp_path, monkeypatch):
    """Шкала без весов снова становится кандидатом; веса без шкалы уходят (замер 03.11: 26 пустых)."""
    conn = _setup_dbs(tmp_path, monkeypatch, [])
    _seed_ref({"PGSGOOD": 10})
    ref = pgs_reference.get_ref_conn()
    ref.execute("INSERT INTO pgs_catalog (pgs_id, trait_name, num_variants, downloaded_at)"
                " VALUES ('PGSHOLLOW','x',100,'2026-08-02')")
    ref.execute("INSERT INTO pgs_weights (pgs_id, rsid, effect_allele, effect_weight)"
                " VALUES ('PGSORPHAN','rs1','A',0.1)")
    ref.commit()
    ref.close()

    res, imp = _discover(conn, [_make_api_score("PGSHOLLOW", nv=100)])
    assert [c.args[0] for c in imp.call_args_list] == ["PGSHOLLOW"]   # снова кандидат
    ref = pgs_reference.get_ref_conn()
    assert ref.execute("SELECT COUNT(*) FROM pgs_weights WHERE pgs_id='PGSORPHAN'").fetchone()[0] == 0
    assert ref.execute("SELECT COUNT(*) FROM pgs_weights WHERE pgs_id='PGSGOOD'").fetchone()[0] == 10
    ref.close()


@pytest.mark.unit
def test_unsupported_format_is_remembered_not_an_error(tmp_path, monkeypatch):
    """03.11: 8 из 100 шкал — веса по дозе генотипа; не ошибка и не повод качать снова."""
    conn = _setup_dbs(tmp_path, monkeypatch, [])
    _seed_ref({"PGSGOOD": 10})
    with patch("requests.get") as g:
        g.return_value.raise_for_status = MagicMock()
        g.return_value.json.return_value = _api_page([_make_api_score("PGSDOSE", nv=100)])
        with patch.object(pgsd.gw, "download_and_import",
                          MagicMock(return_value={"status": "unsupported_format", "pgs_id": "PGSDOSE"})), \
             patch.object(pgsd.pp, "run", MagicMock(return_value={"results": {}})), \
             patch.object(pgsd, "_ref_ok", lambda r: True):
            res = pgsd.discover_and_import_new_scores(conn)
    assert res["errors"] == [] and res["skipped_format"] == 1
    ref = pgs_reference.get_ref_conn()
    assert ref.execute("SELECT reason FROM pgs_rejected WHERE pgs_id='PGSDOSE'").fetchone() == ("format",)
    ref.close()
    res2, imp = _discover(conn, [_make_api_score("PGSDOSE", nv=100)])
    imp.assert_not_called()


@pytest.mark.unit
def test_old_rejected_table_gets_reason_column(tmp_path, monkeypatch):
    """Справочник Studio завёл pgs_rejected 02.11 без причины — миграция на месте."""
    monkeypatch.setenv("HEALTH_PGS_DB", str(tmp_path / "pgs_ref.db"))
    ref = pgs_reference.get_ref_conn()
    ref.execute("CREATE TABLE pgs_rejected (pgs_id TEXT PRIMARY KEY, num_variants INTEGER NOT NULL,"
                " best_coverage_pct REAL NOT NULL, genome_positions INTEGER NOT NULL, rejected_at TEXT NOT NULL)")
    ref.execute("INSERT INTO pgs_rejected VALUES ('PGSOLD', 1, 1.0, 1, 'x')")
    ref.commit()
    pgs_reference.ensure_writer_tables(ref)
    assert ref.execute("SELECT reason FROM pgs_rejected").fetchone() == ("coverage",)
    ref.close()
