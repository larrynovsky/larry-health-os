[English](data_ingestion_paths.en.md) · **Русский**

# Приём данных — пути, условия срабатывания, расписания

> **Тип документа:** Reference (Diátaxis). Факты, а не объяснения.
> Замысел подсистемы — `subsystem_intent.yaml::data_ingestion`.
> Грабли — `lessons.yaml` C-32…C-34 (печатаются в `preflight`).
>
> Переехало из BLUEPRINT (раздел «Подсистема импорта данных») 2026-08-02:
> шесть фактов из шести не имели второго дома
> (замер грепом по `docs/explanation/data_ingestion.md` — там ни одного).

## Где лежат пути

Облачная папка установки — `infra_config.CLOUD_HEALTH_DIR` (ключ `cloud_health_dir` в
`private/infra.yaml`). Модули берут пути внутри неё через `infra_config.cloud_dir(...)`:
`CR/` (бланки, которые владелец кладёт сам), `data/hae_archive/`, `data/Fitdays*.csv`,
`data/reports/`, `reports/`. Ключа нет (новая установка) → те же пути в каталоге данных
процесса (`HEALTH_DATA_DIR`), документы приходят через бота во `incoming/`.
Папка приложения Health Auto Export — `infra_config.HAE_APP_DIR`. Литерал облачного пути вне
`infra_config` и стражей краснит `test_cloud_literal_only_in_home` (с 2026-09-24, BL-PUB-12).

## Оркестратор

`import_all.py`. После успешного импорта каждый модуль вызывает
`health_db.set_import_status(source)` — это conit C1, по нему считается свежесть источника.

## Apple Health (HAE)

Ежедневные JSON лежат в `HAE_DAILY_DIR`:

```
~/Library/Mobile Documents/iCloud~com~ifunography~HealthExport/Documents/Health/
```

Папка `New Automation/` того же контейнера **не используется** — см. C-32.

Архивирование обработанных файлов — `shutil.copy2()` в `hae_archive/`.
`os.rename()` между iCloud-контейнерами не работает (C-33).

**Давление.** В JSON попадает, только если Blood Pressure включён в
Settings → Metrics приложения HAE. Обходной путь — парсинг бинарного `.hae`
от AutoSync: regex по ASCII-маркерам `systol` / `iastol`, диапазон отсечки
60–200 мм рт. ст. (C-34).

### Сон: чтение и поздний экспорт (контракт 2026-11-08)

Владелец — `metrics_db`; `health_db.get_day()` делегирует ему чтение.
Если `raw.sleep` отсутствует, `get_day()` собирает `sleep` из плоских колонок:
длительности в часах, отсутствующие стадии — `None`, источник — `AppleHealth`
при совпадении с Apple-свидетелем, иначе `flat`. Существующий Oura-блок не меняется.

HAE заменяет собственные ранее записанные поля сна; Oura-блок имеет приоритет.
Формат JSON дня не меняется. `migrate_all_json()` передаёт весь файл одним
вызовом upsert: источник Oura при `source=Oura` (файла или блока сна), наличии
sleep `contributors` или одного из ключей `readiness_score`, `resilience`,
`stress`, `hrv_balance`, `body_temperature`, `recovery_index`,
`breathing_disturbance`; иначе AppleHealth. Смешанный файл сохраняет путь Oura.
Уже ошибочно размеченные строки БД автоматически не ремонтируются.

`get_day()` при каждом вызове читает текущую БД. Замена частичного Apple-сна
делает свежие данные видимыми следующему потребителю без флага переоценки,
перезаписи отчёта или автоматической отправки. Ошибка SQLite откатывает запись;
импортёр пишет warning, JSON остаётся для повторного импорта.

Оракул: `tests/integration/test_uc_a_03_hae_merge.py::test_apple_sleep_read_late_update_and_oura_shape`.

### Тренировки с часов (с 2026-11-11)

В HAE одна автоматизация отправляет один тип данных. Метрики и тренировки — две
автоматизации на один и тот же `POST /hae/ingest` с тем же токеном. Пакет тренировок
несёт `data.workouts` (без `metrics`); `import_apple_health.parse_hae_workouts` пишет их в
`workouts` с `source='AppleHealth'`. Приложение Oura само кладёт свои тренировки в
Apple Health — такие записи (источник `com.ouraring.*`) пропускаются, они приходят через
Oura API. Энергия в кДж переводится в ккал; имя устройства не хранится.
Сторож `integrity_tests.check_hae_workouts_automation` (с 2026-11-12): Oura записал тренировку,
автоматизация метрик с тех пор отработала в двух разных часах, а пакета тренировок нет → WARN.
Оракул: `tests/unit/test_activity_temp.py`.

## Oura: длительность тренировок и температура (с 2026-11-11)

- `workout` в API v2 не несёт `duration`: длительность считает писатель
  `workouts_db.upsert_workout` из start/end через `julianday` (UTC, переход на зимнее
  время не сдвигает). История дочинена миграцией `health_db._migrate_workouts_duration`.
- `daily_readiness.temperature_deviation` / `temperature_trend_deviation` (°C от личной
  базы) → колонки `daily_metrics.temp_deviation` / `temp_trend_deviation`. Балл вклада
  `readiness_body_temp` — другая, нелинейная величина.
- Граница сигнала — параметры в `absolute_thresholds` (metric `temp_deviation`, variant `temp_pct`,
  `temp_window_days`, `temp_min_nights`). Сторож `integrity_tests.check_temperature_arrives`: серия ночей
  «сон есть, градусов нет» длиннее самой длинной за год → WARN.

## Лабораторные бланки (JPEG/PDF)

`import_all.py` относит файл к лабораторным, если совпало **≥3 сигналов**:

| Сигнал | Что ищет |
|---|---|
| маркеры показателей | `WBC` / `RBC` / `HGB` |
| числовой диапазон | `\d+\.\d+\s*[-–]\s*\d+\.\d+` |
| источник | имя клиники из `doc_patterns` тенанта |

При совпадении `classify()` возвращает `"lab"` → `lab_extractor.extract_labs()` →
`data/pending_labs/` → карточка в Telegram → после ✅ `health_db.import_from_pending()`
вставляет в `lab_results` с `ON CONFLICT DO UPDATE`.

Ручной ввод (`LAB_DATA` в `import_medical_docs.py`) остаётся запасным путём
для исторических данных без цифрового оригинала.

## Атомарность и устойчивость

- Запись: temp-файл → `os.replace()` (Таненбаум §7.3.1).
- iCloud-evicted файлы пропускаются с warning (graceful eviction), не роняют импорт.

## Расписания вокруг приёма

| Что | Где | Период |
|---|---|---|
| `icloud_conflict_check.sh` | LaunchAgent на MacBook | каждые 6 ч; ищет `*(conflicted copy)*`, пропускает `.DS_Store` и `.tmp_*.json` |
| `watch_and_test.sh` | fswatch на Studio | `*.py`/`*.sh`, debounce 10 с → smoke → integrity |

Полное расписание launchd — `ARCH_SNAPSHOT.md § РАСПИСАНИЕ` (генерируется `gen_schedule.py`).
