<!-- translation-of: docs/explanation/report_absence_claims.md sha256:bc85e05b5d1f -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](report_absence_claims.md)

# The report does not assert the absence of a test that has a row: why the system watches for words about "absence"

## What changed

- **The wording of `claim_judged_against_canon` has been clarified.** The description of what the judge checks absence claims against has changed — the direction of the change cannot be determined automatically, so it is described neutrally: the wording has been adjusted, but it has not been verified whether it became broader, narrower, or simply shifted emphasis.

**Updated:** 2026-11-07


## Why it exists

When a doctor reads "this test was never ordered" in a report, it sounds like a fact. Not a guess, not a caveat — something that was checked and established. And the decision changes: ordering the test for the first time versus pulling old results are different actions with different consequences.

The problem is that the model composing the report does not see the patient's full history — only a fragment, the slice that was passed to it. If the relevant test did not make it into that slice (for example, it was performed before the chosen time window, or for some reason was not included in the list), the model honestly reports: "this is not in what I was shown" — but in the report text this becomes "this never happened at all." From the outside no error is visible: the report looks coherent, confident, well written. The lie in it is targeted and precise — about exactly this test, for exactly this person.

This subsystem exists so that such a lie does not reach the reader unnoticed.

## What it does, in plain terms

The work is organized in two layers.

**The first layer — an honest description of the boundary.** When the system builds a block of laboratory data to pass to the model, that block declares what it is: what period the data covers, whether there are records outside that period. The model therefore receives not just data, but data together with an explanation of its own limits. This does not guarantee that the model will use that explanation correctly — but at least the information is there.

If for some reason that boundary declaration cannot be assembled, the block is still marked — as incomplete. The system does not stay silent and does not crash: it reports its own uncertainty rather than pretending everything is fine.

**The second layer — checking text that has already been written.** Before the report is sent, a dedicated judge checks its "absence" claims not against the slice the model saw, but against the patient's full laboratory archive. If a row exists in the archive — the test was performed, and writing "was never performed" is not permitted. The correction is made before sending.

The judge distinguishes between two different claims. "This test has never been done at all" is one thing, and it is false if even one row exists in the archive. "This test has not been done in the past year" is another thing, and it is false only if a row exists within that specific period. A row that is older than the stated window does not disprove an honest claim that includes a boundary. The system does not penalize the model for having honestly stated the limits of its data.

When the judge rejects something, it records exactly what and why — both to the log and to a notification. Without that record, any subsequent review of the situation would proceed blind.

## What is honest to say about its limits

The system holds — but "holds" and "verified everywhere" are different claims. Here is where the boundary of what has been proved lies.

The boundary declaration is not verified at every point in the system. Of approximately twenty-one places that pass laboratory data to the model, nine declare the boundary directly, twelve are accepted with a stated rationale — for example, because they are judges, or because they have no text output, or because the logic works differently there. This is a judgment, not an automatic measurement. The machine can recount which functions have no live calls to data at all — but it cannot automatically verify each individual rationale.

There is also a more specific vulnerability: the components that assemble the final reports — the monthly report and the consilium report — are not themselves judged. The individual data collectors they call are judged. If a block is lost somewhere between a collector and the final report, that loss will remain invisible to the verification mechanism.

The laboratory section in the chat is tested with a question that deliberately includes the required route. A question that the router directs differently will not receive a laboratory data section at all — and that is a limit of the router, not of the declaration.

Finally, it is important to understand that the system guards the mechanism, not the execution. It verifies that every tract capable of making an absence claim either has a judge or openly declares the reason it does not. Whether the model lied in a specific case is a separate question, and it remains outside what the system can guarantee automatically.

## Where this lives in the system

The judge logic lives in `gp_context.py` — that is where `judge_absence_claims` resides, which checks the report text against the full data archive. The boundary declaration is assembled by `labs_db` via `declared_boundary` and `canon_window_note`. The subsystem's intentions and invariants are described in `subsystem_intent.yaml`. The tests that verify that corrections are made before sending and that the judge is actually connected live in `tests/unit/test_agent_reply.py`.
