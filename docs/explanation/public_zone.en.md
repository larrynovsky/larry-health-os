<!-- translation-of: docs/explanation/public_zone.md sha256:3c7a40a403e7 -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](public_zone.md)

# Public Zone: A Tree You Can Open at Any Moment — Why This Is Not Just a Folder of Code

## What Changed

- **The census now sees the marker "this is real data"** (`zero_means_known_classes_only`, boundary refined, status still open). Background: the owner's personal data passed three cleanups under a report of 0 and was found via phrases written by the authors themselves — "real data <whose>", "taken from <live source>". Now such a phrase near an owner or data source stops the commit. During input, another leak was found — the owner's electrophoresis numbers in a test; replaced with synthetic data. Limit: the marker is caught, not the data itself — a leak without a marker passes through, and erasing the marker while leaving the data is the worst way to "fix" a block. (This item was written by hand.)

**Updated:** 2026-09-26


## Why It Exists

At some point this project will open to the world — as a clean repository, a single commit, with no history. But "at some point" does not mean "we will clean up before publishing". That very logic is the danger.

Without constant attention, every new commit silently brings something personal — a name, a diagnosis, a medication name, a city, a lab test date. The file looks like an ordinary test or helper code, nothing turns red, no one warns you. And then it ships to GitHub along with everything else.

So there is one rule: the tree must be publishable right now, not "almost ready to clean up". Not by a deadline, not after a review — now. The guard stands not at the exit, but at every commit.

## What It Does, in Plain Terms

The system maintains a dictionary of personal words and phrases — names, diagnoses, medications, and anything else that must not end up in public code. The dictionary is kept in the private zone and does not go into any export.

Before every commit, a census runs automatically: it looks only at the files that have entered the public zone and counts how many times words from the dictionary appear in them. That count is the debt. If a new commit increases the debt, it is blocked. If it decreases the debt, the decrease is recorded automatically. The debt can only decrease or stay the same — it cannot grow.

No dictionary at all? The commit is blocked too: the system does not pretend it ran a check when there was nothing to check against.

Separately, the system can judge genetic profiles. A single genotype at a single marker is just knowledge — millions of people share it. But a combination of many markers from one real person's actual data is already a portrait. Overnight, the system collects such combinations from public files and compares them against real data: if the picture is too close to any real person in the system, this is flagged. Only the file path and line number appear in the message — not a single genotype.

## What Is Honest to Say About Its limits

**The census is not a detector of personal data. It is a counter of known words.**

The census can find only what is in the dictionary. If personal data is written with different words, described indirectly, hidden in a binary file, or simply not entered into the dictionary — the census will not see it and will say nothing.

A result of "0 findings" (such a measurement was taken on September 26, 2026 across the entire public tree) means exactly one thing: not a single word from the dictionary was found in the public files. It does not mean there is no personal data there.

From that same date, the census also sees one additional class: if the author has themselves marked something as real data — "this is the real data of such-and-such". But a leak without such a marker, or with a marker phrased differently, still remains outside its view.

Here is what the census does not see today — this is not fine print, it matters:
- personal data not in the dictionary;
- meaning conveyed with different words;
- data without an author-applied marker, or with a marker in a different form;
- binary files;
- bare dates in test data;
- the word "oncological" and gene names in guards;
- old snapshots in the `docs/handoff` folder — they are in the private zone, but contain a date of birth, and this is open debt.

These are not future improvements — these are known blind spots the system has not closed yet.

**The commit guard holds, but not everywhere.**

If a branch is older than the census tool itself — meaning the file `pii_census.py` does not yet exist in it — pre-commit will let the commit through with a warning. In that case, only the nightly test will pick up the check. The guard holds — but "holds" and "checked everywhere" are different claims.

**The genotype judge also does not see everything.**

It looks only at dictionary-style constructs in `.py` files. Genotypes in `.md`, `.json`, in strings, and in lists of tuples are outside its view. If the system is installed for a single person, the judge is by construction unable to find distinguishing matches — it is blind. Common genotypes that appear in many people are not counted at all: a profile built only from such markers will not be caught, even if in combination with other data it identifies a specific person.

## Where This Lives in the System

The central tool is `pii_census.py`. It runs before commits, in nightly checks, and during genotype checks. It is the one that counts findings, compares them against the debt, and decides whether to let a commit through or not.

What counts as the public zone and what counts as private is described in `publication_zones.yaml`. The boundary is there too: what goes into the export and what stays with the owner.

The intent behind the entire subsystem — why it is structured this way and what decisions underlie it — is recorded in `subsystem_intent.yaml`. This is not technical documentation; it is an explanation of the logic: why the guard stands at every commit instead of waiting at the exit.
