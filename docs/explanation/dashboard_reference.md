[English](dashboard_reference.en.md) · **Русский**

# Dashboard — Reference

Что сейчас работает (на 2026-05-15, Wave 6 Phase 1+2).
Reference-документ: факты без обоснований. Обоснования — в `docs/explanation/dashboard.md`. Изменения — `docs/how-to/extend_dashboard.md`.

## Где живёт

- Хост: Studio (`<studio_host>`)
- Порт: `8001`
- Binding: `<studio_host>:8001` (Tailscale interface only — не `0.0.0.0`)
- Запуск: `~/Library/LaunchAgents/com.larry.health.dashboard.plist` (launchd, `KeepAlive=true`)

**Связанные launchd-агенты (создают/анализируют dashboard-данные):**
- `com.larry.health.consilium` (1-го числа 04:00) — `monthly_consilium.py`, рождает гипотезы
- Процесс: `/opt/homebrew/bin/python3.11 ~/health_scripts/dashboard.py`
- Логи: `~/health_scripts/logs/dashboard.{out,err}.log`

## Технологии

- Web: FastAPI + Starlette `TemplateResponse(request, name, ctx)`
- Templates: Jinja2
- БД: SQLite read-only (`file:{DB_PATH}?mode=ro` URI)
- Markdown render: `python-markdown` 3.10.2, extensions `extra/tables/toc`
- Фронт: server-side render, без JS-framework, без build-step

## Routes

| Path | Шаблон | Источник данных |
|---|---|---|
| `/` | `home.html` | counts из 11 таблиц, weekday/today_meta, epigraph из `patient_profile.identity.epigraph` |
| `/profile` | `profile.html` | `patient_profile` сгруппирован по `category` |
| `/hypotheses` | `hypotheses.html` | `memory WHERE category='hypothesis'`, JSON-payload в `value` |
| `/protocols` | `protocols.html` | `protocols`, sorted `status DESC, created_at DESC` |
| `/tasks` | `tasks.html` | `tasks WHERE status IN (open, snoozed, answered)` |
| `/problems` | `problems.html` | `problem_list`, sorted active→watch→other |
| `/labs` | `labs.html` | `lab_results` — latest per test + recent 50 |
| `/constitutions` | `constitutions_index.html` | `~/health_scripts/constitutions/*.md` |
| `/constitutions/{slug}` | `constitution_view.html` | один `.md` → HTML через `python-markdown` |

## Шаблоны

- `base.html` — shell (header + sidebar + content + footer), включает `sidebar.html`
- `sidebar.html` — partial с 4 группами: «сегодня · управляю · слушаю · справочник». Активный пункт по `request.url.path.startswith(...)`
- 13 page-templates выше extends `base.html`

## Дизайн-система Toscana

Источник правды: `~/health/dashboard-design/` (вне репо, в iCloud)
- `tokens.css` — палитра, типографика, spacing, тени, радиусы
- `dashboard.css` — компоненты (shell, sidebar, card, chip, dense, kv, timeline, narrative)
- `how-to.md`, `reference.md`, `explanation.md`, `tutorial.md` — документация системы

В репо: `dashboard_static/tokens.css` (копия) + `dashboard_static/dashboard.css` (реализация компонентов под Toscana).

### Используемые компоненты по страницам

| Страница | Компонент |
|---|---|
| `/profile` | `.kv` rows + `.caption(category)` группы |
| `/hypotheses`, `/protocols`, `/problems`, `/tasks` | `.card` с модификаторами `--warning`/`--action`/`--active` |
| `/labs` | `.dense` таблицы |
| `/events` | `.timeline` (год слева + точки разных цветов) |
| `/constitutions/{slug}` | `.narrative` (max 65ch, CSS-dropcap через `::first-letter`) |
| `/constitutions` | `.const-list` |
| `/` | `ul.counts` |

## Cache-bust

- `templates.env.globals["css_version"] = int(mtime(dashboard.css))` устанавливается при загрузке `dashboard.py`
- `base.html` линкует `<link href="/static/dashboard.css?v={{ css_version }}">`
- Перезапуск процесса (launchctl reload) подхватывает новый mtime автоматически

## Filters / globals

Jinja:
- `age_days(iso_date)` — дни от даты до сегодня
- `short(text, n=120)` — обрезает строку
- `pretty_json(s)` — формат JSON с отступами

Globals:
- `css_version` — int mtime
- `request` — Starlette request (нужен для `request.url.path` в sidebar)

## Источники в БД

| Таблица | Назначение | Дашборд использует |
|---|---|---|
| `patient_profile` | KV-факты о пациенте | `/profile`, `epigraph` на `/` |
| `memory (category='hypothesis')` | гипотезы | `/hypotheses` |
| `protocols` | поведенческие протоколы | `/protocols` |
| `tasks` | задачи (от агента / вручную) | `/tasks` |
| `problem_list` | активные проблемы | `/problems` |
| `periods` | клинические фазы | `/events` (timeline) |
| `context_events` | события дня (legacy, не пополняется с март 2026) | `/events` (тихая лента внизу) |
| `lab_results` | результаты анализов | `/labs` |

## Write endpoints (Phase 3)

Все POST endpoints используют helper `_w()` с `PRAGMA busy_timeout=5000`.
Каждая мутация логируется в `dashboard_edits` через `_log_edit()`.

| Endpoint | Действие | Backend |
|---|---|---|
| `GET /api/profile/{key:path}/edit` | Открыть inline-edit | swap на input/textarea |
| `POST /api/profile/{key:path}` | Сохранить value_text/value_json | UPDATE patient_profile, audit |
| `POST /api/hypotheses/{id}/confirm` | Подтвердить + создать task из payload.test | UPDATE memory active=0, INSERT INTO tasks |
| `POST /api/hypotheses/{id}/reject` | Отвергнуть | UPDATE memory active=0, resolution_type |
| `POST /api/protocols/{id}/retire` | Прекратить протокол | UPDATE protocols status=retired |
| `POST /api/problems/{id}/{archive\|reopen}` | Снять с наблюдения / вернуть | UPDATE problem_list status |
| `GET /api/tasks/{id}/edit` | Inline-edit content | swap на input |
| `POST /api/tasks/{id}` | Сохранить content | UPDATE tasks |
| `POST /api/tasks/{id}/done` | Выполнено | UPDATE tasks status=completed |
| `POST /api/tasks/{id}/snooze?days=N` | Отложить (3/7/30 или 1..365) | UPDATE tasks deadline=today+N |
| `GET /api/ping` | HTMX smoke | возвращает chip pong |

**Cascade rules:**
- `confirm hypothesis` с непустым `payload.test` → INSERT в `tasks` (source=dashboard_confirm, type=followup, priority=medium)
- snooze automatically re-emerges: `/tasks` route фильтрует `WHERE status=open OR (status=snoozed AND deadline <= today)`. Не нужен фоновый cron.

## Audit log

Таблица `dashboard_edits` (создана миграцией при boot dashboard.py):

- `id` AUTOINCREMENT
- `ts` default datetime now
- `entity` — patient_profile / memory / protocols / tasks / problem_list / experiments (до 2026-07-10) / consultations / periods
- `entity_id` TEXT (для profile это `key`, для остальных — INTEGER as string)
- `action` — edit_field / confirm / reject / retire / archive / reopen / complete / cancel / done / snooze / create_from_hypothesis_confirm
- `field`, `old_value`, `new_value`, `actor` (default `dashboard`)

Просмотр: `/audit` с фильтром через `?entity=tasks`.

Rollback: вручную через SQL по `old_value` из audit row.

## Dependency notes (Phase 3)

- **python-multipart 0.0.28** — обязательная для FastAPI `Form(...)` parsing. Установлено через `pip install --quiet python-multipart`.
- **htmx.min.js 1.9.10** — self-host в `dashboard_static/`. Cache-bust по mtime через `templates.env.globals["js_version"]`.
- **python-markdown 3.10.2** — для рендера `.md` конституций (Phase 2).

## Tests (Wave 9, 2026-05-16)

Phase 3.3H закрыт: 53 integration tests на live tmp-БД через TestClient.

**Запуск:**

```bash
cd ~/health_scripts
/opt/homebrew/bin/python3.11 -m pytest tests/integration/test_dashboard_*.py -q
```

**Файлы:**

| Файл | Тестов | Что покрывает |
|---|---|---|
| `test_dashboard_smoke.py` | 22 | Все 13 GET routes → 200, /api/ping, edge cases (400/404) |
| `test_dashboard_profile_edit.py` | 4 | value_text round-trip, value_json validation, 404 |
| `test_dashboard_hypothesis_cascade.py` | 7 | **Cascade confirm→task (главный)**, idempotency, audit |
| `test_dashboard_tasks.py` | 7 | done, snooze, **auto-reawake (главный)**, edit content |
| `test_dashboard_status_actions.py` | 13 | protocol retire, problem archive/reopen, experiment complete/cancel, consultations/periods edit, audit page |

**Fixture:**

- `dashboard_client(db)` — `TestClient(app)` + tmp SQLite (через fixture `db`)
- Builders в `tests/fixtures/db.py`: `add_profile`, `add_hypothesis`, `add_task`, `add_protocol`, `add_experiment`, `add_consultation`, `add_period` (+ existing `add_daily_metrics`, `add_lab_result`, `add_problem`, etc.)

**Время прогона:** ~1.5 сек на весь набор (53 теста).

**Что ловят регрессии:**
- Изменение шаблона ломает GET route → smoke падает
- Изменение `_q`/`_w` API ломает write-side → profile/cascade tests падают
- Изменение CBCR-формата `payload.test` ломает cascade → hypothesis_cascade падает
- Изменение `/tasks` route фильтра ломает snooze → tasks tests падают

## Известные ограничения

- Никаких write-операций пока (read-only БД, нет POST endpoints) — Phase 3 в роадмапе
- Google Fonts через CDN — единственный внешний fetch (Newsreader / Inter / JetBrains Mono)
- `today_meta` показывает только дату, без «<N>д после окончания схемы» — опорные даты не задействованы
- Mobile-адаптация — каркасная (sidebar становится inline-block), не tabbar из дизайна
- Дроп-кап в конституциях через `::first-letter` срабатывает только если первый элемент `<p>` (если markdown начинается с `<h1>` — дроп-капа не будет)

## Версия

- Phase 1+2 (read-only Toscana): `b60ba83`..`629392a`
- Phase 3 (write + cascade): `daac515` (3A) → `30cba11` (3B) → `e1f0814` (3C) → `95b87e6` (3D) → `70e92ef` (3C-extend + 3E) → `834c452` (3F + 3G)
- Закрыто 7/8 этапов. 3H (integration tests) отложен — нужен рефактор `DB_PATH` для test-fixture.
- Стабильно на `2026-05-16`
