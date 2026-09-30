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
    assert "Промоутнуть" in r.text and "Проверить" in r.text


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
