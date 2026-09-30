<!-- translation-of: docs/explanation/brief_repeat_boundary.md sha256:fa8ea2343a9e -->
**English** · [Русский](brief_repeat_boundary.md)

# Why the repeat suppressor sits after the LLM, not before

The morning brief is assembled like this: deterministic code puts pieces of context into a prompt,
the model writes text from them, and that text goes to a person. This page answers
the question of why repetition protection at the input is not enough and what changes when
it moves to the output.

## The input gate answers the builder's question

Card deduplication is a permission mechanism. It decides which findings **may**
be mentioned today and records that decision as `status='shown'`.

The word "shown" tells a well-meaning lie here: it means "allowed to be mentioned,"
not "shown to the person." Between permission and text sits a model that can drop a finding —
and does, which is why the `_ensure_shown_extras` safety net exists. It can also say something
it was not allowed to say.

Hence the general form: **an input gate proves that the system did not intend
to repeat itself. It cannot prove that it did not repeat itself.**

## Two ways around it, both observed

**First — a channel without cards.** The gate lives at the card layer, while the prompt is assembled from
many sources. A source without a card bypasses it: it has no key, no
display history, nothing to suppress. This is how chat notes opened the brief three days
in a row by retelling news the owner had supplied. The gate was working
flawlessly — within its own layer.

The remedy is structural: a channel registry computed by traversing the code, where each source
must name its suppressor. It does not prevent creating a channel without a suppressor — it prevents
creating one unnoticed.

**Second — the model says more than it was allowed to.** The brief has long had
a gene scrubber and a post-render validator for this class: they read the generated text and
remove sentences containing genes the gate did not approve. Tellingly, this
mechanism existed only for genes — output protection had already been recognized as
necessary, but for exactly one class of content.

## Why comparing with yesterday's brief does not work

The natural idea for an output sensor is to compare today's text with yesterday's.
It fails for two reasons, and the second matters more.

The practical reason: the brief and the domain share a vocabulary. Comparing all sentences does not
separate repetition from an ordinary morning — the measured ranges overlap.

The fundamental reason: both texts passed through the same transformation — the same model, the same
prompt, the same style. These are two reads of one replica, not two sources. Disagreement
between them is indistinguishable from agreement **by construction**, not through inattention
(§17 of the rule set; formally, quorum intersection with `NR = 1`).

The second source must bypass the transformation that could have distorted the first.
For "the system retells what the person said themselves," that source turns out to be
**the person's own utterance**: it did not pass through the prompt pipeline, and its text
was not rewritten by the model. It provides separation.

## What this means for new protections

- An instruction in a prompt is not a mechanism. It is useful and cheap, but violations are silent,
  so a guard that reads the result must sit alongside it.
- An output guard chooses not "what looks similar," but "which transformation to look past."
  Name the path first, then compute the metric.
- Such a guard's threshold is calibrated on the corpus of delivered content, not assigned: it has
  no theoretical value, only separation in live data. It is therefore
  data (tenant configuration), not a literal in code.

## Related

- How to add a new channel — [`docs/how-to/add_brief_channel.md`](../how-to/add_brief_channel.md)
- Brief v3 intent (bands, slots, FSM) — `subsystem_intent.yaml::morning_brief`
- Rules: §17 (second source), §14 (liveness ≠ correctness), §20 (passing is caused by the test)
