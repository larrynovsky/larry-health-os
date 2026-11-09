<!-- translation-of: docs/explanation/agent_signals.md sha256:8b8a883b374e -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](agent_signals.md)

# Actions for a Personal Health-Signal Assistant: the Warm Layer

## What Changed

- **The `lab_interval_provenance_and_noise` invariant has been added (status: holds).** It is now explicitly recorded that a lab-test reminder includes the source of the due date — a physician, a manual setting, or an automatic system decision. This is a new promise, not prior behaviour under a new name.
- **The limits of verification for this invariant have been recorded at the same time.** "Holds" means the invariant holds under the conditions that have been checked — not across the full possible range of configurations and edge cases. This is a scoping clarification, not an expansion of guarantees.

**Updated:** 2026-11-06

**Updated:** 2026-11-07


## Why It Exists

Imagine an assistant that is silent most of the time. Not because it is lazy, but because silence is also an answer: everything is fine, there is no reason to disturb you.

But sometimes something changes. Load has persisted for too long. A lab test is due. A supplement you take is running out. In those moments the assistant needs to say something — once, to the point, without unnecessary noise.

That is exactly what this subsystem exists for. Its intent is simple: stay out of the way when everything is fine, and give a gentle reminder when there is a concrete reason to do so. This decision was made by the system owner — deliberately and explicitly.

## What It Does, in Plain Words

Each morning the subsystem looks at your health signals and asks itself several questions.

**Should the load be reconsidered?** If the data indicate that load has gone on too long, the assistant will suggest taking a look at it. If it already tried to do that yesterday, it will not repeat itself on the same day. But it will not "forget" either: it keeps a count of its attempts.

**Is it time to get lab work done?** If a lab-test due date is approaching or has already arrived, a reminder will come. It will honestly state where that due date came from: assigned by a physician, set by you manually, or decided by the system. This is not a minor detail — it matters to understand whose decision it is.

**Is a supplement reminder needed?** If it is time to restock or to take something on schedule, the assistant will say so.

Several simple rules apply that the system always observes:

- **Normal means no message.** If everything is fine, no message will be sent. Silence means "all is well," not "something is broken."
- **A repeat is not duplicated.** The same event will not disturb you twice in one day.
- **An empty recipient switches everything off.** If no message recipient is configured, the subsystem goes completely silent. This is not an error; it is an intentional safeguard.
- **Numbers and thresholds are not its concern.** The subsystem does not decide what counts as normal or abnormal. Those decisions are stored separately, in the parts of the system responsible for them. The assistant only acts on their signal.

Each morning the subsystem also checks whether a full morning data review has taken place. If it did not happen or is stale, that too is noticed.

## What Should Be Honestly Said About Its limits

The subsystem works as described above, and all of its core promises hold at the time of writing. But it is important to understand that "holds" and "verified under any conditions" are different things.

The rules about silence when normal, about no duplicates, about the provenance of lab due dates, about counting attempts — all of these hold. However, none of them has been checked across the full range of possible situations: rare data combinations, non-standard configurations, edge cases at the boundaries of the schedule.

This does not mean the system is unreliable. It means the system is honest in its claims: it does what it promises, under the conditions that have been checked. Beyond those conditions lies reasonable caution, not a guarantee.

If you notice that something arrived at the wrong time, arrived twice, or did not arrive at all — that is worth reporting. The system does not claim perfection; it claims honesty.

## Where This Lives in the System

The subsystem lives in the file **`agent_signals.py`** — that is where the logic resides that looks at signals and decides whether anything needs to be done. The intentions and rules it follows are described in **`subsystem_intent.yaml`** — that is something like its constitution: what it must do and what it must never do.

These two files read each other. One knows the rules; the other executes them. If you want to understand why the assistant behaved the way it did, it is best to start with `subsystem_intent.yaml`: it is written in human terms, explaining why everything is arranged exactly this way.
