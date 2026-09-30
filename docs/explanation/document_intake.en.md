<!-- translation-of: docs/explanation/document_intake.md sha256:150c53202f7a -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](document_intake.md)

# Receiving Documents and Genome from a Person: the Model Only Proposes — Where This Subsystem Came From and Why It Is Built the Way It Is

## What Changed

First version of this section.

**Updated:** 2026-09-24


## Why It Exists

When a person opens a personal medical system for the first time, the system knows nothing about them. Medical history, lab results, discharge summaries, scans, genome data — all of it sits in their folders, in the cloud, in a box of papers. For the system to start being useful, those documents need to be brought in somewhere.

But simply bringing them in is not enough. They need to be brought in in a way that lets you trust what the system does with them afterward.

This is where a non-trivial problem lies. When the system reads a medical document and extracts something from it — a diagnosis, a medication, a lab result — it does so using a language model. And language models are good at making things up convincingly. They can produce a quote that does not exist in the document. They can "recall" a diagnosis that is not there. If the system silently writes such extractions into the medical record, the person never finds out — and a fabrication dressed as fact settles into the record.

A separate risk is unnoticed repeated import. For example, mishandling Cyrillic characters in a filename can create another event and call the model again on every parse. Without a separate sensor, a coherent result does not reveal this failure.

These two risks — model fabrication and invisible duplicates — explain the design of the intake subsystem.

## What It Does, in Plain Terms

A person brings documents in one of three ways: drops a file into the bot, provides a link to cloud storage, or specifies a path to a file on their own machine. The subsystem accepts this, parses it, and produces not finished entries in the record, but **proposals**.

Here is what that means in practice.

**The model proposes — the person decides.** Everything the model extracts from a document — diagnoses, medications — goes not into the record but into a list of proposals. They sit there and wait. Until the person approves each one individually, nothing enters the medical record. There is no automatic addition.

**Every proposal carries a citation.** If the model says "such-and-such diagnosis is mentioned in the document," it must show the exact words from the document it is relying on. For documents with a text layer, the system checks: are those words actually there? If not, the proposal is treated as a fabrication and discarded. For scans and images where text is recovered by OCR, the situation is more complex: OCR itself can make mistakes, so there a discrepancy does not discard the card but flags it — the person sees that this entry warrants closer attention.

**One document — one event.** A nightly sensor checks that each document produced exactly one event in the record. The check is independent of the import process's memory and detects repeated entries.

**The receipt tells the truth.** When the bot has accepted a file, it immediately reports whether the document will be parsed. If the file format is not supported or the parser is not currently running, the receipt says so plainly, rather than promising something that will not happen.

**Links — only from verified sources.** Fetching via link works only for a small number of known cloud storage services. Every redirect is checked against the same rule. Internal network addresses are rejected.

**A file path on disk is a matter of trust.** If a person specifies a path to a file on their machine, the system can read any file on that machine. This is a deliberate decision: the system trusts those who work on that machine. This boundary is stated explicitly, not hidden.

## What to Say Honestly About Its Limits

All of the commitments described above hold as of today. But "holds" and "verified under any conditions" are different claims, and honesty requires distinguishing them.

Citation checking stops the model's fabrications in documents with a text layer — where cross-checking is possible. For scans and images, the protection is weaker: a discrepancy is visible, but it cannot be resolved automatically, because OCR is itself imperfect. The person sees the flag — and makes the call from there.

The duplicate sensor works like a night watchman: it notices what has already happened, rather than preventing an event in real time. This is better than nothing, but it is not the same as making duplication impossible in principle.

The list of supported cloud storage services is a deliberate restriction, not an accidental one. A link from anywhere else will not work. This is an intentional security boundary, not an oversight.

A file path on disk is an open boundary. The system does not try to conceal this, but it is important to understand: this is an architectural decision that carries a cost. Whoever has access to the machine has access to the documents.

A genome and three years of lab results are a large volume of sensitive data. The subsystem accepts and parses them. What exactly happens to that data afterward, and what conclusions from the genome the system may propose — those are separate questions that fall outside the scope of this text.

## Where This Lives in the System

The central file of the subsystem is **`import_medical_events.py`**: it handles document parsing, citation verification, proposal generation, and interaction with the bot. The nightly sensor that watches for duplicates runs separately and checks specifically the events that this file produces.

The intent of the subsystem — why it exists at all, what it is supposed to do, and where its boundaries lie — is recorded in **`subsystem_intent.yaml`**. This is not technical documentation and not a manual: it is a record of intent, something to return to when the question arises of "why on earth was this built this way?"
