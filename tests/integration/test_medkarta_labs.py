"""Медкарта показывает анализы коротко: итог забора к событию-анализу и
отдельной карточкой для забора без события; опросники — не анализы. Данные тестов вымышлены."""
from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.integration


def test_draw_summary_on_event_and_orphan_draw(dashboard_client):
    client, db = dashboard_client
    db.add_lab_result("2026-06-01", "Ferritin", 9.0, source="doc:a.pdf", status="L")
    db.add_lab_result("2026-06-01", "Glucose", 5.0, source="doc:a.pdf", status="N")
    db.add_lab_result("2026-06-01", "ALT", 80.0, source="doc:a.pdf", ref_low=0, ref_high=40)
    db.add_lab_result("2025-02-02", "TSH", 2.0, source="ClinicX/Portal")
    db.add_lab_result("2026-06-05", "ISI total", 12.0, source="instrument:isi")
    db.add_event("lab_result", effective_date="2026-06-01", notes="бланк",
                 attachments=json.dumps(json.dumps({"source_file": "CR/a.pdf"})))
    html = client.get("/medical-record").text
    assert "3 показателей · вне нормы 2: ALT ↑, Ferritin ↓" in html      # событие + итог
    assert "Анализы · ClinicX/Portal" in html and "без пометок об отклонении" in html  # забор без события
    assert "ISI total" not in html and "instrument" not in html        # опросник — не анализ
    assert html.count("3 показателей") == 1                            # забор не задвоен


def test_fresh_db_without_labs_table(dashboard_client):
    client, db = dashboard_client
    db.execute("DROP TABLE IF EXISTS lab_results")
    assert client.get("/medical-record").status_code == 200


def test_fresh_events_outside_episodes_come_first(dashboard_client):
    """В вымышленном примере новые события вне эпизодов должны идти раньше старых
    событий внутри эпизодов; прежняя группировка прятала новые события внизу страницы."""
    client, db = dashboard_client
    ep = db.add_episode("курс", start_date="2025-01-01", end_date="2026-05-17", status="finished")
    db.add_event("lab_result", effective_date="2026-04-17", episode_id=ep, notes="апрель")
    db.add_event("lab_result", effective_date="2026-08-29", notes="август")
    html = client.get("/medical-record").text
    assert html.index("август") < html.index("апрель")
    assert html.index("вне эпизодов лечения") < html.index("курс")


def test_episodes_first_when_they_hold_the_newest(dashboard_client):
    client, db = dashboard_client
    ep = db.add_episode("курс", start_date="2026-01-01", status="active")
    db.add_event("encounter", effective_date="2026-09-01", episode_id=ep, notes="сентябрь")
    db.add_event("encounter", effective_date="2025-03-01", notes="старое")
    html = client.get("/medical-record").text
    assert html.index("сентябрь") < html.index("старое")
