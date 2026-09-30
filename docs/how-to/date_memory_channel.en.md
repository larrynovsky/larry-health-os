<!-- translation-of: docs/how-to/date_memory_channel.md sha256:e634d224253d -->
**English** · [Русский](date_memory_channel.md)

# How-to: date a memory→prompt channel (M6 flagged you)

The `memory_channel_registry` sensor found a function that reads raw `conversation_history` into a
model prompt without a date. Every such channel must make time observable.
Invented nonmedical example: an earlier message says “the workshop is closed.”
Without its date, a model may incorrectly present that statement as today's status.

## Why
A raw transcript without timestamps does not distinguish an earlier message from
one written today. Channel definitions: `docs/reference/memory_prompt_channels.md`.

## Steps

1. Add `created_at` to SELECT:
   ```python
   "SELECT role, content, created_at FROM conversation_history ..."
   ```
2. Stamp each line with a date:
   ```python
   day = (r["created_at"] or "")[:10]
   content = f"[{day}] {content}" if day else content
   ```
   Reference implementations: `hai_core.get_history` (M2), `patient_context.pending_chat` (M5).
3. Register the verdict in `memory_channel_registry.REGISTERED`:
   ```python
   "module.function": {"verdict": "dated", "note": "stamp from created_at"},
   ```
   verdict: `dated` (reads into a prompt, dates it) / `writer` (writes the transcript) / `infra` (DDL).
4. If the channel builds a prompt from `memory_facts` (not the transcript), date it using `valid_from`
   (reference: `patient_context._chat_context_line`, M4), and remove false “fresh” labels.

## Verify
```bash
python3.11 memory_channel_registry.py                     # OK or findings
python3.11 -m pytest tests/unit/test_memory_channel_registry.py -q
```

## Behavior gate (beyond the sensor)
The sensor is a syntactic proxy. A replay gate checks that the model ACTUALLY stops carrying old information over to today:
```bash
PYTHONPATH=~/health_scripts python3.11 scripts/replay_staleness.py
```
The control (without dates) must reproduce the hallucination; the fix must eliminate it.
