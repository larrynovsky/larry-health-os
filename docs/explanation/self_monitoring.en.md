<!-- translation-of: docs/explanation/self_monitoring.md sha256:18bffaa1ca52 -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](self_monitoring.md)

# Self-monitoring: the system watches itself — how the self-check works

## What changed

- **The signal-delivery boundary to the repairer has been clarified.** The promise "the signal will arrive" is now explicitly limited: repair is only guaranteed on the installation where the night-repair process lives (the owner, a Studio instance with a fresh `notify.repairer_alive` mark). On a foreign installation a failure drops into an engineering queue that nobody processes; the bot does not promise a fix — it hands the operator a failure code, and what happens next is the operator's concern. This caveat was not on the page before.

**Updated:** 2026-10-02


## Why it exists

Imagine: everything is working. No alerts, no complaints. And somewhere inside, one check has been quietly failing for days — sending a signal into a log that nobody opens.

That is exactly what the most unpleasant kind of breakage looks like: not loud, but invisible. The system thinks it is watching over you. You think the system is watching over you. In reality, nobody is watching.

Self-monitoring exists so that this gap is impossible. Not "we catch errors," but "we know the signal reached you." Those are different things — and it is precisely in the space between them that the most important information usually gets lost.

## What it does, in plain terms

Every morning the system checks itself: is the database intact, is the data fresh, are the key metrics in place. This is not background noise — a critical failure raises a flag rather than sinking into a log.

But the check itself is only half the job. The core rule is: **you must verify the entire signal path to you, not just the fact that a check exists.** Detecting a problem and reporting it are not the same thing.

That is why alerts are delivered on the principle of "deliver everything except what explicitly requires no action" — not the other way around. The system does not ask "am I allowed to say something about this?"; it asks "is there a reason to stay silent?" This is intentional: if the list of "what to report" is assembled by hand, a new failure can easily never make the list.

The system also watches its own schedule. If a task was supposed to run and did not — the signal arrives after the very first missed run. There is no "let's wait one more time." Each task's rhythm is stored where it is declared: the scheduler calendar, the job's own receipt. No duplicate manual records — because those copies are exactly what used to diverge from reality and cry out about problems that did not exist.

External sources are a separate story. If an important file or knowledge base is unavailable, the system does not stay silent and does not pretend everything is fine: it says aloud that it could not read. A sensor with an unreadable timestamp responds "cannot judge" — rather than pretending to be alive.

Task-to-sensor coverage is not rewritten by hand either: the system reads from the code itself which tasks are under observation. A manual copy of that information is a reason to be suspicious.

## What to honestly say about its limits

All the promises described above hold. But "holds" and "verified under any conditions" are different statements. Here is where the line between them falls.

**Delivery does not work the same everywhere.** The signal reaches someone who can fix things only where the night-repair process lives. On a foreign installation the failure goes into a queue that nobody processes. That is why the bot promises a fix only when it knows the repairer is active; otherwise it hands a failure code to the operator — what happens next is the operator's concern.

**Task coverage is read from the code — but not all of it.** Only sensors of a certain kind are found automatically. Threshold sensors and indirect sensors are still recorded by hand and can diverge from reality. If a label in the code is not a plain string but a computed expression, the system will not guess it and will report a missing sensor — loudly, not silently.

**The strict silence zone is not the whole file.** The rule "a sensor may not stay silent about an unreadable timestamp" applies to a named list of functions. A new sensor written outside that list is only covered by the general numeric check.

**The first missed run rings the alarm — and that is a deliberate choice.** A single failure now also raises an alert. The owner accepted the cost of this decision knowingly. Task-liveness sensors signal to the engineering queue and the Monday digest — not directly to the owner.

**Several edge cases exist in the schedule sensors.** A task with a late-arrival tolerance may look like a missed run in the short window before the system has had a chance to check. An interval task is judged by the age of its receipt, not by the exact moment of launch — differences of seconds between runs are not accounted for. A task that has not declared its rhythm before the first run after an update is considered "not judged" — the system says so aloud.

## Where this lives in the system

The main work happens in **`integrity_tests.py`** — that is where the checks themselves live, along with the list of functions in the strict zone and the logic for reading coverage from the code. The intentions and principles of the subsystem are recorded in **`subsystem_intent.yaml`** — this is not technical documentation but an explanation of why things are arranged the way they are. The external dependencies that the system is required to keep under watch are listed in **`external_dependencies.yaml`**: if anything listed there becomes unavailable, `integrity_tests` will say so rather than degrading silently.
