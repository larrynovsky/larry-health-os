<!-- translation-of: docs/how-to/connect_apple_health.md sha256:1257fc0bcda4 -->
**English** · [Русский](connect_apple_health.md)

# How to connect Health on iPhone and Apple Watch

> Document type: How-to (Diátaxis). Steps 1–2 are done by the person who installed the system, in a
> terminal on the Mac; steps 3–4 by the iPhone owner on the phone; step 5 together.

Steps, heart rate, weight, sleep from the watch — everything the iPhone collects in the Health app —
reaches the system through the **Health Auto Export** app (iOS). Apple Watch writes into Health on
the iPhone, so the watch needs no separate connection. Connecting takes two passes:

- **once**, send the whole history of past years;
- **then every day** the phone sends what is new to the Mac by itself while both are on the same
  home Wi-Fi.

On the "phone → Mac" leg neither the internet nor anyone else's servers take part: the phone sends
straight to the Mac.

> **The simple path and the safe path.** Below is the simple path: over home Wi-Fi, with no extra
> software. It has two weak spots. Inside the Wi-Fi the data travels unencrypted (`http`). And away
> from home the phone will try to send to the same address on schedule — on someone else's network
> (hotel, café) a stranger's device may answer at that address and receive a batch of your data
> together with the token. **We recommend the safe path — over the private Tailscale network:** it
> encrypts sending and checks that your Mac is on the other end, at home and when travelling —
> ["Connecting Health over Tailscale"](connect_apple_health_tailscale.md) (it stands on its own:
> follow it instead of steps 1–3 here, and send the history with step 4 from here). If you stay on the simple
> path, switch the automation off in Health Auto Export (step 3) before a trip and back on at home.

**What you need.** Health Auto Export from the App Store. Automatic sending is a paid feature
(Premium: subscription or one-time purchase, the first 7 days free). The Mac running the system
must be on and awake when the phone sends, or the send will not arrive. Desktop Mac: "System
Settings → Energy" — prevent automatic sleeping. Laptop: "Battery → Options" — the same on power
adapter; with the lid closed a laptop sleeps anyway.

## 1. Turn on intake on the Mac

In a terminal on the Mac, in the installation directory:

```bash
cd ~/health-docker && bash install.sh --lan-ingest
```

This is the same installation command: it updates the system and turns on intake from the phone.
It will not ask again for the keys you entered at installation (bot and model). If it answers
"Unknown option: --lan-ingest", your `install.sh` is old; download a fresh one and run the command
above again:

```bash
cd ~/health-docker && curl -fsSL -o install.sh https://github.com/larrynovsky/larry-health-os/releases/latest/download/install.sh
```

At the end the installation prints two lines you need next (there is no token line if the token
was created earlier — then it is the same one):

- `phone intake token created: …/hae_ingest_token` — where the password by which the Mac recognises
  your phone is kept (step 2 shows the password itself). You do not obtain it anywhere: the
  installation has just created it;
- `phone intake over Wi-Fi: http://192.168.1.23:8011/hae/ingest` — the address the phone will send
  to (your numbers differ). Lost the line — run the command again, it prints the address again.

If macOS asks whether to allow incoming connections — allow them, or the phone will not see the Mac.

**What this opens.** Only data intake reaches the home network: it accepts writes with this token
and shows nothing. The dashboard with the medical record still opens only on this Mac. Someone who
knows the token cannot read the medical record but can add false numbers to it — so keep the token
like a password. The address starts with `http`, not `https`: inside your Wi-Fi the data travels
without intake encryption, protected only by the Wi-Fi password itself. To turn intake off:
`cd ~/health-docker && bash install.sh --no-lan-ingest`.

**Better to give the address by the Mac's name.** The router may one day change the numbers, and
sending will silently stop arriving. The name does not change. Find it with:

```bash
echo "$(scutil --get LocalHostName).local"
```

You get, for example, `Mac-mini-Anna.local`. Then the address for the phone is
`http://Mac-mini-Anna.local:8011/hae/ingest`.

## 2. Move the token to the phone

Show the token:

```bash
cat ~/health-docker/secrets/hae_ingest_token
```

It is a string of 48 letters and digits. Select it with the mouse, copy it (Cmd+C) and paste it on
the iPhone via Universal Clipboard (same Apple ID on Mac and iPhone, Bluetooth and Handoff on) or
send it by AirDrop as text. No need to retype it. Do not send the token over Telegram or email and
do not keep it in Notes: all of these live on someone else's servers.

Phone lost or the token went to the wrong place — change it: delete the file and turn intake on
again, then paste the new token into the app.

```bash
cd ~/health-docker && rm secrets/hae_ingest_token && bash install.sh --lan-ingest
```

## 3. Set up sending in Health Auto Export

1. Open Health Auto Export and allow it to read Health — iOS asks on first launch. Tick all
   categories: the system takes what it can read.
   When iOS asks whether to allow the app to "find devices on your local network" — allow it.
   Refuse, and sending to the Mac silently fails. To fix: "Settings → Privacy & Security → Local
   Network" → turn on Health Auto Export.
2. Open the side menu → **Automations** → **New Automation**, choose **REST API** as the
   **Automation Type**.
3. Fill in:
   - **URL** — the address from step 1;
   - **Headers** → add: name `Authorization`, value `Bearer ` followed by the token (the word
     `Bearer`, a space, the token);
   - **Data Type** — Health Metrics;
   - **Export Format** — **JSON** (the system does not read CSV);
   - **Summarize Data** — on: data is summarised by day, which is how the system reads it;
   - **Date Range** — **Since Last Sync**: each time everything since the previous send goes out, so
     days spent away from home arrive when the phone is back on home Wi-Fi;
   - **Batch Requests** — on: a large batch goes out in several requests instead of one;
   - **Sync Cadence** — hourly: no need for more often, the report is built once a day.
4. Save and enable the automation.

## 4. Send the whole history once

In this automation's settings tap **Manual Export**, choose the period — from the year you got the
iPhone (there is no data before that; extra days are simply empty) to today — and tap **Export**.
For this time turn off auto-lock ("Settings → Display & Brightness → Auto-Lock → Never"), keep the
app open and the phone on the same Wi-Fi: years of data take from a few minutes to half an hour.
Then turn auto-lock back on.

The app may crash on a very long period — its help says plainly that large exports hit the phone's
memory limits. Then send the history in parts: a year or two at a time, oldest first. Sending the
same days again duplicates nothing.

## 5. Check

The person who installed the system opens the dashboard home page on the Mac
(`http://127.0.0.1:8001`). Once sending has finished, the "Activity, heart rate, weight" card shows
the date of the latest data. From the next morning steps and heart rate appear in the bot's
morning report.

Nothing appeared — in the automation settings tap **View Activity Logs** and see what the Mac
answered:

- `401` — the token in the app does not match the file: check the word `Bearer` and the space after it;
- `503` — there is no token file on the Mac: repeat step 1;
- no answer or "could not connect" — the phone cannot see the Mac: is the phone on the same Wi-Fi
  (not the guest one)? Is the Mac awake? Is "Local Network" allowed for the app (step 3)? If the
  `.local` name does not work, use the numeric address from step 1;
- another code — note it and the full answer and open an
  [issue](https://github.com/larrynovsky/larry-health-os/issues).

## After that — every day by itself

The automation sends what is new on its schedule (**Sync Cadence**) while the phone and the Mac are
on the same Wi-Fi. The app's help says Health is unavailable to apps while the phone is locked, so
sending happens while you use the phone. In the system author's experience, the Health Auto Export
widget on the Home Screen lets sending go on even with the phone locked — add it (long-press the
Home Screen → "+" → Health Auto Export). Away from home sending does not arrive; with
**Since Last Sync** the missed days go out with the first send at home.

**Not checked on a live phone (02.10.2026):** that "since last sync" counts from the last
*successful* send rather than the last attempt. If after a trip the morning report has no steps for
the trip days — repeat step 4 with the period starting on the day you left.

Need sending while travelling, the dashboard on the phone, or a second person — the private
Tailscale network path: ["Connecting Health over Tailscale"](connect_apple_health_tailscale.md).

On Linux: there may be no `.local` name — use the numeric address; if at installation you handed the
key directory to user 1000, start the `cat` and `rm` commands from step 2 with the word `sudo`.
