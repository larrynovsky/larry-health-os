<!-- translation-of: docs/explanation/norm_from_documents.md sha256:ed43a2123e9d -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](norm_from_documents.md)

# Norm Is Derived from the Document, Not Typed In: Snapshot → Rows → Fuse: Explanation

## What Changed

- **New invariant: interpretation rules live in documents, not in code** (`interpretation_rule_is_document`, status: holding). This invariant was not previously recorded. It is now explicitly established: interpretation logic — for example, "treat a marker rise as a signal only after confirmation by the next draw" — must come from a source document. A rule baked into code would survive a change in clinical guidance without anyone noticing.

- **New invariant: the verdict that a norm is absent has exactly one storage location** (`norm_verdict_single_home`, status: holding). This invariant was not previously recorded. It is now established: if a norm for an analyte does not yet exist or has been deferred, that state is stored in one strictly defined place. Two locations would produce silent divergence and the risk of forgotten analytes.

**Updated:** 2026-09-27


## Why It Exists

At one point, clinical norm values lived in the head of whoever entered them. Typing a threshold by hand seemed like a small thing. But that is exactly where accuracy was lost.

In April 2026 it turned out that the system held a platelet threshold of 100 instead of 75, an ALT threshold of "seven times the upper limit of normal" instead of five, and oncology-marker thresholds that did not exist in any real clinical document. Nobody was deliberately lying. There was simply a retelling step between the document and the database — and that was precisely where the numbers got distorted.

The system is now structured so that no retelling step exists at all. A norm is not what someone typed; it is what is written in the document.

## What It Does, in Plain Language

Think of a clinical document — an international toxicity standard or a monitoring guideline, for example — as a book with an official seal. The system does not retype the relevant figures from it in its own words. It takes a snapshot of the page and places it in storage together with a checksum — a kind of digital fingerprint that will change if anything in the document changes.

A dedicated importer then parses that snapshot against a strict map: each term to its designated field, each number to its analyte. In the database, every row containing a threshold carries not just a number but a reference to a specific document: its name, version, and date. Without that reference, the row is not considered active. The only alternatives are two: the owner's personal threshold, or a physician's assignment linked to the identifier of a specific consultation. No third option exists.

When a threshold is expressed not as an absolute number but as a multiple — for example, "five times the upper limit of normal" — the system resolves it through the reference range on the laboratory slip from the same laboratory that performed the test. The standard document does not replace the lab slip; it works alongside it. If no lab slip is available, the system looks for a stable reference point across several documents. If that is not available either, the system signals this explicitly rather than staying silent or guessing.

Interpretation rules — not only numbers, but also logic ("treat an oncology-marker rise as a signal only after confirmation by the next draw") — also live in documents rather than being baked into code. This matters: a rule in code would survive a change in clinical guidance and continue judging by the old standard without knowing it.

The system checks document freshness on its own, on a schedule: it looks to see whether the original has changed at the remote source. If it has, a sensor turns red, showing both states: what it was and what it became. If the network is unavailable, the result is a warning, not silence. The system will not rewrite a norm automatically — that decision stays with a human.

The verdict that a norm for a given analyte does not yet exist, or is deferred until a certain date, is stored in one strictly defined place. If there were two such places, they would sooner or later diverge silently, and the system would stop noticing forgotten analytes.

The one person who reads the output of all this work and makes a decision is the physician.

## What to Say Honestly About Its Limits

All of the guarantees described here are, at the time of writing, holding — nightly tests check them regularly and signal red if anything breaks.

But "holding" and "verified under all conditions" are different claims, and it is important to be honest about that.

The tests verify that the threshold source is named correctly — not that the document actually contains that exact number. This is the boundary of snapshot coherence: the system trusts that the importer parsed the document correctly and checks that four copies are consistent with one another, not that they match a human reading of the original.

Biological variability is drawn from meta-estimates rather than from a single study — precisely because on one occasion a single study produced figures several times away from the stable median, and that conclusion persisted for half a day. But what counts as a sufficient number of consistent sources is a judgment threshold, not an absolute guarantee.

The document-freshness sensor reacts to a change in the checksum or update date. It does not read the content of the changes or assess whether they are clinically significant. That is the work of the physician reading the brief.

## Where This Lives in the System

The subsystem's logic is described in **`subsystem_intent.yaml`** — it states the intent and lists the invariants that must hold.

The live rules for working with norm documents — registration, freshness checks, resolution of multiple-based thresholds — are in **`norm_documents.py`**.

If you want to understand how specific checks are structured or where exactly a given row is stored, those two files are the first place to look.
