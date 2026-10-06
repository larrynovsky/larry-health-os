<!-- translation-of: docs/explanation/lab_intake_gate.md sha256:3245837bda86 -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](lab_intake_gate.md)

# Parsing incoming lab results: a missing dictionary match is not the final word

## What changed

Nothing changed in substance; the page was regenerated.

**Updated:** 2026-10-06


## Why it exists

When you forward a file to the bot, the system has no advance knowledge of what you sent. It could be a lab results form, a doctor's letter, a certificate, a discharge summary — or something entirely unrelated. Running every file through expensive recognition makes no sense: it costs money and time. So before recognition there is a filter — a gate — that decides whether it is worth treating the file as lab results at all.

But a filter is dangerous in its own right. If it makes a mistake and says "no" where it should say "yes", your lab results simply disappear. Silently: the system does not complain, does not warn, it just does not add anything to the health record. That is exactly what used to happen, and the subsystem is structured the way it is now precisely so that it does not happen again.

## What it does, in plain terms

**The filter does not make decisions alone.**

The first thing the system does is look at the words in the file — whether any of them are characteristic of lab result forms. This is fast and free. If the words are found, the file goes straight to recognition with no additional check needed. But if the words are not found, that is not yet a verdict.

A "no" from the dictionary is re-checked with the model. The system takes the first page of the file and asks the language model: does this look like a lab results form? The model is told in advance: if you are not sure, treat it as a form. It is better to check one extra time than to lose real results.

**Failures do not become silent losses.**

If the model provider does not respond due to transient problems — network issues, overload, timeout — the system does not give up. It waits and tries again, several times, over the course of several hours. Throughout this time you hear nothing: there is no need to worry about each individual attempt. If nothing worked in the end, the system will tell you plainly: "There was a failure on my side; I tried for several hours and it did not work." Not "please send a clearer copy" — but honestly, what exactly went wrong.

If the problem is with the key or the balance, that is treated as a separate situation, and you will hear about that specifically. The file waits; it does not disappear.

**Resubmission works sensibly.**

If you send the same file again after a failure or after a rejection, the system understands that you want to try again and takes the file into processing from scratch. If you sent a file the system decided was not a form, but you know it contains lab results — resubmission is treated as your verdict, and the file goes straight to recognition, bypassing the filter. The only exception: if the file has already been successfully parsed and added to the health record, a repeat submission remains a duplicate.

**Two parsers read the file, and you hear one outcome.**

When a file arrives, two "readers" examine it in parallel: one looks for tabular lab values, the other reads the file as a medical document — a conclusion, a doctor's letter, a discharge summary. Previously you could receive a response from only the first reader even if the second had not yet finished or had found something important. Now there is one outcome per file, and it is delivered only once both readers have finished their work.

If no lab results were found but the document was read and accepted into the health record, you hear exactly that: "accepted into the health record." If both readers found nothing — "nothing was added," and that is an honest outcome, not accidental silence. The hint "try sending it as lab results" appears only when there is a reason for it — for example, when a document looks like a lab result but could not be read in tabular form.

The file name in messages is the name you gave it, without any technical suffixes.

## What to say honestly about its limits

All of the behaviours described hold — they are verified by tests and live runs. But "holds" and "verified everywhere" are different things.

**On recognising key and balance failures.** The system can distinguish a key or balance problem from a transient failure — based on known error texts returned by the provider's SDK. This works. But this boundary has not been tested against a live empty key in production conditions — only against known error texts. If the provider changes the format of the error message, the system may not recognise the situation correctly.

There are no other disputed or unfulfilled claims as of today.

## Where this lives in the system

The central file of the subsystem is `lab_intake_watcher.py`: it is the one that receives the incoming file, runs the filter, coordinates the two readers, and composes the final message. The logic of what counts as a failure, what counts as a key problem, and what counts as a transient refusal is described in `llm_client`. The intent of the subsystem and the list of promises it is required to keep are recorded in `subsystem_intent.yaml` — this is a living document against which you can check what the system promises and what of that has been verified.
