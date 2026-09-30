<!-- translation-of: docs/how-to/diagnose_silent_check.md sha256:7e227e3751bc -->
**English** · [Русский](diagnose_silent_check.md)

# How-to: the check fired, but no alert arrived

> **Genre (Diátaxis): how-to.** A procedure for diagnosing “detection without delivery”: the check sees a problem, but it never reaches you.
> Channel map: `../reference/check_delivery_contract.md`.

Symptom: you suspect there was a problem, but Telegram stayed silent; or you find a warning in the log that you never saw.

## Steps

1. **Did the check itself fire?** Look at `integrity_latest.json` on Studio:
   ```
   ssh <studio_ssh> 'cd ~/health_scripts; python3.11 -c "import json;d=json.load(open(\"logs/integrity_latest.json\"));print(d[\"fail\"],d[\"warn\"]);print([w[0] for w in d[\"warnings\"]])"'
   ```
   Empty/fresh: the check is alive. The file is old: the 07:50 run itself has stopped (see `logs/integrity_check.log`).

2. **Did the warning reach triage?** `tail logs/triage.log`. Look for `DONE: ... needs_user=[...]`.
   - `needs_user=[]` with nonempty warnings → the warning was muted, OR its class sent it to the Monday digest (`triage_agent.WARN_CLASSES`), OR the filter was narrowed. Compare `MUTE_WARN_SUBSTRINGS`, `WARN_CLASSES`, and `classify_warnings`.
   - No fresh entry at all → triage did not run. Look for `triage_agent упал` in the morning_report logs (since 2026-06-29, this sends a separate alert).
   - ⚠️ The line `USER QUESTIONS sent` is evidence of a CALL, not delivery, even with `via=`. You cannot judge delivery by the log: the authoritative artifact is the run receipt, `logs/triage_delivery_latest.json`, which the delivery path sensor reads.

2a. **What does the run receipt say?** `cat logs/triage_delivery_latest.json` — one file answers every question in this step:
   - `date` — the date of the last run (written on EVERY run, including when there was nothing to deliver). More than `TRIAGE_DELIVERY_MAX_SILENT_DAYS` behind → the delivery path is dead; check launchd `com.larry.health.triage`.
   - `questions` / `digest` — how many items should have been delivered. Both zero: a quiet day, nothing to prove; this is normal.
   - `via` / `via_digest` — the channel for each. `none` = both channels failed, the person received nothing; `null` with a nonzero counter = triage did not write the receipt.
   - `last_proven` — the date of the last PROVEN delivery. Earlier than the last Monday digest → FAIL: the delivery path is alive, but the guaranteed weekly delivery was not confirmed (since September 23; before that, a 9-day threshold).
   - `sent: false` — the run was without `--send` (manual inspection): it proves nothing about the channel.

3. **Is “auto_fixed” telling the truth?** If the log has `auto_fixed=[...]` but the problem returns the next day, the auto-fix is not persisting (as with genome: an orphaned Popen process died before writing). An auto-fix must not say “fixed” without rereading the signal. See `[[feedback_detection_without_delivery]]` in the mirror: delivery of false success.

4. **Is the channel itself alive?** Delivery goes through `notify.notify()` (Telegram → on failure, the fallback `healthchecks.io/fail` = email). If Telegram is silent, check whether an **email** from healthchecks arrived instead (that is the fallback working). Suspect: an expired token (`~/.health_secrets/telegram_token`), a blocked bot, no network. Direct `curl` without a fallback remains in `model_health_check`/`oura_freshness_check`/`check_wellally_updates`; they have only one channel.

5. **Check the path, not the presence of a function.** A green delivery unit test checks that the code *calls* send, not that Telegram *delivered*. Only a live ping confirms actual delivery (step 4). Do not let a green test fool you.

## What NOT to do

- Do not “fix detection” and close the issue: detection without a delivered response is useless (RST: check = step + evaluation + **response**).
- Do not add a silent auto-fix: the project follows Diagnose-don't-Repair (CLAUDE.md §13 (private part), level 1). Deliver the signal to the person.
- Do not mute a warning to “reduce noise” without a rationale and a test: that is a new hole disguised as design.
