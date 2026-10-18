<!-- translation-of: docs/how-to/weekly_digest.md sha256:40ce27bb5303 -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](weekly_digest.md)

# Weekly change digest: regenerate, verify, understand "why it wasn't delivered"

> **Document type:** How-to (Diátaxis). Why it is structured this way: [docs/explanation/multitenancy.md](../explanation/multitenancy.md) (paragraph "One text for everyone"). The thread plan is in the closed part of the project.

Who does what: `launchd com.larry.health.weekly-digest` (Studio, **Sat 22:00**, owner environment)
→ `weekly_digest.py` writes `outputs/weekly_digest/<ISO-week>.json` → each bot once per hour
(`jobs/scheduled.weekly_digest_outbox`) writes its own `<week>.gate.<tenant>.json` and **Sun ≥ 09:00**
local time sends the text if all verdicts are `pass` → label `system_config.weekly_digest.last_sent_week`
→ Monday: `integrity_tests.check_weekly_digest_delivered` goes red if any tenant has no label.

## View the text without sending (dry-run)

On MacBook or Studio, from the repo directory:

```
python3.11 weekly_digest.py --dry-run --week 2026-W36
```

Prints a summary (commit count, threads, gate verdict) and the text. Without `--week` — the current ISO week.
Exit code 2 = gate blocked; `_rejected_preview` contains the rejected text.

## Regenerate a week

On Studio (only there is the DB for the per-tenant dictionary):

```
python3.11 weekly_digest.py --week 2026-W36
```

The file is overwritten atomically. Tenant verdict files are **not** overwritten — delete
`outputs/weekly_digest/2026-W36.gate.*.json`, otherwise the bots will treat the old verdict as valid.
If the week has already been marked as delivered — the bot will not send it again (`due` → `done`).

## Why it wasn't delivered — four reasons

0. **The bot cannot see the generator's folder.** The generator lives on the host (it needs git), the owner's bot from
   30.09 — in a container. The folder `outputs/weekly_digest` reaches it as a volume from
   `scripts/install.py --owner-override`. Check:
   `docker --context colima-health exec health-bot-1 ls /app/outputs/weekly_digest` — empty or
   "No such file" when the file exists on the host = the volume is missing: regenerate the override and run `scripts/deploy_container.sh`.
   This is how week W40 was not delivered to the owner on 04.10 (thread digest-container).
1. **No `<week>.json` file** — the generator did not run. `launchctl list | grep weekly-digest`,
   log `logs/weekly_digest.log` / `_err.log`. From 05.10, a bot that can see the folder calls the operator on Sun after 09:00.
2. **`text: null`** — the gate blocked both variants. In `gate.kind`: `lexicon` (term/number/date —
   `hits_class` gives the class), `judge` (fact about a person), `fidelity` (a paragraph distorts the thread), `guard`
   (secrets guard `llm_client` or API). The operator received `notify_operator` immediately from the generator (Sat 22:xx) and from the bot's first tick.
3. **No `<week>.gate.<tenant>.json`** for one of the tenants — that tenant's bot did not run or its dictionary
   blocked it (`verdict: blocked`). Delivery waits for all tenants; the operator alert fires on the first tick after 09:00 Sun.

## Adjust tone or ban a word

Tone — `data/prompts/weekly_digest.md` (intonation samples inside). Banned technical words —
`data/prompts/weekly_digest_banned_terms.txt` (stem + ≤2-letter ending, whole word).
Showcase views (which files are considered "visible" to a tenant) — `system_config['digest.visible_hints']`
(value_json, list of path substrings); if absent — the code default in the module applies.
Story size: thread ≥ 3 commits, top-3 are mandatory (`MAJOR_MIN_COMMITS`) — decided by the code.

## Install/update the schedule on Studio

```
ssh studio 'cp ~/health_scripts/launchd/com.larry.health.weekly-digest.plist ~/Library/LaunchAgents/ && launchctl unload ~/Library/LaunchAgents/com.larry.health.weekly-digest.plist 2>/dev/null; launchctl load ~/Library/LaunchAgents/com.larry.health.weekly-digest.plist'
```

The schedule will be included in ARCH_SNAPSHOT via `gen_schedule.py`.
