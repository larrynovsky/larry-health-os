<!-- translation-of: docs/reference/duplicate_catch_log.md sha256:c4342495c940 -->
**English** · [Русский](duplicate_catch_log.md)

# Catch-log: duplicate incident log (agent memory)

> Purpose: the agent's working memory, NOT a report for a person (owner's decision,
> 2026-07-23). Provenance for `dup_allowlist`, candidates for the false-path corpus,
> a factual basis for “early/late/missed/false positive” if the system's usefulness comes into question.
> Explicit limit: “missed” includes only omissions found by chance — the log
> provides a lower bound, not completeness. ADR: `docs/explanation/discovery_before_build.md`.

Entry format: `дата · проект · дубль · исход · чем пойман · заметка`.
Outcomes: **early** (discovery before building, work saved) · **late**
(gate at commit time, work spent) · **missed** (found manually later) ·
**false positive** (WARN not confirmed).

## Log

- 2026-09-14 · health · `AUTO_ARTIFACTS` in `scripts/thread_finish.sh` duplicated
  `git_facts.MACHINE_REGENERATED_FILES` (the home since 09/07, with its own guard for
  name liveness) · **missed** · found by an EXTERNAL TESTER, not the gate · the copies had already
  diverged in both directions: the shell copy had an extra TESTING_CONTRACTS.md and
  an INVENTED `docs/reference/dependency_graph.md`, but was missing SECURITY.md.
  The cost was not cosmetic: `git add` with one unmatched pathspec returns 128 and
  STAGES NOTHING; the failure was swallowed by `|| true`, and the branch was a no-op while printing
  a success message. Why the gate missed it: the duplicate gate is anchored to data and
  names in Python, while this copy lived as a STRING in shell — the same RN-A blind spot
  already recorded here for 07/23 (a duplicate procedure anchored to a file
  artifact). Fixed on 09/14: one home, fail-closed reading.

- 2026-09-12 · health · `task_agent.promote_memory_questions` touches `tasks`,
  already served by six modules (assessment_*, dashboard_routers/*) ·
  **false positive** · duplicate gate at commit time · those six work with questionnaire tasks and
  the display; here is the sole writer of a new kind — promoting a question from memory into
  a task. A real duplicate in this thread was found BEFORE building and removed: questions
  lived in TWO homes (`tasks.type='question'` — 7 records and
  `memory_facts.mem_class='question'` — 280), and the plan would have created a third. The second home
  was downgraded to candidates, leaving one home. A separate “answer expiration” axis was also
  rejected after measurement — `memory_facts.temporal_class` already exists (**early**).

- 2026-07-23 · health · `get_active_protocols` ×2 (protocols_db.py,
  hai_hypotheses.py) · legacy reference (F-106) · found manually BEFORE the system ·
  became a known-case oracle for discovery and gate controls; still present in the tree, the gate
  does not fail on legacy (RN-E).

- 2026-07-23 · health · duplicate PROCEDURE: a reader-friendly project_context page was assembled
  manually by the agent, bypassing the standard writer (`doc_agent.py --regen-intent`: LLM generation +
  honesty gate + proofreading through the Telegram bot + a person commits) · **missed**
  by the system, caught by the owner in review · reason: gates are anchored to tables/names,
  while this capability is anchored to a file artifact (docs/explanation/*.md) —
  data-based discovery cannot see it (RN-A in practice). Fixed: the page
  was regenerated through the standard path and sent to the bot; the corpus was expanded (C-25).

- 2026-07-23 · a neighbour project · by_name false positive: `scripts/generate_brochure_praise.py::get_conn`
  suspected as a duplicate/side door under §B.7 while annotating the intent registry
  (brochure_praise entry) · the body turned out to be a thin wrapper around the standard
  `models.get_conn` (hardening Phase 1, 2026-07-02: “previously sqlite3.connect
  directly” — fixed then) · lesson: by_name suspicion without reading the body
  creates noise (RN confirmation) · no fixes required.

- 2026-07-24 · engine (the mechanism itself) · **missed**: the index's basename key lost
  files with the same name — a neighbour project has 5×runner.py, 4×loader.py; the index saw ONE per group,
  discovery reported a false file:line (`run_supervision` → someone else's runner.py). Found
  by EXTERNAL review against the requirements document, not by the system or its controls: unit tests operated
  on the flat health repo and had no collisions · lesson: engine controls must
  include the TARGET repo's structure (packages, namesakes) — fixed in cdefa8b
  (relpath key + tmp_path fixtures with packages; also F2: data files/directories
  as anchors, by syntactic position + one hop through constants).

- 2026-07-24 · engine (the mechanism itself), round 3 of external review · **missed**: the engine
  excluded ITSELF from the index (project_context in SKIP) — `capabilities canonical_token`
  at the health root returned nothing despite a live function in indexer.py; a second anti-duplication mechanism
  would have been invisible to discovery (a “Stage 0 of the requirements” class gap). Found by review-handoff oracle O1,
  which the author INCLUDED WITHOUT RUNNING — the oracle happened to see where the author was blind ·
  two lessons: (a) a tool's self-exclusions are a testable surface, not a convenience;
  (b) an oracle the author has not run before handoff is the same inheritance of assumptions, but
  here it worked TO our benefit: when you do not know the answer in advance, the oracle is more honest · fixed:
  SKIP without project_context, noise measured (0 SQL junk, 5 legitimate file anchors),
  guard test_engine_sees_itself.

- 2026-07-26 · Э2 of the “disposability” thread (sidecar contracts) · **caught by discovery, early**:
  a duplicate reader for ONE format before it even existed. The intent was to write a reader
  for `contracts/*.json` in `check_contracts.py`, but the engine's
  `dispgate` must read the same sidecar (K1 check). Two readers of one format duplicate meaning; they are not replicas:
  the format would silently diverge. Decision: ONE reader, in the engine
  (`indexer.contract_sidecars`); `check_contracts` only adapts it to
  `{имя-импорта: [функции]}` · the duplicate gate independently confirmed this afterward with a WARN:
  “`contract_sidecars` touches `contracts/`, already served by: check_contracts” —
  a true positive based on data, resolved in the same diff (the old reader
  was deleted, not left alongside) · lesson: “two consumers of one format” is just as much
  a trigger for a single source as “two writers of one table.”

- 2026-07-26 · Э3/Э5 of the same thread · **false positive ×2, class identified**: the duplicate gate twice issued a WARN for
  “touches `contracts/` / `scripts/`, already served by …” — for `evaluate_new_modules`
  (dispgate) and `check_dispgate_liveness` (integrity_tests). In both cases, the path appeared not as
  a data anchor but within TEXT for a person: `f"нет сайдкара contracts/{key}.json"` and the path to
  the canonical hook in a wiring message. Neither function accesses these data — sidecars
  are read through the engine's single reader. Class: **the file-anchor heuristic cannot distinguish a data path
  from a path mentioned in an error message.** Not fixing: narrowing the heuristic (ignoring literals
  inside f-strings passed to print/warn) would weaken data-based detection for the sake of WARN cosmetics,
  and WARN does not block. The cost is known: every gate/message with a path will produce noise. Reconsider
  if these false positives start outnumbering true positives — then measure, not guess.

- 2026-07-26 · validation-gate R4 review · **false positive, but useful: one object — two different questions**.
  The duplicate gate issued a WARN for `quarantine_db.quarantine_schema_guarded`: “touches `sqlite_master`,
  already served by db_regression, health_db, integrity_tests, pharmaco_pipeline,
  prs_context, brief_shadow.” Checked all readers: EVERY one asks `sqlite_master`
  one thing — whether a table/index EXISTS (`WHERE name='...'`, `WHERE type='table'`). None
  reads the `sql` column, meaning no one asks about CONSTRAINTS. My reader
  asks exactly that: does the live table's DDL carry CHECKs? The same object, a different question —
  not a duplicate of meaning.
  A class not previously in the log: **the data heuristic catches the object, but it is the
  QUESTION about the object that makes it a duplicate.** `sqlite_master` is metadata for the entire database; every new schema reader will
  intersect with all previous ones. Not fixing: distinguishing the “question” would require parsing SELECT columns, which
  would narrow data-based detection where it actually works. The cost is noise for every schema
  reader; it is small because there are only a few such readers and WARN does not block.
  It is worth recording separately why the function appeared at all: `CREATE TABLE IF NOT EXISTS` does not
  add constraints to an existing table, and SQLite cannot `ALTER ADD CONSTRAINT`.
  Therefore, production `passset_quarantine` (created on 2026-07-26, before review) has NO CHECKs, although
  the DDL declares them. Keeping silent about that would be exactly “a claim stronger than the mechanism” — the very thing
  the review criticized the subsystem for. The sensor says it explicitly: protection relies on the sensor, not on the schema.

- 2026-07-28 · lab photo intake branch · **a real second home, partially removed, remainder named**.
  The duplicate gate issued two WARNs for `doc_intake.tenant_inbox`: touches `incoming/` (already served by
  `handlers/messages`, `lab_backfill`, `lab_intake_watcher`) and `health/`.
  Checked all four. The inbox path is derived by THREE different formulas: `handlers/messages.
  _tenant_inbox` and mine — from `HEALTH_DATA_DIR`; `lab_intake_watcher._incoming` — from
  `Path(health_db.DB_PATH).parent.parent`; `lab_backfill._resolve` iterates over candidates, including
  both variants. The results agree only because the database resides at `<tenant>/data/health.db`.
  This is not a false positive: one concept, three computations, and agreement protected by nothing but the directory
  structure. Move the database, and they silently diverge in different ways.
  Partially unified: `doc_intake.tenant_inbox` is public so that the intake branch has one home. Not unified
  with the watcher: it is a production daemon on the other side of the pipeline; touching it would expand the branch's scope
  from intake to recognition. Recorded in `contracts/doc_intake.json` (D6, explicit about the remainder).
  Class for the log: **the data heuristic caught a shared DIRECTORY, while the duplicate was the FORMULA for its
  path.** The WARN fired for the right reason and pointed exactly where it should.

- 2026-07-29 · thread `validation-gate-repair` · **false positive, but useful: the heuristic caught a NAME, not access.**
  The duplicate gate issued a WARN for `dispgate.touches_canon`: “touches `health.db`, already served by
  `backfill_hae_coverage`, `check_contracts`, `constitution_analysis`, `dashboard`, `dashboard_db`,
  `dashboard_routers/api_lab_review`.” Checked: the function does not open the database and cannot — it
  parses another file's AST and looks for a mention of `health_db`. The string `"health_db"` lives in
  data (`perimeter.json::_canon_access.modules`), while the code contains only the parameter name. It has exactly
  nothing in common with those six: they read the canonical store; it reads syntax.
  A class not previously in the log: **the data heuristic cannot distinguish a MODULE working with
  a table from a module working with MENTIONS of that table.** The irony is that the miss
  happened precisely in a tool that itself looks for access to the canonical store by mention — so we
  now have two detectors with the same blindness, and mine explicitly named it in its docstring
  (`known_hole`: dynamic import and subprocess).
  Not fixing: to distinguish them, the duplicate gate would have to understand WHAT the module does with the name, which is
  the same semantic analysis it deliberately declined. The cost of the miss is one WARN for a rare
  class of modules (code analyzers), and WARN does not block.

## 2026-07-29 · `check_promotion_backlog_stale` × `lab_results_staging` — FALSE POSITIVE

**What the gate said.** The new check touches `lab_results_staging`, already served by
`api_lab_review`, `lab_backfill`, `lab_backfill_report`, `lab_intake_watcher`, `lab_promote`,
`lab_review_sheet` — six modules.

**Verdict: false positive, but useful.** All six PRODUCE or CONSUME the table's actual contents
(write rows, promote them, render the review sheet). The new check does neither: it
asks the table one question about its own state over time — “is there a (date, name) pair
that resides here and has not reached the canonical store, and how many days has it been here?” This is an observation
capability, not processing; there is nothing to duplicate it because, before today, this
table had no observer at all.

**How it helped.** The list included `lab_intake_watcher` — a module I did not know about,
written on 2026-07-28. Inspection showed: no plist in the repository, absent from `launchctl list`,
no log, inbox `~/health/incoming` empty since July 1. In other words, the automatic entry point for the new route
exists and is not connected. Without the gate's warning, I was planning to rebuild it — which would have been
not a “similar function” but a full duplicate of an entire subsystem.

**Second trigger in the same commit:** `propose` was already taken in `memory_consolidation.py:120` —
renamed to `propose_mappings` under the same rule. Two generic verbs in one module
(`record`, `propose`) signal that the module named actions, not subjects.

**Lesson for the gate family.** The warning's value here is not that it pointed to a duplicate
(there is none), but that it showed NEIGHBORS by data. The list of serving modules is
a cheap map of what has already been built around the table. See [[feedback_duplicate_value_splitbrain]].

## 2026-07-29 · `loinc_match.record` × `location_signal.record` — NAME COLLISION, renamed

**What the gate said.** The name `record` is already defined in `location_signal.py:77`.

**Verdict: not a duplicate capability, but the gate was still right.** The functions concern different things —
one writes a location signal, the other maps an analyte name to a LOINC code.
However, the name `record` itself says NOTHING: six months from now, “record” in a traceback
will not tell you what was being recorded. Renamed to `record_mapping` instead of adding it
to `dup_allowlist` — the allowlist would have cost one line less but left the project with
two equally anonymous verbs.

**Second trigger in the same commit:** `propose` was already taken in `memory_consolidation.py:120` —
renamed to `propose_mappings` under the same rule. Two generic verbs in one module
(`record`, `propose`) signal that the module named actions, not subjects.

**Lesson for the gate family.** The trigger's value here is not a prevented duplicate,
but that a name collision is a cheap detector of an OVERLY GENERIC name. The rule
“rename it if the name fits both” is more practical than arguing over whether it is a duplicate.

**Third trigger in the same commit — by DATA:** `propose_mappings` touches
`lab_results_staging`, served by six modules. **False positive:** the other
six produce or consume its actual contents, while the matcher only READS
“name, unit” pairs to prioritize the person's work by weight. As before, it is useful
not for identifying a duplicate but for mapping neighbors around the table.

## 2026-07-29 — `loinc_match.clinical_facts` × `patient_profile`: a REAL duplicate

Duplicate gate by DATA: `clinical_facts` touched `patient_profile` with its own SELECT,
although the table is already served by `profile_db`, `gp_context`, `profile_reconciler`,
`dashboard_routers/api_profile`, `dashboard_routers/views`.

**Verdict: the gate is right; this is a duplicate read.** The profile has a home — `profile_db` — and
a separate SELECT in the matcher created a second path to the same data. Six
months later, when the `patient_profile` schema changed (for example, moving date of birth
to a separate table), this exact path would break, and silently: the
filtering test runs on fixture facts and does not open the database.

Fixed by reusing `profile_db.get_patient_profile()`. The
“adult / fasting” projection stayed in the matcher — it is the matcher's interpretation, not a shared
fact; it does not belong in the profile's home.

**Difference from the previous three triggers.** Those were false positives, useful as
a map of neighbors. This is the first where the gate pointed to a real second path to the data.
The difference is the question to ask: not “do the modules do the same thing,”
but “does this table have an assigned owner whom I am bypassing?”

## 2026-07-29 — `check_reference_tables_not_in_canon` × `loinc.db`: false positive, but useful

Duplicate gate by data: the sensor touches `loinc.db`, “already served by”
`health_db`.

**Verdict: false positive.** `health_db` owns the CONNECTION to the reference database
(`attach_reference`, paths, table list); the sensor checks that the reference database is ABSENT
where it should not be — in the canonical store. They have exactly one thing in common: both
rely on `health_db.REFERENCE_TABLES`, meaning one list, not two
copies of it. That is exactly what the gate is trying to achieve.

**How it helped.** It confirmed that the reference table list has ONE home.
Had I added a second literal list inside the sensor, the gate would have stayed silent (it catches
tables, not constants), and six months later, when a fourth reference table was added,
the sensor would silently fail to guard it. Read the trigger as
the question “are you looking at the same list?” The answer here is yes.


## 2026-07-29 — trend reader × `lab_name_loinc` / `loinc_terms`: false positive, but it checked exactly what was needed

Five triggers in one commit (`5e3f5ab`): `labs_db.get_lab_trend_by_component`
and `integrity_tests.check_loinc_decisions_possible` touch `lab_name_loinc`,
`loinc_terms`, and the `ref` schema, “already served by” `loinc_match` and `loinc_loader`.

**Verdict: false positives for all five.** Write ownership was and remains singular: writes to
`lab_name_loinc` come only from `loinc_match.record_mapping`, and only
`loinc_loader` populates the reference database. The two newcomers are READERS, and adding the first reader was the purpose
of the commit: before it, the table accumulated the owner's verdicts, but no one built a trend from them.

**Where a duplicate would have been real — and there is none.** The sensor asks “does
the recorded decision contradict filtering by facts about the person?” There was a temptation to write this condition
again inside the sensor: just two lines about `^bldco` and `^`. Then the rule
“what is impossible for an adult” would have lived in two homes and silently diverged —
the matcher would learn a new fact while the sensor judged by the old one. Instead,
the judge itself, `loinc_match.impossible_for`, is reused; the sensor brings it data
and does not know which criteria it judges by.

**How the trigger helped.** It separates “how many modules read the table”
(many, and that is normal) from “how many instances of one JUDGMENT” (there must
be one). The gate sees the first and cannot see the second — so the second remains a
human responsibility, and in this commit its answer mattered more.

---

## 2026-07-31 · `lab_promote.conflict_spread` ↔ `system_config` · **REAL DUPLICATE, ELIMINATED**

**What the gate said.** `conflict_spread` touches `system_config`, already
served by `config_db`, `health_db`, `integrity_tests`, `patient_context`, and two
seed scripts.

**Verdict: a real duplicate, but not the one the gate pointed to.** The number
of table readers does not matter — there should be many. The real duplicate was
one line: I wrote MY OWN `SELECT value_num FROM system_config WHERE key=?`,
because `config_db.get_config` did not accept a connection, while the integrity sensor
needed to read from a particular tenant's database. So the duplicate appeared as a workaround
for a neighbor's inflexibility — the most common way to create one.

**What was done instead of making excuses.** `config_db.get_config` gained `conn=None`;
`lab_promote` always calls it. No second SQL query against `system_config` remains. The same
change, for the same reason, was made an hour earlier to
`labs_db.get_confirmed_aliases` — both cases stemmed from one cause: the domain reader could not
work with someone else's connection, while a multitenant sensor requires that.

**Lesson for the next trigger.** If a duplicate arose to work around a neighbor's narrow API,
fix the neighbor's API instead of documenting the duplicate as deliberate.


---

## 2026-07-31 · `check_sleep_stage_provenance` ↔ `data_repair_log` · FALSE POSITIVE (with a caveat)

**What the gate said.** The new sensor touches `data_repair_log`, already served by
`belief_contract` and `health_db`.

**Verdict: not a duplicate capability.** The three accesses ask three different questions of one table:

| who | question | what it computes |
|---|---|---|
| `health_db` | writing and rollback | `log_repair`, `revert_repairs` — the sole writer |
| `belief_contract._last_data_change` | *when* the data changed | `max(repaired_at ∪ reverted_at)` for belief age |
| `integrity_tests.check_sleep_stage_provenance` | whether *this* was repaired before | `COUNT` of live entries for its own cause — recurrence |

There is no shared computation: the last change time and the count of one's own cause cannot be derived
from each other, and merging them into one function would create an abstraction for one
property — “both access the same table.”

**The caveat, stated explicitly.** There are now TWO log readers, each computing its own result over
one table with its own SQL. While the questions differ, this is not a duplicate. If a third reader appears
asking the same question as one of these, that will be a duplicate, and it should be fixed
with access through `health_db`, not a third SQL query. The previous trigger's lesson (“a duplicate to
work around a neighbor's narrow API — fix the API”) does not apply here yet: there is no narrowness; `health_db`
simply offers no readers because none existed.

**Checked:** `project_context capabilities data_repair_log` (earlier in this thread) —
no other capabilities by name apart from `_ensure_data_repair_log` in `health_db`.

| 2026-07-31 | `integrity_tests.check_glossary_targets_known` × `lab_name_aliases` | **real** | The sensor ran its own SELECT against a domain table. The gate named neighbors (`labs_db`), and this was not a false positive: the table's shape would have gained a second home in the monitor. Fixed by moving the read into `labs_db.confirmed_alias_targets`, not by recording a workaround. |
| 2026-08-01 | `labs_db.domain_verdicts` × `lab_domain_verdicts` | **false positive** | The gate named `health_db` as a data neighbor, correctly: `health_db._migrate_lab_domain_verdicts` CREATES and seeds the table. But these are not two homes for one question; they separate “who creates the shape” from “who answers a question about the contents”: the same arrangement as for `lab_name_aliases` in the row above, where reading was moved to `labs_db` precisely because of the previous trigger of this gate. There is exactly one reader, here; there are no runtime writers at all — a person edits the table. A second SELECT against it bypassing `labs_db` would be a real duplicate. **Checked:** `capabilities lab_domain_verdicts` (by name — only the migration in `health_db`) + grep for the table name. |
| 2026-08-01 | `lab_intake_watcher.heartbeat_for` × `data/` | **false positive** | The gate named six neighbors sharing the `data/` directory (`dashboard_db`, `calendar_client`, `backfill_hae_coverage`, …). They share a DIRECTORY, not a subject: each touches its own file in it. There was one formula for the path to `lab_intake_state.json`, private in `_state_path`; this change extracted precisely that — the private function became the public `heartbeat_for`, called from `_state_path`. So the number of homes did not become two; it remained one, now visible to the pulse sensor that needs ANOTHER tenant's path. A second constructor for this path (for example, a sensor assembling `data/lab_intake_state.json` itself) would be a real duplicate, and that is exactly what was prevented here. **Checked:** grep for `lab_intake_state.json` — two occurrences, both in `lab_intake_watcher.py` (the `STATE_NAME` constant and its sole consumer). |

| 2026-08-04 | `integrity_tests.check_brief_does_not_retell_owner` × `reports/` | **false positive** | The gate named six directory neighbors: `jobs/scheduled` (writes the delivered brief), `morning_report` (retired; its `REPORTS_DIR` actually points to iCloud and has been dead since 07/13), `check_contracts` (path map), `consult_prep`, `gen_arch_blocks`, and `morning_test_summary` (the last three concern `tests/reports/`, a different directory with the same name). They share a directory NAME, not a subject: there is one writer (`jobs/scheduled`), and there were no readers of the delivered brief — this sensor is the first. It introduces a new question: “what did the person actually read,” unlike all existing checks, which ask “was it generated?” A second reader of `data/reports/*.md` would be a duplicate, to be fixed through shared access, not a third `glob`. **Checked:** `capabilities context_cards` (in this thread) + grep for `data/reports` across the repository: three occurrences, two live — the writer in `jobs/scheduled` and this sensor. |

| 2026-08-07 | intentgate: receipt completeness (F-01, P1) | **missed** | An empty dictionary and a subset of `invariants` passed the full hook with exit 0 — “you may present nothing.” Thirty author controls (22 unit tests + R3/R4 mutations + sabotage + liveness) missed the class: they distinguished “key missing” from “status forged,” but not “less than the live set presented.” Found by external review (charter audit intent-receipt-gate@91285fc), by execution through the real hook. Fixed @30e3699: R6 set equality, empty/subset fail by name, the R6 mutation fails its tests. Lesson: the “valid type, empty content” class must always be checked with a separate negative control. |
| 2026-08-07 | intentgate: home of the receipt format (F-02, P2) | **missed** | The “format — letitbe §” link led to a document outside the repository without a schema, plus a third unsynchronized copy of the skill in ~/.codex. The substring guard checked the section name, not its contents. Found by the same external review. Fixed @30e3699: normative home docs/reference/intent_receipt_format.md; its examples are EXECUTED by a contract test using the live judge; skills refer to it rather than define the norm. |

| 2026-09-01 | `integrity_tests.check_logrotate_liveness` × `docs/how-to/rotate_logs.md` | **false positive** | The gate named `log_rotate` as a neighbor, correctly: both functions mention the same how-to page. But the mention is an ADDRESS in a docstring, not a capability: the rotation mechanism has one page, and both modules point to it rather than serve it in their own ways (the reader entry point required by `test_doc_reader_path` must be named from code). The separation is exactly what duplicate detection aims for: facts live in `log_rotate` (`receipt_status`, `unlisted_logs`), thresholds and judgment in `integrity_tests`, as with `check_db_size`. A second module that parses the rotation configuration ITSELF or assembles the receipt format itself would be a real duplicate. **Checked:** `capabilities docs/how-to/rotate_logs.md` + grep for `logrotate.log`: one path formula, in `log_rotate.receipt_status`. |
| 2026-09-02 | `integrity_tests.check_norm_kind_unclassified` × `absolute_thresholds` (health_db, rules_db) | **false positive** | The gate correctly identified the shared table: `health_db` seeds and migrates it; `rules_db` reads threshold values for runtime. The sensor reads ONE count (`COUNT(*) WHERE norm_kind='unclassified'`) in ro mode across all tenants and makes no judgment about values — the pattern of all `check_*` functions over other modules' tables (`check_family_names_resolve`, `check_clinical_kb_populated`). A second writer of `norm_kind` or a second reader of thresholds for runtime besides `rules_db.get_threshold` would be a real duplicate. **Checked:** `capabilities absolute_thresholds` — writers `health_db._seed_*`/`_migrate_*`/`_classify_norm_kinds` (one module), runtime reader `rules_db`. |
| 2026-09-02 | `integrity_tests.check_norm_documents_fresh` × `norm_documents` (health_db) | **false positive with a caveat** | The gate is right that there are two writers: `health_db._migrate_norm_documents` writes the REGISTRY (id, version, url, sha256, next_check_days — that the document exists); the sensor writes CHECK STATE (last_check, next_check, remote_state — when it was checked and what was found). The columns do not overlap, and the meaning differs: the registry is code in the repo; the state is the result of a pull. A second writer of sha256/url or a second sensor deciding “has it changed?” would be a real duplicate. **Checked:** `capabilities norm_documents` — health_db (registry) and integrity_tests (state). If a third writer appears, extract `record_check` into norm_documents as the sole entry point. |
| 2026-09-02 | `labs_db.compute_bank_refs` × `sqlite_master` (db_regression, health_db, integrity_tests, lab_specialized, pharmaco_pipeline, prs_context) | **false positive** | The gate saw the string `SELECT 1 FROM sqlite_master WHERE name='lab_results'` — this is a “does the table exist?” guard, not a capability over the catalog: none of the listed functions “serves” `sqlite_master`; all read it with the same one-liner for their own table. The module's capability is the mode of report references from `lab_results.{ref_low,ref_high}`, and it has one home (a generalization of `get_modal_reference` from the same file). A second computation of the report mode or a second writer of `system_config.lab_refs` would be a real duplicate. **Checked:** `capabilities lab_results` — reference readers `labs_db.get_modal_reference`/`compute_bank_refs`, `safety_net._resolve_thresholds` (consumer, not calculator); the only writer of `lab_refs` is `health_db._refresh_lab_refs`. |
| 2026-09-05 | `weekly_digest.render` × `gen_testing_contracts.render` | **real, resolved by renaming** | Two different `render` functions in one repository — class F-106. Renamed to `render_digest`; allowlist untouched. |
| 2026-09-05 | `weekly_digest.tenant_lexicon` × `problem_list`/`medications`/`doc_patterns` (problems_db, treatment_db, config_db …) | **real, eliminated** | The first version read three tables with its own SQL — a third reader for each. Switched to existing readers `problems_db.get_problem_list`, `treatment_db.get_medications`, `config_db.get_doc_patterns`; the module has no SQL of its own. **Checked:** `capabilities problem_list|medications|doc_patterns`. |
| 2026-09-05 | `integrity_tests.check_weekly_digest_delivered` / `jobs.scheduled.weekly_digest_outbox` × `docs/how-to/weekly_digest.md` | **false positive** | The same class as `rotate_logs.md` on 09/01: the how-to path is named in the alert text and sensor message as a READER ENTRY POINT (`test_doc_reader_path` requires it); no one “serves” the page. A second parser of outbox files besides `weekly_digest.read_digest/verdicts` would be a real duplicate. |

| 2026-09-14 | `integrity_tests.check_reco_repeats_fresh_lab` × `hypotheses_cbcr` (hypotheses_db, lab_drytest) | **false positive** | The gate correctly named the neighbors: the table is served by `hypotheses_db` (writes and reads hypothesis payloads); `lab_drytest` reads it for its own analysis. The sensor does not serve it at all: it takes one day's `payload` as TEXT and judges it with two predicates from `gp_context` — the same way it reads `agent_reports` and `tasks`. This is the pattern of all `check_*` functions over other modules' tables (see `check_norm_kind_unclassified`, 09/02). A second parser of the payload structure (patient_view, consensus) besides `hypotheses_db`, or a second text judge besides `gp_context`, would be a real duplicate. **Checked:** `capabilities hypotheses_cbcr` + reading both neighbors: the sensor does not parse structure, only `payload` as a string. |
| 2026-09-23 | `scripts/clean_clone_probe.probe` × `data/`, `health/`, `scripts/` (health_db, assessment_*, daemon_liveness …) | **false positive** | The probe copies the public area into a temporary directory and creates an empty home for the installer there; the path strings matched production data homes, but the probe neither reads nor writes them — isolated through temporary HOME/HEALTH_DATA_DIR. |
| 2026-09-23 | `scripts/install.plan_install` × `data/` (health_db, bot/helpers, dashboard_db …) | **false positive** | The installer creates an empty data directory for a new installation and templates in the repo; it does not intersect with production data (for an existing installation — a no-op run, tested). The original name `plan` collided with `brief_state.plan`/`lab_promote.plan` — renamed. |
| 2026-09-24 | `pii_census.is_public_export` × `private/infra.yaml` (dashboard, gen_key_paths, health_db, scripts/install) | **false positive** | The path `private/infra.yaml` appears only in the docstring, as an example of a file the installer creates but does not put in git. The function reads the list of areas (`publication_zones.yaml`) and `git ls-files`; it does not open the primary machine's configuration. **Checked:** reading the function body, 3 lines. |
| 2026-09-24 | `genome_intake.run_one` × `raw_snps` (genome_parser, genome_pipeline, genome_annotator, genome_db …) | **false positive** | `run_one` only COUNTS the table (`COUNT(*)`: is the tenant's genome empty — a second genome is not loaded over the first). There is still only one writer, `genome_parser.import_raw_genome`, called by `genome_pipeline` (S1) — `run_one` hands the normalized file to the pipeline. Its own INSERT into `raw_snps` or its own genotype parsing besides S1 would be a real duplicate. **Checked:** `grep raw_snps genome_intake.py` — one line, SELECT COUNT. |
| 2026-09-24 | `infra_config.cloud_dir` × `health/` (assessment_*, calendar_client, api_hae_ingest, api_lab_review) | **false positive** | The function neither reads nor writes data: it COMPUTES the installation's cloud folder path (or data directory if there is no cloud) and became the sole home of a value that previously lived as a literal in 27 files (BL-PUB-12). The `health/` string match is a shared project path prefix. A second computation of the same path would be a real duplicate. **Checked:** the function body is 3 lines; `test_cloud_literal_only_in_home` fails on a second literal. |
| 2026-09-25 | `integrity_tests.check_public_genotype_profile` × `raw_snps` (genome_parser, genome_pipeline, genome_annotator, genome_db …) | **false positive** | The judge only READS the table: tenant genotypes are needed to identify whose profile is in the public area. There are no write paths; a profile guard is not a second home for the genome. |
| 2026-09-28 | `doc_translation.translations` × `docs/` (integrity_tests, lexicon_registry, night_cycle) | **false positive** | The function lists only `docs/**/*.en.md` — translations — to check their structure and freshness. The other modules read `docs/` for their own purposes (guards, the lexicon, the nightly cycle); none deals with translations. A second function listing translations would be a real duplicate. **Checked:** `grep 'en\.md\|translation'` across the three modules returned no matches. |
| 2026-09-28 | `project_context.lessons.selftest` × `docs/`, `docs/x.md` (doc_translation, integrity_tests, lexicon_registry, night_cycle, owner_nag, weekly_digest) | **false positive** | The lessons engine selftest writes `docs/x.md` only into a temporary directory `tempfile.TemporaryDirectory` — it is the evidence of a synthetic lesson, to exercise the «evidence exists» branch. It neither reads nor writes the project's real `docs/`. |
| 2026-09-29 | `pilot_shadow.compare` × `data/`, `data/health.db`, `health.db` (doc_intake, integrity_tests, scripts/install, dashboard …) | **false positive** | The sentinel writes only its OWN snapshot copy (`~/health_shadow/health/data/health.db` and `/home/health/shadow/…` in the volume) and judges it; it neither reads nor writes the tenant's working database and serves no data. The match is the shared path tail `data/health.db`. A real duplicate would be a second judge of alerts — there is none; the sentinel calls `safety_net.run_safety_net`. **Checked:** stand run 29.09 — nothing written to the live database by the sentinel (the snapshot is a backup API copy from the volume). |
| 2026-09-29 | `scripts/install.render_docker` × `data/health.db` (doc_intake, pilot_shadow, secrets_paths) | **false positive** | The renderer names the database path inside the container volume (the "file exists" healthcheck); it never opens or writes the database; the layout's home is health_db. |
| 2026-09-29 | `scripts/install.render_owner_override` × `private/` (cpic_reference_db, infra_config, integrity_tests, region_pack, vcf_import_pipeline) | **false positive** | The override lists the NAMES of private/ files to mount read-only into the container; it does not read their content; its own infra.yaml holds a single primary_host. |
