<!-- translation-of: docs/tutorials/first_install.md sha256:823daf5ced1e -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](first_install.md)

# First-Time Setup of Larry Health OS

This tutorial brings the system up from scratch. By the end you will have running services, your own Telegram bot, and a first acquaintance with it. Medical data are not required for setup: at the end the bot will ask for documents, and you can send them right away or later, whenever you like.

The system lives in Docker — in "containers". A container is a sealed box that already contains the required Python, libraries, and text recognition. The box is built in advance and is identical on any machine: you only install Docker itself and download two configuration files. There is no need to clone the repository or build anything yourself.

The bot first offers a choice of language, after which it uses that language for its own interface texts. Model texts remain in Russian for now.

What you will need:

- Mac (Apple Silicon or Intel, macOS 13+) with [Homebrew](https://brew.sh), or a Linux machine with Docker Engine, or Windows 11 (Windows 10 version 22H2 — most likely also works) — Windows has its own procedure, described in the [Windows](#windows) section at the end of the tutorial: start there;
- about 4 GB of free RAM and 20 GB of disk space;
- a Telegram account and an Anthropic API key (roughly 5–15 € per month per person);
- about 20 minutes, a few of which are for the first image download.

The machine must run continuously: the morning report, reminders, and nightly checks are done by the system on its own schedule. A sleeping or shut-down computer will not send anything. On a Mac, disable sleep (the screen may turn off; the machine must not):

```bash
sudo pmset -a sleep 0
```

On a laptop, the lid must also remain open.

<a id="quick-path"></a>

## Quick path: script

Steps 2–7 below can be handled by a script: it first checks everything that is needed (Docker, memory, disk, GitHub access, free port), reports what is missing and how to fix it, then asks for the time zone and the keys from step 4 and starts the system. It does not install Docker (step 1).

```bash
curl -fsSL -o install.sh https://github.com/larrynovsky/larry-health-os/releases/latest/download/install.sh
bash install.sh --check    # только проверка, ничего не меняет
bash install.sh            # установка; повторный запуск — обновление
```

The script asks which model provider you use — for this tutorial press Enter (Anthropic).
Keys are entered without echo and are validated with Telegram and Anthropic before being written. Then go to step 8. The tutorial below explains what the script does and is needed if it stopped on something unclear.

## 1. Install Docker

The system requires any Docker with the `compose` plugin. On a Mac, Docker always runs inside a small virtual machine started by one of these programs: Docker Desktop, OrbStack, or Colima. **Docker Desktop or OrbStack already installed** — keep it: verify that `docker info --format '{{.ServerVersion}}'` and `docker compose version` print version numbers, and move on to step 2 (the tutorial was verified on Colima; not on those). **Nothing installed** — install Colima: free, no registration required. Do not run both programs on the same Mac at the same time — their `docker` commands interfere with each other.

```bash
brew install colima docker docker-compose
colima start --profile health --cpu 2 --memory 4 --disk 20
```

Docker needs to be shown where Homebrew placed the `compose` plugin:

```bash
mkdir -p ~/.docker/cli-plugins
ln -sfn "$(brew --prefix)/opt/docker-compose/bin/docker-compose" ~/.docker/cli-plugins/docker-compose
```

The first Colima start takes a minute or two. Colima remembers these sizes; `--profile health` gives the virtual machine its own name so it does not get mixed up with others if you already have Docker. Verify that Docker responds:

```bash
docker info --format '{{.ServerVersion}}'
```

The response is a version number, for example `27.4.1`. And the plugin is in place: `docker compose version` prints its version rather than `unknown command`. The command `docker context ls` shows a star next to `colima-health` — this means commands are directed to that virtual machine. The error `Cannot connect to the Docker daemon` means Colima did not start: repeat `colima start --profile health`.

On Linux, install Docker Engine with the `compose` plugin following the [Docker documentation](https://docs.docker.com/engine/install/); Colima is not needed there.

On Windows this step is different — see the [Windows](#windows) section, steps A–C; then return here to step 2.

## 2. Download the startup files

Create a directory from which the system will be launched and download into it two files from the latest release: `compose.yaml` (which containers to run and from which image) and `.env` (their configuration).

<!-- tutorial:run -->
```bash
mkdir -p ~/health-docker && cd ~/health-docker
curl -fsSL -o compose.yaml https://github.com/larrynovsky/larry-health-os/releases/latest/download/compose.yaml
curl -fsSL -o .env https://github.com/larrynovsky/larry-health-os/releases/latest/download/health.env
grep 'image:' compose.yaml | sort -u
```

The last line will show the image name with the version number, for example `image: ghcr.io/larrynovsky/larry-health-os:v0.1.0`. **All subsequent commands are run from `~/health-docker`.** Do not edit the files by hand, except for the time zone in the next step.

## 3. Set the time zone

The system consists of three permanent services (bot, dashboard, and file parser) and a scheduler that runs approximately twenty-five tasks on a schedule: morning report, data import, nightly checks. Task times are local, so the system needs your time zone.

Find it: on a Mac `readlink /etc/localtime | sed 's#.*/zoneinfo/##'`, on Linux `timedatectl show -p Timezone --value`. Substitute the result (in the form `Europe/Berlin`) into the first line and run all four:

<!-- tutorial:run -->
```bash
cd ~/health-docker
MY_TZ=Europe/Berlin
sed -i.bak -e "s#^TZ=.*#TZ=$MY_TZ#" -e "s#^HEALTH_TZ=.*#HEALTH_TZ=$MY_TZ#" .env
grep 'TZ=' .env
```

`grep` should show two lines, `HEALTH_TZ=` and `TZ=`, both with your time zone.

## 4. Create your Telegram bot and keys

The bot is your only interface to the system. Without `telegram_token` and `telegram_chat_id` it will not start; without `anthropic_key` reports will not be written.

1. In Telegram, open `@BotFather` and send `/newbot`. It will ask for a name (any) and a username that must end in `bot` (for example `my_health_bot`). The response will include a link `t.me/<username>` — by which you will later find your bot — and a token of the form `123456:ABC…`.
2. Find your numeric id: message `@userinfobot`; it will reply with a line `Id: 12345678`.
3. Get the Anthropic API key at console.anthropic.com: top up your balance (Billing section) — without funds the key is issued but reports are not written — then create a key (API Keys) and copy it immediately: it will not be shown again.

Place the keys in a `secrets` subdirectory of the launch directory:

<!-- tutorial:run -->
```bash
mkdir -p ~/health-docker/secrets && chmod 700 ~/health-docker/secrets
cd ~/health-docker/secrets
printf '%s' 'ТОКЕН_ОТ_BOTFATHER' > telegram_token
printf '%s' 'ВАШ_ID' > telegram_chat_id
printf '%s' 'КЛЮЧ_ANTHROPIC' > anthropic_key
chmod 600 telegram_token telegram_chat_id anthropic_key
```

Replace only the text inside the quotes; leave the quotes themselves. For the id — digits only, for example `printf '%s' '12345678' > telegram_chat_id`. Containers see this directory as read-only. The bot responds only to your id: anyone can message it, but it only listens to you.

Inside the containers the system runs as a user with id 1000. On a Mac this does not matter; on Linux, if your id is different (`id -u` prints something other than `1000`), the container will not read the keys. Transfer ownership to it — the command does nothing if this is not needed:

<!-- tutorial:run -->
```bash
if [ "$(uname)" = Linux ] && [ "$(id -u)" != 1000 ]; then sudo chown -R 1000:1000 ~/health-docker/secrets; fi
```

After running it, keys on such a machine must be changed using `sudo`, for example `printf '%s' '12345678' | sudo tee ~/health-docker/secrets/telegram_chat_id >/dev/null`.

## 5. Start the system

<!-- tutorial:run -->
```bash
cd ~/health-docker
docker compose up -d
docker compose ps
```

The first start downloads the image — a few minutes, with download lines scrolling on screen. Five lines are expected — `bot`, `cron`, `dashboard`, `ingest`, `lab-intake` — with STATUS `Up …`. For the first few seconds `cron` may show `health: starting`: it is creating an empty database while the others wait for it. The database and logs live in Docker volumes: updating the image does not affect them.

## 6. Verify that everything is alive

A couple of minutes after start:

<!-- tutorial:run -->
```bash
cd ~/health-docker
docker compose exec -T cron python3 -c "import daemon_liveness as d; print(d.find_down_daemons())"
```

`[]` — all permanent services are reporting. Right after start you may see "no heartbeat yet" — wait a minute and repeat. The same result for `bot` after five minutes means the bot did not come up; the reason is in its log:

```bash
docker compose exec -T cron sh -c 'tail -n 20 /app/logs/bot_err.log'
```

Most commonly it is `InvalidToken` — an incorrect token in step 4. Fix the file with the same `printf` line from step 4 (in `~/health-docker/secrets`), then from `~/health-docker` restart the bot: `docker compose restart bot`.

The check shows `[]` but the bot is silent — almost certainly the wrong id: the bot does not respond to others. Compare `cat ~/health-docker/secrets/telegram_chat_id` with the response from `@userinfobot`.

The dashboard — browser pages where you can see what the system knows about you: medical record, test results, hypotheses, tasks. Open `http://127.0.0.1:8001` on this machine. Until you have completed the bot introduction, the pages are mostly empty — this is normal. The dashboard has no password and is therefore accessible only from this machine.

## 7. Enable autostart

The containers start automatically when Docker is running. On a Mac with Docker Desktop, enable Settings → General → "Start Docker Desktop when you sign in" (in OrbStack — "Start at login"), and this step is done. With Colima you also need it to come up after a reboot. There is an agent for this — download it and enable it:

```bash
curl -fsSL https://github.com/larrynovsky/larry-health-os/releases/latest/download/com.larry.health.colima.plist \
  | sed "s#__HOME__#$HOME#g" > ~/Library/LaunchAgents/com.larry.health.colima.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.larry.health.colima.plist
```

Verify: `launchctl print gui/$(id -u)/com.larry.health.colima | head -3` prints the agent description. If `bootstrap` replied `5: Input/output error` on a repeated run, the agent is already loaded — this is normal.

The agent starts when you log into your user account. After a power outage the Mac will turn on, but the system will only come up when someone logs in. On Linux, Docker Engine starts automatically if enabled: `sudo systemctl enable docker`. On Windows — step D of the [Windows](#windows) section.

## 8. Introduce yourself to the bot

Message your bot in Telegram:

```
/start
```

The bot will offer a short introduction, about ten minutes. The first question is "Language?": choose your preferred language. Next it will ask what to call you. Reply with text or buttons. After each answer the bot shows what was recorded — so you can see that it understood correctly. If it understood wrong, redo the introduction with `/about`.

At the end the bot will show a summary of what it now knows, and a couple of minutes later will send the first morning report. Health problems you mentioned will arrive as separate cards with "✅ Apply" and "✖ Reject" buttons: only an applied item goes into your problem list. If the report has not arrived within ten minutes, check the bot log (command from step 6): most commonly it is the Anthropic key or an empty balance.

Below the card the bot will ask for documents: doctor's reports, discharge summaries, and test results from the past three years (PDF or photos), and raw genome data if you have had a genetic test. Send them as files directly in the chat. For each file the bot will say what it will do with it. Diagnoses and medications from the reports will arrive as cards — only what you confirm will go into the medical record. The genome loads in the background; the bot will write separately when finished. Raw data from 23andMe, AncestryDNA, MyHeritage, FTDNA, tellmeGen, and LivingDNA are supported, as well as a full genome in VCF (processing takes hours).

Document recognition knows English and Russian. Documents in another language require a custom image build — see ["How to install the system in Docker"](../how-to/install_docker.md) ([English](../how-to/install_docker.en.md)).

Telegram does not pass files larger than 20 MB to the bot — in that case, send a Google Drive link: ["How to send the bot a large file"](../how-to/send_large_file.md) ([English](../how-to/send_large_file.en.md)).

Bot is silent on `/start` — see step 6.

## What is normal to see in the first few days

- In the logs — "lab trend thresholds not seeded: no EFLM snapshot" and "lab safety_net thresholds not seeded". The EFLM and CTCAE reference databases are not included in the image due to licensing; the system operates on the built-in fallback.
- "No active Google Calendar token" — the calendar is not connected; this is optional.
- In the nightly check — "no data" and "not judged in container": the installation is empty, and some checks relate to development rather than to the running system.

## How to update

Once a day, after the morning report, the bot will tell you about a new version — one message per
version, with a link to the notes and the command. The short way to update: `cd ~/health-docker && bash install.sh`.
Or by hand: when a new version is released, download its `compose.yaml` — which contains the new image name — and bring the containers back up:

```bash
cd ~/health-docker
curl -fsSL -o compose.yaml https://github.com/larrynovsky/larry-health-os/releases/latest/download/compose.yaml
docker compose up -d
```

`up -d` downloads the new image and recreates only the containers whose image has changed; the database, keys, and your `.env` with the time zone are preserved. If the GitHub release notes say that settings have changed, also download `.env` (step 2) and set the time zone again (step 3).

## Windows

**No one has gone through this path yet.** The image is built for Linux, and WSL2 is a real Linux inside Windows, so everything should work; the steps below were assembled from Microsoft and Docker documentation and have not been executed on a live Windows machine. If you get stuck — note the letter of the step, the command, and the last lines on screen, and open an [issue](https://github.com/larrynovsky/larry-health-os/issues) (or contact whoever gave you the link): that is how this path will become verified.

Commands here come in two kinds: **PowerShell** — a Windows window (Start menu → type `PowerShell`), and **Ubuntu** — a Linux window that will appear after step A. Which is which is stated before each command.

### A. Install Ubuntu

PowerShell **as administrator** (right-click the icon → "Run as administrator"):

```powershell
wsl --install -d Ubuntu-24.04
```

Restart the computer. After logging in, an Ubuntu window will open and ask you to create a username and password (the password is not visible while typing — that is by design). If the window did not open, find "Ubuntu 24.04" in the Start menu. An error about virtualization means it is disabled in the computer's BIOS settings; how to enable it depends on the manufacturer (search for "enable virtualization" and your computer model).

### B. Configure Ubuntu and WSL

In the **Ubuntu** window, enable the init system (systemd) — without it Docker will not start automatically:

```bash
grep -qs '^systemd=true' /etc/wsl.conf || printf '\n[boot]\nsystemd=true\n' | sudo tee -a /etc/wsl.conf
```

Ubuntu will ask for your password from step A. Then — to prevent Windows from shutting down Ubuntu, and with it the entire system, a few seconds after you close the last window — in a **PowerShell** window (ordinary):

```powershell
Set-Content -Path "$env:USERPROFILE\.wslconfig" -Value "[general]`ninstanceIdleTimeout=-1`n[wsl2]`nvmIdleTimeout=-1"
wsl --shutdown
```

The first line overwrites the `.wslconfig` file; if you have configured WSL yourself before, append these lines to your existing file instead. The second line stops Ubuntu so that both settings take effect. Open the Ubuntu window again and verify:

```bash
ps -p 1 -o comm=
```

The answer should be `systemd`. Any other answer means the first command in this step did not work: repeat it and `wsl --shutdown`.

### C. Install Docker inside Ubuntu

In the **Ubuntu** window:

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
```

The installer will print `WSL DETECTED: We recommend using Docker Desktop` and wait 20 seconds — do not press anything; it will continue on its own. Docker Desktop is not needed. Close the Ubuntu window and open it again (this activates the second line), then verify:

```bash
docker info --format '{{.ServerVersion}}'
```

The response is a version number, for example `27.4.1`. The response `permission denied` — in PowerShell run `wsl --shutdown` and open Ubuntu again.

Now — **steps 2–6 of this tutorial, all commands in the Ubuntu window** (or the [quick path](#quick-path): the script will also check the WSL settings from step B). Time zone for step 3: `readlink /etc/localtime | sed 's#.*/zoneinfo/##'` (Ubuntu takes it from Windows). The dashboard from step 6 opens in an ordinary Windows browser at `http://127.0.0.1:8001`. Then return here to step D.

### D. Autostart after reboot

So that the system starts automatically when you log into Windows, add a command to the startup folder that wakes Ubuntu. In **PowerShell**:

```powershell
Set-Content -Path "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\health-os.cmd" -Value "wsl.exe -d Ubuntu-24.04 -e true"
```

Verification: restart the computer, log in, **without opening Ubuntu** wait a minute, and open `http://127.0.0.1:8001`. If it opens — autostart is working. On login a black window will flash briefly — that is it.

Disable sleep: Settings → System → Power → "Put the computer to sleep" — "Never" (on a laptop — at least when plugged in). A sleeping Windows machine will not send anything. As on a Mac, the system only comes up after logging into a user account.

Next — **step 8**: the bot introduction.

## If something went wrong

- **`bot` in state `Restarting`** — step 6: bot log, most commonly the token or id.
- **The bot replied “I couldn't answer: something failed inside the system…” with a code** — one command or button failed; the code finds it in the log: [“The bot replied ‘something broke’”](../how-to/bot_fault.md) ([English](../how-to/bot_fault.en.md)).
- **After a Mac reboot the bot is `Restarting` again, and the log shows `Timed out` or `name resolution`** — the virtual machine came up before the network and cannot resolve domain names. Stop Colima, set a DNS address for Docker, and start again:

  ```bash
  colima stop --profile health
  sed -i '' 's/^docker: {}$/docker: {"dns": ["1.1.1.1"]}/' ~/.colima/health/colima.yaml
  grep '^docker:' ~/.colima/health/colima.yaml
  colima start --profile health
  ```

  `grep` should show a line with `1.1.1.1`. If it shows `docker: {}` unchanged or something else — manually add `"dns": ["1.1.1.1"]` to the `docker:` section of that file.

- **`cron` is restarting or not becoming healthy** — `docker compose logs cron`. Most commonly an incorrect time zone: fix it (step 3) and repeat `docker compose up -d`.
- **`docker compose up` responds with `denied` or `not found` for the image** — check the image name (step 2) and network connectivity; the image is downloaded from `ghcr.io`.

Details and more complex cases — in ["How to install the system in Docker"](../how-to/install_docker.md) ([English](../how-to/install_docker.en.md)).

## What this tutorial does not do

- Does not build the image from source. This is needed for custom recognition languages or custom code changes — ["How to install the system in Docker"](../how-to/install_docker.md) ([English](../how-to/install_docker.en.md)).
- Does not expose the dashboard to the network. A phone needs it for Apple Health data — how to open it on a private network without exposing it to the internet is described in ["How to connect iPhone Health"](../how-to/connect_apple_health.md) ([English](../how-to/connect_apple_health.en.md)).
- Does not add a second person to the same installation.
- Does not set up a second machine for development — ["How to add a second machine for development"](../how-to/two_machine_setup.md) ([English](../how-to/two_machine_setup.en.md)).
- Does not run tests: those are for people modifying the code and run outside the container — ["How to run tests"](../how-to/run_tests.md) ([English](../how-to/run_tests.en.md)).
