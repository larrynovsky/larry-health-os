<!-- translation-of: docs/explanation/constitutions.md sha256:ae044fcb771f -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](constitutions.md)

# Constitutions: operating rules for your specific organism

## What changed

- **The "Nutrition" constitution sees the nutrition frame (07.10).** The frame is a code decision based on curated rules: energy, protein, restriction codes such as "caution with dumping" and "no lactose". Previously the model wrote about food without seeing the frame, and could recommend a meal schedule that the frame excludes. Now the frame sits in the prompt as a constraint the text does not contradict. This is a request to the model, not a guard: code does not yet catch contradictions with the frame. (Paragraph corrected by hand: on regeneration the model called the change "a wording clarification".)

- **The nutrition frame is a new trigger for reconstruction.** If the frame changes, only "Nutrition" is reconstructed; if something shared changes, all five are reconstructed, as before.

**Updated:** 2026-10-07


## Why it exists

Numbers can describe you, but not explain you. When the system knows your resting heart rate today is 58, that is a fact. But it does not know why yours responds the way it does, and what that means for you personally — not for an average person with similar readings.

Constitutions exist to fill that gap. Not a reference document, not a textbook norm — but a narrative explanation of how you are structured in each domain: sleep, nutrition, stress, nervous system, movement. What is specifically true of you, and why specifically of you.

This is an attempt to move from "what was measured" to "how it is structured".

## What it does, in plain language

For each domain, a powerful language model reads three sources simultaneously: genomic data (SNPs for that domain), your long-term correlations, and your clinical history. From these it produces not a table of metrics but a coherent text — an explanation of how you are structured in that specific domain.

One detail about time matters: a constitution is about structure, not about current state. It includes only things whose consequences have persisted for at least one year. What is measured is not how long an event lasted, but how long its trace continues. A surgery is one day, but its trace in the organism lasts years; that is a constitution. Last autumn's trip, the current protocol, recent lab results — those are not its concern. They live in the monthly report.

Two hard safeguards are built into the generation logic itself.

First: if there is no genomic data for a domain, no constitution is created for that domain at all. An empty genome does not produce confident text. The system simply returns nothing — and that is an honest answer.

Second: if there is no long-term observation history, a text may still be created, but with an explicit flag. That flag prohibits the model from inventing trends from data that does not exist. The same discipline as in the morning report: if you do not know, do not pretend that you do.

A constitution is not reconstructed on a schedule for the sake of a schedule, but only when new inputs have appeared: the genome changed, a new clinical period was added, a new laboratory series long enough has accumulated, or the nutrition frame was updated. Drifting averages or a single new test result are not a reason. Every Sunday morning the system checks: has anything substantial changed? No — it writes only a "checked" timestamp and exits. Yes — it reconstructs all five; with one exception: if only the nutrition frame changed, only "Nutrition" is reconstructed — the other domains do not read it.

## What is honest to say about its limits

It is important to speak plainly here, because two substantial questions are still open, and one check holds but not everywhere.

**The "narrative → threshold" link is not confirmed by outcome.**
A constitution is a powerful model's interpretation, written with discipline against invented trends. But it is not validated by a real outcome. By design it feeds personal alert thresholds — meaning the system plans to rely on it when making decisions. How reliable that chain is in practice is not yet known. This is not a technical disclaimer in fine print — it is an open question that remains open.

**The "reconstruct only on new inputs" logic is adopted as a decision, but not closed as an implementation.**
The fingerprint architecture for inputs is described: what counts as new, what does not, how it is stored, when which domain is reconstructed. But this is a decision from September 2026, and it has not yet gone through long practical testing. The schedule works, the negative control executes — and yet that is not the same as "works reliably under all conditions".

**The output oracle: the promise holds, but not everywhere.**
After a constitution is created, the system checks the text for "freshness" — to see whether anything too transient has leaked through. This promise is fulfilled, and tests have confirmed it. But the check has blind spots: it will not catch a date without a year ("in September" — which one?), does not always recognise relative time expressed in unusual phrasing ("since the end of summer", "recently"), and may misread a number in dd.mm format as a date. The promise holds — but "holds" and "verified everywhere" are different claims.

**One rule that is not a limit but a deliberate choice.**
A constitution does not see your self-descriptions from chat — "I always feel alert in the morning", "stress gives me poor sleep". This is not a technical constraint but an owner decision: subjective input does not go into a constitution.

## Where this sits in the system

Generation is performed by `generate_constitutions.py`. It receives the inputs for each domain, runs the powerful model, checks the output for freshness, and writes the result to the database table — `constitutions_db`. Everything you see in markdown files and in iCloud is a derived copy; the source of truth is always the database.

The intent and rules of the subsystem are described in `subsystem_intent.yaml`. The invariants also live there — the verifiable promises discussed above — along with notes on their status: what holds, what is open, what is not verified everywhere.
