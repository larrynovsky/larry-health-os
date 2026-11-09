<!-- translation-of: docs/explanation/lab_intake_gate.md sha256:2c9a5a200cb9 -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](lab_intake_gate.md)

# Parsing Incoming Lab Results: When No Template Match Occurs, More Than a Dictionary Decides

## What Changed

- **New invariant: a clarifying question when no numbers are found.** The promise `zero_rows_question` has been added (status: holds): if lab results were not parsed but the document was accepted as a lab result, the system asks a clarifying question instead of staying silent.
- **The wording about the combined outcome from both readers has been refined.** The description of the `one_outcome_after_both_readers` invariant has been adjusted; the direction of the change was not determined automatically — it is recorded as a wording clarification, with no conclusion about whether the guarantee was strengthened or weakened.

**Updated:** 2026-10-06

**Updated:** 2026-11-07


## Why It Exists

When you send a file to the bot, the system has to decide: is this a form with lab numbers, or something else — a referral, a doctor's report, a receipt? Paid recognition is only triggered for forms, and something has to stand at the door and decide.

That gatekeeper is the intake parser. The problem is that the gatekeeper can fail silently. It looks at the words in the file and checks them against its dictionary. If the dictionary is empty — and on a fresh install it is empty by default — a genuine lab result form gets rejected, and the person is told "couldn't read it, please send a clearer copy." The model never saw the file at all. That is neither honest nor useful.

The subsystem exists to fix exactly this: a dictionary rejection is not a verdict, a failure is not a permanent dead end, resending the same file is not a blind "duplicate" but a chance to sort things out, and the person always hears the real reason rather than a brush-off.

## What It Does, in Plain Terms

When you send a file, several things happen — not one after another like steps on a staircase, but more like several questions the system asks in parallel and honestly reconciles at the end.

**The gatekeeper does not decide alone.** First it looks at the words in the file and checks them against the dictionary. If no words matched — it does not say "not a form" and stop. It passes the first page of the file to a language model and asks it. When in doubt, the model treats the file as a form — this is a deliberate choice: it is better to run recognition one extra time than to miss a genuine lab result. If the words matched immediately — the model is not called, and no extra cost is incurred.

**A failure is not a permanent answer.** If something went wrong on the provider's side — the account ran out of funds, a key expired, the service is overloaded — the system does not write "failed" forever. The file is placed in a waiting state. For transient failures the system silently retries several times, with pauses. If nothing worked after all attempts — you receive a message that says exactly that: "failure on my side, tried for several hours, will not retry on my own." If the problem is a key or balance issue — you are told so directly, once and in plain words. Not "please send a clearer copy."

**Resending the same file is not always a duplicate.** If you send the same file again after a rejection or failure — the system clears the previous rejection and processes the file from scratch. If you resend it and wrote "these are lab results" — the file goes straight to recognition, bypassing the gatekeeper. Something counts as a duplicate only if the file was already successfully parsed.

**Two readers examine the file, and there is one outcome.** Your file is examined simultaneously: one reader looks for lab numbers, the other reads it as a document — a referral, a report, anything. Previously only the first reader responded — and if it found nothing, you heard "failed to read any values," even though the document might have been read perfectly well by the second. Now the outcome for the file is single, and it is delivered only when both readers have finished. You will hear "nothing was added" only when both are empty. If no lab results were found but the document was accepted — you are told about the document. If the document was read as a lab result but no numbers came out of it — the system does not stay silent: a clarifying question is asked, and you are warned that it will be.

**The response names the real reason.** This is perhaps the simplest and most important point. A failure on the system's side — that is what is written. A problem with a key or balance — that is what is written. A file accepted as a document — that is what is written. The filename in the response is yours, without internal service tags.

## What Is Honest to Say About Its Limits

All the promises described above hold — they are recorded as invariants and verified by tests. But "holds" and "verified under all conditions" are different things.

Several places where verification is incomplete:

**Key and balance problem detection** works by matching known error messages from the provider. If the provider changes the wording of an error — the system may not recognize it as a key problem. This behavior was not measured on a live empty key under real operating conditions.

**Delivery of the clarifying question** in the situation "lab results not parsed even though the document was accepted as a result" is protected against re-triggering on ordinary restarts. However, if a crash occurs precisely between the moment the system decided to ask the question and the moment it sent it — redelivery after such a crash is not guaranteed.

**The gatekeeper's dictionary** is data specific to a particular install. On a fresh install it is empty, and that is exactly what caused the first problem discovered. The language model covers such cases — but that is a safety net, not a replacement for a properly populated dictionary.

Nothing described above is an undisclosed failure — these are the honest limits of what has been verified.

## Where This Lives in the System

The core logic lives in `lab_intake_watcher.py` — that is the intake parser, the gatekeeper, and the coordinator between the two readers. The intentions of the subsystem — why it is structured the way it is — are described in `subsystem_intent.yaml`: there you can find which decisions were deliberate and why. If something about the system's behavior seems odd, `subsystem_intent.yaml` is the first place to look before concluding it is a bug.
