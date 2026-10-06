<!-- translation-of: docs/explanation/lab_recognizer.md sha256:1d22547740b2 -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](lab_recognizer.md)

# Lab-Results Recognizer: staging, oracles, human gate — how it works and what it does not promise

## What changed

- **A new open invariant `date_order_by_code` has been recorded (status `open`).** The mechanism for determining day/month order in dates on lab forms is formalized as a separate unresolved limit — not as a working protection, but as an open problem that the system explicitly acknowledges as unsolved.

**Updated:** 2026-10-04


## Why it exists

Lab results arrive in all kinds of forms: a paper printout, a phone photo, a scan with labels in German or Finnish. And almost always — columns of numbers where a single misplaced decimal point changes the meaning radically. The number 15.2 and the number 152 are not a typo with consequences — they are a completely different world clinically.

When a single model reads a scan directly and immediately writes the result to the database, it can make exactly that kind of mistake — and nobody will know. The recognizer exists precisely so that such errors do not pass unnoticed. Not "read and remember," but "read, double-check, ask me — and only then remember."

## What it does, in plain terms

When you upload a document with lab results, several things happen — one after another, not in parallel and not bypassed.

**Two views instead of one.** The page is read by two independent readers with different instructions. They compare their results with each other: how much they agreed, how confident each one was. This does not mean errors are ruled out — it means disagreement is visible rather than hidden.

**Plausibility checking.** After reading, independent oracles take over — separate validators that look for: whether a value has gone outside a physiologically possible range, whether it has jumped between measurements in a way that does not happen, whether units are mixed up. If something is wrong, the document gets a "needs attention" flag rather than quietly passing through. The outcome is either a green light or a flag.

**Staging — quarantine before canon.** What is recognized does not go directly into your real lab history. It is placed in an intermediate area — staging. Canon remains untouched until you explicitly say "yes." Even technically: by default the system only rehearses the transfer, and the real transfer requires an explicit permission and saves a snapshot of what existed before.

**You are the last line.** Everything that has passed both readers and the oracles still waits for your decision. What is rejected does not enter canon. You see what was recognized, you see what raised questions, and only your "yes" moves the result forward.

## What to say honestly about its limits

It is important to be direct here, because the system works with medical data.

---

**Dates — an unsolved problem.** The intent is this: the order of day and month in a date should be determined by code, not by the model. The model returns the date as it is printed, and the code attempts to figure out the order from context — it looks in the document for dates where the order is unambiguous (for example, if a number is greater than 12, it must be in the day position), and if the model transposed them — it corrects. If it cannot be determined, the date is not guessed: it is marked as ambiguous and sent to you for manual review.

But this promise is not yet fully delivered — it is in progress. This is exactly what happened in a real case: the material receipt date was printed one day before the collection date, and in staging it ended up with day and month transposed. Furthermore, even when the mechanism works, there is a boundary: it distinguishes day/month order, but does not always understand which date is being referred to — receipt of material or collection. These are different things, and if they appear separately on the form, the model may pick the wrong one. How often — unknown, there is one case. And also: if the scan contains no text layer and has no unambiguous dates that could help resolve the order — there is nothing to work with, and the row goes to a human.

---

**The first hop — historically unreliable.** An audit conducted in June 2026 showed: the model that reads the document first historically lost around half of the values and dropped decimal points. The two readers and the oracles are a safeguard against this. But the residual problem is not officially closed: it exists as an open defect. This does not mean the system is bad — it means the system is honest: where it is not sure, it says so.

---

In short: the system is designed not to take itself at its word. Two readings, external checks, quarantine, your decision. But "not taking itself at its word" is not the same as "guaranteed to be correct." Dates are still not resolved everywhere, the first hop is not definitively verified.

## Where this lives in the system

All the logic lives in `lab_recognizer.py` — that is where the pipeline runs from document upload through placement in staging and oracle validation. The intent behind the system's design and its promises are recorded in `subsystem_intent.yaml` — this is not code, but a description of what the system commits to doing and what counts as a violation. If behavior diverges from what is written there, that is not "working as intended" — that is a bug.
