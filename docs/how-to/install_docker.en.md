<!-- translation-of: docs/how-to/install_docker.md sha256:0fd67204fef3 -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](install_docker.md)

# How to Set Up the System in Docker

A reference installation recipe using containers with a CUSTOM image build — on a Mac with Colima, on Linux,
on any machine with Docker. A custom build is needed when a ready-made image isn't enough: documents in a
language not covered by recognition (the ready-made image knows English and Russian), or your own code changes.
Also covered here are options not in the tutorial: a custom secrets directory, a separate Docker context.

The usual first installation — from a ready-made image, without cloning and building:
[first installation](../tutorials/first_install.md) ([English](../tutorials/first_install.en.md)).

## Before You Begin

- Docker Engine with the `compose` and `buildx` plugins. On a Mac: `brew install colima docker
  docker-compose docker-buildx`, then `colima start --profile health --cpu 2 --memory 4
  --disk 20 --activate=false`. Colima remembers the sizes from the first start. `--activate=false`
  leaves the current Docker context in place: otherwise other projects on this Mac that do not
  specify a context explicitly will start routing into this virtual machine. Commands on this page
  should then be written as `docker --context colima-health …`. For the profile to come up automatically
  after a reboot, step 1 places an agent in `build/docker/host/com.larry.health.colima.plist`:
  copy it to `~/Library/LaunchAgents` and run `launchctl bootstrap gui/$(id -u) <plist>`.
- A clone of the repository and Python 3.9 or newer on the machine where you build (only the installer needs it).
- The person's time zone (IANA, e.g. `Europe/Berlin`): the morning summary is delivered according to it.
- A secrets directory (permissions 700) — as in the [first installation](../tutorials/first_install.md) ([English](../tutorials/first_install.en.md)):
  - `telegram_chat_id` and `telegram_token` — without them the bot will not start (the other services will start);
  - `anthropic_key` (or `openai_key` / `gemini_key` for a different model provider — [recipe](llm_provider.md)) — without a provider key, reports are not written.

## 1. Build the Schedule and Image Exclusion List

From the root of the clone:

```bash
cd health_scripts
python3 scripts/install.py --docker --tz Europe/Berlin
```

Summary line: `cron 25, service 3, env 1, host 5, none 3 (total 37)`. `compose.yaml` and `.env`
will appear in `build/docker/`, and `.dockerignore` in the root. All of this is generated; do not edit by hand.

## 2. Build the Image

From the same place, the root of the clone:

```bash
docker buildx build --load -f docker/Dockerfile -t health-os:local .
```

The first build takes minutes (longer on arm64: one library is compiled from source); subsequent builds —
seconds, from cache. The tag `health-os:local` is overwritten with each build. The build verifies the
scheduler checksum and package compatibility (`pip check`); if it fails — do not proceed.

Document recognition languages are set at build time, defaulting to `eng rus`. Codes are three-letter,
as used by the Tesseract recognition engine: `deu` German, `ell` Greek, `fra` French
([full list](https://tesseract-ocr.github.io/tessdoc/Data-Files-in-different-versions.html)):

```bash
docker buildx build --load -f docker/Dockerfile -t health-os:local \
  --build-arg OCR_LANGS="eng rus deu" .
```

With every update, build with the same `--build-arg`, otherwise the new image will be built without
your language. Updating a custom build: `git pull`, then repeat steps 1–3; the database and secrets
remain in volumes and in the secrets directory.

## 3. Start

Still from the root of the clone — copy the built files to the run directory and navigate there. **All further
commands are run from the run directory.**

```bash
mkdir -p ~/health-docker && cp build/docker/compose.yaml build/docker/.env ~/health-docker/
cd ~/health-docker
HEALTH_SECRETS_HOST_DIR=/path/to/secrets docker compose up -d
docker compose ps
```

Five services are expected: `bot`, `dashboard`, `lab-intake`, `cron`, `ingest`, with `Up …` in the STATUS
column (`restarting` for `bot` — see below). Services start after `cron` has created the database; for the
first few seconds `cron` may show `health: starting`.
The dashboard is at `http://127.0.0.1:8001/`, **only** on this machine: the port is published on
loopback intentionally, the dashboard has no password.

## 4. Verify the System Is Alive

```bash
docker compose exec cron python3 -c "import daemon_liveness as d; print(d.find_down_daemons())"
```

`[]` a couple of minutes after start — all persistent services are checked in. The message "no heartbeat
ever" for `bot` means the bot did not start. Service logs are written to files, not to `docker compose logs`:

```bash
docker compose exec cron sh -c 'tail -n 20 /app/logs/bot_err.log'
```

Most commonly the logs show that `telegram_chat_id` or `telegram_token` were not found in the secrets directory.

## What Is Normal to See on First Start

- "lab trend thresholds not seeded: no EFLM snapshot" and "lab safety_net thresholds not seeded" — reference
  snapshots are not included in the image due to licensing; the system runs on the fallback.
- "No active Google Calendar token" — calendar sync in the container is optional;
  without it the calendar simply is not pulled in.
- In the nightly check during the first few days — "no data" and "not evaluated in container": the installation
  is empty, and the subject of some checks (git, private documents) is not included in the image.

## If Something Goes Wrong

- **`cron` is restarting or not becoming healthy** — `docker compose logs cron`: output from the scheduler
  itself and the installer (task logs — in `/app/logs`, as above). Most commonly an incorrect `--tz`.
- **Need access to the dashboard from a phone** — do not publish the port on all interfaces. Use
  `tailscale serve --https=8443 http://127.0.0.1:8001` on the host: access only from your
  private network, with a certificate.
- **Reminders to phone** — optional service: place `caldav.json`
  (`{"url", "username", "password"}`) in the secrets directory; without it tasks are visible in the bot. The phone
  fetches tasks on its own schedule ("Data fetch"), not instantly.
