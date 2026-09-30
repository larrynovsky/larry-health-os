<!-- translation-of: docs/explanation/genome_effect_allele.md sha256:80e1f5779a7e -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](genome_effect_allele.md)

# Genome: strand-safe allele resolution (fails safely): what it is and why it exists

## Why it exists

Your genome is a double helix. Each strand has its own "side," and the letter you read on one side is the mirror image of the letter on the other. When a company like 23andMe records your data, it picks one of the sides. When a medical database records the same position, it may pick the other.

If you simply compare those two records head-to-head — sometimes everything will match by accident, and sometimes it won't. Worse: sometimes a variant that looks "dangerous" from one side turns out to be completely neutral on the other — and vice versa. For most purposes this is an annoying technical detail. For an oncology patient it is fundamental: a confident but false "you're clear" is more dangerous than an honest "we don't know." A gap is visible; a person can see it and ask a doctor. False confidence is not.

This module exists precisely so that such false confidence does not enter the system.

## What it does, in plain terms

The resolver is a small, strict translator. It takes your genotype record and the record from a clinical database and brings them to a common "language" — a single DNA strand — before comparing them.

But the key part is not the translation itself; it is what happens when translation is impossible or unreliable.

There are situations in which an honest answer is not possible:

- **Palindrome.** Some letter pairs look the same from both sides of the helix. It is unclear which side is meant — and any choice would be a guess.
- **Multiple alternatives.** If a database records more than one variant for a single position, it is unclear which one to compare against.
- **Genotype not from the expected pair.** If the letters in your genotype do not correspond to what was expected at that position at all — that is also a warning signal, not a reason to speculate.

In all of these cases the resolver does not guess. It returns "unknown" — and reports why. This behavior is exactly what "fails safely" means: silence instead of conjecture.

Everything further down the chain — the parts of the system that read the result and build conclusions on it — interprets this silence correctly. "Unknown" means "not verified" to them, not "no risk." If somewhere it is written that a person is a carrier, the system checks: there must be a specific verified allele. If it is absent — that is a contradiction that is visible, not hidden.

## What is honest to say about its limits

It is customary here to speak plainly: all three documented promises of this module are, as of today, fulfilled and verified by tests. There are no disputed or non-working invariants that need to be flagged.

This does not mean the system as a whole is perfect — it is part of a larger organism, and its outputs depend on the quality of the data fed into it. But within the limits of what this particular module is responsible for, there are currently no honest caveats to make.

## Where this lives in the system

The resolution logic lives in the file **`effect_allele.py`**. The symmetry check is also implemented there — an automated test that verifies the result does not depend on which strand of the helix the data came from.

The overall intent of the subsystem — why it exists and what place it occupies in the architecture — is described in **`CLAUDE.md`**.

The specific promises the module makes and their current status are recorded in **`subsystem_intent.yaml`** — this is a living document that is updated alongside the code and serves as the single source of truth about what exactly is guaranteed.
