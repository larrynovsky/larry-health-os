<!-- translation-of: docs/explanation/proactivity.md sha256:0f1211dfdb0d -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](proactivity.md)

# Proactivity: a trend, not a spike — why the system stays silent on minor noise

## What changed

- **The exception for nighttime temperature is recorded as a principle, without numbers** (owner's wording 12.11). How rare a warm night must be is now stored in the threshold table, not in the intent description.

**Updated:** 2026-11-12


## Why it exists

Imagine a system that notices everything. Every bad reading — a notification. Every deviation — a signal. It sounds like attentiveness, but in practice it leads to one outcome: within a week you turn off all notifications at once and lose the ones that actually matter along with them.

The problem is not about noticing — it is about noticing the right things. One bad night, one low reading — that is life. The body is not perfect every day. If the system screams at every such moment, it stops being a helper and becomes noise.

This subsystem exists so the system speaks first — but only when it genuinely makes sense.

## What it does, in plain words

The system looks not at a single moment but at a direction. There is your baseline — what your readings look like during calm periods. And there is what is happening now. An alert arises when the current state steadily diverges from the habitual — not a one-time flicker, but a sustained series: several days in a row something is off.

One dip does not raise the alarm. A series is required.

There is one exception — nighttime body temperature. Here the system does not wait for several nights in a row: one night that is noticeably warmer than almost all of your nights over the past year is already sufficient reason to say "check how you feel." This is a deliberate exception to the general rule — temperature behaves differently from most other readings.

There is another discipline as well: the system stays silent when data is scarce. If the history is too short — less than roughly a week — it does not attempt to find a multi-day trend in it. Saying nothing is better than inventing a pattern from nothing.

## What to honestly say about its limits

It is important to state this plainly: the key part of this subsystem remains unfinished.

The thresholds that determine what counts as a "notable" deviation, and the series lengths after which the system decides to signal — all of this was chosen manually, based on common sense and experience. They have not been validated against real outcomes: it is unknown how many important signals the system misses and how many spurious ones it generates. The balance between "alerting too often" and "staying silent too long" is currently an assumption, not a proven optimum.

This is not a minor technical detail. It is the central question of the subsystem, and it remains open.

Everything else — distinguishing a trend from a spike, the temperature exception, refusing to identify trends on short data — works as intended. But how well the thresholds that trigger this logic are calibrated is something that cannot honestly be stated yet.

## Where this lives in the system

The subsystem's intent is recorded in `subsystem_intent.yaml` — it describes why it is structured this way and what promises it makes. This is the starting point: if something in the system's behaviour seems off, that is the first place to check.

The logic for assembling the context the system uses during analysis lives in `gp_context.py`. That is where it is decided which data enters consideration and whether there is enough of it to speak of a trend at all.

Together these two files are the description of the intent and its implementation. Each is incomplete without the other.
