"""Приём с телефона по Wi-Fi (ingest_lan.py) не показывает дашборд и не пускает браузер.

Негативный контроль (02.10): подключить в lan_ingest_app роутер views — краснеет
`test_страниц_дашборда_нет`; снять middleware _no_browsers — краснеет `test_браузер_отвергнут`.
"""
import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    (tmp_path / "hae_ingest_token").write_text("t-ok\n")
    monkeypatch.setenv("HEALTH_SECRETS_DIR", str(tmp_path))
    monkeypatch.setenv("ALLOW_WRITE_NONPRIMARY", "1")
    from ingest_lan import lan_ingest_app
    with TestClient(lan_ingest_app()) as c:
        yield c


def test_страниц_дашборда_нет(client):
    for path in ("/", "/profile", "/medical-record", "/labs", "/static/dashboard.css", "/docs"):
        assert client.get(path).status_code == 404, path


def test_приём_на_месте_и_под_токеном(client):
    assert client.post("/hae/ingest", content=b"{}").status_code == 401
    assert client.post("/hae/ingest", content=b"{}", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.post("/location/ingest", json={}).status_code != 404   # маршрут на месте


def test_браузер_отвергнут(client):
    r = client.post("/hae/ingest", content=b"{}",
                    headers={"Authorization": "Bearer t-ok", "Origin": "http://evil.example"})
    assert r.status_code == 403
    r = client.post("/hae/ingest", content=b"{}",
                    headers={"Authorization": "Bearer t-ok", "Sec-Fetch-Site": "same-origin"})
    assert r.status_code == 403


def test_по_умолчанию_порт_только_на_loopback():
    import yaml
    from scripts import install
    svc = yaml.safe_load(install.render_docker("UTC")["compose.yaml"])["services"]
    assert svc["ingest"]["ports"] == ["${HEALTH_INGEST_BIND:-127.0.0.1}:8011:8011"]
    assert svc["ingest"]["environment"]["INGEST_HOST"] == "0.0.0.0"
    assert svc["dashboard"]["ports"] == ["127.0.0.1:8001:8001"]
