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
