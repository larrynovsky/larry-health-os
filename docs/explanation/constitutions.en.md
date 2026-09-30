<!-- translation-of: docs/explanation/constitutions.md sha256:98c4aa1f72e2 -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](constitutions.md)

# Constitutions: operating rules for your specific body

## What changed

- **The "one year or longer" horizon: wording revised.** The claim for the `horizon_year_or_longer` limit has been rewritten. The direction of the change is not machine-determinable — only the fact of the rewording is recorded, not a strengthening or weakening of the guarantee.

- **The self-description-from-chat limit has been reclassified.** Previously this was called a loss (boundary BL-CONST-SELF-1): the constitution cannot see subjective self-description from conversation. It is now an explicit rule (from 27.09.2026): subjective content is kept out of the constitution by decision, not by a technical limit. The "loss" framing has been removed.

- **The former self-description boundary has been removed — but what is proven has become narrower.** The old boundary concerned a classification limitation: `temporal_class` labels (`standing/durable`) alone do not guarantee separation of stable facts from other content. That boundary has been removed — but precisely because the question is closed by a rule, not because the tagging has become more reliable. The amount of proven content has not grown; what changed is the basis for the justification.

**Updated:** 2026-09-27


## Why it exists

Your personal norms are statistics: an average over you, a corridor you yourself deviate from. They say clearly *what* is happening, but say nothing about *why* it works that way for you specifically.

Constitutions exist to fill that gap. Not "step count norm is such-and-such," but a narrative explanation: here is your upper load threshold without a recovery phase — and here is why it is exactly that, because the genome says one thing and several years of your own history confirm it in another way. This is not a diagnosis and not a protocol. It is a description of the mechanism — your specific one.

Five domains: sleep, nutrition, stress, nervous system, movement. One document per domain.

## What it does, in plain terms

A powerful language model receives three sources simultaneously: your genome data, your long-term correlations and clinical periods — and writes a narrative. Not a table, not a list of recommendations — a text that explains you to yourself.

**What counts as long-term.** A constitution describes structure, not state. Only things whose significant consequence holds for at least one year are included. What is measured is the duration of the consequence, not the duration of the event itself: a surgery lasted a day, but its trace may live for years — and then it is included. Last year's trip or a current protocol are not; their home is the monthly report.

**Two safeguards against fabrication.** The system does not allow itself to speak confidently where data is absent.

- No genomic data for a domain — the constitution for that domain is simply not created. An empty genome does not produce confident text.
- No long-term history — the model generates based on the genome only, but with an explicit note: there are no confirmed trends here, do not invent them.

**When it is rebuilt.** Every Sunday the system checks whether anything has changed in the source data — genome, clinical history, lab series, phases. If nothing is new — no rebuild occurs, only the fact that the check took place is recorded. If the inputs are the same, the text stays the same: a constitution is not updated "just because a week has passed."

**The database is the source of truth.** Files in folders and in iCloud are mirrors. The real constitution lives in the database table; everything else is derived from it.

## What is honest to say about its limits

It is important not to gloss over this.

**The narrative is not verified by outcome — and this is an open question.** A constitution is an interpretation by a capable model, written with discipline against fabricated trends. But it has not been verified by what subsequently happened to you. By design it feeds personal alert thresholds — meaning decisions depend on it. How reliable this chain of "narrative → threshold" actually is has not yet been confirmed. This is not fine print — this is honest incompleteness.

**The rebuild-on-trigger logic is also an open question.** The decision to "rebuild only when inputs change" has been made and implemented; the system tracks a fingerprint of the input data and checks it every week. But the logic itself — what exactly counts as a "new input" sufficient to trigger a rebuild — is still being refined. Drift in averages and a new one-off test result have been explicitly excluded from inputs since September 2026: they do not trigger a rebuild. The boundary between "this is new" and "this is not new" is a live decision, not one that is closed.

**The horizon holds, but has not been verified everywhere.** The promise "only data at or beyond the horizon goes into the prompt" is fulfilled — this is an invariant that holds. But it has blind spots, and they need to be named directly, not in a footnote.

The system looks at the model's output and searches for signs that it slipped into a short period anyway: specific dates, relative phrases like "recently" or "over the last few days." But it looks at form — and that has limits. A month without a year ("in September"), a vague "since late summer," a date of the form day.month without a year — all of these are either outside the check's field of view or may produce a false alarm. This is not a catastrophe, but it is a real limit, not fine print.

Separately: the constitution cannot see subjective self-description from chat. This is not a loss and not a technical limit — it is a deliberate decision: subjective content does not go into the constitution. The sources are the genome, measured history, and clinical data.

## Where this lives in the system

The generation logic lives in `generate_constitutions.py`. The intent and domain boundaries are described in `subsystem_intent.yaml`. Completed constitutions are stored in the `constitutions_db` table — that is their sole authoritative source; files in `constitutions/` and in iCloud are synchronised from it as mirrors.
