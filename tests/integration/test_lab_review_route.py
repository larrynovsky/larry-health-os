"""Тесты роута ревью анализов /lab-review (dashboard).

Закрывает дыру: роут раньше не имел автотестов. Проверяем: рендер staging
тенанта, пустой прогон, санитизацию тенанта (анти-traversal), и что промоут
собирает правильную команду lab_promote (subprocess замокан — без записи в канон).
"""
import sqlite3
import pytest


def _make_tenant_db(path):
    c = sqlite3.connect(path)
    c.execute(
        "CREATE TABLE lab_results_staging(run_id TEXT, source_file TEXT, page INT, "
        "raw_line TEXT, canonical_name TEXT, value REAL, unit TEXT, ref_low REAL, "
        "ref_high REAL, value_agreement TEXT, oracle_status TEXT, review_status TEXT)"
    )
    c.execute(
        "INSERT INTO lab_results_staging VALUES"
        "('rr','doc.pdf',1,'Glucose','Glucose',107.0,'mg/dL',70,99,'agree','green','pending')"
    )
    c.commit(); c.close()


def test_get_renders_staging(dashboard_client, tmp_path, monkeypatch):
    client, _ = dashboard_client
    tdb = tmp_path / "t.db"; _make_tenant_db(tdb)
    import dashboard_routers.api_lab_review as m
    monkeypatch.setattr(m, "_tenant_db", lambda tenant: tdb)
    r = client.get("/lab-review/rr?tenant=partner")
    assert r.status_code == 200
    assert "Glucose" in r.text
    assert "Добавить в базу" in r.text and "Проверить" in r.text


def test_get_empty_run(dashboard_client, tmp_path, monkeypatch):
    client, _ = dashboard_client
    tdb = tmp_path / "t.db"; _make_tenant_db(tdb)
    import dashboard_routers.api_lab_review as m
    monkeypatch.setattr(m, "_tenant_db", lambda tenant: tdb)
    r = client.get("/lab-review/NOPE?tenant=partner")
    assert r.status_code == 200
    assert "Нет строк" in r.text


def test_bad_tenant_rejected(dashboard_client):
    client, _ = dashboard_client
    r = client.get("/lab-review/rr?tenant=../etc")
    assert r.status_code == 400   # анти-traversal санитизация


def test_promote_dryrun_builds_command(dashboard_client, tmp_path, monkeypatch):
    client, _ = dashboard_client
    import dashboard_routers.api_lab_review as m
    monkeypatch.setattr(m, "_tenant_dir", lambda t: tmp_path / "tenant")

    captured = {}

    class _R:
        returncode = 0
        stdout = "DRY-RUN ok"
        stderr = ""

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        captured["env"] = kw.get("env", {})
        return _R()

    monkeypatch.setattr(m.subprocess, "run", fake_run)
    r = client.post("/lab-review/rr/promote", data={"tenant": "partner", "execute": "0"})
    assert r.status_code == 200
    assert "lab_promote.py" in captured["cmd"]
    assert "--run-id" in captured["cmd"] and "rr" in captured["cmd"]
    assert "--execute" not in captured["cmd"]                     # dry-run
    assert str(tmp_path / "tenant") == captured["env"]["HEALTH_DATA_DIR"]  # per-tenant


def test_own_sheet_reads_current_database_and_waits_for_click(dashboard_client, monkeypatch, tmp_path):
    """Мутации: ~/health вместо DB_PATH; скрыть auto/review/gold; выполнить без клика."""
    client, db = dashboard_client
    import health_db
    import dashboard_routers.api_lab_review as m
    from pathlib import Path
    synthetic_home = tmp_path / "unrelated-home"
    synthetic_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: synthetic_home))
    db.add_profile("identity.language", "en", category="identity")
    for status in ("auto", "review", "gold", "pending", "promoted", "rejected", "specialized"):
        db.execute("INSERT INTO lab_results_staging "
                   "(run_id,extractor_version,source_file,date,review_status,raw_line,value) "
                   "VALUES ('self_run','test',?,'2030-01-01',?, ?,5.0)",
                   (status + '.pdf', status, status + '_VALUE'))
    calls = []
    class Result:
        returncode = 0
        stdout = "synthetic confirmation"
        stderr = ""
    monkeypatch.setattr(m.subprocess, "run", lambda cmd, **kw: calls.append((cmd, kw)) or Result())
    r = client.get("/lab-review/self_run")
    assert r.status_code == 200 and ">Add to database</button>" in r.text
    for s in ("auto", "review", "gold", "pending"):
        assert s + '_VALUE' in r.text
    for s in ("promoted", "rejected", "specialized"):
        assert s + '_VALUE' not in r.text
    assert not calls, "открытие листа не подтверждает числа"
    r = client.post("/lab-review/self_run/promote", data={"execute": "1", "tenant": ""})
    assert r.status_code == 200 and len(calls) == 1
    assert "Adding lab results" in r.text and "Back to report" in r.text
    assert "--execute" in calls[0][0]
    assert calls[0][1]["env"]["HEALTH_DATA_DIR"] == str(health_db.DB_PATH.parent.parent)


def test_tenant_sheet_cannot_target_foreign_database(dashboard_client, monkeypatch, tmp_path):
    """Новый собственный маршрут не открывает чужую БД; операторский путь остаётся."""
    client, _ = dashboard_client
    import dashboard_routers.api_lab_review as m
    from pathlib import Path
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(m, "is_owner", lambda: False)
    assert client.get("/lab-review/rr?tenant=foreign").status_code == 403
    assert client.post("/lab-review/rr/promote", data={"tenant": "foreign", "execute": "1"}).status_code == 403
    monkeypatch.setattr(m, "is_owner", lambda: True)
    assert m._tenant_dir("foreign") == Path.home() / "foreign"


def test_sheet_escapes_recognised_test_name(dashboard_client, tmp_path, monkeypatch):
    """Имя из модели/назначения — данные. Мутация: интерполировать canonical_name как HTML."""
    client, _ = dashboard_client
    tdb = tmp_path / "t.db"; _make_tenant_db(tdb)
    with sqlite3.connect(tdb) as conn:
        conn.execute("UPDATE lab_results_staging SET canonical_name=?", ('<script>alert(1)</script>',))
    import dashboard_routers.api_lab_review as m
    monkeypatch.setattr(m, "_tenant_db", lambda tenant: tdb)
    r = client.get("/lab-review/rr")
    assert r.status_code == 200
    assert '<script>alert(1)</script>' not in r.text
    assert '&lt;script&gt;alert(1)&lt;/script&gt;' in r.text


def test_own_confirmation_really_promotes_auto_rows(dashboard_client):
    """Лист → настоящий CLI → тестовая БД; без операторской команды и без модели.
    Мутации: промоут до подтверждения; проигнорировать execute; записать в другую БД.
    """
    client, db = dashboard_client
    import health_db
    health_db.init_db()  # staging приходит после настоящих миграций приёмника, а не старого schema.sql
    db.execute("INSERT INTO lab_results_staging "
               "(run_id,extractor_version,source_file,date,raw_name,canonical_name,panel,value,unit,"
               "review_status,date_source,specimen,specimen_source,page_role) "
               "VALUES ('person_confirm','test','synthetic.jpg','2030-01-01','Glucose','Glucose',"
               "'chemistry',5.0,'mmol/L','auto','read','blood','read','data')")
    assert client.get("/lab-review/person_confirm").status_code == 200
    assert db.fetchone("SELECT COUNT(*) FROM lab_results")[0] == 0
    assert client.post("/lab-review/person_confirm/promote", data={"execute": "0"}).status_code == 200
    assert db.fetchone("SELECT COUNT(*) FROM lab_results")[0] == 0
    assert client.post("/lab-review/person_confirm/promote", data={"execute": "1"}).status_code == 200
    assert db.fetchone("SELECT COUNT(*) FROM lab_results")[0] == 1
    assert db.fetchone("SELECT review_status FROM lab_results_staging")[0] == "promoted"


def test_row_shows_printed_name_and_norm_as_on_the_blank(dashboard_client, tmp_path, monkeypatch):
    """Нить file-outcome (06.10): человек сверяет с бланком. Русский бланк сверяли с «HGB», а норму
    «< 0.2» видели как «–0.2». Мутации: прятать напечатанное имя при найденном международном;
    рисовать одну границу через тире."""
    client, _ = dashboard_client
    tdb = tmp_path / "t.db"; _make_tenant_db(tdb)
    c = sqlite3.connect(tdb)
    c.execute("INSERT INTO lab_results_staging VALUES"
              "('rr','doc.pdf',1,'Гемоглобин','HGB',15.0,'г/дл',13.1,17.2,'agree','green','pending')")
    c.execute("INSERT INTO lab_results_staging VALUES"
              "('rr','doc.pdf',1,'Базофилы, абс.','Basophils_abs',0.03,'тыс/мкл',NULL,0.2,'agree','green','pending')")
    c.commit(); c.close()
    import dashboard_routers.api_lab_review as m
    monkeypatch.setattr(m, "_tenant_db", lambda tenant: tdb)
    r = client.get("/lab-review/rr?tenant=partner")
    assert "Гемоглобин" in r.text and "HGB" in r.text
    assert "Базофилы, абс." in r.text and "&lt; 0.2" in r.text and "–0.2" not in r.text
    assert "13.1 – 17.2" in r.text
