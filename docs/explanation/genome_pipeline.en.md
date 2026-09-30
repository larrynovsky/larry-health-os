<!-- translation-of: docs/explanation/genome_pipeline.md sha256:da1c7c74b97d -->
**English** · [Русский](genome_pipeline.md)

# Genomic data pipeline

> **Domain annotation without an API** (moved from BLUEPRINT, "Genomic subsystem" section, 2026-08-02):
> `genome_annotator.tag_domains_keywords()` — deterministic keyword mapping of
> a variant to health domains, without calling external services. Weighted sorting
> by clinical significance lives in `genome_annotator.SEVERITY_PRIORITY`.

> **Document type:** Explanation (Diataxis).
> To run an update: `/genome_update` in Telegram.
> Table schema: [ARCH_SNAPSHOT.md](../../ARCH_SNAPSHOT.md).

---

## Data sources

| Source | Variants | Table | Script |
|---|---|---|---|
| 23andMe TSV | Hundreds of thousands (depends on the chip) | `raw_snps` | `genome_parser.py` |
| WGS VCF (any provider) | Millions of positions | `raw_snps` (merge) | `vcf_import_pipeline.py` |
| ClinVar/MyVariant | Annotations | `genetic_variants` | `genome_annotator.py` |

Total in `raw_snps` after WGS import: **hundreds of thousands of annotated SNPs** (intersection with ClinVar; the exact number depends on the tenant's sources).

---

## One-time WGS import (vcf_import_pipeline.py)

Run once when a new WGS file is received.

```
Phase A — update_known (update known positions from WGS)
  raw_snps WHERE rsid IS NOT NULL → match against VCF by chr/pos/ref/alt
  → update genotype if the WGS call is more reliable (GQ≥20, DP≥10)

Phase B — discover_new (find new ClinVar variants)
  WGS positions not in raw_snps → batches to MyVariant.info
  → if clinically significant → add to vcf_discovery → upsert into genetic_variants

Phase C — backfill_effect_alleles (fill in effect_allele)
  All genetic_variants without effect_allele → resolve_effect_allele()
  → batch POST myvariant.info → targeted UPDATE (does not touch significance)
  → heterozygous palindromic ones are resolved automatically via HGVS/complement

Phase D — validation_report
  Concordance 23andMe vs WGS, coverage, conflicts → report in /tmp/
```

Run on Studio (after rsync from MacBook):
```bash
/opt/homebrew/bin/python3.11 vcf_import_pipeline.py --vcf ~/health/data/*.vcf.gz
```

---

## Effect allele — statuses and resolution

Each variant in `genetic_variants` has an `effect_allele_status`:

| Status | Meaning | effect_allele |
|---|---|---|
| `resolved` | Unambiguously determined (strand-aware) | Set |
| `palindromic` | ref/alt are complementary (C/G or A/T) — strand cannot be resolved | NULL |
| `palindromic_het_resolved` | Heterozygote + alt from HGVS/complement → carrier detection works | Set |
| `multiallelic_ambiguous` | >1 alt allele | NULL |
| `no_call` | Genotype is not two valid nucleotides (indels, '--') | NULL |
| `no_data` | No ref/alt in the sources | NULL |

### Carrier detection

```python
effect_allele in genotype and genotype not in ('--', 'II', 'DD')
```

### The palindromic gap and how it was closed

`effect_allele.py` deliberately returns `(None, 'palindromic')` for C/G and A/T SNPs
— strand cannot be resolved from a genotype alone. This is correct for homozygotes (CC, GG, TT, AA).

For **heterozygous** palindromic SNPs (genotype=CG or AT), both alleles are present
in the genotype string, so carrier detection works regardless of strand.

**Systemic resolution (backfill_effect_alleles.py v4, `compute_for_variant`):**

```
if status == "palindromic" and genotype in (AT, TA, CG, GC):
    Path 1: HGVS from clinical_summary  → c.187C>G → alt=G
    Path 2: complement(ref_allele)    → ref=T → alt=A
    → if alt found: status = palindromic_het_resolved
```

All new variants from Phase B are resolved automatically in Phase C.
`backfill_effect_alleles.py` skips `palindromic_het_resolved` on subsequent
runs (WHERE guard) to avoid overwriting.

**Historical script:** `fix_palindromic_het.py` — a one-time backfill,
executed on 2026-06-26 for existing variants before WGS import. Retained as
documentation of the algorithm.

---

## Monthly update (genome_update_agent.py)

The update checks whether the clinical significance of known variants has changed
in the ClinVar/MyVariant databases. There is no automatic schedule — run it manually
once a month through `/genome_update` in Telegram.

```
run_monthly_update()
  1. Takes all significant variants from the DB (limit 500)
  2. Batches of 200 → POST myvariant.info/v1/variant
  3. For each variant: new_sig vs old_sig (significance field)
  4. Updates genetic_variants if the status changed
  5. _is_significant_change(): significant only if the risk went UP
  6. If significant_changes > 0 → generate_genome_narrative() → Claude Sonnet
  7. save_genome_update_log() → genome_update_log; mark_genome_log_sent() after sending
```

---

## Severity scale and narrative logic

```
Benign(0) → Likely benign(1) → Uncertain significance(2) →
risk factor/association(3) → Likely pathogenic(4) → Pathogenic(5)
```

**A narrative is generated only when moving UP the scale.**
A decrease in risk is updated in the database, but the user is not notified.

The narrative explains: the gene, what the genotype means, what changed, and what to do.

---

## Why there is no automatic schedule

Running manually each month is an intentional decision:
- ClinVar updates are rare and do not require daily checks
- The narrative requires verification before sending (clinically sensitive information)
- Rate-limited API: a batch of 500 variants takes ~3 minutes
