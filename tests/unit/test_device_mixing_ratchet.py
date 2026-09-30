"""Ратчет смешения приборов краснеет на РОСТЕ, а не на факте смешения.

Зелёный прогон здесь ничего не доказывает: датчик, чей предикат выродился, зелен так же,
как датчик на чистых данных. Поэтому каждый тест сначала портит вход, потом смотрит.

Мутации, на которых тесты обязаны покраснеть, названы в теле.
"""
from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.unit

from tests.unit.test_repair_sleep_cycle_stages import _daily_metrics_ddl

RING = {"source": "Oura", "total": 6.0}
PHONE = {"source": "Sleep Cycle", "total": 6.5}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import health_db as hdb

    monkeypatch.setattr(hdb, "DB_PATH", tmp_path / "t.db")
    with hdb.get_conn() as conn:
        conn.executescript(_daily_metrics_ddl())
    return hdb


def _rows(hdb, rows):
    """rows: список (дата, блок сна, есть ли ВСР)."""
    with hdb.get_conn() as conn:
        for day, sleep, hrv in rows:
            conn.execute(
                "INSERT OR REPLACE INTO daily_metrics (date, raw, hrv) VALUES (?,?,?)",
                (day, json.dumps({"sleep": sleep}, ensure_ascii=False),
                 27.5 if hrv else None))


def _run():
    import integrity_tests as it
    it._warnings.clear()
    res = it.check_sleep_device_mixing()
    return res, [w[0] for w in it._warnings]


def test_predicate_separates_devices():
    """Мутация: `!= "Oura"` заменить на `is not None` — кольцо станет чужим прибором."""
    import integrity_tests as it

    assert it._foreign_sleep_source({"sleep": PHONE}) == "Sleep Cycle"
    assert it._foreign_sleep_source({"sleep": RING}) is None
    assert it._foreign_sleep_source(None) is None
    assert it._foreign_sleep_source({"sleep": None}) is None
    assert it._foreign_sleep_source({}) is None


def test_unreadable_raw_is_loud_not_silent(env):
    """Мутация: вернуть разбор JSON внутрь предиката с `except: return None`.

    Тогда испорченная строка молча перестала бы считаться смешением, и датчик занижал бы
    счётчик — ровно тот отказ, ради которого он написан. Нечитаемое обязано быть слышно.
    """
    import integrity_tests as it

    with env.get_conn() as conn:
        conn.execute("INSERT INTO daily_metrics (date, raw, hrv) VALUES (?,?,?)",
                     ("2026-06-01", "{это не json", 27.5))
    res, warns = _run()
    assert res is None
    assert any("не отработал" in w for w in warns), warns


def test_known_level_is_silent(env, monkeypatch):
    """Мутация: сравнение `>` заменить на `>=` — датчик закричит на известном состоянии.

    Вымышленный уровень смешения задан самой фикстурой. Ратчет говорит о РОСТЕ,
    а не о самом факте смешения.
    """
    import integrity_tests as it

    monkeypatch.setattr(it, "SLEEP_DEVICE_MIXING_KNOWN", 6)
    _rows(env, [(f"2032-01-0{i}", PHONE, True) for i in range(1, 7)]
          + [("2032-02-01", RING, True)])
    res, warns = _run()
    assert res == {"mixed": 6, "known": 6}, res
    assert warns == [], warns


def test_growth_is_loud(env, monkeypatch):
    """Мутация: снять сравнение с известным уровнем — рост перестанет быть слышен."""
    import integrity_tests as it

    monkeypatch.setattr(it, "SLEEP_DEVICE_MIXING_KNOWN", 6)
    _rows(env, [(f"2032-01-0{i}", PHONE, True) for i in range(1, 8)])
    res, warns = _run()
    assert res["mixed"] == 7
    assert any("смешение приборов" in w for w in warns), warns


def test_foreign_sleep_without_hrv_is_not_mixing(env, monkeypatch):
    """Мутация: убрать `hrv IS NOT NULL` — строки одного прибора станут «смешением».

    Смешение — это ДВА прибора в ОДНОЙ строке веры. Чужой сон сам по себе не смешение:
    без ВСР строка в пары веры не попадает вовсе.
    """
    import integrity_tests as it

    monkeypatch.setattr(it, "SLEEP_DEVICE_MIXING_KNOWN", 0)
    _rows(env, [(f"2015-01-0{i}", PHONE, False) for i in range(1, 6)])
    res, warns = _run()
    assert res == {"mixed": 0, "known": 0}, res
    assert warns == [], warns


def test_degenerate_predicate_is_caught(env, monkeypatch):
    """Мутация: выродить предикат в «всегда None» — экзамен обязан это поймать.

    §14: молчащий датчик неотличим от датчика с мёртвым предикатом, и heartbeat это
    различие не ловит по построению. Ловит только экзамен на двух эталонах.
    """
    import integrity_tests as it

    monkeypatch.setattr(it, "_foreign_sleep_source", lambda raw: None)
    _rows(env, [(f"2032-01-0{i}", PHONE, True) for i in range(1, 9)])
    res, warns = _run()
    assert res is None
    assert any("выродился" in w for w in warns), warns
