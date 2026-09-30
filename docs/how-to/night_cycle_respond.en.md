<!-- translation-of: docs/how-to/night_cycle_respond.md sha256:df4907538e44 -->
**English** · [Русский](night_cycle_respond.md)

# How-to: respond to the nightly notification

A task procedure. You received a notification in Telegram: "🔔 Waiting for your decision: N".
Here is what to do. For the reasoning, see `docs/explanation/night_cycle.md`.

Since 09-28, the notification arrives once a day and names each decision in one plain-language line;
stalled work is named by its title. If a decision has a default-decision deadline, the notification
names the nearest one.

## When the notification arrives

1. Open Claude in the `health` project (desktop, so the bridge to Studio is available) and write:
   **"разбери решения"** ("go through the decisions").
2. The session will read the parked cards (`parked_decisions.list_open`, excluding the `dev_fix`
   engineering queue) and show each one: the question, the options, and the cost of each.
3. For each gate, say one of the following:
   - **yes / I approve** — for a work plan;
   - **close it** — to close a stage (once you have verified it is ready);
   - **a substantive decision** — for an owner gate (canonical/threshold/irreversible action);
   - **defer until <date>** — to reschedule; the notification will stay silent until then.
4. You do not need to press ANYTHING in the bot — it only rings. The decision is recorded in the session.

The notification arrives once a day between 08:00 and 20:00 (home local time) until a decision is RECORDED.
"I looked at it" does not count — only a recorded decision or deferral stops it.

## What the notification does not include — the engineering queue

Since 09-23, the notification counts only what you decide. Engineering findings (update a package,
commit a file, recalculate metrics, fix a sensor) go into a separate queue, and
the bell does not ring for them: you are not their oracle. Once a week, they reach you
in a Monday digest "for your information" — one line per finding and a total: "Night cycle engineering
queue: N, oldest X days." If you want to work through it, say "work through
the engineering queue" in the session. Engineering issues are escalated to you only when the action is irreversible or
affects your domain (canonical lab results, normal-range thresholds).

## If you stay silent

Currently (since 09-23), silence **decides nothing**: the card waits for your answer. Your decision
on 09-13 was "silence = delegation," but the check on 09-23 showed that nothing could execute the chosen
option — the system would only have recorded "decided by default: Block" and
reported an action that never happened. A default is therefore assigned only to an option
with an executor, and none has one yet. The notification will include "If you stay silent, this will
be decided automatically on DD.MM" again only when the first real executor exists.

Once an executor exists, the previous mechanism will return: **the silence deadline is 14 days** after
the card appears; when it expires, the system EXECUTES the option, records `решено умолчанием
ДД.ММ.ГГГГ: <вариант> · откат: <как откатить>`, and informs you in a separate message.

What defaults NEVER affect, even with an executor: irreversible actions and anything that writes to
your domain (canonical lab results, normal-range thresholds).

## What is cleared from the desk automatically

- A card from the morning check — when its finding is absent from today's check or
  becomes "for your information" (`standing`). The record's author is `not_on_desk`.
- A "thread stalled — finish or close it?" card — when the thread is closed. The author is `thread_closed`.

Neither is recorded as your response.

Check what is pending — the desk lives where the owner's night cycle runs. Which runtime
that is, `cat ~/health/RUNTIME` on Studio tells:

    # container (docker-install pilot, since 30.09):
    DOCKER_CONTEXT=colima-health docker exec -w /app health-cron-1 python3 -c "import parked_decisions as p; [print(g['id'], '|', g.get('kind')) for g in p.list_open()]"
    # native:
    python3.11 -c "import parked_decisions as p; [print(g['id'], '|', g.get('kind')) for g in p.list_open()]"

A decision is recorded (`record_decision`) the same way. With the container runtime the
native desk is frozen (444, since 30.09): the container desk is the one that rings, and a
decision written to the native one would never reach the bell. On 30.09 the container
started with an empty desk and rang about decisions made on 10.08 and 23.08 — the desks
were merged (the native decision wins); copies before the merge are in
`~/health/backups/bell-state/` on Studio.

## If you get "The decision desk lost its memory"

The desk (the runtime's `logs/parked_decisions.json`) holds fewer cards than the database
remembers (`system_config.parked_decisions.known_ids`) — this is what happened on 30.09 after
the move into the container. In this state the night cycle parks nothing and the bell sends
one line instead of the list. For the session: find the previous desk (native
`~/health_scripts/logs/parked_decisions.json` on Studio, copies in `~/health/backups/`), merge
it into the runtime desk under `parked_decisions._locked()` (the owner's decision wins; a
machine retirement does not), then check `memory_lost() is None`. Never lower the mark in
the database: it is the memory.

## If there is no notification

Silence = no pending decisions. This is a guarantee, not a hope: the bell has a heartbeat
(`check_doorbell_liveness` in nightly integrity checks), and if the channel died, that sensor would fail
instead of silently replacing the notification.

## Activation (one time, when you say "enable")

**Activated on 2026-08-03 (commit 1ad097c).** The procedure below is for
reactivation/after cleanup. The plists in the repository (`launchd/`) are the source; the active files are COPIES in
`~/Library/LaunchAgents/` (this is how all health jobs load, including automatic loading after reboot):

    cp ~/health_scripts/launchd/com.larry.health.night-cycle.plist ~/Library/LaunchAgents/
    cp ~/health_scripts/launchd/com.larry.health.owner-nag.plist   ~/Library/LaunchAgents/
    launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.larry.health.night-cycle.plist
    launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.larry.health.owner-nag.plist

If you edited a plist in the repository, copy it to LaunchAgents again and run `launchctl bootout` + `bootstrap`
again; otherwise, the old copy remains active. Check:

    launchctl list | grep -E "night-cycle|owner-nag"

The night cycle runs at 08:00, and the bell at 08/11/14/17/20 (home local time); at 08:00, the bell waits
for the cycle, and the cycle itself sends the day's first notification when it finishes — the count already reflects cleared cards. If the bell has already rung today, the later runs stay silent. Run manually:
`python3.11 ~/health_scripts/night_cycle.py`. Heartbeat health is covered by the §14 sensors in nightly
integrity checks (bell: "last run N days ago"; `via='none'` on a notification = dead delivery → FAIL).

## Stage 1 boundaries

The cycle currently INVESTIGATES and PARKS; it does not yet prepare or apply patches (stage 2)
— a dev fix goes into the engineering queue with a diagnosis, and a session fixes it.
Automatic application without a console is stage 2, under a separate plan.

