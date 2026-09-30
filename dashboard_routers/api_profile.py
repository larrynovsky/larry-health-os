"""dashboard_routers.api_profile — HTMX inline-edit endpoints для patient_profile.

Sprint 5 step 5 pilot (2026-05-22): извлечено из dashboard.py.

2 endpoints:
  GET  /api/profile/{key:path}/edit — render edit-input (textarea для JSON, input для text)
  POST /api/profile/{key:path}      — save value, валидация JSON если is_json
"""
from __future__ import annotations

import i18n

import html as _html
import json

from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import HTMLResponse

from dashboard_db import _q, _w, _log_edit
from dashboard_views import _profile_view_cell


router = APIRouter(prefix="/api/profile", tags=["profile"])


@router.get("/{key:path}/edit", response_class=HTMLResponse)
def api_profile_edit(key: str):
    rows = _q(
        "SELECT value_text, value_json FROM patient_profile WHERE key=?",
        (key,),
    )
    if not rows:
        raise HTTPException(404, f"profile key not found: {key}")
    r = rows[0]
    if r["value_json"]:
        body = (
            f'<textarea name="value" rows="6" autofocus '
            f'hx-post="/api/profile/{_html.escape(key)}" '
            f'hx-trigger="blur" hx-swap="outerHTML" '
            f'hx-headers=\'{{"X-Field-Kind": "json"}}\' '
            f'class="kv-edit-input kv-edit-input--json">'
            f'{_html.escape(r["value_json"])}</textarea>'
        )
        td_cls = "kv-value--json kv-value--editing"
    else:
        current = r["value_text"] or ""
        body = (
            f'<input name="value" value="{_html.escape(current)}" autofocus '
            f'hx-post="/api/profile/{_html.escape(key)}" '
            f'hx-trigger="blur,keyup[key==&quot;Enter&quot;]" hx-swap="outerHTML" '
            f'class="kv-edit-input">'
        )
        td_cls = "kv-value--text kv-value--editing"
    return HTMLResponse(f'<td class="kv-value {td_cls}">{body}</td>')


@router.post("/{key:path}", response_class=HTMLResponse)
def api_profile_save(
    key: str,
    value: str = Form(...),
    x_field_kind: str | None = None,
):
    rows = _q(
        "SELECT value_text, value_json FROM patient_profile WHERE key=?",
        (key,),
    )
    if not rows:
        raise HTTPException(404, f"profile key not found: {key}")
    r = rows[0]

    is_json = bool(r["value_json"]) or (x_field_kind == "json")

    if is_json:
        try:
            parsed = json.loads(value)
            normalized = json.dumps(parsed, ensure_ascii=False, indent=2)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            return HTMLResponse(
                f'<td class="kv-value kv-value--json kv-value--error" '
                f'hx-get="/api/profile/{_html.escape(key)}/edit" '
                f'hx-trigger="click" hx-swap="outerHTML" '
                f'title="{_html.escape(i18n.t("dashboard.profile.json_error", error=str(exc)))}">'
                f'<pre class="mono">{_html.escape(r["value_json"] or "")}</pre>'
                f'</td>',
                status_code=422,
            )
        old = r["value_json"]
        _w(
            """UPDATE patient_profile
               SET value_json=?, updated_at=datetime('now'), updated_by='dashboard'
               WHERE key=?""",
            (normalized, key),
        )
        _log_edit(
            "patient_profile", key, "edit_field",
            field="value_json", old_value=old, new_value=normalized,
        )
        return HTMLResponse(_profile_view_cell(key, None, normalized))
    else:
        old = r["value_text"]
        _w(
            """UPDATE patient_profile
               SET value_text=?, updated_at=datetime('now'), updated_by='dashboard'
               WHERE key=?""",
            (value, key),
        )
        _log_edit(
            "patient_profile", key, "edit_field",
            field="value_text", old_value=old, new_value=value,
        )
        return HTMLResponse(_profile_view_cell(key, value, None))
