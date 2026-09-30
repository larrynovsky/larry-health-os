<!-- translation-of: docs/explanation/dashboard_reference.md sha256:c8324a32eb10 -->
**English** · [Русский](dashboard_reference.md)

# Dashboard — Reference

What currently works (as of 2026-05-15, Wave 6 Phase 1+2).
Reference document: facts without rationale. Rationale — `docs/explanation/dashboard.md`. Changes — `docs/how-to/extend_dashboard.md`.

## Where it lives

- Host: Studio (`<studio_host>`)
- Port: `8001`
- Binding: `<studio_host>:8001` (Tailscale interface only — not `0.0.0.0`)
- Startup: `~/Library/LaunchAgents/com.larry.health.dashboard.plist` (launchd, `KeepAlive=true`)

**Related launchd agents (create/analyze dashboard data):**
- `com.larry.health.consilium` (1st of the month, 04:00) — `monthly_consilium.py`, generates hypotheses
- Process: `/opt/homebrew/bin/python3.11 ~/health_scripts/dashboard.py`
- Logs: `~/health_scripts/logs/dashboard.{out,err}.log`

## Technologies

- Web: FastAPI + Starlette `TemplateResponse(request, name, ctx)`
- Templates: Jinja2
- Database: SQLite read-only (`file:{DB_PATH}?mode=ro` URI)
- Markdown rendering: `python-markdown` 3.10.2, extensions `extra/tables/toc`
- Frontend: server-side rendering, no JS framework, no build step

## Routes

| Path | Template | Data source |
|---|---|---|
| `/` | `home.html` | Counts from 11 tables, weekday/today_meta, epigraph from `patient_profile.identity.epigraph` |
| `/profile` | `profile.html` | `patient_profile` grouped by `category` |
| `/hypotheses` | `hypotheses.html` | `memory WHERE category='hypothesis'`, JSON payload in `value` |
| `/protocols` | `protocols.html` | `protocols`, sorted `status DESC, created_at DESC` |
| `/tasks` | `tasks.html` | `tasks WHERE status IN (open, snoozed, answered)` |
| `/problems` | `problems.html` | `problem_list`, sorted active→watch→other |
| `/labs` | `labs.html` | `lab_results` — latest per test + recent 50 |
| `/constitutions` | `constitutions_index.html` | `~/health_scripts/constitutions/*.md` |
| `/constitutions/{slug}` | `constitution_view.html` | One `.md` → HTML through `python-markdown` |

## Templates

- `base.html` — shell (header + sidebar + content + footer), includes `sidebar.html`
- `sidebar.html` — partial with 4 groups: "today · manage · listen · reference." Active item determined by `request.url.path.startswith(...)`
- The 13 page templates above extend `base.html`

## Toscana design system

Source of truth: `~/health/dashboard-design/` (outside the repo, in iCloud)
- `tokens.css` — palette, typography, spacing, shadows, radii
- `dashboard.css` — components (shell, sidebar, card, chip, dense, kv, timeline, narrative)
- `how-to.md`, `reference.md`, `explanation.md`, `tutorial.md` — system documentation

In the repo: `dashboard_static/tokens.css` (copy) + `dashboard_static/dashboard.css` (Toscana component implementation).

### Components used by page

| Page | Component |
|---|---|
| `/profile` | `.kv` rows + `.caption(category)` groups |
| `/hypotheses`, `/protocols`, `/problems`, `/tasks` | `.card` with modifiers `--warning`/`--action`/`--active` |
| `/labs` | `.dense` tables |
| `/events` | `.timeline` (year on the left + dots of different colors) |
| `/constitutions/{slug}` | `.narrative` (max 65ch, CSS drop cap through `::first-letter`) |
| `/constitutions` | `.const-list` |
| `/` | `ul.counts` |

## Cache busting

- `templates.env.globals["css_version"] = int(mtime(dashboard.css))` is set when `dashboard.py` loads
- `base.html` links `<link href="/static/dashboard.css?v={{ css_version }}">`
- Restarting the process (launchctl reload) picks up the new mtime automatically

## Filters / globals

Jinja:
- `age_days(iso_date)` — days from the date to today
- `short(text, n=120)` — truncates a string
- `pretty_json(s)` — formats JSON with indentation

Globals:
- `css_version` — int mtime
- `request` — Starlette request (needed for `request.url.path` in the sidebar)

## Database sources

| Table | Purpose | Dashboard use |
|---|---|---|
| `patient_profile` | KV facts about the patient | `/profile`, `epigraph` on `/` |
| `memory (category='hypothesis')` | Hypotheses | `/hypotheses` |
| `protocols` | Behavioral protocols | `/protocols` |
| `tasks` | Tasks (from an agent / manual) | `/tasks` |
| `problem_list` | Active problems | `/problems` |
| `periods` | Clinical phases | `/events` (timeline) |
| `context_events` | Daily events (legacy, no new entries since March 2026) | `/events` (quiet feed at the bottom) |
| `lab_results` | Lab results | `/labs` |

## Write endpoints (Phase 3)

All POST endpoints use the `_w()` helper with `PRAGMA busy_timeout=5000`.
Each mutation is logged in `dashboard_edits` through `_log_edit()`.

| Endpoint | Action | Backend |
|---|---|---|
| `GET /api/profile/{key:path}/edit` | Open inline editing | Swap to input/textarea |
| `POST /api/profile/{key:path}` | Save value_text/value_json | UPDATE patient_profile, audit |
| `POST /api/hypotheses/{id}/confirm` | Confirm + create a task from payload.test | UPDATE memory active=0, INSERT INTO tasks |
| `POST /api/hypotheses/{id}/reject` | Reject | UPDATE memory active=0, resolution_type |
| `POST /api/protocols/{id}/retire` | Retire protocol | UPDATE protocols status=retired |
| `POST /api/problems/{id}/{archive\|reopen}` | Stop monitoring / resume | UPDATE problem_list status |
| `GET /api/tasks/{id}/edit` | Inline-edit content | Swap to input |
| `POST /api/tasks/{id}` | Save content | UPDATE tasks |
| `POST /api/tasks/{id}/done` | Done | UPDATE tasks status=completed |
| `POST /api/tasks/{id}/snooze?days=N` | Snooze (3/7/30 or 1..365) | UPDATE tasks deadline=today+N |
| `GET /api/ping` | HTMX smoke | Returns a pong chip |

**Cascade rules:**
- `confirm hypothesis` with nonempty `payload.test` → INSERT into `tasks` (source=dashboard_confirm, type=followup, priority=medium)
- Snooze automatically re-emerges: the `/tasks` route filters `WHERE status=open OR (status=snoozed AND deadline <= today)`. No background cron needed.

## Audit log

The `dashboard_edits` table (created by migration when dashboard.py boots):

- `id` AUTOINCREMENT
- `ts` default datetime now
- `entity` — patient_profile / memory / protocols / tasks / problem_list / experiments (until 2026-07-10) / consultations / periods
- `entity_id` TEXT (for profile this is `key`; for the others, INTEGER as string)
- `action` — edit_field / confirm / reject / retire / archive / reopen / complete / cancel / done / snooze / create_from_hypothesis_confirm
- `field`, `old_value`, `new_value`, `actor` (default `dashboard`)

View: `/audit` with filtering through `?entity=tasks`.

Rollback: manually through SQL using `old_value` from the audit row.

## Dependency notes (Phase 3)

- **python-multipart 0.0.28** — required for FastAPI `Form(...)` parsing. Installed through `pip install --quiet python-multipart`.
- **htmx.min.js 1.9.10** — self-hosted in `dashboard_static/`. Cache busting by mtime through `templates.env.globals["js_version"]`.
- **python-markdown 3.10.2** — for rendering constitution `.md` files (Phase 2).

## Tests (Wave 9, 2026-05-16)

Phase 3.3H closed: 53 integration tests on a live temporary database through TestClient.

**Run:**

```bash
cd ~/health_scripts
/opt/homebrew/bin/python3.11 -m pytest tests/integration/test_dashboard_*.py -q
```

**Files:**

| File | Tests | Coverage |
|---|---|---|
| `test_dashboard_smoke.py` | 22 | All 13 GET routes → 200, /api/ping, edge cases (400/404) |
| `test_dashboard_profile_edit.py` | 4 | value_text round-trip, value_json validation, 404 |
| `test_dashboard_hypothesis_cascade.py` | 7 | **Cascade confirm→task (main)**, idempotency, audit |
| `test_dashboard_tasks.py` | 7 | done, snooze, **automatic reawakening (main)**, edit content |
| `test_dashboard_status_actions.py` | 13 | protocol retire, problem archive/reopen, experiment complete/cancel, consultations/periods edit, audit page |

**Fixture:**

- `dashboard_client(db)` — `TestClient(app)` + temporary SQLite (through fixture `db`)
- Builders in `tests/fixtures/db.py`: `add_profile`, `add_hypothesis`, `add_task`, `add_protocol`, `add_experiment`, `add_consultation`, `add_period` (+ existing `add_daily_metrics`, `add_lab_result`, `add_problem`, etc.)

**Run time:** ~1.5 seconds for the full set (53 tests).

**Regressions caught:**
- A template change breaks a GET route → smoke fails
- A change to the `_q`/`_w` API breaks the write side → profile/cascade tests fail
- A change to the CBCR format of `payload.test` breaks the cascade → hypothesis_cascade fails
- A change to the `/tasks` route filter breaks snooze → tasks tests fail

## Known limitations

- No write operations yet (read-only database, no POST endpoints) — Phase 3 is on the roadmap
- Google Fonts through CDN — the only external fetch (Newsreader / Inter / JetBrains Mono)
- `today_meta` shows only the date, without "<N>d since regimen completion" — anchor dates are not used
- Mobile adaptation is basic (sidebar becomes inline-block), not the tabbar from the design
- Constitution drop caps through `::first-letter` work only if the first element is `<p>` (if Markdown starts with `<h1>`, there is no drop cap)

## Version

- Phase 1+2 (read-only Toscana): `b60ba83`..`629392a`
- Phase 3 (write + cascade): `daac515` (3A) → `30cba11` (3B) → `e1f0814` (3C) → `95b87e6` (3D) → `70e92ef` (3C-extend + 3E) → `834c452` (3F + 3G)
- 7/8 stages closed. 3H (integration tests) deferred — requires a `DB_PATH` refactor for the test fixture.
- Stable as of `2026-05-16`
