"""Wave 5F-2: tests для _notify_patient_view формата."""
from __future__ import annotations
from pathlib import Path
import sys

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT))

import pytest
import hai_hypotheses as hh

pytestmark = pytest.mark.unit


def test_notify_uses_patient_view_when_complete(monkeypatch):
    captured = {}
    def fake_specialist(text, **kw):
        captured.update(text=text, **kw)
    monkeypatch.setattr(hh, "_notify_specialist", fake_specialist)

    cbcr = {
        "one_line_statement": "Invented technical fallback",
        "patient_view": {
            "noticed":      "Выдуманное наблюдение.",
            "might_mean":   "Выдуманное объяснение.",
            "do_now":       "Выдуманный следующий шаг.",
            "consult_when": "Выдуманное условие обращения к врачу.",
        },
    }
    hh._notify_patient_view(memory_id=42, cbcr_dict=cbcr, trigger_label="drift")
    t = captured["text"]
    # patient_view используется, не one_line_statement
    assert "Invented technical fallback" not in t
    assert "Выдуманное наблюдение" in t
    assert "ЗАМЕТИЛИ" in t
    assert "ВОЗМОЖНАЯ ПРИЧИНА" in t
    assert "ЧТО ДЕЛАТЬ СЕЙЧАС" in t
    assert "КОГДА К ВРАЧУ" in t
    assert "/hyp" not in t
    # 01.10: решить по гипотезе можно прямо в уведомлении — Подтвердить/Отклонить + Запрос
    kb = [[b.callback_data for b in row] for row in captured["reply_markup"].inline_keyboard]
    assert kb == [["act:hc:42", "act:hr:42"], ["act:hq:42"]], kb
    assert "drift" not in t
    assert "Без нажатия" in t


def test_notify_fallback_when_no_patient_view(monkeypatch):
    captured = {}
    monkeypatch.setattr(hh, "_notify_specialist", lambda t, **kw: captured.update(text=t, **kw))

    cbcr = {"one_line_statement": "Some long technical sentence without patient_view"}
    hh._notify_patient_view(memory_id=99, cbcr_dict=cbcr, trigger_label="drift")
    t = captured["text"]
    assert "Some long technical sentence" in t
    assert "/hyp" not in t
    assert captured["reply_markup"].inline_keyboard[1][0].callback_data == "act:hq:99"


def test_notify_fallback_when_partial_patient_view(monkeypatch):
    """Если 3 из 4 полей — тоже fallback."""
    captured = {}
    monkeypatch.setattr(hh, "_notify_specialist", lambda t, **kw: captured.update(text=t, **kw))

    cbcr = {
        "one_line_statement": "fallback statement",
        "patient_view": {
            "noticed":      "a", "might_mean": "b", "do_now": "c",
            # consult_when missing
        },
    }
    hh._notify_patient_view(memory_id=1, cbcr_dict=cbcr, trigger_label="drift")
    assert "fallback statement" in captured["text"]


def test_notify_specialist_no_truncation_at_200(monkeypatch, tmp_path):
    """W5F-2: убрано [:200] обрезание."""
    captured = {}
    def fake_urlopen(url, data, timeout):
        captured["data"] = data
        class FakeResp:
            def read(self): return b""
        return FakeResp()
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    # Создаём fake secrets
    secrets = tmp_path / ".health_secrets"
    secrets.mkdir()
    (secrets / "telegram_token").write_text("fake_token")
    (secrets / "telegram_chat_id").write_text("123")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    # Явно указываем secrets-каталог: тест про truncation, не про резолв секретов.
    # Без этого он опирался на «HEALTH_SECRETS_DIR не задан → дефолт ~/.health_secrets»,
    # что течёт под модульным setdefault из test_intent_page_delta (BL-TESTISO-1).
    monkeypatch.setenv("HEALTH_SECRETS_DIR", str(secrets))

    long_text = "X" * 1500
    hh._notify_specialist(long_text)
    body = captured.get("data", b"").decode()
    # Не обрезано на 200
    assert body.count("X") > 1000


def test_notify_internal_trigger_is_not_in_message(monkeypatch):
    captured = {}
    monkeypatch.setattr(hh, "_notify_specialist", lambda t, **kw: captured.update(text=t, **kw))

    cbcr = {"one_line_statement": "x", "patient_view": {
        "noticed": "a", "might_mean": "b", "do_now": "c", "consult_when": "d",
    }}
    hh._notify_patient_view(memory_id=7, cbcr_dict=cbcr,
                            trigger_label="specialist_review:cardiology")
    assert "specialist_review:cardiology" not in captured["text"]


def test_confirming_twice_does_not_make_a_second_protocol(monkeypatch):
    """01.10: двойное «Подтвердить» по #1118 завело два одинаковых протокола."""
    import json
    saved = []
    row = {"id": 5, "key": "k", "source": "s", "value": json.dumps({"status": "open"})}
    monkeypatch.setattr(hh.db, "get_memory", lambda **k: [row])
    def _save(**k):
        saved.append(k); row["value"] = k["value"]
    monkeypatch.setattr(hh.db, "save_memory", _save)
    first = hh.confirm_hypothesis(5)
    second = hh.confirm_hypothesis(5)
    assert not first.get("_already_confirmed") and second.get("_already_confirmed")
    assert len(saved) == 1
