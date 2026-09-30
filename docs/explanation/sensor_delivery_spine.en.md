<!-- translation-of: docs/explanation/sensor_delivery_spine.md sha256:1ecaedc219e5 -->
**English** · [Русский](sensor_delivery_spine.md)

# How validation-gate sensors reach the owner (the delivery spine)

> Diátaxis: **explanation**. Answers “how it works,” not “what to click.” Created
> on 2026-07-23 because its absence once led to an incorrect answer spoken aloud
> (“the lab sensor is only in the soft brief” — in fact, it sends Telegram). This is the truth about
> how a “waiting” sensor actually fires and gets delivered.

## Why this exists

The project's principle: **“detection without delivery = no sensor.”** A sensor that computed
its condition but did not reach its reader does not exist. This document describes the SINGLE
delivery spine so a new sensor can use the existing channel instead of inventing its own
(and introducing another hardcoded Telegram path — caught by `test_telegram_delivery_coverage`).

## The spine's two rails

At night, `run_checks.sh --scheduled` (launchd 07:50) orchestrates the run. It has two delivery rails.

> ⚠️ **CORRECTED 2026-07-26 following an incident.** The text below says that any new `check_*` with
> `warn()` reaches the owner automatically. From July 13 to 26, this was **FALSE**: `run_triage`
> was called only from `morning_report.__main__`, which was removed from the schedule on 13.07 — delivery
> disappeared with its carrier because it was riding as a passenger. For 13 days, not a single warn arrived.
> Triage now has ITS OWN launchd agent, `com.larry.health.triage` (08:00), and the rail's own liveness
> is guarded by `check_triage_delivery_liveness` (FAIL level, uses the 🚨 channel, bypassing triage).
> Analysis of the failure class: [warn_delivery_rail.md](warn_delivery_rail.md).

**Rail A — integrity `warn()` → brief Telegram (a soft channel for invitations).**
`integrity_tests.py --json` writes `logs/integrity_latest.json` (fields `failures`/`warnings`).
At 08:00, the launchd agent `com.larry.health.triage` calls `triage_agent.py --send`
(before 2026-07-26, `morning_report.py` did this — see the note above); it reads the JSON and runs
`classify_warnings()`. The key point: this is a **mute-list, not a whitelist** — EVERYTHING is delivered except
three explicitly muted substrings (`MUTE_WARN_SUBSTRINGS`). Delivery: `notify.notify()` →
Telegram. Idempotent per day. The rail's guard is `test_triage_delivery`.
Consequence: **any new `check_*` in integrity that calls `warn()` reaches the owner
via Telegram automatically** — unless its name matches the mute list. Nothing additional needs to
be built.

**Rail B — pytest failure → self-dating Telegram (a hard channel for failures).**
The same nightly run executes the full `pytest tests/`. Failure → `send_telegram` (E1s):
self-dating SHA + time + **list of failed node IDs** (since 2026-07-23, `-rf` + parsing
`^FAILED`, so the alert names WHICH sensor has rotted). Return to green → recovery ping E1r.
Statistical sensors written as tests (null validity) use this rail.

Plus two independent channels, not about the validation gate but part of the picture: integrity `FAIL` →
🚨 Telegram + blocking the morning report (exit 2); `oura_freshness_check.py --notify` —
a named alert for biometric data interruption; healthchecks.io dead-man → **email** (a channel
independent of Telegram, in case the entire system dies).

## How each “waiting” sensor fires

| Sensor | Condition | Rail | What the owner sees |
|---|---|---|---|
| Lab reactivation (Gr. 5) | ≥3 analytes with ≥18 measurements (configuration from yaml/manifest) | A | Telegram line “Lab path ready to return… [signal from DATE]” |
| Null validity (Ph. 3-b) | production type-I `_strat_pvalue` outside the 0.12 band | B | Telegram “pytest failed: `test_null_validity_*`” |
| Partner readiness (Gr. 3) | partner ≥365 days of daily data + ≥2 periods (metadata, not PHI) | A | Telegram line “Partner ready for A/D stratification” |
| MC gap (Ph. 1/3-a) | pair passing BY with `\|p−L\|<2·MCSE`, L=(q/H_m)·k*/m — operational selection line | A (through artifact + freshness guard) | Telegram line “MC gap: N discoveries at Monte Carlo resolution: pair X” |
| Pass-set flicker (Ph. 4) | membership of passing pairs changed between the two LATEST runs (since 2026-07-25 WITHOUT an SHA condition: keying on the repository's `head_sha` made the detector permanently silent — 104–298 commits per week; “methodology, not data” is marked by a manual one-time marker `logs/passset_epoch_break.txt`) | A (append-only snapshot + freshness + epoch marker) | Telegram line “pass-set flickered: pairs X entered/left” + comparison context (dates and SHAs of both snapshots) |

> MC gap and flicker judge different properties: Monte Carlo resolution and
> stability of pass-set membership under the frozen gate, respectively. Stable membership
> does not trigger a flicker warning. Response: `HOWTO_mc_gap.md` / `HOWTO_passset_flicker.md`.
> The first membership change triggers building the online controller §5 (until then, the stream sleeps, R₀=0).

## Two traps the spine closes

1. **A documented threshold without a live check** = “someday” = never. A return
   threshold written only in a plan does not fire. So every “waiting” has a
   `check_*`, not a paragraph in the backlog.
2. **A warn whose name is muted by the mute-list** = detection without delivery (fires into a log no one
   reads). That is why sensor names are covered by delivery tests (`test_triage_delivery`:
   `test_fdr_gate_sensor_warns_are_delivered`).

## The MC gap subtlety (a cross-process stale read)

The MC gap originates in the nightly longitudinal gate run and is read by integrity at
07:50 — two different processes. If longitudinal did not run or ran later, integrity
will read yesterday's artifact. So `logs/gate_mc_gap_latest.json` carries a stamp
(gate run date + HEAD SHA), and `check_mc_gap` returns a liveness warn
(“the gate has not run”) when the stamp is stale rather than trusting the content. The same pattern as
`check_morning_brief_gate_liveness` and Oura conit freshness.
