<!-- translation-of: docs/how-to/add_visual_domain.md sha256:c99d6286ca3f -->
**English** · [Русский](add_visual_domain.md)

# How to add a visual-intake domain (with data, without code)

> **Document type:** How-to (Diátaxis). The intent is in
> [explanation/symptom_intake_hypothesis](../explanation/symptom_intake_hypothesis.md).
>
> A verifiable cost of generalization: add a new domain (teeth, nails, eye) through ROWS in
> `visual_domains`, rather than changes to `.py`. If you have to touch the engine, the generalization is wrong.

## When

The symptom-intake flow needs to accept a new body area (currently, one demo row is seeded).
There are NO red flags or clinical thresholds here (deliberately — see the explanation section “why
safety is not danger detection”): a domain carries only regions and dialog parameters.

## Steps

1. **Decide what the domain is.** Use a Latin-character slug (`dental`, `nails`, `hair`, `eye`). It is
   a value in the database and must not appear in code.

2. **Seed the row** through `visual_db.upsert_visual_domain`:

   ```python
   import visual_db
   visual_db.upsert_visual_domain(
       "dental",
       region_options=["tooth", "gum", "tongue", "lip"],
       dialog_params={"min_candidates": 3},   # overrides DIFF_MIN_CANDIDATES if desired
   )
   ```

   Run on Studio (the primary machine): `ssh <studio_ssh> "cd ~/health_scripts &&
   /opt/homebrew/bin/python3.11 -c '...'"`. A write on a non-primary machine will fail (§8).

3. **Check the §9 invariant.** The domain name must NOT appear in code:

   ```bash
   grep -rn "dental" symptom_intake.py handlers/symptom.py specialists/symptom_intake_system.txt
   # should be empty
   ```

   If you want, add a sentinel disease name for this domain to `diagnosis_guard.SITES`
   (first make sure it is absent from the live files, or the guard will fail).

4. **Do not change anything in the engine.** `symptom_intake.step`, the prompt, routing, and sensors are
   domain-agnostic. If you feel tempted to add `if domain == "dental"`, that violates §9;
   the difference must live in `dialog_params`/`region_options`, not in a code branch.

## How to verify that the domain works

- `visual_db.get_visual_domain("dental")` returns the seeded regions/parameters.
- Run elicitation on an invented case in this domain (mock `symptom_intake._call_model`):
  the dialog gathers the outline and moves to `needs_specialist` WITHOUT code changes.
- A `python -m` run of the `check_symptom_prompt_discipline` sensor passes (§9 is not violated).

## What NOT to do

- Do not hardcode domain regions/thresholds in `.py` — this is disguised hardcoding that violates §9.
- Do not add red flags / urgency triage (see the explanation — this was deliberately rejected).
- Do not create a domain on a non-primary host (the database write will fail, as it should).
