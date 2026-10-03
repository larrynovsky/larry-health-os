<!-- translation-of: docs/how-to/connect_google_calendar.md sha256:ae5cd33b077f -->
**English** · [Русский](connect_google_calendar.md)

# How to connect Google Calendar

> Document type: How-to (Diátaxis). For someone who installed the system using the
> [first installation tutorial](../tutorials/first_install.md). The installer uses a terminal
> on the computer running Docker; the calendar owner signs in with their Google account in a browser.
> The browser can be on another computer: paste the address after sign-in into the waiting
> terminal on the system’s machine. One person can do both, or the owner can work with a helper.

The calendar is optional. Connecting grants **read access to all calendars available for you to read**,
not just trips: events from two days ago through 30 days ahead. The system cannot change
or delete events. Trip names, locations and dates may enter the morning report, Telegram
and text sent to your configured model provider — processing is not confined to this machine.
Once connected, the system can show a card before a trip.
Do not send keys or the authorization address to the bot: they grant access to the calendar.

## 1. Obtain the client JSON in Google Cloud Console

The installation owner creates the application; the calendar owner then grants it read access.

**Verification boundary, 2026-10-02:** the signed-in console and a live Google exchange were not tested here.
The steps below describe configuration, not a completed installation trial. Menu names may
vary: the new interface uses Google Auth Platform, the old one uses APIs & Services.
[Google documentation](https://developers.google.com/calendar/api/quickstart/python) is a reference.

1. Sign in to [Google Cloud Console](https://console.cloud.google.com/) with your Google account.
   In the project selector at the top, choose **New project**, enter a name such as
   `Health Calendar`, click **Create**, and select the new project.
2. Open **APIs & Services → Library**, find **Google Calendar API**, and click **Enable**.
3. Open **Google Auth Platform → Branding → Get started** (old interface:
   **APIs & Services → OAuth consent screen**). Fill in the application name, support email,
   and contact email. For a regular personal account, choose **External** as the audience.
4. In **Audience**, keep the **Testing** status; do not click **Publish app**.
   Under **Test users → Add users**, add the email of the account whose calendar you are connecting and save.
   If installing for yourself, add yourself. Under **Data Access → Add or remove scopes**, select
   `https://www.googleapis.com/auth/calendar.readonly` and save. In the old interface these
   are the **Scopes** and **Test users** steps of the consent screen.
5. Open **Clients → Create client** (old interface:
   **APIs & Services → Credentials → Create Credentials → OAuth client ID**).
   **Application type: Desktop app**. Enter a name and click **Create**.
   Do not choose Web application; do not enter a separate redirect address in the console for a Desktop app.
6. Download the client JSON (**Download JSON** or the download icon next to the client).
   On the host, rename the downloaded file to `google_calendar_client.json` and leave it in `Downloads`.
   You need a Desktop app OAuth client JSON, not a service account key or an API key.
   If the browser is on another machine, first transfer the JSON to Downloads on the
   computer running Docker; do not send it to the bot.

For an application in Testing, a grant with the Calendar scope usually requires authorization
again after 7 days. This Google rule was not verified here; consult the
[refresh token lifetime description](https://developers.google.com/identity/protocols/oauth2#expiration).
If data stops arriving, repeat step 3 below. Moving to Production and Google's application
verification requirements are outside the scope of this guide.

## 2. Put the client in the host secrets directory

On the computer running Docker, `google_calendar_client.json` is already in `Downloads` after step 1.
Beside it, save a **plain-text** file named `google_calendar_account`: only the email of the account
chosen when signing in to Google, without quotes, formatting or an extra `.txt`.
This file lets the reader reject another account; without it, calendar identity is not checked.

Copy both files with one native `install` command: mode `600` is set immediately.

```bash
cd ~/health-docker && install -m 600 "$HOME/Downloads/google_calendar_client.json" "$HOME/Downloads/google_calendar_account" secrets/
```

**On Linux, if your UID is not 1000** (`id -u`) and you assigned the keys to the container
in tutorial step 4, use this instead, which also sets their owner:

```bash
cd ~/health-docker && sudo install -o 1000 -g 1000 -m 600 "$HOME/Downloads/google_calendar_client.json" "$HOME/Downloads/google_calendar_account" secrets/
```

After copying succeeds, delete the two temporary Downloads copies and empty the trash.
In the standard installation, `secrets/` is mounted read-only at
`/home/health/.health_secrets`; the OAuth token is later written to the persistent data volume.

## 3. Grant access in your browser

The installer starts this interactive command; do not add `-T`:

```bash
cd ~/health-docker && docker compose exec cron python3 google_calendar_fetcher.py --setup --manual
```

1. The terminal displays a link. The calendar owner opens it in their browser
   (on this or another machine),
   chooses the account from `google_calendar_account`, and grants **read** access to the calendar.
   If Google warns about a test application, proceed only for
   your own application from step 1. An access denial means you should check test users and the scope.
2. The browser redirects to an address beginning with `http://localhost:9877/?`.
   The page may say it cannot connect: no callback server is started,
   and this is expected. Copy the **full address from the address bar**, including `state` and `code`.
3. Paste it into **Paste the full redirect URL (hidden)** in the same terminal and press
   Enter. Input is hidden. The address belongs to this attempt only; do not use an old tab.
4. Wait for “Токен сохранён” (token saved). The authorization is written with mode `600` to
   `/home/health/health/data/google_calendar_token.json` inside the persistent `health-home` volume.
   The secrets directory is unchanged. No service restart or port forwarding is required.

Inside a container, `--setup` selects manual mode automatically; `--manual` makes the choice explicit.

## 4. Check the connection

The installer runs a fetch immediately:

```bash
cd ~/health-docker && docker compose exec -T cron python3 google_calendar_fetcher.py
```

Expect `Calendar cache: N событий` (N events) without calendar retrieval errors or warnings.
Zero events is valid if the calendar is empty within the fetched interval. The
“token saved” message confirms authorization only; this command checks Calendar API access.
Individual calendar errors produce warnings even if a cache is created — check those warnings.

Check the fetch time and event count without displaying their contents:

```bash
cd ~/health-docker && docker compose exec -T cron python3 -c 'import json; from google_calendar_fetcher import cache_path; c = json.loads(cache_path().read_text()); print("Fetched:", c["fetched_at"], "Events:", len(c["events"]))'
```

Open `http://127.0.0.1:8001/` on the computer running Docker. On another computer that
address refers to that computer: open the page on the system’s machine or use private
Tailscale access already configured. Refresh the home page: “Travel calendar” should show “Connected”.
Subsequent fetches run through `cron` every hour. The card confirms cache freshness,
not account identity or the presence of a trip. On an account mismatch, the reader
blocks events and writes `CAL_TENANT_MISMATCH` to the calendar consumer's logs.


For example, an event “Flight to Paris” tomorrow with location “Paris” becomes a candidate
card meaning “tomorrow: trip — Paris”. The code looks for words such as “flight”, “hotel”
or “trip” in the title and location; any nonempty location also marks an event as travel,
so an ordinary meeting with an address can become a candidate. The morning report selects
one event for today, tomorrow or the day after, preferably with a location. This is a candidate:
selection and duplicate suppression may keep it out of the message; a separate card
immediately after connecting is not promised.

When access expires, the fetcher attempts renewal; on failure it logs
`Token refresh failed` and does not update the cache. The reader logs a warning after 3 hours
and stops returning a cache older than 25 hours. A separate message to you about an expired
grant was not confirmed in this path. If the card says fetching is delayed,
repeat the step 4 command, read the error and grant access again with step 3.

## If something goes wrong

| Message or symptom | What to do |
|---|---|
| `--manual` is not recognized | The image predates this guide. Update using the tutorial and repeat step 3 |
| “Файл клиента не найден” (client file not found) | Check the filename and secrets mount; the client belongs in the application owner's directory |
| `Unreadable/invalid google_calendar_client.json` | Download the Desktop app client JSON; check UID 1000 read permissions |
| `access_denied`, user not allowed | Add the selected Google account to Test users; check that Calendar API is enabled |
| `Wrong state` or `Wrong redirect URL` | End this attempt and run step 3 again; paste the full address from the new tab |
| `Google OAuth exchange failed` | Check the client and container internet access, then repeat step 3 with a new address |
| `Google did not return a refresh token` | Repeat step 3 and approve access; the connection is not saved without a refresh token |
| `token could not be saved; access stopped` | Check free space and UID 1000 write permissions in DATA. The old file is not replaced on a write/replace failure; do not make secrets writable |
| `Token refresh failed` / “Нет действующего … токена” (no valid token) | Repeat step 3: the Testing grant may have expired or access may have been revoked |
| `CAL_TENANT_UNVERIFIED` / `CAL_TENANT_MISMATCH` | Put the correct email in `google_calendar_account`; if you signed in to another account, repeat step 3 with the intended one |
| Token exists but the card is waiting for a fetch | Run step 4 and read the fetch errors |

## What data is read and where it stays

The `calendar.readonly` grant allows reading the calendar, without creating, changing, or deleting
events. The fetcher traverses accessible calendars with owner/writer/reader roles; this is broader than a travel
calendar. It requests events from two days ago through 30 days ahead. Full Google responses
may include descriptions and attendees; those fields are not saved to the cache.

DATA stores the calendar list and their identifiers/names, the primary calendar email,
and, for each event, its ID, title, start/end, all-day flag, location, calendar name, and event
type. The reader identifies trips; their details may appear in the morning report, Telegram,
and text sent to the configured model provider. Connecting does not mean the entire calendar
stays exclusively between Google and this machine.

The OAuth token is also stored in DATA: volume backups include it. Do not publish or send the
client JSON, token, or callback address to the bot. To revoke access, remove the
application's grant in [Google account connections](https://myaccount.google.com/connections).
Revocation does not delete the existing local cache or reports already sent.
