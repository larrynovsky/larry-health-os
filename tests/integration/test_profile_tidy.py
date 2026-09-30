"""Порядок в профиле (2026-09-26): раздел не теряется, пустая строка снятого протокола
убирается, страница говорит по-русски и не даёт править то, что пересчитывает система."""
from __future__ import annotations

import pytest

import health_db

pytestmark = pytest.mark.integration


def _row(db, key):
    return db.fetchone("SELECT category, value_text FROM patient_profile WHERE key=?", (key,))


def test_upsert_without_category_keeps_section(db):
    health_db.upsert_profile("identity.name", value_text="А", category="identity")
    health_db.upsert_profile("identity.name", value_text="Б")          # как правка без раздела
    assert _row(db, "identity.name")["category"] == "identity"


def test_migration_restores_section_and_drops_empty_nurosym(db):
    db.execute("INSERT INTO patient_profile(key, value_text, category) VALUES('medical.x','v',NULL)")
    db.execute("INSERT INTO patient_profile(key, value_text, updated_by) "
               "VALUES('routine.nurosym', NULL, 'owner: отменён')")
    db.execute("INSERT INTO patient_profile(key, value_text, category) VALUES('nodot','v',NULL)")
    health_db._migrate_patient_profile_and_config()
    assert _row(db, "medical.x")["category"] == "medical"
    assert _row(db, "routine.nurosym") is None
    assert _row(db, "nodot")["category"] is None                        # без префикса не гадаем


def test_profile_page_labels_and_auto_rows(dashboard_client):
    client, db = dashboard_client
    health_db.upsert_profile("medical.treatment_status", value_text="наблюдение")
    db.execute("INSERT INTO patient_profile(key, value_text, category, updated_by) "
               "VALUES('medical.port_catheter','есть','medical','profile_reconciler')")
    html = client.get("/profile").text
    assert "Статус лечения" in html and "Медицина" in html
    assert "(no category)" not in html
    assert 'hx-get="/api/profile/medical.treatment_status/edit"' in html
    assert 'hx-get="/api/profile/medical.port_catheter/edit"' not in html  # авто — не правится
