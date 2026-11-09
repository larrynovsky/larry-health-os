<!-- translation-of: docs/explanation/self_monitoring.md sha256:7fa44206fb7c -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](self_monitoring.md)

# Self-Monitoring: the System Watches Itself

## What Changed

- **A new invariant added: the task guard runs in shadow mode** (`task_pair_guard_is_shadow`, status=holds). Matching new tasks against already-open ones is now explicitly recorded as a subsystem invariant — this is new coverage that was not on the page before.

**Updated:** 2026-11-07

**Updated:** 2026-11-07


## Why It Exists

Any system can break quietly. Not in a way where everything crashes loudly, but like this: one small check stops working, its result goes into a log nobody opens, and from the outside everything looks fine. You keep thinking you are protected — but the guard is no longer at the post.

That exact scenario — "broke, but nobody found out" — is the main threat that self-monitoring defends against. It is not enough for a check to exist. The signal from it must reach a person who can do something. A system that honestly detects but stays silent is no better than a system that does not detect at all.

That is why self-monitoring does not ask "does the check exist?" but "will the signal reach you if something goes wrong?" These are fundamentally different questions.

## What It Does, in Plain Terms

**Daily inspection.** Every morning the system runs a check on itself: it looks at whether the data is fresh, whether the database is intact, whether the key metrics are in place. If something is critically wrong — it does not sink into a log, it raises a flag. The inspection happens at a fixed time, so it has a rhythm, and a missed inspection is also noticeable.

**Delivery, not just detection.** Warnings are sent on the principle of "deliver everything except explicit useless noise" — not the other way around. It used to be different: some failures from the integrity check were simply dropped because they did not make it onto the "needs to be sent" list. Now the logic is inverted: something can be silenced only if it clearly requires no action.

**Guard for duplicates and contradictions in tasks.** When the system creates a new analysis task, the guard compares it against all already-open tasks of the same user. If two tasks have the same analysis and the same deadline — that is a duplicate. If the deadline differs — that is a contradiction. In both cases the system signals that a human should look at it themselves: it does not choose which task is correct for you, and it does not close them automatically. Every such comparison is written to a separate log with its result and reason.

If something goes technically wrong during a comparison — the guard says "could not assess" and records this as a failure. It does not pass silently.

**Job liveness sensors.** Each background job has its own schedule: some run every few hours, some once a week. The sensor watches that the job actually ran on time. The rule here is one and without exceptions: if the first expected run did not happen — signal. Not "let's wait a bit more," but immediately. The rhythm is taken from the place where it is declared — from the schedule or from the receipt the job leaves about itself.

If the sensor cannot read a timestamp — it says "cannot judge," not pretend everything is fine. "Not judged" is an honest answer; silence is not.

**Coverage is taken from code, not maintained by hand.** Previously the list of "which jobs are covered by the sensor" was maintained manually, and periodically someone forgot to add a line — the system would cry "no sensor" about jobs that were actually covered. Now for a certain class of sensors this is read directly from the code: if a check is registered with the required label — it is counted automatically. A manual copy of computed coverage is a find, a signal of desynchronization.

**External dependencies are also under guard.** Part of the system's methodological base lives in files, not in code. If those files disappear or turn out to be empty — the system will keep running, but some of its conclusions will start degrading invisibly. Therefore such dependencies are explicitly registered, and their presence is checked. A disappearance will not pass silently.

## What to Honestly Say About Its limits

All the described invariants hold — but "holds" and "verified under all circumstances" are different claims. Here is where the boundaries lie.

**Delivery is not everywhere.** The promise to deliver a warning to a person who can fix it is fulfilled only where overnight repair lives — that is, with the system owner in their installation. If the system is running for someone else, the failure will end up in an engineering queue that nobody processes. That is why the bot promises a fix only if it sees a fresh mark showing that repair is active. Otherwise it gives a failure code — not silence, but not a promise either.

**Automatic coverage does not work for all sensors.** Coverage is read automatically from code only via a specific registration mechanism. Threshold-based sensors and indirect sensors are still written by hand and may drift from reality. Additionally: if a label in the code is set by an expression rather than a string literal, the system will not guess it and will cry "no sensor," even if the sensor exists.

**The strict honesty zone of the sensor is a named list.** The rule "unreadable label → 'not judged,' not silence" is verified by name for specific functions. A new sensor written outside this list stands only under the general numeric control of the file, not under a named check.

**The rule "first miss is already a signal" has its own cost.** A single random failure will also ring. The owner accepted this deliberately: better an extra ring than a missed miss. But this is worth knowing.

Additional nuances for jobs with a permitted lateness: in the short window between a late run and an on-time run this can look like a miss — if the integrity check falls exactly in that window. For interval-based jobs (those that run after a fixed interval rather than on a strict schedule) the exact moment of the run is not derived, and a miss is assessed by the age of the receipt — small variations in run duration are not accounted for. Jobs that have not managed to declare their rhythm before the first run after an update will receive status "not judged" — out loud, before they have had a chance to report.

Liveness sensors for background jobs ring into the engineering queue and the Monday digest, not directly to the owner.

## Where This Lives in the System

All check logic lives in the file `integrity_tests.py` — this is the main self-monitoring file. It is what runs every morning, checks the sensors, reads coverage from code, and serves as the "oracle" for most of the invariants described here.

The intent of the subsystem — why it exists at all and what problem it solves — is recorded in `subsystem_intent.yaml`. This is not a technical file but a declaration of intent: what the system promises and what it protects against.

Also in `subsystem_intent.yaml` is recorded the
