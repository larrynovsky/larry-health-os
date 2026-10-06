<!-- translation-of: docs/explanation/device_metrics_owner.md sha256:7cd8a2939f0a -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](device_metrics_owner.md)

# Every device metric has an owner: how the system detects data loss that would otherwise go unnoticed

## What changed

- **The claim wording for `bp_second_source` has been clarified.** The direction of the change is not determined by machine — describe it neutrally: what exactly became more precise can be read in the limits below, not in a conclusion that "it became more reliable."

- **Three explicit limits of what is proven for `bp_second_source` have been added:**
  - Withings is polled twice a day (09:10 and 21:10): a measurement deleted in the app reaches the database only on the next run, not instantly.
  - If a tenant has no Withings connection — the second path is silent in exactly the same way a non-existent device would be.
  - Mirroring of deletions (removing measurements absent in Withings) was added on 06.10 after confirmation on 05.10; no real deletion has passed through it yet — only in tests.

- **Three previous limits of `bp_second_source` have been rewritten, not removed.** The two-day delay and "the sensor does not judge the reverse side" are gone along with the old watcher: Withings now writes daily blood pressure itself, and there is nothing left to compare it against "Health." Silence without a Withings connection remains in the new wording. What is proven has not become broader as a result.

- **A new invariant `bp_day_owner_withings` with status `open` has been added.** The intent is recorded: who writes the daily blood pressure and how it is calculated when a cloud-connected blood pressure monitor is present. Status `open` means this is precisely an intent — not a working mechanism.

**Updated:** 2026-10-06


## Why it exists

When a smart device stops sending data, it is usually noticed immediately: charts disappear, summaries go empty, something is obviously broken. But there is another scenario — a quiet one, and therefore more dangerous: the device keeps sending data, the bot keeps running, the summaries look alive, yet some measurements simply never reach storage. No alert, no signal. Numbers that decisions are later made from just silently disappear.

This is exactly the scenario the "Every device metric has an owner" subsystem addresses. It answers a simple question: every field the device sent — did it get somewhere, or was it lost along the way? And if it was lost — the system must be loud about it, not silent.

The motivation for building this kind of order came from specific findings: blood pressure measurements that exist in the app and in the cloud but never reached the final database; and a measurement that the device averaged together with another one that the owner had already deleted as erroneous. These are not hypothetical risks — these are things that already happened.

## What it does, in plain terms

The subsystem rests on several simple agreements that the system itself verifies.

**Every field must have a fate.** When a device sends data, a dedicated intake judge looks at each field and issues one of four verdicts: "processed and stored," "decided not to take — and here is why," "parsed but did not reach storage," or "unrecognized field." The third and fourth verdicts are alerts: something was lost, or something new has appeared. The judge looks at what is actually happening with the data, not at what is written in the registry.

**"Covered by another source" is verified, not taken on faith.** If a field is recorded as "not taken because this is already present in this column," the system checks: is it actually there? If the column is empty on days when this field arrived — the verdict changes to "has no owner."

**The judge itself is also watched.** There is a separate nightly sensor that checks: has the judge gone blind? If the registry has not seen incoming data for a long time even though the archive is fresh — that is also an alert. The classic case: the intake path changed, the judge is looking into an emptied old directory, sees nothing, and thinks everything is fine. The system now detects this scenario.

**An empty column is not always "no device."** If some metric is not filled in the database, it can mean two completely different things: either the device genuinely sent nothing (and then the silence is correct), or the device sent data that was lost. The system distinguishes these two cases by checking against the raw archive: if the source of this field never appeared in the archive — the silence is justified. If it did appear and the column is empty — that is a loss, and it must be reported. This is exactly why the archive is compressed rather than deleted: without it, silence cannot be distinguished from loss.

**Blood pressure is a special case with two paths.** Blood pressure has a second, independent path in addition to the primary device: measurements arrive directly from the blood pressure monitor's cloud. This matters for a specific reason: the primary device may average the day's data in a way that includes a measurement the owner has already deleted as erroneous. The direct path from the cloud does not do this — it sees only the measurements the owner considers real. A deleted measurement will disappear from the database on the next sync.

## What is honest to say about its limits

Some agreements are already working and verified. Some are still in progress. It is important not to conflate the two.

**What works and is verified — but not everywhere.**

The second blood pressure path through the monitor's cloud is confirmed on real data: three measurements with specific dates matched what the owner sees in the app. But this is a check of one specific run, not of every possible situation. A few honest caveats:

- A measurement deleted in the blood pressure monitor's app will enter the database with a delay — it will disappear only after the next scheduled cloud poll, not instantly.
- If a user has no blood pressure monitor with a cloud connection — this path is silent in exactly the same way a non-existent device would be. This is correct behavior, but it must be understood.
- Deletion mirroring was added after the main path; no real deletion has passed through it under real conditions — only in tests.

**What is not yet complete and needs to be known.**

The agreement about who exactly writes the daily blood pressure and how exactly it is calculated is not yet fully implemented. The decision was made (26 September and 6 October), the intent is recorded, but this cannot be considered working. The substance of the intent: for users who have a cloud-connected blood pressure monitor, the daily blood pressure in the final database must be calculated only from that monitor's measurements, and the path through the primary device must set data aside as a witness rather than overwriting the result. The nightly judge must verify this every night. But for now this is precisely an intent, not a fact.

To this are added limits that hold true even when everything is implemented:

- The day is calculated according to the time zone of the installation location, not the location where the measurement was taken. On a trip, a late-night measurement may "fall" into the adjacent day.
- For a user without a cloud-connected monitor, the primary device still writes blood pressure, and measurements deleted in it are not seen by the system.
- Data from the primary device is set aside as a witness, but is not compared against monitor data by anyone — because the owner deliberately deletes unsuccessful measurements, and such a comparison would raise an alert every time for no good reason.

## Where this is in the system

The central file of the subsystem is `hae_checker.py`: it contains the intake judge and the nightly sensors. The intents and agreements the system is supposed to fulfill are recorded in `subsystem_intent.yaml` — this is not technical documentation, but specifically a record of what was decided and why, so that the next review can rely on something concrete rather than on memory.
