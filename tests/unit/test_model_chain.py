"""Цепочка допущенных моделей и самопереключение с уведомлением (нить llm-provider, 2026-10-01).

Решение владельца 01.10: «сама с уведомлением» — при отзыве модели система переходит на
следующую допущенную модель роли и сообщает, а не спрашивает. Тесты держат три вещи:
выбор (get_model), датчик (model_health_check: доступность → активная → уведомление) и
независимость двух зрений распознавателя, которую смена модели может снять молча.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import hai_core  # noqa: E402
import model_health_check as mh  # noqa: E402


def _cfg(monkeypatch, store: dict):
    monkeypatch.setattr(hai_core.db, "get_config", lambda k, d=None, **kw: store.get(k, d))


def _fresh(ids):
    return {"checked_at": datetime.now(timezone.utc).isoformat(), "ids": list(ids)}


# ── выбор ────────────────────────────────────────────────────────────────────
def test_pick_skips_unavailable_and_keeps_order():
    assert hai_core.pick_from_chain(["a", "b", "c"], {"b", "c"}) == "b"
    assert hai_core.pick_from_chain(["a", "b"], {"a", "b"}) == "a"


def test_pick_without_snapshot_or_with_none_available_takes_first():
    assert hai_core.pick_from_chain(["a", "b"], None) == "a"
    assert hai_core.pick_from_chain(["a", "b"], {"zzz"}) == "a"   # громкий отказ, не тихая подмена
    with pytest.raises(ValueError):
        hai_core.pick_from_chain([], None)


def test_get_model_follows_chain_with_fresh_snapshot(monkeypatch):
    _cfg(monkeypatch, {"model.opus": ["m-old", "m-new"], "llm.available": _fresh(["m-new"])})
    assert hai_core.get_model("opus") == "m-new"


def test_get_model_ignores_stale_snapshot(monkeypatch):
    old = (datetime.now(timezone.utc) - timedelta(hours=hai_core._AVAILABILITY_MAX_AGE_H + 1)).isoformat()
    _cfg(monkeypatch, {"model.opus": ["m-old", "m-new"], "llm.available": {"checked_at": old, "ids": ["m-new"]}})
    assert hai_core.get_model("opus") == "m-old"


def test_get_model_string_config_and_default_unchanged(monkeypatch):
    _cfg(monkeypatch, {"model.sonnet": "m-x"})
    assert hai_core.get_model("sonnet") == "m-x"
    _cfg(monkeypatch, {})
    assert hai_core.get_model("haiku") == hai_core.MODEL_DEFAULTS["haiku"]
    with pytest.raises(KeyError):
        hai_core.get_model("нет-такой-роли")


# ── датчик ───────────────────────────────────────────────────────────────────
class _Models:
    def __init__(self, ids):
        self.ids = ids

    def list(self, limit=100):
        return [type("M", (), {"id": i})() for i in self.ids]


class _Msgs:
    def __init__(self, alive):
        self.alive = alive

    def create(self, model, **kw):
        if model not in self.alive:
            raise RuntimeError("simulated retirement")
        return object()


class _Client:
    def __init__(self, listed, alive=()):
        self.models = _Models(listed)
        self.messages = _Msgs(set(listed) | set(alive))


def _chains(monkeypatch, chains: dict):
    monkeypatch.setattr(hai_core, "model_chain",
                        lambda role: chains.get(role, [hai_core.MODEL_DEFAULTS[role]]))


def _all_listed(extra=()):
    return list(hai_core.MODEL_DEFAULTS.values()) + list(extra)


def test_retired_first_model_switches_to_next_admitted(monkeypatch):
    _chains(monkeypatch, {"opus": ["m-old", "m-new"]})
    res = mh.check_models(client=_Client(listed=_all_listed(["m-new"])))
    assert res["opus"]["model"] == "m-new" and res["opus"]["ok"]


def test_alias_absent_from_list_but_answering_is_available(monkeypatch):
    _chains(monkeypatch, {"haiku": ["alias-x"]})
    res = mh.check_models(client=_Client(listed=_all_listed(), alive=["alias-x"]))
    assert res["haiku"]["ok"] and res["haiku"]["model"] == "alias-x"


def test_whole_chain_gone_is_failure(monkeypatch):
    _chains(monkeypatch, {"opus": ["m-old", "m-new"]})
    res = mh.check_models(client=_Client(listed=[m for m in _all_listed() if m]))
    assert not res["opus"]["ok"] and res["opus"]["model"] == "m-old"


def _persist_env(monkeypatch, store):
    import health_db
    import i18n
    import notify
    sent = []
    monkeypatch.setattr(health_db, "get_config", lambda k, d=None, **kw: store.get(k, d))
    monkeypatch.setattr(health_db, "upsert_config",
                        lambda k, value_text=None, value_json=None, **kw: store.__setitem__(k, value_text or value_json))
    monkeypatch.setattr(notify, "notify_operator", lambda text, *a, **k: sent.append(text))
    monkeypatch.setattr(i18n, "lang_of", lambda *a, **k: "ru")
    return sent


def test_switch_is_announced_once_and_recorded(monkeypatch):
    store = {"llm.active.opus": "m-old"}
    sent = _persist_env(monkeypatch, store)
    res = {"opus": {"model": "m-new", "ok": True, "chain": ["m-old", "m-new"]}, "_available": ["m-new"]}
    assert mh._persist_and_announce(res, notify=True) == [("opus", "m-old", "m-new")]
    assert store["llm.active.opus"] == "m-new" and store["llm.available"]["ids"] == ["m-new"]
    assert len(sent) == 1 and "m-new" in sent[0] and "для сведения" in sent[0]
    assert mh._persist_and_announce(res, notify=True) == [] and len(sent) == 1   # повтор молчит


def test_return_to_primary_uses_back_text(monkeypatch):
    store = {"llm.active.opus": "m-new"}
    sent = _persist_env(monkeypatch, store)
    res = {"opus": {"model": "m-old", "ok": True, "chain": ["m-old", "m-new"]}, "_available": ["m-old", "m-new"]}
    mh._persist_and_announce(res, notify=True)
    assert len(sent) == 1 and "снова доступна" in sent[0]


def test_first_run_records_without_announcing(monkeypatch):
    store = {}
    sent = _persist_env(monkeypatch, store)
    res = {"opus": {"model": "m-old", "ok": True, "chain": ["m-old"]}, "_available": ["m-old"]}
    assert mh._persist_and_announce(res, notify=True) == [] and sent == []
    assert store["llm.active.opus"] == "m-old"


# ── независимость двух зрений ────────────────────────────────────────────────
def test_lab_recognizer_refuses_when_both_passes_resolve_to_one_model(monkeypatch, tmp_path):
    import lab_recognizer as lr
    monkeypatch.setattr(lr.hai_core, "get_model", lambda role: "one-model")
    monkeypatch.setattr(lr, "_render_pages", lambda *a, **k: pytest.fail("до рендера дойти не должно"))
    with pytest.raises(RuntimeError, match="одну модель"):
        lr.recognize(tmp_path / "x.pdf", "2026-10-01")
