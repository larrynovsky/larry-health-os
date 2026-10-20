<!-- translation-of: docs/how-to/bot_fault.md sha256:5ece2131e44c -->
**English** · [Русский](bot_fault.md)

# Something went wrong — how to tell the developer

> **Genre (Diátaxis): how-to.** For someone who installed the system themselves with the
> [installation tutorial](../tutorials/first_install.md) ([English](../tutorials/first_install.en.md)):
> the bot replied “I couldn't answer: something failed inside the system…” with a code, the morning
> report didn't arrive, or a button did nothing.

The bot keeps working meanwhile, there is no rush.

## 1. Send the bot /bug

The bot asks what happened — pick the closest button: “A report didn't arrive”, “The bot didn't
respond to a button or command”, “A lab result wasn't recognised” or “Something else”. Then it shows
exactly what will go to the developer and asks “Send it?”.

Only this goes: the version and system, the button you picked, the type and place of this week's
faults (for example `KeyError in labs_db.py`), what is connected (yes/no) and the names of the bot's
latest messages — without their text. Never sent: message texts, your data and lab results, keys.
Full list — [what goes into the report](../reference/bug_report_fields.md).

“Delivered, number R-…” means the developer has the report. “I can't reach the developer right now” —
the report is saved and the bot will send it itself. “Sending reports isn't set up in this build” —
you built the image yourself instead of downloading a release; see step 3.

## 2. If the bot doesn't answer at all

From the installation directory (`~/health-docker` in the tutorial):

```bash
cd ~/health-docker
docker compose run --rm cron python3 -m bug_outbox
```

The command shows the same report and asks whether to send it.

## 3. A GitHub issue instead of the bot

If you have a GitHub account and want to discuss the fault in the open, print the same report and
paste it into a [new issue](https://github.com/larrynovsky/larry-health-os/issues/new):

```bash
docker compose run --rm cron python3 -m bug_outbox --print
```

Don't paste lines from the `faults.jsonl` and `bot_err.log` journals into the issue: error text can
contain what you wrote to the bot or a lab value. The report from the command above contains neither.

If you have your own AI assistant with access to the installation (for example, Claude), ask it to
trace the fault in the code and write the issue: what you did, what you saw, where the cause is in the
code. Such an issue helps more than the short report. Read it yourself before posting and remove your
data, lab values and journal lines — a GitHub issue is visible to everyone.

## Common causes you can fix yourself

| The bot or the report says | What it is | What to do |
|---|---|---|
| `authentication_error`, `credit balance` among the faults | the model provider key is wrong or the account has no balance | tutorial step 4: check the key and the balance, then `docker compose restart bot` |
| the bot is silent, the journal has `InvalidToken` | wrong Telegram token | tutorial step 6 |
| `Read-only file system` | an old bot version | update — the “How to update” section of the tutorial |

## Why the bot doesn't put details into the chat

Error text can contain internal addresses, pieces of keys and your data, and Telegram messages often
get forwarded. So only a short code goes to the chat, and the developer gets a report assembled from
fields that cannot hold anything personal.
