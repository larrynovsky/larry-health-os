<!-- translation-of: docs/explanation/device_metrics_owner.md sha256:84744de32a41 -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](device_metrics_owner.md)

# Every instrument metric has an owner: explanation

## What changed

- **The proof boundary for `bp_second_source` has been clarified.** Confirmation by live import is recorded more precisely: on 05.10 three readings (dates are in the closed part of the borrow-botkin thread) passed into the production database and matched "Health" (Withings source); the 21:10 run received 1 reading, a live token was created at 21:10:00, the error log is empty, and there are no `import_withings` records in `faults.jsonl`. The criterion was approved by the owner on 05.10 at 16:55. This is "proven within the stated limits" — no broader.

- **The old boundary wording has been removed as the narrower formulation.** The previous text listed specific reading dates in the open — the dates have now been moved to the closed part and the wording has been generalised. The substantive content of the confirmation has not changed; the removal means that the boundaries of what is proven are now described differently, not that the proof has become stronger or weaker.

- **The direction of the claim change for `bp_second_source` is not machine-determined.** Read as a neutral clarification of wording: what exactly became more precise is within the proven limits stated above; the conclusion "became more reliable" or "weakened" does not follow from the anchor.

**Updated:** 2026-10-06


## Why it exists

The instrument sends dozens of different readings at once. Most of them reach somewhere, turn into numbers in a table, appear in the summary — and it seems like everything is working. But "seems" is the key word here.

A failure in this part of the system looks not like an error, but like silence. The bot responds. The summary arrives. It's just that there are no steps for three days in it, or blood pressure is calculated incorrectly, or one metric quietly disappeared after an update — and nobody noticed. Precisely because everything looks alive on the surface.

The "every metric has an owner" subsystem exists so that such silence becomes a scream. Its purpose is to prevent data loss from passing itself off as normal.

## What it does, in plain words

When the instrument sends the next data packet, the system does not simply sort numbers into their places. It asks a question about every metric: *what happened to it?*

There can be several answers. The metric reached storage — good, it has an owner. The metric has a pre-recorded note saying "we don't take this, because…" — also fine, the decision was deliberate. The metric was parsed but did not reach storage — that is an alert. The metric is completely unrecognised — also an alert.

An important point: the words "covered by such-and-such column" are not enough. The system checks that on days when this metric arrived, the column was actually non-empty. If the column is empty — the metric is lost, regardless of how it is labelled in the registry.

The question "has the monitoring system itself gone blind?" is protected separately. If the registry has not seen new data for a long time — not because the instrument was silent, but because the ingestion path became stale — that is also a scream, separate from all others.

There is one more subtle point about empty columns. If some family member's metric is not filled in, it may mean "no device present," or it may mean loss. The system distinguishes between these two cases: "no device present" is accepted only when the source of that metric has not appeared even once across the full depth of the raw archive. That is why the archive is compressed but not deleted — it is needed for this check.

For blood pressure, a separate, independent path is set up: data arrives directly from the Withings cloud, bypassing the main channel. This was done after it became clear that some readings were not reaching the system through the normal channel, and once a reading that the person had already deleted in the app ended up in the daily average. This path works as a cloud mirror — a reading deleted in the app is removed here too; an empty response when history is non-empty is treated as a failure, not as "no readings."

## What is honest to say about its limits

It is important to separate two things here: what holds and what is not yet complete.

**What holds, but has not been verified everywhere.**

The independent blood pressure path via Withings works and is confirmed by live import into the production database. But this confirmation has specific boundaries, and stating them in fine print would be dishonest:

- Withings is polled twice a day. If a person deletes a reading in the app, the database will learn about it only at the next poll — not instantly.
- The mirror logic, by which a reading deleted in the app is also removed from the database, was added recently. Such a deletion has not gone through in a real situation yet — it has been verified in tests, but not in reality.
- If a particular user does not have Withings connected, this path simply stays silent — as a device that is absent would.

**What is not yet complete.**

The rule about exactly who writes the daily blood pressure into the final table is formulated and the decision has been made, but the invariant is not yet closed — it is marked as open. The meaning of the rule is as follows: for those with Withings connected, the daily blood pressure should be calculated as the average of all readings for the day in local time, and the day's peak as the maximum of readings; only the Withings import should write this, while data from "Health" is stored separately as a witness but does not go into the final columns. From the first day Withings is connected it becomes the sole owner of the day; days before that moment remain with "Health." The nightly checker must recalculate averages itself and raise alerts about discrepancies.

This rule is formulated precisely, but has not yet been fully implemented. Treating it as working right now is incorrect.

Additional limits worth knowing:

- The day is counted by the timezone of the system installation, not by the location of the reading. During travel, a night-time reading may fall into the adjacent day.
- For a partner without Withings, blood pressure is still written by "Health." Readings deleted on the device are not visible there.
- "Health" blood pressure data is stored as a witness but is not adjudicated by anyone: the system deliberately does not compare it with Withings, because deleting a reading on the device is a normal human action, and such a comparison would raise an alert on every deletion.

## Where this is in the system

The main logic is concentrated in `hae_checker.py` — that is where the judge lives that delivers a verdict on every incoming metric, and the nightly sensor that checks the state of the registry independently of incoming events. The intentions and rules of the subsystem are described in `subsystem_intent.yaml` — this is not code, but recorded decisions: what we take, what we do not take and why, and what promises the system undertakes to keep.
