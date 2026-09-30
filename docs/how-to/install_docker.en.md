<!-- translation-of: docs/how-to/install_docker.md sha256:7a6df940b4ad -->
**English** · [Русский](install_docker.md)

# How to install the system in Docker

A reference recipe for installing in containers with YOUR OWN image build — on a Mac with
Colima, on Linux, on any machine with Docker. Your own build is needed when the ready image is
not enough: documents in a language recognition does not know (the ready image knows English
and Russian), or your own code changes. The options the tutorial does not cover are here too:
your own secrets directory, a separate Docker context.

The usual first install uses the ready image, with no clone and no build:
[first install](../tutorials/first_install.en.md) ([Русский](../tutorials/first_install.md)).

## Before you start

- Docker Engine with the `compose` and `buildx` plugins. On a Mac: `brew install colima docker
  docker-compose docker-buildx`, then `colima start --profile health --cpu 2 --memory 4
  --disk 20 --activate=false`. Colima remembers the sizes from the first start. `--activate=false`
  keeps the current Docker context where it is: otherwise other projects on this Mac that do
  not name a context would start talking to this virtual machine. Then run this page's
  commands as `docker --context colima-health …`. To bring the profile back after a reboot,
  step 1 puts an agent in `build/docker/host/com.larry.health.colima.plist`: copy it to
  `~/Library/LaunchAgents` and run `launchctl bootstrap gui/$(id -u) <plist>`.
- A clone of the repository and Python 3.9 or newer on the build machine (only the installer needs it).
- The person's time zone (IANA, e.g. `Europe/Berlin`): the morning brief follows it.
- A secrets directory (mode 700) — as in the [first install](../tutorials/first_install.en.md) ([Русский](../tutorials/first_install.md)):
  - `telegram_chat_id` and `telegram_token` — without them the bot will not start (the other services will);
  - `anthropic_key` — without it reports are not written.

## 1. Render the schedule and the image ignore list

From the root of the clone:

```bash
cd health_scripts
python3 scripts/install.py --docker --tz Europe/Berlin
```

Summary line: `cron 25, service 3, env 1, host 4, none 3 (всего 36)`. `build/docker/` gets
`compose.yaml` and `.env`; the repository root gets `.dockerignore`. All generated — do not edit.

## 2. Build the image

Still from the root of the clone:

```bash
docker buildx build --load -f docker/Dockerfile -t health-os:local .
```

The first build takes minutes (longer on arm64: one library compiles from source); a repeat build takes
seconds, from cache. The `health-os:local` tag is overwritten on every build. The build verifies the
scheduler checksum and package compatibility (`pip check`); if it fails, stop here.

Document recognition languages are set at build time, `eng rus` by default. The codes are
three-letter, as in the Tesseract recognition engine: `deu` German, `ell` Greek, `fra` French
([full list](https://tesseract-ocr.github.io/tessdoc/Data-Files-in-different-versions.html)):

```bash
docker buildx build --load -f docker/Dockerfile -t health-os:local \
  --build-arg OCR_LANGS="eng rus deu" .
```

Build with the same `--build-arg` on every update, or the new image comes out without your
language. Updating your own build: `git pull`, then steps 1–3 again; the database and secrets
stay in the volumes and the secrets directory.

## 3. Start

Still from the root of the clone — copy the rendered files into the run directory and go there. **All
further commands run from the run directory.**

```bash
mkdir -p ~/health-docker && cp build/docker/compose.yaml build/docker/.env ~/health-docker/
cd ~/health-docker
HEALTH_SECRETS_HOST_DIR=/path/to/secrets docker compose up -d
docker compose ps
```

Expect four services: `bot`, `dashboard`, `lab-intake`, `cron`, with `Up …` in the STATUS column
(`restarting` for `bot` — see below). Services start after `cron` has created the database; for the
first seconds `cron` may show `health: starting`.
The dashboard is at `http://127.0.0.1:8001/` — **only** on this machine: the port is published on
loopback on purpose, the dashboard has no password.

## 4. Check the system is alive

```bash
docker compose exec cron python3 -c "import daemon_liveness as d; print(d.find_down_daemons())"
```

`[]` a couple of minutes after start — every long-running service is beating. "пульса нет ни
разу" (no pulse ever) for `bot` — the bot did not start. Service logs go to files, not to
`docker compose logs`:

```bash
docker compose exec cron sh -c 'tail -n 20 /app/logs/bot_err.log'
```

Most often it says `telegram_chat_id` or `telegram_token` is missing from the secrets directory.

## What is normal on first start

- "пороги лаб-трендов не посеяны: нет снимка EFLM" and "лаб-пороги safety_net не посеяны" (reference
  snapshots not seeded) — the snapshots are not in the image for licence reasons; the system runs on its fallback.
- "Нет действующего Google Calendar токена" (no Google Calendar token) — calendar sync is optional in a
  container; without it the calendar is simply not pulled.
- In the first days' nightly check — "no data" and "не судимо в контейнере" (not judgeable in a container):
  the install is empty, and some checks are about git and private documents, which are not in the image.

## If something goes wrong

- **`cron` keeps restarting or never becomes healthy** — `docker compose logs cron`: the output of the
  scheduler and the installer itself (job logs are in `/app/logs`, as above). Usually a wrong `--tz`.
- **You need the dashboard on your phone** — do not publish the port on all interfaces. Use
  `tailscale serve --https=8443 http://127.0.0.1:8001` on the host: private network only, with a
  certificate.
- **Reminders on the phone** are optional: put `caldav.json` (`{"url", "username", "password"}`)
  into the secrets directory; without it, tasks are visible in the bot. The phone fetches tasks on its
  own schedule ("Fetch New Data"), not instantly.
