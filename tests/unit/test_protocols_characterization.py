"""
tests/unit/test_protocols_characterization.py — характеризационные пины домена
protocols в health_db (Поток B рефакторинга, 2026-06-27).

Закрепляет ТЕКУЩЕЕ поведение ДО разбиения health_db (Поток C):
  get_active_protocols (4 callers, ранее без контракта), save_protocol, retire_protocol.

behavior-preserving пины — фиксируют «как есть», включая особенность:
save_protocol НЕ заполняет колонку domain (остаётся NULL), хотя
get_active_protocols(domain=...) по ней фильтрует.
"""
from __future__ import annotations

import health_db


def test_save_protocol_inserts_active_returns_id_domain_none(db):
    pid = health_db.save_protocol({"title": "P1", "behavior": "do X"})
    assert isinstance(pid, int)

    match = [p for p in health_db.get_active_protocols() if p["id"] == pid]
    assert len(match) == 1
    assert match[0]["title"] == "P1"
    # пин: save_protocol не задаёт domain → NULL
    assert match[0]["domain"] is None


def test_get_active_protocols_excludes_retired_ordered_by_id(db):
    p1 = health_db.save_protocol({"title": "first", "behavior": "b"})
    p2 = health_db.save_protocol({"title": "second", "behavior": "b"})
    health_db.retire_protocol(p1)

    ids = [p["id"] for p in health_db.get_active_protocols()]
    assert p1 not in ids
    assert p2 in ids
    assert ids == sorted(ids)


def test_get_active_protocols_domain_filter(db):
    # domain задаётся только напрямую (builder), т.к. save_protocol его не пишет
    db.add_protocol("sleep proto", domain="sleep")
    db.add_protocol("stress proto", domain="stress")

    sleep_only = health_db.get_active_protocols(domain="sleep")
    assert [p["title"] for p in sleep_only] == ["sleep proto"]


def test_retire_protocol_sets_status_and_returns_true(db):
    pid = health_db.save_protocol({"title": "temp", "behavior": "b"})

    assert health_db.retire_protocol(pid, note="done") is True
    assert all(p["id"] != pid for p in health_db.get_active_protocols())

    row = db.fetchone("SELECT status, notes FROM protocols WHERE id=?", (pid,))
    assert row["status"] == "retired"
    assert row["notes"] == "done"
