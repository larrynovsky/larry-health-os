<!-- translation-of: docs/how-to/connect_oura.md sha256:8c1fb2bef382 -->
**English** · [Русский](connect_oura.md)

# How to connect an Oura ring

For a Docker installation following the [first installation tutorial](../tutorials/first_install.md).
You need a terminal on the computer running the system; if a helper installed it, do this step
with them. Sign in to your own Oura account in a browser: the browser can be on another computer,
but paste the address after sign-in into the same waiting terminal. You need an Oura account,
a synced ring and, for Gen3 and later, an active Oura Membership.
Connecting lets the system read sleep, readiness, activity, workouts, SpO₂, stress and resilience.

New users connect through OAuth2: approve access once in a browser,
then the system attempts to renew access while the grant remains valid. If you already have a personal token, use the last section.

## 1. Register an application in Oura

1. Open the [new developer portal](https://developer.ouraring.com/applications),
   sign in with your Oura account and create an application under **Applications**.
2. Fill in the required fields:
   - **Display Name** — `Larry Health OS` or your own recognizable name.
   - **Description** — for example, `Personal sleep and activity tracking`.
   - **Contact Email** — your own email address.
   - **Website** — `https://github.com/larrynovsky/larry-health-os`.
   - **Privacy Policy** — `https://github.com/larrynovsky/larry-health-os`.
   - **Terms of Service** — `https://github.com/larrynovsky/larry-health-os/blob/main/LICENSE`.
   - **Redirect URIs** — exactly `http://localhost:9876/callback/`, including the final `/`.
     Do not change the port or use the container address. If **+ Add URI** added
     an extra empty row, delete it before creating the application.
3. The form's **Scopes** are all selected by default. The command below requests
   only the four used by the importer: **daily**, **workout**, **spo2**, **stress**.
   Keep these four enabled in the application settings.
4. Tick **I agree to the Oura API Agreement** and create the application.
5. A window opens with **Client ID** and **Client Secret**, each with a copy button.
   Leave it open for the next step. Never send the secret to the bot or a chat.

**Owner checked on 2026-10-02:** an application was created in the new portal using
these Website, Privacy Policy and Terms of Service addresses. The regular cloud command below successfully
connected that application; the new portal does not need a separate OAuth route.

## 2. Save the Client ID and Client Secret

On the computer running Docker, save two plain-text files in `Downloads`:
`oura_client_id` — only the Client ID, and `oura_client_secret` — only the Client Secret.
No quotation marks, formatting or extra `.txt`; for example, switch TextEdit to plain text.
The secret is not pasted into a command or left in terminal history.

Copy both files with one native `install` command: it sets mode `600` immediately.
Installation already created the `secrets` directory.

```bash
cd ~/health-docker && install -m 600 "$HOME/Downloads/oura_client_id" "$HOME/Downloads/oura_client_secret" secrets/
```

**On Linux, if your UID is not 1000** (`id -u`) and you assigned the keys to the container
in tutorial step 4, use this instead — copying sets owner and mode, without a separate chown:

```bash
cd ~/health-docker && sudo install -o 1000 -g 1000 -m 600 "$HOME/Downloads/oura_client_id" "$HOME/Downloads/oura_client_secret" secrets/
```

After copying succeeds, delete the two temporary Downloads copies and empty the trash.

## 3. Authorize access once

Use one command for applications created in either the new or the old portal:

```bash
cd ~/health-docker && docker compose exec cron python3 oura_oauth.py
```

**If you connected before `stress` was added:** run this command again and give fresh
consent. Updating the program or refreshing an old token does not add permission.
The old `--portal developer` option is no longer supported: its exchange through `moi`
returned HTTP 400 in the live check on 2026-10-02. If a token from that option remains,
reconnect with the command above; there is no automatic retry against a different endpoint.

1. The terminal prints a link. Open it in a browser where you can sign in to your Oura.
   It can be on another computer: the callback is copied as an address, not received by a server.
2. Sign in to Oura and check the account. On the consent screen, access switches
   **are off by default**: click the switches themselves and enable the requested
   **daily**, **workout**, **spo2**, **stress**, then click **Allow**.
   Allow with the switches off returned `access_denied` in the owner's check on 2026-10-02.
3. The browser goes to `http://localhost:9876/callback/?...` and shows a connection error.
   **This is expected:** no server runs on that port; Docker port forwarding is unnecessary.
4. Copy the **full address from the address bar**, including `code=...` and `state=...`.
   Paste it into the waiting terminal and press Enter. The pasted address is hidden — no characters appear.
   It contains a single-use access code: never send it to a chat.
5. Wait for `Oura OAuth connected`. The grant is saved in `data/oura_oauth.json` with mode 600.

In the standard Docker installation, this is `/home/health/health/data/oura_oauth.json` inside the container,
in the persistent `health-home` volume (installer layout checked on 2026-10-02). It is not a file
in `~/health-docker/secrets` on the host. The secrets directory inside the container is read only,
so both initial authorization and renewals are written to data. Include the volume containing this file
in protected backups; never put the file in git.
Do not copy an old OAuth file over the new one: refresh tokens are single-use.

## 4. Check the import

After syncing your ring with the Oura app:

```bash
cd ~/health-docker && docker compose exec -T cron python3 import_oura.py 3
```

`3` means: the request starts at the current date minus three days and ends at the current date.
It is not a count of rings, files or attempts; without an argument the code uses 30 days.

The log should identify `Oura token source: OAuth2` and the number of saved days.
Zero days alone does not confirm a connection: check ring sync and API errors.
Open `http://127.0.0.1:8001/` in a browser **on the computer running Docker**.
On another computer that address refers to that computer: use private Tailscale access
already configured, or open the page on the machine running the system. When data is
available, the “Sleep and recovery” card shows the night’s date; otherwise it waits for arrivals.
The owner checked initial connection and import through the cloud route on 2026-10-02.
Access to `daily_resilience` needs `stress`: without that permission it returned 401.
Fresh consent with `stress` and the subsequent import still need the owner's check
after updating the program; the earlier successful import does not establish that.

The import command itself sends no bot message: data may enter the next morning report.
Scheduled imports take over afterwards.

If access is revoked or renewal fails, OAuth does not fall back to PAT. Repeat the import
command above to see the error, then go through step 3 again. The token file can remain
on disk — its presence does not prove access is still valid.

## Troubleshooting

- **Missing/unreadable / Empty oura_client…** — a file is missing, empty or unreadable by the container.
  Check filenames, directory and owner, then repeat steps 2–3.
- **Wrong state** — the address came from another attempt or was truncated. Restart step 3 and paste
  the address from that new attempt. This error prevents code exchange.
- **Wrong redirect / invalid_redirect_uri** — compare the URI in Oura with `http://localhost:9876/callback/`,
  including the port and final `/`. Paste the address after approval, not the original authorization link.
- **authorization denied / access_denied** — turn on the requested access switches themselves, then Allow; restart step 3.
- **HTTP 400/401 during exchange** — possible causes include wrong client id/secret, an old code or
  a consumed refresh token. Check the client files and use the single command in step 3 for a new code.
  The error body is hidden, so the command cannot establish the exact cause.
- **could not be saved in data** — the data directory is not writable or the disk is full.
  Import stops: the new access token is not used and there is no PAT fallback. Fix writing and repeat
  authorization: the server may already have rotated the refresh token; the old file cannot restore access.
- **network or invalid JSON** — the connection failed or the response was unusable. After an ambiguous
  refresh result, restarting step 3 is safer: it is unknown whether the server already rotated the refresh token.
- **401/403 during import** — access was revoked, permissions were not granted or membership is inactive.
  Check Membership and approve the required permissions again. Import results and data verify Oura access;
  an OAuth file's presence on the card means only “connection configured”.

## If you already have a personal token (PAT)

OAuth is optional for a working PAT. As of 2026-10-02, public materials mark PATs as deprecated;
the ability to issue a new one and the lifetime of existing ones were not checked here. Store your existing
token as `oura_token`, as before.

**Docker**:

```bash
printf '%s' 'TOKEN' > ~/health-docker/secrets/oura_token && chmod 600 ~/health-docker/secrets/oura_token
```

**Docker on Linux**:

```bash
printf '%s' 'TOKEN' | sudo tee ~/health-docker/secrets/oura_token >/dev/null
sudo chown 1000:1000 ~/health-docker/secrets/oura_token
sudo chmod 600 ~/health-docker/secrets/oura_token
```

Check the import using step 4's command; the source will be `Oura token source: PAT`.
If an OAuth grant exists in `data/oura_oauth.json` or the secrets directory, OAuth takes precedence.
OAuth errors never switch to PAT. To deliberately return to PAT, remove both OAuth files
from their active directories, retaining a protected backup. Replace a revoked PAT with OAuth using steps 1–3.
