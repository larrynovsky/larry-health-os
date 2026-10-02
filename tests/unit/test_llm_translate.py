"""Контракт-тесты швов переводчика (нить llm-provider, решение владельца «вариант а»).

Фикстуры — НАСТОЯЩИЕ ответы OpenAI и Gemini на синтетический запрос (живая проба 02.10:
двухходовый вызов инструмента и обрезка лимитом), плюс 48 текстовых ответов замера 01.10
(plans/measure_llm_providers_2026-10-01/runs.tar.gz). Каждый шов — отдельный тест: на нём
ломаются чужие переводчики (plans/research_llm_multiprovider_github_2026-10-01.md)."""
from __future__ import annotations

import json
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import llm_translate as T  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "llm_translate"
PROF = {"reasoning_reserve_tokens": 100, "temperature": "drop"}
PROF_G = {"reasoning_reserve_tokens": 100, "temperature": "pass"}


def _fx(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


TOOLS = [{"name": "get_lab", "description": "d", "input_schema": {"type": "object", "properties": {}}}]
IMG = {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "QUJD"}}


# ── запрос ───────────────────────────────────────────────────────────────────
def test_openai_request_system_image_tools_limits():
    req = T.to_openai(dict(model="m", max_tokens=700, temperature=0, system="ты врач", tools=TOOLS,
                           messages=[{"role": "user", "content": [IMG, {"type": "text", "text": "q"}]}]), PROF)
    assert req["instructions"] == "ты врач"
    assert req["input"][0]["content"][0] == {"type": "input_image", "image_url": "data:image/jpeg;base64,QUJD"}
    assert req["max_output_tokens"] == 800 and "temperature" not in req
    assert req["tools"][0]["parameters"] == {"type": "object", "properties": {}}


def test_openai_tool_history_keeps_call_id_both_ways():
    m1 = T.from_openai(_fx("openai_turn1.json"))
    tu = next(b for b in m1.content if b.type == "tool_use")
    hist = [{"role": "user", "content": "q"},
            {"role": "assistant", "content": [b.model_dump() for b in m1.content]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tu.id, "content": "15.2"}]}]
    items = T.to_openai(dict(model="m", max_tokens=10, messages=hist), PROF)["input"]
    call = next(i for i in items if i.get("type") == "function_call")
    out = next(i for i in items if i.get("type") == "function_call_output")
    assert call["call_id"] == out["call_id"] == tu.id and out["output"] == "15.2"


def test_gemini_tool_result_addressed_by_name_and_signature_round_trips():
    m1 = T.from_gemini(_fx("gemini_turn1.json"))
    tu = next(b for b in m1.content if b.type == "tool_use")
    assert tu.provider_raw and tu.provider_raw.get("thought_signature"), "подпись рассуждения потеряна"
    hist = [{"role": "user", "content": "q"},
            {"role": "assistant", "content": [b.model_dump() for b in m1.content]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tu.id, "content": "15.2"}]}]
    req = T.to_gemini(dict(model="m", max_tokens=10, system="s", messages=hist), PROF_G)
    model_part = req["contents"][1]["parts"][0]
    assert model_part["thought_signature"] == tu.provider_raw["thought_signature"]
    resp = req["contents"][2]["parts"][0]["function_response"]
    assert resp["name"] == "get_lab" and resp["id"] == tu.id
    assert req["config"]["system_instruction"] == "s"
    assert req["config"]["automatic_function_calling"] == {"disable": True}


def test_gemini_orphan_tool_result_is_loud():
    with pytest.raises(T.UnsupportedFeature):
        T.to_gemini(dict(model="m", max_tokens=1, messages=[{"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "x", "content": "y"}]}]), PROF_G)


def test_unknown_block_is_loud_not_dropped():
    for fn in (T.to_openai, T.to_gemini):
        with pytest.raises(T.UnsupportedFeature):
            fn(dict(model="m", max_tokens=1, messages=[{"role": "user", "content": [
                {"type": "document", "source": {"type": "base64", "data": "x"}}]}]), PROF)


# ── ответ ────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("fn, prefix", [(T.from_openai, "openai"), (T.from_gemini, "gemini")])
def test_stop_reasons_tool_end_truncated(fn, prefix):
    assert fn(_fx(f"{prefix}_turn1.json")).stop_reason == "tool_use"
    m2 = fn(_fx(f"{prefix}_turn2.json"))
    assert m2.stop_reason == "end_turn" and "15" in m2.content[0].text
    assert m2.usage.input_tokens > 0 and m2.usage.output_tokens > 0
    assert fn(_fx(f"{prefix}_truncated.json")).stop_reason == "max_tokens", \
        "обрезка лимитом прочитана как законченный ответ"


def test_message_has_the_shape_readers_use():
    m = T.from_gemini(_fx("gemini_turn2.json"))
    assert m.content[0].type == "text" and isinstance(m.content[0].text, str)
    json.dumps(m.model_dump(), ensure_ascii=False)          # сериализуемо, как SDK-объект


def test_recorded_text_answers_of_the_measurement_survive_translation():
    """48 настоящих текстовых ответов OpenAI/Gemini из замера 01.10: перевод даёт тот же
    текст, что замер прочитал родной библиотекой."""
    tar = tarfile.open(ROOT / "plans" / "measure_llm_providers_2026-10-01" / "runs.tar.gz")
    n = 0
    for mem in tar.getmembers():
        if not mem.isfile():
            continue
        rec = json.load(tar.extractfile(mem))
        if rec["provider"] not in ("openai", "gemini") or not rec.get("raw"):
            continue
        msg = (T.from_openai if rec["provider"] == "openai" else T.from_gemini)(rec["raw"])
        assert "".join(b.text for b in msg.content if b.type == "text").strip() == rec["text"].strip(), rec["id"]
        assert msg.stop_reason == "end_turn", rec["id"]
        n += 1
    assert n == 48
