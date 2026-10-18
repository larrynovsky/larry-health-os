"""dashboard_routers.api_memory — владелец сам правит память и запреты (07.10).

Зачем (решения владельца 07.10, нить borrow-botkin, этап A плана единой памяти): память
о здоровье — общая для бота, брифа, Claude и ChatGPT, а поправить её владелец не мог:
бот только показывал записи (/memory), функция гашения была, кнопки к ней не было.
Запрет «не предлагай мне X» — тоже память (класс `preference`), и правится здесь же.

Два действия:
  POST /api/memory/{fact_id}/retire — погасить запись (не удалить: memory_facts_db.retire_fact)
  POST /api/memory/ban              — добавить запрет; запись с дашборда сама по себе «да» владельца

Писатель памяти один — memory_facts_db; своего SQL в памяти здесь нет. След правки живёт в
самой записи: у погашенной — причина (retire_reason) и время, у запрета — source=owner_dashboard
и дата; второго журнала не заводим. От чужих сайтов пишущие ручки закрывает общий
_perimeter_guard дашборда (Sec-Fetch-Site / Origin), не этот модуль.
"""
from __future__ import annotations

import html as _html
import re

from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import HTMLResponse

import i18n

router = APIRouter(prefix="/api/memory", tags=["memory"])

BAN_CLASS = "preference"
BAN_PREFIX = "no_suggest:"
MAX_BAN_LEN = 200


def _slug(text: str) -> str:
    s = re.sub(r"\W+", "_", text.lower()).strip("_")   # \w в Python — любые буквы, и кириллица
    return s[:60] or "x"


@router.post("/{fact_id}/retire", response_class=HTMLResponse)
def api_memory_retire(fact_id: int):
    import memory_facts_db as mf
    if not mf.retire_fact(fact_id, i18n.t("dashboard.memory.retire_reason")):
        raise HTTPException(404, i18n.t("dashboard.memory.not_active", id=fact_id))
    return HTMLResponse("")


@router.post("/ban", response_class=HTMLResponse)
def api_memory_ban(text: str = Form(...)):
    text = " ".join(text.split())
    if not text or len(text) > MAX_BAN_LEN:
        raise HTTPException(400, i18n.t("dashboard.memory.ban_invalid", n=MAX_BAN_LEN))
    import memory_facts_db as mf
    fid = mf.save_fact(BAN_CLASS, text, key=BAN_PREFIX + _slug(text), confidence=1.0,
                       source="owner_dashboard")
    if fid < 0:
        raise HTTPException(409, i18n.t("dashboard.memory.ban_refused"))
    return HTMLResponse(
        f'<li id="mem-{fid}">{_html.escape(text)} '
        f'<button class="action-pill action-pill--ghost" hx-post="/api/memory/{fid}/retire" '
        f'hx-confirm="{_html.escape(i18n.t("dashboard.memory.confirm_unban"))}" hx-target="closest li" '
        f'hx-swap="delete">{_html.escape(i18n.t("dashboard.memory.remove"))}</button></li>')
