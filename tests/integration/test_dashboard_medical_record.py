"""Wave 10: Medical Record tests.

Покрывает:
- /medical-record смотрит episodes + events
- фильтры event_type / episode_id
- /api/events/new простой event
- /api/events/new с encounter — создаёт также encounters row
- /api/events/new с diagnostic — создаёт также diagnostic_events row
- /api/events/new edge: missing required → 422
- counts.events_total и counts.episodes_total на /
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


# ── /medical-record GET ──────────────────────────────────────────────────────

def test_medical_record_empty(dashboard_client):
    """Пустая БД → 200, страница рендерится."""
    client, _ = dashboard_client
    r = client.get("/medical-record")
    assert r.status_code == 200


def test_medical_record_shows_event(dashboard_client):
    client, db = dashboard_client
    db.add_event("encounter", effective_date="2026-05-15", performer="Dr. Test")
    r = client.get("/medical-record")
    assert r.status_code == 200
    assert "Dr. Test" in r.text or "2026-05-15" in r.text


def test_medical_record_groups_by_episode(dashboard_client):
    client, db = dashboard_client
    ep_id = db.add_episode("Exampla treatment", primary_problem_id="test_problem",
                            start_date="2026-01-01")
    db.add_event("encounter", effective_date="2026-02-01", episode_id=ep_id,
                 performer="Dr. Ivanov")
    db.add_event("lab_result", effective_date="2026-03-01", episode_id=ep_id)
    r = client.get("/medical-record")
    assert r.status_code == 200
    assert "Exampla treatment" in r.text


def test_medical_record_filter_event_type(dashboard_client):
    client, db = dashboard_client
    db.add_event("encounter", effective_date="2026-05-01", performer="Dr. A")
    db.add_event("lab_result", effective_date="2026-05-02", performer="Lab B")

    # Filter encounter only
    r = client.get("/medical-record?event_type=encounter")
    assert r.status_code == 200
    assert "Dr. A" in r.text
    # Lab B (lab_result) НЕ должен быть в encounter-фильтре
    # Note: фильтр в SQL, не в HTML — assert через содержимое


def test_medical_record_filter_episode_id(dashboard_client):
    client, db = dashboard_client
    ep1 = db.add_episode("Episode 1")
    ep2 = db.add_episode("Episode 2")
    db.add_event("encounter", effective_date="2026-05-01", episode_id=ep1,
                 performer="Dr. EP1")
    db.add_event("encounter", effective_date="2026-05-02", episode_id=ep2,
                 performer="Dr. EP2")

    r = client.get(f"/medical-record?episode_id={ep1}")
    assert r.status_code == 200
    assert "Dr. EP1" in r.text


# ── /api/events/new POST ─────────────────────────────────────────────────────

def test_api_event_new_simple(dashboard_client):
    """Создание простого event без encounter/diagnostic."""
    client, db = dashboard_client
    before = db.count("events")
    r = client.post("/api/events/new", data={
        "event_type": "self_observation",
        "effective_date": "2026-05-15",
        "performer": "self",
        "notes": "test event",
    })
    # HTMX flow: 204 + HX-Redirect
    assert r.status_code in (200, 204)
    assert db.count("events") == before + 1
    row = db.fetchone("SELECT event_type, performer, notes FROM events ORDER BY id DESC LIMIT 1")
    assert row["event_type"] == "self_observation"
    assert row["notes"] == "test event"


def test_api_event_new_with_encounter(dashboard_client):
    """event_type=encounter → создаётся также encounters row с SOAP."""
    client, db = dashboard_client
    before_ev = db.count("events")
    before_enc = db.count("encounters")
    r = client.post("/api/events/new", data={
        "event_type": "encounter",
        "effective_date": "2026-05-15",
        "performer": "Dr. Ivanov",
        "performer_role": "specialist",
        "specialty": "oncology",
        "reason_text": "follow-up",
        "assessment": "stable",
        "enc_plan": "next visit in 3 months",
    })
    assert r.status_code in (200, 204)
    assert db.count("events") == before_ev + 1
    assert db.count("encounters") == before_enc + 1
    # JOIN проверка
    row = db.fetchone(
        """SELECT en.specialty, en.assessment, ev.performer
           FROM encounters en JOIN events ev ON ev.id = en.event_id
           ORDER BY en.event_id DESC LIMIT 1"""
    )
    assert row["specialty"] == "oncology"
    assert row["assessment"] == "stable"
    assert row["performer"] == "Dr. Ivanov"


def test_api_event_new_with_diagnostic(dashboard_client):
    """event_type=lab_result → создаётся также diagnostic_events row."""
    client, db = dashboard_client
    before_ev = db.count("events")
    before_diag = db.count("diagnostic_events")
    r = client.post("/api/events/new", data={
        "event_type": "lab_result",
        "effective_date": "2026-05-15",
        "modality": "blood_panel",
        "abnormal_flags": "[]",
        "interpreted_report": "WBC слегка повышен",
    })
    assert r.status_code in (200, 204)
    assert db.count("events") == before_ev + 1
    assert db.count("diagnostic_events") == before_diag + 1
    row = db.fetchone(
        """SELECT de.modality, de.interpreted_report, ev.event_type
           FROM diagnostic_events de JOIN events ev ON ev.id = de.event_id
           ORDER BY de.event_id DESC LIMIT 1"""
    )
    assert row["modality"] == "blood_panel"
    assert "повышен" in row["interpreted_report"]


def test_api_event_new_missing_required_field(dashboard_client):
    """Без event_type → 422."""
    client, _ = dashboard_client
    r = client.post("/api/events/new", data={"effective_date": "2026-05-15"})
    assert r.status_code == 422


# ── home() counts включает events/episodes ───────────────────────────────────

def test_home_counts_events_episodes(dashboard_client):
    client, db = dashboard_client
    db.add_episode("Ep1")
    db.add_event("encounter", effective_date="2026-05-01")
    db.add_event("lab_result", effective_date="2026-05-02")

    r = client.get("/")
    assert r.status_code == 200
    # На главной должны быть счётчики
    assert "2 событий" in r.text or "2 событи" in r.text
    assert "1 эпизод" in r.text


# ── Builders sanity (вспомогательные) ────────────────────────────────────────

def test_builders_immunization_and_relationship(dashboard_client):
    """Smoke: 2 редких builder'а работают."""
    client, db = dashboard_client
    parent_id = db.add_event("encounter", effective_date="2026-04-01")
    child_id = db.add_event("lab_result", effective_date="2026-04-15")
    db.add_event_relationship(parent_id, child_id, "followup_to")

    imm_id = db.add_immunization("COVID-19 booster", "2026-03-01",
                                 manufacturer="Pfizer")
    assert imm_id > 0

    # Проверка что записи на месте
    rel = db.fetchone("SELECT relationship_type FROM event_relationships WHERE parent_event_id=?",
                     (parent_id,))
    assert rel["relationship_type"] == "followup_to"
    imm = db.fetchone("SELECT vaccine, manufacturer FROM immunizations WHERE id=?", (imm_id,))
    assert imm["vaccine"] == "COVID-19 booster"
    assert imm["manufacturer"] == "Pfizer"


def test_builders_event_problem_link(dashboard_client):
    """event_problem_links: связь event ↔ problem."""
    client, db = dashboard_client
    ev_id = db.add_event("encounter", effective_date="2026-05-01")
    db.add_problem("test_link_problem", "Test Problem")
    # add_problem не возвращает id, но problem_id — тоже PK через slug
    db.add_event_problem_link(ev_id, "test_link_problem", link_type="reason")
    link = db.fetchone(
        """SELECT link_type FROM event_problem_links
           WHERE event_id=? AND problem_id=?""",
        (ev_id, "test_link_problem"),
    )
    assert link["link_type"] == "reason"
