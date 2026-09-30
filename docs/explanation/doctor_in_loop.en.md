<!-- translation-of: docs/explanation/doctor_in_loop.md sha256:c74f0ca0dbfb -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](doctor_in_loop.md)

# Doctor in the loop: the doctor's response comes back as input: explanation

## Why it exists

There are things a smart system has no right to decide on its own.

When something in your health goes beyond habit and lifestyle — a symptom appears, a lab result, a troubling pattern — it goes to a living doctor. Not because the system "can't" reason. But because a medical conclusion without a license, without an examination, without accountability is not help — it is danger. Confident nonsense causes more harm than silence.

But simply "sending it to the doctor" and forgetting is not enough. If the doctor's response just lands as a chat reply and dissolves — the system learns nothing, updates nothing, and next time will act the same way it did before. That is the problem this module solves.

The doctor's response must come back inside the system as new data — not as text to be read, but as input that changes state.

## What it does, in plain terms

Imagine the system has put forward an assumption — call it a hypothesis. For example: "this pattern in your data is worth showing a doctor, because it may mean such-and-such." The hypothesis goes off for review.

The doctor responds. And this is where the key part begins.

**If the doctor confirmed** — the hypothesis is not simply "closed." It becomes the basis for the next step: a concrete task is created, a protocol of actions.

**If the doctor corrected** — the hypothesis is reformulated in light of their words and goes around again. The system does not argue or insist — it rethinks.

**If new lab results arrive** — even a verdict already reached is reconsidered. Because new data changes the picture.

**If the response is unclear or arrived with an error** — nothing is touched. The system does not guess or "fill in the blanks" on the doctor's behalf. An unclear response does not corrupt what is already known.

Meanwhile, the history is not erased. Every revision of a hypothesis is a new version on top of the old one, not a replacement. What has been confirmed does not disappear — it waits for the next portion of data. This matters: the system accumulates understanding rather than overwriting it.

This principle — the doctor's response comes back as input — is the load-bearing wall. Without it, the system would effectively be making diagnoses on its own. With it, it remains a tool, not an unsupervised medical device.

## What to say honestly about its limits

The entire construction rests on one condition: the system must correctly understand what belongs to the medical domain and what belongs to the behavioral — to lifestyle, habits, self-management.

If something medical is mistakenly classified by the system as "behavioral" — it will never reach the doctor. The load-bearing wall formally "stands" at that point, but has been bypassed. The doctor will not see what they should have seen.

**This is an open limitation, and it is honestly acknowledged as unclosed.** The accuracy and reliability of this classification — where the boundary lies between "this goes to a doctor" and "this can be managed independently" — is not verified in the system. It is not guaranteed that medical content will not leak past the doctor.

This is not a minor technical detail. This is exactly the place where, if it works incorrectly, real harm can occur — quietly, without warning. That is why we name it directly, rather than hiding it in a footnote.

## Where this lives in the system

The logic of returning the doctor's response as input is described and implemented in several places:

- **`hypothesis_resolution.py`** — this is where the mechanics live: how the doctor's verdict is applied to a hypothesis, how a new version is created, what happens with each possible outcome.
- **`CLAUDE.md`** — the overall intent of the system, in which this module occupies the place of a load-bearing wall: an explanation of why, without this return, the system would become an unlicensed medical device.
- **`subsystem_intent.yaml`** — the formal record of invariants: what the system commits to maintaining, and what is honestly marked as open and incomplete.

Reading them together makes sense — each answers its own question: "why," "how," and "what exactly is guaranteed."
