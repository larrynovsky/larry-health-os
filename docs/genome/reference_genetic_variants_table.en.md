<!-- translation-of: docs/genome/reference_genetic_variants_table.md sha256:e180d610f685 -->
**English** · [Русский](reference_genetic_variants_table.md)

# Reference: the genetic_variants table and genome() view

*Document type: reference. For consultation while working with code or the database.*

---

## Schema of the `genetic_variants` table

| Column | Type | Contents |
|---|---|---|
| `rsid` | TEXT | SNP identifier from dbSNP (e.g., `rs1799945`) |
| `gene` | TEXT | Gene symbol (e.g., `HFE`, `BRCA2`) |
| `genotype` | TEXT | Two-character string of the user's alleles (e.g., `AG`, `CC`) |
| `significance` | TEXT | ClinVar assessment (see the list below) |
| `conditions` | TEXT | JSON array of strings containing disease/condition names |
| `clinical_summary` | TEXT | HGVS notation (technical, for exports) |
| `effect_allele` | TEXT | One character — the pathogenic allele according to ClinVar; NULL if unknown |
| `effect_allele_status` | TEXT | Origin label for `effect_allele` (pipeline technical detail) |
| `description_ru` | TEXT | Russian description of ≤25 words; NULL if not yet generated |

---

## `significance` values included in /genome

```
'Pathogenic'
'Likely_pathogenic'
'Pathogenic/Likely_pathogenic'
'Likely pathogenic'
'Pathogenic/Likely pathogenic'
'Pathogenic, low penetrance'
```

Values such as `Benign`, `Likely benign`, `Uncertain significance`, `Conflicting`, and others
are not displayed. Rows with these statuses exist in the table, but the CTE filters them out.

---

## Zygosity classification in the CTE

Computed in the `classified` subquery of the `genome()` function in `dashboard_routers/views.py`.

| zyg_rank | Condition | Meaning | Icon on the page |
|---|---|---|---|
| 0 | `effect_allele` is not NULL, both characters of `genotype` = `effect_allele` | homozygous | “both copies” (red) |
| 1 | `effect_allele` is not NULL, exactly one character of `genotype` = `effect_allele` | heterozygous / carrier | “carrier” (yellow) |
| 2 | `effect_allele IS NULL` | unknown zygosity | no icon |
| 99 | `effect_allele` is not NULL, neither character matches | normal genotype | row is not displayed |

Zygosity determination works only for two-character `genotype` values (`length(genotype)=2`).
Rows with longer or empty values receive zyg_rank=2 (unknown).

After grouping with `GROUP BY gene, conditions`, the resulting zygosity = `MIN(zyg_rank)`,
i.e., the most concerning variant among all SNPs in the pair is selected.

---

## Sorting in /genome

Rows are returned in `ORDER BY _sig_order, _zyg_order, gene` order:

1. `Pathogenic` first (sig_rank=0), then `Likely pathogenic` (sig_rank=1)
2. Within each significance group: homozygous (0) → heterozygous (1) → unknown (2)
3. Within each zygosity group: alphabetical by `gene`

---

## generate_variant_descriptions.py

**Location:** `/tmp/generate_variant_descriptions.py` (MacBook and Studio)

**Run:**
```bash
ssh <studio_ssh> "python3.11 /tmp/generate_variant_descriptions.py"
```

**What it does:**
1. Reads unique `(gene, conditions)` pairs from `genetic_variants` where `description_ru IS NULL` and `significance` is in the pathogenic list
2. Groups them into batches of 50
3. Sends each batch to Claude Haiku (`claude-haiku-4-5-20251001`) through the Anthropic API
4. Parses the JSON response and writes `description_ru` to rows whose `(gene, conditions)` match after normalization

**API key:** `~/.health_secrets/anthropic_key`

**System prompt (description rules):**
- One sentence in Russian, no more than 25 words
- Explain the gene's function and associated risk for a nonspecialist
- Do not use protein abbreviations, rs numbers, or molecular notation
- Do not use the words “pathogenic”, “variant”, or “mutation” — use “risk” or “organ function” instead
- If `conditions` is empty or “not provided”, briefly describe only the gene's function

**Idempotency:** rerunning is safe; rows that are already filled in are skipped.
The `description_ru` column is created automatically if it does not exist (`ALTER TABLE ADD COLUMN`).
