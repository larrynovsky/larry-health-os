<!-- translation-of: docs/how-to/refresh_trail_list.md sha256:180c3c4085f0 -->
**English** · [Русский](refresh_trail_list.md)

# How-to: refresh the trail list (region pack, `trails` key)

Trails are home-region data in `private/region.yaml` (the `trails` key), read by `trails.py`
through `region_pack`. The source is Overpass (OSM). Rescraping is infrequent.

## When
- You are tired of the list / want more trails.
- You want to change the radius from home (the current radius and trail count are recorded in the pack itself).

## Steps

1. Copy the scraper script (ephemeral) to Studio (address: `private/infra.yaml`, `studio_ssh`):
   ```
   scp /tmp/overpass_trails.py <studio_ssh>:/tmp/
   ```
   Parameters at the top of the script: `HOME=(lat, lon)` — home from `system_config` (`location.home_lat/lon`),
   `RADIUS_KM`, `BBOX` (recalculate for the radius: lat±(radius/111), lon±(radius/(111·cos lat))).

2. Run it on Studio (it has network access; Overpass can be slow — retries use mirrors):
   ```
   ssh <studio_ssh> "nohup /opt/homebrew/bin/python3.11 /tmp/overpass_trails.py > /tmp/trails_out.txt 2>&1 &"
   ```
   After about 1–2 minutes: `ssh <studio_ssh> "cat /tmp/trails_out.txt"`. The first line is TOTAL, followed by JSON lines.

3. Format the top N (by proximity) as a list of `{name, km, note}`:
   - sanitize km (OSM sometimes returns garbage such as 4780 — set it to zero; keep only 0<km<40);
   - names must use ONLY Latin characters (no improvised transliteration → errors);
   - note = distance from home.

4. Write the list under `trails:` in `private/region.yaml` (on MacBook). This is a private area — the list does not go
   into the public repository.

5. Run `pytest tests/unit/test_trails.py -q` → all-pass. Commit from MacBook (automatic deployment).

## Pitfalls
- Overpass 406 → a `User-Agent` header is required. 504 → the mirror is busy; retry (kumi.systems).
- Gate cooldown per trail: more trails = less frequent repetition. 50 trails ≈ a repeat about every 50 weekends.
- `pick_trail(day_index)` = deterministic rotation by date ordinal; list order matters.

