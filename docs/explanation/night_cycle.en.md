<!-- translation-of: docs/explanation/night_cycle.md sha256:d0d7c7da6c15 -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](night_cycle.md)

# Nightly Decision Pipeline: A Finding Gets a Name, a Class, and a Deadline

## What Changed

- **Red tests are now fixed by the nightly repair.** Since 06.10 (owner decision: "auto-repair must exist"), a test that has been red two nights in a row becomes a `test:<selector>` card in the engineering queue, and the nightly repair picks it up like any other card; a test that turns green removes its own card. Before this, red tests were never added to the queue at all. Limit: two cards per night — if the queue is long, a test card waits its turn.

- **The question to the owner "should auto-repair be built" has been removed.** The `fix-applier-input-returned` card is no longer created: it was answered by building the nightly repair (30.09) and the owner's word (06.10).

**Updated:** 2026-10-06


## Why It Exists

Imagine that every night the system notices something concerning — a sensor flickered, a test went red, a project hasn't moved in a long time — and simply sends you a message. The next day the same thing sends another message. The day after, a third. A week later you no longer know: is this one story or three different ones? You seem to have resolved one of them — but which one?

The pipeline exists so that this doesn't happen. Its main job is to give each finding a stable identity: a name that doesn't change from night to night, a class (who is supposed to act here), and a deadline. Without this, the stream of messages is just noise in which neither progress nor anything stuck is visible.

There is another reason. The system works at night, without you, and makes some decisions on its own. That is convenient — but this is precisely where silence is most dangerous. If the autonomous part quietly elevated its own privileges and closed a medical question by itself — that looks like silence. If a card closed silently where silence should not have occurred — silence. If one subject spread across two names and one of them keeps ringing — silence. If a sensor sees nothing because it is blind, not because everything is fine — silence again. The pipeline is built so that each of these four kinds of silence is distinguishable.

## What It Does, in Plain Words

Every night the pipeline collects three kinds of things: errors and warnings from sensors; signals that some project hasn't moved in a long time; results of repairs the system attempted to make on its own.

The first thing it does with a finding is give it a name. One name, stable. The name and class live in one place, and only one: if they lived in two, one subject could become two, and while you close one, the other would keep ringing.

Then it decides who should act, and puts the finding in the right home.

There are three classes. The first is "mine to fix": the system will handle it on its own, without you. The second is "into the digest": this is a technical finding that the engineering queue will deal with separately; you don't need to know the details. The third is "your decision": the question is on your desk, and it will not close without you.

Only a card that has three things reaches your desk: a question in plain words, a closed list of options, and the cost of each. If even one of the three is missing, the card goes to the engineering queue with a note: this is an unfinished diagnosis; there is no reason to put it on your desk.

Every morning the bell rings once. It names every waiting question in one line of plain words, states the nearest deadline, and gives one phrase that opens the full cards. Not five rings — one. Not "N questions on the desk" — but what exactly those questions are.

After two weeks of silence a card may close by default — but only if it has an option, someone has already carried it out before the decision was recorded, the action is reversible, and it does not concern your personal space. Without this, the card waits for you indefinitely. A default close is not "close quietly"; the right to close silently is granted explicitly, one card at a time.

Every closed question has its author recorded: you yourself, default, thread closure, or "not on the desk." Without an author, "resolved" reads as your word a month later — and that may not be true.

The desk does not forget what was decided. The number of cards is remembered in the database, and if one day there are fewer cards, the system will understand that the desk has lost its memory, will stop laying out cards, and will tell you directly.

A sensor that notices nothing is required to say so out loud — otherwise "no errors" and "I wasn't looking at anything" are indistinguishable. A sensor that sees its subject not in this environment (for example, a container cannot see host tasks) also says so directly — such a finding goes into the weekly digest, not to you.

The system attempts to fix the technical queue on its own, at night: it finds the cause and brings a fix with a test. The fix is accepted only if the test is red without it and green with it — the author's word is not enough for this. The autonomous cycle cannot elevate its own privileges: it can only downgrade an action to "put on the owner's desk," but never upgrade it to "do it myself." This is not a rule the system assigned to itself out of caution — it is structured so that there is simply no other path.

## Honest Statement of Its Limits

Everything described holds. But "holds" and "verified everywhere" are different claims. Here are where the limits are real, not fine print.

**Default close does not actually resolve anything yet.** The promise "silence = delegation" is formally fulfilled, but the registry of those who can execute decisions by default is currently empty. The mechanism exists; the executors do not yet.

**A missing finding is not always good news.** When a card is removed from the desk because its finding is no longer present in today's check, that may mean "fixed" — or it may mean "the check broke and is silent." Against the second case stand the liveness sensors for checks and the requirement for a fresh artifact date, but not absolute protection.

**If both the desk and the database disappear at once, the protection is silent.** The memory-loss insurance works when one thing disappears. If both disappear together, the system has nothing to push off from.

**A sensor that wrote "cannot check" in its own words outside the required constant will become a question again.** Refusal is recognized by the exact start of the line: a slightly different wording and the protection does not trigger.

**An unknown class label remains "your decision."** If the system encounters a label it cannot recognize, the card will land on your desk — until it is named correctly.

**There is no watchdog for the age of the engineering queue.** Engineering queue cards appear in the digest once a week — in one line. How long they have been waiting there is not tracked separately.

**Nightly repair is visible only at commit time, not in real time.** The liveness of the nightly repair is checked by a label at the moment someone attempts to merge code — not by a monitor that watches continuously.

**The API key is in the environment, not only in the file.** The sandbox closes the secret file but not the environment variable: if the value ends up in output, the carrier will not write it down, but other paths to leak it are not blocked.

**Thread closure is checked by status, not by the owner's word.** The machine sees "closed" in `INDEX` — the code does not check who set it or how. The guarantee that a thread closes only by your decision is maintained by ritual, not by code.

**A thread with no row in `INDEX` remains without a card.** If a thread was renamed and the row was lost, removing
