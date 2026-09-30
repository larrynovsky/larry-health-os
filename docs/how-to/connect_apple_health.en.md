<!-- translation-of: docs/how-to/connect_apple_health.md sha256:49653886baaf -->
**English** · [Русский](connect_apple_health.md)

# How to connect Health on iPhone and Apple Watch

> Document type: How-to (Diátaxis). The person who installed the system does this together with the phone's owner.

Data arrives through the Health Auto Export app (iOS): it automatically sends it to the system's dashboard,
`POST /hae/ingest`, in JSON format.

1. On the primary machine, create an ingestion token and put it in THIS person's secrets
   directory (for the second person in the installation, use their `HEALTH_SECRETS_DIR`, for example,
   `~/.health_secrets_partner`):

   ```bash
   S=~/.health_secrets   # or the second person's secrets directory
   openssl rand -hex 24 > $S/hae_ingest_token && chmod 600 $S/hae_ingest_token
   ```

   Give the token to the phone's owner outside the bot chat.

2. The phone must be able to reach the dashboard. After installation, the dashboard listens only on `127.0.0.1` —
   the phone cannot reach it. Install Tailscale on the primary machine and the phone, find
   the machine's address on the private network (`tailscale ip -4`), and enter it in `private/infra.yaml`
   as `studio_host: <этот адрес>`. Restart the dashboard:
   `launchctl kickstart -k gui/$(id -u)/com.larry.health.dashboard`.
   Verify that `http://<этот адрес>:8001` opens in Safari on the phone. Do not enable
   `tailscale funnel` for this address: the dashboard has no authorization, so exposing it to the internet means
   exposing your medical record to everyone.
3. In Health Auto Export, create a REST API automation:
   - URL: `http://<адрес дашборда этого человека>/hae/ingest` — the second person has their own
     dashboard port (the same one their location is sent to);
   - header: `Authorization: Bearer <токен из шага 1>`;
   - Export Format — **JSON** (the system does not read CSV), Summarize — enabled.
4. Verify: after the first upload, a file appears under `data/hae_rest/` in the data directory,
   and steps and heart rate appear in the morning report.

Why this person's dashboard specifically: data is written to the database of the person whose process accepted
the request, and the token is read from that same person's secrets. The owner's token does not work on the second
person's dashboard, and vice versa — otherwise one phone could write to someone else's database.
