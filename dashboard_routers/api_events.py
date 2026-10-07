"""dashboard_routers.api_events — events form + ping endpoints.

Sprint 5 finish (2026-05-22): извлечено из dashboard.py.

2 endpoints:
  POST /api/events/new — создаёт events + encounters/diagnostic_events
  GET  /api/ping       — HTMX smoke
"""
from __future__ import annotations

import i18n
import html as _html

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

import health_db as _db


router = APIRouter(prefix="/api", tags=["events"])


@router.post("/events/new", response_class=HTMLResponse)
def api_event_new(
    request: Request,
    event_type:      str = Form(...),
    effective_date:  str = Form(...),
    performer:       str = Form(""),
    performer_role:  str = Form(""),
    location:        str = Form(""),
    episode_id:      str = Form(""),
    notes:           str = Form(""),
    specialty:       str = Form(""),
    reason_text:     str = Form(""),
    assessment:      str = Form(""),
    enc_plan:        str = Form(""),
    modality:        str = Form(""),
    abnormal_flags:  str = Form(""),
    interpreted_report: str = Form(""),
):
    encounter = None
    if event_type == "encounter":
        encounter = {
            "class": "outpatient",
            "specialty":    specialty    or None,
            "reason_text":  reason_text  or None,
            "assessment":   assessment   or None,
            "plan":         enc_plan     or None,
        }

    diagnostic = None
    import events_db
    if event_type in events_db.DIAGNOSTIC_TYPES:
        diagnostic = {
            "type":                event_type,
            "modality":            modality            or None,
            "interpreted_report":  interpreted_report  or None,
            "abnormal_flags":      abnormal_flags      or None,
        }

    ep_id = int(episode_id) if episode_id.strip().isdigit() else None

    try:
        _db.save_event(
            event_type     = event_type,
            effective_date = effective_date,
            status         = "completed",
            performer      = performer      or None,
            performer_role = performer_role or None,
            location       = location       or None,
            episode_id     = ep_id,
            notes          = notes          or None,
            recorded_by    = "dashboard",
            encounter      = encounter,
            diagnostic     = diagnostic,
        )
    except Exception as exc:
        return HTMLResponse(
            f'<p class="hint" style="color:var(--color-warning)">{_html.escape(i18n.t("common.error.generic", error=str(exc)))}</p>',
            status_code=500,
        )

    from starlette.responses import Response as _Resp
    r = _Resp(status_code=204)
    r.headers["HX-Redirect"] = "/medical-record"
    return r


@router.get("/ping", response_class=HTMLResponse)
def api_ping():
    """HTMX smoke endpoint."""
    return HTMLResponse('<span class="chip chip--active">pong</span>')
