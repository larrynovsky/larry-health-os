<!-- translation-of: docs/how-to/record_analyte_norm_verdict.md sha256:3167e1ba5bb9 -->
**English** · [Русский](record_analyte_norm_verdict.md)

# How to defer or close a question about an analyte's normal range

The nightly sensor `check_norm_coverage` lists analytes measured ≥ `norm.coverage_min_n` times
with no normal range in any home (threshold from a document · reference range on the report · `lab_refs`). If the answer
is "no normal range by decision" or "waiting for a doctor until a given date," record a verdict; otherwise, the row will
arrive every day as new (precedent: `Chol_HDL_ratio`, `Albumin_Globulin_ratio` — the clinic's report
prints them without Normal values).

On Studio (`~/health_scripts`, health.db exists only there):

```bash
/opt/homebrew/bin/python3.11 -c "
import health_db as db
print(db.set_analyte_norm_verdict(
    'Chol_HDL_ratio',                       # name is normalised via lab_canon
    verdict='deferred',                     # deferred (awaiting oracle) | no_norm (no norm by decision)
    rationale='calculated ratio; the form prints no reference; question for the doctor',
    oracle='owner',                         # owner | doctor:<who>
    review_at='2027-01-05'))"               # REQUIRED and > decided_on: after it the sensor rings again
```

Check: `python3.11 -c "import health_db as db; print(db.analyte_norm_verdicts())"` — active
verdicts; `python3.11 -c "import integrity_tests as I; print(I.check_norm_coverage())"` — the count
without this analyte. Type, a nonempty rationale, and `review_at > decided_on` are enforced by CHECK constraints in SQLite.

Why a date is mandatory even for `no_norm`: the lab may start printing a reference range, and
the verdict must not outlive its subject (§18). The reader does not return expired verdicts — the analyte
automatically returns to the sensor. Explanation: `docs/explanation/norm_kinds.md`.

