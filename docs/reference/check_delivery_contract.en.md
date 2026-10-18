<!-- translation-of: docs/reference/check_delivery_contract.md sha256:82fddf139a75 -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](check_delivery_contract.md)

# Check Delivery Contract

> **Genre (Diátaxis): reference.** A dry map of "check → channel → reader → proving test".
> Purpose: a future reader can verify in one minute that every automated check has a person who will see the failure.
> Principle: a check without a delivered result = false negative at the system level (RST, [[Anatomy of a Check]]).
>
> Introduced 2026-06-29 following the `audit_test_readers` audit (see iCloud health/).

## Map

| Check | Trigger | Delivery channel on failure | Reader | Proving test |
|----------|---------|----------------------------|----------|-------------------|
| `integrity_tests` FAIL | run_checks --scheduled, 07:50 | Telegram + `/tmp/health_integrity_status` + exit 2 | owner | `test_integrity_json_valid` |
| `integrity_tests` WARN | 07:50 → `integrity_latest.json` | triage 08:00 → Telegram (**all warns except mute**) | owner | `test_triage_delivery` |
| `pytest tests/` (regression) | run_checks --scheduled, 07:50 | Telegram | owner | — |
| Night suite (unit→llm_judge) | test-suite, 00:00 | `test_failure_handler` → Telegram (regression); `morning_test_summary` → "Tests" section in morning report | owner | `test_save_agent_report_contract` |
| `oura_freshness_check` | run_checks --scheduled, 07:50 | Telegram (`--notify`) | owner | `test_oura_freshness_check`, `test_sensors_wired` |
| `model_health_check` | monthly_api_report, 1st 09:30 | Telegram on model deprecation | owner | `test_model_health_check`, `test_sensors_wired` |
| `check_wellally_updates` | wellally-check | Telegram (`--notify`) | owner | — |
| `uncommitted_watchdog` | hourly | Telegram | owner | — |
| `arch_guard` | post-commit (Studio) | Telegram on failure (**from 2026-06-29**) | owner | — |
| `check_contracts`, `smoke_tests` | post-commit (Studio) | Telegram on failure | owner | — |
| `doc_agent` | post-commit (Studio) | Telegram on failure | owner | — |
| genome staleness | integrity warn → triage | Telegram (**from 2026-06-29**; previously fake auto-fix) | owner | `test_warn_genome_update_delivered_not_autofixed` |
| genome update (data) | bot job, 1st 05:00, in-process | narrative of significant changes → Telegram | owner | — |

## Mute list (consciously NOT delivered)

Source of truth: `triage_agent.MUTE_WARN_SUBSTRINGS`. Each entry has a justification and is covered by `test_triage_delivery`.

| Substring | Why muted |
|-----------|--------------|
| `нет steps` | step data already missed in the past — not actionable after the fact |
| `активных протокол` | "no active protocols" — expected state, not a defect (owner decision 2026-06-29) |
| `без активных эксперимент` | "N hypotheses without active experiments" — experiments wound down intentionally (BL-EXP-1); hypotheses are resolved by council (2026-06-30) |

Rule: adding to mute requires a justification + a line in the test. "Everything else → silence" is prohibited.

## Delivery channels

- **Primary — Telegram.** Warn-level delivery (triage) and critical alerts (run_checks)
  go through `notify.notify()`: tries Telegram; on failure (bot down / token / network) —
  **fallback ping `healthchecks.io/fail` with text → email** (a channel independent of Telegram).
  Closes "bot is alive but send failed → silent alert loss". See `notify.py`.
- **Fallback — healthchecks.io (email).** Same URL as the dead-man's switch. The conflation is
  intentional: a Telegram failure marks the check down → email; the next clean 07:50 run returns success.
- **Dead-man's switch** pings success only on a clean 07:50 run → catches "system is silent".

### Residual risks (conscious bets)

- **Direct `curl` without fallback** still present in `model_health_check`, `oura_freshness_check`,
  `check_wellally_updates` — point alerts, not migrated to `notify` (low priority).
- **`model_health_check` once a month** — latency up to ~30d. Model deprecation will also surface through agent failures.
- **healthchecks as its own fallback** is not covered: if both Telegram and healthchecks go down — blind spot (unlikely, different providers).

## Removed / deprecated

- `com.larry.health.pubmed-check` (03:00) — **removed 2026-06-29** (plist → `.disabled_redundant_*` on Studio). `--pubmed-only` was not handled → ran the full suite duplicating 07:50, discarded results to `logs/pubmed_check.log` with no delivery. Literature freshness is covered by `check_literature_freshness` in the 07:50 run. To restore: `mv` the plist back + `launchctl load`.
