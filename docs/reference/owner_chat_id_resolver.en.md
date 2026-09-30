<!-- translation-of: docs/reference/owner_chat_id_resolver.md sha256:2eb71be1bfa6 -->
**English** · [Русский](owner_chat_id_resolver.md)

# Owner resolver: API and modes

> **Genre (Diátaxis): reference.** A terse map of “function → behavior → failure mode.”
> Rationale/alternatives: [docs/explanation/owner_chatid_lazy.md](../explanation/owner_chatid_lazy.md).
> How to add a consumer: [docs/how-to/add_bot_consumer.md](../how-to/add_bot_consumer.md).
> Module: `bot/filters.py`. Single secrets directory resolver: `secrets_paths.secrets_dir()`.

## Functions

| Function | Returns | When the secret is missing | Memoization | Where to call |
|---------|-----------|-------------------|------|-----------|
| `owner_chat_id() -> int` | owner's `chat_id` from `<secrets_dir>/telegram_chat_id` | `raise RuntimeError` (NEVER None) | yes (snapshot on first call) | at the point of use, not at import time |
| `assert_owner_configured() -> None` | `None` on success | `raise RuntimeError` | — (delegates to `owner_chat_id()`) | first thing in `bot/main.main()` (startup gate) |
| `_owner_filter() -> filters.BaseFilter` | `filters.Chat(chat_id=[owner_chat_id()])` | propagates `raise` | — | handler registration |
| `get_chat_id() -> int \| None` | `chat_id` or `None` if the file is missing | `None` (not raise) | no (reads the file every time) | delivery where the None path is legitimate |
| `get_token() -> str` | Telegram token from `<secrets_dir>/telegram_token` | `raise` (file not found) | no | `Application.builder` startup |
| `save_chat_id(cid: int) -> None` | writes the secret file | — | resets the snapshot only after a restart | `/start` handler |

## Invariants (guarded)

| Invariant | Meaning | Proving test |
|-----------|-----------|-------------------|
| import-safe | `import bot.filters` does NOT fail without the secret | `test_bot_filters_import_safe_without_chat_id` |
| fail-closed read/startup | `owner_chat_id()`/`assert_owner_configured()` without the secret `raise`, not None | `test_owner_chat_id_and_start_gate_fail_closed_without_secret` |
| single-resolver (anti-split-brain) | no production module holds a second `OWNER_CHAT_ID = ...` | `test_no_second_owner_chat_id_constant` |
| single directory resolver | `HEALTH_SECRETS_DIR` is resolved only in `secrets_paths.py` | `test_secrets_single_resolver` |
| F2 fail-closed | `cb_router` blocks everyone when resolution is unavailable | `test_assessment_router_*` (2 tests) |
| all handlers under owner | no `add_handler` without an owner filter / inline check | `test_all_command_handlers_have_owner_filter` |

## Receipts

- Startup: log `owner resolved: <id>` (`~/health_bot.log`) — resolution succeeded, the gate passed.
- Per-tenant: `HEALTH_SECRETS_DIR=~/.health_secrets_partner` → resolution returns the partner's chat_id,
  different from the owner's (proof of no split-brain at the directory level).

## Failure modes (summary)

- Missing `telegram_chat_id` file → `owner_chat_id()` and `assert_owner_configured()` → `RuntimeError`
  → `bot/main.main()` exits before `run_polling` (the bot does not start). This is the intended behavior.
- A tenant without `HEALTH_SECRETS_DIR` but with `HEALTH_DATA_DIR ≠ .../health` → `secrets_dir()` itself
  calls `raise` (fail-closed against cross-tenant leakage); see `secrets_paths.py`.
