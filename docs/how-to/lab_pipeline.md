[English](lab_pipeline.en.md) · **Русский**

# How-to: конвейер распознавания анализов

Операционный runbook. Все команды — на Studio через `/opt/homebrew/bin/python3.11`
(default python3=3.9 без зависимостей). Канон БД: `~/health/data/health.db`.
Ключ: `~/.health_secrets/anthropic_key`.

Конвейер: `lab_recognizer` (Opus+Sonnet постранично) → `lab_oracles` (верификация)
→ `lab_backfill` (запись в `lab_results_staging`) → `lab_review_sheet`/`lab_staging_summary`
→ `lab_promote` (атомарная замена канона). Канон `lab_results` НЕ трогается до промоута.

При автоматическом приёме итог определяет `lab_intake_watcher._settle_outcomes`
после обоих разборщиков. Ноль строк сам по себе не является сбоем: заключение
врача может законно уйти в медкарту без лабораторных значений. Узкое расхождение —
наличие `.norows` вместе с `doc_outcome(status="imported", kind="lab_result")`.
Только оно создаёт новый `notify.fault` и задачу `type=question`: пришлите значения
текстом или фото крупнее. Дата берётся из `doc["date"]`, неизвестная дата названа
явно. Итог сообщает, что значений нет и придёт отдельный вопрос; очередь бота
доставляет его со своим лимитом. `imported/encounter` не вызывает такой эскалации;
`failed` вместе с `.norows` сохраняет прежний сбой без нового вопроса.
Оракул: `test_zero_rows_queues_question_and_fault_once_without_db`.

Повторные проходы и рестарт используют существующий `outcome_told`; дополнительного
списка отпечатков нет. Явный повтор файла снимает эту отметку по прежнему контракту.
Отметка сохраняется до внешних действий: авария после сохранения может оставить
итог или вопрос недоставленным. Это прежняя граница механизма, не гарантия доставки
«ровно один раз». Уже отмеченные итоги автоматически не переобрабатываются.

## 1. Перераспознать корпус

```bash
# все лаб-документы из JSON-прослойки
python3.11 lab_backfill.py --run-id <run> --max 25
# один документ вне прослойки (clinic-named)
python3.11 lab_backfill.py --run-id <run> --doc-path "<папка>/<документ>.pdf" --date YYYY-MM-DD
```
Долго (постранично, 70-стр. документ ~30 мин). Запускать в фоне: `nohup … &`.
Идемпотентно по (run_id, source_file).

## 2. Посмотреть результат

```bash
python3.11 lab_staging_summary.py --run-id <run> [--doc "полная панель"]
# даты × типы, дедуп, date_source (read/inherited/fallback)
python3.11 lab_backfill_report.py --run-id <run>          # отчёт качества
python3.11 lab_review_sheet.py --run-id <run> --out <path>.html  # лист ручной сверки
```
В листе ревью: 🔴 расхождение моделей, 🟡 оракул, 🔵 один проход. Отметить ошибки
чекбоксом reject → «Экспорт reject-списка» → сохранить JSON.

## 3. Промоут в канон (с гейтами)

```bash
# DRY-RUN (по умолчанию, ничего не пишет): покажет diff и Гейт-2 (полнота)
python3.11 lab_promote.py --run-id <run> [--reject-file rej.json]
# РЕАЛЬНАЯ запись (снапшот + атомарная транзакция)
python3.11 lab_promote.py --run-id <run> --reject-file rej.json --execute
```
Правило: ЗАМЕНИТЬ лаб-строки канона, УДАЛИТЬ клиник-дубли, НЕ ТРОГАТЬ
`instrument:*`. Кровь → `lab_results`; immunoreactivity/microbiome — нет.
`--execute` ОТКАЗЫВАЕТ, если Гейт-2 нашёл старые (date, аналит) без покрытия в новом
(потенциальная потеря) — сперва свести имена в `lab_canon.py` или дозахватить документ.

## 4. Откат

`--execute` делает снапшот `health.db.presnap_<ts>.db` рядом с БД. Откат:
```bash
launchctl stop com.larry.health.bot   # остановить читателей
cp ~/health/data/health.db.presnap_<ts>.db ~/health/data/health.db
launchctl start com.larry.health.bot
```
Проверить целостность: `python3.11 -c "import health_db,sqlite3; sqlite3.connect(health_db.DB_PATH).execute('PRAGMA integrity_check')"`.

## 5. После промоута

Канон изменился (исправлены значения, новые аналиты) → перегенерировать конституции
и ревалидировать корреляции/алерты на новых данных (старые выводы на прежней кривде
невалидны). См. план `iCloud/health/lab_pipeline_workplan_2026-06-30.md`, фаза E.
