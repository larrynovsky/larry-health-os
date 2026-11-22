<!-- translation-of: docs/how-to/llm_provider.md sha256:3c19883781dc -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](llm_provider.md)

# How to Set Up the System with Another Model Provider's Key

The [first install](../tutorials/first_install.md) tutorial sets up the system with an Anthropic key —
everything is tested on it. This recipe is for those who have an OpenAI or Gemini key and no
Anthropic key.

## First, See What Will Work

Only the part of the system whose model passed the checks with that provider works: the table shows
[what works with which provider](../reference/llm_providers.md). A function whose model did not
pass refuses in words rather than answering with an unchecked model. The checks are narrow:
reading lab forms, extracting treatment from text, and short text tasks. How well another
provider's model writes the morning brief or reasons in the consilium has not been compared with
Claude.

## Installation

Run `bash install.sh`, as in the tutorial. A new installation asks:

```
Which key will you use? Lab results, medical letters and your chats with the bot go to the chosen provider.
  1 — Anthropic (recommended: everything is tested on it)
  2 — OpenAI
  3 — Gemini (Google)
Number [1]:
```

Answer `2` or `3`. The script asks for that provider's key (input is hidden), checks it against
the models list (this is free) and stores it in `secrets/openai_key` or `secrets/gemini_key`. An
OpenAI key comes from platform.openai.com → API keys (top up the balance), a Gemini key from
aistudio.google.com → Get API key. At the end the script says that every function passed the checks, or lists in
words what does not.

Without questions (for example, on a server) the provider is named with a flag:

```bash
LLM_KEY=… HEALTH_TZ=Europe/Berlin TELEGRAM_TOKEN=… TELEGRAM_CHAT_ID=… \
  bash install.sh --provider gemini --non-interactive
```

DeepSeek is not offered for now: lab photos and the consilium do not work on it.

## Switching Provider Later

Running `bash install.sh` again is an update: it does not ask for the provider and does not
change the choice. To switch provider, run it with the flag, for example
`bash install.sh --provider openai`: the `HEALTH_LLM_PROVIDER` line in `.env` will change,
containers will be recreated, and the database and keys will remain.

## Important Things to Know

- All texts and documents the system sends to the model (lab results, discharge summaries, chat) go to
  the chosen provider. Its data processing terms are your decision.
- Model charges go against your key with the provider. The script cannot check your balance: top it up yourself.
- The list of admitted models ships with the system version. New checks, and a replacement for a model
  the provider has switched off, reach you after an update: the bot tells you about a new version,
  and updating is running `bash install.sh` again.
- How the system selects a model and what happens when a provider disables it —
  [explanation](../explanation/model_choice.md).

## Unset provider

A nonempty `HEALTH_LLM_PROVIDER` wins. Without it, the sole key file from
`methodology/llm_providers.json` decides. No key files, several key files
(including `anthropic_key` next to another key: since 2026-11-09 Anthropic has no
seniority), or an invalid explicit value block AI calls and produce a log plus ru/en bot instructions
at startup. Acknowledged instructions are suppressed until recovery; failed sends
are retried at the next startup.

Legacy `HEALTH_LLM_PROVIDER=anthropic` cannot be distinguished from a person's
choice. For another provider, correct `.env` (e.g. `HEALTH_LLM_PROVIDER=openai`)
and run `docker compose up -d`. The public template leaves an unselected value
empty; the owner override gives every service an explicit provider, falling back
to Anthropic only when the Compose environment / `.env` value is empty.

## Native/tenant deployment and rollout order

Native plists (`render_launchd`, `render_tenant`, the shadow agent) carry
`HEALTH_LLM_PROVIDER` only with an explicit `install.py --provider <name>` (also
with `--tenant`); without the flag the template is unchanged byte for byte and the
sole key file decides. `native_environment` in `templates/launchd/placement.yaml`
is the fallback for the owner Compose override only; plists do not use it. The
Colima agent gets no variable: it runs no Python.

The native partner's LLM key remains shared: `Path.home()/.health_secrets`,
not `HEALTH_SECRETS_DIR`. Under the same macOS account, owner and partner read
the same directory. Selection and `api_key()` use the same `_KEY_FILE.parent`.
Moving that directory would be a separate change to access and billing boundaries.

Rollout order (code inspected 2026-11-08):

1. Ship the compatibility resolver and new renderers first. Existing native
   jobs with Anthropic plus optional keys keep working, with warnings.
2. Owner container deployment rerenders `--docker` and `--owner-override`, then
   runs `compose up -d`. Every override service receives an explicit provider,
   including MCP, bug-inbox and sidecars.
3. Migrate native plists separately. `--launchd` and `--tenant` only write
   `build/launchd`; deploy hooks do not install those files. `kickstart` does not
   reread changed plist files. Back up existing plists and record loaded labels;
   add `EnvironmentVariables.HEALTH_LLM_PROVIDER=anthropic` while preserving local
   arguments, schedules and paths. Use rendered files as the configuration reference,
   not as permission to overwrite all local customizations.
4. Bootout/bootstrap only previously loaded jobs, then inspect their environment
   with `launchctl print gui/<uid>/<label>`. Do not load disabled owner jobs that
   moved into containers: that would create duplicate writers. Do not run the full
   `--apply` against the frozen native owner database just to render plists: it calls
   `init_db`. Renderer functions can be called separately without installation.
5. Only after verifying every installed AND loaded carrier, including partner
   processes, ship strict multi-key rejection. Done 2026-11-09: 55 plists (all but
   Colima, including `com.larry.healthbot.morningwake`, whose prefix a plain
   `com.larry.health.*` glob misses) carry `anthropic`, 25 loaded jobs were reloaded and checked with
   `launchctl print`, every Compose service was checked with `printenv`, and Studio
   ssh sessions get the value from `~/.zshenv`. Backups:
   `~/health_backups/backup-provider-<date>` on Studio.

The all-service rendering guard covers owner/tenant plists, compose and optional
services. An executable shell test verifies that container cron inherits OpenAI
rather than overwriting it with the native Anthropic setting. Rendering tests do
not establish that existing LaunchAgents have been migrated.
