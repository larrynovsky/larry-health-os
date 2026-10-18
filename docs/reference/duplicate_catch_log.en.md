<!-- translation-of: docs/reference/duplicate_catch_log.md sha256:d546ee99c662 -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](duplicate_catch_log.md)

# Catch-log: incident journal for duplicates (agent memory)

> Purpose: agent working memory, NOT a report for a human (owner decision
> 2026-07-23). Provenance for `dup_allowlist`, candidates for the false-path
> corpus, fact base for "early/late/missed/false-positive" if the value of the
> system is ever questioned.
> Honest limit: "missed" only sees accidentally discovered omissions — the journal
> gives a lower bound, not completeness. ADR: `docs/explanation/discovery_before_build.md`.

Row format: `date · project · duplicate · outcome · how caught · note`.
Outcomes: **early** (discovery before build, work saved) · **late**
(gate on commit, work spent) · **missed** (found later by hand) ·
**false-positive** (WARN not confirmed).

## Journal

- 2026-09-14 · health · `AUTO_ARTIFACTS` in `scripts/thread_finish.sh` duplicated
  `git_facts.MACHINE_REGENERATED_FILES` (home since 07.09, with its own name-liveness
  guard) · **missed** · found by AN EXTERNAL TESTER, not the gate · copies had time to
  diverge in both directions: the shell copy had an extra TESTING_CONTRACTS.md and a
  FABRICATED `docs/reference/dependency_graph.md`, while SECURITY.md was missing.
  The cost is not cosmetic: `git add` with one unmatched pathspec returns 128 and
  STAGES NOTHING, the failure was swallowed by `|| true`, and the branch was a no-op
  printing a success message. Why the gate did not see it: the duplicate gate is
  anchored to data and names in Python, and this copy lived as A STRING in shell —
  the same blind spot RN-A already recorded here for 23.07 (a procedure duplicate
  anchored to a file artifact). Fixed 2026-09-14: one home, reads fail-closed.

- 2026-09-12 · health · `task_agent.promote_memory_questions` touches `tasks`,
  which is already served by six modules (`assessment_*`, `dashboard_routers/*`) ·
  **false-positive** · duplicate gate on commit · those six work with questionnaire
  tasks and the display layer; here there is a single writer of a new kind — promoting
  a question from memory into a task. The real duplicate in this thread was found
  BEFORE the build and removed: questions lived in TWO homes (`tasks.type='question'`
  — 7 records and `memory_facts.mem_class='question'` — 280), and the plan would have
  created a third. The second home was demoted to candidates, one home remained. The
  dedicated "answer expiry" axis was also rejected on measurement — `memory_facts.temporal_class`
  already exists (**early**).

- 2026-07-23 · health · `get_active_protocols` ×2 (`protocols_db.py`,
  `hai_hypotheses.py`) · legacy reference (F-106) · found by hand BEFORE the system ·
  became the known-case oracle for discovery and gate controls; alive in the tree, the
  gate does not flag legacy (RN-E).

- 2026-07-23 · health · PROCEDURE duplicate: a warm `project_context` page was
  assembled by the agent by hand, bypassing the standard writer (`doc_agent.py --regen-intent`:
  LLM-gen + honesty-gate + review via Telegram bot + human commits) · **missed** by
  the system, caught by the owner on review · reason: gates are anchored to
  tables/names, but this capability is anchored to a file artifact (`docs/explanation/*.md`) —
  data-based discovery does not see it (RN-A in the wild). Fixed: page regenerated
  via the standard path, sent to the bot; corpus extended (C-25).

- 2026-07-23 · neighbouring project · false-positive by_name: `scripts/<neighbour script>.py::get_conn`
  suspected as a duplicate/side-door §B.7 during intent-registry annotation
  (that script's entry) · the body turned out to be a thin wrapper over the standard
  `models.get_conn` (hardening Phase 1, 2026-07-02: "was direct `sqlite3.connect`" —
  fixed at that time) · lesson: a by_name suspicion without reading the body is noisy
  (RN confirmation) · no fixes required.

- 2026-07-24 · engine (the loop itself) · **missed**: a basename-keyed index lost
  same-named files — in a neighbouring project 5×`runner.py`, 4×`loader.py`; the index
  saw ONE of each group, discovery reported wrong file:line (`run_<…>` →
  someone else's `runner.py`). Found by EXTERNAL review of the spec document, not by
  the system or its controls: the units lived in a flat health repo and had no
  collisions · lesson: engine controls must include the structure of the TARGET repo
  (packages, same-named files) — fixed in `cdefa8b`
  (relpath key + `tmp_path` fixtures with packages; also F2: data files/directories
  as anchors, by syntactic position + one-hop through constants).

- 2026-07-24 · engine (the loop itself), external-review round 3 · **missed**: the
  engine excluded ITSELF from the index (`project_context` in SKIP) —
  `capabilities canonical_token` on the root health repo returned empty with a live
  function in `indexer.py`; the second anti-duplicate circuit would have been invisible
  to discovery (a "Phase 0 spec" class hole). Found by oracle O1 of the review handoff,
  which the author INCLUDED WITHOUT RUNNING — the oracle happened to be sighted where
  the author was blind ·
  lesson ×2: (a) tool self-exclusions are a verifiable surface, not a convenience;
  (b) an oracle not executed by the author before delivery is the same assumption
  inheritance, but here it worked IN favour: not knowing the answer in advance makes
  the oracle more honest · fixed: SKIP without `project_context`, noise measured
  (0 SQL garbage, 5 genuine file anchors), guard `test_engine_sees_itself`.

- 2026-07-26 · thread "one-time-use" iteration 2 (sidecar contracts) · **caught by discovery, early**:
  a duplicate reader of ONE format before it even existed. The plan was to write a
  reader for `contracts/*.json` in `check_contracts.py`, but the same sidecar must
  also read the engine's `dispgate` (check K1). Two readers of the same format — a
  duplicate of value, not a replica: the format would have diverged silently.
  Solution: ONE reader, in the engine (`indexer.contract_sidecars`), `check_contracts`
  only adapts to `{import-name: [functions]}` · the duplicate gate independently
  confirmed this after the fact with a WARN "``contract_sidecars`` touches `contracts/`,
  which is already served by: `check_contracts`" — a true positive by data, resolved
  by the same diff (old reader deleted, not left alongside) · lesson: "two consumers
  of the same format" is as much a single-source trigger as "two writers of one table".

- 2026-07-26 · thread iterations 3/5 · **false-positive ×2, class named**: the duplicate
  gate twice issued WARNs for "touches `contracts/` / `scripts/`, which is already
  served by …" — on `evaluate_new_modules` (dispgate) and `check_dispgate_liveness`
  (integrity_tests). Both times the path appears not as a data anchor but inside
  TEXT FOR A HUMAN: `f"no sidecar contracts/{key}.json"` and the path to the canonical
  hook in an unwiring message. Neither function accesses that data — sidecars are read
  through the engine's single reader. Class: **the file-anchor heuristic cannot
  distinguish a data path from a path mentioned in an error message.** Not fixing:
  narrowing the heuristic (ignoring literals inside f-strings going to print/warn)
  would weaken data-based detection for the sake of cosmetic WARN cleanup, and the
  WARN does not block. Known cost: every gate/message containing a path will produce
  noise. Revisit if such false positives start outnumbering true positives — measure
  then, don't guess.

- 2026-07-26 · review R4 of the validation gate · **false-positive, but useful: one
  object — two different questions**.
  The duplicate gate WARNed on `quarantine_db.quarantine_schema_guarded`: "touches
  `sqlite_master`, which is already served by `db_regression`, `health_db`,
  `integrity_tests`, `pharmaco_pipeline`, `prs_context`, `brief_shadow`". Checked all
  readers: EVERY one asks `sqlite_master` one thing — does a table/index EXIST
  (`WHERE name='...'`, `WHERE type='table'`). None reads the `sql` column, i.e. none
  asks about CONSTRAINTS. My reader asks exactly that: does the live table's DDL carry
  CHECKs. Same object, different question — not a value duplicate.
  Class not previously in the log: **the data heuristic catches the object, but what
  makes something a duplicate is the QUESTION posed to the object.** `sqlite_master`
  is whole-database metadata; any new schema reader will touch all previous ones. Not
  fixing: distinguishing "question" would require parsing SELECT columns, which would
  narrow data-based detection precisely where it works. Cost — noise on every schema
  reader; low, because such readers are few and WARN does not block.
  Worth recording separately why the function exists at all: `CREATE TABLE IF NOT EXISTS`
  does not add constraints to an already-created table, and SQLite cannot
  `ALTER ADD CONSTRAINT`. So the production `passset_quarantine` (created 2026-07-26,
  before review) does NOT carry CHECKs, even though the DDL declares them. Staying
  silent about this would be exactly "claim stronger than mechanism" — the very thing
  the review was criticising in the subsystem. The sensor says it out loud: the
  protection rests not on the schema but on itself.

- 2026-07-28 · photo-intake branch for test results · **real second home, partially
  resolved, remainder named**.
  The duplicate gate issued two WARNs on `doc_intake.tenant_inbox`: touches `incoming/`
  (already served by `handlers/messages`, `lab_backfill`, `lab_intake_watcher`) and
  `health/`.
  Checked all four. The inbox path is computed by THREE different formulas:
  `handlers/messages._tenant_inbox` and mine — from `HEALTH_DATA_DIR`; `lab_intake_watcher._incoming`
  — from `Path(health_db.DB_PATH).parent.parent`; `lab_backfill._resolve` iterates
  candidates including both variants. The result matches only because the database
  lives at `<tenant>/data/health.db`.
  This is not a false positive: one concept, three computations, and the match is
  protected by nothing except directory structure. Move the database and they will
  diverge silently and differently.
  Partially resolved: `doc_intake.tenant_inbox` is public so the intake branch has
  one home. Not resolved with the watcher: it is a production daemon on the other
  side of the pipeline; touching it would expand the branch's perimeter from intake
  to recognition. Recorded in `contracts/doc_intake.json` (D6, honest about the
  remainder).
  Class for the log: **the data heuristic caught a shared DIRECTORY, and the duplicate
  turned out to be the FORMULA for the path to it.** The WARN fired for the right
  reason and pointed exactly where it needed to.

- 2026-07-29 · thread `validation-gate-repair` · **false-positive, but useful: heuristic
  caught the NAME, not the access.**
  The duplicate gate WARNed on `dispgate.touches_canon`: "touches `health.db`, which
  is already served by `backfill_hae_coverage`, `check_contracts`, `constitution_analysis`,
  `dashboard`, `dashboard_db`, `dashboard_routers/api_lab_review`". Checked: the
  function does not open the database and cannot — it parses another file's AST and
  looks for mentions of `health_db`. The string `"health_db"` lives in data
  (`perimeter.json::_canon_access.modules`), and in code it appears only as a
  parameter name. It has exactly zero in common with the listed six: they read the
  canon, it reads syntax.
  Class not previously in the log: **the data heuristic cannot distinguish a MODULE
  that works with a table from a module that works with MENTIONS of that table.**
  The irony is that the miss happened on the very tool that itself finds canon
  accesses by mention — so we now have two detectors with the same blind spot, and
  mine honestly named it in its docstring (`known_hole`: dynamic import and
  subprocess).
  Not fixing: to distinguish this, the duplicate gate would need to understand WHAT
  the module does with the name, which is the same meaning-parsing it deliberately
  avoids. Cost — one WARN on a rare module class (code analysers), and WARN does not
  block.

## 2026-07-29 · `check_promotion_backlog_stale` × `lab_results_staging` — FALSE POSITIVE

**What the gate said.** The new check touches `lab_results_staging`, which is already
served by `api_lab_review`, `lab_backfill`, `lab_backfill_report`, `lab_intake_watcher`,
`lab_promote`, `lab_review_sheet` — six modules.

**Verdict: false positive, but useful.** All six genuinely PRODUCE or CONSUME the
table (write rows, promote, render the review sheet). The new check does neither: it
poses one question about the table's own state over time — "is there a (date, name)
pair that is here and has not reached canon, and how many days has it been here". This
is an observation capability, not a processing one; there is nothing to duplicate,
because until today this table had no observer at all.

**Why it was useful.** The list revealed `lab_intake_watcher` — a module I did not
know about, written 2026-07-28. The check showed: no plist in the repository, not in
`launchctl list`, no log, inbox `~/health/incoming` empty since 1 July. That is, the
automatic entry point for the new route exists and is not connected. Without the gate's
warning I was planning to build it from scratch — that would not have been "a similar
function" but a complete duplicate of an entire subsystem.

**Second trigger in the same commit:** `propose` is already taken in
`memory_consolidation.py:120` — renamed to `propose_mappings` by the same rule. Two
generic verbs in one module (`record`, `propose`) — a signal that the module was naming
actions rather than subjects.

**Lesson for the gate family.** The value of the warning here is not that it pointed
to a duplicate (there is none), but that it showed the DATA NEIGHBOURS. The list of
serving modules is a cheap map of what has already been built around the table. Cf.
[[feedback_duplicate_value_splitbrain]].

## 2026-07-29 · `loinc_match.record` × `location_signal.record` — NAME COLLISION, renamed

**What the gate said.** The name `record` is already defined in `location_signal.py:77`.

**Verdict: not a capability duplicate, but the gate was right anyway.** The functions
are about different things — one writes a location signal, the other writes a match
between an analyte name and a LOINC code. However, the name `record` says NOTHING: in
six months a traceback saying "record" will not hint at what was being recorded.
Renamed to `record_mapping` instead of adding to `dup_allowlist` — the allowlist
would have cost one line less and left two equally unnamed verbs in the project.

**Second trigger in the same commit:** `propose` is already taken in
`memory_consolidation.py:120` — renamed to `propose_mappings` by the same rule. Two
generic verbs in one module (`record`, `propose`) — a signal that the module was naming
actions rather than subjects.

**Lesson for the gate family.** The value of this trigger is not the duplicate
prevented but the fact that a name collision is a cheap detector of AN OVERLY GENERIC
NAME. The rule "rename if the name fits both" is more practical than arguing about
whether it is a duplicate.

**Third trigger of the same commit — by DATA:** `propose_mappings` touches
`lab_results_staging`, which is served by six modules. **False positive:** the other
six produce or consume it in substance, while the matcher only READS (name, unit)
pairs to sort a human's work by weight. Useful, as before, not by pointing to a
duplicate but by mapping the table's neighbours.

## 2026-07-29 — `loinc_match.clinical_facts` × `patient_profile`: REAL duplicate

Duplicate gate by DATA: `clinical_facts` touched `patient_profile` with its own
SELECT, even though the table is already served by `profile_db`, `gp_context`,
`profile_reconciler`, `dashboard_routers/api_profile`, `dashboard_routers/views`.

**Verdict: gate is correct, this is a read duplicate.** The profile has a home —
`profile_db` — and a dedicated SELECT in the matcher created a second path to the
same data. In six months, when the `patient_profile` schema changes (for example,
if date of birth is moved to a separate table), exactly this path would break, and
it would break silently: the filter test runs on fixture data and does not open the
database.

Fixed by reusing `profile_db.get_patient_profile()`. The "adult / fasting"
projection stayed in the matcher — that is its interpretation, not a shared fact;
it has no place in the profile's home.

**Difference from the three previous triggers.** Those were false positives, useful
as a neighbour map. This is the first where the gate pointed to a genuine second
path to data. The difference lies in the question to ask: not "do the modules do
the same thing" but "does this table have a designated owner that I am bypassing".

## 2026-07-29 — `check_reference_tables_not_in_canon` × `loinc.db`: false positive, but useful

Duplicate gate by data: the sensor touches `loinc.db`, which "is already served by"
`health_db`.

**Verdict: false positive.** `health_db` owns the CONNECTION to the reference database
(`attach_reference`, paths, table list); the sensor checks that the reference database
is NOT where it should not be — in the canon. What they share is exactly one thing:
both rely on `health_db.REFERENCE_TABLES`, i.e. on one list, not two copies of it.
That is precisely what the gate is after.

**Why it was useful.** It confirmed that the reference table list has ONE home. Had I
put a second literal inside the sensor, the gate would have stayed silent (it catches
tables, not constants), and six months later, when a fourth reference table were added,
the sensor would silently stop guarding it. The trigger is best read as the question
"are you looking at the same list", and here the answer is yes.


## 2026-07-29 — trend reader × `lab_name_loinc` / `loinc_terms`: false positive, but it checked exactly what it needed to

Five triggers on one commit (`5e3f5ab`): `labs_db.get_lab_trend_by_component`
and `integrity_tests.check_loinc_decisions_possible` touch `lab_name_loinc`,
`loinc_terms` and the `ref` schema, which "are already served by" `loinc_match` and
`loinc_loader`.

**Verdict: false positive on all five.** The single record owner remained single:
only `loinc_match.record_mapping` writes to `lab_name_loinc`, only `loinc_loader`
populates the reference database. The two new parties are READERS, and the appearance
of the first reader was the goal of the commit: until now the table accumulated the
owner's verdicts while nobody built a trend from it.

**Where a duplicate would have been real — and is not.** The sensor asks "does the
recorded decision contradict the filter based on facts about the person". The
temptation was to write this condition fresh inside the sensor: it is only two lines
about `^bldco` and `^`. That would have put the rule "what is impossible for an adult"
in two homes and they would have diverged silently — the matcher would be taught a
new fact while the sensor judged by the old one. Instead the judge itself is reused,
`loinc_match.impossible_for`; the sensor brings it data and does not know what
criteria it uses to judge.

**Why the trigger was useful.** It separates the question "how many modules read the
table" (many, and that is normal) from the question "how many instances of one
JUDGEMENT exist" (must be one). The gate sees the first and not the second — so the
second remains the human's responsibility, and in this commit the answer to it
mattered more.

---

## 2026-07-31 · `lab_promote.conflict_spread` ↔ `system_config` · **REAL DUPLICATE, RESOLVED**

**What the gate said.** `conflict_spread` touches `system_config`, which is already
served by `config_db`, `health_db`, `integrity_tests`, `patient_context` and two
seed scripts.

**Verdict: the duplicate is real, but not the one the gate was pointing at.** The
number of table readers is irrelevant — there should be many. The real duplicate was
in one line: I wrote MY OWN `SELECT value_num FROM system_config WHERE key=?` because
`config_db.get_config` did not accept a connection, and the integrity sensor needs to
read from a specific tenant's database. That is, the duplicate appeared as a workaround
for a neighbour's inflexibility — the most common way to introduce one.

**What was done instead of justifying it.** `config_db.get_config` was given
`conn=None`; `lab_promote` always calls it. No second SQL against `system_config`
remains. The same fix for the same reason was made an hour earlier for
`labs_db.get_confirmed_aliases` — both cases stem from the same root: the domain
reader could not work with an external connection, which is mandatory for a
multi-tenant sensor.

**Lesson for the next trigger.** If a duplicate arose to work around a narrow
neighbour API — fix the neighbour's API, do not document the duplicate as intentional.


---

## 2026-07-31 · `check_sleep_stage_provenance` ↔ `data_repair_log` · FALSE POSITIVE (with a caveat)

**What the gate said.** The new sensor touches `data_repair_log`, which is already
served by `belief_contract` and `health_db`.

**Verdict: not a capability duplicate.** The three accesses pose three different
questions to the same table:

| who | question | what it computes |
|---|---|---|
| `health_db` | write and rollback | `log_repair`, `revert_repairs` — the sole writer |
| `belief_contract._last_data_change` | *when* data changed | `max(repaired_at ∪ reverted_at)` for belief age |
| `integrity_tests.check_sleep_stage_provenance` | was *this* repaired before | `COUNT` of live records for its own cause — recurrence |

There is no shared computation: last-change time and own-cause count do not derive
from each other, and merging them into one function would create an abstraction for
the sake of one property — "both access the same table".

**Caveat, stated explicitly.** There are now TWO readers of the log, and both compute
their own thing over the same table with their own SQL. While the questions differ,
this is not a duplicate. If a third reader appears asking the same question as one of
these, that will be a real duplicate, and the fix should go through `health_db`, not
a third SQL. The lesson from the previous trigger ("duplicate to work around a narrow
neighbour API — fix the API") does not yet apply: there is no narrowness, `health_db`
simply does not offer readers because there were none.

**Verified:** `project_context capabilities data_repair_log` (earlier in this thread) —
besides `_ensure_data_repair_log` in `health_db`, no other capabilities by name exist.

| 2026-07-31 | `integrity_tests.check_glossary_targets_known` × `lab_name_aliases` | **real** | The sensor made its own SELECT against a domain table. The gate named the neighbours (`labs_db`), and this was not a false positive: the table's form would have gained a second home in the monitor. Fixed by moving the read into `labs_db.confirmed_alias_targets`, not by documenting a workaround. |
| 2026-08-01 | `labs_db.domain_verdicts` × `lab_domain_verdicts` | **false positive** | The gate correctly named `health_db` as a data neighbour: `health_db._migrate_lab_domain_verdicts` CREATES and seeds the table. But this is not two homes for the same question; it is the separation of "who creates the schema" and "who answers questions about the content" — the same arrangement as `lab_name_aliases` one row above, where the read was moved to `labs_db` on the strength of exactly that earlier gate trigger. There is exactly one reader, and it is here; there are no writers at runtime — the table is edited by a human. If a second SELECT to it appears bypassing `labs_db` — that will be a real duplicate. **Verified:** `capabilities lab_domain_verdicts` (by name — only migration in `health_db`) + grep by table name. |
| 2026-08-01 | `lab_intake_watcher.heartbeat_for` × `data/` | **false positive** | The gate named six neighbours by the `data/` directory (`dashboard_db`, `calendar_client`, `backfill_hae_coverage`, …). What they share is the DIRECTORY, not the subject: each touches its own file within it. The path formula for `lab_intake_state.json` was one and lived as a private `_state_path`; the fix simply made it public as `heartbeat_for`, called from `_state_path`. So the number of homes did not become two — it remains one, now also visible to the pulse sensor that needs the path of ANOTHER tenant. If a second assembler of this path appears (e.g. a sensor assembles `data/lab_intake_state.json` itself) — that will be a real duplicate, and that is exactly what was prevented here. **Verified:** grep for `lab_intake_state.json` — two occurrences, both in `lab_intake_watcher.py` (constant `STATE_NAME` and its sole consumer). |

| 2026-08-04 | `integrity_tests.check_brief_does_not_retell_owner` × `reports/` | **false positive** | The gate named six neighbours by directory: `jobs/scheduled` (writes the delivered brief), `morning_report` (retired, its `REPORTS_DIR` points to iCloud and has been dead since 13.07), `check_contracts` (path map), `consult_prep`, `gen_arch_blocks` and `morning_test_summary` (the last three concern `tests/reports/`, a different directory with the same name). What they share is the directory NAME, not the subject: there is one writer (`jobs/scheduled`), and until now there was not a single reader of the delivered brief — this sensor is the first. It poses a new question: "what has the human actually read", unlike all existing ones which ask "was it generated". If a second reader of `data/reports/*.md` appears — that will be a duplicate, and the fix should be a shared accessor, not a third `glob`. **Verified:** `capabilities context_cards` (in this thread) + grep `data/reports` across the repository: three occurrences, two live — the writer in `jobs/scheduled` and this sensor. |

| 2026-08-07 | intentgate: receipt completeness (F-01, P1) | **missed** | An empty dict and a subset of `invariants` passed the full hook with exit 0 — "zero can be presented". Thirty authorial controls (22 units + mutations R3/R4 + sabotage + liveness) did not see the class: they distinguished "key absent" and "status forged", but not "fewer items presented than the live set". Found by external review (charter audit `intent-receipt-gate@91285fc`), by execution through the real hook. Fixed @`30e3699`: R6 equality of sets, empty/subset fail by name, R6 mutation reddens its tests. Lesson — the class "valid by type, empty by content" must always be checked with a dedicated negative. |
| 2026-08-07 | intentgate: receipt format home (F-02, P2) | **missed** | The reference "format — letitbe §" pointed to a document outside the repository with no schema, plus a third out-of-sync copy of the skill in `~/.codex`. The substring guard checked the section name, not the content. Found by the same external review. Fixed @`30e3699`: normative home `docs/reference/intent_receipt_format.md`, its examples are EXECUTED by a contract test with a live judge; skills reference it, they do not normalise. |

| 2026-09-01 | `integrity_tests.check_logrotate_liveness` × `docs/how-to/rotate_logs.md` | **false positive** | The gate correctly named `log_rotate` as a neighbour: both functions mention the same how-to page. But the mention is an ADDRESS in the docstring, not a capability: the log-rotation mechanism has one page and both modules point to it, neither serves it independently (the reader entry-point required by `test_doc_reader_path` must be named from code). The split is exactly the one for which duplicates are caught: facts live in `log_rotate` (`receipt_status`, `unlisted_logs`), thresholds and judgement live in `integrity_tests`, as with `check_db_size`. If a second module appears that itself parses the rotation config or itself assembles the receipt form — that will be a real duplicate. **Verified:** `capabilities docs/how-to/rotate_logs.md` + grep for `logrotate.log`: one path formula, in `log_rotate.receipt_status`. |
| 2026-09-02 | `integrity_tests.check_norm_kind_unclassified` × `absolute_thresholds` (`health_db`, `rules_db`) | **false positive** | The gate correctly named the table as shared: `health_db` seeds and migrates it, `rules_db` reads threshold values for runtime. The sensor reads ONE counter (`COUNT(*) WHERE norm_kind='unclassified'`) in ro mode across all tenants and passes no judgement on values — this is the form of all `check_*` over foreign tables (`check_family_names_resolve`, `check_clinical_kb_populated`). A real duplicate would be a second writer of `norm_kind` or a second runtime reader of thresholds besides `rules_db.get_threshold`. **Verified:** `capabilities absolute_thresholds` — writers `health_db._seed_*`/`_migrate_*`/`_classify_norm_kinds` (one module), runtime reader `rules_db`. |
| 2026-09-02 | `integrity_tests.check_norm_documents_fresh` × `norm_documents` (`health_db`) | **false positive with a caveat** | The gate is correct that there are two writers: `health_db._migrate_norm_documents` writes the REGISTRY (id, version, url, sha256, next_check_days — that the document exists), the sensor writes CHECK STATE (last_check, next_check, remote_state — when it was last looked at and what was found). The columns do not overlap, and the meaning differs: the registry is code in the repo, the state is the result of a pull. A real duplicate would be a second writer of sha256/url or a second sensor deciding "did it change". **Verified:** `capabilities norm_documents` — `health_db` (
