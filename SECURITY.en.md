<!-- translation-of: SECURITY.md sha256:367015318d15 -->
**English** · [Русский](SECURITY.md)

# SECURITY POLICY: Larry Health OS
**Current as of:** 2026-05-11 (after the migration to Studio + Tailscale).
**Next audit:** 2026-08-11 (quarterly).

> The historical part of the audit (SEC-01..SEC-15, the VPS era before 2026-04-09):
> `SECURITY_AUDIT_LOG.md` (the log is kept in the private part of the project).

> The security audit is run with: `python3.11 check_contracts.py`
> (checks the structure) + a manual read of this file.

---

### SEC-12: dashboard write-guard (2026-05-22, v2.3)
A fail-fast was added to the lifespan: the dashboard refuses to start in write mode on non-primary hosts without an explicit `ALLOW_WRITE_NONPRIMARY=1`. This removes the split-brain via iCloud Sync — 31 POST endpoints could write into the iCloud copy of the DB when accidentally started on the MacBook, leading to unauthorized leakage/corruption of medical data. Closes F-111 / Sprint 5 step 1.

---

### SEC-13: bot/filters.py — fail-closed owner check at startup and on access

`owner_chat_id()` reads and caches the ID lazily, on first access; a missing
`telegram_chat_id` in the directory chosen by `secrets_paths.secrets_dir()` raises `RuntimeError`.
Importing `bot/filters.py` itself does not check that this file exists. On startup `bot/main.main()`
first calls `assert_owner_configured()`: without a configured owner the bot does not start
polling Telegram. The access filter also uses `owner_chat_id()` and does not grant access
when the ID is missing (checked against the code on 2026-09-29).

---

### SEC-14: handlers/callbacks.py — the UC-I-02 fail-closed owner check (OWNER_CHAT_ID) is duplicated inline in callback_doc_review and cb_router_with_owner_check, because CallbackQueryHandler does not support filters=; any foreign chat → silent return without revealing callback_data.

---

### SEC-15: the Tailscale address (<studio_host>) and the Funnel URL moved to infra_config.py — network constants are now in one place, lowering the risk that the bind address and the public endpoint diverge.

---

### SEC-16: doctor-brief reads the Telegram token and chat_id from `~/.health_secrets/telegram_token` / `~/.health_secrets/telegram_chat_id` and passes them via `subprocess.run(curl ...)`. **Vulnerability:** a token in command-line arguments is visible in `ps`. **Measures:** (1) permissions 700 on the `~/.health_secrets` directory, 600 on the files; (2) the token is not logged and does not get into the HTML response; (3) passing the token via stdin or an environment variable instead of CLI args is recommended.

---

### SEC-17: Audit 2026-07-02 (WSTG) — backup permissions, plist keys, a neighbour project's bind

- **F-2 [[WSTG-ATHZ-01]]:** daily sqlite backups (`~/health/backups`, `<neighbour>/backups`) and the partner tenant's DB were created `644` (world-readable) while the canon is `600`. A one-off `chmod 600` on all existing ones; the durable fix is `chmod 600` in `backup_studio.sh` (health + a neighbour project). Sensor for the quarterly checklist: `find ~/health*/backups ~/health*/data -name '*.db' ! -perm 600` → 0.
- **F-3 [[WSTG-CRYP-04]]:** `<neighbour-label-prefix>.api.plist` (permissions `644`) contains `ANTHROPIC_API_KEY`/`GEMINI_API_KEY` in the clear → `chmod 600`. Full move of the keys to `<neighbour-secrets>` + **rotation** — deferred (the owner does it separately, BL-003).
- **F-5 [[WSTG-CONF-05]]:** the neighbour project's Flask listened on `0.0.0.0:5001` (LAN+tailnet, bypassing the gateway allow-list) → bind `127.0.0.1`. Outside only via serve/gateway. Verified: funnel/serve `/mm` = 302, direct tailnet `:5001` = 000. Remainder: `jarvis_mini` `:18573`, SMB `:445`, VNC `:5900` are still on `*` (a Studio-local project / macOS services) — a recommendation, not done.
- **F-1:** dashboards `:8001`/`:8002` without auth — **deliberately left**: the tailnet is a family one, cross-tenant viewing is not considered a threat (owner's decision 2026-07-02). Build isolation if untrusted nodes appear in the tailnet.

---

### SEC-18: quarterly checklist → nightly sensors (2026-07-06)

The manual checklist items were moved into code: `security_sensors.py` (4 categories:
DB/backup permissions, secret permissions, non-loopback listeners, hard-coded tokens) →
`integrity_tests.py §[12]` → WARN → triage → Telegram. **Burn-in:** until promotion
to FAIL all findings are WARN (≥7 clean days). The allowlists are in `security_sensors.py`,
each item with a justification. Tests: `tests/unit/test_security_sensors.py`
(including a delivery test through the triage mute list).

Findings of the first calibration (live run 2026-07-06, before the sensor was deployed):

- **Pre-op snapshots 644.** F-2 fixed permissions in `backup_studio.sh`, but ad-hoc
  snapshots (`cp` before migrations: `health_pre_*`, `pgs_reference.before_*`)
  are created with the umask → 644. A one-off `chmod 600`; the durable protection is the sensor itself
  (fix-at-generation-point for a dozen different writers is not worth it — accepted).
- **`~/.health_secrets` was 755** while the canon is 700 (SEC-16 declared 700,
  in fact 755 — nobody checked). Fixed with `chmod 700`.
- Token scan over the repo: **0 matches** — the ratchet baseline is zero.
- Ports: no surprises; allowlist = AirPlay 5000/7000, a neighbour project's local helper 18573,
  dashboards 8001/8002; ephemeral ≥49152 are ignored.

### SEC-21: secret_guard — secret values do not leave the machine (2026-07-06)

`secret_guard.py` scans outgoing text for the VALUES of secrets from the tenant's
`secrets_dir()` and returns only file NAMES (the AI-rules tripwire: the value
does not get even into the alert). Fail-closed: a guard failure = the send is blocked.
The first consumer is `doc_agent.analyze_diff` (commit diffs → Claude API);
the guard stands before the api_key is read. Acceptance: a training detonation on 2026-07-06 — the API call
was blocked, the Telegram alert was delivered. Closes BL-SECRETS-LLM-1.
Deliberately not covered: hai_* and lab_recognizer (images) — see BACKLOG.

---

⚠️ A note on the earlier wording «outside — via Tailscale Funnel :8000»
(section «WHAT IS ALREADY GOOD»): a live check on 2026-07-06 showed **Funnel on :443
is off** (tailnet-only Serve); only `:10000` → the neighbour project gateway is public.
The exposure canon and the sensor are stage 3 of the plan `plan_agents_md_adoption_2026-07-06.md`.

---

### SEC-20: exposure as a contract (2026-07-06)

A doc split-brain was found: BLUEPRINT (section «Architecture») and this file claimed «outside via
Tailscale Funnel → :8000», while in fact (live 2026-07-06) Funnel on `:443` was
OFF — tailnet-only Serve; only `:10000` → the neighbour project gateway is public.
Both docs erred toward more exposure; nobody compared the text with
`tailscale serve status`.

Fix by the «a duplicated value = split-brain» pattern: the canon is `infra_config.py`
(`EXPECTED_FUNNEL_PORTS/HANDLERS`, `EXPECTED_SERVE_*`), the docs refer to it;
the nightly sensor `security_sensors._tailscale_exposure` compares the live
`tailscale serve status --json` ↔ the canon. Unparsed output = red
(«the sensor is blind»), not green. The public surface (`:10000`) is compared
STRICTLY (paths and backends), tailnet Serve — against the set of allowed
loopback backends. Tests: test_security_sensors (funnel-on-443,
an extra public path, a backend outside the canon, garbage output).

A side finding → TD-09 (see «Open questions»).

---

### SEC-22: Telegram delivery completeness sensor (multitenancy::delivery_coverage_open)
`test_telegram_delivery_coverage.py` enumerates all direct `sendMessage` channels and requires each one to take the recipient from `secrets_dir()`/`get_chat_id()` or to be deliberately listed in `ADMIN_GLOBAL_TG_ALLOW` (`secrets_paths.py`). Closes the third gap of the bug class of 2026-07-01 (the partner's hypotheses went to the owner): the earlier sensors `check_contracts` and `test_secrets_single_resolver` let through a new channel with a hard-coded recipient or a non-tenant env.

---

### SEC-23: env_sources.py reads the aqicn token from ~/.health_secrets/aqicn_token; the token is not written into code/logs, the cache is kept in ~/.health_secrets/_env_cache/ — the directory must have permissions 700.

---

### SEC-24: /location/ingest is protected by a Bearer token from the file location_ingest_token (HEALTH_SECRETS_DIR); missing file → 503, wrong token → 401; the X-API-Key header is also supported.

---

### SEC-25: Multi-tenant guard for Google Calendar (Bug 2, 2026-07-14)
**Class:** Leakage of personal data between tenants (cross-tenant).
**Vulnerability:** The partner's token authorized someone else's Google account (both caches contained the owner's email); `secrets_dir()` is fail-closed on the directory but blind to «the right directory — someone else's account in the token».
**Fix:** `google_calendar_fetcher` writes `account` (the email of the primary calendar) into the cache; `calendar_client._verify_tenant_identity()` compares it with the expected account from `secrets_dir/google_calendar_account` — a mismatch → fail-closed (empty list) + `log.error` + `notify`.
**Components:** `google_calendar_fetcher.py`, `calendar_client.py`, `tests/unit/test_calendar_tenant_guard.py`.
**Severity:** High — one tenant's personal events were visible in another's brief.

---

### SEC-26: food_quarterly — the Telegram token and chat_id are read from secrets_dir(); the profile file is written to /tmp and must be deleted after sending (data leakage with a shared /tmp).

---

### SEC-27: fix(bot/filters): removed a fail-open when OWNER_CHAT_ID=None — the old code `if OWNER_CHAT_ID is not None and id != OWNER_CHAT_ID` let anyone in when None; the new owner_chat_id() RAISES when the secret is missing, the auth check always blocks when resolution is unavailable (fail-closed).

---

### SEC-28: Removed a cross-tenant leakage of one tenant's genotype (WSTG-ATHZ-04). The constant `_CPIC_DRUG_GENE` contained one person's phenotype for one gene and returned it to all tenants as if it were their own data. Fix: the phenotype-diplotype was removed from the canon; addressing is by phenotype class; the concrete phenotype is taken from the viewing tenant's `pharmaco_phenotypes` per process. File affected: cpic_reference_db.py (new), replacing the hard-coded constant in the dashboard/consilium.

---

### SEC-29: pgs_discovery — the reference DB snapshot sets permissions 600 (os.chmod) right after sqlite3.connect, before the data are backed up; removes the window where the file is visible via the umask (class db_perms).

---

### SEC-30: media_intake — protection against a pixel bomb and GPS leakage via EXIF
WSTG-BUSL-09. `sanitize_and_store`: 25 MB input limit, pixel-bomb guard (MAX_PIXELS=40 Mpx),
EXIF strip via paste into a clean `Image` (body/GPS metadata are not kept),
file name = sha256 of the source bytes (path traversal from the TG name is excluded).

---

### SEC-31: D2 (2026-07-23) — `secrets_dir()` moved to `is_owner()` as the single source of the owner signal. An inline debt was removed: earlier the check `dd and Path(dd).name != "health"` was duplicated independently of `is_owner()`, which created a risk of logic divergence and a silent cross-tenant leakage of the owner's secrets. Now both paths use one helper; fail-closed behaviour is confirmed by 4 unit tests (`test_d2_*`).

---

### SEC-32: fix(doc_review): prevented leakage of the owner's documents to the partner — the same relative `source_file` could resolve to the owner's folder for a tenant; the attachment is now sent only when `is_owner()==True` (fail-closed, §13 step 2).

---

### SEC-33: Closed a hole where the canonical DB could be replaced by a hard link — `on_canonical_db` now compares file identity by `(st_dev, st_ino)`, not only by the resolved path; `None is not None` prevents a false positive when there is no canon.

---

### SEC-34: fix(notify/routing) — service notifications leaked to a tenant. `notify.notify()` took the recipient from `secrets_dir()` (per tenant), and the partner's watcher sent review requests to the partner themselves for three days — 19278 messages (01.08). `notify_operator()` with an explicit `owner_secrets_dir()` was introduced; all service calls (`lab_intake_watcher`, `safety_net`) were moved to the new channel. Boundary: a tenant's medical data must not be sent to the operator channel.

---

### SEC-35: the autouse fixture `_no_real_anthropic` prevents leakage of medical data into the real Anthropic API during a test run — it patches the `anthropic.Anthropic` and `anthropic.AsyncAnthropic` constructors at the package level.

---

### SEC-35: a single home for egress to the LLM API + a guard by construction (2026-08-03)

`llm_client.get()` is the only point where the Anthropic client is constructed; the key is read in one place (there were 27 copies of the path in 21 modules). The guard sits on `messages.create` and scans text blocks, including `system`; images are not scanned by design. **Guard blindness is separated from a finding** (`GuardUnavailable` versus `SecretLeakBlocked`): earlier «could not run» and «found a secret» were returned as one value, which with 20 consumers would have made diagnosis impossible. Blocks are journaled (`logs/llm_guard_blocks.log`, file names without values) and counted by a nightly sensor — otherwise a block would be silent.
**The boundary, named explicitly:** the key is not per tenant (the owner's path), and the guard scans the CURRENT tenant's directory — so a leak of the owner's key through the partner's path is not detected. Owner's decision: do not extend the scan to someone else's directory; isolation (SEC-31) is worth more than coverage. The ratchet `check_llm_direct_constructors` (baseline 27) keeps the number of bypasses from growing; migration on touch.
Along the way: the watchdog heartbeat moved from `~/.health_secrets/` to `logs/` — its ISO timestamp was becoming a guard «needle»; the mute in `security_sensors._SECRETS_FILE_ALLOW` was removed, the secrets directory contains only secrets.

---

### SEC-36: llm_client — a guard on outgoing text by construction
`guarded_client()` wraps `messages.create` with a call to `secret_guard.find_secret_values` before every send to the Anthropic API. Guard blindness (`GuardUnavailable`) and a secret finding (`SecretLeakBlocked`) are separated into different exception types — they cannot be confused in handling. Blocks are written to `logs/llm_guard_blocks.log` (file names, not values; SEC-21). The API key is read in a single place (`llm_client._KEY_FILE`). The ratchet `check_llm_direct_constructors` (baseline 27) forbids new direct `Anthropic`/`AsyncAnthropic` constructors that bypass the factory. The blindness boundary is named explicitly: the owner's key is not scanned in the partner's process — extending it would break the SEC-31 isolation.

---

### SEC-37: test `test_secrets_dir_755_flagged_and_loose_file_too` — the secrets allowlist was cleaned after the heartbeat moved to `logs/`; any file in `.health_secrets` with permissions ≠ 600 is now flagged unambiguously as a finding, without exceptions.

---

### SEC-38: Auto-repair of permissions on .db files (§13 step 1)
`repair_db_perms()` in `security_sensors.py` — a machine chmod 600 on `.db` files with excessive permissions before findings are collected. The §13 envelope: the safe predicate is total and mechanical (`mode & 0o077`), no data are lost (permissions are tightened, the operation is reversible), every repair is logged in `logs/security_repairs.log` (`_sec_log`). 27 CVEs in dependencies were closed. Covered by the unit tests `test_db_perms_repair.py` (4 cases: repair, idempotence, perimeter, the §13(d) trace).

---

### SEC-39: Tenant gate in resolve_document (2026-08-09)
`_assert_belongs_to_tenant` blocks processing of a document outside the current process's `HEALTH_DATA_DIR` on all resolve branches (absolute path, relative path, glob by name). Before the fix, a partner's lab form parsed without `HEALTH_DATA_DIR=~/health_partner` silently landed in the owner's canon; the promotion then overwrote the owner's rows with someone else's values. Affected: confidentiality (someone else's data in the canon) and integrity (correct rows overwritten). A refusal, not a warning.

---

### SEC-40: The «document → LLM prompt» boundary in `labs_db.result_text`
Raw text from the `value_text` field (including rows from the second writer `doc_reviews_db`, which does not go through the promotion vocabulary) no longer gets into the doctor's prompt line. The function `result_text` returns either a number, or a word from the fixed vocabulary `_QUAL_RU`, or «н/д (см. источник)» — preventing injection of document content into the model's instruction (the prompt injection class, CLAUDE.md §19). The test `test_reader_never_leaks_raw_document_text` verifies the boundary on three samples, including a free-form string and a construction with a control instruction.

---

### SEC-41: llm_guard — the production block journal (`llm_guard_blocks.log`) was polluted with test artifacts for 42 days (168 lines — exclusively mock findings `anthropic_key`/`oura_token`); no real blocks were recorded in the period. Fixed: the path is resolved at call time via `_block_log_path()`, the env `HEALTH_LLM_GUARD_LOG` isolates tests in `tmp_path`; the autouse fixture `_no_real_guard_journal` protects all future tests.

---

### SEC-42: The perimeter of the cross-tenant leakage guards was extended to shell channels (*.sh); a secret's class is declared in SECRET_SCOPE, not by a predicate of three names; the permissions sensor _secrets_perms became recursive (blind spot: 12 files 644 in a subdirectory); the env cache and the uncommitted_watchdog state were moved out of ~/.health_secrets to ~/.health_cache/env and logs/ respectively.

---

### SEC-43: Centralization of the Anthropic key and protection of the location cache (2026-09-02)
The hard-coded `~/.health_secrets/anthropic_key` was removed from 12+ modules; the single access point is `llm_client.api_key()`. The `env_sources` cache directory (it contains coordinates) was moved to permissions 700/600 by an explicit `chmod` on every access — earlier, after the move out of the secrets directory, the directory was created 755/644. A dead-secrets detector was added to `check_contracts.py`: a secret declared in `SECRET_SCOPE` but not mentioned in any file of the repository raises a WARN asking for a check and revocation.

---

### SEC-44: notify.email_owner — SMTP header injection
`email_owner` takes `subject` from model output (`night_investigator`). CR/LF are cut out via `re.sub(r"[\r\n]+"…)` before being passed to `EmailMessage`, which prevents header injection (WSTG-INPV-10). Credentials (smtp_password) do not get into logs or exception text — smtplib does not include them in the error message (§19). The channel is fail-closed: missing secrets disable sending without an exception.

---

### SEC-45: The live-model lane in run_probes.sh — isolation of anthropic_key

`scripts/run_probes.sh` got a separate `LIVE_PROBES` lane with an isolated secrets directory `$STAGING/.secrets_live` (permissions 600), into which **only** `anthropic_key` is copied. All other tokens (telegram_token, oura_token, chat_id etc.) stay fake (`STAGING_DUMMY`), excluding accidental sending of real data. The precedent of the cost is documented in the code: 01.08, 19 278 messages to the partner from a process that «should not» have sent them. The directory is created and destroyed within one run of the script.

---

### SEC-46: smtp_host / smtp_user / smtp_password / smtp_port / email_to were added to `SECRET_SCOPE` with scope `owner` — the system's mail notification channel (BL-STALLED-THREADS-1, 14.09).

---

### SEC-47: The secret-reader scanner was extended with the «variable / "name"» form
The literal scanner `referenced_secret_names` did not see the pattern `s = secrets_dir(); s / "smtp_host"`, so the WARN «secrets without a reader» falsely called five active secrets dead. The function `referenced_via_variable` was added with two narrowings: the file must contain a marker of working with the secrets directory, and the name must be declared in the registry. Without the narrowings the form caught 77 pairs outside the registry and could hide a really dead secret. After the fix the WARN points to exactly one secret without a reader — `read_token` (dead since April 2026). A negative test guarantees that softening the scanner does not hide dead secrets.

---

### SEC-48: Legalizing port 8443 in the Serve map (a neighbour project)
**Date:** 2026-09-21 | **Version:** 15.26 | **File:** `infra_config.py`
**Essence:** `EXPECTED_SERVE_PORTS` was extended to `{443, 8443, 10000}`, `EXPECTED_SERVE_BACKEND_PORTS` — to `{3000, 5001, 5002, 9001}`. Port 8443 (tailnet-only) serves the neighbour project's relay (`127.0.0.1:3000`) and the mobile client pairing endpoint `/pair` (`127.0.0.1:5002`); active since 2026-09-17, the sensor signalled «drift» for four nights until it was added to the canon.
**Owner's decision:** recorded in `~/.infrastructure.md` (addendum 2026-09-17/21) and the neighbour project's plan.
**Test:** `test_tailscale_unexpected_endpoint_and_garbage` was switched — 8443 is now canon, 8444 is detected as drift.

---

### SEC-49: hae-ingest — a per-tenant authorization token
Until 23.09, `/hae/ingest` read the token from the hard-coded path `~/.health_secrets/hae_ingest_token` (owner level). The second person's phone could authorize only with the owner's token — or could not log in at all. Fixed: `_token_path()` calls `secrets_dir()` from `secrets_paths.py`; `hae_ingest_token` was moved to scope `tenant`. Each person uses their own token; the owner's token gives no access to someone else's dashboard. Test: `tests/unit/test_hae_ingest_tenant_token.py`.

---

### SEC-50: port 8003 was added to EXPECTED_SERVE_BACKEND_PORTS — the neighbour project's web page (stdlib http.server); reachable only via tailnet-only Serve :8443/cost; loopback-only, not exposed outside; owner's decision 2026-09-26, see a decision in the neighbour project.

---

### SEC-51: Telegram bot token in process argv — conftest._no_real_importers trims the URL to its last segment (rsplit) before writing it to the attempts log (§19); deliver food_quarterly is silenced by a separate fixture so the secret does not reach the teardown assert

---

### SEC-52: The first-contact smoke test answered the onboarding home question with coordinates near the owner's home — replaced with neutral ones (Berlin, the time zone from the tutorial example). Caught by the pre-export read; the census dictionary did not see this form of writing.

---

## WHAT IS ALREADY GOOD ✅ (current state)

- `anthropic_key`, `oura_token`, `telegram_token` — permissions 600, owner only.
- Secrets are kept in `~/.health_secrets/`, not in git.
- `.gitignore` excludes `.db`, `.log`, `__pycache__`.
- The production DB on Studio is in `~/health/data/health.db`, permissions 600.
- Tailscale exposure: the canon is `infra_config.py` (EXPECTED_FUNNEL_*/EXPECTED_SERVE_*),
  live comparison nightly (SEC-20). ONLY `:10000` → the neighbour project gateway is public; `:443` is
  tailnet-only Serve. (The earlier text «outside via Funnel :8000» was wrong —
  live check on 2026-07-06.)
- The Telegram bot accepts commands only from `OWNER_CHAT_ID` via the `filters.Chat`
  whitelist (UC-I-02 contract test).
- The single-primary rule for health.db: writes only from Studio (`_is_primary()`
  guard in `health_db.get_conn`, hostname-aware default since 2026-05-11).
- Access to Studio only via the Tailscale VPN — no public ports.

---

## OPEN QUESTIONS

- ⬜ `TD-08`: CORS `*` in the legacy VPS API — decommissioned together with the VPS itself, but left
  in the code of vps_sync.py (DEPRECATED 2026-04-09). On final removal —
  remove the mentions.
- ⬜ A pre-commit hook on Studio for drift checks (P3-4 in Wave 3-DOC v2).
- ✅ `TD-09` CLOSED (2026-07-06, owner's decision: kill the mini app): the `/` route
  was removed from Serve, 8000 was removed from EXPECTED_SERVE_BACKEND_PORTS, the `/app` command and
  the Mini App button were cut out of the bot. Along the way `check_pending_consults`
  (VPS era) was removed: for 3 months it silently caught a failure every 2 minutes (log.DEBUG) and sent
  `X-Sync-Token` to a neighbour project endpoint. The showcase is the dashboard :8001.

---

## QUARTERLY CHECKLIST

At every audit check:
- [ ] Run the full test suite: `bash run_full_test_suite.sh` (must be exit=0).
- [ ] Run the contract check: `python3.11 check_contracts.py`.
- [x] ~~Hard-coded tokens~~ → **automatic since 2026-07-06** (SEC-18: `security_sensors`, nightly). Manually — only an allowlist review.
- [x] ~~Secret permissions~~ → **automatic since 2026-07-06** (SEC-18, nightly).
- [x] ~~DB/backup permissions~~ → **automatic since 2026-07-06** (SEC-18, nightly; also covers the `health_partner` tenant).
- [x] ~~Listeners~~ → **automatic since 2026-07-06** (SEC-18: `lsof -nP -iTCP -sTCP:LISTEN` — NOT `ss`, it does not exist on macOS; the earlier item with `ss` was not executable in this form). Manually — only a review of `_LISTEN_ALLOW`.
- [ ] Live Tailscale exposure matches the canon (`tailscale serve status`) — manually until stage 3 of the agents_md_adoption plan.
- [ ] Review the allowlists in `security_sensors.py` — is each item still justified?
- [ ] Promote the SEC-18 burn-in sensors WARN→FAIL if ≥7 clean days (one-off).
- [ ] Update the next audit date in the header of this file.
- [ ] Check the open tasks (⬜) above.
