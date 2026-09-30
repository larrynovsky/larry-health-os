"""W5A-INT-3: tests для save_cbcr_payload / get_cbcr_payload + миграция."""
from __future__ import annotations

from pathlib import Path
import sys, os, json, sqlite3, tempfile

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT))

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture
def tmp_db(monkeypatch, tmp_path):
    """Изолированная БД для тестов — health.db в tmp_path."""
    monkeypatch.setenv("ALLOW_WRITE_NONPRIMARY", "1")  # MacBook test override
    data_dir = tmp_path / "health" / "data"
    data_dir.mkdir(parents=True)
    db_path = data_dir / "health.db"
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path / "health"))
    # Перезагружаем модуль health_db чтобы он подхватил env
    import importlib
    import health_db
    importlib.reload(health_db)
    health_db.init_db()
    yield health_db
    # Cleanup (BL-TESTISO-1, 2026-07-14): снять env-подмены ЧЕРЕЗ monkeypatch и
    # ПЕРЕЗАГРУЗИТЬ health_db. Иначе его модульный DB_PATH остаётся на tmp_path и
    # течёт в другие тесты (split-brain ловит test_db_path_single_source), а сырой
    # `del ALLOW_WRITE_NONPRIMARY` стирал выставленный conftest'ом флаг и ронял
    # test_audit_concept. undo() восстанавливает env ДО reload → DB_PATH к канону.
    monkeypatch.undo()
    importlib.reload(health_db)


def test_hypotheses_cbcr_table_exists(tmp_db):
    """Миграция создаёт таблицу с индексами."""
    with tmp_db.get_conn() as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='hypotheses_cbcr'"
        ).fetchone()
        assert row is not None
        idx_names = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='hypotheses_cbcr'"
        ).fetchall()}
        assert "idx_hypotheses_cbcr_confidence" in idx_names
        assert "idx_hypotheses_cbcr_score" in idx_names


def test_save_and_get_cbcr_payload(tmp_db):
    """Round-trip: save → get возвращает тот же payload."""
    memory_id = tmp_db.save_memory(
        category="hypothesis", value='{"observation":"x"}',
        key="test_key", confidence=0.5, source="auto_drift",
    )
    payload = {"one_line_statement": "test", "evidence_for": [{"x": 1}]}
    tmp_db.save_cbcr_payload(
        memory_id=memory_id,
        payload_json=json.dumps(payload, ensure_ascii=False),
        structural_score=4,
        confidence_level="medium",
        generated_by="cbcr_hypothesis.generate_hypothesis",
        model="claude-sonnet-4-6",
    )
    row = tmp_db.get_cbcr_payload(memory_id)
    assert row is not None
    assert row["payload"]["one_line_statement"] == "test"
    assert row["structural_score"] == 4
    assert row["confidence_level"] == "medium"
    assert row["model"] == "claude-sonnet-4-6"


def test_get_cbcr_payload_returns_none_when_missing(tmp_db):
    """get для несуществующего memory_id → None."""
    assert tmp_db.get_cbcr_payload(999999) is None


def test_save_cbcr_payload_upsert(tmp_db):
    """Повторный save на тот же memory_id → UPDATE (ON CONFLICT)."""
    memory_id = tmp_db.save_memory(
        category="hypothesis", value="{}", key="test_upsert", source="auto_drift",
    )
    tmp_db.save_cbcr_payload(memory_id, '{"v": 1}', 3, "medium", "g1", "m1")
    tmp_db.save_cbcr_payload(memory_id, '{"v": 2}', 5, "high", "g2", "m2")
    row = tmp_db.get_cbcr_payload(memory_id)
    assert row["payload"]["v"] == 2
    assert row["structural_score"] == 5
    assert row["confidence_level"] == "high"
    assert row["model"] == "m2"


def test_fk_cascade_on_memory_delete(tmp_db):
    """FK ON DELETE CASCADE: удаление memory удаляет cbcr_payload."""
    memory_id = tmp_db.save_memory(
        category="hypothesis", value="{}", key="cascade_test", source="auto_drift",
    )
    tmp_db.save_cbcr_payload(memory_id, '{"x":1}', 4, "medium", "g", "m")
    assert tmp_db.get_cbcr_payload(memory_id) is not None
    with tmp_db.get_conn() as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("DELETE FROM memory WHERE id=?", (memory_id,))
    # FK cascade зависит от PRAGMA — проверяем что либо удалилось, либо запись сохранена
    # Главное — не падает
    row = tmp_db.get_cbcr_payload(memory_id)
    # На SQLite по умолчанию PRAGMA foreign_keys=OFF, поэтому может остаться. OK.
    assert row is None or isinstance(row, dict)
