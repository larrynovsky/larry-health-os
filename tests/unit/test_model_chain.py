"""Цепочка допущенных моделей и самопереключение с уведомлением (нить llm-provider, 2026-10-01).

Решение владельца 01.10: «сама с уведомлением» — при отзыве модели система переходит на
следующую допущенную модель роли и сообщает, а не спрашивает. Тесты держат три вещи:
выбор (get_model), датчик (model_health_check: доступность → активная → уведомление) и
независимость двух зрений распознавателя, которую смена модели может снять молча.
"""
from __future__ import annotations

import json
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


# ── запасные из таблицы выпуска (доделки-2, 02.10) ───────────────────────────
_TABLE = {"anthropic": {
    "opus": {"claude-opus-5": {"passed": True, "date": "2026-10-02", "corpus": "c"},
             "claude-opus-5-5": {"passed": False, "date": "2026-10-02", "corpus": "c"},
             "claude-sonnet-x": {"passed": True, "date": "2026-10-02", "corpus": "c"}},
    "sonnet": {"claude-sonnet-x": {"passed": True, "date": "2026-10-02", "corpus": "c"}}}}


def _table(monkeypatch, provider="anthropic"):
    monkeypatch.setattr(hai_core, "_admission_table", lambda: _TABLE)
    monkeypatch.setenv("HEALTH_LLM_PROVIDER", provider)


def test_partner_without_own_admission_gets_the_owners_successor_at_the_end(monkeypatch):
    """У партнёра model.<роль> нет (замер 02.10) — запасная из таблицы встаёт ПОСЛЕ модели по умолчанию."""
    _cfg(monkeypatch, {})
    _table(monkeypatch)
    chain = hai_core.model_chain("opus")
    assert chain[0] == hai_core.MODEL_DEFAULTS["opus"] and "claude-opus-5" in chain
    assert "claude-opus-5-5" not in chain, "непрошедшая попала в цепочку"


def test_owner_chain_is_unchanged_by_the_table(monkeypatch):
    _cfg(monkeypatch, {"model.opus": ["claude-opus-5", "claude-opus-4-7"], "model.sonnet": ["claude-sonnet-x"]})
    _table(monkeypatch)
    assert hai_core.model_chain("opus") == ["claude-opus-5", "claude-opus-4-7"], "таблица сменила начало цепочки владельца"


def test_table_never_makes_the_two_recognizer_passes_meet(monkeypatch):
    """claude-sonnet-x допущена в обе роли таблицей — в opus не дописывается: она уже в цепочке sonnet."""
    _cfg(monkeypatch, {"model.sonnet": ["claude-sonnet-x"]})
    _table(monkeypatch)
    assert not set(hai_core.model_chain("opus")) & set(hai_core.model_chain("sonnet"))


@pytest.mark.parametrize("bases", [{}, {"model.opus": ["base-opus"], "model.sonnet": ["base-sonnet"]}],
                         ids=["defaults", "explicit_bases"])
def test_shared_release_only_model_is_excluded_from_both_chains(monkeypatch, tmp_path, bases):
    """Ревью #4: X только в таблице обеих ролей — не запасная ни одного прохода."""
    _cfg(monkeypatch, bases)
    monkeypatch.setenv("HEALTH_LLM_PROVIDER", "anthropic")
    table = tmp_path / "shared.json"
    table.write_text(json.dumps({"anthropic": {
        "opus": {"X": {"passed": True}}, "sonnet": {"X": {"passed": True}}}}), encoding="utf-8")
    monkeypatch.setattr(hai_core, "_ADMISSION_TABLE", table)
    chains = {r: hai_core.model_chain(r) for r in ("opus", "sonnet")}
    assert all("X" not in c for c in chains.values()), f"таблица пересекла цепочки: {chains}"
    assert not set(chains["opus"]) & set(chains["sonnet"])


@pytest.mark.parametrize("reader", ["model_chain", "get_model"])
@pytest.mark.parametrize("stored", [False, True], ids=["default", "stored_base"])
def test_broken_release_json_preserves_base_or_default(monkeypatch, tmp_path, reader, stored):
    """Ревью #5: реальный reader битого JSON не выводит из строя базовую модель."""
    base = ["base-opus", "base-fallback"] if stored else [hai_core.MODEL_DEFAULTS["opus"]]
    _cfg(monkeypatch, {"model.opus": base} if stored else {})
    monkeypatch.setenv("HEALTH_LLM_PROVIDER", "anthropic")
    table = tmp_path / "broken.json"
    monkeypatch.setattr(hai_core, "_ADMISSION_TABLE", table)
    expected = base if reader == "model_chain" else base[0]
    table.write_text("{}", encoding="utf-8")
    assert getattr(hai_core, reader)("opus") == expected
    table.write_text("{", encoding="utf-8")
    assert getattr(hai_core, reader)("opus") == expected


def test_foreign_provider_does_not_take_anthropic_successors(monkeypatch):
    _cfg(monkeypatch, {"model.opus": ["gpt-x"]})
    _table(monkeypatch, provider="openai")
    assert hai_core.model_chain("opus") == ["gpt-x"]
