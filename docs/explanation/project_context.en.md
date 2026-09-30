<!-- translation-of: docs/explanation/project_context.md sha256:46a7227771f4 -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](project_context.md)

# Anti-duplicate and onboarding: data-driven discovery + gates: why it is built this way and not another

## What changed

- **The page intent has been refined:** the title and frame have shifted from "why this is needed and how it works" to "why it is built this way and not another". This is an intent change, recorded in the manifest.

- **The wording of the `lessons_repeat_after_adoption_red` rule has been refined:** the description of how a lesson is considered repeated and what gets marked as failed has been reworked. The direction of the edit is not determined by anchor machine-side — it is presented as a wording refinement, with no conclusion about whether the guarantee has been strengthened or weakened.

**Updated:** 2026-09-30


## Why it exists

When an agent works in one project for a long time, it accumulates context — a history of decisions, files, code. But the larger that context grows, the easier it is to get lost in it: the agent starts building things that have already been built. Not because it is "bad" or inattentive, but because it has no reliable way to check whether the capability it needs already exists before creating a new one.

This is called duplication. It can be obvious: a function with the same name appears a second time in a different file. But it can also be invisible: a capability that is essentially the same lives under a different name, and no word search will find it.

Duplicates are not just excess code. They are diverging versions of a single truth. One gets a fix; the other does not. Over time the system stops being understandable even to those who work in it.

This subsystem exists so that duplicates do not pass unnoticed — neither the obvious ones nor the hidden ones.

---

## What it does, in plain words

The subsystem operates in three places simultaneously: before work, during work, and at the finish.

**Before work — onboarding.** When an agent starts a new work thread, it does not read a long map of explanations — an experiment showed that does not work. Instead it pulls what it needs itself: first the capability registry catalogue, then a specific page, then the code. This is called pull: the agent goes after knowledge on its own rather than receiving it as a pre-packaged stack. Together with the structure of the system it also receives the lessons of past threads — recorded cases where something went wrong and was reflected on.

**During work — data-driven discovery.** If an agent wants to build a new capability, it must first ask: "Does something like this already exist?" The search is not organised by function names but by data — by tables of what each capability reads and writes. This makes it possible to find a duplicate even when it has a completely different name. If the search finds nothing, that does not automatically mean "there is nothing" — the result carries a note about how much of the registry was scanned, so that "nothing found" does not turn into false confidence.

**At the finish — the duplicate gate.** When an agent is about to commit code changes, a check fires. If the new code introduces an exact name match with something that already exists — in a different file but within the same system — the commit does not pass. This is a hard block. If the match is not by name but by data, the gate warns but does not stop: the agent decides. Importantly, the check only looks at new names; it does not touch existing code.

The receipt confirming that the preflight check was passed is valid not by time but by composition: it remains in force as long as none of the modules and tables the agent read have been changed. The moment a neighbour adds something to the same table, the receipt becomes stale and must be re-read.

There is one engine for the entire machine. Different projects connect to it through a manifest — each project declares itself, and the engine knows about it.

---

## What to say honestly about its limits

It is important not to gloss over anything here.

**What works and holds.** The duplicate gate on commit works: live closings of several threads passed without bypass. Data-driven discovery works: a hidden duplicate under a different name is visible through the read and write tables. Onboarding via pull works: the agent receives the registry, the page, the code — and the lessons of past threads along with them. The receipt is tied to composition, not to time — this has been verified with a real git test.

**What is still open.** The most honest limitation is this question: does the agent call discovery before it starts building, rather than after? Forcing this is not possible: there is no hook in the environment that would stop the agent before it creates a new tool. Only the outcome is enforced — the gate on commit. The habit of calling discovery in advance is not proven.

An attempt was made to measure this: over 51 days of logs, out of 27 commits that introduced a new file, 18 had a capabilities call on the same day or the day before, and 9 did not. But no verdict could be drawn from this — and here is why: before 14 September 2026 the log recorded only calls made through one channel. A call from the terminal left no trace at all. Every "no call" is indistinguishable from "there was one, but from the terminal". This channel has been closed since 14 September; how settled the habit is can only be determined from material accumulated after that date.

**Where lessons work, but not everywhere.** The lessons of past threads do genuinely live in the system and are genuinely checked. The rule is: if a lesson was adopted and then a new lesson with a later date appeared in the same group, that is a signal the rule did not hold, and all lessons in the group are marked as failed. This holds. But this promise has limits, and calling them fine print would not be honest:

- Lessons are presented to the agent at the start of work by the `letitbe` ritual. Nothing checks machine-side that the ritual was actually called. This is held in place by text, not by mechanism.
- For lessons migrated into the corpus before 28 September 2026, the adoption date is the day of migration, not the actual day. If a repeat occurred between the real adoption and 28 September, no signal will fire.
- A group is defined by address and subject key — by data, not by meaning. A repeat recorded with a different address or a different key will not be seen by the rule. A lesson recorded on the same day as adoption is not counted as a repeat.

---

## Where this lives in the system

The subsystem does not exist in a single file — it is distributed across several points, and each has its own role.

**`project_context/indexer.py`** — this is the engine that builds and updates the capability registry. It knows what data each capability reads and writes, and data-driven discovery rests on exactly that knowledge. One instance for the entire machine.

**`docs/explanation/discovery_before_build.md`** — the warm onboarding page: an explanation of the intent in plain words, the very thing the agent pulls at the start of a thread. This is not an instruction and not a reference — it is an understanding of why the system is built this way and not another.

**`subsystem_intent.yaml`** — the subsystem manifest. This is where its composition is declared: which modules and tables belong to it. The composition token — a fingerprint of that composition — is used to validate the receipt at merge time.
