<!-- translation-of: docs/explanation/consilium.md sha256:71e0273d3b9d -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](consilium.md)

# Consilium: dispute, not a vote — why the system pits opinions against each other instead of averaging them

## Why it exists

Imagine asking ten people for advice, but all of them have read the same book, are sitting in the same room, and can hear what their neighbor says before they open their mouth. What you will most likely get is a confident, coherent — and completely identical — answer. That is not the wisdom of the crowd; that is an echo.

The same trap exists with medical AI models, only worse: a single model left to itself tilts in one direction simply because of how it was trained. And if you run several copies in parallel and average their answers, you get a "consensus of mediocrity": everyone made the same mistake, but the result looks like unanimous expert agreement. It looks convincing. That is precisely why it is dangerous.

The consilium in this system is built on a different idea: the value lies in the cardiologist's objection to the endocrinologist, not in their nodding at each other. Disagreement is more productive than agreement — if it is genuine.

## What it does, in plain terms

The consilium works in two rounds, and the difference between them is fundamental.

In the **first round**, each specialist responds blind — without knowing what the others have said. This matters: genuine, independent positions are formed here. If the cardiologist and the endocrinologist cannot hear each other, they cannot pre-adjust to fit someone else's opinion or stay silent out of politeness.

In the **second round**, each specialist sees what their colleagues answered — and can disagree. Not merely rephrase, but say: "No, I see this differently, and here is why." After that, the coordinator assembles the full picture, and the arbiter helps reduce it to a conclusion.

The consilium roster is drawn from a single shared list of specialists — from which only those clearly unsuitable given the patient's sex or age are removed. This is not a random detail: the system previously had two separate lists that diverged from each other and produced confusion — the same consilium could look different in different parts of the system. Now there is one source, and all parts of the system look at it.

By the same logic, the number of participants is not hard-coded as a fixed number — it is always computed from the actual roster. This sounds like a technical triviality, but it is exactly this triviality that once caused the logs to show one participant count while a different number was actually working. When a counter and a list live separately, they will eventually diverge.

## What is honest to say about its limits

All three key properties of this subsystem — a single roster source, genuine two-round dispute, a counter derived from the actual list — are satisfied as of today. This is not a promise for the future and not a goal: it is what has been verified and holds.

What is important to understand alongside this: the consilium does not guarantee a correct answer. It guarantees something else — that the answer will not be the result of silent agreement or the accidental bias of a single model. A dispute may end in deadlock. Specialists may disagree, and the coordinator synthesizes a position under conditions of real uncertainty. That is more honest than artificial unanimity — but it is not the same thing as truth.

The system also does not claim to replace a human physician. The consilium is a structure for thinking alongside you, not instead of a doctor.

## Where this lives in the system

The participant selection logic and the single shared specialist list live in **`consilium_roster.py`** — it is the only place from which all parts of the system draw the consilium roster.

The overall intent of the subsystem — why the dispute is needed, how the rounds relate to each other, what the role of the coordinator and arbiter is — is described in **`CLAUDE.md`**.

The verifiable commitments the system makes to itself (what are called invariants: single source, genuine dispute, counter from the actual roster) are recorded in **`subsystem_intent.yaml`**. This is not documentation for a human reader — these are machine-readable assertions that the system can verify against itself.
