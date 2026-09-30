"""dashboard_routers.api_status_actions — status-change endpoints.

Sprint 5 step 6 (2026-05-22): извлечено из dashboard.py.
HV-2 (2026-05-24): eval action добавлен в hypothesis handler (async).

5 endpoints (3 группы):
  - POST /api/hypotheses/{hyp_id}/{action}  — confirm/reject/eval (eval — фоном, 2026-08-29)
  - GET  /api/hypotheses/{hyp_id}/card      — карточка для опроса во время eval
  - POST /api/protocols/{prot_id}/retire    — retire
  - POST /api/problems/{prob_id}/{action}   — archive/reopen
  - POST /api/experiments/{exp_id}/{action} — complete/cancel
"""
from __future__ import annotations

import i18n
import html as _html
from _time_inject import get_now, get_today  # seam

import json
import subprocess
import sys
from pathlib import Path
from datetime import date, datetime

import json as _json_mod

from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import HTMLResponse

from dashboard_db import _q, _w, _log_edit
from dashboard_filters import _eval_running
from dashboard_state import templates
import logging as _logging
_log = _logging.getLogger(__name__)


router = APIRouter(prefix="/api", tags=["status-actions"])

def _spawn_eval(hyp_id: int) -> None:
    """Консилиум — в ОТДЕЛЬНОМ процессе (start_new_session), не в event loop.
    Урок 30.08: post-commit хук перезапускает дашборд на каждом коммите, задачи
    loop'а гибли на Раунде B. Лог процесса — logs/consilium_eval.log."""
    root = Path(__file__).resolve().parent.parent
    log_path = root / "logs" / "consilium_eval.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "ab") as lf:
        subprocess.Popen(
            [sys.executable, str(root / "hypothesis_resolution.py"), "--eval", str(hyp_id)],
            cwd=str(root), stdout=lf, stderr=subprocess.STDOUT,
            start_new_session=True, close_fds=True,
        )


def _render_card(hyp_id: int, eval_result_html: str = "") -> HTMLResponse:
    """Карточка гипотезы по текущему состоянию БД (POST-ответы и GET …/card)."""
    updated_rows = _q(
        "SELECT id, key, value, confidence, source, active, created_at, updated_at "
        "FROM memory WHERE id=? AND category='hypothesis'",
        (hyp_id,),
    )
    if not updated_rows:
        return HTMLResponse(f'<div class="eval-result eval-result--error">{_html.escape(i18n.t("dashboard.hypothesis.not_found", hypothesis_id=hyp_id))}</div>')
    h = dict(updated_rows[0])
    try:
        h["payload"] = _json_mod.loads(h["value"] or "{}")
    except Exception:
        h["payload"] = {}
    if not eval_result_html and h["payload"].get("eval_error"):
        eval_result_html = (
            f'<div class="eval-result eval-result--error">'
            f'⚠️ {h["payload"]["eval_error"]}'
            f'<br><button class="action-pill action-pill--ghost eval-retry"'
            f' style="margin-top:6px;font-size:12px"'
            f' hx-post="/api/hypotheses/{hyp_id}/eval"'
            f' hx-target="#hyp-{hyp_id}"'
            f' hx-swap="outerHTML"'
            f' hx-indicator="#eval-spin-{hyp_id}">{_html.escape(i18n.t("dashboard.hypothesis.retry"))}</button>'
            f'</div>'
        )
    card_html = templates.env.get_template("_hypothesis_card.html").render(
        h=h, eval_result_html=eval_result_html
    )
    return HTMLResponse(card_html)


@router.get("/hypotheses/{hyp_id}/card", response_class=HTMLResponse)
async def api_hypothesis_card(hyp_id: int):
    """Текущая карточка — опрашивается шаблоном, пока идёт фоновой консилиум."""
    return _render_card(hyp_id)



@router.post("/hypotheses/{hyp_id}/{action}", response_class=HTMLResponse)
async def api_hypothesis_action(hyp_id: int, action: str):
    if action not in ("confirm", "reject", "eval", "doctor-brief"):
        raise HTTPException(400, f"action must be confirm|reject|eval|doctor-brief, got {action}")

    rows = _q(
        "SELECT value, active FROM memory WHERE id=? AND category='hypothesis'",
        (hyp_id,),
    )
    if not rows:
        raise HTTPException(404, f"hypothesis {hyp_id} not found")

    # Запуск МДТ-консилиума в фоне: длительная работа может пережить
    # HTTP-соединение, поэтому завершение запроса не доказывает доставку.
    # Замок в payload
    # (status=testing + eval_started_at, TTL — dashboard_filters.EVAL_TTL_MIN),
    # отдельный процесс (hypothesis_resolution --eval), карточка сразу;
    # результат — опросом GET …/card (шаблон) и outbox бота
    # (jobs.scheduled.deliver_unsent_outcomes).
    if action == "eval":
        try:
            payload = json.loads(rows[0]["value"] or "{}")
        except (TypeError, ValueError):
            payload = {}

        if _eval_running(payload):
            return _render_card(hyp_id, eval_result_html=(
                '<div class="eval-result eval-result--info">'
                f'{_html.escape(i18n.t("dashboard.hypothesis.already_running"))}</div>'))

        payload["status"] = "testing"
        payload["eval_started_at"] = get_now().isoformat(timespec="seconds")
        payload.pop("eval_error", None)
        _w(
            "UPDATE memory SET value=?, updated_at=datetime('now') WHERE id=?",
            (json.dumps(payload, ensure_ascii=False), hyp_id),
        )
        _spawn_eval(hyp_id)
        return _render_card(hyp_id)

    # ── doctor-brief ─────────────────────────────────────────────────────────
    if action == "doctor-brief":
        return await api_hypothesis_doctor_brief(hyp_id)

    # ── confirm / reject ─────────────────────────────────────────────────────
    if not rows[0]["active"]:
        return HTMLResponse("")

    raw = rows[0]["value"] or "{}"
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            payload = {"_raw": raw}
    except (TypeError, ValueError, json.JSONDecodeError):
        payload = {"_raw": raw}

    # L1: status — читается шаблоном (_hypothesis_card.html) и get_open_hypotheses()
    payload["status"]      = "confirmed" if action == "confirm" else "rejected"
    # L3: resolved_as — action-time значение; resolution_type НЕ трогаем (creation-time)
    payload["resolved_as"] = "confirmed" if action == "confirm" else "rejected"
    payload["resolved_by"] = "dashboard"
    payload["resolved_at"] = get_now().isoformat()

    new_value  = json.dumps(payload, ensure_ascii=False)
    # confirm → активная принятая гипотеза; reject → закрыта
    new_active = 1 if action == "confirm" else 0
    _w(
        "UPDATE memory SET active=?, value=?, updated_at=datetime('now') WHERE id=?",
        (new_active, new_value, hyp_id),
    )
    _log_edit(
        "memory", hyp_id, action,
        field="status",
        old_value=raw,
        new_value=payload["status"],
    )

    # L1 cascade: reject → закрываем задачи, связанные с этой гипотезой
    if action == "reject":
        _w(
            """UPDATE tasks SET status='dismissed', resolved_text=?
               WHERE content LIKE ? AND status='open'""",
            (i18n.t("dashboard.hypothesis.rejected", hypothesis_id=hyp_id), f"%[hyp #{hyp_id}]%"),
        )

    # При confirm — если есть payload.test, создаём task (идемпотентно)
    if action == "confirm":
        test_text = (payload.get("test") or "").strip()
        if test_text:
            import gp_context
            # дата последней сдачи — в самом тексте задачи (27.09); дедуп ниже сравнивает уже её
            test_text = gp_context.annotate_lab_recency(test_text[:500])
            existing_task = _q(
                "SELECT id FROM tasks WHERE source='dashboard_confirm' "
                "AND content=? AND status='open'",
                (test_text,),
            )
            if not existing_task:
                task_id = _w(
                    """INSERT INTO tasks
                       (source, source_date, type, content, priority, status, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, datetime('now'))""",
                    ("dashboard_confirm", get_today().isoformat(), "followup",
                     test_text, "medium", "open"),
                )
                _log_edit("tasks", task_id, "create_from_hypothesis_confirm",
                          field="content", old_value=None, new_value=test_text)

    # reject → карточку удаляем; confirm → рендерим обновлённую карточку
    if action == "reject":
        return HTMLResponse("")

    updated_rows = _q(
        "SELECT id, key, value, confidence, source, active, created_at, updated_at "
        "FROM memory WHERE id=? AND category='hypothesis'",
        (hyp_id,),
    )
    if not updated_rows:
        return HTMLResponse("")
    h = dict(updated_rows[0])
    try:
        h["payload"] = _json_mod.loads(h["value"] or "{}")
    except Exception:
        h["payload"] = {}
    card_html = templates.env.get_template("_hypothesis_card.html").render(
        h=h, eval_result_html=""
    )
    return HTMLResponse(card_html)


@router.post("/hypotheses/{hyp_id}/doctor-brief", response_class=HTMLResponse)
async def api_hypothesis_doctor_brief(hyp_id: int):
    """Генерирует клинический запрос по принятой гипотезе и отправляет в Telegram."""
    import asyncio
    import subprocess
    from pathlib import Path

    rows = _q(
        "SELECT value FROM memory WHERE id=? AND category='hypothesis'",
        (hyp_id,),
    )
    if not rows:
        raise HTTPException(404, f"hypothesis {hyp_id} not found")

    try:
        payload = _json_mod.loads(rows[0]["value"] or "{}")
    except Exception:
        payload = {}

    if payload.get("status") != "confirmed":
        return HTMLResponse(f'<span class="doctor-brief-error">{_html.escape(i18n.t("dashboard.doctor_brief.not_confirmed"))}</span>')

    # Генерация клинического запроса (блокирующий LLM-вызов)
    try:
        import sys
        from pathlib import Path as _P
        sys.path.insert(0, str(_P(__file__).parent.parent))
        import consult_prep as cp
        report = await asyncio.to_thread(cp.prepare_hypothesis_query, hyp_id)
    except Exception as exc:
        _log.exception(f"doctor-brief #{hyp_id}: prepare_hypothesis_query failed")
        return HTMLResponse(f'<span class="doctor-brief-error">{_html.escape(i18n.t("dashboard.doctor_brief.generation_error", error=str(exc)))}</span>')

    # Отправка в Telegram
    import os
    from secrets_paths import secrets_dir
    _sec = secrets_dir()
    token_file  = _sec / "telegram_token"
    chat_file   = _sec / "telegram_chat_id"
    tg_ok = False
    if token_file.exists() and chat_file.exists():
        try:
            token   = token_file.read_text().strip()
            chat_id = chat_file.read_text().strip()
            # Telegram ограничивает 4096 символов — отправляем частями
            chunk_size = 4000
            chunks = [report[i:i+chunk_size] for i in range(0, len(report), chunk_size)]
            for chunk in chunks:
                subprocess.run(
                    ["curl", "-s", "-X", "POST",
                     f"https://api.telegram.org/bot{token}/sendMessage",
                     "-d", f"chat_id={chat_id}",
                     "--data-urlencode", f"text={chunk}"],
                    capture_output=True, timeout=15,
                )
            tg_ok = True
        except Exception as exc:
            _log.warning(f"doctor-brief #{hyp_id}: telegram send failed: {exc}")

    if tg_ok:
        return HTMLResponse(f'<span style="color:var(--color-active)">{_html.escape(i18n.t("dashboard.doctor_brief.sent"))}</span>')
    else:
        return HTMLResponse(f'<span class="doctor-brief-error">{_html.escape(i18n.t("dashboard.doctor_brief.not_sent"))}</span>')


@router.post("/protocols/{prot_id}/retire", response_class=HTMLResponse)
def api_protocol_retire(prot_id: int):
    rows = _q("SELECT status FROM protocols WHERE id=?", (prot_id,))
    if not rows:
        raise HTTPException(404, f"protocol {prot_id} not found")
    if rows[0]["status"] == "retired":
        return HTMLResponse("")
    old_status = rows[0]["status"]
    _w(
        "UPDATE protocols SET status='retired', retired_at=datetime('now') WHERE id=?",
        (prot_id,),
    )
    _log_edit("protocols", prot_id, "retire", field="status",
              old_value=old_status, new_value="retired")
    return HTMLResponse("")


@router.post("/problems/{prob_id}/{action}", response_class=HTMLResponse)
def api_problem_action(prob_id: int, action: str):
    """action ∈ {archive, reopen}."""
    if action not in ("archive", "reopen"):
        raise HTTPException(400, f"action must be archive|reopen, got {action}")
    rows = _q("SELECT status FROM problem_list WHERE id=?", (prob_id,))
    if not rows:
        raise HTTPException(404, f"problem {prob_id} not found")
    old_status = rows[0]["status"]
    new_status = "resolved" if action == "archive" else "active_monitoring"
    if old_status == new_status:
        return HTMLResponse("")
    _w(
        "UPDATE problem_list SET status=?, last_updated=datetime('now') WHERE id=?",
        (new_status, prob_id),
    )
    _log_edit("problem_list", prob_id, action, field="status",
              old_value=old_status, new_value=new_status)
    return HTMLResponse("")


@router.post("/proposals/{prop_id}/{action}", response_class=HTMLResponse)
def api_proposal_action(prop_id: int, action: str):
    """action ∈ {approve, reject}. approve → apply_proposal, reject → reject_proposal.

    Ревью problem_list_proposals из веб-дашборда (раньше только бот /approve|/reject).
    """
    if action not in ("approve", "reject"):
        raise HTTPException(400, f"action must be approve|reject, got {action}")
    rows = _q("SELECT status FROM problem_list_proposals WHERE id=?", (prop_id,))
    if not rows:
        raise HTTPException(404, f"proposal {prop_id} not found")
    if rows[0]["status"] != "pending":
        return HTMLResponse("")  # уже разобран — идемпотентно
    import proposals_db
    if action == "approve":
        proposals_db.apply_proposal(prop_id)
        _log_edit("problem_list_proposals", prop_id, "approve",
                  field="status", old_value="pending", new_value="approved")
    else:
        proposals_db.reject_proposal(prop_id, note="отклонено из дашборда")
        _log_edit("problem_list_proposals", prop_id, "reject",
                  field="status", old_value="pending", new_value="rejected")
    return HTMLResponse("")


# Эндпоинт POST /experiments/{id}/{action} снят (BL-EXP-1, 2026-07-10):
# конвейер экспериментов ретайрится, таблица experiments снята. Дашборд больше
# не показывает карточки экспериментов (get_active_experiments → []), кнопок нет.
