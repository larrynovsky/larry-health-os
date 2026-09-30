"""
test_generated_food_rules.py — СКЛАД сгенерированных диет-правил (data-in-code-9, инкремент 2a).

Отдельная полка + версии (supersede прежней) + провенанс + shadow не применяется.
"""
from __future__ import annotations

import pytest

import health_db
import generated_food_rules as gfr

pytestmark = pytest.mark.unit

_PAYLOAD = {"condition": {"label": "резекция"}, "fault": {"category": "iatrogenic"},
            "lever": "management", "frame": {"energy": "gain", "micro": ["B12"]},
            "evidence": {"source": "consilium", "weight": "moderate"}}
_PROV = {"generated_by": "monthly_consilium", "model": "claude-sonnet",
         "model_version_date": "2026-07-15", "data_sources": ["profile", "hypotheses"]}


def test_save_and_read_shadow(db):
    conn = health_db.get_conn()
    v = gfr.save_rule("post_surgery_x", _PAYLOAD, provenance=_PROV,
                      floor_ok=True, critique="pass", status="shadow", conn=conn)
    assert v == 1
    rows = gfr.get_rules(status="shadow", conn=conn)
    assert len(rows) == 1
    r = rows[0]
    assert r["rule_key"] == "post_surgery_x" and r["version"] == 1
    assert r["payload"]["frame"]["energy"] == "gain"      # payload roundtrip
    assert r["floor_ok"] is True
    assert r["generated_by"] == "monthly_consilium" and r["data_sources"] == ["profile", "hypotheses"]


def test_new_version_supersedes_prior(db):
    conn = health_db.get_conn()
    gfr.save_rule("post_surgery_x", _PAYLOAD, provenance=_PROV, status="shadow", conn=conn)
    v2 = gfr.save_rule("post_surgery_x", {**_PAYLOAD, "note": "v2"},
                       provenance=_PROV, status="shadow", conn=conn)
    assert v2 == 2
    live = gfr.get_rules(status="shadow", rule_key="post_surgery_x", conn=conn)
    assert len(live) == 1 and live[0]["version"] == 2      # только новая жива
    superseded = gfr.get_rules(status="superseded", rule_key="post_surgery_x", conn=conn)
    assert len(superseded) == 1 and superseded[0]["version"] == 1


def test_shadow_not_applied(db):
    """Инкремент 2: сгенерированное лежит в shadow, брифом НЕ применяется (active пусто)."""
    conn = health_db.get_conn()
    gfr.save_rule("x", _PAYLOAD, provenance=_PROV, status="shadow", conn=conn)
    assert gfr.get_active_rules(conn=conn) == []


def test_invalid_status_rejected(db):
    conn = health_db.get_conn()
    with pytest.raises(ValueError):
        gfr.save_rule("x", _PAYLOAD, status="bogus", conn=conn)


def test_get_rules_empty_when_no_table(db):
    """До первого save таблицы нет → склад пуст, не падаем."""
    conn = health_db.get_conn()
    assert gfr.get_rules(conn=conn) == []


def test_promote_shadow_to_active(db):
    """ФЛИП: floor-passing shadow → active, shadow пустеет."""
    conn = health_db.get_conn()
    gfr.save_rule("k1", _PAYLOAD, provenance=_PROV, floor_ok=True, status="shadow", conn=conn)
    gfr.save_rule("k2", _PAYLOAD, provenance=_PROV, floor_ok=True, status="shadow", conn=conn)
    assert gfr.promote_shadow_to_active(conn=conn) == 2
    assert {r["rule_key"] for r in gfr.get_active_rules(conn=conn)} == {"k1", "k2"}
    assert gfr.get_rules(status="shadow", conn=conn) == []


def test_generation_keeps_active_until_promote(db):
    """1-цикл лаг (DESIGN §6.1): новая теневая версия НЕ снимает живое active до промоушена."""
    conn = health_db.get_conn()
    gfr.save_rule("k1", _PAYLOAD, provenance=_PROV, floor_ok=True, status="shadow", conn=conn)
    gfr.promote_shadow_to_active(conn=conn)                       # v1 → active
    gfr.save_rule("k1", {**_PAYLOAD, "note": "v2"}, provenance=_PROV,
                  floor_ok=True, status="shadow", conn=conn)
    active = gfr.get_active_rules(conn=conn)
    assert len(active) == 1 and active[0]["version"] == 1         # active v1 ЖИВ, пока v2 в shadow
    gfr.promote_shadow_to_active(conn=conn)                       # v2 → active, v1 → superseded
    active = gfr.get_active_rules(conn=conn)
    assert len(active) == 1 and active[0]["version"] == 2


def test_promote_skips_floor_fail(db):
    """Пол-предохранитель: не прошедшее пол в active не пускаем."""
    conn = health_db.get_conn()
    gfr.save_rule("bad", _PAYLOAD, provenance=_PROV, floor_ok=False, status="shadow", conn=conn)
    assert gfr.promote_shadow_to_active(conn=conn) == 0
    assert gfr.get_active_rules(conn=conn) == []


# ── поколение заменяет рулбук (2026-09-01) ────────────────────────────────────────────────

def test_promote_replaces_generation(db):
    """active k1; новое поколение — только k2 → после флипа active == {k2}, k1 superseded.
    Старое поколение не накапливается вместе с новым; оракул — множество ключей."""
    conn = health_db.get_conn()
    gfr.save_rule("k1", _PAYLOAD, provenance=_PROV, floor_ok=True, status="shadow", conn=conn)
    gfr.promote_shadow_to_active(conn=conn)
    gfr.save_rule("k2", _PAYLOAD, provenance=_PROV, floor_ok=True, status="shadow", conn=conn)
    assert gfr.promote_shadow_to_active(conn=conn) == 1
    assert {r["rule_key"] for r in gfr.get_active_rules(conn=conn)} == {"k2"}
    assert gfr.get_rules(status="superseded", rule_key="k1", conn=conn)


def test_promote_keeps_prior_rulebook_when_nothing_passes(db):
    """Решение владельца «при провале держим прошлый рулбук»: shadow пуст или весь floor-fail → active цел."""
    conn = health_db.get_conn()
    gfr.save_rule("k1", _PAYLOAD, provenance=_PROV, floor_ok=True, status="shadow", conn=conn)
    gfr.promote_shadow_to_active(conn=conn)
    assert gfr.promote_shadow_to_active(conn=conn) == 0                # shadow пуст
    gfr.save_rule("bad", _PAYLOAD, provenance=_PROV, floor_ok=False, status="shadow", conn=conn)
    assert gfr.promote_shadow_to_active(conn=conn) == 0                # только floor-fail
    assert {r["rule_key"] for r in gfr.get_active_rules(conn=conn)} == {"k1"}
