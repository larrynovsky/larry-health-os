"""dashboard_state — shared application state (templates, constants).

Sprint 5 finish (2026-05-22): извлечено из dashboard.py для разрыва циклической
зависимости между dashboard и dashboard_routers.views.

Содержит:
  - templates: Jinja2Templates instance с зарегистрированными фильтрами
  - CONSTITUTIONS_DIR: path к ~/health/constitutions/
  - _CONST_TITLE_KEYS: mapping slug → ключ i18n
"""
from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

from dashboard_filters import _pretty_json, _short, _age_days, _iso_date, _eval_running


ROOT = Path(__file__).resolve().parent
TEMPLATES_DIR = ROOT / "dashboard_templates"
STATIC_DIR = ROOT / "dashboard_static"
CONSTITUTIONS_DIR = ROOT / "constitutions"

_CONST_TITLE_KEYS = {
    "sleep": "dashboard.constitution.title.sleep",
    "stress": "dashboard.constitution.title.stress",
    "nervous_system": "dashboard.constitution.title.nervous_system",
    "nutrition": "dashboard.constitution.title.nutrition",
    "movement": "dashboard.constitution.title.movement",
}


templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.filters["pretty_json"] = _pretty_json
templates.env.filters["short"] = _short
templates.env.filters["age_days"] = _age_days
templates.env.filters["iso_date"] = _iso_date
templates.env.filters["eval_running"] = _eval_running

# CSS cache-bust by mtime (Wave 6 Toscana)
_css_file = STATIC_DIR / "dashboard.css"
try:
    templates.env.globals["css_version"] = int(_css_file.stat().st_mtime)
except OSError:
    templates.env.globals["css_version"] = 0

# JS cache-bust (Phase 3.3A — HTMX)
_js_file = STATIC_DIR / "htmx.min.js"
try:
    templates.env.globals["js_version"] = int(_js_file.stat().st_mtime)
except OSError:
    templates.env.globals["js_version"] = 0
