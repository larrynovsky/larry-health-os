<!-- translation-of: docs/explanation/prediction_core.md sha256:fbcf41d92d6f -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](prediction_core.md)

# A Forecast That Judges Code — a Common Court for Protocols and Hypotheses: Explanation

## What Changed

- **The wording of the `base_by_code_no_retrodiction` claim has been refined:** the text changes how the base calculation is described. The direction of the edit was not determined programmatically — only the fact that the claim wording was adjusted is recorded.

**Updated:** 2026-11-11


## Why It Exists

When a doctor or an algorithm says "this protocol will improve the metric" or "my hypothesis will be confirmed," there is a temptation to verify this after the fact — pick a convenient period, fit the base, count a hit that even a blind guesser could have made. Such verification is worthless.

This subsystem exists so that evaluation is honest — mechanically, without human involvement at the moment of scoring. It answers the question: "Did the forecast actually say something valuable, or did it merely repeat what would have been true anyway — that nothing would change?"

The same court is applied to both protocols and hypotheses. This is fundamental: if the rules differ, there will always be a way to pick the convenient ones.

## What It Does, in Plain Terms

A forecast is three things: which metric will change, by how much (with an allowed spread), and over what time period. The model is not required to say anything more. Everything else is done by the code.

**Step one — the base.** Before the forecast begins to be evaluated, the code itself calculates the starting point: it takes only what was known as of the forecast date. If the metric is a daily one — days up to that date are used; if it is a lab result — the last draw up to and including the forecast date: revision of a hypothesis is usually triggered by a fresh result, and the text is already written with it in hand. No human intervenes in this calculation.

**Step two — waiting.** The system simply waits. Evaluation begins only after the stated horizon has elapsed. Only values strictly after the forecast date are counted; everything known as of that date has already been factored into the base.

**Step three — the court.** When the period has elapsed and values near it exist, the code compares two things: what the forecast predicted — and what the simplest possible forecast, "nothing will change," would have predicted. Falling within the stated interval is not enough — the forecast must be more accurate than inertia. If inertia would have been no worse, the forecast is not counted as fulfilled.

**Scale, not level.** The forecast interval is evaluated relative to how much the metric typically fluctuates on its own — relative to its own historical spread. A high absolute level of the metric does not make a wide interval acceptable: scale is set by variability, not by absolute value.

**Thresholds and percentages.** If a forecast says "will stay above a threshold" or "will decrease by some percentage" — the code translates this into concrete numbers from the base and judges by the same rules. There is an important limit: a forecast of "will stay" when the threshold is already met cannot be confirmed — because inertia predicts the same thing, making it impossible to beat inertia. Such a forecast can still be refuted — if the threshold is violated. Thresholds are accepted only where they are meaningful and stored in the system; they are not used for protocols.

## What Is Honest to Say About Its Limits

All four key rules of the system are currently fulfilled — this has been verified. But "fulfilled" and "verified under all possible conditions" are different claims.

The rules hold for the set of situations the system has encountered so far. This does not mean they will break on new ones — but it does mean that confidence has its limits. How the court behaves with very rare metrics, very short data histories, or non-standard forecast scenarios is an area not yet exhausted by observation.

The system does not make diagnoses and does not replace a doctor. It says: this forecast turned out to be better than inertia — or it did not. What to do with that conclusion is for a human to decide.

## Where This Lives in the System

The evaluation logic lives in `prediction_core.py`. The intentions and boundaries of the subsystem are described in `subsystem_intent.yaml` — it also records why the court was placed in a shared module: so that protocols and hypotheses cannot receive different rules depending on who produced them.

The idea is borrowed from the open-source project Insilico-org/plsi (MIT license) — the idea of separating evaluation from the model, not any specific code. The decision to structure this as a separate subsystem was made by the owner on 10–11 November 2026.
