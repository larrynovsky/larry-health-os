<!-- translation-of: docs/explanation/adr_heldout_train_confirm_split.md sha256:8b493dcd8a01 -->
**English** · [Русский](adr_heldout_train_confirm_split.md)

<!-- ADR: held-out = train/confirm split по frozen_at (семантика A). Diátaxis: explanation.
     Нить heldout, решение владельца «согласен с А» 2026-08-19. Страница вне реестра
     (unguarded) — свежесть по mtime; нормативные числа живут в signal_family.yaml. -->

# ADR: the gate's held-out data — train/confirm split by frozen_at, not post-hoc

## Decision

Discovery in the validation gate (daily path, stratified family A, lag family
q_lag) judges ONLY data with `date <= frozen_at` (train). The post-freeze tail is the confirm window:
pairs that passed all gates on train are confirmed separately on a tail that
discovery physically never saw. The split lives at ONE point — `gate_correlations`
(the boundary of truth); callers pass the full series, and the gate splits it itself.

## Why A, not post-hoc (B)

SPEC §4: "train on the early post-registration window, confirm on the later one" — B (discovery
on the full series, confirmation on top) leaves confirm data involved in selection:
independence is partial, and "survived held-out" means less than it sounds. Semantics A
removes confirm data from selection. Its cost is that discovery does not see the recent tail;
as that tail grows, the family may need to be redeclared (version↑), following the
"registration → clock → confirmation" lifecycle chosen by the owner on 08-08.

## Confirm verdicts (contract)

- `passed` — the sign of r on confirm matches the train sign AND permutation p ≤ confirm_alpha;
- `failed` — a SIGNIFICANT opposite sign (p ≤ alpha, sign opposite to train) — strong evidence;
- `insufficient` — did not cross the threshold: the window is still accumulating power (SPEC: "adaptively — insufficient
  until it crosses"; the spirit of hysteresis: do not bury it after one quiet window);
- `nothing_to_judge` — D is empty (normal when belief is empty, the lesson of exit 3);
- `insufficient_window` — the tail is shorter than the validity floor.

Confirm is a MEASUREMENT, not a block (Gate 3 precedent, 07-31): the verdict travels into gate_meta and
belief; blocking publication is a separate owner decision based on live material.

## Numbers and their provenance (simulation Э1a, seed 20260814, frozen φ from data_manifest)

The window floor of 42 days (6 weeks) is the VALIDITY boundary for the permutation null (false positives ≈ α from 42 days;
at ≤28 days the null is anticonservative by ~2×). It matched the provisional floor_weeks=6 — now this is
a measurement rather than an assigned value. The power boundary (≥80%) is an ENTIRE boundary, not one point: r=0.5 at φ=0.2 →
42 days; at φ=0.5 → 56 days; at φ=0.8 it is not reached through 84 days; r≤0.3 is not reached through
84 days anywhere. Therefore: `insufficient` is an expected window state for months, not a failure.

## Two window axes — legitimately different

Profile epochs (Gate 2 / family A) are the THERAPEUTIC axis: frozen changepoints from
data_manifest, representing a change in lifestyle regimen. Held-out is the METHODOLOGICAL axis: the signal family's frozen_at,
representing what selection saw. This is not split-brain, but two different questions about
time; they need not coincide, and no effort is made to keep them aligned.

## Honest boundaries

- r in belief: the caller's `spearman_r` is computed on the full series (an analyst artifact);
  the gate's judgment carries `r_train` (its own r, on train). Consumers still
  print spearman_r — a named follow-up, not a silent discrepancy.
- Confirm does not judge magnitude separately from significance: |r| enters through p. The operationalization
  of "direction and magnitude" is OURS (SPEC has no formula), as with the sign fraction in Gate 2.
- The lab path is outside the split while descoped (a mode, not a record).
