<!-- translation-of: docs/how-to/dependency_updates.md sha256:1101284a6129 -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](dependency_updates.md)

# How to update dependencies (SEC-19, 2026-07-06)

A recipe for system python3.11 on Studio (second-tenant environment) and for the container
image (owner environment, section below).
Version canon: `requirements.txt` (in git). Vulnerability audit: weekly
`com.larry.health.pipaudit` → `logs/pip_audit_latest.json` → nightly
`security_sensors` → triage → Telegram.

## When to update

- A `security:pip_audit` warning arrived with an advisory-id — update the vulnerable package.
- Quarterly SECURITY.md audit — planned revision of lagging packages.
- At all other times — do not update: freshness is not a value in itself.

## Steps (one package per run)

1. **Cooldown**: target version must be ≥3 days past its PyPI release date (supply-chain
   protection against freshly-compromised releases). Exception — a security fix
   closing an active advisory: install immediately.
2. On Studio: `ssh <studio_ssh> "/opt/homebrew/bin/python3.11 -m pip install 'pkg==X.Y.Z' --break-system-packages"`.
3. Run regression: `bash scripts/test_on_studio.sh` (or wait for nightly).
4. Update the lock from MacBook (single-writer: the file in git is edited here):
   `{ head -3 requirements.txt; ssh <studio_ssh> "/opt/homebrew/bin/python3.11 -m pip freeze"; } > requirements.txt`
   — fixing the date in the 2nd line of the header.
5. Regenerate the audit out of schedule: `ssh <studio_ssh> "cd ~/health_scripts && /opt/homebrew/bin/python3.11 scripts/pip_audit_check.py"` — the warning should go away.
6. Commit the lock (+ if advisory — a line in SECURITY.md if the resolution is non-trivial).

## Container image (owner lives in the container from 30.09)

Image packages are installed from the same `requirements.txt`, so the steps above update the image as well:
the next deploy will build it with the new version. Separate from the lock, the image carries **pip and
setuptools** of Python itself: their versions are `ARG PIP_VERSION` and `ARG SETUPTOOLS_VERSION` in
`docker/Dockerfile`; by default the base `python:3.11-slim` ships old ones.

1. Check the package before the live environment — in a disposable container of the current image:
   `ssh <studio_ssh> "DOCKER_CONTEXT=colima-health docker run --rm --entrypoint sh health-os:local -c 'pip install -q --no-deps --target /tmp/x pkg==X.Y.Z && PYTHONPATH=/tmp/x python3 -c \"import pkg\"'"`
2. Update the lock following the steps above (native Studio environment — second-tenant environment).
3. pip/setuptools of the image — edit `ARG` in `docker/Dockerfile`, same cooldown applies.
4. Build the image under a separate tag from the working copy, without touching `health-os:local`:
   `git archive $(git stash create) | ssh <studio_ssh> 'DOCKER_CONTEXT=colima-health DOCKER_BUILDKIT=0 docker build -q -f docker/Dockerfile --build-arg TARGETARCH=arm64 -t health-os:depcheck -'`
5. Audit inside the built image: `docker run --rm --entrypoint python3 health-os:depcheck -m pip_audit`
   — there should be no vulnerabilities (other than those in `_PIP_AUDIT_ALLOW`).
6. Commit; the deploy will build the working image on its own. After deploy — the same audit in `health-cron-1`.

## Advisory without a fix

If a vulnerability has no fixed version or is not applicable (the feature is not
used, the surface is unreachable) — add the id to `_PIP_AUDIT_ALLOW`
(`security_sensors.py`) with a justifying comment. An empty allowlist is
the normal state; every entry is reviewed by the quarterly checklist.

## Restoring the environment from scratch

`/opt/homebrew/bin/python3.11 -m pip install -r requirements.txt --break-system-packages`

## What NOT to do

- Do not update "everything at once" (`pip install -U ...` as a list) — if a regression occurs,
  binary-searching for the culprit is impossible.
- Do not edit versions only in the lock without installing on Studio — the lock describes
  the actual environment, not the desired one (otherwise it lies, see
  feedback_stale_intermediate_layer).
- Do not run pip-audit from the nightly integrity job — the network at 07:50 gates the morning
  report; network is available only in the weekly job.
