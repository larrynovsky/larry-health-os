"""dashboard_routers.api_tasks — task edit/save/done/snooze endpoints.

Sprint 5 step 7 (2026-05-22): извлечено из dashboard.py.

4 endpoints:
  GET  /api/tasks/{task_id}/edit    — render edit-input
  POST /api/tasks/{task_id}         — save content
  POST /api/tasks/{task_id}/done    — mark completed
  POST /api/tasks/{task_id}/snooze  — set deadline (days)
"""
from __future__ import annotations

import i18n
from _time_inject import get_today  # seam

import html as _html
from datetime import date as _date, timedelta as _td

from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import HTMLResponse

from dashboard_db import _q, _w, _log_edit


router = APIRouter(prefix="/api/tasks", tags=["tasks"])


@router.get("/{task_id}/edit", response_class=HTMLResponse)
def api_task_edit(task_id: int):
    rows = _q("SELECT content FROM tasks WHERE id=?", (task_id,))
    if not rows:
        raise HTTPException(404, f"task {task_id} not found")
    current = rows[0]["content"] or ""
    return HTMLResponse(
        f'<p class="card-description card-description--editing">'
        f'<input name="value" value="{_html.escape(current)}" '
        f'hx-post="/api/tasks/{task_id}" '
        f'hx-trigger="blur,keyup[key==&quot;Enter&quot;]" hx-swap="outerHTML" '
        f'autofocus class="kv-edit-input"></p>'
    )


@router.post("/{task_id}", response_class=HTMLResponse)
def api_task_save(task_id: int, value: str = Form(...)):
    rows = _q("SELECT content FROM tasks WHERE id=?", (task_id,))
    if not rows:
        raise HTTPException(404, f"task {task_id} not found")
    old = rows[0]["content"]
    _w("UPDATE tasks SET content=? WHERE id=?", (value, task_id))
    _log_edit("tasks", task_id, "edit_content", field="content",
              old_value=old, new_value=value)
    return HTMLResponse(
        f'<p class="card-description" '
        f'hx-get="/api/tasks/{task_id}/edit" hx-trigger="click" hx-swap="outerHTML" '
        f'title="{_html.escape(i18n.t("dashboard.edit_hint"))}">{_html.escape(value)}</p>'
    )


@router.post("/{task_id}/done", response_class=HTMLResponse)
def api_task_done(task_id: int):
    rows = _q("SELECT status, type FROM tasks WHERE id=?", (task_id,))
    if not rows:
        raise HTTPException(404, f"task {task_id} not found")
    if rows[0]["status"] == "completed":
        return HTMLResponse("")
    # Опросник закрывается ТОЛЬКО заполнением (assessment_importer → lab_results, §16):
    # закрытая задача без результата опросника не означает выполненную оценку.
    # Fail-closed (§13, WSTG-BUSL-06); «отложить» — snooze, он законен.
    if rows[0]["type"] == "assessment":
        raise HTTPException(
            409, "опросник закрывается заполнением: бот → «📋 Заполнить» (или отложи)")
    # Вопрос — тот же класс: кнопка без текста ответа не завершает обмен.
    # Ответ принимает бот (реплай или /done <id> <текст>);
    # база всё равно отвергнет пустое закрытие.
    if rows[0]["type"] == "question":
        raise HTTPException(
            409, "вопрос закрывается ответом: реплай на сообщение бота "
                 "или /done <id> <ответ> (или отложи) — "
                 "docs/how-to/answer_a_question.md")
    old_status = rows[0]["status"]
    _w(
        """UPDATE tasks SET status='completed', resolved_at=datetime('now'),
                          resolution_type='completed_via_dashboard'
           WHERE id=?""",
        (task_id,),
    )
    _log_edit("tasks", task_id, "done", field="status",
              old_value=old_status, new_value="completed")
    return HTMLResponse("")


@router.post("/{task_id}/snooze", response_class=HTMLResponse)
def api_task_snooze(task_id: int, days: int = 7):
    if days not in (3, 7, 30) and not (1 <= days <= 365):
        raise HTTPException(400, f"days must be 1..365, got {days}")
    rows = _q("SELECT status FROM tasks WHERE id=?", (task_id,))
    if not rows:
        raise HTTPException(404, f"task {task_id} not found")
    deadline = (get_today() + _td(days=days)).isoformat()
    old_status = rows[0]["status"]
    _w(
        """UPDATE tasks SET status='snoozed', deadline=? WHERE id=?""",
        (deadline, task_id),
    )
    _log_edit("tasks", task_id, "snooze", field="status",
              old_value=old_status, new_value=f"snoozed until {deadline}")
    return HTMLResponse("")
