"""Думание — свойство задачи, а не места в коде (нить thinking-modes, 04.10).

03.10 и 04.10 модели 5-й серии потратили весь лимит ответа на рассуждение: упали бриф,
ночной разбор, недельный отчёт. Оракулы:
  • у каждого вызова модели в рабочем коде есть ключ задачи из таблицы, у ключа — вызов;
  • модель, думающая по умолчанию, без записи «как выключить» в цепочку не попадает;
  • обёртка: «читать» — выключает думание способом этой модели; «думать» — только при
    замеренном запасе, запас прибавляется к лимиту ответа; голодный ответ — один повтор
    с удвоенным запасом (и в асинхронном пути); решение вызывающего не трогается.
"""
import ast
import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

import llm_client

ROOT = Path(__file__).resolve().parents[2]
MODES = json.loads((ROOT / "methodology/llm_task_modes.json").read_text(encoding="utf-8"))["tasks"]


def _calls():
    import git_facts   # работает и в песочнице без .git (манифест) — §20: зелёный не от среды
    files = [f for f in git_facts.tracked("*.py")
             if not f.startswith(("tests/", "plans/", "logs/", "scripts/"))]
    for f in files:
        src = (ROOT / f).read_text(encoding="utf-8")
        if not re.search(r"messages\.(create|stream)\(", src):
            continue
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and node.func.attr in ("create", "stream") \
                    and isinstance(node.func.value, ast.Attribute) and node.func.value.attr == "messages":
                kw = {k.arg: k.value for k in node.keywords if k.arg}
                yield f"{f}:{node.lineno}", kw.get("task")


def test_every_model_call_names_its_task_and_every_task_has_a_call():
    calls = list(_calls())
    assert len(calls) > 50, "сторож ослеп: вызовов модели не нашлось"
    bad = [loc for loc, t in calls if not (isinstance(t, ast.Constant) and t.value in MODES)]
    assert not bad, (f"вызов модели без ключа задачи из methodology/llm_task_modes.json: {bad}; "
                     "как добавить — docs/how-to/add_llm_call.md")
    used = {t.value for _, t in calls}
    assert not set(MODES) - used, f"ключи без вызова: {sorted(set(MODES) - used)}"
    assert {v["mode"] for v in MODES.values()} <= {"think", "read", "disputed"}


def test_admitted_model_that_thinks_by_default_must_say_how_to_stop():
    table = json.loads((ROOT / "methodology/llm_admission_table.json").read_text(encoding="utf-8"))
    admitted = {m for role in (table.get("anthropic") or {}).values() for m, v in role.items() if v.get("passed")}
    missing = [m for m in admitted if not llm_client.thinking_profile(m)]
    assert not missing, f"допущенная модель без профиля думания (выключить нечем): {missing}"


class FakeMessages:
    def __init__(self, replies):
        self.calls, self.replies = [], list(replies)

    def create(self, **kw):
        self.calls.append(kw)
        return self.replies.pop(0)


TEXT = NS(stop_reason="end_turn", content=[NS(type="thinking"), NS(type="text", text="ok")])
STARVED = NS(stop_reason="max_tokens", content=[NS(type="thinking")])


def _msgs(replies, monkeypatch, reserve=0):
    monkeypatch.setattr(llm_client, "reasoning_reserve", lambda m: reserve)
    fake = FakeMessages(replies)
    return llm_client._GuardedMessages(fake, "anthropic"), fake


def test_read_task_switches_thinking_off_the_models_own_way(monkeypatch):
    g, fake = _msgs([TEXT, TEXT], monkeypatch, reserve=1000)
    g.create(task="weekly_digest._judge_leaks", model="claude-sonnet-5-5", max_tokens=5, messages=[])
    g.create(task="weekly_digest._judge_leaks", model="claude-opus-5", max_tokens=5, messages=[])
    assert fake.calls[0]["thinking"] == {"type": "between_tools"} and fake.calls[0]["max_tokens"] == 5
    assert fake.calls[1]["thinking"] == {"type": "disabled"}
    assert "task" not in fake.calls[0]


def test_think_task_without_measured_reserve_does_not_think(monkeypatch):
    g, fake = _msgs([TEXT], monkeypatch, reserve=0)
    g.create(task="monthly_consilium._run_coordinator", model="claude-sonnet-5-5", max_tokens=100, messages=[])
    assert fake.calls[0]["thinking"] == {"type": "between_tools"} and fake.calls[0]["max_tokens"] == 100


def test_think_task_adds_reserve_on_top_of_answer_and_drops_temperature(monkeypatch):
    g, fake = _msgs([TEXT], monkeypatch, reserve=3000)
    g.create(task="gp_agent.generate_weekly_report", model="claude-opus-5", max_tokens=4096,
             temperature=0, messages=[])
    assert fake.calls[0]["thinking"] == {"type": "adaptive"}
    assert fake.calls[0]["max_tokens"] == 4096 + 3000 and "temperature" not in fake.calls[0]


def test_budget_style_model_gets_reserve_as_budget(monkeypatch):
    g, fake = _msgs([TEXT], monkeypatch, reserve=2048)
    g.create(task="task_agent._judge_once", model="claude-haiku-4-5-20251001", max_tokens=200, messages=[])
    assert fake.calls[0]["thinking"] == {"type": "enabled", "budget_tokens": 2048}


def test_starved_answer_retries_once_with_double_reserve(monkeypatch):
    g, fake = _msgs([STARVED, TEXT], monkeypatch, reserve=1000)
    r = g.create(task="gp_agent.generate_weekly_report", model="claude-sonnet-5-5", max_tokens=500, messages=[])
    assert r is TEXT and [c["max_tokens"] for c in fake.calls] == [1500, 2500]


def test_starved_twice_stays_loud(monkeypatch):
    g, fake = _msgs([STARVED, STARVED], monkeypatch, reserve=1000)
    r = g.create(task="gp_agent.generate_weekly_report", model="claude-sonnet-5-5", max_tokens=500, messages=[])
    with pytest.raises(llm_client.NoTextInAnswer):
        llm_client.answer_text(r)
    assert len(fake.calls) == 2


def test_async_path_retries_too(monkeypatch):
    monkeypatch.setattr(llm_client, "reasoning_reserve", lambda m: 1000)
    replies, calls = [STARVED, TEXT], []

    class AsyncFake:
        async def create(self, **kw):
            calls.append(kw)
            return replies.pop(0)

    g = llm_client._GuardedMessages(AsyncFake(), "anthropic")
    r = asyncio.run(g.create(task="wellally_consult._call_coordinator_async",
                             model="claude-opus-5", max_tokens=400, messages=[]))
    assert r is TEXT and len(calls) == 2


def test_callers_own_thinking_and_unknown_models_are_untouched(monkeypatch):
    g, fake = _msgs([TEXT, TEXT], monkeypatch, reserve=1000)
    g.create(task="hai_chat.chat", model="claude-opus-5", max_tokens=10, thinking={"type": "x"}, messages=[])
    g.create(task="hai_chat.chat", model="some-unknown-model", max_tokens=10, messages=[])
    assert fake.calls[0]["thinking"] == {"type": "x"} and "thinking" not in fake.calls[1]


def test_foreign_provider_only_loses_the_task_key(monkeypatch):
    fake = FakeMessages([TEXT])
    llm_client._GuardedMessages(fake, "openai").create(task="hai_chat.chat", model="gpt", max_tokens=10, messages=[])
    assert fake.calls[0] == {"model": "gpt", "max_tokens": 10, "messages": []}


class _Stream:
    def __init__(self, msg):
        self.msg = msg

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def get_final_message(self):
        return self.msg


class StreamingFake:
    """Как SDK: create для обычных вызовов, stream для длинных, count_tokens для учёта."""
    def __init__(self, replies):
        self.replies, self.created, self.streamed = list(replies), [], []

    def create(self, **kw):
        self.created.append(kw)
        return self.replies.pop(0)

    def stream(self, **kw):
        self.streamed.append(kw)
        return _Stream(self.replies.pop(0))

    def count_tokens(self, **kw):
        return NS(input_tokens=2)


def _spend_log(monkeypatch, tmp_path):
    import api_spend_log
    path = tmp_path / "spend.log"
    monkeypatch.setattr(api_spend_log, "LOG_PATH", path)
    return path


def test_thinking_call_streams_and_records_thinking(monkeypatch, tmp_path):
    """04.10: с запасом под думание SDK отказывал обычному вызову («Streaming is required»)."""
    log = _spend_log(monkeypatch, tmp_path)
    monkeypatch.setattr(llm_client, "reasoning_reserve", lambda m: 30000)
    fake = StreamingFake([NS(stop_reason="end_turn", model="claude-opus-5",
                             usage=NS(input_tokens=10, output_tokens=12),
                             content=[NS(type="thinking"), NS(type="text", text="ok")])])
    llm_client._GuardedMessages(fake, "anthropic").create(
        task="monthly_consilium._run_coordinator", model="claude-opus-5", max_tokens=16000, messages=[])
    assert fake.streamed and not fake.created, "думающий вызов обязан идти потоком"
    rec = json.loads(log.read_text().splitlines()[-1])
    assert rec["agent"] == "monthly_consilium._run_coordinator" and rec["think_tokens"] == 10


def test_read_call_stays_a_plain_create(monkeypatch, tmp_path):
    log = _spend_log(monkeypatch, tmp_path)
    monkeypatch.setattr(llm_client, "reasoning_reserve", lambda m: 30000)
    fake = StreamingFake([TEXT])
    llm_client._GuardedMessages(fake, "anthropic").create(
        task="weekly_digest._judge_leaks", model="claude-opus-5", max_tokens=5, messages=[])
    assert fake.created and not fake.streamed and not log.exists()


def test_async_thinking_call_streams(monkeypatch, tmp_path):
    _spend_log(monkeypatch, tmp_path)
    monkeypatch.setattr(llm_client, "reasoning_reserve", lambda m: 1000)

    class _AStream(_Stream):
        async def get_final_message(self):
            return self.msg

    class AsyncStreamingFake(StreamingFake):
        async def create(self, **kw):
            return super().create(**kw)

        def stream(self, **kw):
            self.streamed.append(kw)
            return _AStream(self.replies.pop(0))

    fake = AsyncStreamingFake([STARVED, TEXT])
    r = asyncio.run(llm_client._GuardedMessages(fake, "anthropic").create(
        task="wellally_consult._call_coordinator_async", model="claude-opus-5", max_tokens=400, messages=[]))
    assert r is TEXT and len(fake.streamed) == 2 and not fake.created


def test_real_async_sdk_create_is_seen_as_async():
    """§20: фейки выше объявляют `async def create`, а у настоящего SDK create — синхронная
    обёртка над корутиной. Без этой проверки зелёный был от фейка, а прод упал бы на `with`.
    Клиент не создаётся (conftest это запрещает): берём настоящую функцию SDK с класса."""
    import inspect
    import types
    msgs = pytest.importorskip("anthropic.resources.messages")
    assert not inspect.iscoroutinefunction(msgs.AsyncMessages.create), "ловушка SDK исчезла — пересмотри _is_async"
    bound = types.MethodType(msgs.AsyncMessages.create, NS(stream=lambda **k: None))
    assert inspect.iscoroutinefunction(llm_client._streamed(bound))
    assert not llm_client._is_async(types.MethodType(msgs.Messages.create, NS(stream=None)))


# ── Замер модели: запас под думание и срок вызова (think-return, 04.10) ──────────

def _measured(monkeypatch, tmp_path, models):
    f = tmp_path / "measured.json"
    f.write_text(json.dumps({"models": models}), encoding="utf-8")
    monkeypatch.setattr(llm_client, "_MEASURED_FILE", f)


M5 = {"reserve_tokens": 1000, "latency_sec": 2.0, "sec_per_token": 0.01, "slack_sec": 5.0}


def test_reserve_comes_from_measurement_including_dated_model_id(monkeypatch, tmp_path):
    _measured(monkeypatch, tmp_path, {"claude-haiku-4-5": M5})
    assert llm_client.reasoning_reserve("claude-haiku-4-5-20251001") == 1000
    assert llm_client.reasoning_reserve("claude-unknown") == 0, "не замерена — не думает"


def test_call_timeout_covers_both_attempts_of_a_thinking_task(monkeypatch, tmp_path):
    _measured(monkeypatch, tmp_path, {"claude-opus-5": M5})
    # думает: первая попытка 400+1000, повтор 400+2000 → (2+14+5) + (2+24+5)
    assert llm_client.call_timeout("claude-opus-5", "monthly_consilium._run_coordinator", 400) == 52.0
    # читает: одна попытка на 400 → 2+4+5
    assert llm_client.call_timeout("claude-opus-5", "weekly_digest._judge_leaks", 400) == 11.0
    assert llm_client.call_timeout("claude-unknown", "monthly_consilium._run_coordinator", 400) is None


def test_measured_deadline_cuts_a_slow_async_call_and_passes_a_fast_one(monkeypatch, tmp_path):
    _measured(monkeypatch, tmp_path, {"claude-opus-5": {**M5, "sec_per_token": 0.0, "slack_sec": 0.0,
                                                         "latency_sec": 0.05}})

    class Slow:
        def __init__(self, delay):
            self.delay = delay

        async def create(self, **kw):
            await asyncio.sleep(self.delay)
            return TEXT
    kw = dict(task="weekly_digest._judge_leaks", model="claude-opus-5", max_tokens=5, messages=[],
              deadline="measured")
    with pytest.raises(llm_client.DeadlineExceeded):
        asyncio.run(llm_client._GuardedMessages(Slow(1.0), "anthropic").create(**kw))
    assert asyncio.run(llm_client._GuardedMessages(Slow(0.0), "anthropic").create(**kw)) is TEXT
    with pytest.raises(ValueError):   # у синхронного клиента срок не поддержан — не молча
        llm_client._GuardedMessages(FakeMessages([TEXT]), "anthropic").create(**kw)


def test_no_literal_timeout_around_a_model_call():
    """Литерал срока у вызова модели не знает ни думания, ни скорости модели: 04.10 45 с
    отрезали 9 мнений из 17. Срок — deadline="measured" (llm_client.call_timeout)."""
    import git_facts
    bad = []
    for f in git_facts.tracked("*.py"):
        if f.startswith(("tests/", "plans/", "logs/")):
            continue
        src = (ROOT / f).read_text(encoding="utf-8")
        if "wait_for" not in src:
            continue
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "wait_for" and node.args:
                inner = node.args[0]
                if isinstance(inner, ast.Call) and getattr(inner.func, "attr", None) in ("create", "stream") \
                        and getattr(inner.func.value, "attr", None) == "messages":
                    bad.append(f"{f}:{node.lineno}")
    assert not bad, f"срок вызова модели литералом через wait_for: {bad} — используй deadline=\"measured\""


def test_every_thinking_profile_has_a_measurement():
    """Профиль без замера = запас 0 = модель молча не думает и вызов без срока."""
    models = json.loads((ROOT / "methodology/llm_providers.json").read_text(encoding="utf-8"))
    names = models["anthropic"]["thinking"]["models"]
    missing = [m for m in names if not llm_client.measured(m).get("sec_per_token")]
    assert not missing, f"модель с профилем думания без замера: {missing} — llm_admission --record-thinking"


def test_record_thinking_takes_numbers_only_from_rows():
    import llm_admission
    rows = [{"model": "claude-opus-5", "mode": "think", "out": 1000, "sec": 12.0, "think_tok": 700},
            {"model": "claude-opus-5", "mode": "read", "out": 3000, "sec": 32.0, "think_tok": 2500},
            {"model": "claude-opus-5", "mode": "think", "out": 2000, "sec": 25.0, "think_tok": -7}]
    m = llm_admission.record_thinking(rows, "2026-10-04")["claude-opus-5"]
    assert m["reserve_tokens"] == 700, "запас — по задачам think; отрицательный подсчёт — ноль"
    assert m["calls"] == 3 and m["sec_per_token"] > 0 and m["slack_sec"] >= 0
    assert llm_admission.record_thinking(rows[:1], "2026-10-04") == {}, "по одной точке прямой нет"


def test_temperature_is_dropped_only_where_the_model_rejects_it():
    """Замер 04.10: sonnet-5-5, opus-5, opus-4-7 без думания отвечают 400 на temperature."""
    for m in ("claude-sonnet-5-5", "claude-opus-5", "claude-opus-4-7"):
        first, _ = llm_client._thinking_plan({"model": m, "max_tokens": 10, "temperature": 0}, "weekly_digest._judge_leaks")
        assert "temperature" not in first, m
    first, _ = llm_client._thinking_plan({"model": "claude-haiku-4-5-20251001", "max_tokens": 10, "temperature": 0},
                                         "weekly_digest._judge_leaks")
    assert first.get("temperature") == 0, "haiku temperature принимает — не трогать"


def test_lab_vision_thinks_on_both_passes():
    """Gold run 04.10: opus-5 (pass 1) without thinking added a stray Glucose row twice, 7/7 with it."""
    for m in ("claude-opus-5", "claude-sonnet-5-5"):
        first, _ = llm_client._thinking_plan({"model": m, "max_tokens": 4000}, "lab_recognizer._vision_call")
        assert first["max_tokens"] == 4000 + llm_client.reasoning_reserve(m) > 4000, m



def test_a_newer_model_does_not_inherit_an_older_models_profile(monkeypatch, tmp_path):
    """05.10: claude-opus-5-5 брала профиль и запас claude-opus-5 по префиксу и падала 400."""
    _measured(monkeypatch, tmp_path, {"claude-opus-5": M5})
    assert llm_client.reasoning_reserve("claude-opus-5-5") == 0
    assert llm_client.reasoning_reserve("claude-opus-5-20260101") == 1000, "датированный снимок — та же модель"
