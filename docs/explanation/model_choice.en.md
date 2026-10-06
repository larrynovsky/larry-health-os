<!-- translation-of: docs/explanation/model_choice.md sha256:b07a2866ef7f -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](model_choice.md)

# How the System Chooses a Model and Switches It Automatically

## The Problem

Sooner or later a model provider discontinues an old model. If the system is hard-wired to a single
name, on the day it is discontinued the analyses, chat, and consilium stop working — and a human
finds out, not the system. The owner's decision on 01.10.2026: the system switches **on its own**,
but **only to a verified** model, and **reports** this.

## Roles and Chains

The system asks for a role, not a "model": `opus` (photo-based analyses, first pass; consilium),
`sonnet` (second pass of analyses, chat), `haiku` (check-in), `haiku_pinned` (treatment and
text-based analyses). Each role has a chain of models in the settings (`model.<role>`): the first
is the active one, the rest are fallbacks. The `opus` and `sonnet` chains do not overlap: photo
analyses are read by two different models, and if both converge on the same one, the recogniser will
refuse rather than produce a comparison of a model against itself.

## Who Notices a Discontinuation

Every morning a health check (`model_health_check --daily`) asks the provider for a list of models
and pings each model in the chains. The result is a snapshot of "what is available" with a date.
Model selection reads this snapshot: the first available model in the chain. If the active one has
disappeared the next one is used, and the owner receives a card saying "the role's model has
changed"; when it comes back — a card saying "it has returned". A snapshot older than three days is
not trusted: in that case the first model is used, as before.

## How Fallbacks Enter the Chain — Qualification

A fallback model only appears through qualification. Once a week the qualification module fetches
models newer than the active one from the provider, runs each through the tasks of its role, and
compares the output against an exact reference: numbers on lab-report forms, treatment cycles,
text tasks (do not assert absence without data, do not build a trend from a single point). The pass
rule is written before the runs: zero errors in any repeat. A model that passes is appended to the
**end** of the chain — today the system works the same as yesterday.

Models already in the chain are judged too (since 05.10). A verdict remembers the inputs it was
reached on: prompt, reference, the task's thinking mode. If any of them changes, the verdict is
stale and the model is judged again. A suite calls the model the way the work calls it: lab-photo
recognition thinks since 05.10, so qualification judges it with thinking. A failure triggers an
immediate repeat; two failures in a row remove the model from the role's chain (if the next one has passed
qualification — otherwise work would move to an unchecked model), and the night
repair queue gets a record with the previous chain for rollback. One failure does not remove it:
a model may err once on an ambiguous row, and removing it for that would swap the working model on
chance. If every model of a role fails, the chain stays as it was: with no model the function
does not work at all, which is worse than a model that erred on the reference.

The reference must name an analyte the way the canon does. Example from 05.10: the form prints RDW
twice — in % and in fL; the canon stores them as RDW and RDW_SD, but the qualification reference
was built from draft rows where the fL row was still called "RDW". Models that correctly read RDW
in % failed qualification. Now the reference and the model's answer are named by the same rule as
promotion into the canon.

Example from the 01.10 measurement: on a synthetic "hard" form, one of the OpenAI models read MCH
as 29.3 instead of 29.9 three times out of three — plausible and wrong. Such a model does not pass
qualification.

Cost: qualification spends no more than the owner's monthly budget (10 $). The count is kept
**before** the call using a pessimistic rate and response limit: a reasoning model may consume the
entire limit, and a single call would be enough to jump over the budget if counted after.

The owner's own forms participate in qualification only with Anthropic — they go there in normal
operation anyway. Other providers are shown only synthetic data during qualification.

A qualified model becomes a fallback not only for the owner. The release table that ships with the
code carries its name, date, and "passed", but no values from the forms: qualification errors
contain lab numbers, and those do not go into the public repository. Example: on 02.10
claude-opus-5 passed qualification for the owner — at a partner's installation the opus role chain
became "claude-opus-4-7, then claude-opus-5", even though the partner had not verified anything
themselves.

## Other Providers

An installation with an OpenAI or Gemini key ([recipe](../how-to/llm_provider.md)) communicates
with the model through a translator: the system still writes the request in Anthropic format, and
the translator on the way out converts it into a request for the provider's native library and
converts the response back. Why a single translator rather than a middleware solution like LiteLLM:
the translation seams (tool-call number, response truncation at the limit, Gemini's "thought
signature") are held by our tests against recorded real-API responses; a seam that breaks silently
is worse than a failure.
DeepSeek is the fourth provider: its profile uses an Anthropic-compatible endpoint and the same SDK
without a translator; the key and installation constraints are in the same recipe. This endpoint has
a pitfall: given an unfamiliar name it may not refuse but silently respond with its own model
(02.10 measurement: a request to "claude-opus-5" was served by deepseek-v4-pro; a completely
unknown name it did reject). Therefore the system treats a response from a different model as the
model being absent — otherwise qualification would record the verdict of a different model, and the
sensor would not notice it leaving.

With a third-party provider only the role whose model has passed qualification on synthetic data is
available — [table](../reference/llm_providers.md). All other functions fail loudly.

## The Name Does Not Reach the Model

The model judging health does not need a person's name, but does need their age. Therefore all
context assemblers — patient brief, family doctor header, chat, consilium package, visit
preparation — write the model the age and city but not the name, and convert the date of birth into
an age. Example: the chat system prompt previously began "You are the personal health copilot of
<name>", now it begins "You are a personal health copilot". Tone does not suffer: according to the
04.10 measurement the name appeared in one response out of a hundred.

04.10 measurement on the live database after deployment: the brief, doctor header, consilium
package, and lifestyle profile contain no name or date of birth; in the chat context the name
appeared twice — in calendar event titles, i.e. in content, not in what the system adds itself.

A test catches new paths by which the name reaches the model: it builds contexts using a fictitious
name and searches for it in outgoing text, and the ratchet turns red on a new name reader outside
screens intended for humans. The boundary: the model still sees the name inside a document (the
header of a lab-report form) and in the person's own words — the rule concerns what the system adds
itself, not the content of documents.

## Limits

- A partner and a GitHub installation have no qualification of their own: they take fallback models
  from the release table, which the owner's qualification populates when a development thread is
  closed. Between the owner's qualification and the next release they operate with the old set of
  fallbacks; the sensor sees the lag but does not close it.
- Qualification judges a model on a corpus, not on every future task: a role without a complete set
  of checks receives no successor, but a model that has passed is verified only on what is in the
  corpus.
- Qualification does not know the prices of third-party models: accounting is done at a pessimistic
  rate, so actual spending is lower than accounted, and the number of checks per month is less than
  the money would allow.
- With a DeepSeek key, numbers from lab-report forms do not enter the laboratory canon: the
  document recogniser refuses, the document waits and will process itself when reading is qualified.
  The bot will describe images in chat; numbers from such a conversation can only enter memory
  marked "not verified", not the canon. According to DeepSeek's own model list, only one of its
  models accepts images (deepseek-flash; deepseek-v4-pro is text-only), and recognition reads a
  page with two different models and cross-checks them: a shared error across two readings by the
  same model would pass the cross-check. deepseek-v4-pro did not refuse a photo of a form but
  returned numbers that are not on the form (02.10 qualification: WBC 5.2 instead of 6.82). The
  owner's decision on 02.10: an explicit refusal is better than a weakened cross-check. The
  boundary: if DeepSeek introduces a second image-reading model and it passes qualification, photo
  support will work without any code changes.

## What the System Does Not Do

- Does not reorder a chain: it only appends models that pass and removes models that fail twice;
  if all fail, it removes none.
- Does not qualify a model on a partial set of role tasks: a role without a complete set of checks
  receives no successor.
- Does not send real documents to a new provider for verification.
- Does not assign a single model to both reading passes for a photo, even if the provider has no
  second one.
