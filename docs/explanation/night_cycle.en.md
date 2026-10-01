<!-- translation-of: docs/explanation/night_cycle.md sha256:3586754a5657 -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](night_cycle.md)

# Night Decision Pipeline: a finding gets a name, a class, and a deadline

## What changed

- **Night repair: the night prepares the fix, the session lands it.** The invariant `night_repair_prepares_session_lands` (status=holds) has been added: each night the author works through up to two engineering-queue cards in a sandbox and the reviewer attempts to refute them; the fix lands as a patch on Studio, and the commit to main waits until the session either lands it (patch test on the stand is red without the fix and green with it), closes it (environment breakage), or rejects it. The owner takes no part in the chain.

- **The claim about the absence of automatic application has been lifted.** The invariant `auto_fix_applier_absent` has been removed: the former declaration "the carrier of the mechanism is not built and is not being built right now" is no longer a recorded fact in the registry.

**Updated:** 2026-09-30


## Why it exists

Imagine that every night the system looks at your health and notices something. Say, one indicator has gone slightly outside its usual range. The next night — again. And once more. If each of those observations has no name and no address, they never accumulate into a history — they look like fresh news every time. You don't know whether it is the same issue or a different one. You don't know whether anyone has already dealt with it or not.

That is exactly the problem the night pipeline solves: it gives a finding a stable identity. One name per subject. One home where it lives. A clear category: this the system will fix on its own, this goes into a digest, and this is a question that needs your answer. And a deadline: if no one has answered within two weeks, the card closes in silence — but only when a whole set of conditions is met, and not a single one can be skipped.

Without the pipeline, a stream of messages is noise. With the pipeline, they are managed questions with names, owners, and deadlines.

## What it does, in plain terms

Every night the pipeline passes through three sources. The first — crashes and errors the system caught during the day. The second — sensor warnings. The third — silence: if there has been no movement whatsoever in some area of work for more than six days, that is also a finding, and it also needs to be named.

Every finding gets a **name** — one, permanent, by which it can be recognised tomorrow and a month from now. It gets a **class**: the system will handle it on its own, or it goes into a specialist digest, or it is a question specifically for you. It gets a **card** — a place where the entire history is stored: what happened, what the options are, what each of them means, and who ultimately made the decision.

By the time the card reaches your desk it is already prepared: the question is phrased in your terms, the options are listed, and each has its own cost. If even one of the three is missing — the card does not come to you; it goes to the engineering queue with a note about what is incomplete.

Once a day the pipeline calls you — no more often. That call lists every decision that is waiting specifically for you, one line each, and the nearest deadline after which the card may close in silence. Technical findings that the system is to resolve on its own are not included in this call.

Silence as a card outcome is a special matter. For a card to close without your answer, all of the following must hold simultaneously: it must have a ready option, a way to undo, a date, and a specific assignee who has already carried out that option. And the action must not be irreversible. And it must not touch your personal data space. If even one condition is not met — the card stays on the desk and waits for you indefinitely. The system is designed so that the right to close silently must be earned explicitly, card by card. By default, silence does not mean closed.

Every closed decision has a recorded author: you answered yourself, the card closed in silence according to the rules, or the thread was closed entirely in the registry. There is no fourth option. A month from now it will be visible who made each decision — and that is not words, that is a record in the system.

The decision desk remembers everything that has ever been on it — cards are not deleted. If after a system migration there are fewer cards on the desk than there should be, the system does not stay silent: it reports this to you instead of the usual list.

There is one more thing worth saying separately: a sensor cannot be silent out of blindness. If it cannot see what it is supposed to watch — it says so out loud. "Cannot check here" is not a question to you; it is a separate record that goes into the weekly digest. A sensor's silence and its admission of blindness are different things, and the system tells them apart.

During the night, while you sleep, the system works through up to two engineering cards on its own: it looks for the cause, prepares a fix with a test, and checks it through an independent review. You are not part of that chain. The fix lands on the working machine and waits until the test confirms: red without the fix, green with it. Only then can things move forward.

## What is honest to say about its limits

All of the pipeline's promises hold — but "holds" and "verified everywhere" are different claims. Here is where the line between them falls.

**Silence is currently effectively disabled as an outcome.** The promise about closing in silence is correct, but the assignee registry is empty right now. While no one is in it — silence resolves nothing. The rule "silence equals delegation" is suspended until the first assignee with a test appears.

**"Finding disappeared" can mean two different things.** When a card is taken off the desk because the sensor no longer sees it — that may mean the problem is gone. Or it may mean the sensor itself broke and stopped looking. Against the second case stand separate liveness sensors and the requirement of a fresh artifact date, but not a hundred-percent guarantee. A check that does not run every day introduces additional complexity: if it missed its day, the card is removed and reopened on the next run, and the age resets.

**A fresh installation starts from zero.** The protection against desk-memory loss works from the second pass of the cycle — on the very first installation there is nothing to lose, and that is expected. But if both the desk and the database are lost simultaneously, the protection is silent.

**A sensor that wrote its refusal in its own words becomes a question again.** The phrase "cannot check here" is recognised by exact text. If the sensor phrases it differently, the refusal will not be recognised as such.

**An unknown finding class stays as a question for you.** The class is determined by the finding's name from a closed list. If the name is unknown to the system, the finding goes to your desk — until it is given the correct name.

**Night repair: the key lives in an environment variable.** The sandbox closes the file containing the secret, but not the variable itself. If the value ends up in the session output — other paths to exfiltrate it are not blocked. This is named, not closed.

**A thread can be recorded as closed without the owner's decision.** The system sees the status `closed` in the registry but does not verify that you were the one who set it. The norm "a thread closes by the owner's decision" is upheld by the closing ritual, not by code.

**The engineering queue has no voice about its own age.** Once a week — a line in the digest. If a card has been sitting there a long time — the system will not remind about it any more loudly on its own.

**The call waits for the cycle only at its scheduled minute.** If the cycle did not start on time — late calls do not compensate for that.

## Where this lives in the system

The pipeline lives in `night_cycle.py` — a script that runs at night on
