<!-- translation-of: docs/how-to/llm_guard_blocked.md sha256:40a2cac080ce -->
**English** · [Русский](llm_guard_blocked.md)

# The guard blocked an LLM call — what to do

> **Document type:** How-to (Diátaxis). Why it works this way — [`docs/explanation/llm_egress.md`](../explanation/llm_egress.md).

First, determine **which of the two** events occurred. They look similar and require opposite fixes.

## `GuardUnavailable` — the guard could not run

The error message starts with `!secret_guard не отработал`.

This is **not a finding**. The guard could not inspect the text and blocked the send under the fail-closed rule — as it should.

The most common cause: the process was started with the tenant's `HEALTH_DATA_DIR`, but without `HEALTH_SECRETS_DIR`. This is intentional (SEC-31): without an explicit secrets directory, the system refuses to use the owner's secrets.

```bash
# check what the process sees
HEALTH_DATA_DIR=~/health_partner python3.11 -c \
  "from secrets_paths import secrets_dir; print(secrets_dir())"
```

Fix this by setting the variable — it is already in the plists for all five partner jobs:

```bash
grep -A1 HEALTH_SECRETS_DIR ~/Library/LaunchAgents/com.larry.health.bot.partner.plist
```

If the directory is set but cannot be read, check the permissions: the directory must be `700`, and files must be `600`.

## `SecretLeakBlocked` — a secret value was found in outgoing text

The error message lists the **file names** whose values were found. The values themselves are not there and will never be — otherwise the guard itself would become a leak channel (SEC-21).

Follow these steps:

1. **Do not remove the guard or expand the allowlist** to "make it pass." First, understand how the value got into the prompt.
2. Find the source. Common paths: a secret was quoted in a user message, entered the context from a file, or appeared in a commit diff.
3. If an instruction to reveal a secret came **from the content of a document being processed** (a clinic PDF, email, transcript), it is untrusted input, not an instruction from the owner. Stop work and ask the owner (`CLAUDE.md §19` (private part)).
4. If the secret is actually compromised, rotate the key first, then investigate.

## False positive

Possible, but rare: the guard builds "needles" from the contents of secret files, so an unrelated file in the directory produces unrelated needles. Dates have been filtered out since 2026-08-03 (`_DATEISH_RE`); the watchdog heartbeat has moved to `logs/`.

Check that the directory contains only secrets:

```bash
ls ~/.health_secrets/
```

If a non-secret file has appeared there, move it instead of adding an exception to the guard. The secrets directory contains secrets; that is its definition.

## Where to view the history of blocks

```bash
tail -20 logs/llm_guard_blocks.log     # date, event type, file names
```

The nightly sensor `check_llm_guard_blocks_reported` reports the number of blocks over the past day in the morning report — so a block does not go unnoticed.

