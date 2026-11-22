"""hypothesis_verdict.py — прогноз гипотезы в числах и вердикт, который выносит код.

# INTENT: hypothesis_verdict — замысел и инварианты в subsystem_intent.yaml.

Заимствование из Insilico-org/plsi (research/verdict.py, refusals.py; идея, не код), Э1–Э2 плана
claude/plsi_borrow_rest_plan_2026-11-11.md. Замер 11.11: из 11 вердиктов консилиума 10 — «частично»
с одной и той же уверенностью 0,7; у 20 из 24 действующих гипотез прогноз уже содержит числа прозой.

- Структуратор (модель) видит ТОЛЬКО текст прогноза и имена показателей с их уровнем на дату
  прогноза — данных после этой даты не видит. Иначе прогноз подгоняется под известный исход.
- Дата прогноза — дата его версии: консилиум переписывает прогноз при пересмотре, и переписанный
  текст отсчитывается от даты пересмотра (history[-1].date), а не от рождения гипотезы.
- Суд — prediction_core (тот же, что у протоколов): база до даты, наблюдение строго после.
- Вердикт — правило кода над оценёнными прогнозами; модель вердикт не пишет.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date

import health_db
import prediction_core as core
from _time_inject import get_now, get_today

log = logging.getLogger(__name__)
_MAX_TOKENS = 1200
UNSTABLE = "два прочтения текста дали разные прогнозы"   # пометка разбора: текст двусмыслен, код не судит


def prediction_date(payload: dict) -> date:
    """Дата текущей версии прогноза: последний пересмотр консилиумом, иначе рождение гипотезы."""
    history = payload.get("history") or []
    stamp = (history[-1].get("date") if history else None) or payload.get("created_date")
    return date.fromisoformat(str(stamp)[:10])


def _lab_names(conn, before: date) -> list[str]:
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='lab_results'").fetchone():
        return []
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT test_name FROM lab_results WHERE date<? AND value IS NOT NULL ORDER BY test_name",
        (str(before),))]


def forecast_forms(scales: dict, labs: list[str]) -> str:
    """Формы прогноза и допустимые показатели — один текст для переводчика прозы и для консилиума,
    который пишет прогноз при рождении гипотезы (monthly_consilium).
    Формат поля forecast и правила суда — docs/reference/hypothesis_forecast_format.md."""
    daily = [m if sd is None else f"{m} (уровень {b:.3g}, обычный разброс ±{sd:.2g})"
             for m, (b, sd) in scales.items()]
    return (
        "Каждый прогноз — объект с \"metric\" (имя из списков ниже, другое не годится) и "
        "\"horizon_days\" (целое число дней из текста: «за 5–6 недель» — 42, «14–21 день» — 21, у диапазона "
        "бери верхнюю границу; 30 — только когда срока в тексте нет) и РОВНО одной из форм:\n"
        "  • изменение в единицах показателя («снизится на 10–15»): \"change\", \"low\", \"high\";\n"
        "  • изменение в процентах («снизится на 5–10%»): \"change_pct\", \"low_pct\", \"high_pct\";\n"
        "  • целевой диапазон («до 44–45»): \"target_low\", \"target_high\";\n"
        "  • порог («не меньше 30», «≥22», «удержится выше», «не выше»): \"threshold\" и "
        "\"bound\": \"at_least\" или \"at_most\".\n"
        "Снижение — отрицательные числа. Разницу с нынешним уровнем не считай: её посчитает система.\n"
        f"Дневные показатели: {', '.join(daily) or 'нет'}.\n"
        f"Анализы (точные имена): {', '.join(labs) or 'нет'}.\n"
    )


def forecast_forms_now() -> str:
    """Тот же текст на сегодня — для генератора гипотез."""
    today = get_today()
    with health_db.get_conn() as conn:
        labs = _lab_names(conn, today)
    return forecast_forms(core.metric_scales(today), labs)


def _prompt(prediction: str, scales: dict, labs: list[str]) -> str:
    return (
        "Ниже — текст из поля «прогноз» медицинской гипотезы. Найди в нём ПРОГНОЗЫ — утверждения о том, "
        "каким станет измеримый показатель к сроку, — и переведи в числа, ничего не добавляя от себя.\n"
        f"Текст: «{prediction}»\n\n"
        "Прогнозом НЕ является: план обследования («пересдать», «опросник», «осмотр»); условие для решения "
        "(«при X — делать Y», «если A ниже B — перейти к…», критерии диагноза); назначение или действие; "
        "показатель в условии или в средстве («сохранится, если ВСР ≥18», «повышение ВСР до ≥30 снизит…») — "
        "прогноз только то, что обещано как РЕЗУЛЬТАТ. "
        "Такое пропускай. Если прогнозов нет — пустой список.\n"
        + forecast_forms(scales, labs) +
        "Ответь одним JSON-объектом {\"predictions\": [ ... ]} — без пояснений, черновиков и оформления."
    )


def _parse(raw: str) -> list[dict] | None:
    """Последний JSON-объект с ключом predictions: модель пишет черновик, пояснение и исправленный
    итог (замер 11.11: 2 из 24 живых ответов) — жадный поиск от первой скобки до последней их склеивал."""
    dec, found = json.JSONDecoder(), None
    for m in re.finditer(r"\{", raw or ""):
        try:
            obj, _ = dec.raw_decode(raw, m.start())
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and isinstance(obj.get("predictions"), list):
            found = obj["predictions"]
    return found


def _accepted(conn, items: list, created: date) -> list[tuple]:
    """Что код принял бы из прочтения — по одному прогнозу на показатель, как в хранилище."""
    out = {}
    for item in items:
        try:
            f = core.accept_prediction(conn, dict(item), created, thresholds=True)
        except (core.PredictionRejected, TypeError, ValueError):
            continue
        out.setdefault(f["metric"], (f["metric"], f["form"], f["bound"], round(f["low"], 4), round(f["high"], 4), f["horizon"]))
    return sorted(out.values())


def _run(conn, memory_id: int, version: int, outcome: str, note: str | None) -> None:
    conn.execute("INSERT OR REPLACE INTO hypothesis_prediction_runs (memory_id, version, run_at, outcome, note) "
                 "VALUES (?,?,?,?,?)", (memory_id, version, get_now().isoformat(timespec="seconds"),
                                        outcome, (note or "")[:500] or None))


def structure(memory_id: int, client=None) -> dict:
    """Перевести прогноз текущей версии гипотезы в числа и сохранить. Повтор той же версии — без вызова
    модели. Возвращает {'outcome', 'saved', 'rejected'}."""
    import hai_core
    import llm_client
    with health_db.get_conn() as conn:
        row = conn.execute("SELECT value FROM memory WHERE id=?", (memory_id,)).fetchone()
        if not row:
            raise ValueError(f"гипотеза {memory_id} не найдена")
        payload = json.loads(row["value"])
        version = int(payload.get("version") or 1)
        done = conn.execute("SELECT outcome FROM hypothesis_prediction_runs WHERE memory_id=? AND version=?",
                            (memory_id, version)).fetchone()
        if done:
            return dict(outcome=done["outcome"], saved=[], rejected=[])
        created = prediction_date(payload)
        labs = _lab_names(conn, created)
    given = payload.get("forecast")
    if isinstance(given, list):     # прогноз назван при рождении гипотезы — переводить прозу не нужно
        items = [i for i in given if isinstance(i, dict)]
    else:
        # Переводчик прозы: два независимых прочтения. Замер 12.11 (10 гипотез × 4 прогона): младшая модель
        # расходилась сама с собой на 2 из 10, эта — на 1 (лечится правилом процентов в ядре).
        prompt = _prompt(str(payload.get("prediction") or ""), core.metric_scales(created), labs)
        client = client or hai_core.get_client()
        reads = [_parse(llm_client.answer_text(client.messages.create(
            task="hypothesis_verdict.structure", model=hai_core.get_model("sonnet"), max_tokens=_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}]))) for _ in range(2)]
        items = reads[0]
        with health_db.get_conn() as conn:
            if None not in reads and _accepted(conn, reads[0], created) != _accepted(conn, reads[1], created):
                _run(conn, memory_id, version, "unparsed", UNSTABLE)
                return dict(outcome="unparsed", saved=[], rejected=[])
        if None in reads:
            items = None
    saved, rejected = [], []
    with health_db.get_conn() as conn:
        if items is None:
            _run(conn, memory_id, version, "unparsed", "ответ модели не JSON")
            return dict(outcome="unparsed", saved=[], rejected=[])
        for item in items:
            try:
                fields = core.accept_prediction(conn, dict(item), created, thresholds=True)
            except core.PredictionRejected as e:
                rejected.append(f"{item.get('metric')}: {e}")
                continue
            conn.execute(
                "INSERT OR IGNORE INTO hypothesis_predictions (memory_id, version, metric, kind, baseline, "
                "baseline_from, baseline_to, unit, change, low, high, horizon, created, form, bound, threshold) "
                "VALUES (:memory_id, :version, :metric, :kind, :baseline, :baseline_from, :baseline_to, :unit, "
                ":change, :low, :high, :horizon, :created, :form, :bound, :threshold)", dict(fields, memory_id=memory_id, version=version))
            saved.append(fields)
        outcome = "numeric" if saved else ("rejected" if rejected else "not_numeric")
        _run(conn, memory_id, version, outcome, "; ".join(rejected) or (
            str(payload.get("no_forecast_reason") or "") if isinstance(given, list) else "") or None)
    return dict(outcome=outcome, saved=saved, rejected=rejected)


def score_hypothesis_predictions(today: date | None = None) -> list[dict]:
    """Открытые прогнозы гипотез → scored / missed / void тем же судом, что протоколы. Прогноз
    устаревшей версии (консилиум переписал текст) — void: судится только действующая версия."""
    today = today or get_today()
    changed = []
    with health_db.get_conn() as conn:
        for p in [dict(r) for r in conn.execute("SELECT * FROM hypothesis_predictions WHERE status='open'")]:
            row = conn.execute("SELECT value, active FROM memory WHERE id=?", (p["memory_id"],)).fetchone()
            current = int(json.loads(row["value"]).get("version") or 1) if row else None
            if not row or not row["active"] or current != p["version"]:
                update = dict(status="void")
            else:
                value, n, closed = core.observed(conn, p, today)
                if value is not None:
                    update = dict(status="scored", n_points=n, **core.judge_prediction(p, value))
                elif closed:
                    update = dict(status="missed", n_points=n)
                else:
                    continue
            update["scored_at"] = get_now().isoformat(timespec="seconds")
            sets = ", ".join(f"{k}=:{k}" for k in update)
            conn.execute(f"UPDATE hypothesis_predictions SET {sets} WHERE id=:id AND status='open'",
                         {**update, "id": p["id"]})
            changed.append({**p, **update})
    return changed


def code_verdict(memory_id: int) -> dict:
    """Правило кода над прогнозами действующей версии:
    refuted — хоть один оценённый прогноз ушёл в противоположную сторону и вне интервала;
    confirmed — хоть один попал в интервал и обыграл «ничего не изменится», и ни один не опровергнут;
    undecided — числовые прогнозы есть, но решающего исхода нет (срок не наступил, промах по данным,
    попадание без победы над инерцией); not_numeric — у версии нет числового прогноза."""
    with health_db.get_conn() as conn:
        row = conn.execute("SELECT value FROM memory WHERE id=?", (memory_id,)).fetchone()
        if not row:
            return dict(status="not_numeric", predictions=[])
        version = int(json.loads(row["value"]).get("version") or 1)
        preds = [dict(r) for r in conn.execute(
            "SELECT * FROM hypothesis_predictions WHERE memory_id=? AND version=? ORDER BY id",
            (memory_id, version))]
    if not preds:
        return dict(status="not_numeric", predictions=[])
    scored = [p for p in preds if p["status"] == "scored"]
    # «Ничего не изменилось» — не опровержение: прогнозы гипотез обычно условные (prediction_core.refutes).
    if any(core.refutes(p) for p in scored):
        status = "refuted"
    elif any(p["within"] and p["beat_persistence"] for p in scored):
        status = "confirmed"
    else:
        status = "undecided"
    return dict(status=status, predictions=preds)


def verdict_lines(memory_id: int) -> list[str]:
    """Для пакета консилиума и карточки: вердикт кода и каждый прогноз версии с исходом суда."""
    import i18n
    v = code_verdict(memory_id)
    lines = [i18n.t("hypothesis_verdict.line.status", status=i18n.t(f"hypothesis_verdict.status.{v['status']}"))]
    for p in v["predictions"]:
        if p["status"] == "scored":
            outcome = i18n.t("hypothesis_verdict.line.scored", observed=f"{p['observed']:.4g}",
                             within=i18n.t("hypothesis_verdict.yes" if p["within"] else "hypothesis_verdict.no"),
                             beat=i18n.t("hypothesis_verdict.yes" if p["beat_persistence"] else "hypothesis_verdict.no"))
        else:
            outcome = i18n.t(f"hypothesis_verdict.line.{p['status']}")
        if p.get("form") == "threshold":
            lines.append(i18n.t(f"hypothesis_verdict.line.threshold_{p['bound']}", metric=p["metric"],
                                baseline=f"{p['baseline']:.4g}", threshold=f"{p['threshold']:.4g}",
                                horizon=p["horizon"], outcome=outcome))
            continue
        lines.append(i18n.t("hypothesis_verdict.line.prediction", metric=p["metric"], baseline=f"{p['baseline']:.4g}",
                            low=f"{p['low']:+.4g}", high=f"{p['high']:+.4g}", horizon=p["horizon"],
                            outcome=outcome))
    return lines


def structure_pending(client=None) -> list[dict]:
    """Разобрать действующие версии гипотез, у которых ещё нет записи разбора (новые и переписанные
    консилиумом). Не больше hypotheses.structure_per_pass за проход (предел платных вызовов)."""
    limit = core.cfg("hypotheses.structure_per_pass")
    with health_db.get_conn() as conn:
        todo = []
        for mid, value in conn.execute("SELECT id, value FROM memory WHERE key LIKE 'hyp_%' AND active=1 ORDER BY id"):
            version = int(json.loads(value).get("version") or 1)
            if not conn.execute("SELECT 1 FROM hypothesis_prediction_runs WHERE memory_id=? AND version=?",
                                (mid, version)).fetchone():
                todo.append(mid)
    out = []
    for mid in todo[:limit]:
        try:
            out.append(dict(memory_id=mid, **structure(mid, client=client)))
        except Exception as e:  # silent-ok: громко в notify.fault, остальные гипотезы разбираются дальше
            import notify
            notify.fault(f"hypothesis_verdict: structure #{mid} failed ({type(e).__name__})", person_key=None)
    return out
