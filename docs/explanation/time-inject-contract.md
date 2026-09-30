[English](time-inject-contract.en.md) · **Русский**

# Контракт единого времени (`_time_inject`) — почему и как

> Explanation (Diátaxis). Reference — докстрока `_time_inject.py`; enforcement —
> `time_contract_sensor.py` + чек «контракт единого времени» в `integrity_tests`.

## Проблема

Прод-код читал стенные часы напрямую (`datetime.now()`, `date.today()`,
`datetime.utcnow()`) в 152 местах (78 файлов), из них 49 — на recency-путях
(окна свежести, возраст, «не старше N дней»). Два следствия:

1. **Тесты нельзя заморозить.** Логика, читающая реальный `datetime.now()`,
   не поддаётся `set_test_clock` → тест вынужден хардкодить дату впритык к «сейчас»,
   и **гниёт**, когда календарь её догоняет.
2. **Расщеплённые часы.** Часть кода на seam (`get_now`), часть — на стене. Тест
   морозит seam, прод-модуль живёт на стене → тест зелёный, прод — нет.

**Инцидент 2026-07-21** — оба сразу: `test_calendar_tenant_guard` захардкодил
событие на 2026-07-20; `calendar_client._read_cache` рубил `end < datetime.now()`
в обход seam. 20-го тест был зелёным, 21-го — красным, хотя код не менялся.

## Правило (enforced)

Вся recency-логика агентов читает время ТОЛЬКО через `_time_inject`:

| Было | Стало |
|---|---|
| `datetime.now(tz)` | `get_now(tz)` |
| `date.today()` | `get_today()` |
| `datetime.utcnow()` | `get_utcnow()`  ← **не** `get_now()`: то локальное, сдвиг на tz |

Тест управляет временем через `set_test_clock(...)` / `clear_test_clock()`.

## Исключения

- Строка с маркером `# time-inject: ok` — осознанный прямой вызов (лог, штамп
  `created_at`, точка входа во внешнюю систему).
- Блок `if __name__ == "__main__":` — CLI не обязан ходить через seam.
- Сам `_time_inject.py` (внутри и живёт реальное время).

## Датчик и ратчет

`time_contract_sensor.py` сканирует прод на прямые вызовы вне seam. Гибрид-ратчет:

- сайт в `time_contract_baseline.txt` (унаследованное) → **WARN** — разгребаем батчами;
- сайта нет в baseline (новый / в чистом файле) → **FAIL** — течь не пускаем с 1-го дня.

Мигрировал сайт → удали его ключ из baseline (или `reconcile`: baseline ∩ текущий скан).

Плюс **прод-liveness** `assert_clock_live()`: в живом прогоне `is_frozen()` обязан
быть `False`. Иначе утёкший `set_test_clock` заморозил бы прод-время и **ослепил все
freshness-датчики** (GP, backup-SLA, token-expiry, db_perms) — тот самый класс, что
эти датчики и защищают.

## Как мигрировать сайт (how-to)

1. Импорт: `from _time_inject import get_now, get_today` (или `get_utcnow`).
2. Замени прямой вызов по таблице выше, сохранив tz-семантику.
3. Добавь **двусторонний boundary-тест**: заморозь клок на `порог−ε` (сервит/свежо)
   и `порог+ε` (блок/протухло) при ФИКСИРОВАННОЙ дате данных. Пример:
   `tests/unit/test_calendar_end_boundary.py`.
4. Удали сайт из baseline. Прогони `integrity_tests` — чек «контракт единого времени»
   должен остаться зелёным (FAIL=0).

## Статус (2026-07-21) — worklist ЗАКРЫТ

Все 152 прямых вызова (78 файлов) закрыты: мигрированы на seam ИЛИ помечены
`# time-inject: ok` (dev-скрипты, штампы имён бэкапов). `time_contract_baseline.txt`
**пуст** → ратчет строгий: любой новый прямой вызов вне seam → FAIL в `integrity_tests`.
Проверено: полная регрессия 2641 passed / 0 failed на задеплоенной версии (Studio).

## Покрытие boundary-тестами (2026-07-21)

Recency-сравнения распадаются на КЛАССЫ; каждый класс имеет доказанный
frozen-clock boundary-тест (RED проверен). Дубликаты по-инстансно не плодим —
это счётчик, не риск; новый bypass ловит датчик, поведение — общая регрессия.

| Класс сравнения | Пример сайта | Двусторонний тест |
|---|---|---|
| событие «завершилось» (end<now) | calendar_client | `test_calendar_end_boundary` |
| окно-часы (age ≤ N ч) | ecg_db:80 | `test_ecg_db::nonsinus_filter_and_window` (relative, обе стороны) |
| freshness-потолок (utc − N ч) | patient_context:304 | `test_patient_context_clock::utc_floor` |
| окно-дни (today − N д) | gp_context:544, longitudinal:317, api_hae_ingest:37 | `test_gp_specialist_review_block`, `test_longitudinal_cutoff_clock`, `test_hae_ingest_freshness` |
| days-since / возраст-дни | dashboard_filters:43, patient_context:236 | `test_dashboard_filters_clock`, `test_patient_context_clock` |
| возраст-годы | patient_context:25 | `test_patient_context_clock::age_suffix` |
| метка «сегодня/вчера/N» | patient_context:226 | `test_patient_context_clock::age_label` |

Инстансы тех же классов (≈40: «вчера»-таргеты отчётов, параметрные `today−window_days`
у labs/literature/survivorship, age-расчёты, дедлайны `today+N`) — низкий rot-риск
(окно относительно today, параметризовано), покрыты общей регрессией 2641/0 + датчиком.
Вынос порога в чистый хелпер — если сайт зашит (пример: `longitudinal._recent_cutoff_iso`,
`patient_context._utc_floor_str`).
