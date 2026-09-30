<!-- translation-of: docs/explanation/lab_recognizer.md sha256:fbba750073fe -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](lab_recognizer.md)

# Lab Results Recognizer: staging, oracles, human gate — how the system reads paper lab results and why it doesn't take its own word for it

## What changed

- **Double reading (`two_model_reconciled`)** completed a run on 2026-07-30 in the staging environment (canon snapshot: real code and data, but not the production database). No confirmation existed before.
- **Staging + human gate (`staging_then_human_gate`)** completed a run on 2026-07-30 in the same staging environment. No confirmation existed before.
- Both confirmations are valid within staging limits: they cannot be called verification on a live system.
- The open first-hop defect (`hop1_precision_recall_open`) remains unclosed — its status has not changed.

**Updated:** 2026-07-30


## Why it exists

Lab results rarely arrive in a convenient form. More often it is a photo of a form taken on a phone in a clinic hallway, or a scan in another language, or several pages in small print. And the task is not simply "recognize the text" — it is to transfer medical numbers to a place where they will influence decisions about your health.

This is where the real problem lies. When an ordinary model reads a scan directly, it can drop a decimal point: 15.2 becomes 152. In ordinary text this is an awkward mistake. In medical data it means a different person with a different diagnosis. A number that enters a medical record incorrectly does not simply sit there — it participates in comparisons, trends, and reminders.

That is why this subsystem exists not to be smarter than one good model. It exists so that no number enters your medical history without verification — and without your explicit consent.

## What it does, in plain terms

Think of a pipeline with several checkpoints placed one after another.

**First checkpoint: two readers instead of one.** The page is read by two independent "eyes" — two different approaches with different instructions. They do not know each other's answer in advance. Afterwards the results are compared: did they agree, and how confident is each in its reading. If the two views diverge, that is an alarm signal, not a silent acceptance of one of the variants.

**Second checkpoint: plausibility oracles.** Even if both readers agreed, their result goes to independent reviewers — oracles. These look not at the text but at the meaning: does the number fall within a physiologically possible range, did an indicator jump in a way that does not happen in a living person, are the units of measurement mixed up. As a result, each document receives a label: everything is fine — or something requires attention.

**Third checkpoint: staging and human gate.** Even after passing both previous checkpoints, the result does not go directly into your history. It is placed in intermediate storage — staging. The canon, that is the real medical record, remains untouched. Transferring data to canon is only possible through an explicit action, and by default even that action operates in "show what would happen" mode — with no real changes. For changes to actually happen, an explicit flag is required. And anything you reject does not proceed further.

Before changing anything in the canon, the system takes a pre-snapshot — recording how everything looked before. This means there is always something to roll back to.

## What to say honestly about its limits

It is important to speak plainly here, because this concerns medical data.

The entire architecture described — two readers, oracles, staging, human gate — is a system of safeguards. But a safeguard does not mean the problem is solved.

**The first hop remains an open problem.** An audit conducted in June 2026 showed: the reliability of initial data extraction from a scan has historically been systematically violated. Extraction recall was around 50% — meaning roughly half of the indicators could be lost. Decimal point loss was recorded as a real, recurring defect. The ensemble of two readers and the oracles are precisely an attempt to catch such errors. But the residual defect is not closed and is not certified as fixed. This is not "it existed and was repaired" — this is an open limit right now.

What this means in practice: the system honestly signals suspicions, but does not guarantee it will catch everything. Your review of the result before confirmation is not a formality — it is a real part of the protection.

No other boundaries of what is proven have been declared in this version.

## Where this lives in the system

The subsystem lives in several places that work together.

`lab_recognizer.py` — this is the executable core: page reading, comparison of the two readers, oracle invocation, and writing to staging all happen here.

`CLAUDE.md` — the architectural document describing the design principles of the entire system and the place of this subsystem among others. If you want to understand why it is structured this way and not another way — that is where to look.

`subsystem_intent.yaml` — a machine-readable declaration of intent: what the subsystem commits to doing, which invariants hold, and which are open. This is where it is recorded that `hop1_precision_recall_open` has status `open`.
