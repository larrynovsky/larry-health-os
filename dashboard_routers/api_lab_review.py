"""dashboard_routers.api_lab_review — интерфейс сверки распознанных анализов (#1).

Человек открывает лист staging своей базы, отмечает ошибки и подтверждает добавление.
У владельца установки сохранён отдельный операторский доступ к тенантам.
Promote запускается ПОДПРОЦЕССОМ с HEALTH_DATA_DIR=<тенант> —
так канон пишется в БД нужного тенанта (процесс дашборда живёт в своём тенанте,
health_db.DB_PATH фиксирован; кросс-тенантная запись — только через субпроцесс).

Кросс-тенантное ЧТЕНИЕ staging партнёра из дашборда владельца — санкционированный
операторский путь (роль разработчика/ревьюера), не runtime-пользователя.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

import health_db
import i18n
from labs_db import WAITING_REVIEW_SQL
from secrets_paths import is_owner

router = APIRouter(tags=["lab_review"])

_HEALTH_SCRIPTS = str(__import__("pathlib").Path(__file__).resolve().parents[1])  # корень репо
_PY = __import__("sys").executable   # тот же интерпретатор, что у дашборда; в образе нет /opt/homebrew (docker-install, этап 4)


def _tenant_dir(tenant: str) -> Path:
    """Каталог тенанта. Санитизация: только [a-z0-9_], иначе 400 (анти-traversal)."""
    tenant = (tenant or "").strip()
    if tenant and not re.fullmatch(r"[A-Za-z0-9_]+", tenant):
        raise HTTPException(400, i18n.t("person.lab.sheet.bad_tenant"))
    current = Path(health_db.DB_PATH).parent.parent
    if tenant in ("", "self", current.name):
        return current
    if not is_owner():
        raise HTTPException(403, i18n.t("person.lab.sheet.foreign_tenant"))
    if tenant == "health":
        return current
    return Path.home() / tenant


def _tenant_db(tenant: str) -> Path:
    return _tenant_dir(tenant) / "data" / "health.db"


def _ro(tenant: str) -> sqlite3.Connection:
    p = _tenant_db(tenant)
    if not p.exists():
        raise HTTPException(404, i18n.t("person.lab.sheet.no_database"))
    c = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


def _esc(x) -> str:
    from html import escape
    return escape("" if x is None else str(x))


def _name_cell(printed, canonical) -> str:
    """Имя строки для сверки (нить file-outcome, 06.10): человек сверяет с БЛАНКОМ, поэтому крупно —
    как напечатано в бланке, мелко рядом — международное имя, под которым значение ляжет в базу.
    До 06.10 при найденном международном имени напечатанное пряталось: русский бланк сверяли с «HGB»."""
    if not canonical:
        return f'<i>{_esc(printed)}</i> ⚠'
    if not printed or str(printed).strip().casefold() == str(canonical).strip().casefold():
        return _esc(canonical)
    return f'{_esc(printed)}<br><small style="color:#666">{_esc(canonical)}</small>'


def _num(x) -> str:
    try:
        return f"{float(x):g}"
    except (TypeError, ValueError):
        return str(x)


def _ref_text(low, high) -> str:
    """Норма так, как её печатает бланк: «a – b», «< b», «> a». До 06.10 одна граница рисовалась
    как «–0.2», и норма «< 0.2» выглядела распознанной наполовину."""
    if low is not None and high is not None:
        return f"{_num(low)} – {_num(high)}"
    if high is not None:
        return f"< {_num(high)}"
    if low is not None:
        return f"> {_num(low)}"
    return ""


@router.get("/lab-review/{run_id}", response_class=HTMLResponse)
def lab_review(run_id: str, tenant: str = "", show: str = "waiting"):
    c = _ro(tenant)
    try:
        # auto — согласие моделей; все незавершённые строки ждут человека.
        where = "run_id=?"
        if show != "all":
            where += " AND " + ("review_status='pending'" if show == "pending" else WAITING_REVIEW_SQL)
        rows = c.execute(
            "SELECT source_file, page, raw_line, canonical_name, value, unit, "
            "ref_low, ref_high, value_agreement, oracle_status, review_status "
            f"FROM lab_results_staging WHERE {where} "
            "ORDER BY (value_agreement!='disagree'), (canonical_name IS NOT NULL), source_file, page",
            (run_id,)).fetchall()
    finally:
        c.close()
    if not rows:
        return HTMLResponse(f"<h3>{_esc(i18n.t('person.lab.sheet.empty'))}</h3>")

    trs = []
    for i, r in enumerate(rows):
        flag = {"disagree": "🔴", "single": "🔵"}.get(r["value_agreement"], "")
        canon = _name_cell(r["raw_line"], r["canonical_name"])
        val = "" if r["value"] is None else _esc(r["value"])
        ref = _esc(_ref_text(r["ref_low"], r["ref_high"]))
        vval = "" if r["value"] is None else r["value"]
        payload = f'{r["source_file"]}|||{r["canonical_name"] or r["raw_line"]}|||{vval}'
        if r["canonical_name"] is None:
            akey = f'{r["source_file"]}|||{r["raw_line"]}|||{vval}'
            assign_cell = (f'<input type="text" name="assign_{i}" placeholder="{_esc(i18n.t("person.lab.sheet.name_hint"))}" size="12">'
                           f'<input type="hidden" name="assign_key_{i}" value="{_esc(akey)}">')
        else:
            assign_cell = ""
        trs.append(
            f'<tr><td><input type="checkbox" name="reject_{i}" value="{_esc(payload)}"></td>'
            f'<td>{flag} {canon}</td><td>{val}</td><td>{_esc(r["unit"])}</td>'
            f'<td>{ref}</td><td>{_esc(r["oracle_status"])}</td>'
            f'<td>{_esc(r["source_file"])} p{_esc(r["page"])}</td><td>{assign_cell}</td></tr>'
        )
    html = f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(i18n.t('person.lab.sheet.title'))} — {_esc(run_id)}</title>
<style>body{{font-family:-apple-system,sans-serif;margin:12px;font-size:15px}}
table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #ccc;padding:6px;text-align:left}}
th{{background:#f2f2f2}}button{{padding:10px 16px;margin:8px 4px;font-size:16px}}
.warn{{color:#b30000}}</style></head><body>
<h3>{_esc(i18n.t('person.lab.sheet.heading', n=len(rows)))}</h3>
<p>{_esc(i18n.t('person.lab.sheet.instructions'))}<br>
<a href="?show={'waiting' if show=='all' else 'all'}&tenant={_esc(tenant)}">{_esc(i18n.t('person.lab.sheet.waiting' if show=='all' else 'person.lab.sheet.all'))}</a></p>
<form method="post" action="/lab-review/{_esc(run_id)}/promote">
<input type="hidden" name="tenant" value="{_esc(tenant)}">
<table><tr>{''.join(f'<th>{_esc(i18n.t("person.lab.sheet.col." + col))}</th>' for col in ('reject', 'name', 'value', 'unit', 'ref', 'check', 'source', 'assign'))}</tr>
{''.join(trs)}</table>
<button type="submit" name="execute" value="0">{_esc(i18n.t('person.lab.sheet.check'))}</button>
<button type="submit" name="execute" value="1" onclick="return confirm({ _esc(json.dumps(i18n.t('person.lab.sheet.confirm'))) })">{_esc(i18n.t('person.lab.sheet.add'))}</button>
</form></body></html>"""
    return HTMLResponse(html)


@router.post("/lab-review/{run_id}/promote", response_class=HTMLResponse)
async def lab_review_promote(run_id: str, request: Request):
    # run_id уходит отдельным аргументом lab_promote (без оболочки), но с «-» в начале он стал бы
    # флагом (--execute). Форма прогона — буквы, цифры и . _ : + - (CodeQL #35, 2026-10-01).
    if not re.fullmatch(r"[A-Za-z0-9][\w.:+-]{0,127}", run_id):
        raise HTTPException(400, i18n.t("person.lab.sheet.bad_run"))
    form = await request.form()
    tenant = form.get("tenant", "")
    _tenant_dir(tenant)  # валидация тенанта (бросит 400 на мусор)
    execute = form.get("execute") == "1"

    rejects = []
    for k, v in form.multi_items():
        if k.startswith("reject_") and v:
            try:
                sf, name, val = v.split("|||")
            except ValueError:
                continue
            try:
                fval = float(val)
            except (TypeError, ValueError):
                fval = None
            rejects.append({"source_file": sf, "canonical": name, "value": fval})

    # назначить канон неизвестным (⚠) → ОБЩИЙ словарь + обновить staging (до промоута)
    assignments = []
    for k, v in form.multi_items():
        if k.startswith("assign_") and not k.startswith("assign_key_") and v.strip():
            idx = k[len("assign_"):]
            try:
                sf, raw, val = form.get(f"assign_key_{idx}", "").split("|||")
            except ValueError:
                continue
            assignments.append([run_id, sf, raw, val, v.strip()])
    assign_out = ""
    if assignments:
        afd, apath = tempfile.mkstemp(prefix="assign_", suffix=".json")
        with os.fdopen(afd, "w") as f:
            json.dump(assignments, f, ensure_ascii=False)
        try:
            ar = subprocess.run(
                [_PY, "-c", "import json,sys,lab_backfill as lb;"
                 "[lb.apply_assignment(*a) for a in json.load(open(sys.argv[1]))]", apath],
                cwd=_HEALTH_SCRIPTS,
                env={**os.environ, "HEALTH_DATA_DIR": str(_tenant_dir(tenant))},
                capture_output=True, text=True, timeout=120)
            assign_out = i18n.t("person.lab.sheet.assigned", n=len(assignments), code=ar.returncode)
            if ar.stderr:
                assign_out += " " + ar.stderr[:200]
        finally:
            try:
                os.unlink(apath)
            except OSError:
                pass  # silent-ok: temp-файл не удалился — не критично

    cmd = [_PY, "lab_promote.py", "--run-id", run_id]
    rej_path = None
    if rejects:
        fd, rej_path = tempfile.mkstemp(prefix="rej_", suffix=".json")
        with os.fdopen(fd, "w") as f:
            json.dump(rejects, f, ensure_ascii=False)
        cmd += ["--reject-file", rej_path]
    if execute:
        cmd += ["--execute"]

    env = {**os.environ, "HEALTH_DATA_DIR": str(_tenant_dir(tenant))}
    try:
        r = subprocess.run(cmd, cwd=_HEALTH_SCRIPTS, env=env,
                           capture_output=True, text=True, timeout=300)
        out = (r.stdout or "") + ("\n[stderr]\n" + r.stderr if r.stderr else "")
        code = r.returncode
    except Exception as e:
        # Текст исключения (пути, окружение) — в журнал, наружу — факт (CodeQL #3).
        __import__("logging").getLogger(__name__).error("lab_promote не запустился: %r", e)
        out, code = i18n.t("person.lab.sheet.start_failed"), -1
    finally:
        if rej_path:
            try:
                os.unlink(rej_path)
            except OSError:
                pass  # silent-ok: temp-файл не удалился — не критично

    mode = i18n.t("person.lab.sheet.adding" if execute else "person.lab.sheet.checking")
    back = f'/lab-review/{_esc(run_id)}?tenant={_esc(tenant)}'
    return HTMLResponse(
        f'<!doctype html><meta charset="utf-8"><body style="font-family:-apple-system,sans-serif;margin:12px">'
        f'<h3>{_esc(mode)}</h3>'
        f'<pre style="white-space:pre-wrap;background:#f6f6f6;padding:10px;border:1px solid #ccc">{_esc(out)}</pre>'
        f'<a href="{back}">{_esc(i18n.t("person.lab.sheet.back"))}</a></body>'
    )
