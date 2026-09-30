<!-- translation-of: docs/tutorials/first_install.md sha256:df76fd6d6503 -->
**English** · [Русский](first_install.md)

# First install of Larry Health OS

This tutorial brings the system up from scratch. By the end you will have running services,
your own Telegram bot and a first conversation with it. No medical data is needed to install:
at the end the bot asks for documents, and you can send them right away or later.

The system lives in Docker — in "containers". A container is a sealed box that already holds
the right Python, the libraries and text recognition. The box is built in advance and is the
same on any machine: you install only Docker itself and download two settings files. You do
not need to clone the repository or build anything yourself.

The bot first offers a choice of Russian or English and then uses that language for its own
interface texts. Texts written by the model are still in Russian for now.

What you need:

- a Mac (Apple Silicon or Intel, macOS 13+) with [Homebrew](https://brew.sh), or a Linux
  machine with Docker Engine, or Windows 11 through WSL2 — that path is not verified yet, see
  the "Windows through WSL2" section below;
- about 4 GB of free RAM and 20 GB of disk;
- a Telegram account and an Anthropic API key (about €5–15 a month per person);
- about 20 minutes, a few of them for the first image download.

The machine must run all the time: the system sends the morning report, reminders and night
checks by itself, on a schedule. A sleeping or switched-off computer sends nothing. On a Mac,
turn sleep off (the screen may go dark, the machine may not):

```bash
sudo pmset -a sleep 0
```

A laptop must also keep its lid open.

## 1. Install Docker

On a Mac, Docker runs inside a small Colima virtual machine. If you have Docker Desktop, quit
it: the `docker` command from Homebrew and the one from Docker Desktop get in each other's way.
Install Colima and Docker:

```bash
brew install colima docker docker-compose
colima start --profile health --cpu 2 --memory 4 --disk 20
```

Docker needs to be shown where Homebrew put its `compose` plugin:

```bash
mkdir -p ~/.docker/cli-plugins
ln -sfn "$(brew --prefix)/opt/docker-compose/bin/docker-compose" ~/.docker/cli-plugins/docker-compose
```

The first Colima start takes a minute or two. Colima remembers these sizes; `--profile health`
gives the virtual machine its own name so it does not mix with others if you already use
Docker. Check that Docker answers:

```bash
docker info --format '{{.ServerVersion}}'
```

The answer is a version number, for example `27.4.1`. The plugin is in place too:
`docker compose version` prints its version rather than `unknown command`. `docker context ls`
shows an asterisk next to `colima-health` — commands go to this virtual machine. The error
`Cannot connect to the Docker daemon` means Colima did not start: repeat
`colima start --profile health`.

On Linux, install Docker Engine with the `compose` plugin following
[Docker's instructions](https://docs.docker.com/engine/install/); Colima is not needed there.

## 2. Download the launch files

Create the directory the system will run from and download two files of the latest version
into it: `compose.yaml` (which containers to run and from which image) and `.env` (their
settings).

<!-- tutorial:run -->
```bash
mkdir -p ~/health-docker && cd ~/health-docker
curl -fsSL -o compose.yaml https://github.com/larrynovsky/larry-health-os/releases/latest/download/compose.yaml
curl -fsSL -o .env https://github.com/larrynovsky/larry-health-os/releases/latest/download/health.env
grep 'image:' compose.yaml | sort -u
```

The last line shows the image name with its version number, for example
`image: ghcr.io/larrynovsky/larry-health-os:v0.1.0`. **All further commands run from
`~/health-docker`.** Do not edit the files by hand, except for the time zone in the next step.

## 3. Set your time zone

The system is three permanent services (the bot, the dashboard and the parser of files you
send) plus a scheduler that runs about twenty-five tasks: the morning report, data imports,
night checks. Task times are local, so the system needs your time zone.

Find it: on a Mac `readlink /etc/localtime | sed 's#.*/zoneinfo/##'`, on Linux
`timedatectl show -p Timezone --value`. Put the answer, such as `Europe/Berlin`, into the first
line and run all four:

<!-- tutorial:run -->
```bash
cd ~/health-docker
MY_TZ=Europe/Berlin
sed -i.bak -e "s#^TZ=.*#TZ=$MY_TZ#" -e "s#^HEALTH_TZ=.*#HEALTH_TZ=$MY_TZ#" .env
grep 'TZ=' .env
```

`grep` must show two lines, `HEALTH_TZ=` and `TZ=`, both with your time zone.

## 4. Create your Telegram bot and keys

The bot is your only interface to the system. Without `telegram_token` and `telegram_chat_id`
it does not start; without `anthropic_key` no reports are written.

1. In Telegram open `@BotFather` and send `/newbot`. It asks for a name (anything) and an
   address that must end in `bot` (for example `my_health_bot`). It replies with a link
   `t.me/<address>` — that is how you will find your bot — and a token like `123456:ABC…`.
2. Find your numeric id: message `@userinfobot`, it replies with `Id: 12345678`.
3. Get an Anthropic API key at console.anthropic.com: top up the balance (Billing) — without
   money on the account the key is issued but no reports get written — then create a key
   (API Keys) and copy it at once: it is not shown a second time.

Put the keys into the `secrets` subdirectory of the launch directory:

<!-- tutorial:run -->
```bash
mkdir -p ~/health-docker/secrets && chmod 700 ~/health-docker/secrets
cd ~/health-docker/secrets
printf '%s' 'TOKEN_FROM_BOTFATHER' > telegram_token
printf '%s' 'YOUR_ID' > telegram_chat_id
printf '%s' 'ANTHROPIC_KEY' > anthropic_key
chmod 600 telegram_token telegram_chat_id anthropic_key
```

Replace only the text inside the quotes and keep the quotes. The id is digits only, for example
`printf '%s' '12345678' > telegram_chat_id`. The containers see this directory read-only. The
bot answers only your id: anyone can write to it, but it listens only to you.

## 5. Start it

<!-- tutorial:run -->
```bash
cd ~/health-docker
docker compose up -d
docker compose ps
```

The first start downloads the image — a few minutes, with download lines running on screen.
Expect four rows — `bot`, `cron`, `dashboard`, `lab-intake` — with `Up …` in the STATUS column.
For the first seconds `cron` may show `health: starting`: it is creating an empty database and
the others wait for it. The database and logs live in Docker volumes: updating the image does
not touch them.

## 6. Check that everything is alive

A couple of minutes after the start:

<!-- tutorial:run -->
```bash
cd ~/health-docker
docker compose exec -T cron python3 -c "import daemon_liveness as d; print(d.find_down_daemons())"
```

`[]` means every permanent service reports in. Right after the start you may see "no pulse
ever" — wait a minute and repeat. The same line for `bot` after five minutes means the bot did
not come up; the reason is in its log:

```bash
docker compose exec -T cron sh -c 'tail -n 20 /app/logs/bot_err.log'
```

Most often it says `InvalidToken` — a wrong token in step 4. Fix the file with the same
`printf` line from step 4 (in `~/health-docker/secrets`), then restart the bot from
`~/health-docker`: `docker compose restart bot`.

The check shows `[]` but the bot stays silent — almost certainly a wrong id: the bot does not
answer strangers. Compare `cat ~/health-docker/secrets/telegram_chat_id` with the answer from
`@userinfobot`.

The dashboard is a set of browser pages showing what the system knows about you: medical
record, labs, hypotheses, tasks. Open `http://127.0.0.1:8001` on this machine. Until you have
met the bot the pages are almost empty — that is normal. The dashboard has no password, so it is
open on this machine only.

## 7. Turn on autostart

Containers come up by themselves whenever Docker runs. On a Mac, Colima must also come up after
a reboot. There is an agent for that — download it and turn it on:

```bash
curl -fsSL https://github.com/larrynovsky/larry-health-os/releases/latest/download/com.larry.health.colima.plist \
  | sed "s#__HOME__#$HOME#g" > ~/Library/LaunchAgents/com.larry.health.colima.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.larry.health.colima.plist
```

Check: `launchctl print gui/$(id -u)/com.larry.health.colima | head -3` prints the agent's
description. If `bootstrap` answers `5: Input/output error` on a repeat, the agent is already
loaded — that is fine.

The agent starts when you log in to your account. After a power cut the Mac switches on, but
the system comes up only once someone logs in. On Linux Docker Engine starts by itself if it is
enabled: `sudo systemctl enable docker`.

## 8. Meet the bot

Write to your bot in Telegram:

```
/start
```

The bot offers a short introduction, about ten minutes. The first question is
"Язык / Language?": choose "Русский" or "English". Next it asks how to address you. Answer with
text or buttons. After each answer the bot shows what it recorded — that is how you see it
understood you. If it got something wrong, go through the introduction again with `/about`.

At the end the bot shows a summary of what it now knows and a couple of minutes later sends the
first morning report. Health problems you mentioned arrive as separate cards with
"✅ Применить" (apply) and "✖ Отклонить" (reject) buttons: only an applied one enters your
problem list. No report within ten minutes — look at the bot log (the command from step 6):
most often it is the Anthropic key or an empty balance.

Under the card the bot asks for documents: doctors' letters, discharge summaries and labs for
the last three years (PDF or photo), and raw genome data if you took a genetic test. Send them
as files right in the chat. The bot answers each file with what it will do with it. Diagnoses
and medications from letters arrive as cards — only what you confirm enters the record. The
genome loads in the background and the bot reports when it is done. Raw data from 23andMe,
AncestryDNA, MyHeritage, FTDNA, tellmeGen and LivingDNA fit, as does a full genome in VCF
(parsing takes hours).

Document recognition knows English and Russian. Documents in another language need your own
image build — see [How to install the system in Docker](../how-to/install_docker.en.md) ([Русский](../how-to/install_docker.md)).

Telegram does not hand a file over 20 MB to a bot — send a Google Drive link to it instead:
[How to send the bot a large file](../how-to/send_large_file.en.md) ([Русский](../how-to/send_large_file.md)).

The bot is silent on `/start` — see step 6.

## What is normal to see in the first days

- In the logs — "пороги лаб-трендов не посеяны: нет снимка EFLM" and "лаб-пороги safety_net не
  посеяны" (lab thresholds not seeded). The EFLM and CTCAE reference sets are not in the image
  for licensing reasons; the system runs on its built-in fallback.
- "Нет действующего Google Calendar токена" — the calendar is not connected; it is optional.
- In the night check — "данных нет" (no data) and "не судимо в контейнере" (not judged in a
  container): the install is empty, and some checks concern development, not running the system.

## How to update

When a new version is out, download its `compose.yaml` — it carries the new image name — and
bring the containers up again:

```bash
cd ~/health-docker
curl -fsSL -o compose.yaml https://github.com/larrynovsky/larry-health-os/releases/latest/download/compose.yaml
docker compose up -d
```

`up -d` downloads the new image and recreates only the containers whose image changed; the
database, the keys and your `.env` with the time zone stay. If the release notes on GitHub say
the settings changed, download `.env` too (step 2) and set the time zone again (step 3).

## Windows through WSL2 (not verified)

**Nobody has walked this path to the end yet.** The image is built for Linux, and WSL2 is a
real Linux inside Windows, so it should work — but it has not been verified. If you got through
it, or got stuck, open an issue in the repository: that is how the path becomes verified.

1. Open PowerShell as administrator and install Ubuntu: `wsl --install -d Ubuntu-24.04`.
   Reboot; on the first Ubuntu start choose a user name and password.
2. In the Ubuntu window install Docker Engine and allow yourself to use it:

   ```bash
   curl -fsSL https://get.docker.com | sudo sh
   sudo usermod -aG docker $USER
   ```

   Close the Ubuntu window and open it again. `docker info --format '{{.ServerVersion}}'` must
   print a version number. Docker Desktop for Windows is not needed.
3. Then follow steps 2–6 and 8 of this tutorial, all commands in the Ubuntu window. The time
   zone for step 3 is `timedatectl show -p Timezone --value`. The dashboard opens in the
   Windows browser at the same address, `http://127.0.0.1:8001`.

An unverified spot to know in advance: Windows stops WSL when no window is open in it — and the
whole system with it. Keep the Ubuntu window open and turn off Windows sleep (Settings → System
→ Power). The tutorial does not give a reliable autostart for Windows yet.

## If something goes wrong

- **`bot` is `Restarting`** — step 6: the bot log, most often the token or the id.
- **After a Mac reboot the bot is `Restarting` again, with `Timed out` or `name resolution` in
  the log** — the virtual machine came up before the network and cannot resolve site names.
  Stop Colima, give Docker a DNS address and start again:

  ```bash
  colima stop --profile health
  sed -i '' 's/^docker: {}$/docker: {"dns": ["1.1.1.1"]}/' ~/.colima/health/colima.yaml
  grep '^docker:' ~/.colima/health/colima.yaml
  colima start --profile health
  ```

  `grep` must show a line with `1.1.1.1`. If it shows `docker: {}` unchanged or something
  else, write `"dns": ["1.1.1.1"]` into the `docker:` section of that file by hand.

- **`cron` restarts or never becomes healthy** — `docker compose logs cron`. Most often a wrong
  time zone: fix it (step 3) and repeat `docker compose up -d`.
- **`docker compose up` answers `denied` or `not found` for the image** — check the image name
  (step 2) and the network; the image downloads from `ghcr.io`.

Details and harder cases are in [How to install the system in Docker](../how-to/install_docker.en.md) ([Русский](../how-to/install_docker.md)).

## What this tutorial does not do

- It does not build the image from source. That is needed for your own recognition languages
  or your own code changes — [How to install the system in Docker](../how-to/install_docker.en.md) ([Русский](../how-to/install_docker.md)).
- It does not connect the dashboard to the network. The phone needs it for Apple Health data —
  how to open it on a private network but not to the internet is in
  [How to connect iPhone Health](../how-to/connect_apple_health.en.md) ([Русский](../how-to/connect_apple_health.md)).
- It does not add a second person to the same install.
- It does not set up a second machine for development —
  [How to add a second machine for development](../how-to/two_machine_setup.en.md) ([Русский](../how-to/two_machine_setup.md)).
- It does not run the tests: they are for those who change the code, and run outside the
  container — [How to run tests](../how-to/run_tests.en.md) ([Русский](../how-to/run_tests.md)).
