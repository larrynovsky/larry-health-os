<!-- translation-of: docs/how-to/extend_dashboard.md sha256:bb2f9d4fc099 -->
**English** · [Русский](extend_dashboard.md)

# How-to — Extend Dashboard

Specific operations: add a page, change a component, deploy, roll back.
Reference (what exists): `docs/reference/dashboard.md`. Why it works this way: `docs/explanation/dashboard.md`.

## Add a new page

Suppose you need `/medications`: a list of current medications from the `medications` table.

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

### 2. Template

```html
{# dashboard_templates/medications.html #}
{% extends "base.html" %}
{% block title %}Medications — Health OS{% endblock %}
{% set page_name = "medications" %}

{% block header %}
<div class="shell-title-block">
  <h1>Medications</h1>
  <p class="shell-subtitle">{{ medications | length }} active</p>
</div>
{% endblock %}

{% block content %}
{% for m in medications %}
  <article class="card card--action">
    <div class="card-head">
      <div class="card-meta">№ {{ m.id }} · since {{ m.started_at }}</div>
      <div class="card-chips">
        <span class="chip chip--action">{{ m.frequency }}</span>
      </div>
    </div>
    <h3 class="card-title">{{ m.name }} <small>{{ m.dose }}</small></h3>
  </article>
{% endfor %}
{% endblock %}
```

### 3. Sidebar (optional)

In `sidebar.html`, add a link to the appropriate group (probably “manage”):

```html
<a class="sidebar-item {% if path.startswith('/medications') %}sidebar-item--active{% endif %}" href="/medications">
  Medications
  {% if counts %}<span class="sidebar-count">{{ counts.medications_active }}</span>{% endif %}
</a>
```

### 4. counts.* (if needed on the home page)

In `dashboard.py`, the `home()` function:

```python
counts = {
    ...
    "medications_active": _q("SELECT COUNT(*) AS n FROM medications WHERE active=1")[0]["n"],
}
```

And in `home.html`:

```html
<li><a href="/medications">Medications</a><b>{{ counts.medications_active }} active</b></li>
```

### 5. Commit on MacBook and deploy

Make all edits in the MacBook working copy. For work spanning more than one commit, first create a tree with `scripts/thread_start.sh <slug>` and work in `~/.worktrees/health_scripts/<slug>`: [workflow](thread_worktree.md).

```bash
# In the MacBook working copy:
git add dashboard.py dashboard_templates/medications.html dashboard_templates/sidebar.html dashboard_templates/home.html
git commit -m 'feat(dashboard): /medications page' -- dashboard.py dashboard_templates/medications.html dashboard_templates/sidebar.html dashboard_templates/home.html

# If the work was done in a thread's tree, close from the main copy:
cd ~/health_scripts
scripts/thread_finish.sh <slug>
```

A direct commit to `main` triggers post-commit: `git push studio main` and service restarts. A branch commit does not deploy; `thread_finish` runs the full suite on Studio, merges the branch, and invokes the same hook.

### 6. Smoke-check

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://<studio_host>:8001/medications
# Should be 200
```

### 7. Verify delivery (§12)

```bash
git fetch studio
git log studio/main   # contains the commit under test?
```

## Change a design token

All tokens are in `dashboard_static/tokens.css`. The source of truth is `~/health/dashboard-design/tokens.css` (iCloud).

For example, change `--color-warning`:

```css
:root {
  --color-warning: #E04B30;  /* was #D85A3E */
}
```

Workflow:
1. Edit `~/health/dashboard-design/tokens.css` (the source) first
2. Copy it into your MacBook working copy: `cp ~/health/dashboard-design/tokens.css dashboard_static/tokens.css`
3. Commit on MacBook; a direct commit to `main` deploys through post-commit; close a thread with `scripts/thread_finish.sh <slug>` from the main copy.
4. CSS does not require a reload; cache-busting only takes effect if `dashboard.css` has changed. To make the new tokens.css mtime visible, use either `launchctl reload` or `touch dashboard.css` (updates its mtime).
5. Verify delivery: `git fetch studio && git log studio/main` (§12).

## Add a new CSS component

For example, `.metric-block` to display one metric with a trend.

1. Add it to `~/health/dashboard-design/dashboard.css` (the source)
2. Copy it into your MacBook working copy: `cp ... dashboard_static/dashboard.css`
3. After editing, deploy through a commit to `main` on MacBook; for a thread, use `scripts/thread_finish.sh <slug>` from the main copy.
4. No reload: Jinja templates auto-reload, and CSS cache-busting by mtime takes effect on the next request
5. Use it in the template: `<div class="metric-block">...</div>`
6. Verify delivery: `git fetch studio && git log studio/main` (§12).

Contract:
- Only tokens inside (`var(--...)`)
- No `#XXXXXX` or numeric px literals except for spacing
- Class names: `kebab-case`, modifiers: `--state`



## Add a write endpoint (after Phase 3)

Template for a new endpoint that changes data:

```python
@app.post("/api/<entity>/{id}/<action>", response_class=HTMLResponse)
def api_<entity>_<action>(id: int):
    # 1. Read current (for idempotency + audit old_value)
    rows = _q("SELECT <field> FROM <table> WHERE id=?", (id,))
    if not rows:
        raise HTTPException(404, f"<entity> {id} not found")
    if rows[0]["<field>"] == "<terminal_state>":
        return HTMLResponse("")  # idempotent: already done

    old = rows[0]["<field>"]

    # 2. Update (use _w(), not _q())
    _w("UPDATE <table> SET <field>=?, ... WHERE id=?", (new_value, id))

    # 3. Audit log
    _log_edit("<table>", id, "<action>", field="<field>",
              old_value=old, new_value=new_value)

    # 4. Return for the HTMX swap
    return HTMLResponse("")  # empty → hx-swap=delete removes the element
```

HTML button:

```html
<button class="action-pill action-pill--ghost"
        hx-post="/api/<entity>/{{ x.id }}/<action>"
        hx-confirm="Sure?"
        hx-target="closest article"
        hx-swap="delete">label</button>
```

**Rules:**
- Always use `_w()` for writes (not `_q()`, which uses a read-only URI).
- `_log_edit()` after UPDATE: keeps the lock shorter.
- 404 for a missing id, 400 for an invalid action.
- Idempotency: if already in terminal_state, return an empty 200.
- **Never smoke-test against a live record.** Create an isolated test fixture and POST only to it. Check the mutation and audit entry on that fixture; a smoke test must not change a real record.

## Edit a field inline

Click → input → blur → swap. HTML:

```html
<p class="card-description"
   hx-get="/api/<entity>/{{ x.id }}/edit"
   hx-trigger="click"
   hx-swap="outerHTML"
   title="click to edit">{{ x.field }}</p>
```

Backend (two endpoints):

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
        f'title="click to edit">{_html.escape(value)}</p>'
    )
```

`_html.escape()` is required; without it, user input allows XSS.

## Write-side dependencies

```bash
pip install --quiet python-multipart  # for Form(...) parsing
```

Without this, FastAPI crashes when importing an endpoint with `Form()`:
```
RuntimeError: Form data requires "python-multipart" to be installed.
```



## Add a test for a new endpoint

After Wave 9: use the `dashboard_client` fixture + builders.

**Status-action test example:**

```python
# tests/integration/test_dashboard_<feature>.py
import pytest

pytestmark = pytest.mark.integration


def test_my_new_action(dashboard_client):
    client, db = dashboard_client

    # 1. Seed: create a test entity via the builder
    pid = db.add_protocol("test protocol", "behavior", status="active")

    # 2. Action: POST to the endpoint
    r = client.post(f"/api/protocols/{pid}/my_action")
    assert r.status_code == 200

    # 3. Assert: check the DB
    row = db.fetchone("SELECT status FROM protocols WHERE id=?", (pid,))
    assert row["status"] == "expected_value"

    # 4. Audit: check that the record exists
    audit = db.fetchone(
        "SELECT action, field FROM dashboard_edits WHERE entity='protocols' ORDER BY id DESC LIMIT 1"
    )
    assert audit["action"] == "my_action"
```

**Inline-edit test example:**

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

    # the DB got updated
    row = db.fetchone("SELECT value_text FROM patient_profile WHERE key=?", ("test.key",))
    assert row["value_text"] == "new"
```

**Builders (`tests/fixtures/db.py`):**
- `db.add_profile(key, value_text, value_json, category, updated_by)` — patient_profile
- `db.add_hypothesis(payload, active, key, confidence)` — memory category=hypothesis, payload in JSON
- `db.add_task(content, status, source, priority, **extras)` — tasks
- `db.add_protocol(title, behavior, status, **extras)` — protocols
- `db.add_consultation(date, specialist_type, key_findings, specialist_name)` — consultations
- `db.add_period(name, type_, start_date, end_date, notes, tags, active)` — periods
- `db.add_problem(problem_id, title, **extras)` — problem_list
- Standard: `db.fetchone(sql, params)`, `db.fetchall(sql, params)`, `db.count(table, where)`, `db.execute(sql, params)`

**Run:**

```bash
# A single file
/opt/homebrew/bin/python3.11 -m pytest tests/integration/test_dashboard_<feature>.py -q

# All dashboard tests
/opt/homebrew/bin/python3.11 -m pytest tests/integration/test_dashboard_*.py -q
```

The full set takes ~1.5 seconds to run.

## Reload the dashboard after editing .py

```bash
ssh <studio_ssh> "launchctl unload ~/Library/LaunchAgents/com.larry.health.dashboard.plist; \
                         sleep 1; \
                         launchctl load ~/Library/LaunchAgents/com.larry.health.dashboard.plist; \
                         sleep 2; \
                         pgrep -fl dashboard.py"
```

It should print the PID + process path. If `NOT RUNNING`, check `~/health_scripts/logs/dashboard.err.log`.

## Roll back changes

If something broke after deployment:

```bash
# On the MacBook:
git log --oneline -5 dashboard.py dashboard_templates/ dashboard_static/
# Find the SHA of the breaking commit
git checkout <SHA>~1 -- dashboard.py dashboard_templates/ dashboard_static/
git commit -m 'revert: dashboard regression in <SHA>' -- dashboard.py dashboard_templates/ dashboard_static/
```

Post-commit delivers a rollback from `main`; if the work was in a thread, close it with `scripts/thread_finish.sh <slug>` from the main MacBook copy. After delivery, check the dashboard.

## Read the logs

```bash
ssh <studio_ssh> "tail -50 ~/health_scripts/logs/dashboard.err.log"
ssh <studio_ssh> "tail -50 ~/health_scripts/logs/dashboard.out.log"
```

Uvicorn stack traces and Jinja errors are in `err.log`. Access logs are in `out.log`.

## Run locally without launchd (for debugging)

```bash
ssh <studio_ssh> "cd ~/health_scripts && launchctl unload ~/Library/LaunchAgents/com.larry.health.dashboard.plist"
ssh -t <studio_ssh> "cd ~/health_scripts && /opt/homebrew/bin/python3.11 dashboard.py"
# Ctrl+C to stop
# Then again: launchctl load ...
```

## Cache-busting did not work: what to do

If you updated CSS and Cmd+R does not show the changes:
1. Check the link in the HTML: `curl -s http://<studio_host>:8001/ | grep dashboard.css`. It should contain `?v=NNNNN`
2. If `v=` does not change after editing CSS, use `launchctl reload` (mtime is read once at boot)
3. If it changes but Safari shows the old version, use `Cmd+Shift+R` (hard reload, ignores cache)
4. If nothing helped: Safari → Develop → Empty Caches

## Pre-commit hook

A pre-commit hook is installed on MacBook (`scripts/install_hooks.sh`). It checks:
- Silent-except baseline=35 (CLAUDE.md (private part) §7): if you added `except: pass` without `# silent-ok: reason`, it blocks
- Doc inventory: if you added `.md` to LIVE, check `doc_inventory.yaml`

When blocked, the error text appears in the `git commit` output. Do not use `--no-verify` without agreement from the user (CLAUDE.md rule about git).
