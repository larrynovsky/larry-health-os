<!-- translation-of: docs/how-to/adjudicate_quarantine.md sha256:a19e123e1b2b -->
**English** · [Русский](adjudicate_quarantine.md)

# How to release a pair from quarantine (issue a verdict)

This recipe was written FROM COMMANDS EXECUTED on 2026-07-29 (a probe
from the work plans, 11 out of 11 oracles; the probe itself is in the private part of the project), not from memory or guesswork. All exit
codes and text below were observed, not assumed.

## When you need this

A metric pair has entered the gate's pass-set for the first time. This is not yet a finding: the pass-set's composition fluctuates, and
one successful week is not a decision. The pair becomes `pending_adjudication`, and until
your verdict, readers (constitutions, `gp_context`) present it with the label “composition changed,
awaiting a verdict,” rather than as a relationship.

This state does not resolve itself. A stable next run does NOT release it from quarantine —
before 2026-07-26 it did, and that was fixed as a bug (P1-03).

## 1. View pending pairs

```bash
ssh Studio
cd ~/health_scripts
python3.11 scripts/adjudicate_quarantine.py
```

The output contains rows like:

```
  [pending ] sleep_deep×hrv                   D      entered 2026-07-29 (0d) epoch='signal_family_v7'
```

`эпоха` matters: the verdict applies to a pair WITHIN a method epoch. An epoch change (a change to
`signal_family`) reopens the question for all pairs — this is by design, not a bug.

## 2. Look at the numbers, not the list

Issue a verdict by looking at the data — hence a CLI, rather than a button on your phone. What to examine:
how many runs the pair has persisted through, whether epochs have changed, and whether there is a clinical explanation.
`docs/explanation/validation_gate.md` explains what passing the gate means in the first place.

## 3. Issue a verdict

Admit into the belief:

```bash
python3.11 scripts/adjudicate_quarantine.py \
    --pair "sleep_deep×hrv" --verdict admit \
    --reason "holds for 4 runs, epochs unchanged"
```

Reject as an artifact:

```bash
python3.11 scripts/adjudicate_quarantine.py \
    --pair "sleep_deep×sleep_rem" --verdict reject \
    --reason "both metrics derive from the same sleep, the link is tautological"
```

**A reason is required.** An empty one (`--reason "   "`) is rejected with exit code 2 —
verified. The reason is the only thing a future reader will read when asking
“why is this relationship in the constitution?”

Exit codes: `0` — recorded · `1` — no such pending pair (check the name and epoch) ·
`2` — usage error · `3` — database unavailable.

## 4. Verify that the verdict REACHED the reader, not just that it was recorded

This is a separate step, not paranoia: the CLI was once already broken — it called `resolve_quarantine`
without `method_epoch`, printed success, and changed nothing.

```bash
python3.11 scripts/adjudicate_quarantine.py --all
```

The row should show `→ admitted (human, 2026-07-29)`. But a record in a table is not yet
a decision that has reached the reader. The full check is the next run:

```bash
python3.11 longitudinal_analysis.py
```

After it, in the published belief (`agent_reports`, `agent_type='longitudinal_analysis'`):

- the admitted pair is marked `online_status: "admitted"` — the verdict's provenance;
- the rejected pair is **absent entirely** — absent, not labeled;
- a pair without a verdict is marked `online_status: "pending_adjudication"`.

If a rejected pair appears as a finding without a label, this is regression VG-R5-01, in which
the human “no” is ignored. This probe fails on it:

```bash
# on a SNAPSHOT of the canon, not on the live database (owner decision R-7)
ssh Studio 'cd ~/health_staging && HEALTH_DATA_DIR=~/health_staging \
    HEALTH_SECRETS_DIR=~/health_staging/.secrets_staging \
    python3.11 plans/probe_quarantine_exit_2026-07-29.py --phase live'   # the probe lives in the private part of the project
```

## What this recipe does NOT cover

It covers **exit** from quarantine. How a pair gets there — the entry path — is neither described here nor
confirmed by the probe: the pairs were put into the state manually during the run.

A separate issue found while writing: `queue_quarantine` silently swallows a schema violation.
A family outside `CHECK (family IN ('D','A','q_lag'))` results in `INSERT OR IGNORE` → 0 without a single
signal — indistinguishable from “the pair is already there.” This is currently unreachable because the only
producer takes families from the keys of the pass-set snapshot, but nothing enforces the relationship “snapshot keys = schema
CHECK.”

## Related

- `docs/explanation/validation_gate.md` — why the gate exists and what its states mean
- The repair thread and the owner's decisions are in the private part of the project
- Stagnation sensor: `integrity_tests` warns if a pair stays pending past the deadline
