<!-- translation-of: docs/explanation/mcp_gateway.md sha256:f81dbea42d0f -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](mcp_gateway.md)

# Cloud Assistants: One Memory, Login Through a Bot, Only MCP Exposed: How It Works

## What Changed

- **The subsystem intent has been clarified.** The goal statement has been reworked; the exact direction of the shift has not been machine-recorded, so no direction of change is introduced here.

**Updated:** 2026-10-07


## Why It Exists

When a person asks Claude or ChatGPT about their health, those assistants know nothing about them — unless you paste the data into the chat yourself. That is inconvenient and risky: it is easy to forget details, paste something outdated, or mix up units.

But there is another danger. If you give an assistant raw lab tables and a list of diagnoses, it will try to be helpful and fill in the gaps with its own conclusions. It may decide that a closed, no-longer-relevant problem is today's limitation. Or it may invent a dietary rule that already exists in the system but simply was not in its view.

This layer addresses exactly these two questions at once: *how the assistant gets data at all* and *what exactly it gets*, so it does not have to guess.

The idea is simple: Health OS is one memory. Claude and ChatGPT are not separate stores — they are windows into it. They see the same thing the Telegram bot sees — no more, no less. Absence is marked explicitly so it cannot be mistaken for data.

## What It Does, in Plain Terms

**One memory for all assistants.** The Telegram bot, Claude, and ChatGPT all look at the same place. When an assistant asks "who is this person", it gets the same brief profile the bot gets — assembled by the same code. Not five different builds, but one.

**Login only through Telegram.** There is no password at all. When Claude or ChatGPT wants to connect to the data, a six-digit code arrives in the owner's Telegram. If you do not see it — it is not your Telegram, and there will be no login. The code is one-time, lives for a few minutes, and five wrong attempts burn the attempt entirely.

**Only one door is visible from outside.** The layer lives on a separate node on the internal network. Only one entry point is exposed externally — the MCP interface. The data dashboard and neighboring services are not visible from outside and do not respond. A request without a valid token gets a refusal, not data.

**Every response carries a source and a date.** What the assistant receives from the system (not what it writes to you, but what the system hands to it) always begins with "Source: Health OS" and a date. If there is no data — it says so explicitly: "no data". Not an empty list, not silence, but words. The assistant sees this line and knows there is nothing to guess.

**Dietary rules with a label.** The system can output medical dietary rules from a curated knowledge base. If council rules have not yet been approved by the owner, they are still returned — but with an explicit label "not approved by owner". That is more honest than hiding them and letting the assistant invent something of its own.

**Read-only.** At this point, assistants only read. They write nothing, change nothing, delete nothing. Writing is a separate step that will require explicit consent from the owner.

**Personal data does not leave the system.** Name, date of birth, and identifiers are filtered out before the response leaves the system.

**Lab results are read by meaning.** The system identifies a substance in a lab result by its international code, not by whatever label the laboratory used for the row. "Glucose" from one laboratory and "glucose" from another are the same thing.

## What to Honestly Say About Its Limits

**Data goes to Anthropic and OpenAI.** When an assistant receives the system's response, that response ends up with them. What they do with it next — whether they store it or use it for training — is governed by your account settings with them, not by us. We do not control what a third-party model does with what it has received.

**The personal-data filter catches what we have listed.** The personal-word dictionary removes the name, date of birth, and known identifiers. It does not catch everything that could theoretically identify a person. This is a boundary, not a guarantee of anonymity.

**The assistant will repeat data skew as fact.** If a lab reference range is recorded on a different scale than the value itself, the assistant will not notice the discrepancy — it will reproduce what it received.

**Public surface verification is still in burn-in.** The nightly probe that checks that only one door is visible from outside and that it is closed without a token is running — and a live check on 07.10 was clean. But the probe has WARN status (burn-in): it is still accumulating history before we consider it mature.

**"Holds" and "verified everywhere" are different things.** All invariants of this layer currently hold: tests are green, mutations are red, live measurements are clean. But this does not mean they have been verified under all possible conditions — only that on known scenarios they do not break.

## Where This Is in the System

The layer's logic lives in two files: **`health_mcp.py`** — the entry point that accepts requests from assistants, and **`mcp_tools.py`** — the tools that retrieve data from memory and wrap it in a response with source and date.

The intent — why the layer exists, what decisions are built into it, and what boundaries it holds — is described in **`subsystem_intent.yaml`**. This is not code and not API documentation; it is an explanation of the intention behind which the layer was built: one memory, login through Telegram, only MCP exposed externally.
