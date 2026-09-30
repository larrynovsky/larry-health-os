<!-- translation-of: docs/explanation/owner_chatid_lazy.md sha256:943f577f0493 -->
**English** · [Русский](owner_chatid_lazy.md)

# Lazy owner resolution: why the bot learns chat_id after import

> **Document type:** Explanation (Diátaxis) — why it works this way and which alternatives were considered.
> API reference: [docs/reference/owner_chat_id_resolver.md](../reference/owner_chat_id_resolver.md).
> How to add a consumer: [docs/how-to/add_bot_consumer.md](../how-to/add_bot_consumer.md).
> Broader context on delivery: [telegram_bot.md](telegram_bot.md).

## The problem that started it all

The bot has a single “owner address” — a Telegram `chat_id` stored in a tenant
secret file. Previously, `bot/filters.py` read this file **when the module was imported** and,
if the file was missing, failed the import (a top-level `raise`). The intent was correct —
fail-closed: better not to start at all than accidentally make the first person who
messages the bot its owner. But this solution had a nonobvious side effect.

Almost the entire bot chain imports `bot/filters` — `jobs/scheduled`,
`handlers/*`, `telegram_bot`. That meant any test importing this chain
required the secret during **collection** (`pytest tests/`). As long as the secret was present,
no one noticed. But on July 15, 2026, one leaking test pointed `HEALTH_SECRETS_DIR`
at an empty directory — and the whole collection failed with three collection errors. The morning gate turned red
falsely, even though the code was sound. The symptom was treated (a stub for one test), but the **class**
remained: fail-closed at import makes collection fragile in the face of a leaked environment variable.

## The solution and why this one

Resolution became **lazy**: `owner_chat_id()` reads the file on first
access rather than at import, and remembers the result (memoization). Importing `bot/filters` now leaves
the secret untouched — test collection no longer depends on what someone has put in the environment.

Fail-closed did not disappear — it **moved from import to two real boundaries**:

- **Startup.** `bot/main.main()` calls `assert_owner_configured()` first. No
  secret — the bot does not start. The same “a random first visitor will not become the owner” principle,
  now applied when the process starts rather than when the module loads.
- **Read.** `owner_chat_id()` **raises an exception rather than
  returning None** when the secret is missing. This is not pedantry — see F2 below.

Here it is worth using the term from consistency theory: the owner's `chat_id` is
a value with a **single source** (single-resolver). If a copy of this
value lived somewhere else — in a module-local constant or a variable “nearby” — we would
have a classic **split-brain**: two copies, identical for now, eventually diverge,
and the owner's medical record goes to the wrong recipient (for example, a partner). So there is one resolver —
`bot.filters.owner_chat_id()` — and the guard `test_no_second_owner_chat_id_constant`
prohibits creating a second path to the value.

## F2: why “raise, not None” is about safety, not style

Along the way, a real bug surfaced that the old architecture had hidden. In
`assessment_bot_handlers.cb_router`, the owner check had been inserted manually (it uses
`CallbackQueryHandler`, which does not accept a filter). The old code looked like this:

```
try:    from telegram_bot import OWNER_CHAT_ID
except: OWNER_CHAT_ID = None
if OWNER_CHAT_ID is not None and id != OWNER_CHAT_ID:
    return   # block the stranger
```

Let us examine what happens when resolution is unavailable (`OWNER_CHAT_ID = None`). The condition
`None is not None and ...` short-circuits to `False` → `return` is **not** executed →
the handler keeps working for **anyone**. This is **fail-OPEN**: when the secret disappears,
the door does not lock; it swings open. Meanwhile, the neighboring `handlers/callbacks.py` blocked
everyone on None (fail-closed) — the system behaved inconsistently depending on
which handler caught the update.

That is exactly why `owner_chat_id()` **never** returns None: the check
`id != owner_chat_id()` then cannot degenerate into “let everyone through.” And `cb_router` itself
now does this: resolution unavailable → treat it as “owner unknown” → **block everyone**.
An unavailable recipient is no reason to open the door.

## An alternative deliberately rejected

The change touched the core and five consumers importing the name `OWNER_CHAT_ID`.
It was tempting to do this in several commits: the core first, then the consumers. But
deployment here is a `post-commit push` to Studio, meaning **every commit reaches production
immediately**. An intermediate commit (core without consumers) would mean `ImportError` in
four modules → the bot that delivers medical data would not start on canonical until the next commit.

The “separate commits + PEP 562 shim” alternative (keeping the name `OWNER_CHAT_ID` lazily available
through `__getattr__`) would not help: any consumer still importing the name at the
top level would trigger resolution during its own import — so it **would not fix
the stated collection fragility**, and the shim itself would be the very “second path to the value”
that the principle forbids. Hence **one atomic commit**: core + all consumers +
F2 fix + guards together. Not a second of broken production, and no workaround.

## What to understand honestly about the limits

- Memoization means a **snapshot**: a `chat_id` change (through `--setup`) takes effect
  after the bot restarts. For the owner's `chat_id` — a value that does not change at runtime —
  this is a deliberate cost, not a regression (no file read on every auth check).
- “The startup gate passed” is confirmed by the line `owner resolved: <id>` in the bot log at
  startup — an explicit receipt that resolution happened and which owner it resolved to.
- A live check of cross-tenant delivery isolation (the owner gets their record, the partner gets theirs)
  requires a human eye; the code guarantees only that the tenants have different resolvers.
