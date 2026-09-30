"""Основная машина — данные установки (private/infra.yaml), а не литерал имени хоста.
Краснеет, если is_primary вернётся к сравнению с зашитым именем или если «нет ключа»
перестанет значить «нет основной машины» (громкий отказ, а не тихая запись где попало)."""
import subprocess
import sys
from pathlib import Path

import pytest

import infra_config

pytestmark = pytest.mark.unit
_CLI = Path(infra_config.__file__)


def test_основная_машина_из_конфига_терпит_суффикс_local(monkeypatch):
    monkeypatch.setattr(infra_config, "PRIMARY_HOST", "zz-host.local")
    assert infra_config.is_primary("zz-host") and infra_config.is_primary("zz-host.local")
    assert not infra_config.is_primary("zz-other.local")


def test_нет_ключа_нет_основной_машины(monkeypatch):
    monkeypatch.setattr(infra_config, "PRIMARY_HOST", None)
    assert not infra_config.is_primary("zz-host")


def test_cli_для_shell_сайтов():
    host = infra_config.PRIMARY_HOST or "zz-nobody"
    ok = subprocess.run([sys.executable, str(_CLI), "is-primary", host]).returncode
    no = subprocess.run([sys.executable, str(_CLI), "is-primary", "zz-not-this-one"]).returncode
    assert (ok, no) == ((0 if infra_config.PRIMARY_HOST else 1), 1)
