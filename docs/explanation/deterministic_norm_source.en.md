<!-- translation-of: docs/explanation/deterministic_norm_source.md sha256:8195ea3358f3 -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](deterministic_norm_source.md)

# Deterministic Norm Layer: Single Source, Loud Failure

## What Changed

- **The claim about unconditional checking (`fires_regardless_of_signals`) has been clarified.** The wording has been revised; what exactly changed in substance was not machine-determinable. The direction of the edit has not been established.

**Updated:** 2026-09-26


## Why It Exists

When the system looks at your lab results or blood pressure readings and decides whether to raise an alert, somewhere in that decision there has to be a line: "here is the boundary beyond which something is alarming." That line has to come from somewhere.

The problem is that "somewhere" is a dangerous word in medicine. If the boundary lives directly in the code, scattered across different places, it is easy for them to fall out of sync: fixed in one place, forgotten in another. If the boundary is computed each time by a smart model, the model can be wrong — and more importantly, it will never be possible to say precisely where a particular number came from. A fluent, confident, and incorrect boundary is more dangerous than no boundary at all: it does not look like an error.

The deterministic norm layer exists precisely to settle this question once and for all: clinical norms are taken from one place, and only from that place. Not from the model's reasoning. Not from numbers hardcoded into an algorithm. From a single table.

It is not a stand-in for the smart layer, and not a competitor to it. It is the load-bearing wall beneath it — the part of the structure that cannot be demolished no matter how persuasive the arguments from above may sound.

## What It Does, in Plain Terms

When the system needs to check whether a metric has crossed an alarming boundary, it goes to fetch that boundary from one place — the `absolute_thresholds` table. It does not matter which path led to the check: a morning report, a trend signal, a lifestyle agent, or the emergency fuse. All of them read from the same source.

What happens if the needed threshold is not in the table? The system does not substitute a guess. It fails loudly — specifically so that someone notices. Because a quietly wrong number will look like the truth, while a loud failure says plainly: "something is broken here, go fix it."

There is also a fallback for the case where the table is entirely unavailable — for example, the database is not responding. In that case the system uses a cached snapshot of the same data. But even then — not silently: a loud signal is raised indicating that the system is operating from the cache rather than the live source. The cache is not a normal state; it is an alarm.

One type of threshold deserves special mention — not absolute, but relative: "how many times above the reference range for this lab." Such a threshold exists on its own, but to apply it one must know what the reference range of the specific laboratory on your report form is. If that reference is unknown, the system neither guesses nor stays silent: it emits a separate loud signal for that specific metric. The threshold exists, but there is simply nothing to measure it against — that must be said out loud, not swept under the rug.

The absolute check — a floor and ceiling for each metric — fires unconditionally. Even if no other alerts are active at that moment, the fuse still looks at the absolute boundaries. It cannot be accidentally "skipped" because of a quiet day.

Where do the boundaries in the table come from? From clinical documents — not from the model's memory, not from someone's paraphrased recollections. One of the earlier sets of literals turned out to be exactly that, a recollection from memory — and was removed, replaced with snapshots of real sources. This too is part of the principle: not "seems right," but "taken from here."

## What to Honestly Say About Its limits

All the stated invariants of this layer are currently met. But "currently met" and "verified under all conditions" are different claims, and conflating them is not acceptable.

There is one known boundary that is important to understand. The value this path uses for comparison with a threshold is taken from the same calculation that computes percentiles, and that calculation skips any metric that has fewer than 14 days of data within the last 90. For a rare metric that lives as a column in a daily table, the threshold here therefore stays silent: it will not produce an error, but it will not check either. This was the case with blood pressure — a measurement on 2026-09-26 showed that the ceiling of 140 would not have fired regardless of the data. This is a limit, not a design intent; it is named here so that it is not mistaken for a guarantee. (This paragraph was corrected by hand: the model, when regenerating, called it "intentional behavior.")

For metrics of a different type — for example, peak daily blood pressure values — the approach is different: they are read directly from yesterday's data and do not depend on accumulated history. The check there operates without this condition.

In other words: the layer is honest about what it does not know, and loud about what is going wrong. But it is not omniscient, and for some metrics its coverage depends on how much data has already been collected.

## Where This Lives in the System

The rules and thresholds are stored in `rules_db.py` — that is where the `absolute_thresholds` table lives, the table from which all paths draw clinical boundaries. The intent of the subsystem — why it is designed this way rather than another — is described in `subsystem_intent.yaml`: it records that the deterministic layer is the load-bearing structure beneath everything else and cannot be overridden by a model's decision.

These two files are the entry point if you need to understand or verify where a particular threshold comes from.
