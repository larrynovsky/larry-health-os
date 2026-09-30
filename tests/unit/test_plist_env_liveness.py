#!/usr/bin/env python3.11
"""Юнит-тесты чистого ядра plist_env_liveness.parse_stale_env (без launchctl/IO).

BL-MULTITENANT-1: датчик «плист новее загруженной джобы». Проверяем матрицу
declares × loaded × multitenant, включая positive-control (стейл ловится).
"""
import plist_env_liveness as pel


def _declares(has):
    return lambda label: label in has


def test_multitenant_off_never_stale():
    # даже при дрейфе: multitenant выключен → гарда R1 нет → тихо
    stale = pel.parse_stale_env(
        ["a"], _declares({"a"}), lambda l: False, multitenant_on=False)
    assert stale == []


def test_declares_but_not_loaded_is_stale():
    # POSITIVE CONTROL: сканер ОБЯЗАН поймать стейл, иначе датчик мёртв
    stale = pel.parse_stale_env(
        ["lit-search"], _declares({"lit-search"}), lambda l: False, multitenant_on=True)
    assert stale == ["lit-search"]


def test_declares_and_loaded_is_clean():
    stale = pel.parse_stale_env(
        ["lit-search"], _declares({"lit-search"}), lambda l: True, multitenant_on=True)
    assert stale == []


def test_not_declaring_is_skipped():
    # плист без HEALTH_DATA_DIR (напр. multitenant-env-сеттер) — не наша забота
    stale = pel.parse_stale_env(
        ["setter"], _declares(set()), lambda l: False, multitenant_on=True)
    assert stale == []


def test_not_loaded_none_is_skipped():
    # loaded вернул None (джоба disabled/не загружена) → не стейл, пропуск
    stale = pel.parse_stale_env(
        ["disabled"], _declares({"disabled"}), lambda l: None, multitenant_on=True)
    assert stale == []


if __name__ == "__main__":
    for _n, _f in sorted(globals().items()):
        if _n.startswith("test_") and callable(_f):
            _f()
    print("TEST PASS")


def test_missing_tenant_db_seen_from_plist_not_from_disk(tmp_path):
    """BL-BRIEF-TENANT-REGISTRY-1: тенант, которого обслуживает launchd, а его БД удалена
    целиком, виден — реестр ожидаемых берётся из плистов, не из обхода диска."""
    import plist_env_liveness as pel
    alive, gone = tmp_path / "health", tmp_path / "health_partner"
    (alive / "data").mkdir(parents=True)
    (alive / "data" / "health.db").write_text("x")
    assert pel.missing_tenant_dbs({str(alive)}) == []
    assert pel.missing_tenant_dbs({str(alive), str(gone)}) == [str(gone)]
