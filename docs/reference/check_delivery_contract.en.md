<!-- translation-of: docs/reference/check_delivery_contract.md sha256:1888b1c67806 -->
**English** · [Русский](check_delivery_contract.md)

# Check delivery contract

> **Genre (Diátaxis): reference.** A terse map of “check → channel → reader → proving test.”
> Purpose: a future reader can verify in a minute that every automated check has a person who will see a failure.
> Principle: a check without a delivered result = a false negative at the system level (RST, [[Anatomy of a Check]]).
>
> Introduced on 2026-06-29 following the `audit_test_readers` audit (see iCloud health/).

## Map

| Check | Trigger | Delivery channel on failure | Reader | Proving test |
|----------|---------|----------------------------|----------|-------------------|
| `integrity_tests` FAIL | run_checks --scheduled, 07:50 | Telegram + `/tmp/health_integrity_status` + exit 2 | owner | `test_integrity_json_valid` |
| `integrity_tests` WARN | 07:50 → `integrity_latest.json` | triage 08:00 → Telegram (**all warnings except muted ones**) | owner | `test_triage_delivery` |
| `pytest tests/` (regression) | run_checks --scheduled, 07:50 | Telegram | owner | — |
| Nightly suite (unit→llm_judge) | test-suite, 00:00 | `test_failure_handler` → Telegram (regression); `morning_test_summary` → “Tests” section in the morning report | owner | `test_save_agent_report_contract` |
| `oura_freshness_check` | run_checks --scheduled, 07:50 | Telegram (`--notify`) | owner | `test_oura_freshness_check`, `test_sensors_wired` |
| `model_health_check` | monthly_api_report, 1st of the month 09:30 | Telegram when a model is retired | owner | `test_model_health_check`, `test_sensors_wired` |
| `check_wellally_updates` | wellally-check | Telegram (`--notify`) | owner | — |
| `uncommitted_watchdog` | hourly | Telegram | owner | — |
| `arch_guard` | post-commit (Studio) | Telegram on failure (**since 2026-06-29**) | owner | — |
| `check_contracts`, `smoke_tests` | post-commit (Studio) | Telegram on failure | owner | — |
| `doc_agent` | post-commit (Studio) | Telegram on failure | owner | — |
| genome staleness | integrity warn → triage | Telegram (**since 2026-06-29**; previously a fake autofix) | owner | `test_warn_genome_update_delivered_not_autofixed` |
| genome update (data) | bot job, 1st of the month 05:00, in-process | narrative of significant changes → Telegram | owner | — |

## Mute list (deliberately NOT delivered)

Source of truth: `triage_agent.MUTE_WARN_SUBSTRINGS`. Each item has a rationale and is covered by `test_triage_delivery`.

| Substring | Why it is silent |
|-----------|--------------|
| `нет steps` | step data was missed in the past — not actionable after the fact |
| `активных протокол` | “no active protocols” is an expected state, not a defect (owner's decision, 2026-06-29) |
| `без активных эксперимент` | “N hypotheses without active experiments” — experiments were deliberately discontinued (BL-EXP-1); hypotheses are resolved by the consilium (2026-06-30) |

Rule: adding an item to mute requires a rationale + a line in the test. “Everything else → silence” is prohibited.

## Delivery channels

- **Primary — Telegram.** Delivery of warn-level results (triage) and critical alerts (run_checks)
  goes through `notify.notify()`: it tries Telegram; on failure (bot down/token/network), it sends a
  **backup `healthchecks.io/fail` ping with text → email** (a channel independent of Telegram).
  Covers “the bot is alive, but sending failed → silent loss of an alert.” See `notify.py`.
- **Backup — healthchecks.io (email).** The same URL as the dead-man's switch. The conflation
  is intentional: a Telegram failure marks the check down → email; the next clean 07:50 run restores success.
- **The dead-man's switch** pings success only on a clean 07:50 run → catches “the system is silent.”

### Residual risks (deliberate bets)

- **Direct `curl` without fallback** remains in `model_health_check`, `oura_freshness_check`,
  and `check_wellally_updates` — individual alerts not migrated to `notify` (low priority).
- **`model_health_check` once a month** — latency up to ~30 days. Model retirement will also surface through agent failures.
- **healthchecks as its own backup** is not covered: if both Telegram and healthchecks go down, there is no visibility (unlikely, different providers).

## Removed / outdated

- `com.larry.health.pubmed-check` (03:00) — **removed on 2026-06-29** (plist → `.disabled_redundant_*` on Studio). `--pubmed-only` was not handled → ran the entire suite, duplicating 07:50, and discarded the result into `logs/pubmed_check.log` without delivery. Literature freshness is covered by `check_literature_freshness` at 07:50. Restore: `mv` the plist back + `launchctl load`.
