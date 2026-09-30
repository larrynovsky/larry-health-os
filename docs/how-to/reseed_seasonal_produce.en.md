<!-- translation-of: docs/how-to/reseed_seasonal_produce.md sha256:4a57f6deea9f -->
**English** · [Русский](reseed_seasonal_produce.md)

# How-to: update the seasonal table (region pack, `seasonal` key)

The month → {fruits, vegetables, seafood} table is home-region data in `private/region.yaml`
(the `seasonal` key), read by `seasonal_produce.py` through `region_pack`. Seasons are stable →
rescraping is infrequent, done manually from sources. Approved sources for the current region are recorded
in the pack itself (the `sources` key) — they identify the region and therefore are not listed in public documentation.

## When
- You notice an error in a food's season.
- You want to refine/expand the list (a new food, a different source).

## Which sources qualify
- **Fruits/vegetables:** the region's official food calendar + local seasonal blogs.
- **Fish and seafood:** seasonal references for the selected region and supplier data;
  account for imported products as well as the local catch.

## Steps
1. Open the source and note the seasonal ranges for the foods.
2. Distribute them by month under `seasonal:` in the pack (interpolating a range is OK; the monthly breakdown is its representation).
   Use Russian names, like the others (to match `FOOD_BENEFITS`/`_SEAFOOD_BENEFITS`).
3. The regional catalog lives under `food_catalog:` in the same pack: sections `produce` and `seafood`,
   food alias → `{tag, why: {ru, en}, label: {ru, en}}`. This is the installation's full catalog,
   replacing the general European set from `food_genome`, rather than extending it.
   Give a NEW food a benefit tag, reasons and labels in both languages; without an entry it will not appear in the brief.
4. `pytest tests/unit/test_seasonal_produce.py tests/unit/test_food_genome.py -q` → all-pass.
5. Commit from MacBook.

## Pitfalls
- A food without an entry in `FOOD_BENEFITS`/`_SEAFOOD_BENEFITS` → silently drops out of the brief (not an error,
  just no benefit rationale). Check that the new food has been added on both sides.
- Rank in the brief: fruits(0) > vegetables(1) > seafood(2); within each group, folate-boosted foods come first.
- A per-food cooldown (semantic_key `food:seasonal:<tag>:<продукт>`) provides rotation about once a month.
