"""
tests/unit/test_supplements_home.py — нить supplements-home (07.10.2026).

Сказанное «принимаю X» раньше жило строкой в памяти; дом лечения (medications), который
читают консилиум и подготовка к визиту, о нём не знал. Пины:
  1. taking от арбитра → строка medications: proposed, source=conversation, доза и частота.
  2. Незаземлённое в словах человека (или с картинки) в таблицу не идёт.
  3. Мусор от модели (не список, вид вне словаря, кривая дата) отбрасывается.
  4. Карточка гейта для сказанного показывает дозу и режим.
  5. История лечения: добавки отдельной строкой «принимает: …», не стрелкой хронологии.
  6. consult_prep не показывает неподтверждённое.
"""
from __future__ import annotations

import json
import pytest

pytestmark = pytest.mark.unit

_TAKING = [
    {"name": "магний цитрат", "kind": "supplement", "dose": "300 мг",
     "frequency": "вечером", "since": "2026-05"},
]


def _arm(anthropic_mock, monkeypatch, taking):
    import anthropic
    import hai_chat
    monkeypatch.setattr(hai_chat, "get_client", lambda: anthropic.Anthropic(api_key="fake"))
    payload = {"profile_updates": {}, "observations": [], "experiments": [],
               "open_questions": [], "taking": taking, "nothing_to_extract": False}
    anthropic_mock.script(match=lambda p: "taking" in p,
                          response=json.dumps(payload, ensure_ascii=False))
    return hai_chat


def test_stated_taking_becomes_proposed_medication(db, anthropic_mock, monkeypatch):
    hai_chat = _arm(anthropic_mock, monkeypatch, _TAKING)
    saved = hai_chat.run_arbiter("пью магний цитрат 300 мг вечером с мая", "понял")
    import treatment_db
    rows = [r for r in treatment_db.get_medications(include_proposed=True) if r["name"] == "магний цитрат"]
    assert len(rows) == 1, "сказанное должно стать одной строкой medications"
    r = rows[0]
    assert (r["confirmation"], r["source"], r["status"]) == ("proposed", "conversation", "active")
    assert (r["dosage"], r["frequency"], r["start_date"]) == ("300 мг", "вечером", "2026-05")
    assert r["modality"] == "supplement"
    assert saved.get("taking") == ["магний цитрат"]
    # канон не тронут без «да»
    assert not [m for m in treatment_db.get_medications() if m["name"] == "магний цитрат"]


def test_taking_from_picture_does_not_reach_table(db, anthropic_mock, monkeypatch):
    hai_chat = _arm(anthropic_mock, monkeypatch, _TAKING)
    hai_chat.run_arbiter("вот фото банки", "вижу магний цитрат", unverified=True)
    import treatment_db
    assert not [r for r in treatment_db.get_medications(include_proposed=True)
                if r["name"] == "магний цитрат"]


@pytest.mark.parametrize("bad", [
    "магний цитрат",                                   # не список
    [{"name": "", "kind": "supplement"}],            # пустое имя
    [{"name": "X", "kind": "potion"}],               # вид вне словаря
    ["строка вместо словаря"],
])
def test_valid_taking_drops_garbage(bad):
    import hai_chat
    assert hai_chat._valid_taking(bad) == []


def test_valid_taking_rejects_free_text_date():
    import hai_chat
    out = hai_chat._valid_taking([{"name": "B12", "kind": "supplement", "since": "с мая"}])
    assert out and out[0]["since"] == ""


def test_upsert_keeps_dose_on_update_without_dose(db):
    import treatment_db
    treatment_db.upsert_medication("цинк пиколинат", modality="supplement",
                                   confirmation="proposed", source="conversation",
                                   status="active", dosage="25 мг", frequency="через день")
    treatment_db.upsert_medication("цинк пиколинат", modality="supplement",
                                   confirmation="proposed", source="conversation", status="active")
    r = [m for m in treatment_db.get_medications(include_proposed=True)
         if m["name"] == "цинк пиколинат"][0]
    assert (r["dosage"], r["frequency"]) == ("25 мг", "через день")


def test_gate_card_for_stated_shows_dose(monkeypatch):
    import asyncio
    import jobs.scheduled as sch
    sent = {}

    async def fake_send_md(fn, **kw):
        sent.update(kw)
    monkeypatch.setattr(sch, "send_md", fake_send_md)
    monkeypatch.setattr(sch, "owner_chat_id", lambda: 1)
    med = {"id": 7, "name": "магний цитрат", "modality": "supplement", "source": "conversation",
           "dosage": "300 мг", "frequency": "вечером", "start_date": "2026-05"}
    asyncio.run(sch._send_treatment_review_message(type("B", (), {"send_message": None})(), med))
    assert "300 мг" in sent["text"] and "вечером" in sent["text"]
    assert "medrev_7_ok" in str(sent["reply_markup"])


def test_summary_puts_supplements_apart(db):
    import treatment_db
    import treatment_summary
    treatment_db.upsert_medication("REG-A", modality="chemo", indication_problem_id="p1",
                                   cycles_completed=3, confirmation="confirmed")
    treatment_db.upsert_medication("магний цитрат", modality="supplement", confirmation="manual",
                                   source="manual:owner", status="active", dosage="300 мг")
    text = treatment_summary.build_treatment_summary()["text"]
    assert "принимает: магний цитрат (300 мг)" in text
    assert "→ магний цитрат" not in text and "магний цитрат →" not in text


def test_consult_prep_hides_unconfirmed(db):
    import treatment_db
    import consult_prep
    treatment_db.upsert_medication("непроверенное", modality="supplement", confirmation="proposed",
                                   source="conversation", status="active")
    treatment_db.upsert_medication("подтверждённое", modality="supplement", confirmation="manual",
                                   source="manual:owner", status="active")
    block = consult_prep._build_medications_block()
    assert "подтверждённое" in block and "непроверенное" not in block
