<!-- translation-of: docs/how-to/pilot_switch.md sha256:0fe1c25e4f52 -->
**English** · [Русский](pilot_switch.md)

# How to move a tenant from the native install into a container and back

A recipe for the primary machine where the tenant runs natively (launchd) and Docker runs in its own
Colima profile. The tenant's database write moves as a whole: native and container never write at once.
Why it is built this way — the docker-install thread plan (private part of the repository), stage 11.
Installing Docker and the profile — [install in Docker](install_docker.md).

Every command runs on the primary machine, from `~/health_scripts`. Set the per-tenant variables once:

```sh
export PATH=/opt/homebrew/bin:$PATH
T=~/health                      # tenant data root (for a second person — ~/health_<name>)
S=~/.health_secrets             # tenant secrets directory (for a second person — ~/.health_secrets_<name>)
CTX=colima-health               # Docker context of your own Colima profile — not the shared one
PROJECT=health                  # compose project name; for a rehearsal — health-rehearsal
TZ_HOST=$(readlink /etc/localtime | sed 's#.*/zoneinfo/##')
PY=/opt/homebrew/bin/python3.11
```

## Before you start

- The nightly check is green on the commit now on the machine (`git log -1`).
- The image of that commit exists: `docker --context $CTX images health-os`. If not — `bash scripts/deploy_container.sh`
  (build and start only; before step 3 it would bring up an empty database — so do not run it before the switch).
- The owner has decided to switch. Steps 2–4 run back to back, with the owner present.
- The Colima profile has DNS set: in `~/.colima/<profile>/colima.yaml` — `network.dns` and `docker: {"dns": [...]}`
  (for example, your router's address and `1.1.1.1`). Without it, after a machine reboot the containers could not resolve
  network addresses (measured 30.09 12:53; fixed by another session with `colima restart`). Check:
  `docker --context $CTX exec <container> python3 -c "import socket; print(socket.gethostbyname('api.telegram.org'))"`.
- Before the switch, run the morning pipeline inside the tenant's container on a copy of the data — `run_checks.sh --scheduled`
  and the full test suite — and sort every red one: "container environment" or "developer workstation" (the
  `host_only` marker). Lesson C-106: the first morning of the owner's pilot gave 0 tests, 5 red monitor checks and noise in the bot log.

## 1. Prepare the files (nothing live is touched)

```sh
$PY scripts/install.py --docker --tz "$TZ_HOST"
$PY scripts/install.py --owner-override --tz "$TZ_HOST"
$PY scripts/install.py --pilot-split          # which services are unloaded, which stay
```

## 2. Stop the tenant's native services

```sh
for l in $($PY scripts/install.py --pilot-split | awk '$1=="bootout"{print $2}'); do
  launchctl bootout gui/$(id -u)/$l 2>/dev/null; launchctl disable gui/$(id -u)/$l; done
launchctl list | grep -F -f <($PY scripts/install.py --pilot-split | awk '$1=="bootout"{print $2}')   # empty
launchctl list | grep -c '\.partner$'                                                               # as before
```

The plists are not deleted: rollback needs them. `disable` is required: without it `bootout` lasts only until a reboot and launchd loads the services again (finish-prep note, 30.09); check with `launchctl print-disabled gui/$(id -u)`. A service needs time for a clean shutdown — the list does not empty at once (measured 30.09: the bot and the watcher take longer than 3 s); check by repeating, not with one command.

## 3. Move the database

```sh
mkdir -p ~/health_switch && $PY -c "import sqlite3,sys; s=sqlite3.connect(sys.argv[1]); d=sqlite3.connect(sys.argv[2]); s.backup(d); d.close(); print('snapshot ready')" \
  "$T/data/health.db" ~/health_switch/health.db
(cd build/docker && docker --context $CTX compose -p $PROJECT create)
docker --context $CTX run --rm -v ${PROJECT}_health-home:/home/health -v ~/health_switch:/in:ro health-os:local \
  sh -c 'mkdir -p /home/health/health/data && cp /in/health.db /home/health/health/data/health.db && sha256sum /home/health/health/data/health.db'
shasum -a 256 ~/health_switch/health.db          # the same hash
echo container > "$T/RUNTIME"                     # read by the deploy hook and the native backup
chmod 400 "$T/data/health.db" && chmod a-w "$T/data"
```

Mode `400`, not `444`: the monitor's permission repair (`security:db_perms`, any tenant on the host) treats anything readable by group or others as a violation and resets it to `600`, silently lifting the write ban (measured 30.09 12:52 after a reboot: the partner's monitor lifted it and `code-watcher` wrote seed rows into the frozen copy; `code-watcher` has been on the unload list since).

## 4. Start

```sh
(cd build/docker && docker --context $CTX compose -p $PROJECT up -d)
docker --context $CTX compose -p $PROJECT ps     # all Up; cron is healthy
```

## 5. Open the inputs to the tailnet only

```sh
TS=/Applications/Tailscale.app/Contents/MacOS/Tailscale
$TS serve --bg --tcp 8001 tcp://127.0.0.1:8001    # Apple Health intake and dashboard keep their address
$TS serve status
```

CalDAV (if the tenant uses Reminders): create `~/.health_caldav/config` and `users` following the stage 3
probe, with a new password and `users` at mode 600; `$TS serve --bg --https=5232 http://127.0.0.1:5232`;
put `caldav.json` ({"url", "username", "password"}) into `$S` (mode 600); move the open tasks:

```sh
docker --context $CTX compose -p $PROJECT exec cron python3 -c "import tasks_db, task_agent as a; t=tasks_db.get_open_tasks(limit=100000); n=a.create_reminders_for_tasks(t); want=sum(x.get('type') not in a.REMINDER_TYPES_EXCLUDED for x in t); print(n, want); assert n==want"
```

## 6. Turn on the shadow watchdog

```sh
cp build/docker/host/com.larry.health.pilot-shadow.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.larry.health.pilot-shadow.plist
$PY pilot_shadow.py; tail -3 ~/Library/Logs/health-shadow.log      # "matched"
```

The same watchdog also watches the frozen copy every hour: mode exactly 400, the database and journal files
unchanged since the first measurement (`~/health_shadow/frozen_copy.json`), no service from the unload list loaded.
Any of the three — a message to the owner.

## 6a. Check the backups

The tenant's database backup is made by the container job (03:00) into the host directory `~/container_backups/<tenant>` (700;
mounted over `<data>/backups`, created by `scripts/deploy_container.sh`) — not into the Colima VM disk image
where the database itself lives. After the first night: `ls -la ~/container_backups/health` — today's file is there,
`sqlite3 <file> 'PRAGMA integrity_check'` — `ok`.

## 7. First check

The tenant writes to the bot and sends one document; the count of events or lab rows grew (measure before and after):
`docker --context $CTX compose -p $PROJECT exec cron python3 -c "import health_db as d; print(tuple(d.get_conn().execute('select (select count(*) from events), (select count(*) from lab_results)').fetchone()))"`.

## Rollback

```sh
(cd build/docker && docker --context $CTX compose -p $PROJECT stop)
mkdir -p ~/health_rollback && docker --context $CTX run --rm -v ${PROJECT}_health-home:/home/health -v ~/health_rollback:/out health-os:local \
  python3 -c "import sqlite3; s=sqlite3.connect('/home/health/health/data/health.db'); d=sqlite3.connect('/out/health.db'); s.backup(d)"
launchctl bootout gui/$(id -u)/com.larry.health.pilot-shadow 2>/dev/null
$TS serve --tcp=8001 off
chmod u+w "$T/data" "$T/data/health.db" && mv "$T/data/health.db" "$T/data/health.db.frozen-$(date +%F)"
cp ~/health_rollback/health.db "$T/data/health.db" && rm -f "$T/RUNTIME"
rm -f "$S/caldav.json"                   # only if you put it there in step 5: Reminders go back to iCloud
for l in $($PY scripts/install.py --pilot-split | awk '$1=="bootout"{print $2}'); do
  launchctl enable gui/$(id -u)/$l; launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/$l.plist 2>/dev/null; done
```

If the old system tasks in iCloud were marked completed (the owner's decision of 30.09), clear those marks BEFORE loading `reminders-sync`: the native service would read them as done and close the tasks in the database.
Note: the owner's native `reminders-sync` did not read the mailbox once from 08.08 to 30.09 (osascript timeout
in all 422 runs), so after a rollback ticks on the iPhone may again fail to arrive; since 30.09 such a failure
is written to the fault journal, and the nightly monitor sees it.
`code-watcher` is on the unload list: it runs tests on the tenant's data (`$HOME/health`), and after the move
it was a second writer of the frozen copy (measured 30.09 12:52) — on rollback it is enabled with the rest.

Rollback check: `launchctl list | grep com.larry.health.bot` — loaded; the bot answers; do not delete the
container volume until the rollback is verified (`compose down` without `-v`).
