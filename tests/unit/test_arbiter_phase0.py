"""
tests/unit/test_arbiter_phase0.py — Фаза 0 (2026-07-04): страховка арбитра памяти.

Пины:
  1. experiments больше НЕ теряются молча → уходят в memory('experiment_candidate').
  2. run_arbiter пишет heartbeat (_arbiter_last_run) на каждом вызове.
  3. heartbeat свежее последнего user-сообщения → check_arbiter_liveness молчит.

Мокаем LLM через anthropic_mock + подмену hai_chat.get_client (обходит KEY_FILE).
"""
from __future__ import annotations

import json
import pytest

pytestmark = pytest.mark.unit

_PAYLOAD = {
    "profile_updates": {},
    "observations": [],
    "experiments": [
        {"name": "Магний для сна", "action": "start", "note": "начал магний 400мг на ночь"}
    ],
    "open_questions": [],
    "nothing_to_extract": False,
}


def _arm(anthropic_mock, monkeypatch):
    import anthropic
    import hai_chat
    monkeypatch.setattr(hai_chat, "get_client", lambda: anthropic.Anthropic(api_key="fake"))
    anthropic_mock.script(match=lambda p: "experiments" in p,
                          response=json.dumps(_PAYLOAD, ensure_ascii=False))
    return hai_chat


def test_experiments_no_longer_dropped(db, anthropic_mock, monkeypatch):
    hai_chat = _arm(anthropic_mock, monkeypatch)
    saved = hai_chat.run_arbiter("начал пить магний 400мг", "ок, зафиксировал")

    import health_db
    rows = health_db.get_memory("experiment_candidate")
    assert any(r.get("key") == "Магний для сна" for r in rows), \
        "experiment из арбитра должен сохраниться как experiment_candidate"
    assert "experiment_candidates" in saved


def test_arbiter_writes_heartbeat(db, anthropic_mock, monkeypatch):
    hai_chat = _arm(anthropic_mock, monkeypatch)
    hai_chat.run_arbiter("любое сообщение", "любой ответ")

    import config_db
    assert config_db.get_config("_arbiter_last_run") is not None, \
        "run_arbiter обязан оставлять heartbeat"


def test_liveness_sensor_silent_when_heartbeat_fresh(db, anthropic_mock, monkeypatch):
    hai_chat = _arm(anthropic_mock, monkeypatch)
    # сначала пишем user-сообщение, потом гоняем арбитр → heartbeat новее
    import health_db
    with health_db.get_conn() as conn:
        conn.execute("INSERT INTO conversation_history (role, content) VALUES ('user', 'привет')")
    hai_chat.run_arbiter("привет", "здравствуй")

    import integrity_tests as it
    it._warnings.clear()
    it.check_arbiter_liveness()
    labels = [w[0] for w in it._warnings]
    assert not any("арбитр памяти" in l for l in labels), \
        f"свежий heartbeat не должен давать warn, а дал: {labels}"


def test_liveness_warns_on_fresh_activity_without_heartbeat(db):
    """Актуальный отказ: свежее сообщение есть, арбитр не отметился → WARN."""
    import health_db
    with health_db.get_conn() as conn:
        conn.execute("INSERT INTO conversation_history (role, content) VALUES ('user', 'свежее')")
    import integrity_tests as it
    it._warnings.clear()
    it.check_arbiter_liveness()
    assert any("арбитр памяти" in w[0] for w in it._warnings), \
        "свежая активность без heartbeat обязана давать warn"


def test_arbiter_dual_writes_to_memory_facts(db, anthropic_mock, monkeypatch):
    """Фаза 2: run_arbiter зеркалит извлечённое в типизированную memory_facts."""
    import anthropic, hai_chat
    monkeypatch.setattr(hai_chat, "get_client", lambda: anthropic.Anthropic(api_key="fake"))
    payload = {
        "profile_updates": {"diagnosis": "Диагноз X"},
        "observations": ["плохо спал из-за стресса"],
        "open_questions": ["что с железом?"],
        "nothing_to_extract": False,
    }
    anthropic_mock.script(match=lambda p: "profile_updates" in p,
                          response=json.dumps(payload, ensure_ascii=False))
    hai_chat.run_arbiter("сообщение", "ответ")

    import memory_facts_db as mf
    facts = mf.get_facts("fact")
    states = mf.get_facts("state")
    questions = mf.get_facts("question")
    assert any(f["key"] == "diagnosis" and f["value"] == "Диагноз X" for f in facts)
    assert any(s["value"] == "плохо спал из-за стресса" for s in states)
    assert any(q["value"] == "что с железом?" for q in questions)


def test_liveness_silent_on_stale_activity(db):
    """Анти-кричавший-волк: старое сообщение (>3д) без heartbeat → тишина."""
    import health_db
    with health_db.get_conn() as conn:
        conn.execute(
            "INSERT INTO conversation_history (role, content, created_at) "
            "VALUES ('user', 'старое', datetime('now','-10 days'))"
        )
    import integrity_tests as it
    it._warnings.clear()
    it.check_arbiter_liveness()
    assert not any("арбитр памяти" in w[0] for w in it._warnings), \
        "старая активность не должна поднимать тревогу"


def test_arbiter_prompt_forbids_confabulation():
    """Сторож прог-1 (2026-07-06): промпт запрещает сочинять причинность/таймлайн/
    механизмы и брать обобщения из ответа ассистента. Ловит молчаливый откат правила
    будущей правкой. Контекст: composed-класс давал 36% лжи (0/11 причинных верны),
    корень — промпт сам учил сочинять. При ~1000 фраз/мес откат = тысячи ложных строк."""
    import hai_chat
    p = hai_chat.ARBITER_PROMPT.lower()
    for must in ("запрещено сочинять", "причинность", "этапы лечения", "механизм",
                 "не изобретай", "не бери обобщения из ответа ассистента"):
        assert must in p, f"из ARBITER_PROMPT исчезло ограничение прог-1: «{must}»"


def test_liveness_warns_when_heartbeat_stamp_unreadable(db):
    """A6 (23.09): свежее сообщение есть, штамп арбитра битый → «не судимо» вслух.
    До 23.09 датчик выходил молча, и битая отметка читалась как живой арбитр."""
    import health_db
    with health_db.get_conn() as conn:
        conn.execute("INSERT INTO conversation_history (role, content) VALUES ('user', 'свежее')")
        conn.execute("INSERT INTO system_config (key, value_text, updated_at) "
                     "VALUES ('_arbiter_last_run', 'ok', 'не-дата') "
                     "ON CONFLICT(key) DO UPDATE SET updated_at=excluded.updated_at")
    import integrity_tests as it
    it._warnings.clear()
    it.check_arbiter_liveness()
    assert any("штамп нечитаем" in w[0] for w in it._warnings), it._warnings
