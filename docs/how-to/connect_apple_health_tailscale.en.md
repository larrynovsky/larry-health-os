<!-- translation-of: docs/how-to/connect_apple_health_tailscale.md sha256:99e110d36218 -->
**English** · [Русский](connect_apple_health_tailscale.md)

# How to connect Health on iPhone and Apple Watch over Tailscale

> Document type: How-to (Diátaxis). Performed by the person who installed the system, together with the phone's owner.
> This is the safe path: sending is encrypted and goes only to your Mac — at home and when
> travelling. The simple path over home Wi-Fi and the one-time upload of all history —
> ["How to connect Health on iPhone and Apple Watch"](connect_apple_health.md); history is sent
> the same way on this path too (step 4 of that guide).

Data arrives through the Health Auto Export app (iOS): it sends it to the system's dashboard by
itself, `POST /hae/ingest`, as JSON. Three things are needed for this: an ingest token, a route
from the phone to the dashboard, and an automation in the app. Automatic sending in Health Auto
Export is a feature of the paid Premium tier (subscription or one-time purchase; 7 days free to try).

1. **Ingest token.** On the machine where the system runs, create a token and put it in the
   secrets directory. In a Docker installation there is one directory — `~/health-docker/secrets`;
   without Docker each person of the installation has their own. Nothing needs restarting: the
   dashboard reads the token on every send.

   Docker installation — the directory is `~/health-docker/secrets`:

   ```bash
   openssl rand -hex 24 > ~/health-docker/secrets/hae_ingest_token && chmod 600 ~/health-docker/secrets/hae_ingest_token
   cat ~/health-docker/secrets/hae_ingest_token
   ```

   Installation without Docker — `~/.health_secrets`; for a second person, their `HEALTH_SECRETS_DIR`
   (for example `~/.health_secrets_partner`):

   ```bash
   S=~/.health_secrets
   openssl rand -hex 24 > $S/hae_ingest_token && chmod 600 $S/hae_ingest_token
   cat $S/hae_ingest_token
   ```

   The last command prints the token — 48 characters. Move it to the phone any way you like
   (AirDrop, a password manager, the shared Mac–iPhone clipboard), just not through the chat with
   the bot: Telegram messages are stored on someone else's servers.

2. **Route from the phone to the dashboard — private network only.** After installation the
   dashboard listens only on `127.0.0.1`, and the phone cannot see it. Install Tailscale on this
   machine and on the phone and sign in to both with the same account. The devices of this account
   form your private network.
   Use the account of the person whose data this is: every device in this network will see the
   dashboard. The device list is in the Tailscale web console, Machines section; remove anything
   that does not belong. Keep Tailscale on the phone switched on at all times: when it is off,
   sends silently fail.

   Docker installation — open the dashboard port to the private network:

   ```bash
   tailscale serve --bg --tcp 8001 tcp://127.0.0.1:8001
   tailscale ip -4
   ```

   The `tailscale` command may be missing from the terminal if Tailscale was installed on the Mac
   from the App Store. Then the same command lives inside the app: instead of `tailscale`, type
   `/Applications/Tailscale.app/Contents/MacOS/Tailscale`. If `serve` replies that Serve is not
   enabled on your network and gives a link, open the link and enable it: this is a setting of your
   private network, not exposure to the internet.

   The second command prints the machine's private-network address, for example
   `100.101.102.103`. Check: `http://<that address>:8001` opens in Safari on the phone while
   Tailscale is on. An address without the `s` in `http` is not a mistake: inside the Tailscale
   network the connection is encrypted anyway.

   Installation without Docker — put this address into `private/infra.yaml` as the line
   `studio_host: <address>` and restart the dashboard:
   `launchctl kickstart -k gui/$(id -u)/com.larry.health.dashboard`.

   **What this opens.** The dashboard has no password. `tailscale serve` opens it to every device in
   your private network — and only to them. If you have let other people's devices into your
   Tailscale network, they will see the medical record too. **Do not turn on `tailscale funnel`**:
   it opens the dashboard to the whole internet.

3. **Automation in Health Auto Export.** In the app, create an automation of type REST API:
   - URL: `http://<address from step 2>:8001/hae/ingest`;
   - header (Headers): name `Authorization`, value `Bearer <token from step 1>` — the word
     `Bearer`, a space, the token;
   - Export Format — **JSON** (the system does not read CSV), the Summarize setting — on;
   - metrics — whichever you want to see; the system takes what arrives.

4. **Verify.** Run the automation manually in the app. After that it sends by itself, on its own
   schedule; the machine running the system must be on and awake at that time, or the send will not
   arrive. Within a minute the "Activity, heart rate,
   weight" card on the dashboard home page shows the date of the latest data, and from the next
   morning steps and heart rate appear in the report.

   Nothing appeared — check in the Health Auto Export automation log what the server answered:
   - `401` — the token in the app does not match the file: check the word `Bearer` and the space after it;
   - `503` — there is no token file on the machine: repeat step 1;
   - no answer — the phone cannot see the machine: is Tailscale on on both devices, and did the
     command from step 2 work.

Why this person's dashboard: data is written to the database of whoever's process received the
request, and the token is read from that same person's secrets. The owner's token does not work on
the second person's dashboard, and vice versa — otherwise one phone could write into someone else's
database. How to set up a second person in a Docker installation is not described yet; without
Docker — [How to add a person](add_person.md).

The Docker route (`tailscale serve --tcp` on port 8001 and `http://<address>:8001/hae/ingest`) is the
one through which the owner's data has reached his container since 2026-09-30; it has not yet been
tested on a live phone by anyone else. The `tailscale serve` syntax has changed between Tailscale
versions: if the command returns an error, check `tailscale serve --help`.
