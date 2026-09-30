<!-- translation-of: docs/explanation/medgemma_lab_extraction_eval.md sha256:0ed18cb58fae -->
**English** · [Русский](medgemma_lab_extraction_eval.md)

# MedGemma for lab extraction — why we are staying with Haiku

> **Document type:** Explanation (Diataxis). A decision record, not instructions.
> Date: 2026-06-19. Related: `lab_extractor.py`, `hai_core.MODEL_DEFAULTS`,
> `model_health_check.py`.

---

## Question

Should we replace cloud-based Haiku with the local open model MedGemma for lab
extraction (OCR text → JSON)? The motivation for the hypothesis: privacy (raw medical
documents would stop going to the cloud) + the model's medical domain knowledge.

## Answer

No. Tested empirically and rejected. MedGemma trails Haiku in **value
accuracy** — the only thing critical for medical data — and medical tuning
provides no advantage.

## How we tested

The same OCR text was given to: Haiku-4.5 (production), MedGemma-4B-it Q8
(locally, Ollama), MedGemma-27B Q4, and qwen2.5:14b (the “medical tuning vs. any
local model” control). OCR used the same recipe as production (fitz → Tesseract
with support for the document languages). The prompt was the production
`EXTRACTION_PROMPT`. Ground truth: confirmed values from `lab_results` for a
multilingual laboratory-document corpus. This was a real extraction evaluation,
not a synthetic run. Separately, synthetic probes covered OCR-repair,
hallucination-bait, ref-only, non-Latin→English names, and calibration.
The harness was temporary and deleted after the run.

## What the data showed

Correct values delivered: **Haiku 37 > qwen-14B 28 > MedGemma-4B 18 ≈
MedGemma-27B 18**. Value accuracy on matches: Haiku ≈97%, MedGemma-4B ≈60%.

- **4B**: cascading row shifts were observed in the table — each number was
  plausible but assigned to the wrong field. An independently invented illustration:
  `Example_A` receives the value of `Example_B`; these are not corpus fields.
  More dangerous than an omission. 6× slower than Haiku.
- **27B**: value accuracy on clean text improves (88%) — so much of the gap
  is about model size. But `format=json` crashes Ollama (HTTP 500) on this
  GGUF → had to run raw → invalid JSON on 2 of 4 documents → the same 18 correct values.
  ×7–12 latency, OOM with 36 GB before reducing context.
- **Calibration** is dead in both MedGemma models: `confidence` is always "high," never
  "low" (on one document, 9 of 18 “high” values were wrong). This breaks the human
  gate `confidence=low → карточка на проверку`.
- Medical tuning did not help: general-purpose qwen-14B beat medical MedGemma. Extraction is
  a task of perception and preserving table structure, not medical reasoning.
- Savings from running locally ≈ cents/year (Haiku costs fractions of a cent per document).

## When to reconsider

If any of the following becomes available: working `format=json` on another GGUF build; a strict
privacy requirement with a willingness to lose accuracy; or a model whose calibration
is not broken. Until then, `MODEL_DEFAULTS["haiku_pinned"]` is the right choice for
this task.

## Related: model pinning and its EOL

The model is deliberately hardcoded (reproducibility of medical reasoning; see
BLUEPRINT principle 10 / §11 “medical artifacts are not updated automatically”).
The cost of pinning is a future EOL. To prevent it from coming as a surprise, the sensor
`model_health_check.py` was added (pings `MODEL_DEFAULTS`, raises a loud alert on withdrawal).
