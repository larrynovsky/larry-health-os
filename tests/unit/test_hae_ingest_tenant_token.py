"""Вход /hae/ingest читает токен из секретов СВОЕГО человека (secrets_dir), не из ~/.health_secrets.

До 23.09 путь был жёстко `Path.home()/.health_secrets`: дашборд второго человека пускал
только токен владельца. Негативный контроль: вернуть жёсткий путь — `test_токен_владельца_не_пускает`
и `test_свой_токен_пускает` краснеют (HOME подменён на каталог с ДРУГИМ токеном).
"""
import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi import HTTPException  # noqa: E402


@pytest.fixture
def two_people(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".health_secrets").mkdir(parents=True)
    (home / ".health_secrets" / "hae_ingest_token").write_text("t-owner\n")
    tenant = tmp_path / "tenant_secrets"
    tenant.mkdir()
    (tenant / "hae_ingest_token").write_text("t-tenant\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("HEALTH_SECRETS_DIR", str(tenant))
    from dashboard_routers import api_hae_ingest
    return api_hae_ingest, tenant


def test_свой_токен_пускает(two_people):
    mod, _ = two_people
    mod._check_token("Bearer t-tenant", None)
    mod._check_token(None, "t-tenant")


def test_токен_владельца_не_пускает(two_people):
    mod, _ = two_people
    with pytest.raises(HTTPException) as e:
        mod._check_token("Bearer t-owner", None)
    assert e.value.status_code == 401


def test_нет_токена_у_человека_503(two_people):
    mod, tenant = two_people
    (tenant / "hae_ingest_token").unlink()
    with pytest.raises(HTTPException) as e:
        mod._check_token("Bearer t-owner", None)
    assert e.value.status_code == 503
