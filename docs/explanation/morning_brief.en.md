<!-- translation-of: docs/explanation/morning_brief.md sha256:2e92916f653c -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](morning_brief.md)

# Morning Brief — Anti-Repeat: Why You See Exactly This Every Morning

## What Changed

- **`env_seasonal_not_absolute`** — the claim wording has been refined. The nature of the refinement was not determined by machine; see the current version in `subsystem_intent.yaml`.

**Updated:** 2026-09-30


## Why It Exists

Imagine someone reads you the same thing every morning: "you have chronic gastritis." At first you listen. Then you stop. Then you skip the brief entirely — and one day you miss something important.

That is how a system without anti-repeat works: it is honest, but useless. A piece of news repeated every day turns into wallpaper.

Anti-repeat solves the opposite problem: making every appearance of anything in the brief a reason to pay attention. Not because the system is hiding information, but because it distinguishes between what has changed and what simply exists.

## What It Does, in Plain Terms

**The brief shows changes relative to your personal normal band.** Not absolute numbers, not a comparison with "the average person" — but what differs from how things usually are for you.

**What is unchanged does not shout every day.** If something chronic is a constant presence in your life, it appears in the brief roughly once a month — in full, with an explanation, so you can re-read it and recall the context. Not as a quiet line slipped in between other things, but as a complete card.

**Warmth and precision are different things, and the system does not confuse them.** The brief can be written gently and in a human voice — that is one dimension. But how important something is for safety is another, and it does not depend on writing style. A safety signal cannot "get lost" simply because it is not new or seems obvious: such signals have a dedicated channel that does not compete with ordinary daily news.

**Every omission has a reason.** If a card did not appear — it means either nothing changed, or it appeared recently, or there is no data and the system prefers silence over fabrication. The system does not fake freshness: if a data source is unavailable, the card simply does not appear.

**Context accumulates rather than multiplies.** Genetic traits, environmental data, seasonality, travel — all of this influences what you see, but each element lives in its own place. Genomic context, for example, does not pop up as a separate card every day — it quietly refines other findings when that is appropriate.

**Where you are matters.** If you are at home — the system compares weather and environment against what is normal for your location at this time of year. If you are traveling — the rules change, because the system has no local baseline.

**Food and activity are not universal advice.** The nutritional profile is assembled for you: taking into account your medical record, genetics, and season. The medical record takes priority over genetics — this is not a compromise, it is a structural decision. The list of walking trails rotates so that the same route does not grow tiresome.

**Text is produced in one place.** Previously, the morning text could be assembled from several sources — this created confusion and duplicates. Now the brief text is formed in exactly one place, and the content of cards is determined by code before the language model is involved. The model only puts the decision into words — it does not decide what to show.

## What Is Honest to Say About Its limits

The system rests on verified rules — but "rests on" and "verified under every conceivable condition" are different claims.

**All stated invariants are currently in status holds** — meaning they have been checked and are satisfied. But that does not mean they have been verified under every imaginable condition. Here is where reasonable caution is warranted:

- Validation on the partner's data was conducted by running directly against their database — but that is one tenant, one profile. The variety of medical-data and genome combinations encountered in real life is broader.

- Seasonal norms for the home region work when the system has history for that region. While traveling, the system switches to an absolute threshold — because there is no local baseline. This is an honest decision, but it means that in an unfamiliar location, sensitivity to an "anomalous" environment is different.

- Location is taken from the phone. If the signal was lost and reappeared — the system uses the last known point rather than going silent. This is reasonable, but the last known point may be stale.

- The nutritional profile is recalculated at every brief using current data. If the genome or medical record has changed — the profile will update. But between recalculations the quarterly document in Telegram remains as it was.

- Leakage of suppressed genetic data into the brief text is monitored and raises a warning. The mechanism works — but it catches what it knows how to look for. New phrasings or unexpected contexts may require updating the search rules.

None of these limits makes the system unreliable. But honesty requires naming them aloud.

## Where This Lives in the System

The anti-repeat logic lives in **`brief_pipeline.py`** — this is the main orchestrator of the morning brief. This is where the decision is made about what to show today and what to defer.

The intentions and principles of the subsystem are described in **`subsystem_intent.yaml`** — it records why the system is structured the way it is and not otherwise. This is not a technical config but a document of intent: what the system promises and what it consciously refrains from.

