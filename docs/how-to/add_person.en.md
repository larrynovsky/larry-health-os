<!-- translation-of: docs/how-to/add_person.md sha256:83084782ca09 -->
**English** · [Русский](add_person.md)

# How to add a person

One installation can look after several people, for example a family. Each person has their own
database, their own secrets, their own Telegram bot and their own background services. The code is
shared. This recipe adds a second (third…) person on a main machine that is already running.

Why it is built this way and where the isolation boundary lies: [multitenancy](../explanation/multitenancy.en.md)
([Русский](../explanation/multitenancy.md)).

## Before you start

- The main installation is already running ([first installation](../tutorials/first_install.en.md),
  [Русский](../tutorials/first_install.md)).
- Choose the person's name: lowercase Latin letters, digits, `_` (for example, `anna`). It becomes
  part of the paths: `~/health_anna`, `~/.health_secrets_anna`.
- Find out their time zone (IANA, for example `Europe/Berlin`): the morning brief arrives by it.
- Choose a free dashboard port: `8001` is taken by the main installation, `8002` by the first added
  person, and so on.

## 1. Installer dry run

```bash
cd ~/health_scripts
python3 scripts/install.py --tenant anna --tz Europe/Berlin --dashboard-port 8003
```

The installer prints what it will create and writes nothing.

## 2. Installation

```bash
python3 scripts/install.py --tenant anna --tz Europe/Berlin --dashboard-port 8003 --apply --launchd
```

This creates `~/health_anna/data/health.db` (an empty database, permissions 600), the secrets
directory `~/.health_secrets_anna` (permissions 700) and service plists in `build/launchd/anna/`.
The installer names the services this person does not get, and why.

## 3. The person's secrets

Put one file per secret into `~/.health_secrets_anna/`, with the value as its only line:

| File | What it is | Required |
|---|---|---|
| `telegram_token` | token of this person's SEPARATE bot (created with @BotFather) | yes |
| `telegram_chat_id` | id of the person's chat with their bot | yes |
| `oura_token` | personal Oura token | if they have the ring |
| `google_calendar_account`, `google_calendar_token.json` | sign-in to their calendar | if a calendar is needed |
| `hae_ingest_token`, `location_ingest_token` | sign-in from their phone | if they send data from a phone |

The model key (`anthropic_key`) and the mail settings are shared by the whole installation, so they
stay with the main one only. The full list and whose each secret is: `SECRET_SCOPE` in
`secrets_paths.py`.

Without their own secrets directory, the person's process refuses to start instead of taking the
main installation's secrets. This is by design.

## 4. Require an explicit person

The installer sets the person's service paths, but does not enable `HEALTH_MULTITENANT`.
Without this flag, a process on the primary machine with no `HEALTH_DATA_DIR` uses the main
installation's data directory. Enable the guard for every person, including the main one:

- For manual commands, run the following in your shell and add it to your shell's startup file:

```bash
export HEALTH_MULTITENANT=1
```

Pass `HEALTH_DATA_DIR` and `HEALTH_SECRETS_DIR` for the intended person with each command;
do not give all commands one person's paths as a global default.

- For launchd, add this entry inside the `EnvironmentVariables` dictionary of each service
  plist you use, for both the main installation and added people:

```xml
<key>HEALTH_MULTITENANT</key>
<string>1</string>
```

Check that each plist also has the intended person's explicit `HEALTH_DATA_DIR` (an absolute
path to `health` or `health_anna`, not its `data/` subdirectory) and secrets directory.
A shell `export` alone does not configure launchd services. Apply the entry to generated
plists before copying them; repeat after every installer run with `--launchd`, which rewrites
generated plists. Unload already loaded services with `launchctl bootout`, then copy and
bootstrap their updated plists. Any nonempty flag value enables the guard, including `0`.

## 5. Loading the services

```bash
cp build/launchd/anna/*.plist ~/Library/LaunchAgents/
for p in build/launchd/anna/*.plist; do
  launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/$(basename "$p")
done
launchctl list | grep '\.anna$'
```

Service labels end in `.anna` and do not clash with the main installation's services.

## 6. Getting acquainted

The person sends `/start` to their bot. The first question is the language (Russian or English),
then a short introduction. After it, the first morning brief arrives.

## 7. Checking

- `launchctl list | grep '\.anna$'`: the services are loaded, and the bot has a PID.
- In the morning, the nightly integrity check also runs over the person's data. If it finds
  something, the message goes to the **operator** (the owner of the main installation), not to the
  person: only the names of the checks, with no values from their data. The verdict is in
  `~/health_anna/logs/`.
- The sensor for "one person's trace in another person's database" checks everyone in the installation.

## Which services the person gets

The list is `templates/launchd/tenant_services.yaml`. The person gets every service marked `ready`:
the bot, the dashboard, the calendar, Oura, document intake, literature, constitutions,
questionnaires, the consilium, longitudinal analysis, PGS scores, reminders and the nightly check.

They do not get services marked `excluded`, because they do not need them by design:

- triage of the nightly check's findings: findings about the person's data go to the operator;
- the old document intake from the main installation's iCloud folder: the person's documents are
  taken in by `lab-intake`;
- a one-off experiment that has already run.

The mark `blocked: <reason>` means the service has acquired a file shared with another person, and a
copy would overwrite someone else's result. The installer does not set such a service up until this
is fixed.

## A person who was already added by hand

If a person's plists were once written by hand, the installer replaces them. Repeat step 2 with the
same name, port and time zone: the directories and the database stay as they are ("exists, not
touching"), and the plists are built again. Then unload the person's old plists
(`launchctl bootout`), repeat the guard configuration in step 4 and load the new plists as in step 5.

## Limits

- The machine's files are not separated between people: one person's bot can read a file whose
  path it was sent, even if the file belongs to someone else. Isolation rests on data and secrets,
  not on the file system (people on one machine trust each other).
- The shared weekly digest goes out only when every person's bot has agreed to it. If one person's
  bot is down, the digest waits for everyone.
