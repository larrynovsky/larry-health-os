<!-- translation-of: docs/explanation/deterministic_norm_source.md sha256:2d0c57b92fbb -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](deterministic_norm_source.md)

# Deterministic Norm Layer: Single Source, Loud Failure

## What Changed

- **The claim about threshold storage has been clarified.** The claim about where and how clinical thresholds (`safety_net_thresholds_in_db`) are stored has been corrected; the previous wording is acknowledged as inaccurate. The direction of the change could not be determined automatically — it is described neutrally.

- **The claim about check permanence has been clarified.** The claim that absolute thresholds are checked on every run regardless of signals (`fires_regardless_of_signals`) has been corrected; the previous wording is acknowledged as inaccurate. The direction of the change could not be determined automatically — it is described neutrally.

**Updated:** 2026-10-18


## Why It Exists

Imagine you have several smoke detectors at home, and each one decides for itself at what temperature to call a fire a fire. One triggers at fifty degrees, another at seventy, and a third just guesses "from context." That is not safety — that is the illusion of safety.

The same danger arises in a medical system when an "alert threshold" for a blood marker or blood pressure is scattered across code in dozens of places, or — worse — is computed on the fly by a smart model. A model can be convincing and still be wrong. Values scattered across many places diverge from one another without anyone noticing.

The deterministic norm layer exists precisely to prevent this. It is not a stand-in for the smart AI layer, nor a competitor to it. It is the load-bearing wall beneath it — dumb, predictable, and fundamentally impossible to argue out of its position. No matter how eloquently a model reasons about your specific case, it does not assign and does not change the alert threshold.

---

## What It Does, in Plain Terms

**One table — one source of truth.**
All clinical thresholds — "above this is dangerous," "below this is dangerous" — live in one table. Not in code, not in the model's internals, but in one place that can be opened, inspected, and updated. Every part of the system that checks your readings — smart agents, the safety net, the morning report — reads thresholds from there and only from there.

Some thresholds came from clinical documents; one threshold is personal, set by the owner. The source of each is recorded honestly.

**No silent guess — a loud failure.**
If a needed threshold is not in the table, the system does not substitute "something reasonable" and does not stay silent. It fails loudly — specifically so that someone notices and fixes it. The reasoning behind this is simple: a confidently wrong threshold is more dangerous than an absence. An absence is a signal that says "go and figure this out." A silent guess is a false sense that everything is under control.

A related but distinct case: sometimes a threshold for a metric exists, but a reference point from your lab report is needed for comparison — and that reference point is absent. This is not the same as a missing threshold: the threshold exists, there is simply nothing to judge against yet. Here too the system does not guess — it says so out loud, as a separate signal.

**The safety net checks everything — not only when asked.**
Absolute limits for metrics are checked regularly, on a schedule, regardless of whether any treatment protocol is currently active. If a check has not run for a long time, the system notices this and raises an alert on that fact alone.

There is also a fallback mechanism for cases where the primary table is unavailable — for example, if the database is not responding. The fallback is a snapshot of the same clinical documents that formed the basis for the table. If the system switches to the fallback, it announces this loudly rather than pretending everything is fine. The fallback and the primary table must match — this is verified separately.

**Alert delivery is your choice.**
When the system finds a clinical threshold violation, it records it. Whether a message is sent to you depends on your configuration. If sending is turned off, the system still names the event out loud — it does not suppress it. "Already alerted today" is remembered in the database, not in memory: if the bot restarts, the same alert does not arrive twice. A personal threshold — for example, "one bad night" — does not generate an alert message; its picture is carried by the morning report.

---

## What to Honestly Say About Its limits

All claimed invariants of this layer hold. But "holds" and "verified under any conditions" are different statements, and it is important not to conflate them here.

**History and column-level metrics.** Trend thresholds operate on metrics that have accumulated sufficient observation history. If there is too little data — the threshold stays silent. Some metrics, due to the way they are stored, do not enter this check layer at all — they are judged by other parts of the system.

**The connection between layers.** The safety net is a load-bearing wall, but not the only safeguard. Some metrics reach evaluation only through smart agents or the morning report. This is not a gap in the architecture but a deliberate separation of responsibilities — however, it is important to understand: "the deterministic layer checked it" does not mean "everything that could possibly be checked has been checked."

**The fallback is not a permanent support.** The threshold backup exists for emergency situations, not for normal operation. Switching to it is accompanied by a loud signal precisely because this is an abnormal situation that requires attention.

**Consistency of the table with reality** is a separate responsibility. The layer guarantees that all parts of the system look at the same place. It does not guarantee that the contents of that place are current for your specific clinical situation. That remains a human responsibility.

---

## Where This Lives in the System

The safety net logic lives in `rules_db.py` — it implements reading the threshold table, loud failure when values are absent, and fallback switching with an alert. The intent and boundaries of the subsystem are described in `subsystem_intent.yaml` — this is a document about *why* things are arranged this way, not *how* to run it. If something in the safety net's behavior seems unexpected, `subsystem_intent.yaml` is the first place to look: it records the intentions that the code is meant to embody.
