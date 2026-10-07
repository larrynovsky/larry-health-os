<!-- translation-of: docs/explanation/document_intake.md sha256:37f204e22c58 -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](document_intake.md)

# Accepting Documents and Genome from a Human: the Model Only Suggests — How It Works and Why

## What Changed

- **The `types_by_content` claim wording has been refined:** the description of how the system determines a document type has been adjusted — the nature of the adjustment is not determined programmatically, and the direction of the change is unknown.

**Updated:** 2026-10-07


## Why It Exists

When a person first comes to the system, they already have a history. Years of test results, hospital discharge summaries, specialist reports, and possibly a genome data file. All of this sits on their phone, in the cloud, or on a home computer, and it would be strange to start from a blank slate while ignoring what is already known.

This subsystem exists precisely so that a person can bring their history with them — and so that the system does something meaningful with it. Not just file it away in a folder, but read it, understand what is written there, and suggest how to reflect it in the medical record.

But "suggest" is the key word. The system does not decide on the person's behalf. It reads the document and says: "Here is what I see in it, here is what I propose to record." After that, the person looks at it and decides for themselves.

## What It Does, in Plain Language

**How a document gets in.** A file can be sent directly to the bot, linked from the cloud, or specified as a path to a file on disk. The last option works only because the machine's owner has explicitly chosen to grant that trust: the system reads what it has access to on that machine, and that is a deliberate choice, not an accident.

For a link, the system downloads the file only from four known cloud storage services. Every redirect step is re-verified: it is not possible to supply a link that ultimately leads inside a local network. The system rejects such addresses.

**One document — one event.** It sometimes happens that a file with a Cyrillic name enters the system twice and creates two events instead of one. To prevent this, a sensor runs every night: it checks whether more than one event has appeared from the same source, and if so, it raises an alert. Duplicates are not merely a possible technical inconvenience: they can cause the model to parse the same document twice and propose the same thing twice. The sensor stops that.

**The receipt tells the truth.** When the bot accepts a file, it immediately replies with what will happen next. If the file format is unknown to the system or the parser is not running at that moment, the receipt says so directly, without pretending that everything is fine.

**The model reads and suggests; it does not decide.** After the document has been parsed, the model determines what it is: a blood test, a scan, an endoscopist's report, histology, or something that is not a medical document at all — for example, an insurance invoice. An invoice does not enter the medical record at all; the person hears "not a medical document," and the story ends there.

If the document is medical, the model proposes diagnoses and medications. The proposal is exactly that — a proposal: it sits separately and waits while the person reviews it. Nothing enters the medical record automatically. Only after the person has explicitly confirmed — via a command or a card in the interface — does the entry appear in the record.

**A quote as a safeguard against fabrication.** Every model proposal is accompanied by a verbatim quote from the document. This is not decoration — it is a check. If the model wrote something that is not in the document, the quote will reveal that. For documents with a text layer, any proposal without a quote is discarded automatically: if there is no quote, the model invented it. For scanned images, greater precision is not possible: OCR is itself imperfect and can make mistakes. Therefore, a discrepancy there does not discard the card but flags it — the person can see that it is worth double-checking.

## What Is Honest to Say About Its Limits

All of the promises described hold — these are not words but verifiable invariants that are tested automatically. But "holds" and "verified in all cases" are different things, and honesty requires naming that difference.

The nightly duplicate sensor was tested on specially prepared cases — three variants that were supposed to fire, and they did. That is good. But it does not mean the sensor will catch every way a duplicate can arise in real life.

Permitted links were tested against a real Google Drive link from a real machine. That is a live check, not a synthetic one — and that is valuable. But one link, one cloud service, and one redirect scenario were tested.

Document type classification was cross-checked against manual parsing on a set of ambiguous cases, and the results matched in all of them. That is encouraging, but the set of ambiguous cases is finite, and real life is inventive.

Quote verification for scanned documents works through OCR, which can itself make mistakes. That is why a discrepancy there is flagged rather than discarded — and that is precisely why the person should look at such flags carefully.

A file path on disk represents trust between those working on the same machine. The system reads what it has access to. If the machine is shared, this is important to understand in advance.

## Where This Lives in the System

The central file of the subsystem is `import_medical_events.py`: it is the one that accepts the document, starts parsing, calls the model, forms the proposal, and sends the receipt. The intent and boundaries of the subsystem are described in `subsystem_intent.yaml` — it records exactly what the system undertakes to do and where it deliberately stops. The nightly sensor that watches for duplicates lives separately and observes `import_medical_events` events from the outside — without relying on the memory of the import process itself.
