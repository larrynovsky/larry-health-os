<!-- translation-of: docs/explanation/problem_list_proposals.md sha256:b7724bab634c -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](problem_list_proposals.md)

# Medical Record Suggestions: An Edit as the Owner's Unit of Decision — Why and How It Works

## What Changed

First version of this section.

**Updated:** 2026-09-01


## Why It Exists

A medical record is a list of health problems, and only the owner should change it. Not the model, not a background algorithm, not an automatic entry produced by an analysis. The owner.

But "the owner decides" is an empty promise if a proposal never reaches them. An independently invented example: a model proposes the training condition `Example_Condition_A`. Its batch also contains a disputed edit, so the entire batch is rejected. On repetition, the next batch expires the previous one through an overlapping `problem_id`. The proposal can thus be lost before reaching the "approve" button, regardless of its content. This illustrates the mechanism with a fictional example, not a medical-record history.

This subsystem exists so that the owner's gate actually works: every finding reaches them as one specific action requiring one press — and only then enters the record.

## What It Does, in Plain Terms

**One edit — one line.** If a model proposes adding a problem or removing one, that is one proposal living on its own. Not a batch of five items where the fate of all depends on the most contested one. Each proposal is approved or rejected on its own — via a button in the bot or in the dashboard.

**A repeat is a counter, not a new line.** If the same finding is proposed again the following week, the old proposal is not dismissed and not cloned. A note simply appears alongside it: "proposed for the second week," "proposed for the fifth week." The owner can see that this is not new information but a persistent repetition of the same signal.

**What counts as "the same" is determined by data, not by chance.** The clinical conditions reference maps proposal text to a condition. In an independently invented example, "training condition A" and "condition A from the training dataset" both map to `Example_Condition_A`. Matching the condition establishes proposal identity; it does not confirm a diagnosis. Without a shared reference, the system cannot reliably distinguish a duplicate from a new signal.

**If the problem is already in the record — no proposal is created.** Adding something that is already recorded means creating a duplicate. The system notices this and simply does not save such a proposal. Nothing to approve means nothing is shown.

**No auto-entry.** Models — the GP reviewer, the literature and survival curators — can only propose. An entry into the medical record happens only when the owner presses "approve." This is not a technical constraint but a deliberate decision: the record is the owner's space, and the model does not enter it without being asked.

The bottom line is simple: a laboratory finding that a model considers significant now has a real path to the medical record. Not an automatic one — through a human. But one that does not get lost along the way.

## What Is Honest to Say About Its Limits

All the described invariants hold — they have been verified and work. But "holds" and "verified in every case" are different things, and it is important to be precise here.

No claim is made that the system has been tested in every conceivable scenario: with non-standard proposal phrasings, with unusual intersections of conditions in the reference, with edge cases in the repeat logic. The invariants are satisfied in verified cases — and that is the honest boundary of what can be asserted.

A separate point concerns the nature of the proposals themselves: the system guarantees that a proposal will reach the owner and will not be lost. It does not guarantee that the proposal is clinically correct. Assessment of the content remains with the person pressing the button. A single press is not automatic agreement with the model's correctness — it is a deliberate decision about whether this entry belongs in the record.

## Where This Lives in the System

The core logic is in `problems_db.py` — that is where the rules live for how a proposal is saved, how the repeat counter is updated, how a duplicate against a live record entry is checked. The functions called from the bot when "approve" is pressed are there as well.

The intent and boundaries of the subsystem are recorded in `subsystem_intent.yaml` — a document that explains why it is structured this way, which decisions were made deliberately and when. This is not code, but it is an important part of the system: without it, it is unclear why certain things are done one way and not another.

The clinical conditions reference — `clinical_kb/_index.yaml` — determines what counts as "the same" during duplicate checking and during identification of repeated proposals. It is shared across several parts of the system: the same condition that decides whether a proposal is a repeat is later used in other places as well — for example, when activating rows in the nutrition table.
