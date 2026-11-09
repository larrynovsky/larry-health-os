<!-- translation-of: docs/explanation/morning_brief.md sha256:2902e76ff930 -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](morning_brief.md)

# Morning Brief — Anti-Repeat: Why the Brief Does Not Turn into White Noise

## What Changed

- **Invariant `food_kb_not_assessed` added (status=holds):** it is recorded that the clinical basis for personal food recommendations may not be established; in this case the system explicitly reports the absence of recommendations rather than substituting an approximation. This is a limit, now explicitly accounted for, not a finished feature.

**Updated:** 2026-10-28

**Updated:** 2026-11-08


## Why It Exists

Imagine the same letter arriving every morning. At first you read it. Then you start skimming it. Then you stop opening it altogether — and on exactly the day something important was in there, you miss it.

The morning brief lives and dies by attention. If it repeats the same thing every day, it stops being a brief and becomes background noise. The anti-repeat subsystem exists precisely to prevent that: it ensures only what has actually changed, or what you have not heard about in long enough, makes it into the brief.

At the same time the system has one hard condition: "do not repeat" must never suppress something genuinely important for safety. These are different tasks — and they are solved independently of each other.

## What It Does, in Plain Terms

**Shows changes, not state.** The brief is built around what has shifted relative to your personal band — not relative to some abstract norm, but specifically yours. What has not changed does not become news.

**Chronic items are not daily news.** If something is a permanent part of your picture — a chronic finding, a long-term context — the system will not remind you of it every day. Such a card appears no more than once a month, in full and with an explanation: not a fragment, but complete meaning. This is a deliberate decision — not suppression, but respect for your attention.

**Recency and importance are not the same thing.** Every suppression has a reason. The system does not collapse "how much of a novelty this is" and "how important this is for safety" into a single combined score — otherwise a signal with low novelty but high importance could simply disappear. These two considerations live on separate tracks and are not multiplied together.

**Genomic context is not a separate daily topic.** Genetic context — for example, the influence of a gene variant on sleep quality — is embedded inside the card it belongs to, rather than appearing as a standalone line every morning. One cooldown, one occasion.

**Environment — by deviation, not by absolute value.** Typical summer heat for your location is not news. News is an anomaly relative to what normally occurs here at this time of year. The system knows where you are right now, not where your registered address is. If you are travelling, the logic is slightly different, because the system does not have a seasonal baseline for an unfamiliar place, and this is honestly accounted for. If the environmental data source is unavailable, there will be no card at all: silence is preferable to fabrication.

**The decision is made by code; the text is written by the model.** What enters the brief is determined deterministically — before the language model is involved at all. The model only renders the selected content into live prose. This means filtering does not depend on the mood of the wording.

**The brief is not lost on a repeated run.** If the system launches twice for some reason, you receive exactly one brief, not two. Cooldowns and counters advance only after delivery is confirmed, not at the moment the text was generated.

**Nutrition — personal and safe.** The food profile is built separately for each person: your genome and medical record do not influence another family member's recommendations. At the same time the medical picture always takes precedence over genetic preferences — where the health condition requires it, genetic constraints yield.

**Trips — in advance, not in the stream.** If your calendar contains an upcoming trip, the system will mention it one day ahead — once, in a dedicated slot. The entire calendar is not poured onto the screen.

## What to Honestly Say About Its Limits

**Not built: delivery reliability under network failures.** This must be stated plainly: resilience to temporary connectivity failures with Telegram remains an open question — the invariant is recorded as unresolved. The intent is clear: if the connection dropped or flood control triggered before a delivery confirmation arrived, the brief should retry with an increasing interval, and after exhausting retries should write the failure to the log that the nightly repair process reads. Failures of a different kind — invalid markup, invalid token, invalid chat — are not fixed by retrying and should not retry, otherwise the system will simply hide a permanent breakage. Every attempt outcome is recorded in the tenant settings. But at the time of writing this, the mechanism in this form is not yet fully implemented. This is an honest limit, not a finished feature.

**Silence when data is unavailable is a deliberate choice, not a failure.** If the environmental data source is unavailable, there will be no environmental card. The system prefers to stay silent rather than present stale data as fresh. This may sometimes feel like a gap, but it is an intentional decision.

**The old module has been removed.** Until the end of September 2026, the morning text was also being generated in parallel by a separate, standalone script. That script has been retired: the brief is now written by one subsystem only. If you see anything resembling double output, that is a reason for diagnosis, not normal behaviour.

**The clinical basis may not be established.** If the clinical package is not installed, personal food recommendations will be unavailable — the system will explicitly report this rather than substituting something that approximates the truth.

## Where This Lives in the System

The anti-repeat logic lives in **`brief_pipeline.py`** — that is where decisions are made about what enters the brief, and what is suppressed and why. The intentions and principles of the subsystem are described in **`subsystem_intent.yaml`**: this is a kind of constitution explaining the design in human language. Both files are read together: the first says *how*, the second says *why*.
