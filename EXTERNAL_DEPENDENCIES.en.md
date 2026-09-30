<!-- translation-of: EXTERNAL_DEPENDENCIES.md sha256:22e202e4f3e5 -->
**English** · [Русский](EXTERNAL_DEPENDENCIES.md)

# EXTERNAL_DEPENDENCIES.md — External artifact registry

**Version:** 0.1 (draft) | **Date:** 2026-05-08

> This file is the single source of truth for the system's external dependencies.
> It complements `USE_CASES.md` §7 and group K (Upstream Tracking).
> Related to UC-K-01..K-06 and UC-X-01..X-03.

---

## 1. Purpose

Larry Health OS uses artifacts that change independently of our code:
specialist prompts, ClinVar/MyVariant API, PubMed, Oura/Apple schemas, Anthropic
models, Telegram API, macOS Reminders/Calendar, medical guideline windows.

If these artifacts are updated and we do not notice, medical conclusions drift
without an explicit commit. The registry exists to:

1. Know what we use from outside the system.
2. Know which version has been applied and approved.
3. See the diff between upstream and the local version.
4. Apply updates only deliberately (especially `impact=medical`).
5. Link an update to the UC that may be affected.

---

## 2. Record fields

Each dependency is a record in `external_dependencies.yaml` with the following fields:

| Field | Type | Purpose |
|---|---|---|
| `name` | str | Dependency identifier |
| `category` | enum | `prompt` / `api_schema` / `model` / `os_integration` / `guideline` |
| `source` | str/url | Where upstream lives (URL, repository path, service name) |
| `local_path` | str | Local file/snapshot (if applicable) |
| `language` | str | `zh`/`ru`/`en`/`-` for non-text artifacts |
| `approved_sha256` | hex | SHA-256 of the last version approved by a human |
| `current_sha256` | hex | SHA-256 of the current local version |
| `last_checked` | iso8601 | When upstream was last checked |
| `last_applied` | iso8601 | When upstream was last applied to the local version |
| `last_check_ok` | bool | Whether the last check succeeded (upstream was reachable) |
| `local_override` | bool | If `true`, upstream must not silently overwrite the local version |
| `impact` | enum | `medical` / `behavior` / `infra` / `cosmetic` |
| `owner` | str | File/module/person responsible for the integration |
| `impacted_uc` | list | UC IDs that may be affected by an update |
| `notes` | str | Free-form notes |

**Impact rule:**

- `medical` — a change may alter the system's medical behavior (specialist prompts, ClinVar, guideline windows). An update is **never** applied automatically.
- `behavior` — a change affects UX/tone/process, but not medical conclusions (lifestyle prompts, model routing).
- `infra` — technical integration (Telegram API, launchd, file paths).
- `cosmetic` — output format, display, no logic.

---

## 3. Minimal registry (initial)

Below is the list expected in `external_dependencies.yaml`. Specific SHA/URL values
are filled in as they are confirmed.

### 3.1 Specialist Prompts (Chinese)

```yaml
specialist_prompts_oncology:
  category: prompt
  source: https://github.com/huifer/WellAlly-health  # confirmed 2026-05-08
  local_path: .claude/specialists/oncology.md
  language: zh
  approved_sha256: TBD
  current_sha256: TBD
  last_checked: null
  last_applied: null
  last_check_ok: null
  local_override: false
  impact: medical
  owner: wellally_consult.py
  impacted_uc: [UC-H-01, UC-I-01, UC-I-08, UC-K-01]
  notes: |
    Part of Council v2. A prompt update can change the oncologist's
    medical conclusions in Round A/B. Apply only manually via /upstream_apply.
```

Equivalent records for each of the 9 medical prompts:
`gastroenterology, cardiology, hematology, nephrology, nutrition, endocrinology,
psychiatry, pulmonology` — using the same schema.

### 3.2 Lifestyle Prompts (Russian)

```yaml
lifestyle_prompts_sleep:
  category: prompt
  source: TBD                                  # probably local-only, not from upstream wellally
  local_path: .claude/specialists/lifestyle_sleep.md
  language: ru
  approved_sha256: TBD
  current_sha256: TBD
  last_checked: null
  last_applied: null
  last_check_ok: null
  local_override: false
  impact: behavior
  owner: lifestyle_agents.py
  impacted_uc: [UC-B-05, UC-B-06, UC-K-02]
  notes: |
    Sleep coach prompt. Changes affect the tone and content of the briefing,
    but not medical diagnoses.
```

Likewise for `lifestyle_movement, lifestyle_stress, lifestyle_energy`.

### 3.3 ClinVar / MyVariant API

```yaml
clinvar_via_myvariant:
  category: api_schema
  source: https://myvariant.info/v1/
  local_path: null
  approved_sha256: null                       # for an API: a field spec, not a SHA
  expected_fields:
    - clinvar.rcv.clinical_significance
    - dbsnp.alleles
    - dbsnp.chrom
    - dbsnp.gene
  last_checked: null
  last_check_ok: null
  impact: medical
  owner: genome_annotator.py
  impacted_uc: [UC-C-02, UC-C-03, UC-K-04]
  notes: |
    Response schema change = upstream_drift. Do not disguise it as "no data".
```

### 3.4 PubMed E-utilities

```yaml
pubmed_eutils:
  category: api_schema
  source: https://www.ncbi.nlm.nih.gov/books/NBK25500/
  local_path: null
  expected_fields:
    - PubmedArticleSet.PubmedArticle.MedlineCitation.PMID
    - PubmedArticleSet.PubmedArticle.MedlineCitation.Article.ArticleTitle
  last_checked: null
  last_check_ok: null
  impact: behavior
  owner: pubmed_client.py
  impacted_uc: [UC-B-07, UC-K-05]
  notes: |
    API change → PMID verification switches to degraded mode.
    GP weekly/monthly flag has_findings=1 without a strong PMID as a warning.
```

### 3.5 Anthropic Models

```yaml
anthropic_models:
  category: model
  source: https://docs.claude.com/en/docs/about-claude/models
  models_in_use:
    - claude-sonnet-4-6   # chat, MDT coordinator, GP daily/weekly/monthly
    - claude-haiku-4-5    # arbiter, task extraction, hypothesis generation
    - claude-opus-4-7     # constitutions
  last_checked: null
  last_check_ok: null
  impact: behavior
  owner: hai_core.py
  impacted_uc: [UC-K-03]
  notes: |
    On deprecation of any model — an alert, not a silent fallback.
    Check once a month (manually, or via an Anthropic RSS feed if one appears).
```

### 3.6 Oura API

```yaml
oura_api:
  category: api_schema
  source: https://cloud.ouraring.com/v2/docs
  local_path: null
  expected_fields:
    - sleep.summary_date
    - sleep.total_sleep_duration
    - sleep.deep_sleep_duration
    - daily_activity.steps
    - daily_readiness.score
    - daily_stress.stress_high
  last_checked: null
  last_check_ok: null
  impact: medical
  owner: import_oura.py
  impacted_uc: [UC-A-04, UC-D-02]
  notes: |
    Sleep by wake-up date (target=today), activity/HRV — yesterday.
    Field renaming = upstream_drift.
```

### 3.7 Apple Health Export (HAE)

```yaml
apple_health_hae:
  category: api_schema
  source: https://www.healthautoexport.app/    # third-party app, format changes less often than Oura API
  local_path: ~/Library/Mobile Documents/iCloud~com~ifunography~HealthExport/Documents/Health/
  expected_fields:
    - HKQuantityTypeIdentifierStepCount
    - HKQuantityTypeIdentifierHeartRateVariabilitySDNN
    - HKQuantityTypeIdentifierBloodPressureSystolic
    - HKCategoryTypeIdentifierSleepAnalysis
  last_checked: null
  impact: medical
  owner: import_apple_health.py
  impacted_uc: [UC-A-03, UC-A-05]
  notes: |
    BP via JSON works only if enabled in HAE Settings → Metrics.
    Otherwise — via the .hae binary.
```

### 3.8 Telegram Bot API

```yaml
telegram_bot_api:
  category: api_schema
  source: https://core.telegram.org/bots/api
  expected_fields:
    - update.effective_chat.id
    - update.message.text
    - update.message.photo
    - InlineKeyboardMarkup
  last_checked: null
  impact: infra
  owner: telegram_bot.py
  impacted_uc: [UC-A-01, UC-G-01, UC-I-02, UC-I-06]
  notes: |
    API changes are caught via python-telegram-bot versions.
    Major library bump → recheck InlineKeyboard and send_long.
```

### 3.9 macOS Reminders / Calendar / launchd

```yaml
macos_integration:
  category: os_integration
  source: macOS system frameworks
  components:
    - osascript                # Reminders read/write
    - icalbuddy                # Calendar read
    - launchd                  # scheduled tasks
  last_checked: null
  impact: infra
  owner: task_agent.py, calendar_client.py, calendar_sync.py
  impacted_uc: [UC-G-01, UC-G-02, UC-A-06]
  notes: |
    A major macOS upgrade can break osascript syntax or the launchd plist format.
    Check on OS updates.
```

### 3.10 Medical Guideline Windows

```yaml
guideline_freshness:
  category: guideline
  source: TESTING_CONTRACTS.md §1
  windows:
    lab_results_reminder_days: 180
    lab_results_alert_days: 270
    genome_update_alert_days: 30
    gp_weekly_max_age_days: 8
    gp_monthly_max_age_days: 35
  last_reviewed: 2026-04-24
  impact: medical
  owner: integrity_tests.py, TESTING_CONTRACTS.md
  impacted_uc: [UC-D-05, UC-A-05, UC-B-08]
  notes: |
    Freshness windows are not code but a medical decision.
    Reviewed annually or when the attending physician/protocol changes.
```

---

## 4. Record lifecycle

```
            ┌─────────────────────┐
            │ New dependency      │
            │ detected            │
            └──────────┬──────────┘
                       │
                       ▼
            ┌─────────────────────┐
            │ Registry entry      │
            │ status=proposed     │
            │ approved_sha256=null│
            └──────────┬──────────┘
                       │  manual review
                       ▼
            ┌─────────────────────┐
            │ approved_sha256 =   │
            │ current_sha256      │
            │ last_applied = now  │
            └──────────┬──────────┘
                       │
                       ▼  weekly upstream check
            ┌─────────────────────┐       ┌──────────────┐
            │ current_sha256      │  yes  │ Diff to TG/  │
            │ changed?            ├──────►│ proposal UC  │
            └──────────┬──────────┘       └──────┬───────┘
                       │ no                       │
                       ▼                          ▼
                  next check          manual /upstream_apply
                                              │
                                              ▼
                                     approved_sha256 ←
                                     current_sha256
```

---

## 5. Relationship to USE_CASES.md

| UC | What it covers |
|----|---------------|
| `UC-K-01` | Specialist prompts upstream tracking — the main behavioral UC |
| `UC-K-02` | Lifestyle prompts |
| `UC-K-03` | Anthropic models routing |
| `UC-K-04` | ClinVar/MyVariant schema drift |
| `UC-K-05` | PubMed E-utilities |
| `UC-K-06` | WellAlly upstream weekly check |
| `UC-X-01` | The registry exists and is valid |
| `UC-X-02` | A medical dependency is not applied silently |
| `UC-X-03` | After an update, a proposal for affected UC |

---

## 6. Open questions

(copied from `USE_CASES.md` §9, expanded as they become clearer)

1. ~~Where is the upstream source of the Chinese specialist prompts?~~ ✅ closed 2026-05-08: `https://github.com/huifer/WellAlly-health`.
2. Where do we get an Anthropic models deprecation signal? RSS, a manual monthly check, webhook?
3. Do the lifestyle prompts (Russian) have a separate upstream, or are they your own (`local_override: true` forever)?
4. Should the registry be YAML in `external_dependencies.yaml` (a separate file) or front matter for each UC in `USE_CASES.md`?

---

## 7. Change history

| Date | Version | Changes |
|------|--------|-----------|
| 2026-05-08 | 0.1 | First draft — 10 minimal registry records, field schema, lifecycle |
