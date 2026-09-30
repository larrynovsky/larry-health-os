<!-- translation-of: docs/how-to/add_new_uc.md sha256:20b33fd62f24 -->
**English** · [Русский](add_new_uc.md)

# How to add a new UC and test

> **Document type:** How-to (Diataxis).
> Context: `docs/explanation/test_architecture.md` — why this process exists.
> Full UC catalog: `USE_CASES.md`. Machine-readable index: `uc_index.yaml`.

---

## Scenario 1: a new UC from scratch

### Step 1: Add an entry to the §3 catalog in `USE_CASES.md`

Find the appropriate group (A import / B analytics / ... / X external deps).
Add a row to the table:

```markdown
| `UC-X-NN` | Brief pragmatics | P0 | check | intended | proposed |
```

- `intended` if there is no code yet.
- `partial` if part of it works.
- `implemented` if it already works (but then why the UC?).
- `proposed` until you confirm it.

### Step 2: Detailed description in §4 (P0 only)

Copy the template from an existing P0 UC:

```markdown
#### UC-X-NN: Title

**Lifecycle:** `status=intended · confirmation=proposed · ...`
**Owner:** `module.py`

**Given:** ...

**When:** ...

**Then:**
1. ...

**B (negative invariants):**
- ...

**E (cross-check):**
- ...

**NOT-Then:**
- ...
```

### Step 3: Confirm the UC

When you are satisfied with the wording, change `confirmation: proposed → confirmed`
in the §3 catalog. This unlocks test generation.

### Step 4: Create a test plan

```bash
# In tests/plans/UC-X-NN.md
```

Structure: what you are checking, oracles (B/E/C/D), which fixtures, and what it does NOT cover.
Example: `tests/plans/UC-I-02.md`.

### Step 5: Generate a skeleton

```bash
python3.11 generate_test.py UC-X-NN --dry-run    # show the plan
python3.11 generate_test.py UC-X-NN              # waits for yes → creates skeleton
python3.11 generate_test.py UC-X-NN --auto-confirm  # for CI/Claude
```

The skeleton goes into `tests/{layer}/test_uc_x_nn_*.py`, where layer comes from `type`.
`generate_test.py` will refuse if `confirmation != confirmed`.

### Step 6: Fill in the tests

The skeleton contains `pytest.skip("заглушка")`. Replace it with real checks from the plan.

### Step 7: Update `uc_index.yaml`

Add an entry with `modules / data / tests / oracle / risk`. Run
`validate_uc_index.py` — the result should be OK.

### Step 8: Run the tests

```bash
python3.11 -m pytest tests/{layer}/test_uc_x_nn_*.py -v
```

If they pass, commit.

---

## Scenario 2: a UC appears automatically from a git diff (UC-J-01)

After a commit within scope B, `propose_uc.py` creates a file in
`tests/plans/proposed/{date}_{sha}.md`.

```bash
# Manual trigger (normally via the post-commit hook)
python3.11 propose_uc.py
```

It contains a list of changed files and affected UC groups. Next:

1. Read the proposal.
2. Decide: does the commit change a UC contract?
   - Yes → update the corresponding UC in `USE_CASES.md` (Then/B/E/NOT-Then).
   - No → close the proposal (move it to `proposed/applied/`).

`propose_uc.py` **never writes to `USE_CASES.md` on its own**. This is an explicit
guard invariant of UC-J-01.

---

## Scenario 3: Change an existing UC

1. Open the §3 catalog in `USE_CASES.md`. Find the entry.
2. You may need to change `confirmation: confirmed → proposed` (new
   behavior requires review).
3. Update the detailed description in §4 (Then/B/E/NOT-Then).
4. If the UC has `tests` in `uc_index.yaml`, review the tests:
   is the old logic still relevant? Should you add new checks?
5. Run `validate_uc_index.py` — the result should be OK.

---

## Scenario 4: a UC changes status

| Transition | What to do |
|---|---|
| `intended` → `partial` | A partial implementation is available. Remove `xfail` from tests that now pass. |
| `partial` → `implemented` | Full implementation. Remove all `xfail` markers. If the tests pass, the UC is closed. |
| `implemented` → `partial` | A regression is accepted as “temporary.” Mark failing tests with `xfail` and a link to the reason. |
| any → `rejected` | The UC has been deemed unnecessary. Delete the tests and plan; keep the catalog entry for the record. |

---

## Related documents

- `USE_CASES.md` — UC catalog.
- `uc_index.yaml` + `validate_uc_index.py` — machine-readable index.
- `propose_uc.py` (UC-J-01) and `generate_test.py` (UC-J-02) — meta-infrastructure.
- `docs/explanation/test_architecture.md` — why this process exists.
- `tests/README.md` — directory structure.
