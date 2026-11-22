<!-- translation-of: docs/explanation/hypothesis_verdict.md sha256:7af17ebd4d88 -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](hypothesis_verdict.md)

# Forecast of a hypothesis in numbers and the verdict the code returns: how the system decides whether a guess came true

## What changed

- **The block's intent has been restated.** The subsystem's intent has changed — not just prose, but a commitment to what the block does and what it does not do.

- **The invariant `forecast_at_birth_by_code` added (status: holds).** A boundary is recorded: the numerical forecast at hypothesis birth was tested in a single run on a copy of the database; a live monthly cycle had not yet occurred. This is proven within the named limits — not in a live stream.

- **The invariant `two_reads_must_agree` added (status: holds).** The double-read when converting a forecast to numbers is recorded as a rule: if the two reads disagree, the result is not used. The rule holds, but has not been verified on a live database in its new form.

- **The wording of `live_structuring_unverified_open` has been refined; the direction is not determined by machine.** The old version named a specific failure from 11 November. The new version clarifies that after edits to the prompt and the core, the translator was moved to a stronger model — but a check against live data has not been conducted and has not been recorded. The wording has been adjusted; this is a refinement of a boundary, not an assessment of direction.

- **The invariant `live_birth_forecast_unverified_open` added (status: open).** Explicitly recorded: the ability to write a forecast at hypothesis birth has not undergone live verification in a real cycle. An open question, not an established practice.

**Updated:** 2026-11-12


## Why it exists

The system builds hypotheses — guesses about what is happening with your health. But a guess without verification is just a word. For it to teach anything, you need to state in advance exactly what should change and by how much, and then honestly compare that against what actually happened.

Previously there was a quiet problem: when the council evaluated hypotheses, nearly all of them received the same answer — "partially", with the same degree of confidence. This means the evaluation distinguished almost nothing. Everything looked roughly equally uncertain — regardless of how strongly the metrics had actually changed or not changed.

This block was created to fix exactly that. It translates a forecast — what the council wrote about a hypothesis — into numbers, and then checks them against what actually happened using a clear code rule. The council does not vote, the model does not weigh intonations — the code looks at the figures and returns a verdict.

## What it does, in plain terms

Imagine the council of models wrote: "we expect this metric to decrease." That is a forecast. But "will decrease" is not a number. Code cannot argue with adjectives. So the first step is translation.

**Translating a forecast into numbers.** When a hypothesis is born at the monthly council, the council immediately records the forecast in numbers — in a dedicated field. This is the primary path. If the hypothesis is older or was revised but the forecast remained as text, the block reads that text and attempts to extract its numerical content. In doing so it sees only the forecast text itself, the names of the metrics, and their level at the time the forecast was made — but sees none of the data that appeared afterwards. This matters: the translator does not know how things turned out and cannot fit the translation to the answer.

The text is read twice, independently. If both reads produce the same result, the translation is accepted. If they disagree, the result is not used and the disagreement is recorded. An honest blank is better than an unreliable number.

The forecast date is the date of the council's most recent revision, not the date the hypothesis was born. If the council replaced the forecast with a new version, the old one becomes invalid and does not enter the evaluation.

**The verdict is a code rule.** Once the numbers exist, the code compares them against what happened. The rule is simple and does not change from case to case: if the metric moved in the opposite direction — the hypothesis is refuted. If it landed in the expected interval and that is better than if nothing had changed — confirmed. If neither — undecided. If there is no numerical forecast at all — the verdict says exactly that: there was no numerical forecast.

An important point: "nothing changed" does not by itself refute a hypothesis. Hypothesis forecasts are conditional; they say "if the situation develops in such-and-such a way, we expect this." Therefore the absence of change is simply one of the outcomes, not an automatic "did not come true."

The code verdict is a piece of evidence, not a sentence. The council sees it when evaluating a hypothesis, together with every forecast and how it ended. If the block fails to fire for any reason, this does not pass silently — the system raises noise and does not lose the data packet.

## What to say honestly about its limits

It is important not to gloss over things here.

**Translating text to numbers has not yet been verified on a live database in its new form.** Before 12 November, translation was handled by a less powerful model, and it behaved unstably: it would read the same text and return different results from run to run. In the most recent verification round there were two errors out of twenty-four. After that, the translator was moved to a stronger model and double-reading was added. But a check of this new version against real live data has not yet been conducted and has not been recorded. This is an open question.

**The forecast at hypothesis birth has been tested only once, not under production conditions.** The ability to record a numerical forecast at the moment of hypothesis birth was tested in a single full council run on a copy of the database. A live monthly pass with this field has not yet occurred. The mechanism looks functional, but there has been no real verification in a live cycle.

Both of these things are not minor details. They mean that the block is designed sensibly and its logic holds, but how it will behave on a live data stream in a full cycle remains to be seen.

## Where this lives in the system

The main logic lives in `hypothesis_verdict.py`. The block's intent and boundaries are described in `subsystem_intent.yaml`. The court over hypothesis forecasts uses the same mechanism as the court over protocols — `prediction_core` — so that the rules are identical in both places and do not drift apart over time.
