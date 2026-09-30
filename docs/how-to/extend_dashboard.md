[English](extend_dashboard.en.md) · **Русский**

# How-to — Extend Dashboard

Конкретные операции: добавить страницу, изменить компонент, задеплоить, откатить.
Reference (что есть) — `docs/reference/dashboard.md`. Почему так — `docs/explanation/dashboard.md`.

## Добавить новую страницу

Пусть надо `/medications` — список текущих препаратов из таблицы `medications`.

### 1. Route

```python
# dashboard.py
@app.get("/medications", response_class=HTMLResponse)
def medications(request: Request):
    rows = _q("""
        SELECT id, name, dose, frequency, started_at, active
        FROM medications
        WHERE active = 1
        ORDER BY started_at DESC
    """)
    return templates.TemplateResponse(request, "medications.html", {
        "request": request,
        "medications": [dict(r) for r in rows],
    })
```

### 2. Шаблон

```html
{# dashboard_templates/medications.html #}
{% extends "base.html" %}
{% block title %}Препараты — Health OS{% endblock %}
{% set page_name = "препараты" %}

{% block header %}
<div class="shell-title-block">
  <h1>Препараты</h1>
  <p class="shell-subtitle">{{ medications | length }} активных</p>
</div>
{% endblock %}

{% block content %}
{% for m in medications %}
  <article class="card card--action">
    <div class="card-head">
      <div class="card-meta">№ {{ m.id }} · с {{ m.started_at }}</div>
      <div class="card-chips">
        <span class="chip chip--action">{{ m.frequency }}</span>
      </div>
    </div>
    <h3 class="card-title">{{ m.name }} <small>{{ m.dose }}</small></h3>
  </article>
{% endfor %}
{% endblock %}
```

### 3. Sidebar (опционально)

В `sidebar.html` добавить ссылку в правильную группу (вероятно «управляю»):

```html
<a class="sidebar-item {% if path.startswith('/medications') %}sidebar-item--active{% endif %}" href="/medications">
  Препараты
  {% if counts %}<span class="sidebar-count">{{ counts.medications_active }}</span>{% endif %}
</a>
```

### 4. counts.* (если нужно на главной)

В `dashboard.py` функция `home()`:

```python
counts = {
    ...
    "medications_active": _q("SELECT COUNT(*) AS n FROM medications WHERE active=1")[0]["n"],
}
```

И в `home.html`:

```html
<li><a href="/medications">Препараты</a><b>{{ counts.medications_active }} активных</b></li>
```

### 5. Commit на MacBook и Deploy

Все правки — в рабочей копии MacBook. Для работы длиннее одного коммита сначала создай дерево через `scripts/thread_start.sh <slug>` и работай в `~/.worktrees/health_scripts/<slug>`: [порядок работы](thread_worktree.md).

```bash
# В рабочей копии MacBook:
git add dashboard.py dashboard_templates/medications.html dashboard_templates/sidebar.html dashboard_templates/home.html
git commit -m 'feat(dashboard): /medications page' -- dashboard.py dashboard_templates/medications.html dashboard_templates/sidebar.html dashboard_templates/home.html

# Если работа шла в дереве нити — закрыть из главной копии:
cd ~/health_scripts
scripts/thread_finish.sh <slug>
```

Прямой коммит в `main` запускает post-commit: `git push studio main` и рестарт служб. Коммит в ветке не деплоит; `thread_finish` сам гонит полный прогон на Studio, сливает ветку и вызывает тот же хук.

### 6. Smoke-check

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://<studio_host>:8001/medications
# Должно быть 200
```

### 7. Проверить доставку (§12)

```bash
git fetch studio
git log studio/main   # содержит проверяемый коммит?
```

## Изменить токен дизайна

Все токены в `dashboard_static/tokens.css`. Источник правды — `~/health/dashboard-design/tokens.css` (iCloud).

Например, изменить `--color-warning`:

```css
:root {
  --color-warning: #E04B30;  /* было #D85A3E */
}
```

Workflow:
1. Правишь сначала в `~/health/dashboard-design/tokens.css` (источник)
2. Копируешь в свою рабочую копию MacBook: `cp ~/health/dashboard-design/tokens.css dashboard_static/tokens.css`
3. Коммитишь на MacBook; прямой коммит в `main` деплоит через post-commit, нить закрываешь через `scripts/thread_finish.sh <slug>` из главной копии.
4. CSS не требует reload — cache-bust сработает только если `dashboard.css` обновился. Чтобы новый mtime tokens.css был виден — нужен либо `launchctl reload`, либо `touch dashboard.css` (поднимет его mtime).
5. Проверяешь доставку: `git fetch studio && git log studio/main` (§12).

## Добавить новый CSS-компонент

Например, `.metric-block` для отображения одной метрики с трендом.

1. Добавить в `~/health/dashboard-design/dashboard.css` (источник)
2. Скопировать в свою рабочую копию MacBook: `cp ... dashboard_static/dashboard.css`
3. После правок — деплой через коммит в `main` на MacBook; для нити — `scripts/thread_finish.sh <slug>` из главной копии.
4. Никакого reload — Jinja templates auto-reload, css cache-bust по mtime сработает на следующий запрос
5. Использовать в шаблоне: `<div class="metric-block">...</div>`
6. Проверить доставку: `git fetch studio && git log studio/main` (§12).

Контракт:
- Только токены внутри (`var(--...)`)
- Никаких `#XXXXXX` и числовых px-литералов кроме отступов
- Имя класса — `kebab-case`, модификаторы `--state`



## Добавить write-endpoint (после Phase 3)

Шаблон для нового endpoint, который меняет данные:

```python
@app.post("/api/<entity>/{id}/<action>", response_class=HTMLResponse)
def api_<entity>_<action>(id: int):
    # 1. Read current (для idempotency + audit old_value)
    rows = _q("SELECT <field> FROM <table> WHERE id=?", (id,))
    if not rows:
        raise HTTPException(404, f"<entity> {id} not found")
    if rows[0]["<field>"] == "<terminal_state>":
        return HTMLResponse("")  # idempotent — уже сделано

    old = rows[0]["<field>"]

    # 2. Update (use _w(), not _q())
    _w("UPDATE <table> SET <field>=?, ... WHERE id=?", (new_value, id))

    # 3. Audit log
    _log_edit("<table>", id, "<action>", field="<field>",
              old_value=old, new_value=new_value)

    # 4. Return для HTMX swap
    return HTMLResponse("")  # пусто → hx-swap=delete уберёт элемент
```

HTML-кнопка:

```html
<button class="action-pill action-pill--ghost"
        hx-post="/api/<entity>/{{ x.id }}/<action>"
        hx-confirm="Точно?"
        hx-target="closest article"
        hx-swap="delete">подпись</button>
```

**Правила:**
- Всегда `_w()` для writes (не `_q()` — она read-only URI).
- `_log_edit()` после UPDATE — держит lock короче.
- 404 на missing id, 400 на invalid action.
- Idempotency — если уже в terminal_state, пустой 200.
- **Никогда не делай smoke-test на реальной записи.** Создай изолированную тестовую фикстуру и отправляй POST только в неё. Изменение и запись аудита проверяй на этой фикстуре: smoke-test не должен менять реальные данные.

## Inline-edit поля

Click → input → blur → swap. HTML:

```html
<p class="card-description"
   hx-get="/api/<entity>/{{ x.id }}/edit"
   hx-trigger="click"
   hx-swap="outerHTML"
   title="клик чтобы изменить">{{ x.field }}</p>
```

Backend (два endpoint):

```python
@app.get("/api/<entity>/{id}/edit", response_class=HTMLResponse)
def api_<entity>_edit(id: int):
    rows = _q("SELECT field FROM <table> WHERE id=?", (id,))
    if not rows: raise HTTPException(404)
    current = rows[0]["field"] or ""
    return HTMLResponse(
        f'<p class="card-description card-description--editing">'
        f'<input name="value" value="{_html.escape(current)}" autofocus '
        f'hx-post="/api/<entity>/{id}" '
        f'hx-trigger="blur,keyup[key==&quot;Enter&quot;]" hx-swap="outerHTML" '
        f'class="kv-edit-input"></p>'
    )

@app.post("/api/<entity>/{id}", response_class=HTMLResponse)
def api_<entity>_save(id: int, value: str = Form(...)):
    rows = _q("SELECT field FROM <table> WHERE id=?", (id,))
    if not rows: raise HTTPException(404)
    old = rows[0]["field"]
    _w("UPDATE <table> SET field=? WHERE id=?", (value, id))
    _log_edit("<table>", id, "edit_field", field="field",
              old_value=old, new_value=value)
    return HTMLResponse(
        f'<p class="card-description" '
        f'hx-get="/api/<entity>/{id}/edit" hx-trigger="click" hx-swap="outerHTML" '
        f'title="клик чтобы изменить">{_html.escape(value)}</p>'
    )
```

`_html.escape()` обязательно — без него XSS через user input.

## Зависимости write-side

```bash
pip install --quiet python-multipart  # для Form(...) parsing
```

Без этого FastAPI крашится при импорте endpoint с `Form()`:
```
RuntimeError: Form data requires "python-multipart" to be installed.
```



## Добавить тест на новый endpoint

После Wave 9: используй `dashboard_client` fixture + builders.

**Пример теста status-action:**

```python
# tests/integration/test_dashboard_<feature>.py
import pytest

pytestmark = pytest.mark.integration


def test_my_new_action(dashboard_client):
    client, db = dashboard_client

    # 1. Seed: создаём test entity через builder
    pid = db.add_protocol("test protocol", "behavior", status="active")

    # 2. Action: POST на endpoint
    r = client.post(f"/api/protocols/{pid}/my_action")
    assert r.status_code == 200

    # 3. Assert: проверяем БД
    row = db.fetchone("SELECT status FROM protocols WHERE id=?", (pid,))
    assert row["status"] == "expected_value"

    # 4. Audit: проверяем что запись есть
    audit = db.fetchone(
        "SELECT action, field FROM dashboard_edits WHERE entity='protocols' ORDER BY id DESC LIMIT 1"
    )
    assert audit["action"] == "my_action"
```

**Пример теста inline-edit:**

```python
def test_my_field_edit(dashboard_client):
    client, db = dashboard_client
    db.add_profile("test.key", value_text="old", category="test")

    # GET edit returns input HTML
    r1 = client.get("/api/profile/test.key/edit")
    assert "input" in r1.text

    # POST save
    r2 = client.post("/api/profile/test.key", data={"value": "new"})
    assert r2.status_code == 200

    # БД обновилась
    row = db.fetchone("SELECT value_text FROM patient_profile WHERE key=?", ("test.key",))
    assert row["value_text"] == "new"
```

**Builders (`tests/fixtures/db.py`):**
- `db.add_profile(key, value_text, value_json, category, updated_by)` — patient_profile
- `db.add_hypothesis(payload, active, key, confidence)` — memory category=hypothesis, payload в JSON
- `db.add_task(content, status, source, priority, **extras)` — tasks
- `db.add_protocol(title, behavior, status, **extras)` — protocols
- `db.add_consultation(date, specialist_type, key_findings, specialist_name)` — consultations
- `db.add_period(name, type_, start_date, end_date, notes, tags, active)` — periods
- `db.add_problem(problem_id, title, **extras)` — problem_list
- Стандартные: `db.fetchone(sql, params)`, `db.fetchall(sql, params)`, `db.count(table, where)`, `db.execute(sql, params)`

**Запуск:**

```bash
# Один файл
/opt/homebrew/bin/python3.11 -m pytest tests/integration/test_dashboard_<feature>.py -q

# Все dashboard tests
/opt/homebrew/bin/python3.11 -m pytest tests/integration/test_dashboard_*.py -q
```

Время прогона полного набора ~1.5 сек.

## Перезагрузить дашборд после правки .py

```bash
ssh <studio_ssh> "launchctl unload ~/Library/LaunchAgents/com.larry.health.dashboard.plist; \
                         sleep 1; \
                         launchctl load ~/Library/LaunchAgents/com.larry.health.dashboard.plist; \
                         sleep 2; \
                         pgrep -fl dashboard.py"
```

Должно вывести PID + путь к процессу. Если `NOT RUNNING` — смотри `~/health_scripts/logs/dashboard.err.log`.

## Откатить изменения

Если что-то сломалось после deploy:

```bash
# На MacBook:
git log --oneline -5 dashboard.py dashboard_templates/ dashboard_static/
# Найди SHA сломавшего commit'а
git checkout <SHA>~1 -- dashboard.py dashboard_templates/ dashboard_static/
git commit -m 'revert: dashboard regression in <SHA>' -- dashboard.py dashboard_templates/ dashboard_static/
```

Откат из `main` доставит post-commit; если работа шла в нити — закрой её через `scripts/thread_finish.sh <slug>` из главной копии MacBook. После доставки проверь дашборд.

## Прочитать логи

```bash
ssh <studio_ssh> "tail -50 ~/health_scripts/logs/dashboard.err.log"
ssh <studio_ssh> "tail -50 ~/health_scripts/logs/dashboard.out.log"
```

Uvicorn-stack-traces и Jinja-ошибки — в `err.log`. Access-логи — в `out.log`.

## Запустить локально без launchd (для отладки)

```bash
ssh <studio_ssh> "cd ~/health_scripts && launchctl unload ~/Library/LaunchAgents/com.larry.health.dashboard.plist"
ssh -t <studio_ssh> "cd ~/health_scripts && /opt/homebrew/bin/python3.11 dashboard.py"
# Ctrl+C для остановки
# Потом снова: launchctl load ...
```

## Cache-bust не сработал — что делать

Если ты обновил CSS и Cmd+R не показывает изменения:
1. Проверь линк в HTML: `curl -s http://<studio_host>:8001/ | grep dashboard.css`. Должно содержать `?v=NNNNN`
2. Если `v=` не меняется после правки CSS — `launchctl reload` (mtime читается один раз при boot)
3. Если меняется, но Safari показывает старое — `Cmd+Shift+R` (hard reload, игнорирует cache)
4. Если ничего не помогло — Safari → Develop → Empty Caches

## Pre-commit hook

На MacBook установлен pre-commit hook (`scripts/install_hooks.sh`). Он проверяет:
- Silent-except baseline=35 (CLAUDE.md (в закрытой части) §7) — если ты добавил `except: pass` без `# silent-ok: reason` — блок
- Doc inventory — если ты добавил `.md` в LIVE — проверь `doc_inventory.yaml`

При блокировке — текст ошибки в выводе `git commit`. Не используй `--no-verify` без согласования с пользователем (CLAUDE.md правило про git).
