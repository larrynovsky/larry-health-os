<!-- translation-of: docs/explanation/report_absence_claims.md sha256:a0d0e8724f3b -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](report_absence_claims.md)

# The report does not claim the absence of a test that has a row: why the system stays silent instead of lying

## What changed

- **The status of `context_declares_its_boundary` changed from open to holds.** The promise that a data slice declares its own boundaries is now considered met — but only within the limits named below.

- **The run was confirmed on staging (2026-10-05).** Staging is a snapshot of the canon: real code and real data, but not the production database. "Holds" means "holds where it was tested", not "verified on a live system".

**Updated:** 2026-10-05


## Why it exists

Imagine: a doctor reads your report and sees the phrase "this test was never taken". They make a decision — they order it for the first time instead of comparing it against results that already exist. Or you read it yourself and think: a clean history, then; we start from scratch.

The problem is that the phrase may be a lie — quiet, confident, and completely invisible from the outside.

The model that writes the report does not see your full history. It is shown a slice: for example, tests from the past six months, or only those that made it into a particular list. If the test in question is not in that slice, the model draws what seems to it a reasonable conclusion: "it was never taken." It does not know it was looking through a window rather than at the whole room. The report remains coherent, confident, and well-written. The failure is invisible — and that is precisely what makes it dangerous.

This mechanism exists so that such a lie does not get through.

## What it does, in plain terms

Two simple principles work here, and they hold together.

**First:** when a data slice is passed to the system, the slice must describe itself. The block containing the tests reports: "I am showing data for this period; beyond its boundary there are this many rows." The model knows it is looking through a window, not at the full archive. If that declaration cannot be assembled, the block does not stay silent and does not crash: it says "treat me as incomplete." There is no silence.

**Second:** the finished report text is checked not against what was shown to the model, but against the full archive. The judge that checks the report reads the entire history — ten years of it — not the slice that went into the prompt. If the report says "this test was never taken" and the archive contains a row with a result, that is a lie, and it is rejected. The filter between the database and the prompt cannot become the definition of reality, because reality is verified separately.

There is one subtle point about phrasing. The phrase "this test did not appear in the past six months" is honest if there is genuinely no row within that six-month period. The phrase "this test was never taken" is a lie if a row exists somewhere in the archive. The system distinguishes these two cases: a time-bounded claim of absence is verified only within its own window and is not refuted by something old that lies outside it. When a report is honest about its boundaries, that is not a reason to reject it.

When something is rejected, the system does not simply discard the text. It records exactly what failed and why — so that the next review does not proceed blind.

## What is honest to say about its limits

These principles hold — but "holds" and "verified everywhere" are different things. Here is where the boundaries are.

**Report composers are not themselves checked.** The modules that assemble a final report — a monthly summary or a case-conference report, for example — call the data collectors, and it is the collectors that carry responsibility for declaring boundaries. But if a composer drops a data block along the way, that will not reach the verification step. The judge will see the text, not the fact that the composer dropped a piece of context.

**The "accepted with reason" list is a judgment, not an automatic measurement.** Some modules do not declare data boundaries — and that is considered acceptable for recorded reasons: they make no claims of absence, or their output goes directly to a human, or their context is structured differently. But that list was assembled by people, not recalculated by a machine on every change. The only thing recalculated automatically is whether the relevant functions have any live call sites at all.

**The laboratory section does not always appear in chat.** When you ask something in chat, the router decides whether the test block is needed. If the question did not activate the laboratory domain, the section simply will not appear. This is a router boundary, not a declaration boundary: the system will not lie about absence, but it will also not warn you that it never looked in that direction at all.

In short: the mechanism is verifiable, and it holds where it was tested. Where it was not tested, that is stated plainly, not buried in fine print.

## Where this lives in the system

The logic lives in several places that work together.

`gp_context.py` — this is where context is assembled for the general practitioner and for case conferences. This is where the laboratory block receives its declaration of window boundaries: what is shown, how many rows remain beyond the edge, whether there are exceptions.

`subsystem_intent.yaml` — the file where the intent of this subsystem is recorded explicitly: what it promises, which invariants hold, where the boundary of what has been verified lies. This is not after-the-fact documentation — it is what the system checks itself against.

The judge that checks the finished text operates independently of all of this — it reads the full archive and has no knowledge of what the model saw. That independence is what makes the verification real.
