<!-- translation-of: docs/how-to/rotate_logs.md sha256:ccd23b24214f -->
**English** · [Русский](rotate_logs.md)

# Log rotation

**Measurement on September 1, 2026:** no log had rotation on either machine —
`health_import_poll.log` was 204 MB and `health_bot.log` 71 MB on Studio; `code_watcher.log` was 46/31 MB
on Studio/MacBook. About 470 MB total, growing since May. Rotation configuration:
`launchd/health-logs.newsyslog.conf` (configuration for the owner's machine; public installations get a template).

**First production run, September 1 at 21:03:** Studio — 6 files, 366 MB → 11.7 MB of archives, all with
`inode_kept: true`; MacBook — 3 files, 81 MB. A field check that the writers were not orphaned:
one minute after truncation, `health_import_poll.log` was back to 7.6 KB and `health_bot.log` to 660 B,
meaning live processes with open file descriptors kept writing to THE SAME file.

## Why newsyslog instead of RotatingFileHandler

Logs are written in **two** ways, and you cannot fix the second from Python:

| Method | Writer | Example |
|---|---|---|
| `logging.FileHandler` | `bot/main`, `import_oura`, `import_apple_health`, `reminders_sync`, `vcf_import_pipeline` | `~/health_bot.log` |
| launchd redirection (`StandardOutPath` / `StandardErrorPath`) | all `.plist` files | `logs/bot_err.log`, `logs/dashboard.err.log` |

`RotatingFileHandler` would cover only the first row: launchd holds the file descriptors for
the second, so rotation from inside the process is impossible. `newsyslog` rotates the **file**, not the writer —
it covers both methods and requires no code changes.

## What is installed

**The mechanism is custom and runs without root:** `log_rotate.py` + the user agent
`launchd/com.larry.health.logrotate.plist` (every 30 minutes). Install it **without sudo**:

```sh
cp ~/health_scripts/launchd/com.larry.health.logrotate.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.larry.health.logrotate.plist
launchctl kickstart -k gui/$(id -u)/com.larry.health.logrotate     # run now
```

Installed by the agent on September 1, 2026, on **both machines** (Studio and MacBook — each has its own logs).
The owner's decision: “install it without me”; the standard `newsyslog` requires writing to `/etc/newsyslog.d/`
and runs as root, while `sudo` is blocked by the bridge policy (Desktop Commander's `blockedCommands`) —
the agent did not remove that protection for a single installation.

**The key mechanism is copytruncate, not renaming.** Two different writers write logs, and
both hold an OPEN file descriptor: rename the file and the writer keeps writing to the renamed
inode, while the live log goes silent forever. So the contents are copied to `.0.gz`, and the file
is truncated in place (the inode is preserved). The oracle for this is
`tests/unit/test_log_rotate.py::test_open_writer_keeps_writing_after_rotation`.

The same assertions are in `__main__` and run **every time the agent starts** (every 30 minutes):
if copytruncate ever breaks, `logs/logrotate_err.log` will report an `AssertionError`
instead of staying silent. The `selftest ok` line at the end of `logs/logrotate.log` means the check passed.

## Alternative: newsyslog (if you ever need it)

The configuration `launchd/health-logs.newsyslog.conf` is the **shared home of the log list**: it is also read by
`log_rotate.py`. Install the standard rotator like this (requires sudo; an owner step):

```sh
sudo cp ~/health_scripts/launchd/health-logs.newsyslog.conf /etc/newsyslog.d/health.conf
sudo newsyslog -nv | grep health
```

**Do not enable both at once** — two executors of the same list will race to rotate the logs.
If you enable newsyslog, unload the agent: `launchctl bootout gui/$(id -u)/com.larry.health.logrotate`.

## Oracle: did it work?

```sh
ls -laS ~/health_scripts/logs/*.log ~/health*.log | head -5   # no file > ~5 MB
ls ~/*.gz ~/health_scripts/logs/*.gz 2>/dev/null | head       # .0.gz archives have appeared
launchctl print gui/$(id -u)/com.larry.health.logrotate | grep -E "state|last exit"
tail -3 ~/health_scripts/logs/logrotate.log                   # what was rotated last time
```

Run a dry run at any time; it changes nothing:
`/opt/homebrew/bin/python3.11 ~/health_scripts/log_rotate.py --dry-run`

## A new log

The list of paths is in `launchd/health-logs.newsyslog.conf` (newsyslog format, the shared home for both
mechanisms). `newsyslog` on macOS does not understand `*.log` in a path (none of the six system configurations
in `/etc/newsyslog.d/` uses a glob, and it is not documented in the man page), so paths are listed
explicitly. If you create a module that writes its own log or add `StandardErrorPath` to a new `.plist`,
add a line to the configuration and copy it over again.

## Sensor: a log is growing but is missing from the configuration

`integrity_tests` → “Logs missing from the rotation configuration” (`check_logs_all_listed`), which evaluates
`log_rotate.unlisted_logs()`:

* **WARN at 1 MB** — visible in the integrity run. Measurement on September 1: 160 files on two
  machines were missing from the configuration, none larger than 1 MB — the threshold produces a signal, not background noise.
* **FAIL when a file exceeds the rotation threshold** (the one in the configuration itself, 5 MB): no one
  will trim that log. FAIL is a deliberate choice — failures in `integrity_latest.json` are read by `night_cycle`
  and brought to the owner; WARN stays in the run output.

The fix is always the same: a line in `launchd/health-logs.newsyslog.conf`.

Check manually:
`/opt/homebrew/bin/python3.11 -c "import log_rotate; print(log_rotate.unlisted_logs(min_mb=0.3))"`

### Sensor for the agent itself

`integrity_tests` → “Log rotation is alive and clean” (`check_logrotate_liveness`). It is needed because
the previous sensor is **blind** to the agent's death: logs grow, but they are all IN the configuration — zero findings,
and silence looks like order. That is exactly how 470 MB accumulated.

It checks two different things (§14: a heartbeat proves “started”, not “works correctly”):
the freshness of the receipt `logs/logrotate.log` (FAIL at 24 hours, WARN at 2 hours with a 30-minute interval), and its
`selftest ok` ending — whether the copytruncate self-check passed during the run. The second fails when
the agent is alive but broken. The job is listed in `producer_registry.MONITORED` — the coverage ratchet caught
it the same evening the agent was installed.

The receipt is read **locally**: integrity runs on Studio, so the agent on MacBook is covered
by its own integrity run there, not this one.

**Sensor liveness (§14):** an empty or unparsed configuration means failure, not “all clear”:
`_parse_conf` raises `ValueError`, and both rotation and the sensor fail loudly. Without this,
a broken sensor's silence would look like order.

**What the sensor does NOT see:** a log in a directory missing from the configuration, and a file without a `.log` extension.
Directories are derived from the configuration itself (the parents of its paths) — a second directory list would be
a second home for the same information and would drift from the first. The cost is stated, not forgotten.
