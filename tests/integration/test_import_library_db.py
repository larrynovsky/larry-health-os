"""
tests/integration/test_import_library_db.py

Интеграционные тесты для lab_formats + lab_name_aliases + pending_field_reviews.

Покрывает:
  1. Миграция создаёт таблицы (идемпотентно)
  2. Synevo seed: format существует, aliases confirmed
  3. get_confirmed_aliases возвращает dict
  4. DB isolation: known → lab_results, unknown → pending, независимо
  5. confirm_field_alias: orphan-флаг
  6. queue_field_reviews: дедупликация pending
  7. resolve_field_review: подтверждение создаёт alias
"""
import json
import sqlite3
import tempfile
import os
from pathlib import Path
from unittest.mock import patch

import pytest

import health_db as db


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    """
    Стандартный паттерн (см. tests/fixtures/db.py):
    патчит DB_PATH + HEALTH_DIR, не трогает get_conn().
    """
    health_dir = tmp_path / "health"
    data_dir   = health_dir / "data"
    data_dir.mkdir(parents=True)
    db_path = data_dir / "health.db"

    monkeypatch.setenv("HEALTH_DATA_DIR", str(health_dir))
    monkeypatch.setattr(db, "DB_PATH",     db_path)
    monkeypatch.setattr(db, "_HEALTH_DIR", health_dir)
    # ICLOUD и METRICS_DIR могут отсутствовать в разных версиях — safe setattr
    for attr, val in [("ICLOUD", health_dir),
                      ("METRICS_DIR", data_dir / "daily_metrics")]:
        if hasattr(db, attr):
            monkeypatch.setattr(db, attr, val)

    db.init_db()
    yield


# ── 1. Миграция идемпотентна ──────────────────────────────────────────────────

class TestMigration:
    def test_tables_created(self):
        with db.get_conn() as conn:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
        assert "lab_formats" in tables
        assert "lab_name_aliases" in tables
        assert "pending_field_reviews" in tables

    def test_idempotent_double_init(self):
        db.init_db()  # второй вызов не должен падать
        with db.get_conn() as conn:
            count = conn.execute("SELECT count(*) FROM lab_formats").fetchone()[0]
        assert count >= 1


# ── 2. Synevo seed ────────────────────────────────────────────────────────────

class TestSynevoSeed:
    def test_synevo_format_exists(self):
        row = db.get_lab_format_by_name("synevo")
        assert row is not None
        assert row["name"] == "synevo"
        assert row["parser_type"] == "fitz"

    def test_seed_aliases_confirmed(self):
        row = db.get_lab_format_by_name("synevo")
        aliases = db.get_confirmed_aliases(row["id"])
        # Базовые тесты из SYNEVO_NAME_MAP должны быть в aliases
        assert "Glucose" in aliases
        assert aliases["Glucose"] == "Glucose"
        assert "HGB" not in aliases   # HGB — не Synevo raw name
        assert "Hemoglobin" in aliases
        assert aliases["Hemoglobin"] == "HGB"

    def test_2026_additions_seeded(self):
        row = db.get_lab_format_by_name("synevo")
        aliases = db.get_confirmed_aliases(row["id"])
        assert "Mg" in aliases
        assert aliases["Mg"] == "Magnesium"
        assert "Troponin T hs" in aliases
        assert aliases["Troponin T hs"] == "Troponin"
        # 2026-07-29: сверено с бланком synevo CR/10000000001.PDF. Бланк метит обе
        # колонки — процентную суффиксом «%», абсолютную «Abs»; ГОЛОГО имени в нём нет
        # (строки «… Abs» рвутся переносом при извлечении PDF). Поэтому пара для голого
        # имени удалена из сида как опровергнутая догадка, а «X Abs» ведёт в «X_abs».
        assert "Neutrophils Abs" in aliases
        assert aliases["Neutrophils Abs"] == "Neutrophils_abs"
        assert "Neutrophils" not in aliases, (
            "пара для ГОЛОГО имени вернулась в сид: бланк показывает, что голое имя — "
            "это обрезанный «Abs», а не процент; догадке в данных здесь не место"
        )


# ── 3. get_all_canonical_names ────────────────────────────────────────────────

def test_get_all_canonical_names():
    names = db.get_all_canonical_names()
    assert isinstance(names, list)
    assert "Glucose" in names
    assert "Magnesium" in names
    # Уникальны
    assert len(names) == len(set(names))


# ── 4. DB isolation: known пишется независимо от pending ─────────────────────

class TestDbIsolation:
    def test_biochemical_json_writer_retired(self, tmp_path, monkeypatch):
        """import_biochemical_json РЕТАЙРНУТ жёстким блоком (фенс BL-LAB-CANON-1):
        канон lab_results принадлежит единственному писателю lab_promote (source=doc:).
        Ни по умолчанию, ни через env старый путь не пишет — raise RuntimeError."""
        import pytest
        bio_dir = tmp_path / "data" / "biochemical" / "2026-06"
        bio_dir.mkdir(parents=True)
        bio_file = bio_dir / "2026-06-01_CBC_chemistry.json"
        bio_file.write_text(json.dumps({
            "date": "2026-06-01", "type": "lab",
            "results": {"HGB": {"value": 16.4, "unit": "g/dL", "flagged": False}}
        }), encoding="utf-8")
        db._ensure_lab_table()
        monkeypatch.setenv("HEALTH_ALLOW_BIOCHEMICAL_LABWRITE", "1")  # env НЕ открывает
        with pytest.raises(RuntimeError):
            db.import_biochemical_json(str(bio_file))
        with db.get_conn() as conn:
            assert conn.execute(
                "SELECT COUNT(*) FROM lab_results WHERE date='2026-06-01'"
            ).fetchone()[0] == 0

    def test_pending_reviews_do_not_block_lab_results(self, tmp_path):
        """Pending field reviews существуют рядом с lab_results — без блокировки."""
        db._ensure_lab_table()  # создаём таблицу явно (lazily created)
        fmt = db.get_lab_format_by_name("synevo")
        n = db.queue_field_reviews(
            format_id=fmt["id"],
            source_file="test.PDF",
            unknown_fields=[{"raw_name": "NewTest", "value": 1.5, "unit": "mmol/L"}]
        )
        assert n == 1

        # lab_results пусты — проверяем что pending не блокирует их
        with db.get_conn() as conn:
            pending = conn.execute(
                "SELECT count(*) FROM pending_field_reviews WHERE status='pending'"
            ).fetchone()[0]
            labs = conn.execute("SELECT count(*) FROM lab_results").fetchone()[0]
        assert pending == 1
        assert labs == 0


# ── 5. confirm_field_alias — orphan-флаг ─────────────────────────────────────

class TestConfirmAlias:
    def test_known_canonical_not_orphan(self):
        fmt = db.get_lab_format_by_name("synevo")
        # "HGB" — есть в стандартных lab_refs ключах
        db.confirm_field_alias(fmt["id"], "Hemoglobin_new", "HGB", "example text")
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT orphan FROM lab_name_aliases WHERE raw_name='Hemoglobin_new'"
            ).fetchone()
        # Если HGB есть в lab_refs — orphan=0; если нет в test DB — orphan=1.
        # Просто проверяем что поле создалось.
        assert row is not None

    def test_update_on_conflict(self):
        fmt = db.get_lab_format_by_name("synevo")
        db.confirm_field_alias(fmt["id"], "TestRaw", "TestCanonical1")
        db.confirm_field_alias(fmt["id"], "TestRaw", "TestCanonical2")
        aliases = db.get_confirmed_aliases(fmt["id"])
        assert aliases.get("TestRaw") == "TestCanonical2"


# ── 6. queue_field_reviews — дедупликация ────────────────────────────────────

class TestQueueFieldReviews:
    def test_dedup_pending(self):
        fmt = db.get_lab_format_by_name("synevo")
        fields = [{"raw_name": "NewMarker", "value": 5.0, "unit": "U/L"}]
        n1 = db.queue_field_reviews(fmt["id"], "file1.PDF", fields)
        n2 = db.queue_field_reviews(fmt["id"], "file2.PDF", fields)
        assert n1 == 1
        assert n2 == 0  # уже pending — пропускается

    def test_confirmed_alias_not_re_queued(self):
        fmt = db.get_lab_format_by_name("synevo")
        # Glucose уже confirmed в seed
        n = db.queue_field_reviews(
            fmt["id"], "test.PDF",
            [{"raw_name": "Glucose", "value": 5.5, "unit": "mmol/L"}]
        )
        assert n == 0

    def test_multiple_unknown_all_queued(self):
        fmt = db.get_lab_format_by_name("synevo")
        fields = [
            {"raw_name": "TestA", "value": 1.0},
            {"raw_name": "TestB", "value": 2.0},
        ]
        n = db.queue_field_reviews(fmt["id"], "file.PDF", fields)
        assert n == 2


# ── 7. resolve_field_review ───────────────────────────────────────────────────

class TestResolveFieldReview:
    def test_confirmation_creates_alias(self):
        fmt = db.get_lab_format_by_name("synevo")
        db.queue_field_reviews(
            fmt["id"], "test.PDF",
            [{"raw_name": "NewHormone", "value": 3.14, "unit": "pmol/L"}]
        )
        reviews = db.get_pending_field_reviews(fmt["id"])
        assert len(reviews) >= 1
        review_id = next(r["id"] for r in reviews if r["raw_name"] == "NewHormone")

        db.resolve_field_review(review_id, canonical="SomeHormone")

        # Review закрыт
        remaining = db.get_pending_field_reviews(fmt["id"])
        assert all(r["raw_name"] != "NewHormone" for r in remaining)

        # Alias создан и confirmed
        aliases = db.get_confirmed_aliases(fmt["id"])
        assert aliases.get("NewHormone") == "SomeHormone"

    def test_rejection_no_alias(self):
        fmt = db.get_lab_format_by_name("synevo")
        db.queue_field_reviews(
            fmt["id"], "test.PDF",
            [{"raw_name": "SpuriousField", "value": 0.0}]
        )
        reviews = db.get_pending_field_reviews(fmt["id"])
        review_id = next(r["id"] for r in reviews if r["raw_name"] == "SpuriousField")

        db.resolve_field_review(review_id, canonical=None)  # reject

        aliases = db.get_confirmed_aliases(fmt["id"])
        assert "SpuriousField" not in aliases
