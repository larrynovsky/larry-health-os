[English](dashboard_phase3_plan.en.md) · **Русский**

# Dashboard Phase 3 — Edit plan

Design-doc для редактирования полей и status-actions. После реализации → конвертировать в `docs/how-to/edit_dashboard_data.md`.

## Статус (2026-05-16)

**Закрыто 8/8 этапов:**

- ✅ 3A — HTMX + `_w()` infra + `dashboard_edits` миграция + `/api/ping`
- ✅ 3B — /profile inline-edit (value_text + value_json с JSON-валидацией)
- ✅ 3C — /hypotheses confirm/reject
- ✅ 3C-extend — confirm с непустым `payload.test` → INSERT в tasks (cascade из observations #2.1)
- ✅ 3D — protocols retire / problems archive-reopen / experiments complete-cancel
- ✅ 3E — tasks inline-edit + done + snooze (+3/+7/+30д) с auto-reawake через route-фильтр
- ✅ 3F — consultations (key_findings textarea, specialist_name input) + periods (notes input, tags запятая→JSON)
- ✅ 3G — /audit страница с фильтром по entity
- ✅ 3H — закрыт через Wave 9 (53 integration tests PASS, commits `195d2d3` + `a9309df`)

**UX-решения 2026-05-15 (применены):**
- done = 1 клик, resolved_text пустой
- snooze = пресеты +3д / +7д / +30д
- tags = запятая с парсингом
- cascade confirm→task = в Phase 3

**Production state:** 14 write endpoints + /audit. HTMX self-host. python-multipart 0.0.28. dashboard_edits audit log.

---

## Scope (по результатам обсуждения 2026-05-15)

**Inline-edit полей** (значения меняются):
- `/profile` — `patient_profile.value_text` / `value_json`
- `/tasks` — `tasks.content`, `tasks.resolved_text`
- `/consultations` — `consultations.key_findings`, `consultations.specialist_name`
- `/events` — `periods.notes`, `periods.tags`

**Status-actions** (записи не редактируются, только меняется состояние):
- `/hypotheses` — кнопки «принять» / «отвергнуть» → меняет `memory.active` + дописывает `payload.resolution_type`
- `/problems` — «снять с наблюдения» / «возобновить» → меняет `problem_list.status`
- `/protocols` — «retire» → меняет `protocols.status='retired'` + `retired_at`
- `/experiments` — «завершить успехом» / «отменить» → меняет `experiments.status` + `result`

Read-only остаются: `/patterns` (исторический лог, не редактируется), `/labs` (внешние данные), `/constitutions` (регенерируются скриптом).

## Проверки SQLite (выполнены 2026-05-15)

Перед планированием реализации сделана разведка БД. Результаты ниже — теперь конкретика, не предположения.

**Текущая конфигурация `health.db`:**
- `journal_mode=wal` ✅ — writer не блокирует readers (read-only connection из дашборда продолжит работать во время writes)
- `synchronous=1` (NORMAL) ✅ — оптимально для WAL
- `busy_timeout=0` ❌ — при contention SQLITE_BUSY возвращается мгновенно

**Что это значит для Phase 3:**
Каждый write-connection в `dashboard.py` обязан установить `PRAGMA busy_timeout=5000` (5 секунд) сразу после `connect()`. Это закрывает класс багов «`morning_report` пишет → пользователь жмёт confirm в дашборде → 500 ошибка». С таймаутом — `dashboard.py` подождёт до 5 сек, потом получит блокировку.

**Audit log:**
Таблицы `audit_log` или `dashboard_edits` нет. `agent_reports` (есть в БД) — специализированная: обязательные поля `agent_type`, `agent_name`, `data_queried`, `pubmed_ids`, `peers_reviewed`, `findings`, `recommendations`. Помещать туда «dashboard edit field X» = нарушение семантики (agent_type принимает значения `lifestyle|specialist|therapist|synthesizer`, дашборд — не агент).

Решение: **новая таблица `dashboard_edits`** — простой audit log своя.

**Schema observations (для Phase 3 endpoints):**

| Таблица | Колонки для edit | Tracking |
|---|---|---|
| `patient_profile` | `value_text`, `value_json` | `updated_at`, `updated_by` — заполняем `'dashboard'` |
| `memory` (hypothesis) | `value` (JSON-string), `active` | `updated_at` есть. Для confirm/reject — парсим `value` → мутируем `payload.resolution_type` → сериализуем `json.dumps(ensure_ascii=False)` |
| `protocols` | `status`, `retired_at` | tracking через `dashboard_edits` |
| `tasks` | `content`, `status`, `resolved_at`, `resolved_text`, `resolution_type` | resolution_type для done = `'completed_via_dashboard'` |
| `problem_list` | `status`, `last_updated` | `last_updated` уже NOT NULL — обновляем |
| `experiments` | `status`, `result`, `end_date` | tracking через `dashboard_edits` |
| `consultations` | `key_findings`, `specialist_name` | ❌ нет `updated_at` — audit только через `dashboard_edits` |
| `periods` | `notes`, `tags` | ❌ нет `updated_at` — audit только через `dashboard_edits` |

## Архитектурный контракт

### Stack

- **HTMX** для inline-edit и POST-actions. Не jQuery, не Alpine, не React. HTMX — это «server-side rendering с маленькими интерактивностями»: декларативные `hx-post` атрибуты в HTML, сервер возвращает HTML-фрагмент, HTMX подменяет блок.
- **Без модалок**. Кнопка «принять» сразу шлёт POST и подменяет карточку на закрытую. Inline-edit — клик по значению → input → blur → POST → значение обновлено.
- **CSRF не используем.** Trust boundary = Tailscale-сеть. POST endpoints не выставлены за пределы Tailscale, CSRF-токен не добавляет защиты (атакующий внутри mesh уже имеет доступ к терминалу). Это design choice, не упущение.
- **Read+Write connections раздельно.** Сейчас `_q()` использует read-only URI. Для writes — отдельная функция `_w(sql, params)` с обычным connection + `PRAGMA busy_timeout=5000`.

### Concurrent writes

WAL включён → readers не блокируются writers. Дашборд читает (для `_q()`) и пишет (для `_w()`) в одной БД параллельно с `morning_report`, `gp_agent`, `consilium` и др. `busy_timeout=5000` даёт 5 сек на ожидание блокировки. Это покрывает 99% контеншена для single-user системы.

Если SQLITE_BUSY всё-таки возник через 5 сек — endpoint возвращает 503 + HTMX откатывает UI с error chip «БД занята, попробуй ещё». Это лучше, чем тихое поражение.

### Контракт endpoint'ов

`POST /api/<entity>/<id>/<action>` либо `PATCH /api/<entity>/<id>` для inline-edit полей.

Возвращает: HTML-фрагмент с обновлённой карточкой / строкой. HTMX подменяет блок с `id="entity-{id}"`.

Все endpoints логируют write в **`dashboard_edits`** (новая таблица из 3A.2) — для audit trail кто что когда правил.

## Этапы реализации

### 3A: Фундамент (HTMX + write-инфра + миграция) (~1 час)

#### 3A.1 HTMX self-host

1. `wget https://unpkg.com/htmx.org@1.9.10/dist/htmx.min.js -O ~/health_scripts/dashboard_static/htmx.min.js`
2. `base.html`: `<script src="/static/htmx.min.js?v={{ js_version }}"></script>`
3. `templates.env.globals["js_version"] = int(mtime(htmx.min.js))` рядом с `css_version`
4. Smoke: `<button hx-post="/api/ping" hx-swap="outerHTML">ping</button>` → endpoint возвращает `<span>pong</span>`

#### 3A.2 Write-инфраструктура

```python
def _w(sql, params=()):
    """Write connection — busy_timeout=5000, корректное закрытие."""
    c = sqlite3.connect(str(DB_PATH))
    c.execute("PRAGMA busy_timeout=5000")
    try:
        cur = c.execute(sql, params)
        c.commit()
        return cur.lastrowid
    finally:
        c.close()
```

Использовать в каждом POST-endpoint. Никогда не открывать connection с `mode=ro` для write.

#### 3A.3 Миграция `dashboard_edits`

```sql
CREATE TABLE IF NOT EXISTS dashboard_edits (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT DEFAULT (datetime('now')),
    entity        TEXT NOT NULL,    -- patient_profile|memory|protocols|tasks|...
    entity_id     TEXT NOT NULL,    -- INTEGER as TEXT (профиль использует key)
    action        TEXT NOT NULL,    -- edit_field|confirm|reject|retire|...
    field         TEXT,             -- которое поле меняли (для inline-edit)
    old_value     TEXT,             -- что было
    new_value     TEXT,             -- что стало
    actor         TEXT DEFAULT 'dashboard'
);
CREATE INDEX idx_dashboard_edits_entity ON dashboard_edits(entity, entity_id);
CREATE INDEX idx_dashboard_edits_ts     ON dashboard_edits(ts);
```

Helper:
```python
def _log_edit(entity, entity_id, action, field=None, old=None, new=None):
    _w("""INSERT INTO dashboard_edits(entity, entity_id, action, field, old_value, new_value)
          VALUES (?, ?, ?, ?, ?, ?)""",
       (entity, str(entity_id), action, field, old, new))
```

Запускается из миграционного скрипта `scripts/migrations/2026-05-15_dashboard_edits.py` один раз; idempotent через `IF NOT EXISTS`.

**Риск self-host vs CDN:** выбран self-host чтобы Tailscale-only был чистым (никаких внешних fetch для core-функциональности). Google Fonts остаются исключением — пациент не отозвал согласие на Google.

### 3B: Profile inline-edit (~2-3 часа)

#### 3B.1 Шаблон с двумя режимами `.kv-value`

```html
<td class="kv-value"
    hx-get="/api/profile/{{ r.key }}/edit"
    hx-trigger="click"
    hx-swap="outerHTML">
  {{ r.value_text or r.value_json }}
</td>
```

При клике подменяется на `<td><input ... hx-post="/api/profile/{key}" hx-trigger="blur"></td>`. После blur сервер возвращает обратно `<td class="kv-value">новое значение</td>`.

#### 3B.2 Endpoints

```python
import html as _html

@app.get("/api/profile/{key}/edit", response_class=HTMLResponse)
def profile_edit_field(key: str):
    rows = _q("SELECT value_text FROM patient_profile WHERE key=?", (key,))
    if not rows: raise HTTPException(404)
    current = rows[0]["value_text"] or ""
    return (f'<td class="kv-value kv-value--editing">'
            f'<input name="value" value="{_html.escape(current)}" '
            f'hx-post="/api/profile/{key}" hx-trigger="blur,keyup[key==&quot;Enter&quot;]" '
            f'hx-swap="outerHTML" autofocus></td>')

@app.post("/api/profile/{key}", response_class=HTMLResponse)
def profile_save_field(key: str, value: str = Form(...)):
    old_rows = _q("SELECT value_text FROM patient_profile WHERE key=?", (key,))
    old = old_rows[0]["value_text"] if old_rows else None
    _w("""UPDATE patient_profile
          SET value_text=?, updated_at=datetime('now'), updated_by='dashboard'
          WHERE key=?""", (value, key))
    _log_edit("patient_profile", key, "edit_field", field="value_text", old=old, new=value)
    return (f'<td class="kv-value" '
            f'hx-get="/api/profile/{key}/edit" hx-trigger="click" hx-swap="outerHTML">'
            f'{_html.escape(value)}</td>')
```

#### 3B.3 Edge cases

- **`value_json` (структурированный JSON)**: для них поднимаем `<textarea>` вместо `<input>`. Валидация — `json.loads()` на сервере, если не парсится → 422 + красный текст ошибки.
- **Пустое значение**: разрешено, UPDATE с NULL.
- **Escape**: всегда через Jinja `{{ }}` или Python `html.escape()`.

#### 3B.4 Audit log

Внутри `profile_save_field` уже вызывается `_log_edit("patient_profile", key, "edit_field", field, old, new)` — это пишет в `dashboard_edits`. Никаких отдельных шагов не нужно.

### 3C: Status-actions для hypotheses (~1.5 часа)

#### 3C.1 Кнопки на карточке

```html
<article class="card card--warning" id="hyp-{{ h.id }}">
  ...
  <footer class="card-foot">
    <button class="action-pill"
            hx-post="/api/hypotheses/{{ h.id }}/confirm"
            hx-target="#hyp-{{ h.id }}"
            hx-swap="outerHTML">принять</button>
    <button class="action-pill action-pill--ghost"
            hx-post="/api/hypotheses/{{ h.id }}/reject"
            hx-target="#hyp-{{ h.id }}"
            hx-swap="outerHTML">отвергнуть</button>
  </footer>
</article>
```

CSS-добавка: `.action-pill--ghost { background: transparent; color: var(--color-text-hint); border: 0.5px solid var(--color-border-soft); }`

#### 3C.2 Endpoints

```python
@app.post("/api/hypotheses/{id}/{action}", response_class=HTMLResponse)
def hyp_action(request: Request, id: int, action: str):
    if action not in ("confirm", "reject"):
        raise HTTPException(400)
    
    # Найти запись
    row = _q("SELECT value FROM memory WHERE id=?", (id,))[0]
    payload = json.loads(row["value"]) if row["value"] else {}
    payload["resolution_type"] = "confirmed" if action == "confirm" else "rejected"
    payload["resolved_by"] = "dashboard"
    payload["resolved_at"] = datetime.now().isoformat()
    
    _w("""UPDATE memory SET active=0, value=?, updated_at=datetime('now')
          WHERE id=?""",
       (json.dumps(payload, ensure_ascii=False), id))
    _log_edit("memory", id, action, field="payload.resolution_type",
              old=row.get("value"), new=payload["resolution_type"])
    
    # Вернуть закрытую версию карточки
    return templates.TemplateResponse(request, "_hypothesis_card.html", {
        "h": {**row, "payload": payload, "active": False},
    })
```

Для этого нужен partial `_hypothesis_card.html` — одна карточка, без обёрток. `hypotheses.html` его include'ит в цикле.

#### 3C.3 Resolution_type семантика

Дискуссия методологии CBCR (см. RTFM):
- `confirmed` — гипотеза подтвердилась данными
- `rejected` — данные опровергли
- `needs_specialist` — требует консультации (уже есть в payload)
- `superseded` — заменена более точной гипотезой

Поэтому кнопки — только `confirm` / `reject`. `needs_specialist` ставится автоматически из CBCR-pipeline.

### 3D: Status-actions для protocols/problems/experiments (~1.5 часа)

Идентичный паттерн как 3C, но с разными actions:

| Entity | Actions | SQL |
|---|---|---|
| `protocols` | `retire` | `UPDATE protocols SET status='retired', retired_at=datetime('now') WHERE id=?` |
| `problems` | `archive` (active/monitoring → resolved) / `reopen` (resolved → active) | `UPDATE problem_list SET status=?, last_updated=datetime('now') WHERE id=?` |
| `experiments` | `complete` / `cancel` | `UPDATE experiments SET status=?, result=?, end_date=date('now') WHERE id=?` |

⚠️ **Перед началом 3D**: проверить какие значения `status` есть в `problem_list` сейчас: `SELECT DISTINCT status FROM problem_list`. Возможные кандидаты — `active`, `monitoring`, `resolved`, `watch`. Решить какие переходы валидны (для UI «снять с наблюдения» = `monitoring→resolved` или `active→resolved`? оба?). Не из памяти — из реальных данных.

Partials: `_protocol_card.html`, `_problem_card.html`, `_experiment_card.html`. Каждый action вызывает `_log_edit(entity, id, action, ...)`.

### 3E: Tasks edit (~1 час)

Tasks особенные: `content` редактируется, плюс есть status-actions (`done` / `snooze`).

Inline-edit `content` — как профиль, но через `<textarea>` для длинных текстов.

Status-actions:
- `done` → `UPDATE tasks SET status='completed', resolved_at=datetime('now'), resolution_type='completed_via_dashboard', resolved_text=? WHERE id=?` (resolved_text может быть пустой строкой при простом «выполнено», или содержать ответ если есть)
- `snooze` → `UPDATE tasks SET status='snoozed' WHERE id=?` + опционально `deadline` через mini-form

### 3F: Consultations / Events (~1 час)

Те же inline-edit на полях `key_findings` / `specialist_name` (consultations) и `notes` / `tags` (periods). Никаких status-actions.

⚠️ Обе таблицы (`consultations`, `periods`) **не имеют `updated_at`** — единственный audit будет в `dashboard_edits`. Это явный trade-off: не делаем миграцию схемы (она дороже), полагаемся на dashboard_edits. Если когда-то потребуется глобальный `last_modified` — добавляется отдельной миграцией.

`periods.tags` хранятся как JSON-string (`'["oncology","exampla"]'`). Inline-edit через `<input>` — пользователь видит сырой JSON. Это не лучший UX, но честный. Альтернативы (tag-picker UI) — отложить до Phase 4.

### 3G: Audit & rollback (~0.5 часа)

Audit уже реализован в 3A.3 (миграция `dashboard_edits`) и автоматически вызывается из каждого endpoint через `_log_edit(...)`. На этом этапе — добавить страницу `/audit` для просмотра timeline:

```python
@app.get("/audit", response_class=HTMLResponse)
def audit(request: Request):
    rows = _q("""SELECT ts, entity, entity_id, action, field, old_value, new_value
                 FROM dashboard_edits ORDER BY ts DESC LIMIT 200""")
    ...
```

Шаблон — `.dense` таблица с фильтром по entity через query-параметр. Rollback на уровне UI **не делаем** (это даст возможность пользователю стрелять себе в ногу) — если что-то правится не туда, читать `dashboard_edits` и откатывать вручную.

### 3H: Tests (~1-2 часа)

Минимум:
1. **Endpoint smoke**: каждый POST endpoint возвращает 200 на валидном input и 4XX на невалидном
2. **DB invariant**: после `confirm` гипотезы — `active=0` AND `payload.resolution_type='confirmed'`
3. **HTMX integration**: один Playwright-тест (или curl-based mock) что после клика карточка действительно подменяется

Тесты добавить в `tests/integration/test_dashboard_actions.py`.

## Риски и неопределённости

| Риск | Вероятность | Mitigation |
|---|---|---|
| HTMX cache в Safari (старый JS) | средняя | Cache-bust JS через mtime, как CSS |
| Конфликт writes с `morning_report` | низкая | SQLite WAL, retry через busy_timeout |
| `value_json` валидация не покрывает все edge-cases | средняя | На сервере `json.loads()`, при ошибке 422 + красная подсветка |
| Inline-edit textarea на длинных конституциях не работает | high | Конституции read-only — это правильно (регенерируются скриптом) |
| Пользователь случайно «принимает» гипотезу кликом | средняя | Confirm-dialog в HTMX (`hx-confirm="Точно принять?"`) для status-actions |
| Memory `category='hypothesis'` имеет старые гипотезы без payload | high | Endpoint должен парсить graceful — если payload=`{}`, всё равно работает |

## Решения пользователя (2026-05-15)

Перед стартом Phase 3 закрыты:

1. **После confirm/reject карточка уезжает в архив.** Сразу исчезает из основного списка, появляется в секции «архив» внизу. Реализуется через `active=0` (для memory) или `status='resolved'/'retired'/'cancelled'/'completed'` (для остальных).
2. **Confirm-dialog: браузерный `confirm()`** через `hx-confirm="Точно?"`. Уродливо, но работает. Каждая status-кнопка получает этот атрибут.
3. **`confidence` НЕ меняется** после confirm/reject. Только `payload.resolution_type` + `active=0`.
4. **`problem_list.status` фактические значения:** `resolved` (архив), `active_monitoring` и `watchful_waiting` (активные). Переходы: archive → `resolved`. Reopen → `active_monitoring` (более внимательный режим).
5. **HTMX SRI-hash не добавляем.** Self-host на той же машине — SRI не защищает от внутренней подмены.

## Открытые неопределённости

Что было неясно до — что прояснено разведкой:

1. ~~WAL включён?~~ → **Закрыто.** `journal_mode=wal`, `synchronous=1`. Но `busy_timeout=0` — нужно явно ставить `PRAGMA busy_timeout=5000` в `_w()`.

2. ~~`agent_reports` для audit?~~ → **Закрыто.** Семантически не подходит (agent_type ограничен `lifestyle|specialist|therapist|synthesizer`). Создаём отдельную `dashboard_edits` миграцией в 3A.3.

3. ~~Схемы редактируемых таблиц?~~ → **Закрыто.** Все 8 таблиц проверены (см. таблицу в начале документа). Главное открытие: у `consultations` и `periods` нет `updated_at` — audit только через `dashboard_edits`.

Все 5 закрыты решениями выше (см. §«Решения пользователя 2026-05-15»). К старту Phase 3 неопределённостей > 0.1 нет — оставшиеся открытые вопросы методологически малы (форма UI-чипа «принято», текст в `hx-confirm`).

## Что не делаем в Phase 3

- Создание новых записей (только edit existing). Создание гипотез / задач — через Telegram-бота или внешние скрипты.
- Удаление записей. Только soft-delete через `active=0` или `status='archived'`.
- Bulk-операции. Один клик = одна запись.
- Undo (кроме git revert на серверной стороне для каких-то экстремальных случаев).
- Auth/CSRF/rate-limit. Trust boundary = Tailscale.

## Definition of done

- [ ] HTMX подключён, smoke-тест passing
- [ ] `_w()` использует `PRAGMA busy_timeout=5000`
- [ ] `dashboard_edits` таблица создана миграцией, `_log_edit()` helper готов
- [ ] `/profile` — inline-edit `value_text` и `value_json` работает, audit пишется
- [ ] `/hypotheses` — кнопки confirm/reject работают, карточка переезжает в архив
- [ ] `/protocols` — retire работает
- [ ] `/problems` — archive/reopen работает (поверх фактических значений `status` из БД)
- [ ] `/experiments` — complete/cancel работает
- [ ] `/tasks` — edit content + done/snooze работают
- [ ] `/consultations` — edit key_findings, specialist_name
- [ ] `/events` — edit notes, tags
- [ ] `/audit` — страница просмотра timeline правок
- [ ] Integration tests в `tests/integration/test_dashboard_actions.py` — 8+ tests, все PASS
- [ ] Документация конвертирована: этот файл → `docs/how-to/edit_dashboard_data.md`
- [ ] Audit-trail работает: `SELECT * FROM dashboard_edits ORDER BY ts DESC LIMIT 10` показывает реальные правки
- [ ] CLAUDE.md правило #1 соблюдено: все ssh-edits закоммичены немедленно

## Очерёдность работы

Рекомендуемая последовательность (можно делать в любой, но эта минимизирует риск):
1. **3A** (HTMX setup) — фундамент
2. **3B** (profile inline-edit) — самый простой случай, отшлифовать паттерн
3. **3C** (hypotheses confirm/reject) — отшлифовать status-actions
4. **3D** (protocols/problems/experiments) — копия паттерна 3C
5. **3E** (tasks) — комбинация inline-edit + status
6. **3F** (consultations/events) — копия паттерна 3B
7. **3G** (audit) — параллельно с 3B
8. **3H** (tests) — после 3D, чтобы покрыть critical path

## Оценка времени

Суммарно: 8-10 часов работы. Можно разбить на 3 сессии:
- Сессия 1: 3A + 3B + 3C — фундамент + первый рабочий feature
- Сессия 2: 3D + 3E — статусные действия для остальных
- Сессия 3: 3F + 3G + 3H — добивка + тесты + документация
