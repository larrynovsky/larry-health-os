<!-- translation-of: docs/explanation/night_test_triage.md sha256:0a1c3f053b56 -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](night_test_triage.md)

# Nightly Red Triage: severity by test intent, not by loudness of failure

## What changed

- **Status of the morning report clarified.** A previous entry (23.09 record) incorrectly stated that the old morning_report module (removed on 30.09.2026) "silently drops a test block with no result". Corrected: this module was taken out of the schedule on 13.07 and does not reach the owner at all — it does not silently drop the block, it simply is not in the pipeline.

- **The single alert delivery channel is now explicitly marked as the only one.** The daily warn-level triage (class `decide`) is not the primary channel — it is the entire channel. There is no fallback, and this is recorded as a known limit, not as engineered reliability.

**Updated:** 2026-09-30


## Why it exists

Tests fail at night. But not every failure is equally alarming — and if you treat them all the same way, you stop reacting altogether.

Imagine: every night the system screams CRITICAL. At first you look. Then you look with half an eye. Then you stop looking entirely — because you know that half the screams are about things that are not ready yet anyway. This is exactly what happens when the rule is simple: "test failed — it's critical". The signal drowns in noise, and a real regression — one that actually broke something that was working — passes unnoticed.

This subsystem exists to separate two fundamentally different kinds of red: red that signals a breakage, and red that simply reflects a known, incomplete area of work. It does this not by the on-call engineer's intuition and not by the loudness of the failure — but by what is known about the purpose of each test in advance.

## What it does, in plain terms

Every test has a subject — what exactly it checks. That subject is recorded in the use-case index (UC). And every such use case has a status: whether it is already implemented, partially implemented, or only planned.

When a test fails at night, the subsystem looks not at the fact of the failure itself, but at that status.

If the test checks something that is already considered implemented — that is a regression. Something broke. The owner is called and reminded.

If the test checks something that has not yet reached implementation or is partially implemented — the red is expected. This is not news, but a known gap. It is recorded in a separate report and does not raise an alert.

If the test is not listed in the index at all — that is CRITICAL. Not necessarily because something is broken, but because an unknown subject is a hole in the index itself. Staying silent about such a hole means pretending everything is fine when that is unknown.

On top of this triage, two additional layers operate. The first is automatic retries for failures that resemble random flakes by the character of the error message: if the test passes on retry, it is treated as a flake; if it fails again or the retry did not occur — it goes into the normal triage. The second is one short automatic diagnosis for each remaining CRITICAL, with no authority to change any code itself.

It is important to understand three places where the system may appear green but actually know nothing. First: a failure that matched a flake pattern and did not pass retry — it is no longer silently dropped, but goes into triage with a marker. Second: a diagnosis written for a different version of the code — the cache is tied to a specific commit, and a code change produces a fresh diagnosis. Third and least visible: a night that did not happen at all. For this there is a separate morning sensor — it checks that the nightly results actually cover the last scheduled run. If the nightly job died before reaching the end, that is audible in the morning rather than silent.

## What to honestly say about its limits

Three invariants hold, but have not been tested under all possible conditions. This is important to state plainly.

**The run perimeter is defined by the directory, not by a tag.** This solves a specific problem: previously, tests without a layer tag were silently dropped from the run, and the drop looked like a green result. Currently, the guard tracks one specific way the run can silently shrink — a layer-name filter returning results when tags are incomplete. But there are other ways a run can silently become smaller — for example, a directory that is no longer called from the script, or a test being skipped from within itself — and the guard does not see these. A broad receipt of "how many tests were collected versus how many should be" is not built. The cost of this decision is also real: the run became longer, and this sharpened a database-lock conflict in one specific test — it is red in the combined run, though it is always green in isolation. The flake layer has a standard answer for that situation.

**The retry on suspected flake is one.** If the test passes on the first retry, it is treated as a flake. This is a limit of the oracle, not an error: a failure that reproduces every other time will sometimes be classified as a flake under this rule. An important detail: across the entire history of live logs, not a single case has been recorded where a flake pattern coincided with a real regression. All traces of automatic retries found in the logs turned out to be an artifact of a different problem. This is encouraging, but does not remove the limit.

**The morning sensor judges by the task's trace, not by the fact of its launch.** This is the correct decision: a task that started but did not reach completion reads as missed — which is exactly right. But the sensor only knows about the schedule formats it can read; other formats produce a "cannot judge" signal and sound that explicitly rather than staying silent. Information reaches the owner through one channel — the daily triage. This is the only route.

## Where this lives in the system

The triage logic lives in `test_failure_handler.py`. This is where a test is matched against its UC status, the severity decision is made, a retry is launched, and a diagnosis is requested.

The subsystem's intentions and invariants are recorded in `subsystem_intent.yaml` — a machine-readable contract that describes what the system is obligated to do and what counts as its limits.

The decision of whether to apply a fix automatically is not made by this module — it references that as an external rule. This separation is intentional: the module triages and reports, but does not act on its own.
