<!-- translation-of: docs/explanation/report_absence_claims.md sha256:e6b409c07396 -->
<!-- intent-provenance: report_absence_claims sha256:9bb034af1230 -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](report_absence_claims.md)

# The Report Must Not Claim the Absence of a Test That Has a Row: Why the System Has No Right to Say "Never Submitted"

## What Changed

- **The claim wording for `window_claim_is_judged_too` has been clarified.** The description of how the system distinguishes unbounded absence claims ("never submitted") from period-bounded claims ("no data for this period") has changed. The direction of the edit was not determined mechanically: whether the wording became more precise, stricter, or softer does not follow from the anchor.

**Updated:** 2026-09-25


## Why It Exists

Imagine: a doctor reads a report and sees the phrase "test was never submitted." This is not just a description — it is a fact that changes the decision. Either refer the patient for a first-time test, or compare against a previous value. Two different paths.

The problem is that such a phrase can arise from nothing — from the system simply not having shown the test to the model. Not because it does not exist, but because it did not end up in the slice of data on which the report was built.

The model does not see the full database. It sees a slice — a time window or a selected set of indicators. If the relevant test fell outside that window or was not included in the list, the model honestly reports: "I do not see it." But from the outside this looks like "it was never there." The report meanwhile remains coherent and confident — no hint that anything was left out of frame. The lie is targeted and invisible.

That is precisely why the system has a dedicated mechanism that prevents a slice from becoming the definition of reality.

## What It Does, in Plain Terms

There are two foundational decisions from which everything else grows.

**First: the judge reads the canon, not the slice.**

Once the model has written a report — the text is verified not against what the model was shown, but against the full database. The judge looks at ten years of history. If the report says "test was never submitted" but the database contains even a single row with that test — that is an error, and the report does not pass. The filter between the database and the prompt is irrelevant to verification.

**Second: the slice honestly describes itself.**

The data block that travels to the model declares its own boundaries: what period the data covers, whether there are rows that fell outside the window. This is done so that the model can say not "test was never submitted" but "no data for this period" — and these are fundamentally different claims.

The difference matters for verification as well. An unbounded claim — "never submitted," "no data" without qualification — is false if even one row exists anywhere in the history. A window-bounded claim — "no data for this period" — is false only if a row exists within that specific period. The system must not penalize the model for a row that is older than the window: if the judge punishes an honest formulation, it pushes the model back toward lying.

When the judge rejects a text, it saves not merely the fact of rejection but the specific clause of the report and the date of the row that caused the problem. Without this, the next review proceeds blind.

## What It Is Honest to Say About Its Limits

It is important here not to create the impression that everything is under control. It is not.

**What holds and has been verified:**

The judge genuinely reads the canon, not the slice — this has been verified through live runs. A rejection carries the clause and the date. An unbounded "never submitted" is caught when a row exists in the database. A bounded "no data in the window" is not caught when a row is older than the window — and correctly is not caught.

**What is not yet complete:**

The commitment that every data block declares its own boundaries exists and works in a number of places — but it **has not been fulfilled everywhere, and this is an open debt of the system, not fine print**.

At the time of the last measurement, out of twenty-one tracts where this matters, nine declare boundaries directly. Twelve were accepted with justification — some of them are judges, some operate without text output, some catch events rather than make claims. This is a judgment, not a machine measurement; only which functions have no live calls is recalculated automatically.

There are also structural limits: composers of summary documents are not themselves judged — the data assemblers they call are judged. If a composer loses a block along the way, the probe will not see it. The laboratory data section in the chat is built on a single question chosen so that the system activates the relevant domain; a question that did not activate the domain simply will not receive that section — this is a limit of the router, not of the declaration.

In short: the mechanism is verifiable and works where it has been applied. It has not been applied everywhere.

## Where This Lives in the System

The judge logic and the definition of what counts as a false absence claim live in `gp_context.py` — the same place where laboratory context for the doctor is assembled and the data block itself is built. The subsystem's intent and its invariants are documented in `subsystem_intent.yaml` — that is the document against which it is checked whether the commitment holds overall.

The relationship between them is straightforward: `gp_context.py` is the implementation, `subsystem_intent.yaml` is what the implementation must conform to. If they diverge, the divergence must be named — as openly as the unfulfilled commitment about boundaries is named above.
