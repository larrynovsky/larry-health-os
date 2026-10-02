#!/usr/bin/env python3.11
"""llm_translate.py — перевод запроса/ответа между форматом Anthropic Messages и родными
API OpenAI (Responses) и Google Gemini. Чистые функции, без сети (сеть — в llm_client).

ЗАЧЕМ. Решение владельца 2026-10-01 «вариант а»: 75 мест вызова модели говорят на формате
Anthropic и не переписываются; перевод живёт в ОДНОМ месте, и допуск модели к роли
(llm_admission) идёт ЧЕРЕЗ этот же перевод — искажение шва проваливает допуск, а не
проходит молча. Поиск 01.10 (plans/research_llm_multiprovider_github_2026-10-01.md) назвал
швы, на которых ломаются чужие переводчики; у каждого здесь свой контракт-тест:
  * связка tool_use ↔ tool_result (LiteLLM #12404, #19061) — id вызова сохраняется в обе
    стороны; у Gemini результат функции адресуется ИМЕНЕМ — имя берётся из истории;
  * подпись рассуждения Gemini (openai/codex #7519) — сырая часть ответа едет в блоке
    (`provider_raw`) и возвращается в историю как есть;
  * system (LiteLLM #18538) — отдельным полем инструкции, не сообщением;
  * причина остановки (LiteLLM #21348, MLflow #26272) — «обрезано лимитом» ≠ «закончил»,
    вызов инструмента — tool_use; сырое значение провайдера сохраняется рядом;
  * картинки — base64 с media_type как есть.

Чего НЕ делает: не выбирает модель, не ретраит, не сканирует секреты (гард — в llm_client,
до транспорта), не знает про роли.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field


# ── форма ответа, которую читают 39 файлов (подмножество Anthropic Message) ──
@dataclass
class TextBlock:
    text: str
    type: str = "text"

    def model_dump(self, **kw) -> dict:
        return {"type": "text", "text": self.text}


@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: dict
    provider_raw: dict | None = None
    type: str = "tool_use"

    def model_dump(self, **kw) -> dict:
        d = {"type": "tool_use", "id": self.id, "name": self.name, "input": self.input}
        if self.provider_raw is not None:
            d["provider_raw"] = self.provider_raw
        return d


@dataclass
class Usage:
    input_tokens: int
    output_tokens: int

    def model_dump(self, **kw) -> dict:
        return {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens}


@dataclass
class Message:
    content: list
    stop_reason: str
    usage: Usage
    model: str
    provider: str
    native_stop_reason: str | None = None
    id: str = ""
    role: str = "assistant"
    type: str = "message"
    extra: dict = field(default_factory=dict)

    def model_dump(self, **kw) -> dict:
        return {"id": self.id, "type": "message", "role": "assistant", "model": self.model,
                "content": [b.model_dump() for b in self.content], "stop_reason": self.stop_reason,
                "native_stop_reason": self.native_stop_reason, "provider": self.provider,
                "usage": self.usage.model_dump()}


def _as_dict(b):
    return b.model_dump() if hasattr(b, "model_dump") else b


def _blocks(content) -> list[dict]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return [_as_dict(b) for b in (content or [])]


def _system_text(system) -> str | None:
    if system is None:
        return None
    if isinstance(system, str):
        return system
    return "\n\n".join(b.get("text", "") for b in system if isinstance(b, dict))


def _tool_result_text(c) -> str:
    if isinstance(c, str):
        return c
    return "\n".join(b.get("text", "") for b in _blocks(c) if b.get("type") == "text")


class UnsupportedFeature(ValueError):
    """Возможность запроса, которую перевод не умеет, — громкий отказ, не тихий пропуск."""


# ── OpenAI Responses ─────────────────────────────────────────────────────────
def to_openai(kw: dict, profile: dict) -> dict:
    """Аргументы messages.create (формат Anthropic) → аргументы responses.create."""
    items = []
    for m in kw.get("messages") or []:
        role = m["role"]
        parts = []
        for b in _blocks(m.get("content")):
            t = b.get("type")
            if t == "text":
                parts.append({"type": "input_text" if role == "user" else "output_text", "text": b["text"]})
            elif t == "image":
                src = b["source"]
                if src.get("type") != "base64":
                    raise UnsupportedFeature(f"картинка не base64: {src.get('type')}")
                parts.append({"type": "input_image",
                              "image_url": f"data:{src['media_type']};base64,{src['data']}"})
            elif t == "tool_use":
                if parts:
                    items.append({"role": role, "content": parts})
                    parts = []
                items.append({"type": "function_call", "call_id": b["id"], "name": b["name"],
                              "arguments": json.dumps(b.get("input") or {}, ensure_ascii=False)})
            elif t == "tool_result":
                if parts:
                    items.append({"role": role, "content": parts})
                    parts = []
                items.append({"type": "function_call_output", "call_id": b["tool_use_id"],
                              "output": _tool_result_text(b.get("content"))})
            else:
                raise UnsupportedFeature(f"блок {t!r} не переводится в OpenAI")
        if parts:
            items.append({"role": role, "content": parts})
    req = {"model": kw["model"], "input": items,
           "max_output_tokens": int(kw["max_tokens"]) + int(profile.get("reasoning_reserve_tokens", 0))}
    sys_text = _system_text(kw.get("system"))
    if sys_text:
        req["instructions"] = sys_text
    if kw.get("tools"):
        req["tools"] = [{"type": "function", "name": t["name"], "description": t.get("description", ""),
                         "parameters": t.get("input_schema") or {"type": "object", "properties": {}}}
                        for t in kw["tools"]]
    if kw.get("temperature") is not None and profile.get("temperature") == "pass":
        req["temperature"] = kw["temperature"]
    return req


def from_openai(r) -> Message:
    d = r.model_dump() if hasattr(r, "model_dump") else r
    content = []
    for it in d.get("output") or []:
        if it.get("type") == "message":
            for c in it.get("content") or []:
                if c.get("type") == "output_text":
                    content.append(TextBlock(c.get("text", "")))
                elif c.get("type") == "refusal":
                    content.append(TextBlock(c.get("refusal", "")))
        elif it.get("type") == "function_call":
            try:
                args = json.loads(it.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {"_unparsed_arguments": it.get("arguments")}
            content.append(ToolUseBlock(id=it.get("call_id") or it.get("id"), name=it["name"], input=args))
    status = d.get("status")
    inc = ((d.get("incomplete_details") or {}).get("reason")) if d.get("incomplete_details") else None
    if any(isinstance(b, ToolUseBlock) for b in content):
        stop = "tool_use"
    elif status == "incomplete" and inc == "max_output_tokens":
        stop = "max_tokens"
    elif status == "incomplete" and inc == "content_filter":
        stop = "refusal"
    elif status == "completed":
        stop = "end_turn"
    else:
        stop = "error"
    u = d.get("usage") or {}
    return Message(content=content, stop_reason=stop, native_stop_reason=f"{status}:{inc}" if inc else status,
                   usage=Usage(int(u.get("input_tokens") or 0), int(u.get("output_tokens") or 0)),
                   model=d.get("model") or "", provider="openai", id=d.get("id") or "")


# ── Google Gemini ────────────────────────────────────────────────────────────
def to_gemini(kw: dict, profile: dict) -> dict:
    """→ {'model', 'contents', 'config'} в виде dict (google.genai принимает dict-формы)."""
    contents, names = [], {}
    for m in kw.get("messages") or []:
        role = "user" if m["role"] == "user" else "model"
        parts = []
        for b in _blocks(m.get("content")):
            t = b.get("type")
            if t == "text":
                parts.append({"text": b["text"]})
            elif t == "image":
                src = b["source"]
                if src.get("type") != "base64":
                    raise UnsupportedFeature(f"картинка не base64: {src.get('type')}")
                parts.append({"inline_data": {"mime_type": src["media_type"], "data": src["data"]}})
            elif t == "tool_use":
                names[b["id"]] = b["name"]
                parts.append(b["provider_raw"] if b.get("provider_raw") else
                             {"function_call": {"id": b["id"], "name": b["name"], "args": b.get("input") or {}}})
            elif t == "tool_result":
                name = names.get(b["tool_use_id"])
                if not name:
                    raise UnsupportedFeature(f"tool_result {b['tool_use_id']!r} без tool_use в истории — "
                                             "Gemini адресует результат по имени функции")
                parts.append({"function_response": {"id": b["tool_use_id"], "name": name,
                                                    "response": {"result": _tool_result_text(b.get("content"))}}})
            else:
                raise UnsupportedFeature(f"блок {t!r} не переводится в Gemini")
        contents.append({"role": role, "parts": parts})
    cfg = {"max_output_tokens": int(kw["max_tokens"]) + int(profile.get("reasoning_reserve_tokens", 0)),
           "automatic_function_calling": {"disable": True}}
    sys_text = _system_text(kw.get("system"))
    if sys_text:
        cfg["system_instruction"] = sys_text
    if kw.get("temperature") is not None and profile.get("temperature") == "pass":
        cfg["temperature"] = kw["temperature"]
    if kw.get("tools"):
        cfg["tools"] = [{"function_declarations": [
            {"name": t["name"], "description": t.get("description", ""),
             "parameters_json_schema": t.get("input_schema") or {"type": "object", "properties": {}}}
            for t in kw["tools"]]}]
    return {"model": kw["model"], "contents": contents, "config": cfg}


_GEMINI_STOP = {"STOP": "end_turn", "MAX_TOKENS": "max_tokens", "SAFETY": "refusal",
                "RECITATION": "refusal", "PROHIBITED_CONTENT": "refusal", "BLOCKLIST": "refusal",
                "SPII": "refusal"}


def from_gemini(r) -> Message:
    d = r.model_dump(mode="json", exclude_none=True) if hasattr(r, "model_dump") else r
    cands = d.get("candidates") or []
    cand = cands[0] if cands else {}
    content, n = [], 0
    for p in (cand.get("content") or {}).get("parts") or []:
        if p.get("thought"):
            continue
        if p.get("function_call"):
            fc = p["function_call"]
            n += 1
            content.append(ToolUseBlock(id=fc.get("id") or f"toolu_gemini_{n}", name=fc["name"],
                                        input=dict(fc.get("args") or {}), provider_raw=p))
        elif p.get("text") is not None:
            content.append(TextBlock(p["text"]))
    fr = str(cand.get("finish_reason") or "").split(".")[-1]
    if any(isinstance(b, ToolUseBlock) for b in content):
        stop = "tool_use"
    elif fr:
        stop = _GEMINI_STOP.get(fr, "error")
    else:
        stop = "refusal" if (d.get("prompt_feedback") or {}).get("block_reason") else "error"
    um = d.get("usage_metadata") or {}
    out = int(um.get("candidates_token_count") or 0) + int(um.get("thoughts_token_count") or 0)
    return Message(content=content, stop_reason=stop, native_stop_reason=fr or None,
                   usage=Usage(int(um.get("prompt_token_count") or 0), out),
                   model=d.get("model_version") or "", provider="gemini", id=d.get("response_id") or "")
