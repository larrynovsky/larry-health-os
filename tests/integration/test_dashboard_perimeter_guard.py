"""Браузер внутри периметра: чужая страница не пишет в дашборд и не читает его под чужим именем.

Падение первых двух = вернулась подделка запроса (форма с evil.example проводит промоут анализов);
третьего = вернулась подмена имени (DNS rebinding читает медкарту); последних — сторож сломал
законные пути (телефон без Origin, своя страница дашборда)."""
import pytest

PROMOTE = "/lab-review/rr/promote"
FORM = {"tenant": "", "execute": "0"}


def test_cross_origin_write_refused(dashboard_client):
    client, _ = dashboard_client
    r = client.post(PROMOTE, data=FORM, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


def test_cross_site_fetch_metadata_refused(dashboard_client):
    client, _ = dashboard_client
    r = client.post(PROMOTE, data=FORM, headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403


@pytest.mark.parametrize("host", ["evil.example", "evil.example:8001", "127.0.0.1.nip.io"])
def test_public_host_name_refused(dashboard_client, host):
    client, _ = dashboard_client
    assert client.get("/", headers={"Host": host}).status_code == 421


@pytest.mark.parametrize("host", ["127.0.0.1:8001", "localhost:8001", "[::1]:8001", "studio", "studio.tail1234.ts.net"])
def test_local_host_names_pass(dashboard_client, host):
    client, _ = dashboard_client
    assert client.get("/", headers={"Host": host}).status_code != 421


@pytest.mark.parametrize("headers", [{}, {"Origin": "http://127.0.0.1:8001"}, {"Origin": "https://studio.tail1234.ts.net"}])
def test_legit_writes_pass_the_guard(dashboard_client, headers, monkeypatch):
    client, _ = dashboard_client
    import subprocess
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, "ok", ""))
    assert client.post(PROMOTE, data=FORM, headers=headers).status_code == 200


def test_run_id_cannot_become_a_flag(dashboard_client):
    client, _ = dashboard_client
    assert client.post("/lab-review/--execute/promote", data=FORM).status_code == 400
