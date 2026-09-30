<!-- translation-of: docs/reference/memory_prompt_channels.md sha256:ffa2900c1f10 -->
**English** · [Русский](memory_prompt_channels.md)

# Reference: memory→prompt channels (staleness)

Invariant: every channel that introduces a memory into a model prompt makes time observable
(a `[ГГГГ-ММ-ДД]` stamp on each item). Otherwise, the model treats old information as today's
(incident: the model presented an old memory as a fact from today).

| Channel | Source | Date from | Mechanism | Test |
|---|---|---|---|---|
| `hai_core.get_history` | conversation_history (transcript) | `created_at` | stamp on the message (M2) | test_history_dating |
| `patient_context.pending_chat` | conversation_history (read-your-writes bridge) | `created_at` | stamp on the line (M5) | test_context_dating |
| `patient_context.recent_notes` | memory_facts (state) | `valid_from` | stamp + 2-day window (Ф2) | test_memory_temporal |
| `patient_context._chat_context_line` | memory_facts (question, 45-day window) | `valid_from` | stamp on the question, “recent” removed (M4) | test_context_dating |
| `hai_chat.build_chat_payload` | assembly of the entire input | — | + a time-discipline line in the system prompt (M2) | test_chat_payload |

## Enforcement
- **Completeness sensor:** `memory_channel_registry` — AST scan of readers of raw
  `conversation_history`, census symmetry → **pre-commit** (block 5). Catches new
  uncovered channels. How-to: `docs/how-to/date_memory_channel.md`.
- **Behavior gate:** `scripts/replay_staleness.py` — controlled A/B replay of the
  05-07→today incident (validated: control 5/5 hallucinations, fix 0/5).
- **Assembly observability:** `hai_chat.assembled_context_text()` — the exact string
  the model sees (the test targets the point of consumption, not a slice).

## Known limits (explicit)
- The sensor is a syntactic proxy for raw transcripts; dating is proved by tests M2–M5 +
  replay M3, not by the sensor. It will miss a new channel in a NON-transcript form.
- Open questions accumulate (45-day window, “never closed”) — they are dated but not retired (debt).
- The write-side loop (the arbiter writes a hallucination as a fact) is outside this fix, thread A1.

Explanation (why it works this way): `дизайн_память_в_отчёт_staleness_2026-07-08.md` (iCloud).
