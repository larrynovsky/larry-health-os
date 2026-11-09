<!-- translation-of: docs/how-to/reseed_food_rules.md sha256:b61ee443b8ad -->
**English** · [Русский](reseed_food_rules.md)

# How-to: extend food rules (medical record/genome → diet)

A deterministic layer builds the food profile from curated mappings. Add rules when
a NEW condition appears in the medical record or you want to account for a new relationship. **The LLM does not decide here** —
only these mappings do. Framework: the medical record takes precedence over the genome.

## Where things live
- `methodology/clinical_kb/_index.yaml` + `food.yaml` — conditions and `frame_rule` rules
  → a mutator of the {energy, protein, micro, constraints} framework. `medical_frame()` reads them through `clinical_kb.active_entries()`.
- Genetic associations live there too: genomic triggers in `_index.yaml`, the framework in `food.yaml`. Leave weak SNPs alone.
- `food_profile.py::_SUPPRESSED_WHEN_GAIN` — which genetic constraints are lifted during weight gain/maintenance.
- `food_staples.py::STAPLES` — nonseasonal staples (meat/eggs/dairy/pantry items/fats) + tags.
- `food_staples.py::MICRO_FOODS` — micronutrient → where to get it.

## Add a new condition
1. On MacBook, add a condition with `trigger.problem_regex` to `methodology/clinical_kb/_index.yaml`.
   In `food.yaml`, under `by_condition.<id состояния>`, add a `kind: frame_rule` entry with the framework in `payload`.
   After deployment, update the tables on Studio through `clinical_kb.seed_clinical_kb()` (also called by `init_db`).
2. If you introduce a new constraint, handle it in `_annotate` (form_note) and in the "Limit"
   section inside `build_food_profile`; otherwise, it will have no effect.
3. If this is a genetic constraint that should be lifted during gain, add it to `_SUPPRESSED_WHEN_GAIN`.

## Add a food
- To `STAPLES` — `{item, cat, tags, why}`. Tags are the framework's language (see the food_staples docstring).
- Seasonal foods go in `seasonal_produce.py`, with benefits in `food_genome.FOOD_BENEFITS`/`_SEAFOOD_BENEFITS`.

## Check
The food rules test (`tests/unit/test_food_profile.py`, in the private part of the project) must pass the **flagship case** (resection ⇒ no
"lean"). Live, only on Studio: `HEALTH_DATA_DIR=<каталог тенанта> /opt/homebrew/bin/python3.11 -c "import food_profile as fp; print(fp.medical_frame())"`.

## Pitfalls
- A new constraint without handling in `_annotate`/limit → a "dead" flag (changes nothing). Check both.
- The medical record MUST override the genome: if you add a genetic calorie/fat constraint, add it to the suppression set.
- Do not treat weak SNPs as facts (honesty): use only established associations.


## Public installs without a clinical pack (owner decision, 2026-11-08)

The public `_index.yaml` declares `clinical_pack: not_installed`. This is not a
successful clinical assessment. `clinical_kb.readiness(conn)` owns the read-only
verdict across YAML and the tenant's SQLite replica: `ready`, `not_installed`, or
`failed`, with a reason. It compares the existing source/table content hashes;
missing or corrupt files/tables, drift, or fewer than two usable floor entries
fail closed. Runtime retirement/review dates can make a floor unusable.

Both integrity checks use this verdict. Only explicit, intact public absence emits
`clinical_kb: свод не установлен` (WARN, standing); seed failures remain FAIL.
The food profile, MCP nutrition frame, brief card and nutrition constitution say
that clinical restrictions are not assessed. Generated proposals are stored as
rejected, never `floor_ok=True`; already-active rules are skipped with the floor
failure logged. Nutrition constitution generation returns the notice without a
model call. No clinical rules or thresholds are added by this change.

For an older public template, add the declaration only after confirming that this
installation intentionally has no clinical pack. Do not use it to suppress a seed
failure: a mismatched replica still fails. To install a reviewed clinical pack,
remove the declaration, supply conditions and at least two usable floor entries,
and run the existing seed procedure. Existing complete packs need no marker and
no schema migration. Source and replica must agree. A rollback restores the old
code/templates; it also restores the old fail-open behavior and is not a safety fix.

Regression oracle: `tests/unit/test_clinical_kb_phase2.py` (in the private part of the project) exercises public templates,
synthetic seeded packs, corruption, consumers and rejected-rule storage. These are
software checks, not validation of clinical rules or production delivery.
