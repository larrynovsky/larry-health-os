"""Датчик «реестр классов секретов против каталогов» на синтетических каталогах (ремонт 12.10).

Ночной отказ 06.10–15.10: на Studio легли withings_client_id, withings_client_secret,
withings_oauth.json, а в secrets_paths.SECRET_SCOPE их не объявили — FAIL владельцу и партнёру
каждую ночь. Ревью починки: тест полноты реестра по литералам ловит пропуск только после
переписывания читателя; ночной отказ тоже должен иметь регрессию. Здесь — тело
настоящего датчика на каталогах в tmp: живые секреты не читаются (§19), зелёный причинён
тестом, а не содержимым ~/.health_secrets машины (§20).

Граница вслух: класс `tenant` у Withings проверяется только как «объявлен»; правильность
выбора между tenant и owner — решение о развёртывании (у Oura так же), не свойство кода.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import integrity_tests as it

pytestmark = pytest.mark.unit

WITHINGS = ("withings_client_id", "withings_client_secret", "withings_oauth.json")


def _home(tmp_path, monkeypatch, owner, partner=None):
    (tmp_path / ".health_secrets").mkdir()
    for n in owner:
        (tmp_path / ".health_secrets" / n).write_text("x")
    if partner is not None:
        (tmp_path / ".health_secrets_partner").mkdir()
        for n in partner:
            (tmp_path / ".health_secrets_partner" / n).write_text("x")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))


def test_withings_files_are_declared(tmp_path, monkeypatch):
    """Ровно ночной случай: три файла Withings у владельца — датчик молчит (до правки — FAIL)."""
    _home(tmp_path, monkeypatch, WITHINGS)
    r = it.check_secret_scope_matches_reality()
    assert r["secrets_checked"] == 3


def test_undeclared_file_still_fails(tmp_path, monkeypatch):
    """Отрицательный контроль: необъявленное имя по-прежнему роняет датчик."""
    _home(tmp_path, monkeypatch, WITHINGS + ("withings_new_unknown_secret",))
    with pytest.raises(AssertionError, match="withings_new_unknown_secret: лежит в каталоге"):
        it.check_secret_scope_matches_reality()


def test_partner_without_withings_is_a_warn_not_fail(tmp_path, monkeypatch):
    """У партнёра Withings не подключён: правдивый WARN «tenant-секреты отсутствуют», не FAIL."""
    _home(tmp_path, monkeypatch, WITHINGS, partner=())
    said = []
    monkeypatch.setattr(it, "warn", lambda label, detail="": said.append((label, detail)))
    it.check_secret_scope_matches_reality()
    hit = [d for l, d in said if "tenant-секреты отсутствуют" in l]
    assert hit, said
