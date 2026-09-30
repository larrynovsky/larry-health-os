<!-- translation-of: docs/explanation/generated_food_rules.md sha256:7e315425808f -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](generated_food_rules.md)

# Diet Rules Tier 2: LLM Proposes, Code Decides: How the System Decides What to Trust in Food

## What Changed

- **`dead_rule_not_stored`** — the claim wording has been refined: active status alone
  does not prove that a rule's condition fires on the current profile. Applicability
  must be checked with the same predicate used by the consumer.

**Updated:** 2026-09-01


## Why It Exists

Nutrition is one of the few levers a person pulls every day. But a good dietary rule for a specific person with a specific health condition is a fragile thing. Writing it once and forgetting it won't work: the condition changes, the profile changes, and a rule that once made sense may quietly stop being relevant to you — or, worse, begin running idle, creating the appearance of care where there is none.

This subsystem exists to break two familiar temptations: trusting a recommendation simply because a smart algorithm produced it, and trusting it simply because a human once reviewed it. Instead, roles are divided here strictly and deliberately: the language model proposes, deterministic code judges, and the person sees the result before anything begins to be applied.

## What It Does, in Plain Terms

Once a month the system runs something like an internal review. The language model looks at your medical condition — the list of problems and the medical profile from the single store the entire system trusts — and proposes a set of rules of the form "if such-and-such condition, then such-and-such nutritional frame." Each proposal arrives together with an explanation of where the logic came from and with the condition under which the rule is supposed to fire.

Then the critic enters — deterministic code that has no mood and no desire to please. It checks each proposal against several questions: is it correctly structured, does it have a legible origin, does it pass the basic safety threshold, and — crucially — does its condition fire on your real data right now. That last point is essential: a rule that sounds fine but has no bearing on you today is rejected as dead and does not enter the system at all. Active status without an applicability check can create a false impression that a rule is useful.

Rules that pass review go into a shadow state: they are stored but not yet applied. You see a monthly summary — what was proposed, what was rejected, and why — before anything begins to affect your dietary brief. Only the next cycle moves what passed into active status.

When a new generation of rules is ready to be promoted, it does not get added to the old one — it replaces it. Exactly those rules that passed fresh review become active; everything absent from the new set goes into the archive. If the new generation passed review with zero rules at all, the old set remains in force: yesterday's map is better than none.

Rejected rules do not disappear. They are stored indefinitely with their status and reasons for rejection, are visible in the summary, but never reach the brief. If the same condition fails review again and again, that is a signal: the relevant entry may need to be added to the knowledge base manually — the system will not do this on its own.

Lab data and genomics do not live in this layer. That is a deliberate decision: their home is the clinical knowledge base, and the layers were not mixed here.

## What Is Honest to Say About Its Limits

All documented invariants of this subsystem are currently holding. But "holding" and "verified under all conditions" are different claims, and it is important not to conflate the two here.

No verified bounds for these invariants have been officially declared. That does not mean there are none, but that they have not been described — which means it is unknown under what edge cases the system's behavior remains predictable.

An empty medical text is a separate case: if a tenant has no health condition data, the critic cannot judge whether rules fire. It will say so in the log but will not apply the rule automatically — this is honest behavior, but it means the system is effectively non-functional in that situation.

Independent re-review of the safety floor inside the subsystem has been deliberately omitted — this is a recorded architectural decision, not an oversight. But precisely because it is a deliberate decision, it is worth understanding: two layers check the same thing independently of each other, and if they ever diverge on data, the system's behavior will require attention.

Finally, the subsystem works with what is in the knowledge base. If the needed clinical condition is not described there, no generation will fix that — only manual work on the base will.

## Where This Lives in the System

The logic for generating and sending proposals lives in `food_rule_generator.py`. The subsystem's intentions, its limits, and the owner's documented decisions are described in `subsystem_intent.yaml` — this is the primary source of truth about what is supposed to happen here and why it happens this way.
