"""Consistency: каждый партнёрский launchd-плист должен задавать HEALTH_SECRETS_DIR.

Инцидент 2026-07-03: watcher.partner имел HEALTH_DATA_DIR=health_partner, но БЕЗ
HEALTH_SECRETS_DIR → процессы тенанта откатывались на секреты владельца
(~/.health_secrets) → кросс-тенант утечка Oura-данных владельца в БД партнёра.
Датчик ловит весь класс: тенант-процесс без своего secrets-каталога.
"""
from pathlib import Path

import pytest

_LAUNCHD = Path(__file__).resolve().parent.parent.parent / "launchd"
_PARTNER_PLISTS = sorted(_LAUNCHD.glob("*partner*.plist"))


@pytest.mark.owner_data
def test_partner_plists_exist():
    assert _PARTNER_PLISTS, f"нет партнёрских плистов в {_LAUNCHD}"


@pytest.mark.parametrize("plist", _PARTNER_PLISTS, ids=lambda p: p.name)
def test_partner_plist_has_both_env(plist):
    """Партнёрский процесс обязан задавать И HEALTH_DATA_DIR, И HEALTH_SECRETS_DIR —
    иначе secrets_dir() возьмёт секреты владельца (кросс-тенант утечка)."""
    txt = plist.read_text()
    assert "HEALTH_DATA_DIR" in txt, f"{plist.name}: нет HEALTH_DATA_DIR"
    assert "HEALTH_SECRETS_DIR" in txt, (
        f"{plist.name}: нет HEALTH_SECRETS_DIR — процесс тенанта откатится на "
        "секреты владельца (кросс-тенант утечка, инцидент 2026-07-03 watcher.partner)")
    assert "health_partner" in txt and ".health_secrets_partner" in txt, (
        f"{plist.name}: пути должны указывать на каталоги партнёра")
