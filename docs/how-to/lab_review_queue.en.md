<!-- translation-of: docs/how-to/lab_review_queue.md sha256:c503c9e92d03 -->
**English** · [Русский](lab_review_queue.md)

# How-to: work through the lab row review queue

> When you need this: nightly triage sent `очередь ревью[health] не движется: N строк
> ждут человека Nд`. Sensor: `integrity_tests.check_lab_review_queue_movement`.
> Measure the current queue before working (step 1). Queue contents and counts
> belong to the current run and are not a fixed baseline for this guide.

## What the alert means and what it does NOT mean

It means: `lab_results_staging` contains rows with status `review`, and the oldest
of them has been waiting longer than the threshold (`lab.review_stale_days`).

It **does NOT mean “you must make a decision about every row.”** Some rows may
only need promotion or a status update after an existing rule has resolved them.
Separate these from rows that require a new decision.

The `review` status is set by `lab_triage.py:89`. **Only promotion clears it**,
and only for rows it actually takes (`review_status='promoted'`,
`lab_promote.py:593`) or that you explicitly rejected. A row blocked by promotion
stays in `review` forever: it has no way out until its name is reconciled
to a canonical name or the row is rejected. That is why the queue does not clear itself.

## Step 0. If the alert is about `pending`, not `review`

The second alert in the same family is `staging[<тенант>]: N строк не классифицированы Nд`
(sensor `check_staging_status_coverage`). This is a DIFFERENT queue: rows that
`lab_triage` has not touched yet. Procedure:

1. **Specialized layer first, then triage.** Rows whose class, under the verdict
   (`lab_domain_verdicts`), goes to `specialized` receive terminal status
   `specialized` only from `lab_specialized.promote_specialized(run_id, execute=True)`.
   Running triage first can send them to `review` as unmapped analytes even when
   the specialized layer is their assigned destination.
2. **Triage dry run**: `lab_triage.triage(execute=False)` prints the buckets.
   Inspect the rows and reasons returned by `_classify`, especially
   `rejected: физиологически невозможно`: check units and repeated conversions
   before accepting the rejection.
3. `triage(execute=True)` — then follow the steps below for the `review` queue.

## Step 1. Measure again (read-only, changes nothing)

```bash
ssh <studio_ssh> 'cd ~/health_scripts && /opt/homebrew/bin/python3.11 -c "
import sqlite3, collections, sys
sys.path.insert(0, \".\")
import lab_promote as lp
c = sqlite3.connect(\"file:~/health/data/health.db?mode=ro\", uri=True)
c.row_factory = sqlite3.Row
rows = [dict(r) for r in c.execute(
    \"SELECT * FROM lab_results_staging WHERE review_status=%s\" % repr(chr(39)+\"review\"+chr(39)))]
kept, blocked = lp.prepare(rows, {})
print(\"in queue:\", len(rows), \"| promote will take:\", len(kept), \"| will block:\", len(blocked))
print(collections.Counter(r for _x, r in blocked).most_common())
for r in kept: print(\"  WILL TAKE  \", r[\"source_file\"], \"|\", r[\"raw_name\"], \"=\", r[\"value\"], \"→\", r[\"_cname\"])
for r, why in blocked: print(f\"  [{why}]\", r[\"source_file\"], \"|\", r[\"raw_name\"], \"=\", r[\"value\"])
"'
```

Divide the output into four groups: they need DIFFERENT actions:

| group | indicator | what to do | owner's decision? |
|---|---|---|---|
| 1 | promotion WILL TAKE it | run promotion | no |
| 2 | `unmapped-nonanalyte`, `value=None` | reject: the canonical store has no representation for a qualitative result | no |
| 3 | `unmapped-nonanalyte`, value EXISTS | reconcile the name to a canonical name OR reject | **YES** |
| 4 | `page-role:narrative` | reject under the rule “commentary pages are not imported” | no |

## Step 2. Visually check group 1 BEFORE promotion

Promotion takes a row if its name maps to a known canonical name. The mapping
can be WRONG, and then promotion adds the measurement to the wrong trend.

Check the specimen as well as the name. Hypothetical failure: similarly named
results from different specimen types map to one canonical name. Even with no
value, such a row indicates an unsafe mapping.

Look at the `→ _cname` column in the output of step 1. Anything suspicious
(the specimen in the name does not match the target, “stool”/“urine”/“saliva” → a blood analyte)
goes to rejection in step 3, not promotion.

## Step 3. Create a reject file

The format is the same as the HTML review sheet export (`lab_review_sheet.py`); the path and
value below are examples:

```json
[
  {"source_file": "<folder>/panel_example.pdf", "canonical": "Группа крови", "value": null},
  {"source_file": "<folder>/panel_example.pdf", "canonical": "ПСА свободный*", "value": 1.23}
]
```

The matching key in `lab_promote._apply_rejects`: `run_id` + `source_file` +
(`canonical_name` OR `raw_name`) + the value within a tolerance of 1e-6. For rows without
a value, set `"value": null`: the code converts it to `-1e30` on both sides, and
the match succeeds.

**Take the name VERBATIM from the output of step 1**, including punctuation and
case. Invented nonmedical example: `DEMO-C` and `DEMO-С` look alike but end in
Latin `C` and Cyrillic `С`. Visually similar names need not match byte for byte.

Put the file on Studio: `/tmp/rej_review.json`.

## Step 4. ⚠️ TRAP: `--reject-file` writes EVEN WITHOUT `--execute`

`_apply_rejects` is called at the very beginning of `plan()` and **runs `commit`** before
the code reaches the dry-run branch (`lab_promote.py:446`). That is:

```bash
# THIS ALREADY REJECTS ROWS, even without --execute
python3.11 lab_promote.py --run-id example-run --reject-file /tmp/rej_review.json
```

A sequence that will not bite you:

```bash
# 4.1 — first a dry-run WITHOUT the reject file: see what the promote would do at all
ssh <studio_ssh> 'cd ~/health_scripts && \
  /opt/homebrew/bin/python3.11 lab_promote.py --run-id example-run' | head -60

# 4.2 — DB snapshot (§3), because writing comes next
ssh <studio_ssh> 'cp -a ~/health/data/health.db \
  ~/health/data/health.db.pre_review_$(date +%Y%m%d_%H%M%S)'

# 4.3 — apply reject (writes statuses) and view the plan again, now WITHOUT them
ssh <studio_ssh> 'cd ~/health_scripts && \
  /opt/homebrew/bin/python3.11 lab_promote.py --run-id example-run \
  --reject-file /tmp/rej_review.json' | head -60

# 4.4 — only now write to the canon
ssh <studio_ssh> 'cd ~/health_scripts && \
  /opt/homebrew/bin/python3.11 lab_promote.py --run-id example-run \
  --execute'
```

Read the two gates in the output of 4.3: **Gate-2 COMPLETENESS** (old data without coverage is NOT
deleted) and **Gate-2b INFLOW** (new names that have never existed in the canonical store).
Inflow with unfamiliar names is a reason to stop, not “probably fine.”

## Step 5. Group 3: the only place you are needed

The row carries an actual number, but its name is unknown to the canonical dictionary. Three outcomes:

1. **Synonym of an existing analyte** → add it to `lab_canon._SYNONYMS`
   (`lab_canon.py`, beginning of the file) and run `tests/unit/test_lab_canon.py`.
2. **New analyte** → add a new canonical name in the same place + an entry in `lab_refs`
   if it has reference ranges.
3. **Not an analyte** → reject in step 3.

The route through `lab_name_aliases` (`confirmed=1`) exists, and promotion reads it
first (`lab_promote._canon`), but accepts ONLY a target the canonical dictionary already
knows; otherwise, the name would create a new trend outside the dictionary. An alias to
an unknown name therefore does not work; the name must first appear in `lab_canon`.

## Step 6. Check that the queue has moved

```bash
ssh <studio_ssh> 'cd ~/health_scripts && \
  /opt/homebrew/bin/python3.11 integrity_tests.py 2>&1 | grep -iE "очеред|Итог"'
```

Expected: the line `очередь ревью … не движется` disappears, or the number in it
decreases and the age is recalculated from the NEW oldest row.

Also run the full suite on Studio (§12): promotion touched the canonical data:

```bash
cd ~/health_scripts && ./scripts/test_on_studio.sh
```

## Known traps

- **`--reject-file` writes without `--execute`** (step 4).
- **A name with a homoglyph**: Latin and Cyrillic in one corpus (step 3).
- **Incorrect canonicalization** silently sends a measurement to the wrong trend (step 2).
- **Promotion replaces ONLY covered data**: old (date, analyte) pairs without coverage
  are kept as “old uncovered data,” not deleted. This protects against loss,
  but also hides leakage: the two look the same.
- **Do not assume the `gold` bucket is empty.** Count it in the current run.
  It is distinct from `review`, and promotion also accepts it.

## Files

| file | role |
|---|---|
| `lab_triage.py` | sets `review_status` (`rejected` / `review` / `gold`) |
| `lab_promote.py` | the only writer to `lab_results`; `_apply_rejects`, `_block_reason`, `prepare`, `plan` |
| `lab_canon.py` | name dictionary `_SYNONYMS`, `normalize`, `CANONICALS` |
| `labs_db.py` | `get_confirmed_aliases_all`, `confirmed_alias_targets` |
| `lab_review_sheet.py` | HTML review sheet for a run (original page + table), reject-list export |
| `integrity_tests.py` | `check_lab_review_queue_movement`: the sensor itself |

---

## Checks when reconciling queue and canonical state

### Count state transitions, not touched rows

`cur.rowcount` can include rows that already had the target status. Compare the
before/after snapshot by row key and status; distinguish a repeated update from
a new rejection. Record the actual changes separately from the SQL counter.

### Deduplication can leave a stale queue status

If `split_groups` selects one representative, a status update tied only to its
insertion can leave the other rows in `review`. Verify the candidates:

```sql
SELECT id, run_id, page, review_status, value FROM lab_results_staging
WHERE source_file = ? AND raw_name = ? ORDER BY id;
```

Then check whether the result exists under its canonical name, date and source:

```sql
-- Compare the canonical name, date, source and value.
SELECT COUNT(*) FROM lab_results
WHERE date = ? AND source = ? AND test_name = ?
  AND ABS(COALESCE(value, -1e30) - ?) < 1e-6;
```

Only a match permits `review_status='promoted'`. Without a match, the row remains
unresolved. An alert about a queue status alone does not prove that the result
is absent from the canonical store.

### Check synonym reachability and meaning

A dictionary key must be reachable through `_key`. Invented nonmedical example:
if `_key` removes parentheses, a literal key `demo (suffix)` cannot be reached
in that form. Before normalizing keys, check whether distinct specimen types or
units would collapse into one trend; do not merge them mechanically.

For bilingual names, check both `/` and `\` as separators. If the unit changes
the meaning, a synonym alone cannot resolve it: an explicit dimension rule is
needed. Names present or absent in a particular corpus are not a reusable rule.

### Completion criterion

Measure the remaining queue again and explain each unresolved reason. Compare
canonical changes against the intended set in both directions: nothing missing
and nothing unexpected added. Check reader output separately from queue counts.
Fix recurring status drift where promotion writes statuses; record unresolved
dictionary or dimension decisions without claiming they have been implemented.
