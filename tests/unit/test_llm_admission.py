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
def store(monkeypatch, tmp_path):
    import hai_core
    import health_db
    st: dict = {}
    monkeypatch.setattr(health_db, "get_config", lambda k, d=None, **kw: st.get(k, d))
    monkeypatch.setattr(health_db, "upsert_config",
                        lambda k, value_text=None, value_num=None, value_json=None, **kw:
                        st.__setitem__(k, value_json if value_json is not None else
                                       value_num if value_num is not None else value_text))
    monkeypatch.setattr(hai_core.db, "get_config", lambda k, d=None, **kw: st.get(k, d))
    table = tmp_path / "admission.json"
    table.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(hai_core, "_ADMISSION_TABLE", table)
    return st


def test_admit_appends_to_end_and_keeps_two_passes_apart(store):
    store["model.opus"] = ["claude-opus-4-7"]
    assert la.admit_to_chain("opus", "claude-opus-5-5") == ["claude-opus-4-7", "claude-opus-5-5"]
    assert store["model.opus"][0] == "claude-opus-4-7", "первая модель не меняется"
    with pytest.raises(ValueError):
        la.admit_to_chain("sonnet", "claude-opus-5-5")


def test_admit_persists_only_base_chain_with_nonempty_release_table(store, monkeypatch):
    """Ревью #7: уже зелёный на 06edf2d; base_chain→model_chain обязан его покраснить."""
    import hai_core
    monkeypatch.setenv("HEALTH_LLM_PROVIDER", "anthropic")
    store["model.opus"] = ["base-opus"]
    store["model.sonnet"] = ["base-sonnet"]
    hai_core._ADMISSION_TABLE.write_text(json.dumps({"anthropic": {
        "opus": {"table-only": {"passed": True}},
        "sonnet": {"other-table-only": {"passed": True}}}}), encoding="utf-8")
    assert hai_core.model_chain("opus") == ["base-opus", "table-only"]
    expected = ["base-opus", "newly-judged"]
    assert la.admit_to_chain("opus", "newly-judged") == expected
    assert store["model.opus"] == expected, "запасная таблицы вросла в базу"
    assert hai_core.model_chain("opus") == expected + ["table-only"]


# ── бюджет ───────────────────────────────────────────────────────────────────
def test_ledger_reserves_before_call_and_refuses_over_cap(store):
    led = la.Ledger({"usd_month": 1, "price_ceiling_per_mtok": {"in": 15, "out": 75}}, NOW)
    led.reserve(1000, 8000)                  # 0.615 $ — помещается
    led.charge(1000, 8000)
    with pytest.raises(la.BudgetExhausted):
        led.reserve(1000, 8000)              # ещё 0.615 — не помещается в 1 $
    assert store["llm.admission.spend.2026-10"] == pytest.approx(0.615)


def test_ledger_charges_cache_tokens_at_input_ceiling(store):
    led = la.Ledger({"usd_month": 1, "price_ceiling_per_mtok": {"in": 15, "out": 75}}, NOW)
    assert led.charge(1000, 100, cache_read_input_tokens=20000,
                      cache_creation_input_tokens=30000) == pytest.approx(0.7725)
    assert store["llm.admission.spend.2026-10"] == pytest.approx(0.7725)
    with pytest.raises(la.BudgetExhausted):
        led.reserve(20000, 0)


@pytest.mark.parametrize("cache", [{}, {"cache_read_input_tokens": None, "cache_creation_input_tokens": None},
    {"cache_read_input_tokens": 20000}, {"cache_creation_input_tokens": 30000},
    {"cache_read_input_tokens": 20000, "cache_creation_input_tokens": 30000}])
def test_call_records_cached_usage_and_limits_next_call(store, cache):
    from types import SimpleNamespace as NS
    led = la.Ledger({"usd_month": 0.1, "price_ceiling_per_mtok": {"in": 15, "out": 75}}, NOW)
    calls = []
    def create(**kw):
        calls.append(kw)
        return NS(model="m", content=[NS(type="text", text="ok")],
                  usage=NS(input_tokens=1000, output_tokens=100, **cache))
    client = NS(messages=NS(create=create))
    kw = dict(prompt="q", image=None, system=None, max_tokens=1, temperature=None, ledger=led)
    assert la._call(client, "m", **kw) == ("ok", "m")
    expected = ((1000 + sum(v or 0 for v in cache.values())) * 15 + 100 * 75) / 1e6
    assert led.used == pytest.approx(expected)
    assert store["llm.admission.spend.2026-10"] == pytest.approx(round(expected, 4))
    if any(cache.values()):
        with pytest.raises(la.BudgetExhausted):
            la._call(client, "m", **kw)
        assert len(calls) == 1, "cache-токены не остановили следующий платный вызов"


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
                    return _Msg(json.dumps({"tests": [dict(g, date=it["date"]) for g in it["gold"]]}), model)
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
    assert {(r["model"], r["passed"]) for r in rep["results"] if not r["member"]} == \
        {("claude-opus-5-5", True), ("claude-sonnet-5", True)}
    assert {r["model"] for r in rep["results"] if r["member"]} >= {"claude-opus-4-7", "claude-sonnet-4-6"}, \
        "члены цепочек без вердикта не судились"
    assert store["model.opus"] == ["claude-opus-4-7", "claude-opus-5-5"]
    assert store["llm.admission.claude-opus-5-5"]["roles"]["opus"]["passed"] is True
    assert 0 < store["llm.admission.spend.2026-10"] < 10

    store2 = {k: v for k, v in store.items()
              if not k.startswith(("llm.admission.claude", "llm.admission.suite."))}
    store.clear(); store.update(store2)
    store["model.opus"] = ["claude-opus-4-7"]
    store["model.sonnet"] = ["claude-sonnet-4-6"]
    rep = la.run_admission(force=True, client=_FakeClient(LISTED[:5], wrong_text=True), now=NOW)
    assert all(not r["passed"] for r in rep["results"])
    assert store["model.opus"] == ["claude-opus-4-7"], "непрошедшая попала в цепочку"
    assert all(x["all_failed"] for x in rep["removed"]), "провалили все — цепочка обязана остаться"


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
    for k in [k for k in store if k.startswith("llm.admission.suite.")]:
        del store[k]                      # другая «модель» под тем же именем — без готовых наборов
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


def test_reference_has_a_section_per_provider_including_anthropic():
    """Справочник повторяет устройство: каждый профиль — свой раздел. До 02.10 раздела anthropic
    не было — посторонний с ключом Anthropic не видел ни основных моделей, ни запасных."""
    import hai_core
    import llm_client
    t = {"anthropic": {"opus": {"claude-x-fallback": {"passed": True, "date": "2026-10-02", "corpus": "c"}}}}
    md = la.admission_reference_md(t)
    assert [ln[3:] for ln in md.splitlines() if ln.startswith("## ")] == list(llm_client.profiles())
    sec = md.split("## anthropic", 1)[1].split("\n## ", 1)[0]
    for role, model in hai_core.MODEL_DEFAULTS.items():
        assert f"| {role} |" in sec and f"`{model}`" in sec
    assert "`claude-x-fallback` | запасная: да |" in sec


def test_readme_points_to_the_reference_and_names_no_model():
    """Какие модели допущены, README не пересказывает — ссылается на генерируемый справочник.
    Копия разъехалась бы при первом же допуске (02.10: за день у OpenAI/Gemini допуск менялся
    дважды), и README обещал бы посторонним чат, которого нет. Имя модели в README — красный."""
    import hai_core
    import llm_client
    root = Path(la.__file__).parent
    t = json.loads(la._TABLE_FILE.read_text(encoding="utf-8"))
    names = set(hai_core.MODEL_DEFAULTS.values())
    names |= {m for p in llm_client.profiles().values() for m in (p.get("role_defaults") or {}).values()}
    names |= {m for prov in t.values() if isinstance(prov, dict)
              for role in prov.values() if isinstance(role, dict) for m in role}
    for readme, ref, howto in (("README.md", "llm_providers.en.md", "llm_provider.en.md"),
                               ("README.ru.md", "llm_providers.md", "llm_provider.md")):
        text = (root / readme).read_text(encoding="utf-8")
        assert f"docs/reference/{ref}" in text and f"docs/how-to/{howto}" in text, readme
        leaked = sorted(n for n in names if n in text)
        assert not leaked, f"{readme} называет модели {leaked} — ссылайтесь на справочник"


# ── исправления после первого живого допуска (02.10) ────────────────────────
def test_dated_previous_column_is_not_an_extra_row():
    """Жёсткий бланк печатает «Önceki (12.03.2026)». claude-opus-5-5 отдал эти значения отдельными
    строками с датой 12.03 — честная история, а не лишний текущий результат. Та же строка без даты
    или с датой текущих — ошибка: легла бы в базу как сегодняшняя."""
    page = CORPUS["lab_vision"][2]
    gold = page["gold"]
    cur = [dict(g, date=page["date"]) for g in gold]
    prev = [dict(g, date=page["history"]["date"]) for g in page["history"]["gold"]]
    kw = dict(complete=True, page_date=page["date"], history=page["history"])
    assert la.judge_lab(json.dumps({"tests": cur + prev}), gold, **kw) == []
    same_day = [dict(r, date="2026-09-15") for r in prev]
    assert len(la.judge_lab(json.dumps({"tests": cur + same_day}), gold, **kw)) == len(gold)
    undated = [{k: v for k, v in r.items() if k != "date"} for r in prev]
    assert len(la.judge_lab(json.dumps({"tests": cur + undated}), gold, **kw)) == len(gold)


@pytest.mark.parametrize("fault", ["current_with_past_date", "invented_history", "all_1900"])
def test_admission_rejects_wrong_lab_dates_and_invented_history(store, monkeypatch, fault):
    """Ревью #1: правильное число с неверной датой и выдуманная история не получают допуск."""
    import base64
    _budget(store)
    monkeypatch.setenv("HEALTH_LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(la, "owner_real_pages", lambda: [])
    hard = CORPUS["lab_vision"][2]
    hard_image = base64.standard_b64encode((la.CORPUS_DIR / hard["image"]).read_bytes()).decode()
    assert all(p["date"] == "2026-09-15" for p in CORPUS["lab_vision"])
    assert hard["history"]["date"] == "2026-03-12"

    class WrongDate(_FakeClient):
        def create(self, model, messages, **kw):
            r = super().create(model, messages, **kw)
            obj = json.loads(r.content[0].text)
            if isinstance(obj, dict) and obj.get("tests"):
                rows = obj["tests"]
                if fault == "all_1900":
                    for row in rows:
                        row["date"] = "1900-01-01"
                elif messages[0]["content"][0]["source"]["data"] == hard_image:
                    wbc = next(row for row in rows if row["canonical_name"] == "WBC")
                    assert wbc["value"] == 6.82
                    if fault == "current_with_past_date":
                        wbc["date"] = hard["history"]["date"]
                    else:
                        invented = 8.1234
                        assert all(g["value"] != invented for g in hard["history"]["gold"])
                        rows.append(dict(wbc, value=invented, date=hard["history"]["date"]))
                r.content[0].text = json.dumps(obj)
            return r

    model = LISTED[0]["id"]
    rep = la.run_admission(force=True, client=WrongDate(LISTED[:3]), now=NOW)
    verdict = store[f"llm.admission.{model}"]["roles"]["opus"]
    assert rep["results"] and not verdict["passed"], f"{fault}: модель допущена вопреки ошибке"
    assert model not in store.get("model.opus", [])


def test_gold_keeps_only_the_latest_reread():
    """Две promoted-строки одного показателя (перечитывание без оператора и позднее с «<») —
    эталон берёт позднюю; иначе требовал бы две строки, и верное чтение «< N» получало «пропущен».
    Значения выдуманы (публичный репозиторий)."""
    rows = [{"id": 101, "canonical_name": "Urine_Urobilinogen", "value": 20.0, "value_op": None},
            {"id": 102, "canonical_name": "Urine_Urobilinogen", "value": 20.0, "value_op": "<"},
            {"id": 7000, "canonical_name": "Urine_pH", "value": 6.0, "value_op": None},
            {"id": 7001, "canonical_name": None, "value": 1.0, "value_op": None}]
    gold = la.gold_from_promoted(rows)
    assert gold == [{"canonical_name": "Urine_Urobilinogen", "value": 20.0, "value_op": "<"},
                    {"canonical_name": "Urine_pH", "value": 6.0, "value_op": None}]
    assert la.judge_lab(json.dumps({"tests": [{"canonical_name": "Urine_Urobilinogen", "value": 20, "value_op": "<"},
                                              {"canonical_name": "Urine_pH", "value": 6}]}), gold) == []


def test_role_not_started_when_money_would_run_out_mid_role(store, monkeypatch):
    """Прерванный стоп-краном прогон тратит без вердикта (02.10: 4-й кандидат). Роль не начинается,
    если остаток ниже порога политики."""
    store["llm.admission.budget"] = {"usd_month": 10, "price_ceiling_per_mtok": {"in": 15, "out": 75}}
    store["llm.admission.spend.2026-10"] = 8.5
    monkeypatch.setattr(la, "owner_real_pages", lambda: [])
    store["model.opus"] = ["claude-opus-4-7"]
    store["model.sonnet"] = ["claude-sonnet-4-6"]
    client = _FakeClient(LISTED[:5])
    rep = la.run_admission(force=True, client=client, now=NOW)
    assert client.calls == [] and "не хватит" in rep["stopped"]


# ── доделки-2 (02.10): обрыв без повторной оплаты, псевдонимы, таблица выпуска ──
class _VisionCounter(_FakeClient):
    def create(self, model, messages, **kw):
        if messages[0]["content"][0]["type"] == "image":
            self.vision = getattr(self, "vision", 0) + 1
            self.by_model = getattr(self, "by_model", {})
            self.by_model[model] = self.by_model.get(model, 0) + 1
        return super().create(model, messages, **kw)


def test_rerun_after_a_killed_run_does_not_pay_again(store, monkeypatch):
    """Прогон оборвали после набора зрения (02.10 — деплой чужой нити) — перезапуск берёт готовый
    набор из базы и за зрение не платит; текст — платит, его не было."""
    _budget(store)
    monkeypatch.setattr(la, "owner_real_pages", lambda: [])
    store["model.opus"] = ["claude-opus-4-7"]
    store["model.sonnet"] = ["claude-sonnet-4-6"]
    real = la.run_suite

    def dies_on_text(client, model, suite, *a, **kw):
        if suite == "text":
            raise KeyboardInterrupt("деплой пересоздал контейнер")
        return real(client, model, suite, *a, **kw)
    monkeypatch.setattr(la, "run_suite", dies_on_text)
    first = _VisionCounter(LISTED[:5])
    with pytest.raises(KeyboardInterrupt):
        la.run_admission(force=True, client=first, now=NOW)
    assert first.vision > 0
    monkeypatch.setattr(la, "run_suite", real)
    second = _VisionCounter(LISTED[:5])
    la.run_admission(force=True, client=second, now=NOW)
    # чьё зрение оплачено в первом прогоне — во втором берётся из базы; остальные платят впервые.
    # Без кэша второй прогон заплатил бы за то же зрение дважды.
    paid = set(first.by_model)
    assert paid and not paid & set(second.by_model), (first.by_model, second.by_model)


@pytest.mark.parametrize("change", ["gold", "image", "cache_date"])
def test_suite_cache_tracks_real_inputs_and_original_measurement_date(store, monkeypatch, tmp_path, change):
    """Ревью #2: меняем только приватный вход; run_suite и кэш остаются настоящими."""
    import base64
    _budget(store)
    image = tmp_path / "private-page.jpg"
    image.write_bytes(b"private-page-v1")
    gold = [{"canonical_name": "WBC", "value": 1.25, "value_op": None}]
    monkeypatch.setattr(la, "owner_real_pages", lambda: [{
        "image_bytes": image.read_bytes(), "gold": gold, "key": image.name, "real": True}])

    class ReadsPage(_FakeClient):
        def __init__(self):
            super().__init__([])
            self.private_calls = []

        def create(self, model, messages, **kw):
            content = messages[0]["content"]
            if content[0]["type"] == "image":
                data = base64.b64decode(content[0]["source"]["data"])
                if data.startswith(b"private-page-"):
                    self.private_calls.append(data)
                    return _Msg(json.dumps({"tests": [{"canonical_name": "WBC", "value": 1.25}]}), model)
            return super().create(model, messages, **kw)

    model, client = "claude-opus-cache-fixture", ReadsPage()
    first = la._judge_role(client, model, "opus", CORPUS,
                          la.Ledger(store["llm.admission.budget"], NOW), "anthropic", {})
    assert la._role_passed(first, CORPUS["policy"]["role_suites"]["opus"])
    initial_calls = len(client.private_calls)
    assert initial_calls > 0
    key = la._suite_key(model, "lab_vision_p1")
    measured = store[key]["date"]
    version = CORPUS["version"]
    if change == "gold":
        gold[0]["value"] = 2.5
        fresh = la.run_suite(ReadsPage(), model, "lab_vision_p1", CORPUS,
                             la.Ledger(store["llm.admission.budget"], NOW), "anthropic")
        assert fresh["errors"], "свежий оракул должен обнаружить исправленное значение"
    elif change == "image":
        image.write_bytes(b"private-page-v2")
    second = la._judge_role(client, model, "opus", CORPUS,
                           la.Ledger(store["llm.admission.budget"], NOW + timedelta(days=6)), "anthropic", {})
    assert CORPUS["version"] == version
    if change == "cache_date":
        assert len(client.private_calls) == initial_calls, "неизменный набор не переиспользован"
        assert store[key]["date"] == measured, "переиспользование омолодило дату замера"
    else:
        assert len(client.private_calls) > initial_calls, f"изменился {change}, но приватную страницу не проверили"
        if change == "gold":
            assert second["lab_vision_p1"]["errors"], "модель не проверена по исправленному эталону"
        else:
            assert b"private-page-v2" in client.private_calls


def test_anthropic_suite_cache_cannot_publish_private_errors_for_another_provider(store, monkeypatch, tmp_path):
    """Ревью #3: одинаковый id у двух провайдеров; ошибка владельца не переезжает в выпуск."""
    import base64
    import copy
    import llm_client
    _budget(store)
    model = "same-model-id"
    private_value = 23.4567   # вымышленное значение, не данные владельца
    monkeypatch.setattr(la, "owner_real_pages", lambda: [{
        "image_bytes": b"private-page", "key": "private-page.jpg", "real": True,
        "gold": [{"canonical_name": "WBC", "value": private_value, "value_op": None}]}])

    class OwnerMismatch(_FakeClient):
        def create(self, model, messages, **kw):
            content = messages[0]["content"]
            if content[0]["type"] == "image" and base64.b64decode(content[0]["source"]["data"]) == b"private-page":
                return _Msg(json.dumps({"tests": [{"canonical_name": "WBC", "value": 1.25}]}), model)
            return super().create(model, messages, **kw)

    owner = la._judge_role(OwnerMismatch([]), model, "opus", CORPUS,
                          la.Ledger(store["llm.admission.budget"], NOW), "anthropic", {})
    assert any(str(private_value) in e for e in owner["lab_vision_p1"]["errors"])
    profiles = copy.deepcopy(llm_client.profiles())
    profiles["openai"]["role_defaults"] = {"opus": model}
    monkeypatch.setattr(llm_client, "profiles", lambda: profiles)
    monkeypatch.setattr(la, "owner_real_pages", lambda: pytest.fail("приватные страницы ушли чужому провайдеру"))
    table = tmp_path / "public.json"
    table.write_text("{}", encoding="utf-8")
    foreign = _FakeClient([])
    la.run_provider_admission("openai", client=foreign, now=NOW, table_file=table)
    public = table.read_text(encoding="utf-8")
    ref = la.admission_reference_md(json.loads(public))
    verdict = json.loads(public)["openai"]["opus"][model]
    assert verdict["passed"] and foreign.calls and str(private_value) not in public + ref, (
        "чужой допуск использовал приватный кэш вместо собственного прогона")


def test_alias_in_chain_is_not_a_candidate_for_itself(store):
    """claude-haiku-4-5 в цепочке = claude-haiku-4-5-20251001 в списке (models.retrieve, 02.10)."""
    class C(_FakeClient):
        def retrieve(self, mid):
            return type("M", (), {"id": {"claude-haiku-4-5": "claude-haiku-4-5-20251001"}.get(mid, mid)})()
    listed = [{"id": "claude-haiku-4-5-20251001", "created_at": "2025-10-15"}]
    _budget(store)
    store["model.haiku"] = ["claude-haiku-4-5"]
    rep = la.run_admission(force=True, dry_run=True, client=C(listed), now=NOW)
    assert all(m != "claude-haiku-4-5-20251001" for m, _ in rep["candidates"])
    rep = la.run_admission(force=True, dry_run=True, client=_FakeClient(listed), now=NOW)
    assert all(m != "claude-haiku-4-5-20251001" for m, _ in rep["candidates"]), \
        "без retrieve датированный снимок псевдонима из цепочки — тоже не кандидат (ревью 02.10, #6)"


@pytest.mark.parametrize("fault", ["retrieve_raises", "wrong_family"])
def test_canonicalization_failure_does_not_turn_chain_members_into_candidates(store, monkeypatch, fault):
    """Ревью #6: ошибка metadata API не теряет уже используемую модель."""
    _budget(store)
    monkeypatch.setenv("HEALTH_LLM_PROVIDER", "anthropic")
    if fault == "retrieve_raises":
        original, listed_id = "claude-haiku-4-5", "claude-haiku-4-5-20251001"
        store["model.haiku"] = [original]
        listed = [{"id": listed_id, "created_at": "2025-10-15"}]
    else:
        original = listed_id = LISTED[0]["id"]
        store["model.opus"] = ["claude-opus-4-7", original]
        listed = LISTED[:3]

    class C(_FakeClient):
        def retrieve(self, mid):
            if mid == original:
                if fault == "retrieve_raises":
                    raise RuntimeError("metadata API temporarily unavailable")
                return type("M", (), {"id": "claude-sonnet-wrong-family"})()
            return type("M", (), {"id": mid})()

    client = C(listed)
    rep = la.run_admission(force=True, dry_run=True, client=client, now=NOW)
    assert all(m != listed_id for m, role in rep["candidates"]), (
        f"{fault}: уже используемая модель стала кандидатом: {rep['candidates']}")
    assert client.calls == []


def test_export_carries_no_values_from_owner_pages():
    v = {"claude-opus-5-5": {"roles": {"opus": {"passed": False, "date": "2026-10-02", "corpus": "c",
                                                "suites": {"lab_vision_p1": {"errors": ["page-1.jpg: ALT: 31 вместо 13"]}}}}},
         "claude-opus-5": {"roles": {"opus": {"passed": True, "date": "2026-10-02", "corpus": "c", "suites": {}}}},
         "claude-sonnet-5": {"model": "claude-sonnet-5", "roles": {}, "withdrawn": {"why": "x"}}}
    out = la.export_anthropic_verdicts(v)
    assert out == {"opus": {"claude-opus-5": {"passed": True, "date": "2026-10-02", "corpus": "c"},
                            "claude-opus-5-5": {"passed": False, "date": "2026-10-02", "corpus": "c"}}}
    assert "31 вместо" not in json.dumps(out) and "ALT" not in json.dumps(out)


def test_release_table_has_no_owner_values():
    """Таблица выпуска уходит в публичный репозиторий: в разделе anthropic — только три поля."""
    t = json.loads(la._TABLE_FILE.read_text(encoding="utf-8"))
    for role, models in (t.get("anthropic") or {}).items():
        for m, v in models.items():
            assert set(v) == {"passed", "date", "corpus"}, (role, m, v)


# ── второе ревью (02.10, Codex D): даты как в бою, история с оператором, версии ──
_HARD = CORPUS["lab_vision"][2]


def _kw():
    return dict(complete=True, page_date=_HARD["date"], history=_HARD["history"])


def test_undated_current_rows_take_the_page_date_the_recognizer_would_take():
    """Все текущие без даты + одна историческая с датой: в бою (lab_recognizer.recognize) строки без
    даты получают самую частую дату страницы — 12.03. Судья обязан увидеть это как чужую дату."""
    cur = [dict(g) for g in _HARD["gold"]]
    one_past = [dict(_HARD["history"]["gold"][0], date=_HARD["history"]["date"])]
    errs = la.judge_lab(json.dumps({"tests": cur + one_past}), _HARD["gold"], **_kw())
    assert any("дата 2026-03-12 вместо 2026-09-15" in e for e in errs)
    assert la.judge_lab(json.dumps({"tests": cur}), _HARD["gold"], **_kw()) == [], "без дат вовсе — дата документа"


def test_same_value_in_current_and_past_column_is_not_an_error():
    gold = [{"canonical_name": "WBC", "value": 6.82}]
    hist = {"date": "2026-03-12", "gold": [{"canonical_name": "WBC", "value": 6.82}]}
    rows = [{"canonical_name": "WBC", "value": 6.82, "date": "2026-03-12"},
            {"canonical_name": "WBC", "value": 6.82, "date": "2026-09-15"}]
    assert la.judge_lab(json.dumps({"tests": rows}), gold, complete=True, page_date="2026-09-15",
                        history=hist) == []


def test_past_value_with_invented_operator_is_an_error():
    cur = [dict(g, date=_HARD["date"]) for g in _HARD["gold"]]
    past = [dict(h, date=_HARD["history"]["date"]) for h in _HARD["history"]["gold"]]
    past[0]["value_op"] = "<"
    errs = la.judge_lab(json.dumps({"tests": cur + past}), _HARD["gold"], **_kw())
    assert len(errs) == 1 and "лишняя строка <" in errs[0]


def test_gold_keeps_rereads_of_different_dates_apart():
    rows = [{"id": 1, "canonical_name": "WBC", "value": 6.82, "value_op": None, "date": "2026-03-12"},
            {"id": 2, "canonical_name": "WBC", "value": 6.82, "value_op": None, "date": "2026-09-15"}]
    assert len(la.gold_from_promoted(rows)) == 2


def test_canonical_id_accepts_only_a_dated_snapshot_of_the_same_name():
    class C:
        def __init__(self, rid):
            self.models = self
            self.rid = rid

        def retrieve(self, mid):
            return type("M", (), {"id": self.rid})()
    assert la._canonical_id(C("claude-haiku-4-5-20251001"), "claude-haiku-4-5") == "claude-haiku-4-5-20251001"
    assert la._canonical_id(C("claude-opus-4-5"), "claude-opus-4") == "claude-opus-4", "соседняя версия — не псевдоним"


def test_snapshot_rule_does_not_swallow_a_newer_version():
    listed = [{"id": "claude-opus-4-5", "created_at": "2026-02-01"},
              {"id": "claude-opus-4", "created_at": "2025-05-01"}]
    got = la.admission_candidates(listed, {"opus": ["claude-opus-4"]}, {}, {"claude-opus": ["opus"]})
    assert ("claude-opus-4-5", "opus") in got


def test_fingerprint_changes_with_policy(monkeypatch):
    import copy
    a = la._suite_fingerprint("text", CORPUS, "anthropic")
    c2 = copy.deepcopy(CORPUS)
    c2["policy"]["reps"]["text"] += 1
    assert la._suite_fingerprint("text", c2, "anthropic") != a


def test_unadmitted_functions_speak_words_not_role_names(monkeypatch):
    """Итог install.sh (03.10): «работает всё» при полном допуске, иначе — функции словами.
    До 03.10 итог печатал «допущены роли: opus sonnet …» и «остальное не работает» даже у
    поставщика, где допущено всё."""
    import hai_core
    table = {"p_full": {r: {"m": {"passed": True}} for r in la._ROLE_DOC},
             "p_half": {r: {"m": {"passed": True}} for r in ("sonnet", "haiku", "haiku_pinned")}}
    monkeypatch.setattr(hai_core, "_admission_table", lambda: table)
    assert la.unadmitted_functions("p_full") == []
    off = la.unadmitted_functions("p_half", "en")
    assert off == [la._ROLE_DOC["opus"][1]] and "consilium" in off[0]
    assert not any(r in " ".join(off) for r in ("opus", "sonnet", "haiku"))


def test_reference_marks_a_provider_that_is_not_offered():
    import llm_client
    hidden = [p for p, v in llm_client.profiles().items() if v.get("offered", True) is False]
    assert hidden, "нет ни одного скрытого поставщика — тест потерял предмет"
    md = la.admission_reference_md({})
    for p in hidden:
        assert "не предлагается" in md.split(f"## {p}", 1)[1].split("\n## ", 1)[0]


# ── члены цепочек под судом (нить admission-incumbents, 05.10, вариант «В») ──
class _Flaky(_FakeClient):
    """Портит ответ trend_n1: `always` — всем попыткам модели, `once` — только первой."""
    def __init__(self, listed, always=(), once=()):
        super().__init__(listed)
        self.always, self.once, self.spoiled, self.tasks = set(always), set(once), set(), []

    def create(self, model, messages, **kw):
        self.tasks.append((model, kw.get("task"), messages[0]["content"][0]["type"]))
        prompt = messages[0]["content"][-1]["text"]
        trend = next((i for i in CORPUS["text"] if i["id"] == "trend_n1"), {})
        if prompt == trend.get("prompt") and (model in self.always or (model in self.once and model not in self.spoiled)):
            self.spoiled.add(model)
            return _Msg(json.dumps({"trend": "down"}), model)
        return super().create(model, messages, **kw)


@pytest.fixture
def faults(monkeypatch):
    import notify
    got = []
    monkeypatch.setattr(notify, "fault", lambda msg, person_key=None: got.append(msg))
    return got


def _chains(store):
    store["model.opus"] = ["claude-opus-4-7", "claude-opus-4-6"]
    store["model.sonnet"] = ["claude-sonnet-4-6"]
    store["model.haiku"] = ["claude-haiku-4-5"]
    store["model.haiku_pinned"] = ["claude-haiku-4-5"]


def test_member_failing_twice_leaves_the_chain_and_once_stays(store, monkeypatch, faults):
    _budget(store)
    monkeypatch.setenv("HEALTH_LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(la, "owner_real_pages", lambda: [])
    _chains(store)
    listed = [m for m in LISTED if m["id"] in ("claude-opus-4-7", "claude-opus-4-6", "claude-sonnet-4-6")]
    rep = la.run_admission(force=True, now=NOW,
                           client=_Flaky(listed, always={"claude-opus-4-7"}, once={"claude-sonnet-4-6"}))
    assert store["model.opus"] == ["claude-opus-4-6"], "дважды провалившая осталась в цепочке"
    assert store["model.sonnet"] == ["claude-sonnet-4-6"], "один провал (разброс) снял модель"
    v = store["llm.admission.claude-sonnet-4-6"]["roles"]["sonnet"]
    assert v["passed"] and v["attempts"] == 2 and v["first_attempt_errors"] == 1
    assert rep["removed"] == [{"role": "opus", "before": ["claude-opus-4-7", "claude-opus-4-6"],
                               "after": ["claude-opus-4-6"], "all_failed": False}]
    assert len(faults) == 1 and "model.opus" in faults[0] and "claude-opus-4-7" in faults[0], faults


def test_all_members_failing_keep_the_chain_and_say_so(store, monkeypatch, faults):
    _budget(store)
    monkeypatch.setenv("HEALTH_LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(la, "owner_real_pages", lambda: [])
    _chains(store)
    rep = la.run_admission(force=True, now=NOW, client=_Flaky([], always={"claude-sonnet-4-6"}))
    assert store["model.sonnet"] == ["claude-sonnet-4-6"]
    assert rep["removed"] == [{"role": "sonnet", "before": ["claude-sonnet-4-6"],
                               "after": ["claude-sonnet-4-6"], "all_failed": True}]
    assert any("sonnet" in f and "оставлена" in f for f in faults), faults


def test_suite_calls_the_model_in_its_work_task_mode(store, monkeypatch):
    """Допуск судит ту модель, что работает: зрение — режимом lab_recognizer._vision_call,
    текст — в обоих режимах; думающий вызов резервирует запас думания (1 + 2 на повтор)."""
    import llm_client
    _budget(store)
    monkeypatch.setattr(la, "owner_real_pages", lambda: [])
    pol = CORPUS["policy"]
    assert la.suite_modes("lab_vision_p1", pol) == ["think"]           # с 05.10 (lab-vision-think)
    assert la.suite_modes("treatment", pol) == ["read"]
    assert la.suite_modes("text", pol) == ["read", "think"]
    assert la.suite_modes("text", pol, "openai") == ["read"]
    reserved = []
    real_reserve = la.Ledger.reserve
    monkeypatch.setattr(la.Ledger, "reserve", lambda self, i, o: (reserved.append(o), real_reserve(self, i, o))[1])
    c = _Flaky([])
    la.run_suite(c, "claude-opus-5", "lab_vision_p1", CORPUS, la.Ledger(store["llm.admission.budget"], NOW), "anthropic")
    assert {t for _, t, kind in c.tasks if kind == "image"} == {"llm_admission._call_think"}
    assert reserved[0] == pol["max_tokens"]["lab_vision_p1"] + 3 * llm_client.reasoning_reserve("claude-opus-5") \
        > pol["max_tokens"]["lab_vision_p1"]
    c = _Flaky([])
    la.run_suite(c, "claude-opus-5", "text", CORPUS, la.Ledger(store["llm.admission.budget"], NOW), "anthropic")
    assert {t for _, t, _ in c.tasks} == {"llm_admission._call", "llm_admission._call_think"}


def test_fresh_verdict_is_not_rejudged_and_a_mode_change_makes_it_stale(store, monkeypatch, faults):
    import llm_client
    _budget(store)
    monkeypatch.setenv("HEALTH_LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(la, "owner_real_pages", lambda: [])
    _chains(store)
    la.run_admission(force=True, now=NOW, client=_Flaky([]))
    rep = la.run_admission(force=True, dry_run=True, now=NOW, client=_Flaky([]))
    assert rep["members"] == [] and rep["candidates"] == []
    real = llm_client.task_mode
    monkeypatch.setattr(llm_client, "task_mode",
                        lambda t: "read" if t == "lab_recognizer._vision_call" else real(t))
    rep = la.run_admission(force=True, dry_run=True, now=NOW, client=_Flaky([]))
    assert {m for m, r in rep["members"]} == {"claude-opus-4-7", "claude-opus-4-6", "claude-sonnet-4-6"}, \
        "режим задачи зрения сменился — вердикты зрения устарели, а текстовые нет"


def test_treatment_suite_follows_the_providers_task_tier():
    """04.10: лечение у Anthropic извлекает sonnet (task_tiers) — набор treatment судит sonnet."""
    got = la.role_suites(CORPUS["policy"], "anthropic")
    assert "treatment" in got["sonnet"] and "treatment" not in got["haiku_pinned"]
    assert "treatment" in la.role_suites(CORPUS["policy"], "openai")["haiku_pinned"]


def test_failed_head_is_not_dropped_onto_an_unjudged_next(store, monkeypatch, faults):
    """Бюджет кончился до суда следующей модели: снять провалившую — значит переключить работу
    на непроверенную (switch_only_to_admitted). Не снимаем, говорим."""
    _budget(store)
    monkeypatch.setenv("HEALTH_LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(la, "owner_real_pages", lambda: [])
    _chains(store)
    results = [{"model": "claude-opus-4-7", "role": "opus", "passed": False, "member": True}]
    out = la._drop_failed_members(_FakeClient([]), results, {})
    assert store["model.opus"] == ["claude-opus-4-7", "claude-opus-4-6"]
    assert out[0]["next_not_admitted"] and any("не прошла допуск" in f for f in faults), faults
    passed_next = results + [{"model": "claude-opus-4-6", "role": "opus", "passed": True, "member": True}]
    la._drop_failed_members(_FakeClient([]), passed_next, {})
    assert store["model.opus"] == ["claude-opus-4-6"]



# ── имя аналита по правилу канона (нить admission-gold-identity, 05.10) ──────
def test_rdw_in_fl_is_rdw_sd_in_gold_and_in_the_answer():
    """Бланк печатает RDW дважды — в % и в фл. Черновик зовёт обе «RDW», канон — RDW и RDW_SD.
    Эталон страницы требовал «RDW = 40.5» и валил модели, верно прочитавшие RDW 12.7 %."""
    gold = la.gold_from_promoted([
        {"id": 1, "canonical_name": "RDW", "unit": "фл", "value": 40.5, "value_op": None, "date": "2026-09-15"},
        {"id": 2, "canonical_name": "RDW", "unit": "%", "value": 12.7, "value_op": None, "date": "2026-09-15"}])
    assert {(g["canonical_name"], g["value"]) for g in gold} == {("RDW_SD", 40.5), ("RDW", 12.7)}
    both = json.dumps({"tests": [{"canonical_name": "RDW", "unit": "%", "value": 12.7},
                                 {"canonical_name": "RDW", "unit": "фл", "value": 40.5}]})
    assert la.judge_lab(both, gold, page_date="2026-09-15") == []
    only_cv = json.dumps({"tests": [{"canonical_name": "RDW", "unit": "%", "value": 12.7}]})
    assert la.judge_lab(only_cv, gold, page_date="2026-09-15") == ["RDW_SD: пропущен"]
    sd_as_cv = json.dumps({"tests": [{"canonical_name": "RDW", "unit": "%", "value": 40.5},
                                     {"canonical_name": "RDW", "unit": "фл", "value": 40.5}]})
    assert la.judge_lab(sd_as_cv, gold, page_date="2026-09-15") == ["RDW: 40.5 вместо 12.7"]



# ── хвосты прогона 05.10 (нить admission-crash-tails) ────────────────────────
def test_a_failing_call_does_not_lose_the_run(store, monkeypatch, faults):
    """05.10: 400 у кандидата уронил весь прогон — без отметки last_run и без снятия провалов."""
    _budget(store)
    monkeypatch.setenv("HEALTH_LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(la, "owner_real_pages", lambda: [])
    _chains(store)

    class Boom(_Flaky):
        def create(self, model, messages, **kw):
            if model == "claude-opus-4-6":
                raise RuntimeError("400 thinking.type.disabled is not supported")
            return super().create(model, messages, **kw)
    rep = la.run_admission(force=True, now=NOW, client=Boom([], always={"claude-sonnet-4-6"}))
    assert [e["model"] for e in rep["errors"]] == ["claude-opus-4-6"]
    assert "opus" not in (store.get("llm.admission.claude-opus-4-6") or {}).get("roles", {}), "сбой вызова записан как вердикт"
    assert store["llm.admission.last_run"], "прогон потерян"
    assert any("сбой вызова" in f for f in faults)


def test_failed_twice_before_a_crash_is_dropped_on_the_next_run(store, monkeypatch, faults):
    """Вердикт «провал дважды» пережил обрыв прогона; следующий прогон его не судит (свежий), но снять обязан."""
    _budget(store)
    monkeypatch.setenv("HEALTH_LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(la, "owner_real_pages", lambda: [])
    _chains(store)
    la.run_admission(force=True, now=NOW, client=_Flaky([]))
    v = store["llm.admission.claude-opus-4-7"]
    v["roles"]["opus"].update(passed=False, attempts=2)
    store["model.opus"] = ["claude-opus-4-7", "claude-opus-4-6"]
    rep = la.run_admission(force=True, now=NOW, client=_Flaky([]))
    assert rep["members"] == []
    assert store["model.opus"] == ["claude-opus-4-6"]
