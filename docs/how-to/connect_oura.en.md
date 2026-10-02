<!-- translation-of: docs/how-to/connect_oura.md sha256:4bf70acac009 -->
**English** · [Русский](connect_oura.md)

# How to connect an Oura ring

> Document type: How-to (Diátaxis). Performed by the person who installed the system, on the machine where it runs.

The system gets sleep, heart rate variability, and activity from Oura API v2 (`import_oura.py`). It
needs an Oura personal access token. The import is already on the schedule: once the token is in
place, there is nothing to start or restart.

1. Get a token in your Oura account (developer section / Personal Access Tokens).

   Check Oura's website for where it is now: the account changes. According to third-party
   sources, Oura is winding down personal tokens (end of 2025 is mentioned); as of 2026-10-02 the
   token in the system author's installation works. If there is no token section in your account,
   write in [issues](https://github.com/larrynovsky/larry-health-os/issues): it means this route has
   closed and needs replacing.

2. Put the token in the secrets directory as a file named `oura_token`. In the commands below,
   replace `TOKEN` with your token and keep the quotes.

   **Docker installation** (following the [First installation](../tutorials/first_install.md)
   tutorial) — the directory is `~/health-docker/secrets`, the same one that holds the Telegram and
   Anthropic keys:

   ```bash
   printf '%s' 'TOKEN' > ~/health-docker/secrets/oura_token && chmod 600 ~/health-docker/secrets/oura_token
   ```

   This is enough on a Mac and on most Linux machines. A different command is needed only on Linux
   where, in step 4 of the tutorial, you handed the secrets directory to user 1000 with `sudo chown`:
   then a normal write into the directory fails, so write through `sudo tee`:

   ```bash
   printf '%s' 'TOKEN' | sudo tee ~/health-docker/secrets/oura_token >/dev/null
   ```

   **Installation without Docker** — the directory is `~/.health_secrets`:

   ```bash
   printf '%s' 'TOKEN' > ~/.health_secrets/oura_token && chmod 600 ~/.health_secrets/oura_token
   ```

   For a second person on the same machine — their own secrets directory (how it is set up —
   [How to add a person](add_person.md)).

3. Check right away, without waiting for the morning (Docker installation, from `~/health-docker`) —
   fetch the data for the last 3 days:

   ```bash
   cd ~/health-docker && docker compose exec -T cron python3 import_oura.py 3
   ```

   An error in the output means the token was not accepted: check that the file holds only the
   token, with no spaces or quotes (`cat ~/health-docker/secrets/oura_token`). No error — within a
   minute the "Sleep and recovery" card on the dashboard home page shows the date of the last night.
   The next morning, sleep also appears in the report.

Do not send the token to the bot: Telegram messages are stored on someone else's servers.

If nights stop arriving (the "Sleep and recovery" card turns red), the token has most likely been
revoked or has expired. Issue a new one in your Oura account and replace the file with the same command as in step 2.
