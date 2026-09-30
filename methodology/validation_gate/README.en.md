<!-- translation-of: methodology/validation_gate/README.md sha256:ca3dfcb6c83c -->
**English** · [Русский](README.md)

# Validation gate — FDR methodology (provenance)

The load-bearing methodology of the correlation validation gate, moved from iCloud into
git on 2026-07-12 (СК-1: load-bearing material does not live in iCloud).

## What is here

- `fdr_harness.py` — a two-stage null harness for the statistics. Stage A: a continuous
  oracle with eff-df correction; Stage B: an exact circular shift via FFT (floor 1/T).
  Implements BH and BY (`by_reject` = BH at `q/H(m)`) and the discreteness wall `m·H_m/q`.
- `fdr_sweep.py` — the driver: an m/T/φ grid, prints BH/BY power+FDR (300 trials,
  illustrative; a release requires 50k — §10 of the verdict).
- The subsystem spec is `../signal_validation_lifecycle_SPEC.md` (the registry entry
  `subsystem_intent.validation_gate.spec` points to it).
- `validation_gate_FDR_verdict.md` — a closed internal verdict (final, R1–R6),
  excluded from the public export. References to its § in this README belong to the
  historical summary below; the document itself is not in the public copy. The harness
  sources and the [intent specification](../signal_validation_lifecycle_SPEC.md) are available,
  but they do not replace the evidence of FDR qualification.

## Historical status (closed verdict, moved 2026-07-12)

This summary does not establish the current release status. At the time of the verdict
the harness was a **mechanism proof, NOT release-grade**: Stage A used a single-φ eff-df
(verdict §2/§9: a pair-specific ACF/spectrum is needed), trials were modest, there was no partial-null /
mixed-sign / Monte Carlo CI / digital twin. «The harness exists» ≠ «FDR control is proven».
Running the scripts requires numpy/scipy; whether these packages are present is checked in the
environment of whoever runs them, not installed by this document.

The verdict prescribed an order (§1): validity of a single p → discreteness → BY →
the time layer (LOND) → classes → reproducible qualification; changing `_bh_threshold` in
`correlation_gate.py` was deferred until Ф0–Ф2 were ready. These are historical conditions,
not a confirmation that they are met in the current code.
