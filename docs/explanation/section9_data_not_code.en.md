<!-- translation-of: docs/explanation/section9_data_not_code.md sha256:0a6c80eaee30 -->
**English** · [Русский](section9_data_not_code.md)

# Why clinical norms do not belong in code (§9)

*Document type: explanation. Answers “why it works this way,” not “what to do.”*
*The normative wording is in `CLAUDE.md` §9. This page contains the reasoning behind it.*

---

## The point

Health is assessed using thresholds: “readiness below sixty-five is low,” “less than half an hour of deep sleep is too little,” “blood pressure above one hundred forty is hypertension.” The temptation is to put these numbers directly in code, next to the logic that applies them. Rule §9 prohibits this: a number with clinical meaning lives in a versioned store from which code reads it, not in code itself.

## Why a norm cannot live in code

A norm is not constant. It shifts along three independent axes, and code notices none of them.

The first is age. The readiness or heart rate variability range at forty is not the same as at sixty. A number that was once correct quietly becomes outdated as the person ages.

The second is science. Reference ranges, sleep targets, and marker thresholds are reconsidered with each major study. What was a norm five years ago may have been revised today.

The third is condition. After intensive treatment or during ongoing therapy, “normal” for a particular person may not match the population norm at all. A personal norm is a separate quantity.

A hardcoded number follows none of these axes. And it falls behind silently — the worst kind of error in health: the system confidently calculates using the wrong threshold, and the person trusts it. Silent staleness is more dangerous than an explicit failure.

The same argument applies to “tuning” multipliers. A comparison such as “variability is eighteen percent below its own weekly average” is sound logic: it is relative to a personal baseline. But “eighteen percent” itself is a judgment about what deviation counts as significant, and science refines that too. No clinical number escapes drift.

## How to distinguish a norm from a true constant

The only test is drift. A number is data (and belongs outside code) if it changes along at least one axis: age, new research, patient condition, or development of the methodology. If it changes along none, it is a constant and may remain in code.

The presumption is to move it out. The burden of proof lies with whoever wants to leave a number in code: they must show that it falls into one of three closed classes.

The first is arithmetic facts: sixty minutes in an hour, one hundredth in a percent. Mathematics does not drift.

The second is system mechanics: the name of the canonical node, paths, safety limits, retry counts. These are not about health or a person.

The third is structure and representation: sign checks, string slices, protection against division by zero. The skeleton that supports the logic.

A special case is an initial value that immediately yields to the database. It is legitimate only with a provable flow: at startup it is written to a table, and at runtime the code reads from the table rather than the literal itself, and the value can be overridden. An initial value without this flow is disguised hardcoding. A proper example is lab test freshness thresholds: the code seeds defaults, but the runtime path reads from the database, and a doctor visit or a manual edit can override them.

## Why several homes instead of one

The very first audit of live code showed that “one thresholds table” does not hold up. Numbers fall into classes with different owners of the right to change them — and that is the boundary between homes.

Norms for the body (readiness, blood pressure, sleep, variability multipliers) are changed either by the person for their own context or by the literature for the population. Their home is a thresholds table with source precedence.

Methodological scores (the hypothesis evaluation rubric) are changed by the methodology's author, not the literature. These are not about the body at all — they are about the method. They have a separate home.

Analysis configuration (minimum sample size for a verdict, monitoring window shares, time-of-day boundaries) is changed by an engineer. Its home is configuration.

Putting everything into one table means losing the distinction of “who has the right to change this.” Yet that distinction is the very reason for moving it out: the number should be changed by the person whose decision it is, without editing code.

## The connection to longevity

The system is intended to live for years. During that time, the person will age, science will move on, and their condition will change. If norms are embedded in code, each such change requires finding, understanding, and rewriting a literal — which most likely no one will remember. If norms live in a store with history and a source, a change is one table row, with a date and rationale, accessible to the person whose decision it is. Code remains logic; knowledge about the body and the world lives where it can be updated.

## Related

- `CLAUDE.md` §9 — normative wording (what is required and what is prohibited).
- `docs/reference/absolute_thresholds.md` — structure of the table of norms for the body.
- `docs/how-to/add_clinical_threshold.md` — how to add or change a threshold.
