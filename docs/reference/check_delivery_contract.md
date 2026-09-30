[English](check_delivery_contract.en.md) · **Русский**

# Контракт доставки проверок

> **Жанр (Diátaxis): reference.** Сухая карта «проверка → канал → читатель → доказывающий тест».
> Назначение: будущий читатель за минуту проверяет, что у каждой автопроверки есть человек, который увидит провал.
> Принцип: проверка без доставленного ответа = false negative на уровне системы (RST, [[Anatomy of a Check]]).
>
> Введён 2026-06-29 по итогам аудита `audit_test_readers` (см. iCloud health/).

## Карта

| Проверка | Триггер | Канал доставки при провале | Читатель | Доказывающий тест |
|----------|---------|----------------------------|----------|-------------------|
| `integrity_tests` FAIL | run_checks --scheduled, 07:50 | Telegram + `/tmp/health_integrity_status` + exit 2 | владелец | `test_integrity_json_valid` |
| `integrity_tests` WARN | 07:50 → `integrity_latest.json` | triage 08:00 → Telegram (**все варны кроме mute**) | владелец | `test_triage_delivery` |
| `pytest tests/` (регрессия) | run_checks --scheduled, 07:50 | Telegram | владелец | — |
| Ночной суит (unit→llm_judge) | test-suite, 00:00 | `test_failure_handler` → Telegram (regression); `morning_test_summary` → секция «Тесты» в утреннем отчёте | владелец | `test_save_agent_report_contract` |
| `oura_freshness_check` | run_checks --scheduled, 07:50 | Telegram (`--notify`) | владелец | `test_oura_freshness_check`, `test_sensors_wired` |
| `model_health_check` | monthly_api_report, 1-е 09:30 | Telegram при отзыве модели | владелец | `test_model_health_check`, `test_sensors_wired` |
| `check_wellally_updates` | wellally-check | Telegram (`--notify`) | владелец | — |
| `uncommitted_watchdog` | hourly | Telegram | владелец | — |
| `arch_guard` | post-commit (Studio) | Telegram при падении (**с 2026-06-29**) | владелец | — |
| `check_contracts`, `smoke_tests` | post-commit (Studio) | Telegram при падении | владелец | — |
| `doc_agent` | post-commit (Studio) | Telegram при падении | владелец | — |
| genome staleness | integrity warn → triage | Telegram (**с 2026-06-29**; раньше фейк-авто-фикс) | владелец | `test_warn_genome_update_delivered_not_autofixed` |
| genome обновление (данные) | bot job, 1-е 05:00, in-process | нарратив значимых изменений → Telegram | владелец | — |

## Mute-список (осознанно НЕ доставляется)

Источник правды: `triage_agent.MUTE_WARN_SUBSTRINGS`. Каждый пункт — с обоснованием, покрыт `test_triage_delivery`.

| Подстрока | Почему немой |
|-----------|--------------|
| `нет steps` | данные шага уже пропущены прошлым — постфактум не actionable |
| `активных протокол` | «нет активных протоколов» — ожидаемое состояние, не дефект (решение владельца 2026-06-29) |
| `без активных эксперимент` | «N гипотез без активных экспериментов» — эксперименты свёрнуты намеренно (BL-EXP-1); гипотезы разрешаются консилиумом (2026-06-30) |

Правило: добавление в mute требует обоснования + строки в тесте. «Прочее → тишина» запрещено.

## Каналы доставки

- **Основной — Telegram.** Доставка warn-уровня (triage) и критических алертов (run_checks)
  идёт через `notify.notify()`: пробует Telegram, при провале (бот лёг/токен/сеть) —
  **резервный пинг `healthchecks.io/fail` с текстом → email** (канал, не зависящий от Telegram).
  Закрывает «бот жив, но отправка не прошла → тихая потеря алерта». См. `notify.py`.
- **Резервный — healthchecks.io (email).** Тот же URL, что у dead-man's switch. Конфляция
  осознанная: Telegram-сбой пометит check down → email; следующий чистый 07:50 вернёт success.
- **Dead-man's switch** пингует success только на чистом 07:50-прогоне → ловит «система молчит».

### Остаточные риски (осознанные ставки)

- **Прямой `curl` без fallback** ещё в `model_health_check`, `oura_freshness_check`,
  `check_wellally_updates` — точечные алерты, не переведены на `notify` (низкий приоритет).
- **`model_health_check` раз в месяц** — латентность до ~30д. Отзыв модели всплывёт и через падения агентов.
- **healthchecks как резерв самого себя** не покрыт: если ляжет и Telegram, и healthchecks — слепота (маловероятно, разные провайдеры).

## Снято / устаревшее

- `com.larry.health.pubmed-check` (03:00) — **снят 2026-06-29** (плист → `.disabled_redundant_*` на Studio). `--pubmed-only` не обрабатывался → гонял весь суит дублируя 07:50, результат выбрасывал в `logs/pubmed_check.log` без доставки. Литературная свежесть покрыта `check_literature_freshness` в 07:50. Возврат: `mv` плист обратно + `launchctl load`.
