<!-- translation-of: docs/reference/norm_sources.md sha256:1137e7a7256d -->
**English** · [Русский](norm_sources.md)

# Where to obtain norms: a map of accepted sources

*Document type: reference. A catalog of external norm sources by KIND (`docs/explanation/norm_kinds.md`), with assessments of reliability, access, and review cadence.*
*Compiled on 2026-09-02 from searches and primary documents; anything that could not be opened is marked. No number from here has been transferred into `absolute_thresholds`: transfer requires a human verdict (§13).*

---

## 0. Main finding from the measurement

The thresholds that `safety_net` has relied on since April are not “random junk” but **half-remembered CTCAE**: HGB 12/10/8 are anemia grades 1/2/3 (`<LLN–10`, `<10–8`, `<8 g/dL`); PLT 100/50/20 are distorted grades (CTCAE uses 75/50/25 ×10³); ALT “3x/7x ULN” — CTCAE gives 3x/5x/20x. In other words, the model retold a cancer treatment toxicity scale without naming it and got the numbers wrong. This is both bad news (the numbers are wrong) and good news: **the right document for the “decision threshold” kind for a patient after cancer is CTCAE; it is open, versioned, and dated.**

Second: CTCAE expresses grades as **multiples of ULN/LLN** — that is, it requires the reference from *the laboratory that performed the test* as input. Decision thresholds do not replace the reference; they are **composed** with it. This directly determines the architecture: `reference_interval` comes from the report (already in `lab_results.ref_low/ref_high`), `decision_threshold` comes from the document as a multiplier or an absolute value, and one does not work without the other.

Third — and here I am correcting myself on the same day. The first measurement, based on one study (Erden 2008, [DOI](https://doi.org/10.1080/00365510701601699), 49 healthy subjects, 4 time points), gave CV_I 27–31% and RCV 65–73% for CA 19-9/CEA, and I wrote “our 50% trend threshold is below the noise.” **The EFLM database returns JSON** (`GET https://biologicalvariation.eu/api/measurands`, 326 measurands, no key) with meta-estimates from BIVAC-compatible studies: CV_I for CEA — **median 6.8%** (CI 6.4–30.9, n=3), for CA 19-9 — **4.3%** (4.0–27.2, n=3), for ALT 12.6%, creatinine 4.4%, Hb 2.7%. Erden is at the upper end of the interval, an outlier. With the median and desirable analytical precision CV_A = 0.5·CV_I, the one-sided RCV for CEA ≈ 20%, CA 19-9 ≈ 13% — our 50% is **not below the noise but three times above it**, meaning it is insensitive. The lesson is the same as in §0 item 1: one study is not a source; a rated meta-estimate is a source, and it is machine-readable.

## 1. Kind 1 — reference interval (statistics of healthy people)

| Source | What it provides | Access | Reliability | Cadence |
|---|---|---|---|---|
| **Lab report** (`lab_results.ref_low/ref_high`) | The interval for the exact method and instrument that measured your sample | Available after importing the report; interval coverage depends on the imported documents | **The only correct kind 1 source under CLSI EP28-A3c**: the lab must establish or verify it for its method | Changes when the method changes — sensor `check_norm_vs_lab_reference` catches a change in the mode |
| **CLSI EP28-A3c** (Defining, Establishing and Verifying Reference Intervals) | Methodology: ≥120 reference subjects, 2.5–97.5 percentiles, partitions by sex/age, verification of transfer using 20 samples | Paid standard (ANSI webstore) | Normative | 2010, in effect |
| **IFCC C-RIDL** and **GRID** (grid.ifcc.org) | Global multicenter database of RI studies, indexed by LOINC, country, sex/age; NORIP is already included | Public website; API/export — unconfirmed (pages do not serve content to bots) | Curated, but a *study catalog*, not “truth” | TF-GRID launched in 2022–2024, growing |
| **AACB — harmonized Australasian RIs** | Ready-to-use numbers for 17 analytes: Na 135–145, K 3.5–5.2, Cl 95–110, HCO₃ 22–32, creatinine 60–110 (M) / 45–90 (F) µmol/L, Ca 2.10–2.60, PO₄ 0.75–1.50, Mg 0.70–1.10, LDH 120–250, ALP 30–110, total protein 60–80; 2nd wave: bilirubin, CK, ALT/AST (non-P5P), GGT, lipase | Open position paper PDF | High: bias studies on 8 platforms + “Aussie Normals” (1876 healthy subjects) + data mining + consensus; **explicitly explains why harmonization failed for GGT and lipase** | 2014 / 2015; “workshops are planned,” no formal cycle |
| **NORIP** (Nordic Reference Interval Project 2000) | 25 biochemical analytes, 3000 subjects, 5 countries, common intervals | Open (Scand J Clin Lab Invest 2004), in GRID | A classic, but from 2004 and based on the methods of that time | Not updated |
| **UK Pathology Harmony** | Harmonized biochemistry and hematology intervals for the NHS | Publications from 2011–2012; actual tables are in NHS trust reference guides | Pragmatic consensus, not always evidence-based | One-time |
| **Reference laboratory catalogs** (Mayo Clinic Labs Test Catalog, ARUP) | Intervals for their methods, with partitions; Mayo publishes an annual “Laboratory Reference Edition” PDF | Open websites; no machine-readable API | High for *their* methods; cannot be transferred to a local lab's report without verification | Annually |
| **NHANES-derived RIs** (CDC) | US population intervals for individual analytes (RBC, Mg…) in papers | Raw NHANES data are open; intervals are in publications | Large N, but US population and methods | By publication |
| **Indirect RI (refineR, reflimR, TMC)** | Deriving an interval from routine lab data | Open R packages | A method for laboratories, not for a patient: we have N=1 | — |

**What applies to the project.** Kind 1 is covered by **the lab report**, and only by it; harmonized lists (AACB) are a second witness for checking a report for absurd values, not a replacement. Mayo/ARUP catalogs are a source of *partitions and units*, not numbers.

## 2. Kind 4 — personal norm and biological variation

| Source | What it provides | Access | Reliability | Cadence |
|---|---|---|---|---|
| **EFLM Biological Variation Database** (biologicalvariation.eu) | CV_I, CV_G for 132 measurands (global meta-estimates), >2400 datasets from ~550 publications; RCV calculator with your CV_A; analytical performance specifications | **Free, no registration; JSON API `/api/measurands`** (median, CI, number of studies, matrix, `updated_at` — a ready-made freshness sensor) | Every study assessed with BIVAC (grade A–D), meta-analysis includes only BIVAC-compatible studies; STARBIV reporting standard (2024) | Live, growing |
| Primary BV studies on tumor markers | Erden 2008 (see §0): CA19-9/CEA/AFP on Architect | PubMed | One study, 49 healthy subjects, 4 time points — likely a low BIVAC grade | — |

**What applies.** RCV = √2 · Z · √(CV_A² + CV_I²). CV_I comes from EFLM; ask the laboratory for CV_A (within-laboratory reproducibility by analyte; they are required to know it), or use EFLM specifications as an upper bound. This is the only correct way to answer “is the shift in the series larger than the noise?” — and it beats our `p10_personal` over 90 days and the 50% `lab_trend_thresholds`. An index of individuality CV_I/CV_G < 0.6 indicates that the population interval is useless for the analyte and a personal baseline is needed (creatinine, TSH, ferritin, tumor markers).

## 3. Kind 2 — decision threshold (from outcomes, a versioned document)

| Source | What it provides | Access | Reliability | Cadence |
|---|---|---|---|---|
| **NCI CTCAE** (Common Terminology Criteria for Adverse Events) | Grades 1–4 of lab abnormalities: anemia `<LLN–10 / <10–8 / <8 g/dL`; PLT `<LLN–75 / 75–50 / 50–25 / <25 ×10³`; neutrophils `<LLN–1.5 / 1.5–1.0 / 1.0–0.5 / <0.5`; leukocytes `<LLN–3.0 / 3–2 / 2–1 / <1`; lymphocytes `<LLN–0.8 / 0.8–0.5 / 0.5–0.2 / <0.2`; ALT/AST `>ULN–3× / 3–5× / 5–20× / >20×`; creatinine `>ULN–1.5× / 1.5–3× / 3–6× / >6×`; bilirubin `>ULN–1.5× / 1.5–3× / 3–10× / >10×`; amylase `>ULN–1.5× / 1.5–2× / >2×`; CPK `>ULN–2.5× / 2.5–5× / 5–10× / >10×`; albumin `<LLN–3.0 / 3.0–2.0 / <2.0 g/dL`; glucose `>ULN–160 / 160–250 / 250–500 / >500 mg/dL`; K `<LLN–3.0 / 3.0–2.5 / 2.5–2.0 / <2.0` and `>ULN–5.5 / 5.5–6 / 6–7 / >7`; Na `<LLN–130 / 130–120 / 120–110 / <110`; Ca `<LLN–8.0 / 8–7 / 7–6 / <6 mg/dL` | **Open PDF** at dctd.cancer.gov; terminology in NCI EVS (machine-readable) | Normative oncology standard; **v5.0 — 11/27/2017, v6.0 — 07/22/2025** (based on MedDRA 28.0); v6 vs v5 changes not checked | Dated versions — exactly what `next_review` needs |
| **IFCC C-RIDL: “Distinguishing RI and CDL”** (Ozarda et al. 2018, [DOI](https://doi.org/10.1080/10408363.2018.1482256)) | Definitions: CDL relates to outcome risk or is diagnostic; RI is the distribution in healthy people; CDL examples: lipids, glucose, HbA1c, tumor markers | PubMed | Normative committee review | — |
| **ADA Standards of Care** (diabetes) | Fasting glucose ≥126 mg/dL, HbA1c ≥6.5% — diagnostic CDLs; prediabetes 100–125 / 5.7–6.4 | Open (Diabetes Care, January each year) | High | **Annually**, January |
| **KDIGO CKD** | eGFR/albuminuria — stages; creatinine is not used as a CDL; eGFR by CKD-EPI 2021 is used | Open 2024 PDF; draft Diabetes-CKD 2026 update in public review | High | 2012 → 2024 → 2026 (irregular, ~4–12 years) |
| **ESC/EAS dyslipidemia, ESC/AHA hypertension** | LDL targets by risk category (already kind 3!), BP ≥140/90 (ESC) / ≥130/80 (AHA) | Open | High | 2019/2021 → review ~5 years |
| **NACB/AACC LMPG on tumor markers** (Sturgeon et al. 2008, [DOI](https://doi.org/10.1373/clinchem.2008.105601); quality requirements — Sturgeon, Hoffman, Diamandis 2008, [DOI](https://doi.org/10.1373/clinchem.2008.105494)) | CEA recommended for postoperative colorectal cancer surveillance; serial measurements using one method; significant change assessed through RCV, confirmed with a repeat sample | PubMed, full text in PMC | High, but **2008** | Not updated; EGTM provides updates for individual tumors |
| **ASCO 2013 (CCO endorsement)** — surveillance after CRC (Meyerhardt et al., [DOI](https://doi.org/10.1200/JCO.2013.50.7442)) | Examination + **CEA every 3–6 months for 5 years**; chest/abdominal CT annually for 3 years; colonoscopy after 1 year, then every 5 | PubMed | High | 2013; NCCN updates annually (registration required) |
| **ESMO CPG** — pancreatic cancer 2023 ([DOI](https://doi.org/10.1016/j.annonc.2023.08.009)) | Surveillance cadences and the role of CA19-9 | Full text — 403 for bots; read manually at esmo.org | High | ESMO: eUpdates between versions, “living guidelines” for some |

**What applies.** For someone after cancer, kind 2 means **CTCAE grades (composed with the lab report) + surveillance cadences from ASCO/ESMO/NCCN by tumor type**. Which guideline applies is determined by the diagnosis the doctor names. If the required facts are absent from `patient_profile`, the system must not infer them from the available tests.

## 4. Kind 3 — stratified target

There is no public “catalog” here, nor can there be: a doctor sets a target for the medical history under a guideline (ESC risk categories for LDL; NCCN risk stratification by stage). The sources are the same as in §3, but they are applied through a profile fact. The project can store only **what the doctor said** and **which document it came from** — `stratum` + `source`.

## 5. Machine-readable homes and APIs (for storage and updates)

| Tool | Purpose | Access |
|---|---|---|
| **LOINC** | Analyte identity (code instead of a name string; GRID indexes by LOINC) | Free license, commercial and personal use, attribution |
| **UCUM** | Units (our `to_conventional` is a manual version of the same thing) | Open, ucum.org, `ucum-lhc` library |
| **FHIR Observation.referenceRange** | Standard form of “value + reference + reference type (`type`: normal/treatment/therapeutic…) + applies-to (sex/age) + text” — a ready-made schema for `norm_kind`/`stratum` | Open (hl7.org/fhir/R4) |
| **NCI EVS / CTCAE terminology** | CTCAE as a terminology with codes | Open |
| **PubMed E-utilities** (MCP already connected) | Search and metadata; full text only in PMC | Free, key for >3 rps |
| **Europe PMC REST API** | 33 million records including **NICE guidelines and preprints**, 6.5 million open full texts, `HAS_FT:y OPEN_ACCESS:y` filters, annotations; no key | Free |
| **GIN International Guidelines Library**, **ECRI Guidelines Trust** | Guideline registries with methodology assessments (replacement for NGC) | GIN — membership; ECRI — registration |
| **NICE** | Open guidelines with a review date in each document | Open |

## 6. What to bring into the project (proposal, not a decision)

1. **The reference comes from the lab report**, and only from it; AACB/Mayo are a second witness for absurd values. Almost in place already: `check_norm_vs_lab_reference`.
2. **Lab decision thresholds — CTCAE v6 (2025-07-22)**, recorded as *ULN/LLN multipliers* or absolute values with `source='CTCAE_6.0'`, `source_date='2025-07-22'`, `next_review` = v7 release (check annually). All 64 “receipt” rows in `safety_net` are replaced by this document — not rewritten, but **derived**.
3. **Tumor markers — RCV from the EFLM API + CV_A** (desirable APS 0.5·CV_I until the laboratory provides its own), not 50% and not the upper reference limit. The upper reference limit on the lab report for a tumor marker is kind 1; for surveillance after cancer, it is secondary to *change over time* (NACB 2008, ASCO 2013).
4. **Surveillance cadences (CEA/CA19-9 every 3–6 months)** belong in `lab_monitoring_schedule` with a `source`; currently all 19 rows have `code_seed`.
5. `next_review` by kind: ADA — 1 year; CTCAE — version check after 1 year; ESMO/ESC — 2 years with an eUpdate check; lab report — automatically with every new document.

## 7. What has not been checked / where I am uncertain

- The **CTCAE v6 vs v5** changes have not been read; the table in §3 is from v5.0 (except anemia, checked against v6).
- **ESMO** full texts could not be opened (403); pancreatic surveillance cadences were not extracted.
- **GRID**: whether an export/API exists has not been established.
- All guideline “cadences” are based on actual dates of past versions; most have no formal review commitments.
- The local laboratory's **CV_A** and reference verification document have not been requested. This is a step for the owner.

## Sources

- AACB, Adult and paediatric common reference intervals for a first panel — <https://www.aacb.asn.au/common/Uploaded%20files/aacb/guidelines%20and%20position%20statements/position%20statements/supporting%20documentation/20200211%20GPS%20Adult%20and%20paediatric%20common%20reference%20intervals%20for%20a%20first%20panel%20.pdf>; continuation — <https://www.ncbi.nlm.nih.gov/pmc/articles/PMC5111244/>
- IFCC TF-GRID — <https://cms.ifcc.org/media/479860/1-tf-grid-2022-10-30-v4.pdf>; GRID — <https://grid.ifcc.org/>; C-RIDL — <https://ifcc.org/ifcc-scientific-division/sd-committees/c-ridl/>
- NORIP — <https://pubmed.ncbi.nlm.nih.gov/15223694/>
- EFLM BV Database — <https://biologicalvariation.eu/>; release description — <https://acclmu.org.ua/en/new-release-of-the-eflm-biological-variation-database-website/>; Bartlett 2025 — <https://journals.sagepub.com/doi/10.1177/00045632241311453>
- CTCAE v5.0 — <https://dctd.cancer.gov/research/ctep-trials/for-sites/adverse-events/ctcae-v5-8x11.pdf>; v6.0 — <https://dctd.cancer.gov/research/ctep-trials/for-sites/adverse-events/ctcae-v6.pdf>; EVS — <https://nciterms.nci.nih.gov/ncitbrowser/pages/vocabulary.jsf?dictionary=CTCAE_v5&version=5.0>
- KDIGO 2024 — <https://kdigo.org/wp-content/uploads/2024/03/KDIGO-2024-CKD-Guideline.pdf>
- ESMO pancreatic 2023 — <https://www.annalsofoncology.org/article/S0923-7534(23)00824-4/fulltext>
- LOINC license — <https://loinc.org/kb/license>; FHIR Observation — <http://hl7.org/fhir/R4/observation.html>
- Europe PMC API — <https://europepmc.org/RestfulWebService>
- GIN — <https://g-i-n.net/international-guidelines-library>; ECRI Guidelines Trust — <https://home.ecri.org/pages/ecri-guidelines-trust>
- PubMed (through MCP): DOIs in the text.
