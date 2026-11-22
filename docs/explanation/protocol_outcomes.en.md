<!-- translation-of: docs/explanation/protocol_outcomes.md sha256:6cc2665ce26a -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](protocol_outcomes.md)

# What happened to the protocol — was it followed, and did its prediction come true: explanation

## What changed

- Added the **prediction_scale_by_spread** invariant with status *holds*: the protocol's prediction is built taking into account the natural spread of the metric for a specific person, and is rejected at the creation stage if it falls outside their real limits. This is a constraint on what can enter the system as a prediction at all — not an expansion of guarantees, but an explicit boundary of what is permissible.

**Updated:** 2026-11-11


## Why it exists

A protocol is not just a list of actions. It is born from a hypothesis and carries a promise: "if you do it this way, the metric will change roughly like this, over such-and-such a period of time." Without a way to verify that promise, the hypothesis hangs in the air — the system accumulates recommendations but does not learn from whether they come true.

This component exists precisely so that every protocol ultimately receives two answers: *did the owner do what was intended* — and *did the metric shift the way it was predicted*. Both answers are then fed back into the evaluation of the hypothesis from which the protocol grew. This is how the system closes the loop: it does not merely record actions, but checks whether what generated it deserves to be trusted.

## What it does, in plain language

**On adherence.** When the owner did something according to the protocol — or did not — the owner's assistant describes this in their own words and passes it to the system. This is not an instrument reading or an automatic counter: it is a retelling. The system accepts such a retelling only from the owner's assistant, marks it with that source, and stores it exactly that way — as a human report, not a measurement. Everyone who reads this data afterward sees that mark.

Numbers are not written here bypassing the assistant; an unknown protocol will not receive an adherence record either.

**On the prediction.** When a protocol is created, it records three things: which metric should change, by approximately how much (and within what limits), and over what timeframe. Nothing more is required from the protocol — it does not explain the mechanism and does not give guarantees.

Evaluating whether the prediction came true is the job of code, not a model. The code looks only at values that appeared *after* the protocol was created — nothing from before is taken into account. This is essential: otherwise the system could "confirm" predictions retroactively, drawing on what was already known.

The code compares: what happened to the metric against what would have happened if nothing had changed. If no measurements were taken during the protocol's active period — that too is a result, it is called "missed" and is counted in the overall picture. If the protocol was discontinued early, its evaluation is not included in calibration at all.

The prediction is constructed from the outset so as not to promise something plainly impossible. The system knows how much the metric typically "drifts" for a specific person and does not allow the prediction to venture far beyond those natural limits. If a prediction would be absurd relative to the real spread, it is rejected at the creation stage.

**What goes back up.** When the council evaluates a hypothesis, it receives a block with the protocol evaluation: what the code said about the prediction and what the assistant relayed about adherence. If this block could not be assembled — the system makes noise about this loudly, but does not drop everything else.

There is also continuous monitoring: if open predictions exist and the evaluator has not run for too long — the system notices this and signals it.

## What is honest to say about its limits

There is one place that has not yet been tested in real operation.

The adherence retelling must be provided by the owner's assistant in a specific format — so that the system can recognize it and store it correctly. A trial run showed that the assistant can hold the required format for other types of responses: twelve out of twelve attempts passed without deviations. But a real instance of an adherence-to-protocol string in live conditions has not yet occurred. This does not mean everything will break — but it does mean this part is not yet verified in practice. Calling it working would be dishonest.

Everything else — the data intake rules, the score calculation, the protection against retrodiction, the evaluator-idle signaling — holds according to checks at the time this was written.

## Where this lives in the system

The core logic lives in `protocol_outcomes.py` — that is where the prediction score calculation, the adherence retelling intake, and the block assembly for the council all reside.

The intentions and rules that the entire component follows — what counts as permissible input, how the evaluation is structured, what should make noise on failure — are described in `subsystem_intent.yaml`. This is a declaration of intent, not code: it can be read to understand *why* the system is built the way it is.
