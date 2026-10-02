"""Допуск модели к роли (нить llm-provider, 2026-10-02). Держит: судей с точным эталоном
(включая позитивный контроль корпуса), выбор кандидатов, запись в КОНЕЦ цепочки,
раздельность цепочек двух проходов распознавателя, бюджет до вызова, правило
«настоящие бланки — только anthropic» и путь от вердикта до строки цепочки (C-48)."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import llm_admission as la  # noqa: E402

CORPUS = la.load_admission_corpus()
NOW = datetime(2026, 10, 2, 7, 0, tzinfo=timezone.utc)


def _perfect_answer(check: dict):
    if "all" in check:
        out = {}
        for c in check["all"]:
            out.update(_perfect_answer(c))
        return out
    return {check["field"]: check.get("set", check.get("equals"))}


# ── судьи ────────────────────────────────────────────────────────────────────
def test_lab_judge_catches_decimal_and_missing():
    gold = CORPUS["lab_vision"][2]["gold"]
    perfect = {"tests": [dict(g) for g in gold]}
    assert la.judge_lab(json.dumps(perfect), gold) == []
    bad = json.loads(json.dumps(perfect))
    bad["tests"][10]["value"] = 152          # ферритин без запятой
    del bad["tests"][0]                      # пропуск
    errs = la.judge_lab(json.dumps(bad), gold)
    assert len(errs) == 2 and any("Ferritin" in e for e in errs) and any("пропущен" in e for e in errs)


def test_treatment_judge_positive_and_negative():
    spec = CORPUS["treatment"]
    good = [dict(agents=e["agents"], cycles_completed=e["cycles"], status=e["status"]) for e in spec["expected"]]
    assert la.judge_treatment(json.dumps(good), spec) == []
    bad = json.loads(json.dumps(good))
    bad[3]["cycles_completed"] = 8
    bad.append(dict(agents=["metformin"]))
    errs = la.judge_treatment(json.dumps(bad), spec)
    assert any("циклов 8" in e for e in errs) and any("metformin" in e for e in errs)


@pytest.mark.parametrize("item", CORPUS["text"], ids=lambda i: i["id"])
def test_text_item_has_a_passing_and_a_failing_answer(item):
    """Позитивный контроль корпуса: верный ответ проходит, испорченный — краснеет."""
    good = _perfect_answer(item["check"])
    assert la.judge_text(json.dumps(good, ensure_ascii=False), item) == []
    spoiled = {k: ("insufficient_data" if v == "up" else ["X"] if isinstance(v, list) else 999 if isinstance(v, int)
                   else "up") for k, v in good.items()}
    assert la.judge_text(json.dumps(spoiled), item), "судья не отличил испорченный ответ"
    assert la.judge_text("не json", item)


# ── кандидаты и цепочки ──────────────────────────────────────────────────────
LISTED = [
    {"id": "claude-opus-5-5", "created_at": "2026-09-21"},
    {"id": "claude-opus-4-7", "created_at": "2026-04-14"},
    {"id": "claude-opus-4-6", "created_at": "2026-02-04"},
    {"id": "claude-sonnet-5", "created_at": "2026-06-29"},
    {"id": "claude-sonnet-4-6", "created_at": "2026-02-17"},
    {"id": "claude-fable-5", "created_at": "2026-06-07"},
]
FAM = CORPUS["policy"]["family_roles"]


def test_candidates_newer_family_only_newest_first():
    chains = {"opus": ["claude-opus-4-7"], "sonnet": ["claude-sonnet-4-6"]}
    got = la.admission_candidates(LISTED, chains, {}, FAM)
    assert got == [("claude-opus-5-5", "opus"), ("claude-sonnet-5", "sonnet")]


def test_candidates_skip_chain_members_and_judged():
    chains = {"opus": ["claude-opus-4-7", "claude-opus-5-5"], "sonnet": ["claude-sonnet-4-6"]}
    judged = {"claude-sonnet-5": {"roles": {"sonnet": {"passed": False}}}}
    assert la.admission_candidates(LISTED, chains, judged, FAM) == []


@pytest.fixture
def store(monkeypatch):
    import hai_core
    import health_db
    st: dict = {}
    monkeypatch.setattr(health_db, "get_config", lambda k, d=None, **kw: st.get(k, d))
    monkeypatch.setattr(health_db, "upsert_config",
                        lambda k, value_text=None, value_num=None, value_json=None, **kw:
                        st.__setitem__(k, value_json if value_json is not None else
                                       value_num if value_num is not None else value_text))
    monkeypatch.setattr(hai_core.db, "get_config", lambda k, d=None, **kw: st.get(k, d))
    return st


def test_admit_appends_to_end_and_keeps_two_passes_apart(store):
    store["model.opus"] = ["claude-opus-4-7"]
    assert la.admit_to_chain("opus", "claude-opus-5-5") == ["claude-opus-4-7", "claude-opus-5-5"]
    assert store["model.opus"][0] == "claude-opus-4-7", "первая модель не меняется"
    with pytest.raises(ValueError):
        la.admit_to_chain("sonnet", "claude-opus-5-5")


# ── бюджет ───────────────────────────────────────────────────────────────────
def test_ledger_reserves_before_call_and_refuses_over_cap(store):
    led = la.Ledger({"usd_month": 1, "price_ceiling_per_mtok": {"in": 15, "out": 75}}, NOW)
    led.reserve(1000, 8000)                  # 0.615 $ — помещается
    led.charge(1000, 8000)
    with pytest.raises(la.BudgetExhausted):
        led.reserve(1000, 8000)              # ещё 0.615 — не помещается в 1 $
    assert store["llm.admission.spend.2026-10"] == pytest.approx(0.615)


def test_no_budget_spends_nothing(store):
    class Boom:
        def __getattr__(self, n):
            raise AssertionError("без бюджета клиент трогать нельзя")
    rep = la.run_admission(force=True, client=Boom(), now=NOW)
    assert "бюджет" in rep["skipped"]


def test_weekly_gate(store):
    store["llm.admission.last_run"] = (NOW - timedelta(days=3)).isoformat()
    assert "неделя" in la.run_admission(now=NOW)["skipped"]


# ── сквозной путь: вердикт → строка цепочки (C-48) ──────────────────────────
class _Msg:
    def __init__(self, text, model):
        self.content = [type("B", (), {"type": "text", "text": text})()]
        self.usage = type("U", (), {"input_tokens": 100, "output_tokens": 50})()
        self.model = model


class _FakeClient:
    """Отвечает эталоном: по тексту запроса понимает, какой набор спрашивают."""
    def __init__(self, listed, wrong_text=False):
        self._listed = listed
        self.wrong_text = wrong_text
        self.messages = self
        self.models = self
        self.calls = []

    def list(self, limit=100):
        return [type("M", (), m)() for m in self._listed]

    def create(self, model, messages, **kw):
        self.calls.append(model)
        content = messages[0]["content"]
        prompt = content[-1]["text"]
        if content[0]["type"] == "image":
            data = content[0]["source"]["data"]
            for it in CORPUS["lab_vision"]:
                import base64
                if base64.standard_b64encode((la.CORPUS_DIR / it["image"]).read_bytes()).decode() == data:
                    return _Msg(json.dumps({"tests": it["gold"]}), model)
        if prompt.startswith("ДОКУМЕНТ:"):
            exp = CORPUS["treatment"]["expected"]
            return _Msg(json.dumps([dict(agents=e["agents"], cycles_completed=e["cycles"], status=e["status"])
                                    for e in exp]), model)
        item = next(i for i in CORPUS["text"] if i["prompt"] == prompt)
        ans = _perfect_answer(item["check"])
        if self.wrong_text and item["id"] == "trend_n1":
            ans = {"trend": "down"}
        return _Msg(json.dumps(ans, ensure_ascii=False), model)


def _budget(store):
    store["llm.admission.budget"] = {"usd_month": 10, "price_ceiling_per_mtok": {"in": 15, "out": 75}}


def test_end_to_end_pass_lands_in_chain_and_fail_does_not(store, monkeypatch):
    _budget(store)
    monkeypatch.setattr(la, "owner_real_pages", lambda: [])
    store["model.opus"] = ["claude-opus-4-7"]
    store["model.sonnet"] = ["claude-sonnet-4-6"]
    rep = la.run_admission(force=True, client=_FakeClient(LISTED[:5]), now=NOW)
    assert {(r["model"], r["passed"]) for r in rep["results"]} == {("claude-opus-5-5", True), ("claude-sonnet-5", True)}
    assert store["model.opus"] == ["claude-opus-4-7", "claude-opus-5-5"]
    assert store["llm.admission.claude-opus-5-5"]["roles"]["opus"]["passed"] is True
    assert 0 < store["llm.admission.spend.2026-10"] < 10

    store2 = {k: v for k, v in store.items() if not k.startswith("llm.admission.claude")}
    store.clear(); store.update(store2)
    store["model.opus"] = ["claude-opus-4-7"]
    store["model.sonnet"] = ["claude-sonnet-4-6"]
    rep = la.run_admission(force=True, client=_FakeClient(LISTED[:5], wrong_text=True), now=NOW)
    assert all(not r["passed"] for r in rep["results"])
    assert store["model.opus"] == ["claude-opus-4-7"], "непрошедшая попала в цепочку"


def test_real_owner_pages_go_only_to_anthropic(monkeypatch, store):
    _budget(store)
    called = []
    monkeypatch.setattr(la, "owner_real_pages", lambda: called.append(1) or [])
    led = la.Ledger(store["llm.admission.budget"], NOW)
    client = _FakeClient(LISTED)
    la.run_suite(client, "gpt-x", "lab_vision_p1", CORPUS, led, provider="openai")
    assert called == [], "настоящие бланки ушли не-anthropic провайдеру"
    la.run_suite(client, "claude-x", "lab_vision_p1", CORPUS, led, provider="anthropic")
    assert called == [1]


def test_run_stops_before_a_call_that_would_overspend(store, monkeypatch):
    """Стоп-кран стоит ДО вызова: зрение с лимитом 8000 токенов не помещается в 0.5 $ —
    ни одного платного вызова, прогон остановлен с причиной."""
    store["llm.admission.budget"] = {"usd_month": 0.5, "price_ceiling_per_mtok": {"in": 15, "out": 75}}
    monkeypatch.setattr(la, "owner_real_pages", lambda: [])
    store["model.opus"] = ["claude-opus-4-7"]
    store["model.sonnet"] = ["claude-sonnet-4-6"]
    client = _FakeClient(LISTED[:5])
    rep = la.run_admission(force=True, client=client, now=NOW)
    assert client.calls == [] and rep["stopped"] and rep["results"] == []


# ── Э3б: чужие провайдеры → таблица выпуска ─────────────────────────────────
def test_lab_judge_counts_extra_rows_only_when_gold_is_complete():
    """Дубль строк (gpt-5.6-sol, замер 01.10: 32 вместо 16) — ошибка на синтетике, где
    эталон полный; на настоящей странице эталон неполон (владелец мог отклонить строку)."""
    gold = CORPUS["lab_vision"][2]["gold"]
    doubled = json.dumps({"tests": [dict(g) for g in gold] * 2})
    assert la.judge_lab(doubled, gold) == []
    errs = la.judge_lab(doubled, gold, complete=True)
    assert len(errs) == len(gold) and all("лишняя" in e for e in errs)


def test_suite_stops_at_first_error_and_spends_no_more(store, monkeypatch):
    _budget(store)
    led = la.Ledger(store["llm.admission.budget"], NOW)
    good, bad = _FakeClient(LISTED), _FakeClient(LISTED, wrong_text=True)
    full = la.run_suite(good, "m", "text", CORPUS, led, provider="anthropic")
    short = la.run_suite(bad, "m", "text", CORPUS, led, provider="anthropic")
    assert not full["errors"] and not full["stopped_early"]
    assert short["errors"] and short["stopped_early"] and len(bad.calls) < len(good.calls)


def test_foreign_reserve_includes_reasoning_tokens(store):
    """Переводчик просит у чужой модели max_tokens + запас на рассуждение — резерв обязан
    считать тот же потолок, иначе один вызов перепрыгнет бюджет."""
    budget = {"usd_month": 0.3, "price_ceiling_per_mtok": {"in": 15, "out": 75}}
    client = _FakeClient(LISTED)
    la.run_suite(client, "m", "text", CORPUS, la.Ledger(budget, NOW), provider="anthropic")
    store.clear()
    n = len(client.calls)
    with pytest.raises(la.BudgetExhausted):
        la.run_suite(client, "m", "text", CORPUS, la.Ledger(budget, NOW), provider="openai")
    assert len(client.calls) == n, "вызов ушёл до проверки бюджета"


class _TerraLike(_FakeClient):
    """Как gpt-5.6-terra на жёстком бланке: одно правдоподобное число мимо."""
    def create(self, model, messages, **kw):
        r = super().create(model, messages, **kw)
        obj = json.loads(r.content[0].text)
        if isinstance(obj, dict) and len(obj.get("tests") or []) == len(CORPUS["lab_vision"][2]["gold"]):
            obj["tests"][0]["value"] = float(obj["tests"][0]["value"]) + 0.6
            r.content[0].text = json.dumps(obj)
        return r


class _SolLike(_FakeClient):
    """Как gpt-5.6-sol на жёстком бланке: каждая строка дважды."""
    def create(self, model, messages, **kw):
        r = super().create(model, messages, **kw)
        obj = json.loads(r.content[0].text)
        if isinstance(obj, dict) and obj.get("tests"):
            obj["tests"] = obj["tests"] * 2
            r.content[0].text = json.dumps(obj)
        return r


def test_provider_admission_writes_table_not_chains(store, monkeypatch, tmp_path):
    import llm_client
    _budget(store)
    store["model.opus"] = ["claude-opus-4-7"]
    monkeypatch.setattr(la, "owner_real_pages", lambda: pytest.fail("настоящие бланки ушли чужому"))
    table = tmp_path / "t.json"
    table.write_text('{"_why": "x"}')
    rd = llm_client.profiles()["openai"]["role_defaults"]
    rep = la.run_provider_admission("openai", client=_FakeClient(LISTED), now=NOW, table_file=table)
    t = json.loads(table.read_text())
    assert {r for r in t["openai"]} == set(CORPUS["policy"]["role_suites"]) & set(rd)
    assert all(t["openai"][r][rd[r]]["passed"] for r in t["openai"]), rep
    assert store["model.opus"] == ["claude-opus-4-7"], "допуск чужого провайдера тронул цепочку владельца"
    assert la.run_provider_admission("openai", dry_run=True, now=NOW, table_file=table)["candidates"] == []

    table.write_text('{"_why": "x"}')
    la.run_provider_admission("openai", client=_TerraLike(LISTED), now=NOW, table_file=table)
    t = json.loads(table.read_text())
    assert t["openai"]["opus"][rd["opus"]]["passed"] is False, "число мимо на жёстком бланке не поймано"


def test_provider_admission_fails_doubled_rows_on_synthetic(store, tmp_path):
    import llm_client
    _budget(store)
    table = tmp_path / "t.json"
    table.write_text('{"_why": "x"}')
    la.run_provider_admission("openai", client=_SolLike(LISTED), now=NOW, table_file=table)
    rd = llm_client.profiles()["openai"]["role_defaults"]
    v = json.loads(table.read_text())["openai"]["opus"][rd["opus"]]
    assert v["passed"] is False and "лишняя" in v["suites"]["lab_vision_p1"]["errors"][0]


def test_reference_page_is_generated_from_the_table():
    """Справочник «что работает на каком провайдере» руками не пишется: разошёлся с таблицей
    допуска — красный (лечение: python3 llm_admission.py --reference)."""
    t = json.loads(la._TABLE_FILE.read_text(encoding="utf-8"))
    ref = Path(la.__file__).parent / "docs" / "reference"
    assert (ref / "llm_providers.md").read_text(encoding="utf-8") == la.admission_reference_md(t, "ru")
    assert (ref / "llm_providers.en.md").read_text(encoding="utf-8").split("\n", 1)[1] == la.admission_reference_md(t, "en")


def test_reference_shows_a_failed_role_with_its_reason():
    import llm_client
    rd = llm_client.profiles()["openai"]["role_defaults"]
    t = {"openai": {"sonnet": {rd["sonnet"]: {"passed": False, "date": "2026-10-02",
                                              "suites": {"lab_vision_p1": {"errors": ["lab_hard.jpg: MCH: 29.3 вместо 29.9"]}}}}}}
    md = la.admission_reference_md(t)
    row = next(line for line in md.splitlines() if line.startswith("| sonnet") and rd["sonnet"] in line)
    assert "| нет |" in row and "MCH" in row
