<!-- translation-of: docs/genome/explanation_genome_variant_filtering.md sha256:7eb3b163e369 -->
**English** · [Русский](explanation_genome_variant_filtering.md)

# How genomic variant filtering works

*Document type: explanation. Answers “why it works this way,” not “what to do.”*

---

## Illustrative example: 90 of 300 rows remain

All counts in this example are invented. Suppose the `genetic_variants` table
contains 300 rows after a VCF file is imported.
Each row is one SNP (a variation at one position in the genome) for which ClinVar
has documented an association with a disease or condition.

Most of these rows describe positions where the person has the usual (reference)
nucleotide: the gene functions normally, and there is no risk. These rows meet the
condition `zyg_rank = 99` (“normal genotype”) and are filtered out entirely.
In this illustrative example, 210 rows are filtered out, leaving 90 — those in which at least one allele
matches a known pathogenic variant, or the zygosity could not be
determined (in that case, risk cannot be ruled out, and the row is retained with an “unknown” label).

---

## What zygosity is and why it matters

Each gene in the human genome is present in two copies — one from each
parent. “Zygosity” describes how many of these copies contain the pathogenic
variant.

A **homozygous variant** (both copies affected) means the person has no
“backup” working copy of the gene. Both copies encode an altered protein. For
diseases with recessive inheritance, this is a necessary condition for the disease
to manifest; for dominant diseases, the risk generally doubles compared with a
heterozygote. In the dashboard, these variants have a red “both copies” icon.

A **heterozygous variant** (“carrier”) means one copy works and the other is
altered. With recessive diseases, the person is usually healthy but can pass the
variant on to their children. With dominant diseases or incomplete penetrance, the risk is substantially lower
than for a homozygote, but it is not zero. The yellow “carrier” icon indicates
this intermediate status.

**Unknown zygosity** occurs when no “effect allele” has been recorded in the database
for the variant. Without that reference point, it is impossible to determine whether
the pathogenic variant is present in the genotype at all. These rows are not filtered out — risk
cannot be ruled out — but no icon is displayed.

---

## Why the “significance” column alone cannot be trusted

ClinVar assigns “pathogenic” or “likely pathogenic” statuses to variants based
on population data. But all of these statistics were collected from people who have the variant
in at least one copy. For an individual, the significance status
says only “this variant is potentially dangerous” — not “you have it.”

Without zygosity, the significance column is information about the world, not about you. That is why
the `zyg_rank != 99` filter comes before grouping by gene and disease: rows where
the genotype does not match the pathogenic variant in either copy are removed before
the data reaches the page.

---

## Why group by gene and disease rather than by individual SNP

One gene can affect one disease through dozens of different positions. In the
`genetic_variants` table, there may be several hundred rows for the BRCA2 gene
and a single condition — each corresponding to a separate sequence variant. Displaying them
individually would be pointless: clinically, they all mean the same thing —
an increased risk of a particular disease through disruption of this gene's function.

Grouping with `GROUP BY gene, conditions` collapses all variants for one
“gene + disease” pair into a single row. The resulting zygosity is the minimum
rank: if at least one variant in the pair is homozygous, the pair is marked
homozygous; if there are only heterozygous variants, it is marked heterozygous. This is a conservative
approach: we do not understate the risk.

---

## Why the descriptions are in Russian rather than technical notation

The `clinical_summary` column in the database stores standard molecular genetic notation,
which looks something like “NM_000059.4(BRCA2):c.8167G>A”. For a specialist, this is
an exact address of a mutation in an RNA sequence. For everyone else, it is an opaque
string that explains neither what the gene does nor why the variant is dangerous.

The `description_ru` column contains one sentence in Russian, written so that
someone without medical training can understand it: what the gene does in the body and
what risk or condition is associated with this variant. A maximum of 25 words is enough
to convey the main point without overloading the page with text when there are more than a thousand variants.

Descriptions are generated automatically through Claude Haiku and saved in the database.
Technical details of generation are in `howto_regenerate_descriptions.md`.
