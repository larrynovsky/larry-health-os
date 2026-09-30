<!-- translation-of: docs/genome/howto_regenerate_descriptions.md sha256:1713145c6cb7 -->
**English** · [Русский](howto_regenerate_descriptions.md)

# How to regenerate Russian variant descriptions

*Document type: how-to. A step-by-step recipe for a specific task.*

**When to use:** after importing new variants from a VCF, after updating ClinVar,
or when descriptions need to be rewritten for a particular gene (for example, after refining
the system prompt).

---

## Step 1. Check how many rows have no description

Connect to the database on Studio and run:

```sql
SELECT COUNT(*)
FROM genetic_variants
WHERE description_ru IS NULL
  AND significance IN (
    'Pathogenic', 'Likely_pathogenic',
    'Pathogenic/Likely_pathogenic',
    'Likely pathogenic',
    'Pathogenic/Likely pathogenic',
    'Pathogenic, low penetrance'
  );
```

If the result is 0, everything has already been generated; there is nothing to do.

---

## Step 2 (optional). Reset descriptions for a specific gene

If you need to rewrite descriptions for just one gene rather than all empty entries:

```sql
UPDATE genetic_variants
SET description_ru = NULL
WHERE gene = 'BRCA2';
```

After this, the script in step 3 will cover only the reset rows.

---

## Step 3. Run the generation script on Studio

The script lives on MacBook at `/tmp/generate_variant_descriptions.py`, but runs
only on Studio because that is where the database is located.

If the script has already been copied to Studio, run it directly:

```bash
ssh <studio_ssh> \
  "cd ~/health_scripts && python3.11 /tmp/generate_variant_descriptions.py 2>&1 | tee /tmp/gen_desc.log"
```

If the script needs to be updated before running:

```bash
scp /tmp/generate_variant_descriptions.py <studio_ssh>:/tmp/
ssh <studio_ssh> \
  "python3.11 /tmp/generate_variant_descriptions.py 2>&1 | tee /tmp/gen_desc.log"
```

The script reads the key from `~/.health_secrets/anthropic_key`, uses the
`claude-haiku-4-5-20251001` model, and processes (gene, conditions) pairs in batches of 50.
The `description_ru` column is created automatically if it does not exist yet.

---

## Step 4. Monitor progress

In another terminal:

```bash
ssh <studio_ssh> "tail -f /tmp/gen_desc.log"
```

Illustrative output; all counts below are invented. This example uses the same
300 imported rows as the filtering explanation: generation covers 120
(gene, conditions) pairs across all 300 rows before display filtering.
With a batch size of 50, there are three batches: 50 + 50 + 20 pairs and 120 + 130 + 50 rows.

```
Pairs to generate: 120
  Batch 1/3 (50 pairs)... OK (120 rows)
  Batch 2/3 (50 pairs)... OK (130 rows)
  Batch 3/3 (20 pairs)... OK (50 rows)
Done: 120/120 pairs, 0 errors
```

If a batch ends with an `ERR:` error, the script continues with the next batch.
Rerunning is safe — rows that are already filled in are skipped.

---

## Step 5. Verify the result

```sql
SELECT gene, description_ru
FROM genetic_variants
WHERE significance IN (
  'Pathogenic', 'Likely_pathogenic',
  'Pathogenic/Likely_pathogenic',
  'Likely pathogenic',
  'Pathogenic/Likely pathogenic',
  'Pathogenic, low penetrance'
)
LIMIT 5;
```

Make sure the field is filled in, the description is in Russian, and it contains no abbreviations such as
“SNP”, “rs numbers”, or genetic notation.

Check the remaining NULL count (it should be 0, or include only rows where gene IS NULL):

```sql
SELECT COUNT(*)
FROM genetic_variants
WHERE description_ru IS NULL
  AND gene IS NOT NULL
  AND significance IN (
    'Pathogenic', 'Likely_pathogenic',
    'Pathogenic/Likely_pathogenic',
    'Likely pathogenic',
    'Pathogenic/Likely pathogenic',
    'Pathogenic, low penetrance'
  );
```

---

## Step 6. Restart the dashboard

```bash
ssh <studio_ssh> "pkill -f 'python.*dashboard.py'"
```

launchd will start the process automatically. The dashboard will refresh in a few seconds.
Open `/genome` and check that descriptions appear below the disease names.
