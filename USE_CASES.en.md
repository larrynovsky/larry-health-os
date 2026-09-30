<!-- translation-of: USE_CASES.md sha256:f6d0177de1f9 -->
**English** · [Русский](USE_CASES.md)

# USE_CASES.md — Larry Health OS use cases

**Version:** 0.3 (final working version)  
**Date:** 2026-05-08

This document describes **what the system must do correctly**. It is not a
list of tests already implemented, and it does not pretend that all aspects of medical quality
can be checked with `assert` statements.

The main principle: **first confirm exactly what we are testing, then write the test**.

---

## 1. Purpose

Larry Health OS is the owner's personal medical system (owner tenant):
it imports data from Oura, Apple Health, medical documents, and the genome;
produces daily, weekly, and monthly medical conclusions; manages tasks,
checkins, hypotheses, a problem list, and Council/MDT consultations.

A use case here is not a set of “test steps”. It is a **practical purpose**: observable behavior
that has value for the user or for medical care.

---

## 2. Document rules

### 2.1 UC status

Each use case has a status:

| Status | Meaning | What the test system does |
|---|---|---|
| `implemented` | The behavior already exists in the code | Regression tests can be created |
| `partial` | Some behavior exists, some is a gap | Create tests for the implemented part + a gap-report |
| `intended` | Desired behavior, not yet implemented | Not a regression. Mark as an expected gap |
| `speculative` | An idea that requires a decision | Do not generate tests |

### 2.2 Confirmation

Each UC has a confirmation:

| Confirmation | Meaning |
|---|---|
| `proposed` | Proposed by the system or a person, not yet confirmed |
| `confirmed` | Confirmed: “yes, this is exactly what we are checking” |
| `rejected` | We are not testing this / it is not needed |

**Tests are generated only for `confirmed`.**

### 2.3 Check types

| Type | Meaning |
|---|---|
| `check` | A deterministic check of a module or invariant |
| `integration` | A check of how several modules connect through data |
| `e2e_mock` | An end-to-end scenario with mocked external systems |
| `llm_review` | LLM-as-judge as an auxiliary check, not a source of truth |
| `manual_charter` | A risk-oriented route for human review |
| `meta` | Self-updating catalog and tests |

### 2.4 Oracles

We use the RST distinction between checking and testing:

- `B` — negative invariants: what the system must NOT do.
- `E` — cross-check against deterministic truth: database, context, JSON, API mock.
- `C` — a structural snapshot where structure matters more than exact wording.
- `D` — LLM-as-judge with explicit questions. D is never the only oracle for P0.
- `H` — Human charter: risk-oriented human review guided by a list of questions.
  Used where automation does not work: tone, oncology context, «not a verdict».
  H never replaces B/E for the testable part of a UC and never returns
  a false pass/fail — its result is always notes and decisions.

### 2.5 Thematic aliases

Frequently mentioned UC have a second name — a thematic prefix. This
makes the subject clear at a glance (“what is this about?”). The structural ID (`UC-A-01`)
remains primary; the alias serves as a cross-reference.

| Structural ID | Alias | Meaning |
|---|---|---|
| `UC-I-01` | `UC-LANG-001` | Outward-facing language is Russian |
| `UC-I-02` | `UC-SEC-001` | Telegram fail-closed |
| `UC-I-03` | `UC-NULL-001` | NULL ≠ 0 |
| `UC-I-08` | `UC-ONCO-001` | Oncology context in all conclusions |
| `UC-I-09` | `UC-GENOME-FRAME-001` | The genome is a framework, not a verdict |
| `UC-A-01` | `UC-LABS-001` | Lab document → Council full chain |
| `UC-A-04` | `UC-OURA-001` | Sleep by wake-up date |
| `UC-B-09` | `UC-CORR-001` | Correlations without invented r values |
| `UC-D-05` | `UC-LABS-AGE-001` | Stale labs/genome → date annotation |
| `UC-G-01` | `UC-TASK-001` | GP → tasks → Reminders with dedup |
| `UC-H-01` | `UC-MDT-001` | Council Round A/B |
| `UC-J-01` | `UC-SELFTEST-001` | Code diff → UC proposal |
| `UC-J-02` | `UC-SELFTEST-002` | Confirmation before a test |
| `UC-K-01` | `UC-UPSTREAM-PROMPT-001` | Specialist prompts upstream tracking |

### 2.6 Expected gaps

If `status=intended` or `partial`, a failing result means not “the test is broken”,
but “the code does not match the confirmed intent”. These checks go into a gap-report,
not the blocking regression suite.

### 2.7 Conflict between USE_CASES and TESTING_CONTRACTS

If `USE_CASES.md` and `TESTING_CONTRACTS.md` conflict, the system creates
`needs_review`. No new test is generated until a human confirms it.

---

## 3. UC metadata

The machine-readable version will live in `uc_index.yaml`. For each UC:

```yaml
UC-I-01:
  title: "External language is always Russian"
  status: implemented
  confirmation: confirmed
  priority: P0
  testability: check
  oracle: [B, E]
  modules:
    - telegram_bot.py
    - wellally_consult.py
    - hai_core.py
  data:
    - ".claude/specialists/*.md"
  tests: []
  risk: high
  notes: "Chinese specialist prompts are allowed only internally."
```

A UC with no entry in `uc_index.yaml` is considered **uncovered**, even if it is described
in this file.

---

## 4. UC catalog

### A. Data import and recognition

| ID | Practical purpose | P | Type | Status | Confirmation |
|---|---|---|---|---|---|
| `UC-A-01` | Lab PDF/JPEG → recognition → confirmation → database write | P0 | e2e_mock | partial | proposed |
| `UC-A-02` | A non-lab PDF is classified into the correct domain and does not trigger Council | P0 | check | partial | proposed |
| `UC-A-03` | Apple Health / HAE → `daily_metrics`, merge without overwriting Oura | P0 | integration | implemented | confirmed |
| `UC-A-04` | Oura: sleep by wake-up date, activity/HRV by the correct date | P0 | check | implemented | confirmed |
| `UC-A-05` | Withings BP freshness and an alert on sync failure | P1 | check | intended | proposed |
| `UC-A-06` | Calendar/KAYAK trips → `periods`, manual periods are not overwritten | P1 | integration | partial | proposed |
| `UC-A-07` | Financial documents do not enter medical data | P1 | check | implemented | proposed |

### B. Analytics and reports

| ID | Practical purpose | P | Type | Status | Confirmation |
|---|---|---|---|---|---|
| `UC-B-01` | Morning deterministic report: numbers in the text = numbers in the database | P0 | check | partial | proposed |
| `UC-B-02` | GP daily sees context for 7/14/30/90 days, labs, problem list, genome | P0 | integration | partial | proposed |
| `UC-B-03` | `_triage_metric` — retired 28.09: trends go into review, not tasks | P2 | check | intended | rejected |
| `UC-B-04` | System work is not added to the person's task list | P0 | check | implemented | confirmed |
| `UC-B-05` | Lifestyle agents stay silent without domain data | P0 | check | implemented | proposed |
| `UC-B-06` | Lifestyle agents receive a domain-specific genome/promethease block | P1 | integration | implemented | proposed |
| `UC-B-07` | GP weekly synthesizes MDT, labs, problem list, tasks, genome, freshness | P0 | integration | partial | proposed |
| `UC-B-08` | GP monthly provides a 30/90-day strategy and problem list review | P1 | integration | partial | proposed |
| `UC-B-09` | Tracker correlations contain no invented r values | P0 | check | partial | proposed |
| `UC-B-10` | Evening checkin: 1 contextual question, up to 3 turns, structured extraction | P1 | e2e_mock | partial | proposed |
| `UC-B-11` | Checkin is visible to GP daily the next morning | P0 | integration | partial | proposed |

### C. Genome

| ID | Practical purpose | P | Type | Status | Confirmation |
|---|---|---|---|---|---|
| `UC-C-01` | 23andMe TSV is parsed fully, double-tab is not lost | P0 | check | implemented | proposed |
| `UC-C-02` | Annotator + ClinVar + FUNCTIONAL_WHITELIST | P0 | integration | implemented | proposed |
| `UC-C-03` | Genome update monthly: ClinVar changes are logged, significant upward changes are explained | P0 | e2e_mock | partial | proposed |
| `UC-C-04` | Genome context for AI is domain-specific, without irrelevant noise | P1 | check | implemented | proposed |

### D. Alerts and safety

| ID | Practical purpose | P | Type | Status | Confirmation |
|---|---|---|---|---|---|
| `UC-D-01` | `safety_net.lab_alerts` deterministically catches fresh lab flags | P0 | check | partial | proposed |
| `UC-D-02` | `evaluate_domain_need`: percentile + floors + constraints | P0 | check | implemented | proposed |
| `UC-D-03` | An urgent safety alert is sent before the main report | P0 | e2e_mock | partial | proposed |
| `UC-D-04` | `triage_agent` fixes WARN, does not repair FAIL, is idempotent by day | P0 | e2e_mock | implemented | confirmed |
| `UC-D-05` | Stale labs/genome are explicitly marked with a date and reduced confidence | P0 | check | partial | confirmed |

### E. Constitutions

| ID | Practical purpose | P | Type | Status | Confirmation |
|---|---|---|---|---|---|
| `UC-E-01` | 5 constitutions: two-step generation of text + diff, does not start without SNP/longitudinal data | P1 | integration | implemented | proposed |
| `UC-E-02` | SNP source = `genetic_variants`, not the empty `promethease_variants` | P0 | check | implemented | proposed |
| `UC-E-03` | `_run_alert_review()` raises critical findings after the generation of 5 constitutions | P1 | check | implemented | proposed |

### F. Hypotheses and experiments

| ID | Practical purpose | P | Type | Status | Confirmation |
|---|---|---|---|---|---|
| `UC-F-01` | A drift streak creates a hypothesis with `mechanism + prediction + test` | P1 | check | partial | proposed |
| `UC-F-02` | A hypothesis follows the lifecycle: open → testing → confirmed/rejected | P1 | check | partial | proposed |

### G. Tasks and Reminders

| ID | Practical purpose | P | Type | Status | Confirmation |
|---|---|---|---|---|---|
| `UC-G-01` | GP report → tasks → Reminders, dedup by fingerprint | P0 | e2e_mock | partial | proposed |
| `UC-G-02` | `reminders_sync.py` closes completed tasks every 3h | P1 | integration | implemented | proposed |
| `UC-G-03` | The problem list is managed: GP proposes, a human approves/rejects | P1 | integration | partial | proposed |
| `UC-G-04` | Active protocols + patient_constraints actually affect recommendations | P1 | integration | implemented | proposed |

### H. `/consult` v2 — Deliberative Council

| ID | Practical purpose | P | Type | Status | Confirmation |
|---|---|---|---|---|---|
| `UC-H-01` | `/consult <Q>` → 13 participants, Round A/B, coordinator | P0 | e2e_mock | implemented | proposed |
| `UC-H-02` | `user_qa.append()` strictly before `_build_data_package()` and before the rounds | P0 | check | implemented | confirmed |
| `UC-H-03` | Round B actually uses the opinions from Round A | P1 | manual_charter | partial | proposed |
| `UC-H-04` | Multi-turn consult retains user_qa between turns; restart resets it explicitly | P1 | integration | partial | proposed |

### I. Cross-functional invariants

| ID | Practical purpose | P | Type | Status | Confirmation |
|---|---|---|---|---|---|
| `UC-I-01` | Outward-facing language is always Russian; Chinese prompts are internal only | P0 | check | implemented | confirmed |
| `UC-I-02` | Telegram fail-closed: only `OWNER_CHAT_ID` | P0 | check | implemented | confirmed |
| `UC-I-03` | NULL != 0 throughout the reporting layer | P0 | check | implemented | confirmed |
| `UC-I-04` | `integrity_tests --json` blocks the morning report on FAIL | P0 | e2e_mock | partial | proposed |
| `UC-I-05` | Backup does not break SQLite/WAL or create split-brain | P0 | check | partial | proposed |
| `UC-I-06` | `send_long` splits Telegram messages at paragraph boundaries without truncation | P1 | check | implemented | proposed |
| `UC-I-07` | Single-primary: only Studio writes the production DB | P0 | manual_charter + check | implemented | confirmed |
| `UC-I-08` | Oncology context is present in all LLM conclusions | P0 | manual_charter | partial | proposed |
| `UC-I-09` | The genome is a framework for interpretation, not a verdict | P1 | manual_charter | partial | proposed |

### J. Meta — Catalog self-maintenance

| ID | Practical purpose | P | Type | Status | Confirmation |
|---|---|---|---|---|---|
| `UC-J-01` | Code/doc diff → UC proposal, without automatically writing tests | P0 | meta | intended | confirmed |
| `UC-J-02` | Explicit confirmation of “exactly what we are testing” before generating a test | P0 | meta | intended | confirmed |
| `UC-J-03` | Every confirmed UC is linked to modules/data/tests/oracle/risk in YAML | P0 | meta | intended | proposed |

### K. Upstream Tracking — External artifacts

The system uses external or semi-external elements that change independently
of our code: Chinese specialist prompts, lifestyle prompts, Anthropic models,
ClinVar/MyVariant, PubMed, Oura/Apple schemas, Telegram API, macOS Reminders/Calendar,
guideline threshold windows.

| ID | Practical purpose | P | Type | Status | Confirmation |
|---|---|---|---|---|---|
| `UC-K-01` | Chinese specialist prompts are tracked by upstream diff, with manual application only | P0 | e2e_mock | partial | confirmed |
| `UC-K-02` | Lifestyle prompts are tracked separately, with hash and diff | P1 | check | intended | proposed |
| `UC-K-03` | Anthropic model routing and deprecation are monitored | P1 | check | intended | proposed |
| `UC-K-04` | ClinVar/MyVariant schema drift is not disguised as “no data” | P0 | check | partial | proposed |
| `UC-K-05` | PubMed E-utilities degradation marks PMID verification as degraded | P1 | check | intended | proposed |
| `UC-K-06` | The WellAlly upstream weekly check shows a diff and does not apply it silently | P1 | e2e_mock | partial | proposed |

---

## 5. Detailed P0 UC

Below are the P0 contracts. For `intended`/`partial`, this is not a blocking regression test,
but an acceptance target + gap-report.

### UC-A-01 — Lab PDF/JPEG → Recognition → Confirmation → Database write

**Status:** `partial`  
**Confirmation:** `proposed`  
**Owner:** `import_all.py`, `lab_extractor.py`, `health_db.py`, `telegram_bot.py`

**Practical purpose:** I drop in a lab report; the system extracts the numbers, shows me a card
for confirmation, and saves them in the database. The data then becomes available to GP/Council
on schedule and through manual `/consult`.

**Given:**
- the watcher is running;
- the document looks like a laboratory PDF/JPEG;
- previous labs may or may not exist — both cases must be handled correctly;
- `genetic_variants` may be empty or nonempty, but the system must not invent a genome.

**When:** a new lab PDF/JPEG appears in the health folder.

**Then:**
1. `import_all.py` classifies the document as lab only with sufficient signals (≥3 markers).
2. Structured JSON is extracted: `{name, value, unit, ref_low, ref_high, flagged, confidence, raw_line}`.
3. Low confidence (`confidence="low"`) requires user approval and displays `raw_line` on the card.
4. After approval, data enters `lab_results` without duplicates (idempotency by content_hash).
5. `set_import_status("oncology_pdf")` is updated only after successful import.

**B:**
- <3 lab signals → lab import does not start; the document is classified into another domain (UC-A-02).
- a repeated file by content hash does not create a duplicate.
- approval rejected (`❌`) → no write to `lab_results`.

**E:**
- Values in `lab_results` match OCR/raw_line after decimal normalization.
- After approval, `safety_net.lab_alerts()` sees the new values.

**Self-consistency check** (instead of snapshot, 2026-05-08): run `lab_extractor` twice on a reference PDF from each laboratory; the key fields `{name, value, unit, flagged}` must match bit for bit between runs. Differences = extractor instability, not «the reference is outdated».

**Outside this UC, deferred to BACKLOG:** automatic Council launch after
approval. See BACKLOG.md → «Automatic Council after lab import» (2026-05-08).
Currently: after approval, Council runs either on schedule (Sun 23:00 MDT) or
manually through `/consult`. Tested separately through UC-H-01.

---

### UC-A-02 — Non-lab PDF is classified and does not go to Council

**Status:** `partial`  
**Confirmation:** `proposed`  
**Owner:** `import_all.py`

**Then:**
- discharge/pathology/biopsy/PET/nutrition guide documents are saved in their respective domains;
- hospital bills and financial docs are skipped;
- Council does not run.

**B:** an OCR error must not create empty “valid” medical JSON.  
**E:** `index.json.imported_files` must not reference nonexistent data files.

---

### UC-A-03 — Apple Health / HAE → Daily Metrics

**Status:** `partial`  
**Confirmation:** `confirmed`  
**Owner:** `import_apple_health.py`, `health_db.py`

**Then:**
- HAE/Apple Health data for recent days is read and merged into `daily_metrics`;
- existing Oura fields are not overwritten;
- `set_import_status("apple_health")` is updated only after successful import;
- missing BP remains `NULL`, not 0.

**B:** iCloud-conflicted/evicted files do not crash the entire import.  
**E:** steps and key metrics in the database match the source within the allowed tolerance.

**Confirmed risk (validation 2026-05-08):**
- `import_apple_health.py:248` — `existing.update(summary)` is a shallow merge.
  If HAE sends JSON with `{"hrv": null, "sleep_total": 7.5}` and the existing
  file contains `{"hrv": 25, "steps": 8000}` (from Oura), after `update()` we get
  `{"hrv": null, "sleep_total": 7.5, "steps": 8000}`. **Oura HRV is overwritten with null.**
- A filter is needed: «a new value overwrites only if `not None`».
- Test: pre-fill `hrv=25` → import HAE without `hrv` → `hrv` remains `25`.

**Additional note (not critical):** `import_daily_new_automation()` (line 282) uses
the `New Automation/` folder, despite the known rule: «**NOT** `New Automation/` — that folder
is not used». Possibly legacy code; needs review for removal.

---

### UC-A-04 — Oura: sleep date and activity date

**Status:** `implemented`  
**Confirmation:** `proposed`  
**Owner:** `import_oura.py`, `gp_agent.py`, `health_db.py`

**Then:**
- sleep is recorded on the wake-up date;
- the daily report uses `sleep_date=today`;
- activity/steps/readiness/stress are taken from the date for which the source is actually complete;
- missing data is not interpreted as a real 0.

**B:** “0 hours of sleep” or “HRV 0” is prohibited in the narrative if the source is missing.  
**E:** `daily_metrics[today].sleep_total` matches the Oura sleep summary ending today.

---

### UC-B-01 — Deterministic Morning Report: numbers = database

**Status:** `partial`  
**Confirmation:** `proposed`  
**Owner:** `morning_report.py` — the module was retired on 13.07 and removed on 30.09.2026; the scenario went with it, the morning text is written by the GP brief

**Then:**
- the deterministic report does not call an LLM;
- numbers in the text come from `daily_metrics` or an explicitly named window;
- `triage_agent.run_triage()` runs before generation;
- the report is saved to `data/reports/YYYY-MM-DD.md`.

**B:** a NULL metric → “no data”, not zero.  
**E:** a parser of numbers from the report matches each number to the database/window.

---

### UC-B-02 — GP Daily sees context for 7/14/30/90

**Status:** `partial`  
**Confirmation:** `proposed`  
**Owner:** `gp_agent.py`, `health_db.py`, `genome_context.py`

**Then:**
- GP context contains current metrics, 7/30/90 statistics, labs freshness,
  problem list, protocols/constraints, genome block, workouts/stress/checkins where available;
- GP does not draw conclusions from a missing section;
- stale labs/genome are marked with a date.

**B:** `has_findings=1` without evidence/PubMed where the contract requires PubMed results in warning/fail.  
**E:** numerical claims in the GP text must be reconstructible from the supplied context.

---

### UC-B-03 — `_triage_metric` (retired)

**Status:** `intended`  
**Confirmation:** `rejected`  

Retired 28.09 by the owner's decision: the trend classifier was needed only for automatic tasks (UC-B-04
in its previous version) and confused direction — it reported «deteriorating» even when the metric increased.
Trends for 7/14/30/90 days go into the weekly review context (`gp_context._build_trends_block`).

---

### UC-B-04 — System work is not added to the person's list

**Status:** `implemented`  
**Confirmation:** `confirmed`  
**Owner:** `gp_agent.py`, `jobs/scheduled.py`

**Then:** no production call to `save_task` creates a task of type `analysis`/`review`
(«analyze changes», «review hypotheses») — these tasks have no human executor.
Owner's decision 28.09: the person's list contains only what they can do themselves.

**B:** the sentinel detects an injected call with type `analysis`.

---

### UC-B-05 — Lifestyle Agents stay silent without data

**Status:** `implemented`  
**Confirmation:** `proposed`  
**Owner:** `lifestyle_agents.py`

**Then:**
- the agent returns `None`/an empty result if there is no domain data;
- a genome block alone does not produce a brief;
- GP receives a list of missing agents.

**B:** missing sensors do not become “poor sleep/low activity”.  
**E:** a nonempty brief requires minimal domain data to be present.

---

### UC-B-07 — GP Weekly

**Status:** `partial`  
**Confirmation:** `proposed`  
**Owner:** `gp_agent.py`, `wellally_consult.py`, `task_agent.py`

**Then:**
- weekly uses MDT synthesis when available;
- if MDT is missing, weekly explicitly marks the gap and uses the available context;
- tasks from the report are extracted and deduplicated;
- weekly is not the sum of daily reports.

**E:** weekly numerical claims match the actual window.

---

### UC-B-09 — Correlations without invented r

**Status:** `partial`  
**Confirmation:** `proposed`  
**Owner:** `longitudinal_analysis.py`, `gp_agent.py`

**Then:**
- if the text contains a numerical `r=...`, it must exist in `agent_reports.raw_output`,
  be recomputed from the data, or be omitted;
- without a source, a “possible pattern” can be discussed, but not an exact correlation.

**B:** exact r values estimated “by eye” are prohibited.  
**E:** `r` is checked within a tolerance on the same window and the same metrics.

---

### UC-B-11 — Checkin is visible to GP

**Status:** `partial`  
**Confirmation:** `proposed`  
**Owner:** `checkin_agent.py`, `health_db.py`, `gp_agent.py`

**Then:** the evening checkin is saved in `checkins`/`context_events` and visible
to the next GP daily.

**E:** if `COUNT(checkins WHERE date=yesterday) > 0`, GP context contains a checkin section.

---

### UC-C-01 — 23andMe TSV is parsed fully

**Status:** `implemented`  
**Confirmation:** `proposed`  
**Owner:** `genome_parser.py`

**Then:** `raw_snps` contains all TSV data rows after excluding `#` comments.

**B:** double-tab rows are not lost; `genotype="--"` is not discarded as an error.  
**E:** database count = source row count.

---

### UC-C-02 — Genome Annotator + Whitelist

**Status:** `implemented`  
**Confirmation:** `proposed`  
**Owner:** `genome_annotator.py`

**Then:**
- FUNCTIONAL_WHITELIST is included in `genetic_variants`;
- ClinVar pathogenic/risk variants are included regardless of the whitelist;
- domain tags are filled in.

**B:** an API failure must not write semi-valid data as “benign”.  
**E:** genotype in `genetic_variants` matches `raw_snps` for reference rsid values.

---

### UC-C-03 — Genome Update Monthly

**Status:** `partial`  
**Confirmation:** `proposed`  
**Owner:** `genome_update_agent.py`, `telegram_bot.py`, `triage_agent.py`

**Then:**
- significant variants are checked;
- all changes are logged;
- significant upward movement produces a clear Russian narrative;
- if the API does not respond, the successful `run_date` is not updated.

**B:** no changes means no spam to the user.  
**E:** changed variants in the database match `genome_update_log`.

---

### UC-D-01 — Safety Net Labs

**Status:** `partial`  
**Confirmation:** `proposed`  
**Owner:** `safety_net.py`

**Then:** fresh lab flags enter the deterministic alert list and are available to GP/Council.

**B:** NULL is not treated as a flag.  
**E:** alerts ⊆ flagged lab rows.

---

### UC-D-02 — Domain Need: Percentile + Floors + Constraints

**Status:** `implemented`  
**Confirmation:** `proposed`  
**Owner:** `telegram_bot.py`, `recommendation_engine.md`, `health_db.py`

**Then:** a domain recommendation is explained through percentile signals,
absolute floors, and active constraints/protocols.

**B:** without a baseline, no confident claim about a percentile is possible.  
**E:** the reasons for each alert can be reconstructed from the database.

---

### UC-D-03 — Urgent safety alert before the report

**Status:** `partial`  
**Confirmation:** `proposed`  
**Owner:** `gp_agent.py`, `telegram_bot.py`, `safety_net.py`

**Then:** the urgent/critical safety message is sent before the regular daily report.

**B:** a safety_net failure must not disappear silently.  
**E:** safety message timestamp < report timestamp.

---

### UC-D-04 — Triage Agent

**Status:** `implemented`  
**Confirmation:** `proposed`  
**Owner:** `triage_agent.py`, `run_checks.sh`, `integrity_tests.py`

**Then:**
- WARN may trigger auto-fixes or a question to the user;
- FAIL is not repaired automatically like WARN;
- the daily flag prevents repeated spam.

**E:** every triage action has a result log.

---

### UC-D-05 — Stale sources are marked with a date

**Status:** `partial`  
**Confirmation:** `proposed`  
**Owner:** all agents using labs/genome

**Then:** conclusions based on labs/genome older than the contract allows contain the source date and a limit on confidence.

**B:** confident medical conclusions based on a stale source without annotation are prohibited.  
**D:** LLM-review looks for a stale source without a date.

---

### UC-E-02 — Constitutions read `genetic_variants`

**Status:** `implemented`  
**Confirmation:** `proposed`  
**Owner:** `generate_constitutions.py`

**Then:** SNP source = `genetic_variants`; the empty `promethease_variants` is not used as a fallback.

**E:** SNP in a constitution ⊆ SNP in `genetic_variants` with a relevant domain tag.

---

### UC-G-01 — GP Report → Tasks → Reminders

**Status:** `partial`  
**Confirmation:** `proposed`  
**Owner:** `task_agent.py`, `health_db.py`, `reminders_sync.py`

**Then:**
- the task extractor returns valid task JSON;
- tasks are saved with a fingerprint;
- Reminders are created in the Health list;
- duplicates by fingerprint do not accumulate.

**Important:** the current code saves the task first, then creates the Reminder. If atomicity is needed,
that is a separate intended gap, not a current regression.

**B:** a task without content is not saved; abstract advice does not become a task.  
**E:** an open task with reminder-required must have `[task_id:N]` in Reminders or a gap flag.

---

### UC-H-01 — Council full cycle

**Status:** `implemented`  
**Confirmation:** `proposed`  
**Owner:** `wellally_consult.py`, `lifestyle_agents.py`, `telegram_bot.py`

**Then:**
- the session saves the user question/answer;
- `_build_data_package()` sees the history;
- Round A is independent;
- Round B sees colleagues' contributions;
- the coordinator synthesizes in Russian;
- Chinese specialist prompts are read as an internal artifact but do not leak outward.

**B:** an empty genome does not permit genome claims.  
**E:** numerical claims in the final output must be in the context.

---

### UC-H-02 — `user_qa.append()` before the rounds

**Status:** `implemented`  
**Confirmation:** `proposed`  
**Owner:** `wellally_consult.py`

**Then:** a new user answer enters the session before `_build_data_package()` and before `asyncio.gather`.

**E:** a unit test with 3 turns checks that the data package sees fresh `user_qa`.

---

### UC-I-01 — Outward-facing language is always Russian

**Status:** `partial`  
**Confirmation:** `confirmed`  
**Owner:** `telegram_bot.py`, `wellally_consult.py`, `hai_core.py`, `.claude/commands/*`

**Then:** all user-facing outputs — Telegram, reports, errors, `/consult`,
`/specialist`, `/query` — are in Russian.

**B:**
- CJK text must not appear in user-facing output.
- English terms, rsID, model names, and measurement units are allowed as insertions.

**E:** language detector + CJK leakage detector on outward-facing messages.

---

### UC-I-02 — Telegram Fail-Closed

**Status:** `implemented`  
**Confirmation:** `proposed`  
**Owner:** `telegram_bot.py`

**Then:** only `OWNER_CHAT_ID` passes the handlers.

**B:** an unauthorized chat receives no personal data, approval buttons, or AI responses.  
**E:** mock update with another chat_id → 0 outgoing messages containing patient data.

---

### UC-I-03 — NULL != 0

**Status:** `partial`  
**Confirmation:** `proposed`  
**Owner:** all report/agent modules

**Then:** NULL in the database = “no data”, not a real zero.

**B:** “slept 0 hours”, “HRV 0”, and similar phrases are prohibited when the source is missing.  
**E:** a mock context with all NULL values produces no claims of zero values.

**Confirmed bug (validation 2026-05-08):**
- `gp_agent.py:478` — `int((stats.get('avg_deep') or 0)*60)` in an f-string without a guard:
  when `avg_deep` is NULL for 7d, the output contains «Deep: 0 / 50 / 60 / 70 min» — a medically
  incorrect claim of 0 minutes of deep sleep.
- `gp_agent.py:389` — likewise for `s.get('deep') or 0` in the daily table output.
- `gp_agent.py:527-528` — `_avg_s = _sr["avg_stress"] or 0` in the stress/load line.

`morning_report.py` is protected through `if hrv:` / `if sleep_t:` (lines 186, 201) — there,
NULL does not become 0. Only `gp_agent.py` (Tier-1 fixes) has gaps.

---

### UC-I-04 — Integrity Gate

**Status:** `partial`  
**Confirmation:** `proposed`  
**Owner:** `run_checks.sh`, `integrity_tests.py`, launchd

**Then:** FAIL before the morning report blocks the report and sends an alert; WARN does not block.

**E:** mock FAIL → report not sent, alert sent.

---

### UC-I-05 — Backup without Split-Brain

**Status:** `partial`  
**Confirmation:** `proposed`  
**Owner:** `backup.sh`, launchd, infrastructure rules

**Then:** backup creates an atomic SQLite snapshot and does not create a second operational primary DB.

**B:** a plain copy of an active WAL database is prohibited as a production backup strategy.

---

### UC-I-07 — Single Primary

**Status:** `partial`  
**Confirmation:** `proposed`  
**Type:** `manual_charter` + a minimal guard in the code  
**Oracle:** `H` + `B` (for the guard)  
**Owner:** all import/db writers, operational discipline

**Practical purpose:** only Claude through Cowork writes to the database, from the correct machine (Studio).
There are no other writers. Protection is needed against an accidental mistake by
Claude itself, not against the system (for example, forgetting the Studio-only rule).

**Protection layers:**
1. The `BLUEPRINT.md` rule «MacBook = development, Studio = production».
2. Claude's memory (`feedback_health_scripts_protocol.md`).
3. **Guard in importers** (testable): each script that
   writes to `health.db` starts with `if socket.gethostname() != STUDIO: raise`.
   If run outside Studio, the import fails explicitly with a clear message.

**Tested (B):** a unit test for the guard — mock `socket.gethostname()` →
non-Studio → expected `RuntimeError` mentioning BLUEPRINT.md.

**Charter (H) route:** `tests/charters/CH-PRIMARY-01.md` —
if the database is in a bad state, check the WAL audit, find the last write's
timestamp, and compare it with the git log of the import scripts.

**What we do NOT do:** a database migration adding an `origin_host` column — overkill for a system with one writer.

---

### UC-I-08 — Oncology context in all LLM conclusions

**Alias:** `UC-ONCO-001`  
**Status:** `partial`  
**Confirmation:** `proposed`  
**Type:** `manual_charter`  
**Oracle:** `H`  
**Owner:** GP/Council/specialist prompts, `wellally_consult.py`, `gp_agent.py`

**Practical purpose:** conclusions must account for the PATIENT'S diagnosis, treatment, and disease status
from their own data (oncology status, therapy, tumor markers, imaging), not from
an embedded profile. The same numbers mean different things for a healthy person
and a patient with an oncology context. (brief-neutralization: specific metric values
and dates come from the patient's record and are not hardcoded in the specification.)

**Review Questions** (run monthly and after major changes to
prompts/Council/GP):

- Does the conclusion sound as if it were for a healthy person, without adjustment for the patient's diagnosis?
- Is abnormal HRV interpreted as an effect of treatment (from the patient's context), or as pathology without context?
- Is reduced deep sleep explained with treatment taken into account, or dramatized?
- Are tumor markers/imaging mentioned cautiously and with dates?
- Is the patient's current disease status considered (remission/active — from their data)?

**Red flag:** a conclusion written as if for a healthy patient, without adjustment for
the patient's treatment and oncology context.

**Related overarching route:** §6 `CH-ONCO-01` (when to run).

---

### UC-I-09 — The genome is not a verdict

**Alias:** `UC-GENOME-FRAME-001`  
**Status:** `partial`  
**Confirmation:** `proposed`  
**Type:** `manual_charter`  
**Oracle:** `H`  
**Owner:** genome narrative, constitutions, GP/Council outputs

**Practical purpose:** genomic information is a framework for interpretation, not a diagnosis
or a prognosis. A SNP with pathogenic clinical significance requires a probabilistic
explanation, not «you will definitely develop X».

**Review Questions:**

- Is the language probabilistic («may», «is associated with»), rather than categorical?
- Is a functional/benign variant causing panic?
- Is the tone one of «informing», not «predicting»?
- If a SNP is indeed pathogenic, is there an explanation that this is
  relative risk, not inevitability?

**Red flag:** «you will definitely develop X», «this means that...» instead of
«may increase the risk».

**Related overarching route:** §6 `CH-GENOME-01`.

---

### UC-J-01 — Diff → UC Proposal

**Status:** `intended`  
**Confirmation:** `confirmed`  
**Owner:** future `propose_uc.py`, `doc_agent.py`, git hook

**Then:** a code/document change proposes new or changed UC, but does not write tests
or change confirmed contracts without a human.

---

### UC-J-02 — Confirmation before a test

**Status:** `intended`  
**Confirmation:** `confirmed`

**Then:** the test generator shows what is checked, which oracle, which mocks,
and what blast radius. It writes the test only after confirmation.

---

### UC-J-03 — YAML coverage index

**Status:** `intended`  
**Confirmation:** `proposed`

**Then:** each confirmed UC has an entry in `uc_index.yaml`, and the coverage report shows:
covered / not covered / expected gap / needs review.

---

### UC-K-01 — Specialist Prompts Upstream Tracking

**Status:** `intended`  
**Confirmation:** `confirmed`  
**Owner:** future `upstream_watcher.py`, `.claude/specialists/*.md`

**Practical purpose:** Chinese specialist prompts are part of the system. Their updates may
change medical behavior, so the system must track upstream,
show the diff, and apply changes only manually.

**Then:**
1. The external dependencies registry contains source, local path, approved hash,
   current hash, last_checked, last_applied.
2. A weekly check compares upstream and the local copy.
3. The diff is sent to Telegram/the report.
4. Apply only after manual confirmation.
5. After application, related UC run: Russian outward-facing language, Council, oncology context.

**B:**
- a prompt update is not applied silently;
- a local override is not overwritten;
- an upstream failure does not update `last_check_ok`.

**E:** local hash = approved hash, or a pending update is explicitly registered.

---

### UC-K-04 — ClinVar/MyVariant Schema Drift

**Status:** `partial`  
**Confirmation:** `proposed`  
**Owner:** `genome_annotator.py`, `genome_update_agent.py`

**Then:** an API schema change is not disguised as “no variants” or “benign”.

**B:** missing required fields → degraded/warn, not silent success.  
**E:** a mock of a changed schema triggers an upstream_drift warning.

---

## 6. Charters

A charter is testing, not a check. Its result is notes and decisions, not
a false automated pass/fail.

### CH-ONCO-01 — Oncology context

Review GP daily/weekly/monthly and Council (for a patient with an oncology context — from their data):

- whether the patient's diagnosis and stage are considered (from the problem list);
- whether past therapy and the post-chemotherapy context are considered;
- abnormal HRV is neither treated as «normal for a healthy person» nor dramatized;
- reduced deep sleep is explained with treatment taken into account;
- tumor markers/imaging are used cautiously and with dates.

Run: monthly and after major changes to prompts/Council/GP.

### CH-DAILY-01 — Does not dramatize a single outlier

Take days with an isolated poor HRV/sleep reading. Check that GP distinguishes noise from a trend
and maintains a calm medical tone.

### CH-GENOME-01 — The genome is not a verdict

Review genome narrative and constitutions: a probabilistic tone, no panic,
no “you will definitely develop X”.

### CH-MDT-01 — Round B hears colleagues

Compare Round A/B in several consultations:

- whether there is agreement/disagreement with colleagues;
- whether positions change;
- whether Round B mechanically repeats Round A.

### CH-CORR-01 — Correlations are not magic

If there is an r value, `UC-B-09` checks it. If there is no number, a human checks
whether this is cherry-picking coincidences.

---

## 7. External Dependencies

Future file: `external_dependencies.yaml`.

```yaml
specialist_prompts:
  oncology:
    local_path: ".claude/specialists/oncology.md"
    upstream: "TBD"
    language: "zh"
    approved_sha256: "TBD"
    current_sha256: "TBD"
    last_checked: null
    last_applied: null
    local_override: false
    impacted_uc:
      - UC-H-01
      - UC-I-01
      - UC-I-08
      - UC-K-01
```

Minimal set of upstream dependencies:

- `.claude/specialists/*.md` — Chinese medical prompts;
- `.claude/specialists/lifestyle_*.md` — lifestyle prompts;
- Anthropic model routing;
- ClinVar/MyVariant API schemas;
- PubMed E-utilities;
- Oura API schema;
- Apple Health / HAE schema;
- Telegram Bot API;
- macOS Reminders / Calendar / launchd;
- guideline freshness windows.

---

## 8. First confirmation batch

Before generating tests, confirm or edit:

1. `UC-I-01` — only Russian outward-facing output.
2. `UC-I-02` — Telegram for the owner only.
3. `UC-A-04` — Oura sleep/activity date.
4. `UC-A-03` — Apple Health merge.
5. `UC-B-03` — the actual `_triage_metric` algorithm.
6. `UC-B-04` — automatic tasks based on trends.
7. `UC-D-04` — triage agent.
8. `UC-H-02` — `user_qa` before the rounds.
9. `UC-J-01` / `UC-J-02` — proposal + confirmation before a test.
10. `UC-K-01` — upstream tracking of Chinese prompts.

---

## 9. Open questions

1. Where is the upstream source of the Chinese specialist prompts?
2. Is atomicity of `task -> Reminder -> DB` needed, or is the current best-effort approach acceptable?
3. What is the status of the lab approval pipeline: do we build a new one or retain the current OCR/import as an interim solution?
4. Where do we get an Anthropic models deprecation signal: a manual monthly check or an official source?
5. Do charters stay here or move to `CHARTERS.md`?

---

## 10. Change history

| Date | Version | Changes |
|---|---|---|
| 2026-05-08 | 0.1 | First UC draft |
| 2026-05-08 | 0.2 | Combined plan: catalog, P0, charters, meta, upstream |
| 2026-05-08 | 0.3 | Final working version: status/confirmation, expected gaps, external dependencies, dangerous discrepancies with the code corrected |
| 2026-05-08 | 0.4 | Merged with Codex: oracle `H` (Human charter); thematic aliases (§2.5); embedded Review Questions for UC-I-08/UC-I-09 |
