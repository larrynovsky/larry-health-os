<!-- translation-of: docs/how-to/add_bot_consumer.md sha256:cfa0188db808 -->
**English** · [Русский](add_bot_consumer.md)

# How to safely add a consumer of the owner's chat_id

> **Document type:** How-to (Diátaxis) — a recipe for a specific task.
> API reference: [docs/reference/owner_chat_id_resolver.md](../reference/owner_chat_id_resolver.md).
> Rationale: [docs/explanation/owner_chatid_lazy.md](../explanation/owner_chatid_lazy.md).

Task: a new module/handler needs to send a message to the owner or check
that an update came from the owner.

## The rule in one line

Always use `from bot.filters import owner_chat_id` and call `owner_chat_id()` at the point
of use. **Never** cache the value in a local constant or read the secret file
yourself — that creates a second path (split-brain), and the guard
`test_no_second_owner_chat_id_constant` will fail.

## If the consumer is a regular CommandHandler / MessageHandler

You do not need to check anything manually: pass the `owner` filter during registration.

```python
def register(app, owner_filter):
    app.add_handler(CommandHandler("mycmd", cmd_mycmd, filters=owner_filter))
```

The guard `test_all_command_handlers_have_owner_filter` requires an owner filter on every
`add_handler`. Without it, the test fails.

## If the consumer is a CallbackQueryHandler (filters are not accepted)

Add the owner check manually, **fail-closed**: if resolution is unavailable, block everyone.

```python
async def my_cb(update, context):
    from bot.filters import owner_chat_id
    try:
        _owner = owner_chat_id()
    except Exception:      # secret unavailable → block everyone (NOT fail-open)
        _owner = None
    if _owner is None or update.effective_chat is None \
            or update.effective_chat.id != _owner:
        return             # silent exit, without revealing the callback_data structure
    ...
```

Antipattern (do not do this — it is bug F2):

```python
if OWNER_CHAT_ID is not None and id != OWNER_CHAT_ID:   # None → short-circuit → fail-OPEN
    return
```

## If the consumer needs to send a message to the owner

```python
from bot.filters import owner_chat_id
await bot.send_message(chat_id=owner_chat_id(), text=...)
```

If the None path is legitimate (for example, “no owner — exit silently instead of crashing”), use
`get_chat_id()`, which returns `None` instead of `raise`. But this is forbidden for an auth check
(None-auth = fail-open).

## Check before committing

```bash
# On Studio (canonical):
/opt/homebrew/bin/python3.11 -m pytest tests/consistency/test_owner_chat_id_resolver.py \
    tests/unit/test_uc_i_02_sec.py -q
```

Both pass → the consumer was added correctly.
