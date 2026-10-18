<!-- translation-of: docs/how-to/night_cycle_respond.md sha256:6778ec90310e -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](night_cycle_respond.md)

# How-to: respond to a night call

A recipe task. You received a Telegram call "🔔 Awaiting your decision: N".
Here is what to do. The "why" behind this is in `docs/explanation/night_cycle.md`.

The call (since 28.09) arrives once a day and names each decision in one plain-language line;
a stuck task — by its name. If a decision has a default deadline, the call names the nearest one.

## When the call arrives

1. Open Claude on the `health` project (desktop, so the bridge to Studio is available) and write:
   **"parse decisions"**.
2. The session will read the parked cards (`parked_decisions.list_open`, excluding the
   engineering queue `dev_fix`) and show each one: the question, the options, and the cost of each.
3. For each gate, say one of:
   - **yes / I approve** — for a work plan;
   - **I'm closing** — for closing a phase (when you have verified yourself that it is ready);
   - **decision on the merits** — for an owner gate (canon/threshold/irreversible);
   - **postpone until <date>** — to defer; the call will go silent until the deadline.
4. There is NOTHING to tap in the bot — it only calls. The decision is recorded in the session.

The call arrives once a day in the 08–20 window (local home time) until the decision is RECORDED.
"Looked at it" does not count — only a recorded decision or a deferral silences it.

## What is not in the call — the engineering queue

Since 23.09 the call counts only what you decide. Engineering findings (update a package,
commit a file, recalculate metrics, fix a sensor) sit in a separate queue, and
the bell does not ring for them: you are not the oracle for those. Once a week they arrive
as a Monday digest "for your information" — each finding on its own line and a total "Engineering
night-cycle queue: N, oldest X days". If you want to go through it — say "parse the engineering
queue" in the session. Engineering escalates to you only when an action is irreversible or
touches your domain (lab canon, normal thresholds).

## If you stayed silent

Right now (since 23.09) silence **decides nothing**: the card waits for your word. Your decision
on 13.09 was "silence = delegation", but the check on 23.09 showed there was nothing to execute
the chosen option — the system would only have recorded "decided by default: Block" and
reported an action that never happened. Therefore a default is assigned only to an option that
has an executor, and there are currently none. The line "If you stay silent — DD.MM
it will resolve itself" will appear in the call again only alongside the first real executor.

When an executor appears, the mechanic will revert to the previous one: **silence deadline is 14 days**
from when the card appears; on expiry the system EXECUTES the option, records `decided by default
DD.MM.YYYY: <option> · rollback: <how to roll back>` and notifies you in a separate message.

What a default NEVER touches, even with an executor: anything irreversible, and anything that writes
to your domain (lab canon, normal thresholds).

## What is removed from the table automatically

- A card from the morning check — when its finding is absent from today's check or
  it has become "for your information" (`standing`). Record author — `not_on_desk`.
- A card "thread is stalled — finish or close?" — when the thread is closed. Author — `thread_closed`.

Neither of the above is recorded as your response.

To check what is pending — the table is wherever the owner night cycle runs. Which runtime that is,
`cat ~/health/RUNTIME` on Studio tells you:

    # container (docker-install pilot, since 30.09):
    DOCKER_CONTEXT=colima-health docker exec -w /app health-cron-1 python3 -c "import parked_decisions as p; [print(g['id'], '|', g.get('kind')) for g in p.list_open()]"
    # native:
    python3.11 -c "import parked_decisions as p; [print(g['id'], '|', g.get('kind')) for g in p.list_open()]"

The decision is recorded (`record_decision`) via the same path. The native table is frozen
(444, since 30.09) when the runtime is containerised: the container rings, and a decision
recorded to the native table would not be seen by the bell. On 30.09 the container started
with an empty table and called with decisions from 10.08 and 23.08 — the tables were merged
(the native decision wins), pre-merge copies are at
`~/health/backups/bell-state/` on Studio.

## If you receive "Decision table has lost memory"

The table (`logs/parked_decisions.json` of the runtime) contains fewer cards than are remembered
in the database (`system_config.parked_decisions.known_ids`) — this happened on 30.09 after
migration to the container. The night cycle in this state parks nothing, and the bell sends
one line instead of a list. Session: find the previous table (native
`~/health_scripts/logs/parked_decisions.json` on Studio, copies in `~/health/backups/`),
merge it into the runtime table under `parked_decisions._locked()`
(owner decision wins; machine-removed — does not), verify `memory_lost() is None`.
Do not edit the database mark downward: it is the memory.

## If there is no call

Silence = no pending decisions. This is a guarantee, not a hope: the bell has a heartbeat
(`check_doorbell_liveness` in the night integrity), and if the channel died — it would go red,
not silently substitute for the call.

## Activation (one-time, your "enable")

**Activated 2026-08-03 (commit 1ad097c).** The procedure below is for re-activation /
after a cleanup. Plists in the repo (`launchd/`) are the source; the COPIES in
`~/Library/LaunchAgents/` are what is active (that is how all health jobs load + auto-load on reboot):

    cp ~/health_scripts/launchd/com.larry.health.night-cycle.plist ~/Library/LaunchAgents/
    cp ~/health_scripts/launchd/com.larry.health.owner-nag.plist   ~/Library/LaunchAgents/
    launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.larry.health.night-cycle.plist
    launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.larry.health.owner-nag.plist

If you edited the plist in the repo — copy it to LaunchAgents and run `launchctl bootout` + `bootstrap`
again, otherwise the old copy remains active. To verify:

    launchctl list | grep -E "night-cycle|owner-nag"

The night cycle runs at 08:00, the bell — 08/11/14/17/20 (local home time); at 08:00 the bell
waits for the cycle, and the first call of the day is made by the cycle itself when it finishes —
the count is already after removals. If the call already came today, later runs stay silent.
To run manually:
`python3.11 ~/health_scripts/night_cycle.py`. Heartbeat health — §14 sensors in the night
integrity (bell "run N days ago"; `via='none'` on a call = dead delivery → FAIL).

## Phase 1 limits

Right now the cycle INVESTIGATES and PARKS; patch preparation and application (phase 2) it does
not yet do — a dev fix goes into the engineering queue with a diagnosis, the session fixes it.
Auto-application without a console — phase 2, a separate plan.
