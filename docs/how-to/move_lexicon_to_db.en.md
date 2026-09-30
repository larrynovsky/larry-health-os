<!-- translation-of: docs/how-to/move_lexicon_to_db.md sha256:20bd95a5954e -->
**English** · [Русский](move_lexicon_to_db.md)

# How-to: respond to a §9 lexicon sensor flag

The sensor found a domain-specific list of strings (`*_TERMS/_MARKERS/_FLAGS/_LEXICON/_KEYWORDS`) in code.
Under §9, meaningful data belongs in the database, not in `.py`. Choose one of three paths based on what the constant means.

## Path 1. This is patient/clinical data → move it to system_config (default)

Use `memory_salience.SYMPTOM_TERMS` as the reference (already moved). Follow the pattern:

1. Keep the literal in the module as a **fallback** and read it through an accessor:
   ```python
   import memory_config as _mc
   SYMPTOM_TERMS = ("боль", "тошнота", ...)          # code = fallback
   def symptom_terms():
       return _mc.get_lexicon("symptom_terms", SYMPTOM_TERMS)
   ```
   All consumers call `symptom_terms()`, not the constant directly.
2. Seed the data in the database (write-through, once at initialization):
   ```python
   memory_config.seed(lexicons={"symptom_terms": list(SYMPTOM_TERMS)}, params={})
   ```
3. Register the verdict in `lexicon_registry.REGISTERED`:
   ```python
   "memory_salience.SYMPTOM_TERMS": {
       "verdict": "db-backed", "oracle": "owner",
       "note": "memory_lexicon.symptom_terms in system_config; code = fallback"},
   ```
Changing the list now means changing a database/dashboard row, not a `git commit`.

## Path 2. This is linguistics/infrastructure/representation (not patient data) → structural

Negation markers, system behavior triggers, and infrastructure heuristics (`_NEG_MARKERS`,
`_DEV_CLONE_MARKERS`) stay in code — they are not personal (§9: structural and semantic
signals belong in code). Register them as `structural`:
```python
"memory_salience._NEG_MARKERS": {
    "verdict": "structural", "oracle": "engineer",
    "note": "negation markers are linguistics, not a clinical list"},
```
Do not overuse this: `structural` means "not about the patient and not about methodology." If you are unsure,
use Path 1.

## Path 3. Legacy, cannot move it now → legacy (count frozen)

Keep the existing list, but it **must not grow** (an occurrence-count ratchet):
```python
"module.OLD_TERMS": {"verdict": "legacy", "count": 12,   # current number of elements
    "oracle": "owner", "note": "move to DB — task #NN"},
```
Add a term → `count` grows → the sensor blocks it until you update the database or deliberately update the count.

## Check locally
```bash
python3.11 lexicon_registry.py          # prints scan + findings, exit 1 if any
python3.11 -m pytest tests/unit/test_lexicon_registry.py -q
```

## Why this works this way (short reference)
The full explanation is in `дизайн_датчик_§9_lexicon_2026-07-07.md` (iCloud): a hardcoded list is a perpetually
stale replica of the single source (the database); the sensor checks for a write-through path,
not for a default. The registry is the single home for approved exceptions (not scattered `# ok` comments).

