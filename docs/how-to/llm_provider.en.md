<!-- translation-of: docs/how-to/llm_provider.md sha256:9fac08be0b2a -->
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
