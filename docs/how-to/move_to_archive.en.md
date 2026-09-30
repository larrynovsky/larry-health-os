<!-- translation-of: docs/how-to/move_to_archive.md sha256:19f607840af3 -->
**English** · [Русский](move_to_archive.md)

# How-to: move a document from LIVE to history

Use this procedure when "the document describes completed work, but you do not want to delete it."

> Rewritten on 2026-08-04 after checking: the previous version pointed to `docs/archive/` —
> a directory that does not exist in the repository and never has. It also
> referred to a nonexistent test and example files that do not exist. Checking
> a procedure by running it, rather than reading it, costs less than finding a second home later.

## When to move it

| Criterion | Example |
|---|---|
| Describes removed infrastructure | The VPS era after the migration to Studio on 2026-05-09 |
| Describes a completed one-time process | A work plan marked CLOSED, an audit report |
| Contains stop words from `doc_inventory.yaml::stop_words_in_live` and cannot be rewritten | Old network addresses — if they are historical context |
| Outdated, but needed for an investigation | SECURITY incidents before the current policy |

**Do NOT move it** if you can update the document to reflect the current state. History means
"recorded as of time X," not "cannot be bothered to rewrite it."

## Where exactly

The home depends on the kind of document:

| What | Home | Why |
|---|---|---|
| One-time plan, report, audit, owner decision | `plans/` | `doc_inventory.yaml::archive` includes `plans/*.md`; it contains 21 such files |
| Closed debt | `BACKLOG_ARCHIVE.md` | As a section inside it, not a file |
| Thread snapshot between sessions | `docs/handoff/<нить>/` | Written by the `the-end` ritual; do not create it manually |
| Delivery record | `CHANGELOG.md` | Written by `doc_agent` when a thread is closed (`thread_finish`) |

**The `docs/archive/` directory does not exist — do not create it.** Until 2026-08-04,
two mechanisms watched it to no effect: the append-only guard in
`scripts/git-hooks/pre_commit_check.py` and the filter in
`tests/integration/test_doc_invariants.py:211`. The guard now targets `plans/`
(and includes `_selftest_append_only`); the dead filter was removed — there are no GEN blocks in `plans/`.

**Consequence for this procedure:** once a file moves to `plans/`, you can no longer delete it
with an ordinary commit — the guard blocks it. This is intentional: it protects against silently
wiping the decision history referenced by CLAUDE.md §15
(`plans/verify_*.py` as evidence of closed gaps). For a deliberate deletion, use
`git commit --no-verify`; the contents remain in git history either way.

## Steps

### 1. Move the file

Make all edits and commits on MacBook (§1, § Environment: Studio is deploy-only; pre-commit
rejects commits there).

```bash
git mv docs/<section>/<FILE>.md plans/<TYPE>_<topic>_<YYYY-MM-DD>.md
```

The name carries a date — it tells the reader the document is a snapshot in time. The convention
in neighboring files is `PLAN_`, `AUDIT_`, `DECISIONS_`. A separate "ARCHIVE · frozen" header
is **not needed**: the project has no test that requires one, and none of the 21
files in `plans/` has one. The date in the name is the marker.

### 2. Check the classification

A separate entry in `doc_inventory.yaml` is usually unnecessary: `plans/*.md` is already
covered by a glob in `archive:`, and the file drops out of `live:` when it no longer matches
`docs/<раздел>/*.md`. If you move it to another home, check that the path belongs
to exactly one category (`test_no_file_in_both_categories`).

### 3. Remove the page from the entry-point registry

If the file was listed in `doc_inventory.yaml::reader_path.frozen`, delete the row
in the same commit and update `measured`. After the move, the guard
`test_doc_reader_path.py` no longer sees this page (it looks at `docs/**/*.md`), while
the row in `frozen` would still point to nothing.

### 4. Update incoming links

```bash
grep -rn "<FILE>.md" --include="*.md" --include="*.py" .
```

Point each link either to a current LIVE document or label it explicitly as
`[<название> (история)](../../plans/<новое-имя>.md)`.

### 5. Run the checks

```bash
HEALTH_DATA_DIR=~/health /opt/homebrew/bin/python3.11 -m pytest \
  tests/integration/test_doc_inventory.py \
  tests/integration/test_doc_invariants.py \
  tests/consistency/test_doc_reader_path.py -q
```

What failures mean:

- `test_every_md_classified` — the path is in neither `live` nor `archive` → step 2.
- `test_no_file_in_both_categories` — it is in both → step 2.
- `test_frozen_list_does_not_rot` — the page gained an entry point but stayed in
  `frozen` → step 3.
- `INV-DOC-1` — stop words remain in a LIVE file → either rewrite the mention,
  or move this file to history too.

### 6. Commit

Uncommitted work on canonical is a latent loss (§1).

```bash
git add -A && git commit -m "docs: <FILE> to history (<short reason>)"
```

## What not to do

- **Do not delete the file** instead of moving it if other documents
  or audit logs reference it. Deletion is valid when there are no references and the contents can be reproduced
  from git.
- **Do not leave it in `live:` "temporarily"** — INV-DOC-1 will fail every night and
  clutter the morning briefing.
- **Do not remove stop words from a historical document** to make a test pass: this
  destroys its audit value.
- **Do not create a new directory for history.** The homes are listed above; a new home
  alongside an existing one silently diverges (§15, §18).

## Related documents

- [doc_inventory.yaml](../../doc_inventory.yaml) — classification SSOT
- [test_doc_inventory.py](../../tests/integration/test_doc_inventory.py) — classification
- [test_doc_reader_path.py](../../tests/consistency/test_doc_reader_path.py) — reader entry points
- [update_docs.md](update_docs.md) — general how-to for updating documentation

