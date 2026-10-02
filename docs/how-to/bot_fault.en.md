<!-- translation-of: docs/how-to/bot_fault.md sha256:d9925b15cb7e -->
**English** · [Русский](bot_fault.md)

# The bot replied “I couldn't answer: something failed inside the system…” with a code

> **Genre (Diátaxis): how-to.** For someone who installed the system themselves with the
> [installation tutorial](../tutorials/first_install.md) ([English](../tutorials/first_install.en.md)) and got this message from the bot.

The bot answers this way when a command or button failed and your installation has no other
repairer: you installed it, so you repair it. The bot keeps working meanwhile, there is no rush. The
code in the message has six characters, for example `a1b2c3`. It leads to the fault record on your machine.

## 1. Find the fault record

From the installation directory (`~/health-docker` in the tutorial), put in your code:

```bash
cd ~/health-docker
docker compose exec -T cron sh -c 'grep a1b2c3 /app/logs/faults.jsonl'
```

The answer is one line: when (`ts`), where (`where`) and what happened (`text`). Empty means the
code is mistyped or the journal was already trimmed (it keeps the last two thousand records).

Details with the traceback are in the bot log, close in time:

```bash
docker compose exec -T cron sh -c 'tail -n 60 /app/logs/bot_err.log'
```

## 2. Try the common causes

| In the record | What it is | What to do |
|---|---|---|
| `Read-only file system` … `.health_secrets` | this bot version writes to the keys directory, which is read-only | update to the latest release — the “How to update” section of the tutorial |
| `authentication_error`, `credit balance` | the Anthropic key is wrong or the account has no balance | tutorial step 4: check the key and the balance, then `docker compose restart bot` |
| `InvalidToken` | wrong Telegram token | tutorial step 6 |

## 3. Still broken — open an issue

Open an issue on [GitHub](https://github.com/larrynovsky/larry-health-os/issues/new) and attach:

- the fault code and its line from `faults.jsonl`;
- the version: output of `grep 'image:' ~/health-docker/compose.yaml | sort -u`;
- 20–60 lines of `bot_err.log` around the time of the fault.

Look the text over before sending: it must not contain keys or tokens. The fault journal holds no
medical data by design, but a traceback may contain the text of your message to the bot — you can
erase it.

## Why the bot doesn't put details into the chat

Error text can contain internal addresses and pieces of keys, and Telegram messages often get
forwarded and attached to issues. So only the code goes to the chat, and the details stay on your
machine.
