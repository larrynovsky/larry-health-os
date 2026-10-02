<!-- translation-of: docs/how-to/llm_provider.md sha256:7316bbbd1243 -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](llm_provider.md)

# How to Set Up the System with Another Model Provider's Key

The [first install](../tutorials/first_install.md) tutorial sets up the system with an Anthropic key — that is
the only path where everything works. This recipe is for those who have an OpenAI,
Gemini or DeepSeek key and no Anthropic key.

## First, See What Will Work

Only the part of the system whose model has been admitted by that provider works. The table shows
[what works with which provider](../reference/llm_providers.md). A role marked 'no' or 'not
tested' fails loudly (error `ModelNotAdmitted`), rather than responding with an untested
model. Example: if the sonnet role is marked 'no', lab result recognition from photos does not work
(it reads each page with two different models), while chat and check-in may work.
DeepSeek example (admission of 2026-10-02): on a lab-form photo `deepseek-v4-pro` returned numbers
that are not on the form, and the opus role failed — with a DeepSeek key, numbers from lab forms
do not enter the lab canon: for a sent document the bot says lab reading has not passed the checks,
and the document is processed automatically once reading becomes available (for example, with an
Anthropic key). Chat, check-in and treatment parsing from text work; the bot will describe a photo
in chat, but those numbers do not go into the canon.

## Installation

Instead of `bash install.sh` from the tutorial:

```bash
bash install.sh --provider openai
```

The script will ask for the OpenAI key (input is hidden), verify it against `api.openai.com` using the models list
(this is free), and store it in `secrets/openai_key`. For Gemini — `--provider gemini`, the key
is verified against `generativelanguage.googleapis.com` and stored in `secrets/gemini_key`.
For DeepSeek — `--provider deepseek`: get a key at `platform.deepseek.com` → API keys
and top up the balance; the script verifies it against `api.deepseek.com` and stores it in `secrets/deepseek_key`.

Without prompts (for example, on a server):

```bash
LLM_KEY=… HEALTH_TZ=Europe/Berlin TELEGRAM_TOKEN=… TELEGRAM_CHAT_ID=… \
  bash install.sh --provider gemini --non-interactive
```

At the end the script will list the admitted roles and remind you that everything else does not work.

## Switching Provider Later

Run the install again with a different `--provider`: the `HEALTH_LLM_PROVIDER` line in `.env`
will change, containers will be recreated, and the database and keys will remain. Running again without
`--provider` does not change the selection.

## Important Things to Know

- All texts and documents the system sends to the model (lab results, discharge summaries, chat) go to
  the chosen provider. Its data processing terms are your decision.
- Model charges go against your key with the provider. The script cannot check your balance: top it up yourself.
- How the system selects a model and what happens when a provider disables it —
  [explanation](../explanation/model_choice.md).
