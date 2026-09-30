<!-- translation-of: docs/explanation/intent_receipts.md sha256:bd6d4733b380 -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](intent_receipts.md)

# Intent Receipt: the Closure Artifact Carries a Live Registry Read

## What Changed

- **Subsystem intent clarified.** The purpose statement was reworked: the emphasis shifted from "visibility of the read event" to the question "how does closing a task prove that the registry was read live." The semantic core is the same; the clarification is in how the question is framed.
- **Receipt format: the normative document is now separate.** The old version pointed to the letitbe skill as the home of the format. The new version establishes that the contract lives in `docs/reference/intent_receipt_format.md`; skills reference it but do not themselves define the norm.
- **A legacy precedent was added.** The new version explicitly records: modified legacy files and the INDEX/LATEST service entries are not checked by the gate — this is a deliberate decision, recorded as a precedent. The old version had no such limit.
- **The status-comparison claim was clarified** (direction is not machine-determined): the wording was changed, neutrally.

**Updated:** 2026-08-07


## Why It Exists

The system contains an intent registry — records of what each subsystem promises and what it depends on. But reading that registry was a matter of good will: one could travel the full path from idea to task closure without consulting it once. The plan is written, the handoff is filed, the commit is gone — and no one knows whether the author read the current promises or acted from memory, from a stale copy, from a guess.

This is a quiet problem. It does not shout and does not break the build. It simply allows the intent and its execution to drift apart unnoticed.

The intent receipt is the answer to the question: how do you make closing a task prove that the registry was read live, rather than left to one side?

## What It Does, in Plain Terms

When an author or agent commits a thread plan or a handoff snapshot, the system meets them at the threshold — before the commit is accepted.

That threshold is a gate, built into pre-commit. It looks for one thing: does the artifact contain an "Intent" section with a read receipt? The receipt is not a free-form retelling but a specific record: which registry entries are affected, what their invariant statuses are, and when that was captured.

But the important thing is not the form — it is the moment of verification. At commit time, the gate compares the statuses in the receipt against the live registry — what is currently in the index. If the statuses diverge, the commit does not pass, and the author sees both versions: the one in the receipt and the one in the registry right now. This is the essence of the mechanism: the receipt cannot be written without looking at the registry, and cannot be delivered stale — it is checked again at commit time.

There is an important nuance for external review and handoff cases. When an artifact goes to an external tester, the receipt carries only the identifiers of the affected entries — without statuses. This is a deliberate decision: the author's judgments about statuses must not leak to someone who is verifying independently.

One further trait: if something inside the gate breaks, it does not pass silently — it blocks. A silent pass on error would make every future defect silent. This is a lesson learned from prior experience.

## What Is Honest to Say About Its Limits

The mechanism holds — but "holds" and "verified everywhere" are different claims.

**Thread start is outside the gate's view.** The gate sees the moment when a plan or handoff goes into the repository. A letitbe plan that exists before the first commit is invisible to the gate by design. This is not a gap that can be closed with configuration — it is a boundary of the architecture: there is no commit yet, so there is nothing to guard.

**The nightly health check is not a full test.** There is a probe that runs every night to confirm the gate is alive. But it proves exactly that — "the gate is alive" — not that every individual rule inside it is alive. The completeness of the rules is the responsibility of separate unit tests; those are what catch a rule that has stopped working.

**The gate proves a read, not understanding.** The mechanism confirms that the receipt was assembled from the live registry at commit time. It cannot confirm that the person or agent understood what was written, and it cannot judge whether the author's own assessment of compliance is correct. That last part is always a human judgment.

**A falsely valid receipt is possible.** Statuses can be copied from the registry mechanically, without reading the rest. The gate will not see this. Honest work is structured so that this is cheaper than circumventing the gate — but the mechanism has no proof of understanding.

**Legacy is not touched.** Modified legacy files and the INDEX/LATEST service entries are not judged by the gate — this is a deliberate decision, recorded as a precedent: legacy is not painted over.

## Where This Lives in the System

The live gate code is `project_context/intentgate.py`. That is where the pre-commit hook meets the commit, reads the index as a single snapshot, and compares the receipt against the registry.

The normative description of the receipt format lives in `docs/reference/intent_receipt_format.md` — that is the home of the contract; the contract test runs its examples with a live judge. The system's skills reference that document but do not themselves define the norm.

The history of decisions, disputes, and repairs is collected in the thread plan (in the closed part of the project) — there one can trace why the mechanism is structured the way it is and not otherwise.

The subsystem's place in the whole is `subsystem_intent.yaml`, the intent registry whose reading the receipt attests to.
