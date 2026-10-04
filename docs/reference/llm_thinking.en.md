<!-- translation-of: docs/reference/llm_thinking.md sha256:beae07665c44 -->
**English** · [Русский](llm_thinking.md)

# Model thinking: the data the system decides by

A model can think before it answers. Thinking spends the same length limit as the answer, so the
system decides about it itself, from three entries in data. The calling code says only two things:
which task this is (`task=`) and how long the answer may be (`max_tokens`).

## Task mode — `methodology/llm_task_modes.json`

| Field | Meaning |
|---|---|
| key | task name: `module.function`; a second call in the same function gets `.2` |
| `mode` | `think` — think; `read` — do not think; `disputed` — like `read` until a measurement on reference data decides |
| `why` | the reason, one line |

Selection rule (owner's decision, 2026-10-04): think where there is inference between input and
answer; do not think where the answer comes from careful reading.

## How a model turns thinking on and off — `methodology/llm_providers.json`, `anthropic.thinking.models`

| Field | Meaning |
|---|---|
| key | model name; a dated snapshot (`claude-haiku-4-5-20251001`) finds its entry by prefix |
| `think` | the parameter that turns thinking on; `"{reserve}"` is replaced by the reserve |
| `no_think` | the parameter that turns it off, or `null` if the model does not think without a parameter |
| `measured` | date and what the measurement showed |

State on 2026-10-04:

| Model | On | Off | Without a parameter |
|---|---|---|---|
| `claude-sonnet-4-6` | `adaptive` | — | does not think |
| `claude-opus-4-7` | `adaptive` | — | does not think |
| `claude-haiku-4-5` | `enabled` + budget | — | does not think |
| `claude-sonnet-5-5` | `adaptive` | `between_tools` | thinks |
| `claude-opus-5` | `adaptive` | `disabled` | thinks |

For a model without an entry the system neither turns thinking on nor off. An admitted model without
an entry is a red test: `test_admitted_model_that_thinks_by_default_must_say_how_to_stop`.

## Thinking reserve — `methodology/llm_admission_table.json`, `reasoning_reserve_tokens`

The number of tokens the wrapper adds to the answer limit when a task thinks. It is written by the
model admission measurement, never set by hand. No number — the task does not think.

## What the wrapper (`llm_client`) does

1. The caller passed `thinking` itself — nothing is changed.
2. The task is `think`, the model has `think`, the reserve is measured — thinking is on, the limit is
   answer + reserve, `temperature` is dropped (the API does not accept it with thinking).
3. Otherwise, if the model has `no_think`, thinking is turned off.
4. The answer stopped at the limit with no text in it — one retry with double the reserve; a second
   such answer stays a loud failure (`NoTextInAnswer`).
5. A thinking call goes as a stream (`stream`): with the reserve the limit is large, and the SDK rejects
   a plain call ("Streaming is required…", measured 2026-10-04). How much went to thinking is written
   to the spend log `logs/agent_api_spend.log` (fields `think_tokens`, `answer_limit`, `attempts`) — the
   reserve is recalculated from working calls.

This applies to the Anthropic provider only. For OpenAI, Gemini and DeepSeek thinking is governed by
their provider profile, as before.
