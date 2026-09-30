<!-- translation-of: docs/how-to/update_constitutions.md sha256:03040c387989 -->
**English** · [Русский](update_constitutions.md)

# How to update the health constitutions

> **Document type:** How-to (Diataxis).
> Structure and tone rules: [CONSTITUTION_RULES.md](../../CONSTITUTION_RULES.md).
> An explanation of why they exist: [docs/explanation/how_gp_works.md](../explanation/how_gp_works.md).

---

## When to update

Normally, never manually. Since September 25, 2026 (the owner's decision: “rebuild only on triggers,
when new inputs appear”), every Sunday at 05:30 (05:50 for the tenant), launchd
`com.larry.health.constitutions[.partner]` calls `generate_constitutions.py --if-changed`.
It compares an input fingerprint — the set of confirmed associations, clinical phases, blood draw dates,
medical periods, genome size — and rebuilds all five domains only if
one of these has changed. Weekly drift in averages does not count as an input change. What changed
is written to `~/health_constitutions*.log`.

Run it manually when you need to rebuild one domain or the prompt itself has changed:

```bash
ssh <studio_ssh> "/opt/homebrew/bin/python3.11 ~/health_scripts/generate_constitutions.py sleep"
```

If the scheduled job is silent for more than 8 days, the nightly monitor reports “constitution rebuild on new
inputs is silent” — check whether launchd is loaded and what the log says.

---

## Prerequisite: fresh longitudinal_analysis

Constitutions depend on `longitudinal_analysis` in `agent_reports`. Before generating, check:

```bash
ssh <studio_ssh> "python3.11 ~/health_scripts/generate_constitutions.py --check-only"
```

If `longitudinal_analysis` is outdated, run it first:

```bash
ssh <studio_ssh> \
  "nohup /opt/homebrew/bin/python3.11 ~/health_scripts/longitudinal_analysis.py \
   > /tmp/longitudinal.log 2>&1 &"
# Takes 5–10 minutes. Watch: tail -f /tmp/longitudinal.log
```

---

## Run generation

```bash
# All five domains (~10–15 min, run via nohup):
ssh <studio_ssh> \
  "nohup /opt/homebrew/bin/python3.11 ~/health_scripts/generate_constitutions.py \
   > /tmp/constitutions.log 2>&1 &"

# One domain (faster, ~2–3 min):
ssh <studio_ssh> \
  "/opt/homebrew/bin/python3.11 ~/health_scripts/generate_constitutions.py sleep"
```

Available domains: `sleep`, `nutrition`, `stress`, `nervous_system`, `movement`

Monitor progress:
```bash
ssh <studio_ssh> "tail -f /tmp/constitutions.log"
```

---

## What happens during generation

1. The script reads SNPs from `genetic_variants` (not `promethease_variants` — that table is empty)
2. Reads clinical history from `periods` (medical types only)
3. Reads longitudinal analysis from `agent_reports.raw_output`
4. Two-step generation:
   - Step 1: `_build_prompt()` → Claude Opus → fresh text
   - Step 2: if a previous version exists → `_build_diff_prompt()` → insert `## Что изменилось`
5. After all domains: `_run_alert_review()` → analyzes the diff → alerts in Telegram

**⚠️ Generation stops if:**
- SNP count = 0 (not a single variant for the domain)
- No longitudinal data in agent_reports

---

## After generation

Files are updated in `constitutions/`. Check:

```bash
ssh <studio_ssh> "head -5 ~/health_scripts/constitutions/sleep.md"
# There should be a fresh date in **Обновлено:** ("Updated:")
```

Sync to MacBook:
```bash
rsync -av <studio_ssh>:~/health_scripts/constitutions/ \
      ~/health_scripts/constitutions/
```

---

## Manually edit a constitution

Constitutions are narrative documents; you can edit them manually.
The structure is required (SWOT + Narrative + Basic recommendations + Recommendations for specialists).
Section format: [CONSTITUTION_RULES.md](../../CONSTITUTION_RULES.md).

After a manual edit, record it in git:
```bash
cd ~/health_scripts && git add constitutions/ && git commit -m "update: constitution [domain]"
```


---

## Epistemic discipline (ON by default)

Since June 20, 2026, generation applies confidence calibration by default
(`epistemic_skill/`) — you do not need to change anything. To disable it for a specific run:

```bash
ssh <studio_ssh> \
  "EPISTEMIC_DISCIPLINE=off /opt/homebrew/bin/python3.11 ~/health_scripts/generate_constitutions.py sleep"
```

The same discipline applies to the consilium coordinator's synthesis (`wellally_consult`).
Why and how it works: [docs/explanation/epistemic_discipline.md](../explanation/epistemic_discipline.md).
