<!-- translation-of: docs/explanation/lab_recognizer.md sha256:80cd582294e7 -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](lab_recognizer.md)

# Lab Results Recognizer: staging, oracles, human gate — how the system reads paper lab results and why it does not do this alone

## What changed

- **The `date_order_by_code` invariant has been moved from `open` to `holds`.** This means "confirmed within the named limits", not "closed completely" — the limits are described below.

- **The boundaries of what is proven have been recorded (run 06.10.2026).** Re-recognition of 22 documents from staging (21 processed — 683 rows, 1 did not pass due to the page limit): out of 473 rows with a printed date, 352 matched the printed date, 121 received `ambiguous_order` status and were sent to a human, erroneous — 0. All 21 forms in the sample print the day first and contain an unambiguous clue; the sample is limited to exactly that.

- **Every fourth row with a date goes to a human.** 121 out of 473 rows (26%) are scans without a text layer: the unambiguous clue is physically present on the form, but the model returns only the dates of the result rows, not the header. This is a deliberate choice by the system, not a failure — but it is a queue for manual review.

- **An incorrect date remains visible under inattentive review.** A row with `ambiguous_order` status has the model's guess in the `date` field — for example, 12 January instead of 1 December. The source annotation is there, but if the reviewer looks only at the date, they see a potentially incorrect number.

- **The `open` status in the old version reflected that the mechanism had not yet been verified.** Now it has been verified — on a specific sample, with specific limits. "Holds" and "proven across everything possible" remain different claims.

**Updated:** 2026-10-06


## Why it exists

Medical lab results arrive in many forms: a paper form, a phone photo, a clinic scan — sometimes in German, sometimes in Finnish, sometimes with a stamp over the numbers. And the first, obvious impulse is simply to hand it to a model so it reads and records everything into the database.

The problem is that the model sometimes drops a decimal point. 15.2 becomes 152. In everyday text this is a curiosity. In medical numbers it is a different value, a different range, potentially a different conclusion. And the model does not signal this: it is confident, it simply made a mistake.

That is why the subsystem is structured not as "read and write" but as a pipeline with several checkpoints, where the canon — what is considered the final record of your health — is not touched until you yourself have said "yes".

## What it does, in plain language

When a document with lab results enters the system, several things happen in sequence.

**Two sets of eyes, not one.** The page is read by two independent passes — with different instructions, different approaches. Then they are compared: did they agree, how confident was each. This is not caution for its own sake — it is a way to catch exactly those errors that one pass cannot see, because it was that same pass that made them.

**Plausibility oracles.** Before a result goes anywhere, independent checkers look at it: is this even physiologically possible? Is the number within an acceptable range? Did it jump in a single day to something implausible? Do the units at least resemble something real? If anything looks suspicious, the row is flagged. The whole document receives a final status: either everything is fine, or there are questions.

**Draft first, then canon.** What was recognized does not fall directly into your medical history. It goes to an intermediate store — staging. There it sits and waits. The canon lives separately and does not change at this point.

**You decide.** Moving a result from staging to canon is only possible through your explicit "yes". And even then, the system first shows exactly what will change, takes a snapshot of the state before — and only after that, with a separate confirmation, makes the change. Rejected items remain rejected and do not pass into canon.

**The date is a separate story.** It is easy to transpose the day and month digits in a lab date: 04.10 or 10.04 — they look similar, they mean different things. Models handle this poorly, especially when the form is in another language. Therefore, the order of day and month is determined not by the model but by code — based on clues in the document itself: if there is a date somewhere in which the number is unambiguously greater than twelve, then that is the day, and the order is clear. If there is no clue, the date is not guessed — it is sent to a human for review.

## What to honestly say about its limits

It is important to speak plainly here, because this is medicine.

**The first hop is still in question.** Historically, the accuracy and completeness of data extraction in the first step has been a persistent problem — an audit from mid-2026 showed that roughly half of rows were being lost, and dropping a decimal point was a real problem, not a hypothetical one. Two passes and oracles are a safeguard, but they do not definitively close the defect. Work on it continues, and the honest status now is: it is not verified that the problem is fully resolved. This is not fine print — it is something that must be kept in mind until confirmation appears.

**On dates: verified on a specific sample, not everywhere.** The logic for determining day-and-month order was tested on just over twenty real documents from staging — all forms in that sample printed the day first, and the code handled them without errors. But this is a specific sample, specific forms. "Holds" and "verified across everything possible" are different claims, and the second one is not true here.

**Every fourth document with a date still goes to a human.** Roughly a quarter of rows with a printed date receive "unclear" status — because the scan has no text layer and the code cannot find an unambiguous clue, even though it is physically present on the form. This is not a failure, it is a deliberate choice: better to send to a human than to guess. But it means a queue for manual review.

**An incorrect date remains visible if you are not looking carefully.** When a row goes to "unclear", the date field still contains what the model read — its version, which may be incorrect. The source annotation is nearby, but if the reviewer looks only at the date and does not notice the annotation, they see a potentially incorrect number.

**The receipt date and the collection date are different things.** There was a case where the model took the date the laboratory received the sample, not the date the sample was actually collected. This is a different error, unrelated to day-and-month order, and how frequently it occurs is currently unknown.

**A scan without clues is a dead end.** If a scan contains no text layer and the model found no unambiguous date in it, the code cannot determine the order. Those rows also go to a human — there is no automatic answer.

## Where this lives in the system

The core logic of the subsystem lives in `lab_recognizer.py`. Intentions and invariants — what the system promises to comply with — are described in `subsystem_intent.yaml`. The verifiable promises and their current status are recorded there as well, including those that remain open.

The subsystem stands between the outside world (forms, photos, scans) and the canon — your medical history. Its job is to not let an error pass unnoticed, and to not let anything enter the canon without your knowledge.
