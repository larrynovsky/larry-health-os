<!-- translation-of: docs/patient_context_single_source.md sha256:875351de05e0 -->
**English** · [Русский](patient_context_single_source.md)

# Unified patient context (patient_context) — single source

Status: in effect since 2026-07-05 (task C). Enforced by the sensor `tests/unit/test_patient_context_contract.py` (hard FAIL).

## How to add a new LLM consumer (how-to)

If you are writing a new module/function that calls `client.messages.create` and **reasons about the patient** (report, consilium, hypothesis assessment, consultation preparation, check-in):

1. Build the prompt as usual.
2. Insert memory from chat with ONE line:
   ```python
   import patient_context as pc
   block = pc.reasoning_block()   # facts from chat + fresh complaints/corrections
   # ... add block to your prompt as a section "NOTES FROM DIALOGUES" ...
   ```
   `reasoning_block()` is safe (catches errors internally, returns `""`) — no `try/except` wrapper is needed (and the silent-except guard will reject one).
3. Run `pytest tests/unit/test_patient_context_contract.py`. If the module performs **extraction/analytics** (does not reason about the patient's condition, e.g., a lab parser), add its `stem` to `ALLOWLIST` in the test **with a reason in a comment**, rather than pulling in context needlessly.

If you do neither, the sensor will fail the entire suite with the name of your module/function. This is by design: drift where “a new consumer forgot memory” becomes structurally impossible.

## Single-source API (reference)

Everything is in `patient_context.py`:

| Function | What it returns | For whom |
|---|---|---|
| `build_patient_brief()` | full profile: identity + diagnosis/treatment + lab results + facts from chat + questions + recommendations | consilium, wellally, lifestyle agents, bot system prompt (through hai_core) |
| `reasoning_block()` | compact: consolidated facts from chat + recent notes/complaints | constitutions, gp weekly/monthly, hypothesis assessment, consult_prep, check-in |
| `recent_notes(days=2)` | only recent states/data-quality corrections | daily report (ring errors, etc.) |
| `chat_facts()` | only consolidated facts (lifestyle) | internal to reasoning_block |

All read the same sources (`memory_facts`, profile). `subject='self'` everywhere (R11 — other people's facts do not enter). Rebuilt on demand, without a cache (one process, small scale → no coherence protocol needed; always fresh).

## Why a single source (explanation)

When each consumer assembles the profile and connects memory independently, some may miss a user's correction. For example, a report of a sensor error should inform both the chat and a later report. A single mandatory source makes this a shared requirement.

The solution comes from software architecture (not the ML frontier): **single source of truth** + **fitness function**. One module assembles the context; a contract test requires every reasoning consumer to go through it. A quote describing our problem: *“if a rule lives only in documentation, it is a candidate for a fitness function; the gap between what is written and what is checked is where things rot.”* We turned “read memory” from a convention into a check.

The sensor uses method 2 (function-level precision) for multifunction modules (`gp_agent`: daily/weekly/monthly — catches “daily reads it, weekly is blind”) + a file-level backstop for new modules.
