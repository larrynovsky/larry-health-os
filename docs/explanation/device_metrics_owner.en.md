<!-- translation-of: docs/explanation/device_metrics_owner.md sha256:5d6b73f0ea7e -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](device_metrics_owner.md)

# Every instrument metric has an owner: explanation

## What changed

First version of this section.

**Updated:** 2026-09-26


## Why it exists

When an instrument sends health data, everything looks fine from the outside: the bot responds, summaries arrive, charts are drawn. Nobody notices that some measurements quietly disappear along the way. Not with an error, not with a loud failure — they simply vanish.

An independently invented example: a device sends a fictional `example_level` field,
but the parser expects `example_value`. The registry marks the metric as "processed"
although the parser silently returns an empty result. Another scenario: after an intake
path changes, the registry keeps watching the emptied folder and stops noticing uploads.
Both examples illustrate technical loss without describing anyone's measurement history.

This layer exists precisely so that such silence becomes impossible. Not "we try not to lose anything", but: if a metric arrived and did not find its place — the system knows about it and says so out loud.

## What it does, in plain words

Imagine that every metric, as it enters the system, passes through a judge. The judge looks not at what is written in the registry, but at what is actually happening: did the data reach storage?

The judge delivers one of four verdicts:

- **"Accepted"** — the metric was parsed and saved where it should be.
- **"Consciously not collected"** — there is a recorded decision not to collect this metric, and the reason is on file.
- **"Lost"** — the metric was parsed but never reached storage. This is a problem.
- **"Unknown"** — the system is seeing this metric for the first time and does not know what to do with it.

An important detail: if someone has written "not collecting, because it is already in another column" — the judge checks whether that column actually contains data on the days the metric arrived. If the column is empty — that is not a "conscious decision", that is a loss. Word is not taken on faith.

At night a separate sensor runs that looks not at arrival events but at the overall state of the registry: are there any metrics without an owner right now. This is protection against the situation where something slipped through unnoticed.

And finally — the system watches the judge itself. If the registry has not seen new data for a long time, even though data exists in the archive, that too is a cry: "the judge is blind." Such a sensor detects a stale intake path.

A separate question is what an empty column means. The system considers a device absent only if the source of that metric has genuinely never appeared in the raw data — including old compressed files. If the source did appear but the column is empty, that is a loss, not silence. That is precisely why archives are compressed but not deleted: without them it is impossible to answer this question honestly.

## What to say honestly about its limits

All the invariants described here hold today. But "holds" and "verified under all conditions" are different statements, and conflating them would be dishonest.

The rule about an empty column is valid exactly as far as the raw archive goes. If data once arrived but is not in the archive — the system will not be able to distinguish "the device was absent" from "data was lost". The boundary of what can be proven coincides with the depth of what has been preserved.

The same applies to coverage checking: the system looks at the days a metric arrived and compares them against the column. That is more honest than taking someone's word for it — but it is still a check against the material that exists.

These limits do not make the system unreliable. They make it honest: it knows where the boundary is of what it can claim with confidence.

## Where this lives in the system

The judge logic lives in **`hae_checker.py`** (`judge_payload`); it is called by the instrument intake **`dashboard_routers/api_hae_ingest.py`** on every upload. What exactly is parsed and stored — **`import_apple_health.py`** (`aggregate_metric_by_day`) and **`metrics_db.py`** (`APPLE_RAW_KEYS`). The night sensors — every metric has an owner and judge blindness (`check_hae_arrivals_have_owner`), the empty-column rule (`_hae_device_absent`) — are in **`integrity_tests.py`**. The intentions behind this entire subsystem are recorded in **`subsystem_intent.yaml`** — this is not technical documentation, but a record of why it is structured this way at all and what lessons stand behind it.
