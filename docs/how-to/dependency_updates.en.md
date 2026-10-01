<!-- translation-of: docs/how-to/dependency_updates.md sha256:f14b532b93d7 -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](dependency_updates.md)

# How to Update Dependencies (SEC-19, 2026-07-06)

A recipe for the system python3.11 on Studio — the canonical environment for health-os.
Version canon: `requirements.txt` (in git). Vulnerability audit: weekly
`com.larry.health.pipaudit` → `logs/pip_audit_latest.json` → nightly
`security_sensors` → triage → Telegram.

## When to Update

- A `security:pip_audit` warning arrived with an advisory-id — update the vulnerable package.
- Quarterly SECURITY.md audit — planned revision of lagging packages.
- At all other times — do not update: freshness is not a value in itself.

## Steps (one package per run)

1. **Cooldown**: the target version must be ≥3 days past its PyPI release date (supply-chain
   protection against freshly-compromised releases). Exception — a security fix
   closing an active advisory: install immediately.
2. On Studio: `ssh <studio_ssh> "/opt/homebrew/bin/python3.11 -m pip install 'pkg==X.Y.Z' --break-system-packages"`.
3. Run regression: `bash scripts/test_on_studio.sh` (or wait for nightly).
4. Update the lock from MacBook (single-writer: the file in git is edited here):
   `{ head -3 requirements.txt; ssh <studio_ssh> "/opt/homebrew/bin/python3.11 -m pip freeze"; } > requirements.txt`
   — updating the date in the 2nd line of the header.
5. Regenerate the audit out of schedule: `ssh <studio_ssh> "cd ~/health_scripts && /opt/homebrew/bin/python3.11 scripts/pip_audit_check.py"` — the warning must go silent.
6. Commit the lock (+ if advisory — a line in SECURITY.md if the resolution is non-trivial).

## Advisory Without a Fix

If a vulnerability has no fixed version, or it is not applicable (the feature is not
used, the attack surface is unreachable) — add the id to `_PIP_AUDIT_ALLOW`
(`security_sensors.py`) with a justifying comment. An empty allowlist is
a normal state; each entry is reviewed by the quarterly checklist.

## Restoring the Environment from Scratch

`/opt/homebrew/bin/python3.11 -m pip install -r requirements.txt --break-system-packages`

## What NOT to Do

- Do not update "everything at once" (`pip install -U ...` as a list) — if a regression
  occurs, binary search for the culprit is impossible.
- Do not edit versions only in the lock without installing on Studio — the lock describes
  the actual environment, not the desired one (otherwise it lies, see
  feedback_stale_intermediate_layer).
- Do not run pip-audit from the nightly integrity job — the network at 07:50 gates the morning
  report; network access is only in the weekly job.
