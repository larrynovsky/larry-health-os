"""Оракул: то, что человек сообщает о себе, доходит до профиля — и только до своего.

Замер 2026-09-23 (нить profile-home), три дефекта одного класса «пишется не туда, откуда читают»:
  1. разборщик чата писал «поля профиля» в profile_context.json, а профиль читается из
     таблицы patient_profile (JSON — лишь запасной путь при ПУСТОЙ таблице). У второго
     тенанта 92 таких факта, у владельца 150 — ни один не дошёл до профиля;
  2. геопозиция из Telegram писалась в iCloud-файл ВЛАДЕЛЬЦА — зашитым путём, для любого бота;
  3. ответы опросников ложились в ~/health/data/assessments — каталог владельца — у любого тенанта.
Каждый тест ниже красный на коде до правки (b0fb076).
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


def _arbiter(anthropic_mock, monkeypatch, payload: dict, user_message: str):
    import anthropic
    import hai_chat
    monkeypatch.setattr(hai_chat, "get_client", lambda: anthropic.Anthropic(api_key="fake"))
    anthropic_mock.script(match=lambda p: "profile_updates" in p,
                          response=json.dumps(payload, ensure_ascii=False))
    hai_chat.run_arbiter(user_message, "ответ")


def test_сказанное_в_чате_доходит_до_профиля(db, anthropic_mock, monkeypatch):
    import health_db as hdb
    _arbiter(anthropic_mock, monkeypatch,
             {"profile_updates": {"weight": "80", "smoking_status": "не курю", "psa_level": "1.2"},
              "nothing_to_extract": False},
             "я вешу 80 кг, не курю, PSA был 1.2")
    prof = hdb.get_patient_profile()
    assert prof.get("identity.weight_kg") == "80", prof
    assert prof.get("routine.smoking") == "не курю", prof
    assert "psa_level" not in prof and not any("psa" in k for k in prof), (
        f"ключ без поля в profile_fields.yaml попал в профиль: {prof}")


def test_незаземлённое_в_профиль_не_пишется(db, anthropic_mock, monkeypatch):
    """Число, которого нет в словах человека (модель прочла его с картинки или выдумала),
    остаётся в памяти с пометкой карантина и в профиль не едет."""
    import health_db as hdb
    _arbiter(anthropic_mock, monkeypatch,
             {"profile_updates": {"weight": "95"}, "nothing_to_extract": False},
             "как там мой сон?")
    assert "identity.weight_kg" not in hdb.get_patient_profile()


def test_ответ_вне_диапазона_отбит(db):
    import profile_db
    assert profile_db.apply_stated("рост", "1860", source="test") is None
    assert profile_db.apply_stated("рост", "186 см", source="test") == ("identity.height_cm", "186")
    assert profile_db.apply_stated("пол", "Мужской", source="test") == ("identity.sex", "male")
    assert profile_db.apply_stated("dob", "2099-01-01", source="test") is None


def test_геопозиция_не_пишется_в_файл_владельца(tmp_path, monkeypatch):
    from handlers.messages import handle_location
    monkeypatch.setenv("HOME", str(tmp_path))
    icloud = tmp_path / "Library/Mobile Documents/com~apple~CloudDocs/health/data"
    icloud.mkdir(parents=True)
    upd = SimpleNamespace(message=SimpleNamespace(
        location=SimpleNamespace(latitude=64.14, longitude=-21.94),
        chat=SimpleNamespace(id=1, send_action=AsyncMock()), reply_text=AsyncMock()),
        effective_chat=SimpleNamespace(id=1))
    geo = MagicMock()
    geo.read.return_value = b'{"address": {"city": "Reykjavik", "country": "Iceland", "country_code": "is"}}'
    geo.__enter__ = lambda self: self
    geo.__exit__ = lambda *a: None
    with patch("handlers.messages.db.get_profile_context", return_value={"identity": {"name": "T"}}), \
         patch("handlers.messages.db.save_memory"), \
         patch("urllib.request.urlopen", return_value=geo):
        asyncio.run(handle_location(upd, SimpleNamespace()))
    assert not (icloud / "profile_context.json").exists(), (
        "геопозиция тенанта записана в iCloud-файл владельца")


def test_ответы_опросника_ложатся_в_каталог_тенанта(db, tmp_path, monkeypatch):
    import assessment_dialog as ad
    import assessment_importer as ai
    # Каталог «владельца» — песочница: старый код пишет туда, а не в настоящий ~/health.
    чужой = tmp_path / "home" / "health" / "data" / "assessments"
    monkeypatch.setattr(ad, "ASSESSMENTS_DIR", чужой, raising=False)
    cat = tmp_path / "instruments"
    cat.mkdir()
    (cat / "t.json").write_text(json.dumps({
        "id": "t", "name": "T", "wording_version_hash": "v", "cadence_days": 30,
        "items": [{"id": "q1", "text": "Q1?", "subscale": "s"}],
        "subscales": [{"id": "s", "items": ["q1"], "direction": "symptom_higher_worse"}],
        "response_scale": {"min": 1, "max": 4}, "scoring_formula": "linear"}), encoding="utf-8")
    monkeypatch.setattr(ad, "INSTRUMENTS_DIR", cat)
    import health_db as hdb
    tid = hdb.save_task(source="t", type_="assessment", content="t", priority="medium")
    db.execute("UPDATE tasks SET fingerprint=? WHERE id=?", ("assessment:t", tid))
    _, _, sid = ad.start(chat_id=1, task_id=tid)
    ad.answer(sid, "q1", 2)
    свои = list((Path(hdb._HEALTH_DIR) / "data" / "assessments").glob("*.json"))
    чужие = list(чужой.glob("*.json"))
    assert свои and not чужие, f"ответы ушли не тенанту: свои={свои} чужие={чужие}"
    assert ai.assessments_dir() == Path(hdb._HEALTH_DIR) / "data" / "assessments"
