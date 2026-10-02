<!-- translation-of: docs/explanation/model_choice.md sha256:7a90015aa128 -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](model_choice.md)

# How the System Chooses a Model and Switches It Automatically

## The Problem

Sooner or later a model provider retires an old model. If the system is hard-coded to a single
name, the day it is retired the analyses, chat, and consilium stop working — and a human finds
out, not the system. The owner's decision as of 01.10.2026: the system switches **on its own**,
but **only to a verified** model, and **reports** the change.

## Roles and Chains

The system requests a role, not a "model": `opus` (photo-based analyses, first pass; consilium),
`sonnet` (second pass of analyses, chat), `haiku` (check-in), `haiku_pinned` (treatment and
text-based analyses). Each role has a chain of models in the settings (`model.<role>`): the first
is the active one, the rest are fallbacks. The `opus` and `sonnet` chains do not overlap: photo
analyses are read by two different models, and if both converge on the same one, the recogniser
will refuse rather than produce a comparison of a model against itself.

## Who Notices a Retirement

Every morning a health check (`model_health_check --daily`) queries the provider for the list of
models and pings every model in each chain. The result is a snapshot of "what is available" with a
date. Model selection reads this snapshot: the first available model in the chain. If the active
model has disappeared the next one is used, and the owner receives a card saying "role model has
changed"; if it comes back — a card saying "it is back". A snapshot older than three days is not
trusted: in that case the first model is used, as before.

## Where the Fallbacks in a Chain Come From — Qualification

A fallback model only appears through qualification. Once a week the qualification module takes
models newer than the active one from the provider, runs each through the tasks of its role, and
compares the results against an exact reference: numbers from analysis forms, treatment cycles,
text tasks (do not assert absence without data, do not build a trend from a single point). The
pass rule is written before the runs: not a single error in any single repeat. A model that passes
is appended to the **end** of the chain — today the system works exactly the same as yesterday;
only the owner changes the first model.

An example from the 01.10 measurement: on a synthetic "hard" form, one of the OpenAI models read
MCH as 29.3 instead of 29.9 three times out of three — plausible and wrong. Such a model does not
pass qualification.

Cost: qualification spends no more than the owner's monthly budget (10 $). The count is kept
**before** the call using the pessimistic rate and the response limit: a model that reasons may
consume the entire limit, and a single call would be enough to jump over the budget if counted
after.

The owner's own lab forms take part in qualification only with Anthropic — they go there in
production anyway. Other providers are shown only synthetic data during qualification.

## Other Providers

An installation with an OpenAI or Gemini key ([recipe](../how-to/llm_provider.md)) talks to the
model through a translator: the system still writes the request in Anthropic format, and the
translator converts it at output into a request in the provider's native library format and
converts the response back. Why a single translator rather than a middleware layer like LiteLLM:
the translation seams (tool call number, response truncation at the limit, Gemini's "thought
signature") are held by our tests on recorded real-API responses; a seam that breaks silently is
worse than an outright failure.

With a third-party provider, only the role whose model passed qualification on synthetic data is
active — [table](../reference/llm_providers.md). All other functions fail loudly.

## What the System Does Not Do

- Does not change the first model in the chain on its own.
- Does not qualify a model on a partial set of role tasks: a role does not get a successor without the full set of checks.
- Does not send real documents to a new provider for the purpose of verification.
