<!-- translation-of: docs/how-to/dependency_updates.md sha256:ddec675c6ab4 -->
**English** · [Русский](dependency_updates.md)

# How to update dependencies (SEC-19, 2026-07-06)

A procedure for the system python3.11 on Studio, the canonical health-os environment.
Canonical versions: `requirements.lock` (in git). Vulnerability audit: weekly
`com.larry.health.pipaudit` → `logs/pip_audit_latest.json` → nightly
`security_sensors` → triage → Telegram.

## When to update

- A `security:pip_audit` warning arrives with an advisory-id: update the vulnerable package.
- Quarterly SECURITY.md audit: scheduled review of outdated packages.
- At other times, do not update: freshness has no value on its own.

## Steps (one package at a time)

1. **Cooldown**: the target version must be ≥3 days past its PyPI release date (supply-chain
   protection against newly compromised releases). Exception: a security fix
   addressing an active advisory; install it immediately.
2. On Studio: `ssh <studio_ssh> "/opt/homebrew/bin/python3.11 -m pip install 'pkg==X.Y.Z' --break-system-packages"`.
3. Run regression tests: `bash scripts/test_on_studio.sh` (or wait for the nightly run).
4. Update the lock from MacBook (single-writer: edit the file tracked in git here):
   `{ head -3 requirements.lock; ssh <studio_ssh> "/opt/homebrew/bin/python3.11 -m pip freeze"; } > requirements.lock`
   — updating the date in the second header line.
5. Regenerate the audit outside its schedule: `ssh <studio_ssh> "cd ~/health_scripts && /opt/homebrew/bin/python3.11 scripts/pip_audit_check.py"` — the warning should clear.
6. Commit the lock (+ for an advisory, a line in SECURITY.md if the decision is nontrivial).

## Advisory without a fix

If the vulnerability has no fixed version or does not apply (the function is not
used, the surface is unreachable), add the id to `_PIP_AUDIT_ALLOW`
(`security_sensors.py`) with a comment explaining why. An empty allowlist is
normal; review every entry using the quarterly checklist.

## Rebuild the environment from scratch

`/opt/homebrew/bin/python3.11 -m pip install -r requirements.lock --break-system-packages`

## What NOT to do

- Do not update “everything at once” (a list passed to `pip install -U ...`): if a regression occurs,
  you cannot use binary search to find the culprit.
- Do not change versions only in the lock without installing them on Studio: the lock describes
  the actual environment, not the desired one (otherwise it lies; see
  feedback_stale_intermediate_layer).
- Do not run pip-audit from nightly integrity: network access at 07:50 gates the morning
  report; network access belongs only in the weekly job.
