"""Профиль: образ жизни доходит до GP, медицинские поля с домом в медкарте читаются оттуда
(2026-09-26, владелец: «медицина в профиле бессмысленна, образ жизни пустоватый»)."""
from __future__ import annotations

import pytest

import health_db

pytestmark = pytest.mark.integration


def test_chat_key_lands_in_lifestyle_field_and_reaches_gp(db):
    # разборщик чата называет поле ключом памяти — реестр узнаёт его по алиасу
    assert health_db.apply_stated("caffeine_pattern", "2 чашки до полудня", source="conversation")
    import gp_agent
    facts = gp_agent._known_lifestyle_facts(health_db.get_profile_context())
    assert "  Кофеин: 2 чашки до полудня" in facts


def test_treatment_status_comes_from_episodes(db):
    health_db.upsert_profile("medical.treatment_status", value_text="ручная строка")
    db.add_episode("курс А", start_date="2025-01-01", end_date="2025-06-01", status="finished")
    st = health_db.get_profile_context()["medical"]["treatment_status"]
    assert st.startswith("активного лечения нет") and "2025-06-01" in st
    db.add_episode("курс Б", start_date="2026-02-01", end_date=None, status="active")
    assert "идёт лечение: курс Б" in health_db.get_profile_context()["medical"]["treatment_status"]


def test_manual_status_kept_without_medkarta(db):
    health_db.upsert_profile("medical.treatment_status", value_text="ручная строка")
    assert health_db.get_profile_context()["medical"]["treatment_status"] == "ручная строка"


def test_pet_result_older_than_scan_is_marked(db):
    health_db.upsert_profile("medical.last_pet_ct_result", value_text="без динамики")
    db.execute("UPDATE patient_profile SET updated_at='2026-01-10' WHERE key='medical.last_pet_ct_result'")
    health_db.upsert_profile("medical.last_pet_ct", value_text="2026-04-01")
    res = health_db.get_profile_context()["medical"]["last_pet_ct_result"]
    assert "результат последнего исследования не внесён" in res
    db.execute("UPDATE patient_profile SET updated_at='2026-04-20' WHERE key='medical.last_pet_ct_result'")
    assert health_db.get_profile_context()["medical"]["last_pet_ct_result"] == "без динамики"
