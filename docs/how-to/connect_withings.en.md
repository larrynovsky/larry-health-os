<!-- translation-of: docs/how-to/connect_withings.md sha256:55a53d76b3b7 -->
**English** · [Русский](connect_withings.md)

# How to connect a Withings blood-pressure monitor

> **Document type:** How-to (Diátaxis). Steps 1–4 are for the **author's original setup** (a repository
> clone, Python 3.11 on the machine). For the **Docker image install**, step 1 is the same and steps 2–4
> are in the last section.

Why: Withings readings reach the system through iPhone Health, and that path loses readings
silently. A direct Withings connection gives a second, independent source: every reading with
its own time and pulse. It lands in the `bp_readings` table, and a nightly check compares it with
what arrived through Health.

The connection lets the system only **read measurements** (`user.metrics`): blood pressure, pulse,
weight. The system cannot write anything to Withings.

You need: a Withings account that shows your monitor's readings; a browser where you can sign in
to that account; a terminal on the computer **with that same browser**.

## 1. Create an application in the Withings developer portal

1. Open the [developer portal](https://developer.withings.com/dashboard/) and sign in with your
   Withings account. When asked about the data center, choose the one that holds your account
   (Europe Cloud for Europe).
2. The portal asks for an organization: **Create organization** → any name (for example,
   `Health OS personal`) and your email → **Next**.
3. **1. Application creation:** tick **Public API integration**. Read the **Terms of use** and
   tick **I accept the terms of use** → **Next**.
4. **2. Information:**
   - **Target environment** — leave **Development**.
   - **Application name** — for example, `Health OS BP import`.
   - **Application description** — for example, `Personal import of my own blood pressure readings`.
   - **Registered URLs** — exactly `http://localhost:9877/withings/callback`.
5. Click **Done**. A yellow warning appears: `http` and `localhost` addresses are not accepted in
   production, and the application is limited to 10 users. **This is expected:** it is enough for
   yourself and your family. Click **Done** again.
6. A window with the **Client ID** and **Client Secret** opens. The secret is shown **once** —
   keep the window open until you finish step 2. Do not send the secret to the bot or a chat.

## 2. Save the Client ID and Client Secret

Copy the **Client ID** in the portal window and immediately run in the terminal:

```bash
mkdir -p ~/.health_secrets && chmod 700 ~/.health_secrets
pbpaste | tr -d '[:space:]' > ~/.health_secrets/withings_client_id && chmod 600 ~/.health_secrets/withings_client_id
```

Then copy the **Client Secret** and run:

```bash
pbpaste | tr -d '[:space:]' > ~/.health_secrets/withings_client_secret && chmod 600 ~/.health_secrets/withings_client_secret
printf '' | pbcopy   # clear the secret from the clipboard
```

Check without showing the values: `wc -c ~/.health_secrets/withings_client_*` — each file has
64 characters (as for the owner on 05.10.2026). You may now close the portal window.

`pbpaste`/`pbcopy` are macOS commands. On Linux use `xclip -o -selection clipboard`
and `xclip -selection clipboard < /dev/null` instead.

## 3. Grant access once

```bash
cd ~/health_scripts && python3.11 withings_api.py
```

1. The terminal prints a link. Open it in a browser **on this same computer**.
2. Sign in to Withings, check that it is the account with your readings, and click **Allow**.
3. The browser goes to `localhost:9877` and shows «Готово, окно можно закрыть.» ("Done, you may
   close the window"). The code in that address lives 30 seconds, so the command catches it
   itself — nothing to copy.
4. Wait for `Withings подключён; токен сохранён в …/withings_oauth.json (0600)` in the terminal.
   The command waits 5 minutes for Allow, then stops — in that case run it again.

**If the system runs on another machine** (for the author: a Mac Studio, while the browser is on a MacBook):
do steps 2–3 on the machine with the browser, but into a separate temporary folder, and move three files:

```bash
mkdir -p ~/.withings_setup && chmod 700 ~/.withings_setup
# step 2 — the same commands, but into ~/.withings_setup instead of ~/.health_secrets
cd ~/health_scripts && HEALTH_SECRETS_DIR=~/.withings_setup python3.11 withings_api.py ~/.withings_setup/withings_oauth.json
scp -p ~/.withings_setup/withings_client_id ~/.withings_setup/withings_client_secret \
       ~/.withings_setup/withings_oauth.json <system-machine>:.health_secrets/
ssh <system-machine> 'chmod 600 ~/.health_secrets/withings_*'
```

After moving, delete the temporary folder: the token copy in it becomes invalid at the first
refresh on the system machine, and the application secret should not sit in an extra copy.

Where things live afterwards. `~/.health_secrets/withings_oauth.json` is the initial grant; the
system only reads it. Withings **replaces the refresh token on every refresh**, so the system writes
the live token into the data directory (`data/withings_oauth.json`, mode 600) and saves the new one
before using it. Do not copy an old token file over a new one: the old refresh token is spent.

## 4. Check the import

```bash
cd ~/health_scripts && python3.11 import_withings.py
```

Expected: `Withings: замеров давления N, удалено как удалённых в Withings 0, дней давления
пересчитано M` ("N readings, 0 removed as deleted in Withings, M pressure days recomputed"), exit
code 0. Every run fetches the whole Withings history and makes the database its mirror: a reading
you delete in the app as a failed one is deleted here too. Once Withings is connected, it writes the
daily pressure (the mean of all readings of the day) instead of Health; days before the first Withings
reading stay as they were. After that the import runs on schedule twice a day, at 09:10 and 21:10.

Compare one reading with the Withings app: the database stores time in UTC, the app shows local time.

```bash
cd ~/health_scripts && python3.11 -c "
import health_db as db
with db.get_conn() as c:
    for r in c.execute(\"select datetime(measured_at,'unixepoch','localtime'), systolic, diastolic, pulse from bp_readings order by measured_at desc limit 3\"): print(*r)"
```

**Verified 05.10.2026:** the owner's connection, then an import with this version's code inside
the main machine's container on a temporary database — 3 readings received, times and values
match the Withings response (measure codes 9 — diastolic, 10 — systolic, 11 — pulse confirmed by a
live response). The owner's comparison of one reading with the Withings app is still pending.

If Withings is not connected (no files from steps 2–3), the import exits quietly with code 0 —
this is not an error. If it is connected but Withings refuses, the import exits with code 1 and
writes the fault into the fault journal; the night cycle handles it.

## If it did not work

- **нет файла withings_client_id / пустой withings_client_secret** (file missing / empty) — step 2
  was not done or the file is empty. Repeat step 2. If the secret window is already closed and the
  secret was not saved, look for issuing a new secret in the application settings in the portal
  (this path is not verified here).
- **ответа от Withings не было за 5 минут или state не совпал** (no answer within 5 minutes, or
  state mismatch) — Allow was not clicked in time, or a link from an earlier run was opened. Run
  step 3 again and open the new link.
- **The browser says it cannot connect to localhost:9877** — the browser is on a different computer
  than the command, or the command has already stopped. Run step 3 on the machine with the browser.
- **invalid redirect_uri** at Withings — the address in the portal does not match
  `http://localhost:9877/withings/callback` character for character.
- **токен Withings нечитаем; авторизуйте заново** (token unreadable, authorize again) or a refusal
  on refresh — repeat step 3. The new grant is newer than the live token, and the system takes it.

## Docker image install

Step 1 (the application in the Withings portal) is the same. Then, on the computer where Docker runs,
**with a browser on that same computer**:

**2D. Keys.** Save two plain-text files in `Downloads`: `withings_client_id` — only the Client ID,
`withings_client_secret` — only the Client Secret (as for Oura). Copy them with one command:

```bash
cd ~/health-docker && install -m 600 "$HOME/Downloads/withings_client_id" "$HOME/Downloads/withings_client_secret" secrets/
```

On Linux, if your UID is not 1000, run the same `install` command via `sudo` with `-o 1000 -g 1000`.
Then delete both copies from `Downloads`.

**3D. Permission.** The code receiver runs in a temporary container, and port 9877 is published
only on `localhost` of this machine:

```bash
cd ~/health-docker && docker compose run --rm -p 127.0.0.1:9877:9877 -e WITHINGS_CALLBACK_BIND=0.0.0.0 cron python3 withings_api.py /home/health/health/data/withings_oauth.json
```

Open the printed link in this computer's browser, click **Allow**, and wait for
`Withings подключён; токен сохранён в …` ("Withings connected; token saved to …"). The grant goes into
the data volume (`data/withings_oauth.json`, mode 600), not into `secrets`: the keys directory is
read-only inside the container. If port 9877 is busy, the command stops with an error — free the port;
another port will not do, it is registered in the Withings portal.

**4D. Check.**

```bash
cd ~/health-docker && docker compose exec -T cron python3 import_withings.py
```

**Verified 06.10.2026 on the main machine's image, without a real account:** with
`WITHINGS_CALLBACK_BIND=0.0.0.0` and the published port, a browser answer with the right `state` reaches
the receiver in the container («Готово, окно можно закрыть.»), a foreign `state` is refused; without the
variable the port is unreachable from outside. Connecting a real account this way has not happened yet.
