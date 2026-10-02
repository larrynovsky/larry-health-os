<!-- translation-of: docs/explanation/model_choice.md sha256:0e4f83650147 -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](model_choice.md)

# How the System Selects a Model and Switches It Automatically

## Problem

A model provider will eventually shut down an old model. If the system is hard-coded to
a single name, on the day of shutdown its analyses, chat, and consilium stop working — and
a human discovers this, not the system. Owner's decision as of 01.10.2026: the system switches
**on its own**, but **only to a verified** model, and **reports** this.

## Roles and Chains

The system requests a role, not a "model": `opus` (photo-based analyses, first pass; consilium),
`sonnet` (second pass of analyses, chat), `haiku` (check-in), `haiku_pinned` (treatment and text-based
analyses). Each role has a chain of models in its settings (`model.<role>`): the first is the active
one, the rest are fallbacks. The `opus` and `sonnet` chains do not overlap: photo-based analyses are
read by two different models, and if both converge on the same one, the recognizer will fail rather
than produce a comparison of a model against itself.

## Who Notices a Shutdown

Every morning a health check (`model_health_check --daily`) queries the provider's model list and
pings every model in the chains. The result is a snapshot of "what is available" with a date. Model
selection reads this snapshot: the first available model in the chain. If the active model has
disappeared, the next one is used, and the owner receives a card saying "role model has changed"; if
it comes back — a card saying "it has returned". A snapshot older than three days is not trusted:
in that case the first model is used, as before.

## How Fallbacks Enter the Chain — Admission

A fallback model enters only through admission. Once a week the admission module fetches models
from the provider that are newer than the active one, runs each through the tasks of its role, and
compares against an exact reference: numbers from analysis forms, treatment cycles, text tasks (do
not assert absence without data, do not build a trend from a single point). The pass rule is written
before the runs: not a single error in any single repeat. A model that passes is appended to the
**end** of the chain — today the system behaves exactly as it did yesterday; only the owner changes
the first model.

Example from the 01.10 measurement: on a synthetic "hard" form, one OpenAI model read MCH as 29.3
instead of 29.9 three times out of three — plausible and wrong. Such a model does not pass admission.

Cost: admission spends no more than the owner's monthly budget (10 $). The count is kept **before**
the call using a pessimistic rate and response limit: a reasoning model may consume the entire limit,
and a single call would be enough to exceed the budget if counted after the fact.

The owner's own forms participate in admission only at Anthropic — they are sent there anyway during
normal operation. For other providers, admission shows only synthetic data.

A model that passes admission becomes a fallback not only for the owner. The release table that ships
with the code carries the model's name, date, and "passed", but none of the values from the forms:
admission errors contain analysis numbers, and those do not go into the public repository. Example:
on 02.10 claude-opus-5 passed admission at the owner's installation — at a partner's installation the
opus role chain became "claude-opus-4-7, then claude-opus-5", even though the partner had not verified
anything themselves.

## Other Providers

An installation with an OpenAI or Gemini key ([instructions](../how-to/llm_provider.md)) communicates
with the model through a translator: the system still writes the request in Anthropic format, and the
translator converts it on the way out into a request for the provider's native library and converts
the response back. Why a single translator rather than a proxy like LiteLLM: the translation seams
(tool call numbering, response truncation at the limit, Gemini's "thought signature") are held by our
tests on recorded real API responses; a seam that fails silently is worse than an outright failure.
DeepSeek is the fourth provider: its profile uses an Anthropic-compatible endpoint and the same
SDK without a translator; the same instructions cover its key and installation limits. This endpoint
has a catch: for a foreign name it may not refuse but silently answer with its own model (measured
02.10: a request to "claude-opus-5" was served by deepseek-v4-pro; a wholly unknown name was rejected). So the system treats an answer
from a different model as the model being absent — otherwise admission would record a verdict for
someone else's model, and the sensor would not notice the model leaving.

With a third-party provider, only a role whose model has passed admission on synthetic data is
available — [table](../reference/llm_providers.md). Other functions fail loudly.

## Limits

- A partner installation and a GitHub installation have no admission of their own: they take fallback
  models from the release table, which is populated by the owner's admission when a development thread
  is closed. Between the owner's admission and the next release they operate with the old set of
  fallbacks; a sensor sees the lag but does not close it.
- Admission judges a model on a corpus, not on every future task: a role without a complete set of
  checks receives no successor, but a model that has passed is only verified against what is in the
  corpus.
- Admission does not know the pricing of third-party models: accounting is done at a pessimistic rate,
  so actual spending is lower than the recorded amount, and the number of checks per month is less than
  the budget would otherwise allow.
- With a DeepSeek key, numbers from lab forms do not enter the lab canon: the document recognizer
  refuses, and the document waits and is processed automatically once reading is admitted. The bot
  will describe a photo in chat; numbers from such a conversation can only reach memory marked
  "unverified", not the canon. Per DeepSeek's own model list, only one of its models accepts images
  (deepseek-flash; deepseek-v4-pro is text-only), while recognition reads each page with two different
  models and compares them: a shared error of two reads by one model would pass the comparison.
  On a lab-form photo deepseek-v4-pro did not refuse but returned numbers that are not on the form
  (admission of 02.10: WBC 5.2 instead of 6.82). Owner's decision of 02.10: a refusal in words is
  better than a weakened comparison. Boundary: if DeepSeek gets a second model that reads images and
  passes admission, photos will work without a code change.

## What the System Does Not Do

- Does not change the first model in the chain on its own.
- Does not admit a model on a partial set of role tasks: a role without a complete set of checks
  receives no successor.
- Does not send real documents to a new provider for verification.
- Does not put one model on both photo-reading passes, even if the provider has no second one.
