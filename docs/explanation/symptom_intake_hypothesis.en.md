<!-- translation-of: docs/explanation/symptom_intake_hypothesis.md sha256:5bb251d4a8d4 -->
**English** · [Русский](symptom_intake_hypothesis.md)

# Symptom photo intake: building a hypothesis through a differential

> **Document type:** Explanation (Diátaxis) — why it works this way, not how to use it.
> Code: `symptom_intake.py`, `handlers/symptom.py`, `media_intake.py`, `visual_db.py`.
> Prompt: `specialists/symptom_intake_system.txt`. Flag: `system_config.symptom_intake_enabled`.

## What this is

A tenant sends the bot a photo WITH A CAPTION describing a bodily problem. The system conducts
a clarifying dialogue and prepares the person to see a human doctor: with a constructed
hypothesis (or an open list of possibilities) in `needs_specialist` format. It **does not make
a diagnosis** and **does not triage emergencies**.

## Why this is a SECOND hypothesis birth point (deliberately)

The main hypothesis engine is **data-born**: `monthly_consilium` creates hypotheses FROM data
(metrics, labs, correlations), and they converge through `eval` cycles. The “experiments” pipeline
(hypotheses “from someone's head”) was deliberately retired (BL-EXP-1) precisely because of its arbitrariness.

Symptom intake is interactive, question-driven formation. At first glance, it looks like that same
retired philosophy. The difference is fundamental: **a symptom with a photo is data FROM the patient**,
not an invented experiment. “From someone's head” has nothing to do with it. But this deliberately introduces a second
hypothesis birth point alongside the consilium's — recorded here so a future reader
does not see it as a relapse. The hypothesis framework is the same — `{observation, mechanism, prediction,
test}` — and after construction, symptom intake hands the hypothesis to the EXISTING lifecycle (`eval`,
doctor-in-loop, `consult_prep`), duplicating none of it.

## Where the load-bearing wall is

The same as in [doctor_in_loop](doctor_in_loop.md): medical matters go to a human doctor
(`resolution_type='needs_specialist'`), and the doctor's response comes back as input. Symptom intake
reuses the wall rather than building a new one. It also inherits the wall's open limitation: classifying
“a symptom or an everyday photo” is not validated. So intake requires **a caption describing a complaint**, and
ambiguity is treated as a symptom (the safe side: an extra dialogue costs less than
a missed symptom).

## Why safety is NOT about detecting danger

The original design included urgency triage based on red flags. This was discarded for two reasons:

1. **The “emergency” level is a phantom for this bot.** In an acute situation (pain, loss of vision),
   a person calls an ambulance rather than taking a photo and waiting for a response. Where the bot is useful —
   the quiet-but-serious class (a painless mole that would be ignored) — there is precisely no acute urgency.
2. **Red flags are the most stable part of medicine**, updating slowly; dynamic
   external ingestion of flags would add freshness where it contributes almost nothing, at the cost of
   the reliability of a live safeguard.

So safety rests on **two things, not a knowledge base**:

- **A ban on reassurance.** The default is always “worth showing a doctor”; a “normal” verdict
  is prohibited. False reassurance is the only catastrophic failure (it would cause delay).
- **Preventing the hypothesis set from collapsing.** Otherwise the model will anchor on the first possibility and
  keep confirming it instead of ruling out alternatives.

## Brake and motor

“Always see a doctor” as a fixed stance itself provokes confabulation (the neural network defends
its initial position). So:

- **The motor of divergence** is a forced differential in the prompt: at least N grounded
  competing possibilities; the next question is chosen for how much it SEPARATES the set
  (eliminates candidates), not how much it confirms the leader; a possibility is dropped when a fact contradicts it.
- **The brake** is `epistemic_skill` (injected verbatim, version `epi_sha12` stamped into
  the case): AP-9 “a term is a descriptor, not a diagnosis,” prohibition of false consensus, and a confidence
  ceiling based on N. This is also the diagnosis guard at the prompt level.

`epistemic_skill` by itself is **a brake, not a generator**: it preserves divergence when
present, but in a one-on-one dialogue divergence must be CREATED (forced differential) and grounded
(otherwise there are five hallucinated possibilities instead of one). Divergence + elimination, not divergence
alone — the same pattern as the consilium's `eval` (diversification + arbiter).

## Stop logic belongs in code, not in the model

The model proposes; **code decides** when to stop (otherwise the model decides for itself = anchoring):

- convergence (`converged` + complete framework + one possibility remaining) → hypothesis handoff;
- the set has not split for `STUCK_TURNS` consecutive turns → safeguard → escalate with an open
  differential; a dead end is a valid exit to a doctor, not a failure;
- `MAX_TURNS` — a hard ceiling against runtime loops (§9 class 2, system mechanics).

“Enough data” = the set has stopped splitting, not an arbitrary number of questions.

## Domain independence (§9)

The engine knows no medical specialty. Domains/regions/dialogue parameters are data in
`visual_domains`. “Teeth tomorrow” = seed rows, leave code alone. The guard is `diagnosis_guard`
(diagnosis sentinels in `symptom_intake.py` and the prompt) + §9 grep checks. Adding a domain: see
[how-to/add_visual_domain](../how-to/add_visual_domain.md).

## Storage and privacy

Photos are stored locally on Studio (`HEALTH_DATA_DIR/media/visual`, §8: not iCloud), indefinitely. On entry,
EXIF is stripped (a body photo carries no GPS), input is validated as an image (pixel-bomb guard),
the name comes from the original bytes' sha256 (not the Telegram name — path traversal), and deduplication uses the hash. Consent
to send a photo to the vision API is recorded in this document (there is no UI request): the photo goes to the model
for analysis, as with any of the bot's vision paths.

## Who looks at the results

- **Medical quality** — the doctor themselves, in aggregate: the sensor `check_visual_verdict_rate` counts the share
  of rejected hypotheses. The signal is LAGGING (after visits) — an honest limitation; there is no real-time check
  of medical quality.
- **Safe stance** — `check_symptom_prompt_discipline` (the prohibition on reassurance is in place,
  diagnosis_guard is clean) + the owner as a behavioral observer (“was I reassured / was my
  first guess confirmed?”).
- **Structure/liveness** — `check_visual_orphans`, `check_stale_visual_cases` (§14, loud).
- The owner-tenant is NOT a case reviewer: for their own cases they are the patient; for others' cases they are absent due to
  tenant isolation.
