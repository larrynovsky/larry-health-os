[English](dashboard_architecture.en.md) · **Русский**

# Dashboard — Explanation

Почему такие решения. Reference (что есть) — `docs/reference/dashboard.md`. Изменения — `docs/how-to/extend_dashboard.md`.

## Зачем дашборд

Контекст: до 2026-05-15 «живые факты» о пациенте жили в трёх местах:
- БД (10+ таблиц `patient_profile`, `protocols`, `periods`, `hypotheses`)
- Markdown-конституции (5 файлов, регенерируются скриптом)
- Telegram-бот (read-only выдача, без обзора)

Аудит #179 (AUDIT-DATA-LIFECYCLE) показал три класса «кладбищ»: `patterns`/`context_events`/`recommendations` накапливаются, но не закрываются. Кладбище = факт, который пациент не видит в обзоре.

Дашборд решает один контракт: **визуальное место, где видно всё что есть, без необходимости спрашивать у Telegram-бота или читать markdown в редакторе**. Read-first, edit-second.

## Почему Tailscale-only

Trust boundary = приватная mesh-сеть Tailscale (2 устройства: MacBook + Studio).
Альтернативы рассмотрены:
- **Cloudflare Tunnel / Tailscale Funnel наружу** — открывает дашборд для интернета. Требует auth-слой (OAuth, basic auth, etc.). Большой attack surface для PII пациента (гены, диагнозы, психология).
- **Локально (`127.0.0.1`)** — Studio работает в headless-режиме, MacBook не видит локалхост.
- **Tailscale interface** — компромисс: mesh-only доступ, без auth-слоя (доверие = доступ к mesh), пациент видит дашборд с любого устройства в своей сети.

Решение: `uvicorn.run(app, host="<studio_host>", port=8001)`. Хост явно Tailscale-IP, не `0.0.0.0` — это запирает дашборд от случайного слушания на других интерфейсах (например, если Studio когда-то подключится к незнакомой сети).

## Почему FastAPI + Jinja2, не Flask

Изначально считалось «возьму Flask» — это упоминается в `dashboard-design/README.md`. На практике FastAPI оказался удобнее по трём причинам:
1. **Starlette `TemplateResponse(request, name, ctx)`** — новый API проще, чем `flask.render_template`, потому что прокидывает `request` автоматически (нужно для `request.url.path` в sidebar)
2. **Async-ready** — если потом понадобится stream long responses (большие конституции, history queries), переход безболезненный.
3. **Один процесс с другими FastAPI-микросервисами** в Health OS (если когда-то появится Telegram-webhook FastAPI — единый стек).

Trade-off: дизайн-документ упоминает Flask, реализация — FastAPI. Контракт компонентов идентичен (Jinja2 + класс-based CSS), поэтому смешение допустимо.

## Почему server-side render, не SPA

В репо был `static/index.html` — 51KB React SPA от VPS-эры. Отключён, потому что:
- Build-step (React+Webpack) усложняет deploy
- State-sync (frontend ↔ backend) ломается через раз
- Безопасность — нужны API endpoints, CSRF, JWT и весь этот «корпоративный» слой

Дашборд для **одного пользователя** не нуждается в SPA-сложности. Jinja2 рендерит HTML за один проход, нет client-state, нет XHR. Это **простота**, которая чувствуется в чтении кода.

HTMX появится в Phase 3 — это всё ещё «server-side render с маленькими интерактивностями», не SPA.

## Почему Toscana, а не «нормальный дашборд»

Контракт пациента: «**это не дашборд, это дневник**».

Дашборд предполагает:
- Высокую плотность чисел
- Bar charts / heatmaps
- Bold-bombing для «highlight-and-scan»
- Холодные цвета (синий, серый, светлый фон)

Дневник предполагает:
- Серифную типографику для длинного чтения
- Тёплые цвета (терракотовый, кремовый)
- Воздух между мыслями
- Italic-метки секций («под взглядом сейчас») вместо CAPS LETTERS
- Inset-тени, не приподнятие — характер ламповости, не плакатности

Дизайн-система формализована в `~/health/dashboard-design/` (вне репо, потому что owned пациентом). Контракт:
- **Только токены** в шаблонах. Никаких `#XXXXXX`, никаких `14px`. Если нужного нет — добавь в `tokens.css`.
- **Серифы для смысла, sans для UI, mono для значений**. Это семантическая разметка через шрифт.
- **`№` вместо `#`** для ID — литературный регистр, не GitHub.
- **lowercase italic для section captions** — «гипотезы», не «ГИПОТЕЗЫ» и не «**Гипотезы**».

## Антипаттерн: Bold-bombing

Обнаружен при критике `/constitutions/sleep` 2026-05-15. Markdown отдаёт `<strong>` для каждого лида списка, и при недостаточном vertical spacing глаз перестаёт реагировать на bold-якорь — превращается в зашумлённую сетку. Решение:
- Воздух между `<li>`: 4px → 14px
- Воздух перед h2: 24px → 44px
- Узкая мера строки: `max-width: 65ch` (через `.narrative` класс)

Урок: bold нужен **с воздухом вокруг**, иначе работает как сплошной паттерн, а не выделение.

## Cache-bust через mtime

Проблема: правка CSS на сервере не отражается в браузере, потому что Safari кеширует `dashboard.css` навсегда.

Решение: `templates.env.globals["css_version"] = int(mtime(dashboard.css))`. `<link href="dashboard.css?v={{ css_version }}">` меняется при каждой правке CSS.

Trade-off: чтобы новый `css_version` попал в HTML, FastAPI должен **перечитать** mtime при загрузке `dashboard.py`. То есть после правки CSS нужен `launchctl reload` дашборда, либо `templates.env.globals["css_version"]` должен вычисляться **на каждый запрос** (это дороже). Сейчас выбран первый путь — reload-on-deploy, потому что deploys и так требуют reload.

## Deploy-flow

Single canonical Studio (после миграции 2026-05-09, docs/explanation/git_architecture.md):
1. Редактируешь файлы локально (MacBook → outputs) или через ssh
2. `scp` на Studio (`~/health_scripts/dashboard_static/` или `dashboard_templates/`)
3. Если правил `.py` — `launchctl unload && load` дашборда
4. Если правил только CSS/HTML — браузер сам подхватит благодаря cache-bust + Jinja авто-reload шаблонов
5. `curl http://<studio_host>:8001/...` для smoke-check
6. `git commit` на Studio немедленно (CLAUDE.md правило #1)

Все правки `.py` через ssh-edit требуют немедленного commit, иначе при следующем `git pull` / `reset` пропадут (правило #1 CLAUDE.md, инцидент 2026-05-10).

## Текущие компромиссы и known-issues

| Что | Почему сейчас так | Что бы было лучше |
|---|---|---|
| Google Fonts через CDN | Самый дешёвый старт | Self-host шрифтов в `dashboard_static/fonts/` — закрывает «дашборд работает offline» |
| Mobile = inline-block sidebar | Tabbar требует FAB + 4-pick decision | Tabbar из дизайна §11 — отдельная задача |
| `today_meta = «<дата>»` | Опорные даты лечения (конец схемы, старт устройства) не задействованы | Запрос к `periods` для активных лечений + относительная дата |
| Cache-bust требует reload | CSS mtime читается один раз при boot | `globals` через callable — дороже, но live |
| Read-only | По дизайну Phase 1+2 | Phase 3 в роадмапе |
| Дроп-кап через `::first-letter` | Минимум разметки, работает на сыром markdown | Может не сработать если первый элемент — h1/table |

## Что не делалось

- Дашборд не пытается заменить Telegram-бота. Бот остаётся для «push» (утренний брифинг, алерты), дашборд — для «pull» (когда пациент сам захотел посмотреть)
- Дашборд не пытается быть accessible для эссе из чужих рук — это интерфейс для одного пользователя, который знает контекст
- Дашборд не делает аналитику (нет графиков, нет агрегаций) — для этого есть `longitudinal_analysis.py` и утренний бриф

## Связанные документы

- Дизайн-система Toscana: `~/health/dashboard-design/README.md` + 4 файла Diataxis
- AUDIT-DATA-LIFECYCLE: `outputs/audit/data_lifecycle_2026-05-15.md` (мотивация)
- Архитектура canonical Studio: `docs/explanation/git_architecture.md`
- Inv-doc правила: `CLAUDE.md` §1–9
