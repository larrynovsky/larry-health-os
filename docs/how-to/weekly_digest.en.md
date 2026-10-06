<!-- translation-of: docs/how-to/weekly_digest.md sha256:22d4c26170e2 -->
**English** · [Русский](weekly_digest.md)

# Weekly change digest: regenerate, verify, and understand why it did not arrive

> **Document type:** How-to (Diátaxis). Why it works this way: [docs/explanation/multitenancy.md](../explanation/multitenancy.md) (the “One text for everyone” paragraph). The thread plan is in the private part of the project.

Who does what: `launchd com.larry.health.weekly-digest` (Studio, **Saturday 22:00**, owner's environment)
→ `weekly_digest.py` writes `outputs/weekly_digest/<ISO-неделя>.json` → each bot, once an hour
(`jobs/scheduled.weekly_digest_outbox`), writes its own `<неделя>.gate.<тенант>.json` and, **Sunday ≥ 09:00**
local time, sends the text if all verdicts are `pass` → the `system_config.weekly_digest.last_sent_week` marker
→ Monday: `integrity_tests.check_weekly_digest_delivered` fails if anyone is missing the marker.

## View the text without sending it (dry-run)

On MacBook or Studio, from the repository directory:

```
python3.11 weekly_digest.py --dry-run --week 2026-W36
```

Prints a summary (commit count, threads, gate verdict) and the text. Without `--week`, uses the current ISO week.
Exit code 2 = the gate blocked it; `_rejected_preview` contains the rejected text.

## Regenerate a week

On Studio (only it has the database for the per-tenant dictionary):

```
python3.11 weekly_digest.py --week 2026-W36
```

The file is overwritten atomically. Tenant verdict files are **not** overwritten — delete
`outputs/weekly_digest/2026-W36.gate.*.json`, or the bots will consider the old verdict valid.
If the week is already marked as delivered, the bot will not send it again (`due` → `done`).

## Why it did not arrive — four reasons

0. **The bot cannot see the generator's folder.** The generator lives on the host (it needs git); the
   owner's bot has lived in a container since 30.09. The `outputs/weekly_digest` folder reaches it as a
   volume from `scripts/install.py --owner-override`. Check:
   `docker --context colima-health exec health-bot-1 ls /app/outputs/weekly_digest` — empty or
   "No such file" while the host has the file = no volume: re-render the override and run
   `scripts/deploy_container.sh`. This is how week W40 was lost for the owner on 04.10 (thread digest-container).
1. **No `<неделя>.json` file** — the generator did not run. Check `launchctl list | grep weekly-digest`,
   log `logs/weekly_digest.log` / `_err.log`. Since 05.10 a bot that sees the folder alerts the operator on Sunday after 09:00.
2. **`text: null`** — the gate blocked both versions. In `gate.kind`: `lexicon` (term/number/date —
   `hits_class` gives the class), `judge` (a fact about a person), `fidelity` (a paragraph misrepresents the thread), `guard`
   (the `llm_client` secret guard or API). The operator received `notify_operator` directly from the generator (Saturday 22:xx) and on the bot's first tick.
3. **No `<неделя>.gate.<тенант>.json`** for one of the tenants — their bot did not run or their dictionary
   blocked it (`verdict: blocked`). Delivery waits for everyone; the operator alert comes on the first tick after 09:00 Sunday.

## Adjust the tone or ban a word

Tone: `data/prompts/weekly_digest.md` (tone samples inside). Banned technical words:
`data/prompts/weekly_digest_banned_terms.txt` (stem + ≤2 ending letters, matched as a whole word).
Visible surfaces (which files count as “visible” to the tenant): `system_config['digest.visible_hints']`
(value_json, a list of path substrings); if absent, the module's code default applies.
Story size: a thread has ≥ 3 commits, the top 3 are required (`MAJOR_MIN_COMMITS`) — the code decides.

## Install/update the schedule on Studio

```
ssh studio 'cp ~/health_scripts/launchd/com.larry.health.weekly-digest.plist ~/Library/LaunchAgents/ && launchctl unload ~/Library/LaunchAgents/com.larry.health.weekly-digest.plist 2>/dev/null; launchctl load ~/Library/LaunchAgents/com.larry.health.weekly-digest.plist'
```

The schedule will appear in ARCH_SNAPSHOT through `gen_schedule.py`.
