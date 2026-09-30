"""Wave 5E-6: tests для GP-context инжект specialist_review."""
from __future__ import annotations

from pathlib import Path
import sys, os, json
from datetime import date, timedelta

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT))

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture
def tmp_db(monkeypatch, tmp_path):
    os.environ["ALLOW_WRITE_NONPRIMARY"] = "1"
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path / "health"))
    (tmp_path / "health" / "data").mkdir(parents=True)
    import importlib, health_db
    importlib.reload(health_db)
    health_db.init_db()
    yield health_db
    del os.environ["ALLOW_WRITE_NONPRIMARY"]


def _save_specialist_hyp(db, subtype, observation, score, conf):
    """Сохраняет hypothesis с trigger=specialist_review + cbcr_payload."""
    payload = {
        "observation": observation,
        "trigger": "specialist_review",
        "trigger_subtype": subtype,
        "status": "open",
        "mechanism": "x", "prediction": "y", "test": "z",
        "resolution_type": "needs_specialist",
        "created_date": str(date.today()),
    }
    mid = db.save_memory(
        category="hypothesis",
        key=f"k_{subtype}_{observation[:20]}",
        value=json.dumps(payload, ensure_ascii=False),
        source="auto_specialist_review",
    )
    db.save_cbcr_payload(
        memory_id=mid, payload_json="{}",
        structural_score=score, confidence_level=conf,
        generated_by="cbcr", model="claude-sonnet-4-6",
    )
    return mid


def _read_specialist_block(tmp_db):
    """Симулирует SQL запрос блока (без вызова всего _build_gp_context)."""
    cutoff = str(date.today() - timedelta(days=8))
    with tmp_db.get_conn() as conn:
        rows = conn.execute(
            """SELECT m.id, m.value AS payload,
                      hc.structural_score, hc.confidence_level
               FROM memory m
               LEFT JOIN hypotheses_cbcr hc ON hc.memory_id = m.id
               WHERE m.category = 'hypothesis'
                 AND m.active = 1
                 AND date(m.created_at) >= ?
               ORDER BY COALESCE(hc.structural_score, 0) DESC, m.id DESC
               LIMIT 20""",
            (cutoff,)
        ).fetchall()
    out = []
    for r in rows:
        pl = json.loads(r["payload"])
        if pl.get("trigger") == "specialist_review" and pl.get("status") not in ("confirmed", "rejected"):
            out.append((r, pl))
        if len(out) >= 2:
            break
    return out


def test_top2_by_score(tmp_db):
    _save_specialist_hyp(tmp_db, "cardio", "obs low score", 2, "low")
    _save_specialist_hyp(tmp_db, "sleep", "obs high score", 5, "high")
    _save_specialist_hyp(tmp_db, "onco", "obs mid score", 4, "medium")
    top = _read_specialist_block(tmp_db)
    assert len(top) == 2
    # Топ — по score DESC
    assert top[0][0]["structural_score"] == 5
    assert top[1][0]["structural_score"] == 4


def test_excludes_confirmed_rejected(tmp_db):
    _save_specialist_hyp(tmp_db, "cardio", "good open", 5, "high")
    # second — мокаем как rejected
    mid = _save_specialist_hyp(tmp_db, "sleep", "rejected one", 6, "high")
    payload = json.loads(tmp_db.get_memory(category="hypothesis", n=10)[0]["value"])
    # переставим status одной записи в rejected
    with tmp_db.get_conn() as conn:
        # Самая последняя с обозначением rejected
        rows = conn.execute("SELECT id, value FROM memory WHERE id=?", (mid,)).fetchone()
        p2 = json.loads(rows["value"])
        p2["status"] = "rejected"
        conn.execute("UPDATE memory SET value=? WHERE id=?",
                     (json.dumps(p2, ensure_ascii=False), mid))
    top = _read_specialist_block(tmp_db)
    assert len(top) == 1
    assert top[0][1]["trigger_subtype"] == "cardio"


def test_excludes_other_triggers(tmp_db):
    # Сохраняем гипотезу с trigger=drift — не должна попадать в specialist block
    payload = {
        "observation": "drift hyp", "trigger": "drift", "status": "open",
        "mechanism": "x", "prediction": "y", "test": "z",
        "resolution_type": "self_managed", "created_date": str(date.today()),
    }
    tmp_db.save_memory(category="hypothesis", key="kdrift",
                       value=json.dumps(payload, ensure_ascii=False),
                       source="auto_drift")
    # specialist
    _save_specialist_hyp(tmp_db, "cardio", "spec hyp", 4, "medium")
    top = _read_specialist_block(tmp_db)
    assert len(top) == 1
    assert top[0][1]["trigger"] == "specialist_review"


def test_empty_when_no_specialist_review(tmp_db):
    # Сохраняем не-specialist гипотезу чтобы таблица memory создалась
    payload = {
        "observation": "drift", "trigger": "drift", "status": "open",
        "mechanism": "x", "prediction": "y", "test": "z",
        "resolution_type": "self_managed", "created_date": str(date.today()),
    }
    tmp_db.save_memory(category="hypothesis", key="k1",
                       value=json.dumps(payload, ensure_ascii=False),
                       source="auto_drift")
    top = _read_specialist_block(tmp_db)
    assert top == []
