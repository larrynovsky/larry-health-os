<!-- translation-of: docs/explanation/old_hypothesis_pipeline.md sha256:18d25ad96a72 -->
**English** · [Русский](old_hypothesis_pipeline.md)

# The old hypothesis cycle: experiments, protocols, attribution

*Document type: explanation. Why read this: if you see nearly empty `protocols`/`experiments` tables, a disabled attribution report, or an xfail test and cannot tell whether this is a bug or intentional.*

---

## Two eras

Health hypotheses once came from people's heads, not data. A pipeline was built for them: a hypothesis produced an **experiment** (a validation plan), a confirmed hypothesis produced a **protocol** (prescribed behavior), and completing an experiment generated an **attribution report** (what worked). The chain: hypothesis → experiment → protocol → attribution.

The methodology later changed. Hypotheses now arise from data: the monthly consilium evaluates them and writes a verdict to `hypothesis_outcomes`. The old pipeline was not removed. The architectural review distinguishes a connected rare trigger, a deliberately retired path, and a software error.

## Protocols — connected but dormant (BL-PROTO-1)

Protocols are not disconnected: the weekly consilium job, the bot's `/confirm` command, and an inline button all call `resolve_hypothesis`. It creates a protocol only on a `confirmed` verdict; `partial` is insufficient. The frequency of new records alone does not prove that the path is disabled.

Decision: keep it. A rare trigger does not mean the creation path is dead. Details: `BACKLOG.md` BL-PROTO-1.

## Experiments — deliberately wound down (BL-EXP-1)

Experiments were among the earliest features, built to test hypotheses formulated in advance. They were deliberately wound down. The creation path is technically connected: lab import can link an experiment to a hypothesis in testing status. Full retirement in a single workstream requires a separate methodological decision and a review of callers. Details: `BACKLOG.md` BL-EXP-1.

## Attribution report — dead because of a bug (BL-GP-1)

`generate_attribution_report` references `db`, but `health_db` is not imported in the function — every call fails with `NameError` on the first line. It is called from the experiment check inside a `try/except` that only logs, so attribution reports **were never generated** — silently. Since experiments have been wound down, reviving part of a dead pipeline makes no sense: the bug is documented and pinned by an `xfail` test (which will itself demand removal of the mark if someone decides to fix it). Details: `BACKLOG.md` BL-GP-1.

## How to read this today

The contents of `protocols`/`experiments` alone cannot establish whether the old pipeline works. A rare protocol trigger, deliberate retirement of experiments, and an attribution bug are different causes. Retiring these parts requires a separate methodological effort and a safety gate: establish that the creation path is no longer needed before deleting it, or a live feature may be removed.

## Related

- `BACKLOG.md` — BL-PROTO-1, BL-EXP-1, BL-GP-1 (finding details).
- `CLAUDE.md` — how the hypothesis and consilium subsystem works.
