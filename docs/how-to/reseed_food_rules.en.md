<!-- translation-of: docs/how-to/reseed_food_rules.md sha256:2699f35452b9 -->
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

