<!-- translation-of: docs/explanation/telegram_bot.md sha256:3eefe3abc1c5 -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](telegram_bot.md)

# Bot: fail-closed delivery to the owner — why this is not just "send a message"

## What changed

- **`owner_fail_closed` status changed: `holds` → `doc_drift`.** The owner protection mechanism works, but the documentation no longer accurately describes the actual behavior.
- **Claim adjusted.** The old assertion "fails at import if no file with owner identifier is present" no longer reflects reality: the mechanism was reworked due to environment variable leakage issues in pytest.
- **New behavior:** the owner identifier is resolved not at module load time but on first access; if absent — the bot throws an error rather than falling back to "let everyone in" mode. There is an explicit check before startup.
- **The essence of the protection is preserved:** without an owner the bot does not start, a "random first user" does not become the owner — but the specific enforcement mechanism has changed.
- **The divergence between documentation and code is honestly recorded as a current limitation,** not hidden behind a "everything is fine" formulation.

**Updated:** 2026-07-18


## Why it exists

Medical information is not ordinary notifications. It must reach exactly the person it is intended for, and the system must know that it reached them.

The bot is not an interface with buttons. It is a channel that has two properties without which it loses its purpose: it knows who its owner is, and it knows whether a message was delivered. Both properties exist not as a convenience but as protection against specific failures that have already occurred.

## What it does, in plain terms

**First: it only responds to you.**

The bot has a concept of an owner — the single chat it communicates with. This is not just a setting. If there is no owner information, the bot does not start. It does not begin operating "for whoever writes first" — it stops before startup and waits until the owner is explicitly defined.

The recipient for each message is taken from a single resolver — the shared place in the system where delivery routes are stored. This means a partner's card goes to the partner and not to you, and vice versa: each party has their own chain.

**Second: it knows the message was truly delivered.**

When a long message needs to be sent — for example, a weekly report — the bot splits it into chunks and receives an acknowledgment from the messenger for each chunk: an identifier of the delivered fragment. Only this counts as delivery.

It used to work differently: the system was satisfied that "the request was sent" and considered the job done. At one point a weekly report was sent — and never arrived. Nobody found out. After that the rule changed: delivery without an acknowledgment is not delivery. Message contents are not written to the log — they may contain biometric data.

## What to honestly say about its limits

It is important not to gloss over things here.

**The owner protection is preserved — but the mechanism is in the process of being reworked, and the documentation has not yet caught up with it.**

Originally the system was structured so that if the file containing the owner identifier was not found, the process would crash at load time. Strict, but reliable: it was impossible to start the bot without an owner.

That mechanism was changed. The reason is practical: the old scheme caused tests to crash because environment variables leaked into the pytest build. Now the owner identifier is resolved later — on first access — and if it is absent, the bot throws an error and does not fall back to "let everyone in" mode. There is an explicit check before startup that prevents the bot from starting without an owner.

The essence of the protection is the same: without an owner there is no startup, a "random first user" does not become the bot's owner. But the formulation "crashes at import," which may appear in older documents, no longer describes reality. This is a live area in the system: the mechanism has changed, the documentation is being updated, and until they fully align — this is an honest limitation worth keeping in mind.

## Where this lives in the system

The access logic — who is allowed to write to the bot — lives in `bot/filters.py`. That is where it is checked whether the incoming chat matches the owner.

The overall map of the subsystem, its place among other parts, and its connections are in `CLAUDE.md`. The intent of the subsystem, why it exists, and which properties it must preserve are in `subsystem_intent.yaml`. If something about the bot's behavior seems odd, these two files are the first place to look.
