<!-- translation-of: docs/explanation/self_monitoring.md sha256:afbc77d448dd -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](self_monitoring.md)

# Self-Monitoring: The System Watches Itself

## What Changed

- **Alert after the first missed run** (owner decision September 23, "general rule"). Eight liveness sensors each had their own tolerance for missed runs — from two hours to nine days. There is no longer any tolerance: a sensor turns red as soon as a job has failed to run even once according to its schedule. The schedule is taken from wherever it lives: for operating-system jobs — from their schedule file; for bot jobs — from the timestamp the job itself writes to indicate how often it runs; for the morning brief — from its time setting. The cost, accepted consciously: a single random failure also triggers an alert; for engineering jobs — into the engineering queue, not to the owner.

- **New invariant: the list of covered scheduled jobs is computed from code** (`job_coverage_is_read_from_code`, status: holding). Previously there was no guaranteed mechanism for this; now the system does not rely on manual records — a divergence between the registry and the code becomes a loud signal, not a silent failure. *Boundary*: auto-discovery works only for sensors whose schedule is given as a simple string; indirect and threshold-based sensors are still recorded manually.

- **New invariant: a liveness sensor does not stay silent when it cannot read a timestamp** (`liveness_sensor_does_not_read_bad_stamp_as_alive`, status: holding). Previously such a sensor could report nothing — and that would look like "all is well." Now it explicitly signals "cannot judge." *Boundary*: strict verification of this behavior covers only a named list of functions; sensors outside that list go through only a general numerical check.

**Updated:** 2026-09-23


## Why It Exists

When everything is running it is easy to think everything is fine. But "everything is running" and "you know about it" are two different claims.

Imagine a quiet day: one of the checks starts failing, sends a signal… and the signal goes into a log that nobody opens. From the outside — silence. The system looks alive. And inside, something is already broken.

That gap — between "caught" and "reported" — is exactly what self-monitoring closes. Its job is not to watch your health. Its job is to watch that the system watching your health has not lost its voice.

## What It Does, in Plain Terms

Every day, early in the morning, the system runs a check on itself: are the data intact, are they fresh, are the key metrics that make up the picture of your state all present. If any of this breaks — it becomes known immediately, not when someone happens to open a log.

But checking that a check ran is not enough. The main rule is this: **verify the signal path all the way to you**, not merely the fact that a check exists somewhere. The signal must get through.

That is why alerts are delivered on a "everything except known noise" basis, not on an "only what is explicitly permitted" basis. The difference matters: if a new failure was not on the permitted list — under the old approach it would simply have disappeared. Here it will get through.

One more thing the system watches: there are external data without which part of the analytics quietly degrades — it does not break loudly, it simply starts working worse. Those data are now under supervision: the system knows where they live and checks that they are present and not empty.

A separate matter concerns the sensors that watch whether scheduled jobs actually ran. Previously the list of such sensors was written by hand, and it happened a couple of times: a job was covered, a sensor existed, but someone forgot to add the line to the registry — and the system would cry "no sensor" about a job that was actually being watched. Now this list is computed from code, not copied by hand. A manual copy of the computed result is a finding, a reason to investigate.

Finally, a job's liveness sensor must not stay silent when it cannot read a timestamp. Silence is not "all is well" — it is a loss of information. Now such a sensor honestly says "cannot judge" rather than pretending nothing happened.

## What to Honestly Say About Its limits

Self-monitoring is holding — but "holding" and "verified everywhere" are different things. Here is where the boundary runs clearly.

**Coverage of scheduled jobs is computed from code — but not for all sensors.** Only those that use a specific way of specifying a schedule are found automatically. Sensors tied directly to a numeric threshold, and indirect ones — for example, digest-type or dead-man's-switch-type — are still recorded by hand and may diverge from what is actually in the code. Furthermore, if a job's schedule label is given not as a simple string but as a computed expression, the system will not infer it — and the census will cry "no sensor" even if a sensor exists. This is a loud signal, not a silent failure — but it is still a limit.

**Strict verification of liveness sensor behavior does not cover all sensors.** There is a named list of functions that are checked with particular thoroughness — that they do not stay silent on bad data. A new sensor written outside that list falls only under the general numerical check of the whole file: it will be noticed that something changed, but the behavior will not be verified in detail. This is not a gap — but it is not the same degree of confidence.

There are no other unfulfilled or disputed promises in this subsystem as of today.

## Where This Lives in the System

The core check logic lives in **`integrity_tests.py`** — it describes what exactly is checked every morning and which sensors are registered. The census of scheduled jobs and the coverage computed from code are in **`producer_registry.py`**; the named strict zone for the silence guard is in `tests/consistency/test_silent_handler_guard.py`.

The intent of the subsystem — why it is structured this way, what problem it closes, and what is considered its area of responsibility — is recorded in **`subsystem_intent.yaml`**.

The external data that the system is required to keep under supervision are listed in **`external_dependencies.yaml`** — this is the registry of what lives outside and without which part of the analytics would begin to degrade unnoticed.
