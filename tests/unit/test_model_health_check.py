#!/usr/bin/env python3.11
"""Unit-тест датчика: детекция отказа без реального API."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import model_health_check as mh


class _Msgs:
    def __init__(self, bad):
        self.bad = bad
    def create(self, model, **kw):
        if model in self.bad:
            raise RuntimeError("simulated retirement")
        return type("M", (), {"model": model})()


class _Client:
    def __init__(self, bad):
        self.messages = _Msgs(bad)


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _chains_from_code_defaults(monkeypatch):
    """Датчик судится на цепочках по умолчанию, а не на данных тенанта: 02.10 владелец перевёл
    основные модели на claude-opus-5/claude-sonnet-5-5, и снимок его базы на стенде сделал
    test_one_retired_detected зелёным переключением вместо отказа (C-75: тест зависел от данных)."""
    import hai_core
    monkeypatch.setenv("HEALTH_LLM_PROVIDER", "anthropic")
    real = hai_core.db.get_config
    monkeypatch.setattr(hai_core.db, "get_config",
                        lambda k, d=None, **kw: d if str(k).startswith("model.") else real(k, d, **kw))
    monkeypatch.setattr(hai_core, "_admission_table", lambda: {})


def test_all_ok():
    r = mh.run_check(notify=False, client=_Client(bad=set()))
    assert all(v["ok"] for v in r.values()), r
    assert set(r) == set(mh.hai_core.MODEL_DEFAULTS), r


def test_one_retired_detected():
    bad = {next(iter(mh.hai_core.MODEL_DEFAULTS.values()))}
    r = mh.run_check(notify=False, client=_Client(bad=bad))
    fails = [v for v in r.values() if not v["ok"]]
    assert len(fails) == 1, r
    assert fails[0]["detail"], r


def test_temporary_failure_only_journals(monkeypatch, fault_journal):
    import notify
    sent = []
    monkeypatch.setattr(mh, "check_models", lambda **k: {
        "example": {"model": "fictional-model", "ok": False, "detail": "Timeout: fictional"}})
    monkeypatch.setattr(notify, "notify_operator", lambda text: sent.append(text))
    mh.run_check(notify=True)
    assert sent == [] and "model request failed" in fault_journal.read_text()


class _UsageLimit(Exception):
    """Как anthropic.BadRequestError на лимите ключа (текст выдуман по форме ответа API)."""
    status_code = 400

    def __str__(self):
        return "Error code: 400 - You have reached your specified API usage limits"


class _ListedClient:
    """Список моделей проходит (он бесплатный), пинг отбит по счёту — у алиаса, которого
    в списке нет (claude-haiku-4-5), доступность решает именно пинг."""
    def __init__(self, listed):
        self.models = type("L", (), {"list": lambda _s, limit=100: [type("M", (), {"id": m})() for m in listed]})()
        self.messages = type("C", (), {"create": lambda _s, **kw: (_ for _ in ()).throw(_UsageLimit())})()


def test_account_refusal_is_not_model_fault_nor_availability(monkeypatch, fault_journal):
    """Карточка report:9198ad (v0.2.4): «none в model_health_check.py:run_check». Пинг, отбитый
    по ключу/счёту, ничего не говорит о модели: в ремонт кода не пишется, владельцу установки —
    одна карточка про ключ, роль честно НЕ ok (код выхода CLI не врёт «всё хорошо»)."""
    import notify
    sent = []
    monkeypatch.setattr(notify, "notify_operator", lambda text: sent.append(text))
    alias = mh.hai_core.MODEL_DEFAULTS["haiku"]
    listed = [m for m in mh.hai_core.MODEL_DEFAULTS.values() if m != alias]
    r = mh.run_check(notify=True, client=_ListedClient(listed))
    assert r["haiku"]["model"] == alias and not r["haiku"]["ok"], r
    assert r["haiku"]["detail"].startswith(mh.ACCOUNT), r
    assert not fault_journal.exists() or "model request failed" not in fault_journal.read_text()
    import i18n, llm_client
    assert sent == [i18n.t("owner.card.llm_account", provider=llm_client.provider_title())]


def test_account_refusal_does_not_touch_the_snapshot(monkeypatch, fault_journal):
    """Ревью 28.10: «доступна» по отказу счёта вело к записи снимка и «модель снова доступна»
    с переключением с резервной на основную. Доступность не проверена — снимок не трогаем."""
    import notify
    monkeypatch.setattr(notify, "notify_operator", lambda text: None)
    persisted = []
    monkeypatch.setattr(mh, "_persist_and_announce", lambda res, n: persisted.append(res))
    alias = mh.hai_core.MODEL_DEFAULTS["haiku"]
    listed = [m for m in mh.hai_core.MODEL_DEFAULTS.values() if m != alias]
    mh.run_check(notify=True, client=_ListedClient(listed), persist=True)
    assert persisted == []
    mh.run_check(notify=True, client=_Client(bad=set()), persist=True)   # контроль: без отказа пишет
    assert len(persisted) == 1


def test_unavailable_model_asks_for_choice(monkeypatch, fault_journal):
    import notify
    import i18n
    sent = []
    monkeypatch.setattr(i18n, "lang_of", lambda *a, **k: "ru")
    monkeypatch.setattr(mh, "check_models", lambda **k: {
        "example": {"model": "fictional-model", "ok": False, "detail": "retired/not_found: example"}})
    monkeypatch.setattr(notify, "notify_operator", lambda text: sent.append(text))
    mh.run_check(notify=True)
    assert len(sent) == 1 and "Варианты:" in sent[0] and "Если промолчишь" in sent[0]
    assert "MODEL_DEFAULTS" not in sent[0] and "fictional-model" not in sent[0]


if __name__ == "__main__":
    test_all_ok()
    test_one_retired_detected()
    print("TEST PASS")


def test_missing_provider_is_failed_check_without_persistence(monkeypatch, tmp_path):
    import llm_client
    monkeypatch.delenv("HEALTH_LLM_PROVIDER", raising=False)
    monkeypatch.setattr(llm_client, "_KEY_FILE", tmp_path / "anthropic_key")
    monkeypatch.setattr(mh, "_persist_and_announce", lambda *a: pytest.fail("invalid snapshot"))
    result = mh.run_check(persist=True)
    assert result["configuration"]["ok"] is False
    assert "HEALTH_LLM_PROVIDER" in result["configuration"]["detail"]
