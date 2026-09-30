<!-- translation-of: docs/explanation/how_gp_works.md sha256:19e8344d93db -->
**English** · [Русский](how_gp_works.md)

# How the GP works: cycle, MDT, and trend logic

> **Document type:** Explanation (Diataxis) — explains the decision-making architecture.
> For the run schedule: [data_flow.md](data_flow.md).
> For the methods reference: [ARCH_SNAPSHOT.md](../../ARCH_SNAPSHOT.md).

---

## Daily cycle (08:30)

```
gp_agent.generate_daily_report(target=today)
  1. Sets sleep_date=today, activity_date=yesterday
     (Oura records sleep on the wake-up date, activity one day earlier)
  2. Runs all 4 LifestyleAgents → briefs dict
  3. Builds the GP context (7 days + trends + labs + genome)
  4. Adds the drift report if there is one
  5. Claude Sonnet → generate_daily_report (1 claude call)
  6. _triage_metric() for key metrics:
     emerging_trend or persistent_low → _auto_create_metric_task() (with deduplication)
  7. _review_problem_list() in the background after generation
```

## Weekly GP (Monday 07:00)

```
gp_agent.generate_weekly_report(run_mdt=False)
  1. Reads the MDT from agent_reports for the last 3 days
     (MDT ran Sunday 23:00, GP reads Sunday→Monday morning)
  2. _build_gp_context(7 days)
  3. Adds the MDT synthesis to the context
  4. Claude Sonnet (GP_SYSTEM_PROMPT) → report (4096 tokens)
  4a. _guard_absent_claims — guard against "never tested" vs the canon (see below)
  5. _save_gp_report("weekly")
  6. _review_problem_list() in the background
```

### An absent row is read as an absent test (2026-08-30)

The lab block in the physician context shows the **entire** `lab_results` canonical store for 730 days
(~90 analytes, ~5 KB), not a manual list. Until 08-30, `gp_context` contained the allowlist
`_GP_KEY_LABS` with 24 names (including the dead key `Cholesterol` instead of the canonical
`Cholesterol_Total`). The model saw only that list — and called everything absent from it
"never tested": the GP's weekly report claimed several analytes had "not been
tested at all / ever / even once," although rows for those analytes existed in the database.
A task in `problem_list` with `next_action` "check <analytes>" served as a cue —
the model combined "task exists" + "not on the list" = "not done" and repeated this
for several weeks in a row.

This is the same class as an earlier case involving another test: the result existed in a document but never reached the physician.
Then the input was fixed and three names were added to the list — the second occurrence involved
other names. The list was removed: the LLM must see the data, not a selection, otherwise it lies
by construction.

Two guards sit alongside it. `_check_lab_freshness` now prints `⚠ нет данных [medium]`
for a test without a row at any priority (previously, medium was silently skipped — analytes
with a priority=medium encounter row received neither ✓ nor ❌). And the post-validator
`gp_context.absent_claims_contradicted`: the phrase "never tested / not once / not checked"
in the same clause as the name of an analyte with a canonical row → one regeneration
with an explicit correction; if the retry still lies → the report **is not saved**, the failure
goes to an ERROR log and the returned text (`⛔ …`). The guard judges text against data, so
it catches the entire class — wherever the conclusion came from (allowlist, stale `next_action`,
carryover from a previous report). The name is tied to the phrase within its own part of the clause (before/after
`, ` / ` — `); otherwise, the phrase "<analyte A> — never tested with elevated <analyte B>" would blame
analyte B.

What else surfaced after removing the list: the freshness block showed overdue tests for several routine
biochemistry analytes (schedule encounter rows that the allowlist's
`seen` had not seen). This is not a regression — it is what had been hidden.

## MDT consilium (Sunday 23:00)

```
gp_agent.run_specialists_and_save()
  └── wellally_consult.run_mdt_consultation()
        Specialists: oncologist, cardiologist, nutritionist + others from prompts/
        Each receives the same data_package (7 days)
        Parallel Claude calls — independent assessments, not consensus
        Synthesis of all opinions → save_agent_report(agent_type="mdt")
```

---

## Trend logic

### detect_metric_drift() — detecting a sustained deviation

**Metrics:** deep_min, rem_min, hrv_ms, readiness, steps

```
1. Take 60 days of data
2. Baseline = mean over the first 30 days (≥ 10 points)
3. Current = mean over the last 7 days
4. Threshold = 15% deviation from baseline
5. Streak = how many consecutive days the deviation is > 15% in one direction
6. If streak ≥ 3 → record a drift
```

**Severity:** Mild (15–25%) / Moderate (25–40%) / Severe (>40%)

**Why streak, not absolute value:** one bad day (streak=1) or two
in a row (streak=2) do not reach the threshold. The system responds only to a sustained
deviation for at least 3 consecutive days. A one-time spike will not appear in any report.

**Where the result goes:**
- `generate_hypothesis_from_drift()` — automatically creates a hypothesis
- `format_drift_report()` → inserted into GP context
- streak < 3 → not mentioned anywhere

### _triage_metric() — classification for automatic tasks

Goes deeper than drift, working at the level of individual metrics:

- **fluctuation:** |z| < 1.5 and small slope → a one-time spike, no task created
- **emerging_trend:** slope > 1% of mean over 10+ days → an "[AUTO]" task
- **persistent_low:** median of the last 7 days < 85% of the historical median → an "[AUTO]" task

---

## How the GP sees other agents' reports

| Who looks | What they see | What they do not see |
|---|---|---|
| GP daily | Lifestyle ⚠ flags through aggregation | Lifestyle agents' reports |
| GP weekly | MDT synthesis | Specialists' individual opinions |
| GP monthly | Last 4 MDTs over 35 days | — |
| MDT specialists | The same data_package | Colleagues' opinions |
| Lifestyle agents | Their own domain | GP reports |

Principle: no cross-reading within one level. Information moves
bottom-up through aggregation, not through direct access to raw reports.
