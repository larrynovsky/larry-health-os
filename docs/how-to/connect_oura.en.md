<!-- translation-of: docs/how-to/connect_oura.md sha256:0bf7eea0822d -->
**English** · [Русский](connect_oura.md)

# How to connect an Oura ring

> Document type: How-to (Diátaxis). Performed by the person who installed the system, on the primary machine.

The system gets sleep, heart rate variability, and activity from Oura API v2 (`import_oura.py`,
background service `com.larry.health.oura-import`). It needs an Oura personal access token.

1. Get a token in your Oura account (developer section / Personal Access Tokens).
   Check Oura's website for its current location: the account interface changes.
2. Put the token in this person's secrets directory as a single line:

   ```bash
   printf '%s' 'TOKEN' > ~/.health_secrets/oura_token && chmod 600 ~/.health_secrets/oura_token
   ```

   For a second person on the same machine, use their secrets directory (`HEALTH_SECRETS_DIR` in their plists).
3. Start the background import service (the plist from `build/launchd/`, as in the installation tutorial, step 7).
4. Verify: the next morning's report includes sleep from the previous night.

Do not send the token to the bot: Telegram messages are stored on someone else's servers.
