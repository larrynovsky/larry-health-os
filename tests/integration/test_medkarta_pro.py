"""Опросники в медкарте по-человечески (26.09, владелец про строку `dysphagia=0.0, …`:
«что я должен понять из этого?»)."""
from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    import assessment_importer as ai
    d = tmp_path / "instruments"
    d.mkdir()
    (d / "isi.json").write_text(json.dumps({
        "id": "isi", "name": "Insomnia Severity Index", "title_ru": "Сон (ISI)",
        "recall_period": "past_2_weeks",
        "threshold_labels_ru": {"subthreshold": "подпороговая бессонница",
                                "moderate_clinical": "умеренная бессонница"},
        "response_scale": {"min": 0, "max": 4},
        "subscales": [{"id": "total", "items": [f"q{i}" for i in range(7)]}],
        "thresholds": {"subthreshold": 8, "moderate_clinical": 15, "severe_clinical": 22}}))
    (d / "mfsi_sf.json").write_text(json.dumps({
        "id": "mfsi_sf", "name": "MFSI-SF", "recall_period": "past_week",
        "subscales": [{"id": "general_fatigue", "direction": "symptom_higher_worse", "items": ["a"]},
                      {"id": "vigor", "direction": "vigor_reverse_higher_better", "items": ["b"],
                       "label_ru": "бодрость"}]}))
    monkeypatch.setattr(ai, "INSTRUMENTS_DIR", d)
    return ai


def test_isi_raw_score_and_band(catalog):
    # 46.43 на шкале 0–100 = 13 баллов из 28 → подпороговая
    assert catalog.describe_scores("isi", {"total": 46.43}) == \
        "Сон (ISI), за 2 недели: 13 из 28 — подпороговая бессонница"


def test_direction_is_named(catalog):
    s = catalog.describe_scores("mfsi_sf", {"general_fatigue": 0.0, "vigor": 79.17})
    assert "жалоб нет (0 по всем 1 шкалам)" in s and "бодрость 79 из 100 (больше — лучше)" in s


def test_unknown_instrument_falls_back(catalog):
    assert catalog.describe_scores("nope", {"x": 1.0}) is None


def test_card_shows_human_text(catalog, dashboard_client):
    client, db = dashboard_client
    import health_db
    health_db.save_event(event_type="self_observation", effective_date="2026-09-03",
                         performer="self", performer_role="self", recorded_by="patient",
                         notes="PRO: isi; subscales: total=46.43",
                         diagnostic={"type": "functional_test", "modality": "isi",
                                     "interpreted_report": "total=46.43", "abnormal_flags": []})
    html = client.get("/medical-record").text
    assert "13 из 28 — подпороговая бессонница" in html
    assert "total=46.43" not in html and "самоотчёт" in html and "опросник" in html
