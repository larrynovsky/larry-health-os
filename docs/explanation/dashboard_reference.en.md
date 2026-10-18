<!-- translation-of: docs/explanation/dashboard_reference.md sha256:5d9ef1c54685 -->
<!-- Machine translation by doc_agent --translate-intent; regenerated with the Russian page, do not edit by hand. -->

**English** · [Русский](dashboard_reference.md)

# Dashboard — Reference

What is currently working (as of 2026-05-15, Wave 6 Phase 1+2).
Reference document: facts without rationale. Rationale — in `docs/explanation/dashboard.md`. Changes — `docs/how-to/extend_dashboard.md`.

## Where it lives

- Host: Studio (`<studio_host>`)
- Port: `8001`
- Binding: `<studio_host>:8001` (Tailscale interface only — not `0.0.0.0`)
- Launch: `~/Library/LaunchAgents/com.larry.health.dashboard.plist` (launchd, `KeepAlive=true`)

**Related launchd agents (create/analyze dashboard data):**
- `com.larry.health.consilium` (1st of month, 04:00) — `monthly_consilium.py`, generates hypotheses
- Process: `/opt/homebrew/bin/python3.11 ~/health_scripts/dashboard.py`
- Logs: `~/health_scripts/logs/dashboard.{out,err}.log`

## Technologies

- Web: FastAPI + Starlette `TemplateResponse(request, name, ctx)`
- Templates: Jinja2
- DB: SQLite read-only (`file:{DB_PATH}?mode=ro` URI)
- Markdown render: `python-markdown` 3.10.2, extensions `extra/tables/toc`
- Front end: server-side render, no JS framework, no build step

## Routes

| Path | Template | Data source |
|---|---|---|
| `/` | `home.html` | "Getting started": `getting_started.board()` — state of 17 opportunities from tenant data, catalog `methodology/getting_started.yaml` (from 02.10.2026); below, collapsed counts from 11 tables; weekday/today_meta, epigraph from `patient_profile.identity.epigraph` |
| `/profile` | `profile.html` | `patient_profile` grouped by `category` |
| `/hypotheses` | `hypotheses.html` | `memory WHERE category='hypothesis'`, JSON payload in `value` |
| `/protocols` | `protocols.html` | `protocols`, sorted `status DESC, created_at DESC` |
| `/tasks` | `tasks.html` | `tasks WHERE status IN (open, snoozed, answered)` |
| `/problems` | `problems.html` | `problem_list`, sorted active→watch→other |
| `/labs` | `labs.html` | `lab_results` — latest per test + recent 50 |
| `/constitutions` | `constitutions_index.html` | `~/health_scripts/constitutions/*.md` |
| `/constitutions/{slug}` | `constitution_view.html` | single `.md` → HTML via `python-markdown` |

## Templates

- `base.html` — shell (header + sidebar + content + footer), includes `sidebar.html`
- `sidebar.html` — partial with 4 groups: "today · managing · listening · reference". Active item by `request.url.path.startswith(...)`
- 13 page templates above extend `base.html`

## Toscana design system

Source of truth: `~/health/dashboard-design/` (outside repo, in iCloud)
- `tokens.css` — palette, typography, spacing, shadows, radii
- `dashboard.css` — components (shell, sidebar, card, chip, dense, kv, timeline, narrative)
- `how-to.md`, `reference.md`, `explanation.md`, `tutorial.md` — system documentation

In repo: `dashboard_static/tokens.css` (copy) + `dashboard_static/dashboard.css` (component implementation under Toscana).

### Components used per page

| Page | Component |
|---|---|
| `/profile` | `.kv` rows + `.caption(category)` groups |
| `/hypotheses`, `/protocols`, `/problems`, `/tasks` | `.card` with modifiers `--warning`/`--action`/`--active` |
| `/labs` | `.dense` tables |
| `/events` | `.timeline` (year on left + dots of different colors) |
| `/constitutions/{slug}` | `.narrative` (max 65ch, CSS drop-cap via `::first-letter`) |
| `/constitutions` | `.const-list` |
| `/` | `card` + `gs-*` (opportunity states), `dense gs-time`, collapsed `ul.counts` |

## Cache-bust

- `templates.env.globals["css_version"] = int(mtime(dashboard.css))` is set when `dashboard.py` loads
- `base.html` links `<link href="/static/dashboard.css?v={{ css_version }}">`
- Process restart (launchctl reload) picks up the new mtime automatically

## Filters / globals

Jinja:
- `age_days(iso_date)` — days from date to today
- `short(text, n=120)` — truncates string
- `pretty_json(s)` — JSON with indentation

Globals:
- `css_version` — int mtime
- `request` — Starlette request (required for `request.url.path` in sidebar)

## DB sources

| Table | Purpose | Dashboard uses |
|---|---|---|
| `patient_profile` | KV facts about the patient | `/profile`, `epigraph` on `/` |
| `memory (category='hypothesis')` | hypotheses | `/hypotheses` |
| `protocols` | behavioral protocols | `/protocols` |
| `tasks` | tasks (from agent / manual) | `/tasks` |
| `problem_list` | active problems | `/problems` |
| `periods` | clinical phases | `/events` (timeline) |
| `context_events` | day events (legacy, not populated since March 2026) | `/events` (quiet feed at bottom) |
| `lab_results` | lab results | `/labs` |

## Write endpoints (Phase 3)

All POST endpoints use helper `_w()` with `PRAGMA busy_timeout=5000`.
Every mutation is logged to `dashboard_edits` via `_log_edit()`.

| Endpoint | Action | Backend |
|---|---|---|
| `GET /api/profile/{key:path}/edit` | Open inline edit | swap to input/textarea |
| `POST /api/profile/{key:path}` | Save value_text/value_json | UPDATE patient_profile, audit |
| `POST /api/hypotheses/{id}/confirm` | Confirm + create task from payload.test | UPDATE memory active=0, INSERT INTO tasks |
| `POST /api/hypotheses/{id}/reject` | Reject | UPDATE memory active=0, resolution_type |
| `POST /api/protocols/{id}/retire` | Retire protocol | UPDATE protocols status=retired |
| `POST /api/problems/{id}/{archive\|reopen}` | Remove from watch / reopen | UPDATE problem_list status |
| `GET /api/tasks/{id}/edit` | Inline edit content | swap to input |
| `POST /api/tasks/{id}` | Save content | UPDATE tasks |
| `POST /api/tasks/{id}/done` | Mark done | UPDATE tasks status=completed |
| `POST /api/tasks/{id}/snooze?days=N` | Snooze (3/7/30 or 1..365) | UPDATE tasks deadline=today+N |
| `GET /api/ping` | HTMX smoke | returns chip pong |

**Cascade rules:**
- `confirm hypothesis` with non-empty `payload.test` → INSERT into `tasks` (source=dashboard_confirm, type=followup, priority=medium)
- Snooze re-emerges automatically: `/tasks` route filters `WHERE status=open OR (status=snoozed AND deadline <= today)`. No background cron needed.

## Audit log

Table `dashboard_edits` (created by migration on dashboard.py boot):

- `id` AUTOINCREMENT
- `ts` default datetime now
- `entity` — patient_profile / memory / protocols / tasks / problem_list / experiments (until 2026-07-10) / consultations / periods
- `entity_id` TEXT (for profile this is `key`, for others — INTEGER as string)
- `action` — edit_field / confirm / reject / retire / archive / reopen / complete / cancel / done / snooze / create_from_hypothesis_confirm
- `field`, `old_value`, `new_value`, `actor` (default `dashboard`)

View: `/audit` with filter via `?entity=tasks`.

Rollback: manually via SQL using `old_value` from audit row.

## Dependency notes (Phase 3)

- **python-multipart 0.0.28** — required for FastAPI `Form(...)` parsing. Installed via `pip install --quiet python-multipart`.
- **htmx.min.js 1.9.10** — self-hosted in `dashboard_static/`. Cache-busted by mtime via `templates.env.globals["js_version"]`.
- **python-markdown 3.10.2** — for rendering `.md` constitutions (Phase 2).

## Tests (Wave 9, 2026-05-16)

Phase 3.3H closed: 53 integration tests on a live tmp DB via TestClient.

**Run:**

```bash
cd ~/health_scripts
/opt/homebrew/bin/python3.11 -m pytest tests/integration/test_dashboard_*.py -q
```

**Files:**

| File | Tests | What it covers |
|---|---|---|
| `test_dashboard_smoke.py` | 22 | All 13 GET routes → 200, /api/ping, edge cases (400/404) |
| `test_dashboard_profile_edit.py` | 4 | value_text round-trip, value_json validation, 404 |
| `test_dashboard_hypothesis_cascade.py` | 7 | **Cascade confirm→task (primary)**, idempotency, audit |
| `test_dashboard_tasks.py` | 7 | done, snooze, **auto-reawake (primary)**, edit content |
| `test_dashboard_status_actions.py` | 13 | protocol retire, problem archive/reopen, experiment complete/cancel, consultations/periods edit, audit page |

**Fixture:**

- `dashboard_client(db)` — `TestClient(app)` + tmp SQLite (via fixture `db`)
- Builders in `tests/fixtures/db.py`: `add_profile`, `add_hypothesis`, `add_task`, `add_protocol`, `add_experiment`, `add_consultation`, `add_period` (+ existing `add_daily_metrics`, `add_lab_result`, `add_problem`, etc.)

**Run time:** ~1.5 sec for the full suite (53 tests).

**What regressions are caught:**
- Template change breaks GET route → smoke fails
- `_q`/`_w` API change breaks write side → profile/cascade tests fail
- CBCR format change in `payload.test` breaks cascade → hypothesis_cascade fails
- `/tasks` route filter change breaks snooze → tasks tests fail

## Known limits

- No write operations yet (read-only DB, no POST endpoints) — Phase 3 is on the roadmap
- Google Fonts via CDN — the only external fetch (Newsreader / Inter / JetBrains Mono)
- `today_meta` shows date only, without "&lt;N&gt; days since end of regimen" — reference dates are not used
- Mobile adaptation — skeletal (sidebar becomes inline-block), not the tabbar from the design
- Drop-cap in constitutions via `::first-letter` only fires if the first element is `<p>` (if markdown starts with `<h1>` — there will be no drop-cap)

## Version

- Phase 1+2 (read-only Toscana): `b60ba83`..`629392a`
- Phase 3 (write + cascade): `daac515` (3A) → `30cba11` (3B) → `e1f0814` (3C) → `95b87e6` (3D) → `70e92ef` (3C-extend + 3E) → `834c452` (3F + 3G)
- 7/8 stages closed. 3H (integration tests) was deferred — `DB_PATH` refactor needed for test fixture.
- Stable as of `2026-05-16`
