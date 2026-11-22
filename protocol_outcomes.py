"""protocol_outcomes.py — что стало с протоколом: выполнялся ли он и сбылся ли его прогноз.

# INTENT: protocol_outcomes — замысел и инварианты в subsystem_intent.yaml.

Два вопроса, один дом (нить assistant-loop, решение владельца 10.11):
- ВЫПОЛНЕНИЕ — пересказ ассистента хозяина строкой письма. Это не замер прибора:
  хранится с source='assistant_report' и с данными приборов не сливается.
- ПРОГНОЗ — изменение показателя, интервал и горизонт, названные моделью при рождении
  протокола. Базу считает код, оценку ставит код, модель оценку не пишет. Годятся только
  значения, взятые ПОСЛЕ рождения протокола (без ретродикции); оценка сравнивается с
  прогнозом «ничего не изменится»; промахи всегда входят в калибровку.

Приём оценки заимствован из Insilico-org/plsi (policy/score.py, MIT) — идея, не код.
Это не ретайренные эксперименты N-of-1 (BL-EXP-1): протокол рождается из гипотезы по
данным, отчёта атрибуции нет, вопрос только «выполнялся ли» и «сбылось ли».
"""
from __future__ import annotations

import re
from datetime import date
from statistics import fmean

import config_db
import health_db
from _time_inject import get_now, get_today
from prediction_core import (  # noqa: F401 — ре-экспорт: читатели и тесты знают эти имена здесь
    PredictionRejected, cfg, daily_metrics, judge_prediction, metric_scales, observed, predictable_metrics,
    accept_prediction)

SOURCE = "assistant_report"
ADHERENCE_HINT = "протокол #P: N из M с ДД.ММ по ДД.ММ"
_ADHERENCE = re.compile(
    r"протокол\s*#\s*(\d+)\s*:\s*(\d+)\s*из\s*(\d+)\s*с\s*(\d{1,2})\.(\d{1,2})(?:\.\d{2,4})?"
    r"\s*по\s*(\d{1,2})\.(\d{1,2})(?:\.\d{2,4})?", re.IGNORECASE)


# ── Выполнение: строка ассистента ─────────────────────────────────────────

def _day(d: int, m: int, before: date) -> date:
    """День без года — ближайший не позже `before` (период уже прошёл)."""
    candidate = date(before.year, m, d)
    return candidate if candidate <= before else date(before.year - 1, m, d)


def parse_adherence(text: str, today: date) -> list[dict]:
    """Строки «протокол #P: N из M с ДД.ММ по ДД.ММ». Кривая строка пропускается, а не чинится."""
    out = []
    for pid, done, planned, d1, m1, d2, m2 in _ADHERENCE.findall(text or ""):
        try:
            end = _day(int(d2), int(m2), today)
            start = _day(int(d1), int(m1), end)
        except ValueError:
            continue
        done, planned = int(done), int(planned)
        if planned < 1 or done > planned:
            continue
        out.append(dict(protocol_id=int(pid), done=done, planned=planned,
                        period_from=str(start), period_to=str(end)))
    return out


def record_from_letter(letter: dict, today: date | None = None) -> list[dict]:
    """Только ассистент хозяина и только счётчики: статус протокола строкой не меняется."""
    import agent_reply
    if letter.get("peer") not in agent_reply.allowed_peers():
        return []
    rows = parse_adherence(letter.get("wrapped") or "", today or get_today())
    saved = []
    with health_db.get_conn() as conn:
        for r in rows:
            proto = conn.execute("SELECT created_at FROM protocols WHERE id=?", (r["protocol_id"],)).fetchone()
            if not proto or str(proto["created_at"] or "")[:10] > r["period_to"]:
                continue
            cur = conn.execute(
                "INSERT OR IGNORE INTO protocol_adherence "
                "(protocol_id, period_from, period_to, done, planned, source, letter_key) "
                "VALUES (?,?,?,?,?,?,?)",
                (r["protocol_id"], r["period_from"], r["period_to"], r["done"], r["planned"],
                 SOURCE, letter.get("key")))
            if cur.rowcount:
                saved.append(r)
    return saved


def adherence(protocol_id: int, start: str | None = None, end: str | None = None) -> dict:
    """Сумма пересказов за окно (периоды, пересекающие окно). Пусто — reports=0, а не «не выполнялся»."""
    with health_db.get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n, SUM(done) AS done, SUM(planned) AS planned, MAX(period_to) AS last "
            "FROM protocol_adherence WHERE protocol_id=? AND period_to>=? AND period_from<=?",
            (protocol_id, start or "0000-01-01", end or "9999-12-31")).fetchone()
    return dict(reports=row["n"], done=row["done"] or 0, planned=row["planned"] or 0, last=row["last"],
                source=SOURCE)


# ── Прогноз: принять у модели, базу посчитать самим ───────────────────────

def attach_prediction(protocol_id: int, raw, created: date | None = None) -> dict:
    """Прогноз протокола: изменение показателя, интервал изменения, горизонт в днях.
    Проверки и база — в prediction_core (общий суд протоколов и гипотез)."""
    created = created or get_today()
    with health_db.get_conn() as conn:
        row = dict(protocol_id=protocol_id, **accept_prediction(conn, raw, created))
        conn.execute(
            "INSERT INTO protocol_predictions (protocol_id, metric, kind, baseline, baseline_from, "
            "baseline_to, unit, change, low, high, horizon, created) VALUES (:protocol_id, :metric, :kind, "
            ":baseline, :baseline_from, :baseline_to, :unit, :change, :low, :high, :horizon, :created)", row)
    return row


# ── Оценка: только код, только значения после рождения протокола ──────────

def score_due(today: date | None = None) -> list[dict]:
    """Открытые прогнозы с открывшимся окном → scored / missed / void. Возвращает изменённые."""
    today = today or get_today()
    changed = []
    with health_db.get_conn() as conn:
        open_rows = [dict(r) for r in conn.execute(
            "SELECT pp.*, pr.status AS protocol_status, pr.retired_at FROM protocol_predictions pp "
            "JOIN protocols pr ON pr.id=pp.protocol_id WHERE pp.status='open'")]
        for p in open_rows:
            update: dict = {}
            if p["protocol_status"] != "active" and str(p["retired_at"] or "")[:10] < p["horizon"]:
                update = dict(status="void")
            else:
                value, n, closed = observed(conn, p, today)
                if value is not None:
                    update = dict(status="scored", n_points=n, **judge_prediction(p, value))
                elif closed:
                    update = dict(status="missed", n_points=n)
            if not update:
                continue
            update["scored_at"] = get_now().isoformat(timespec="seconds")
            sets = ", ".join(f"{k}=:{k}" for k in update)
            conn.execute(f"UPDATE protocol_predictions SET {sets} WHERE id=:id AND status='open'",
                         {**update, "id": p["id"]})
            changed.append({**p, **update})
    config_db.upsert_config("protocols.scored_last", value_text=str(today),
                            category="protocols", source="protocol_outcomes")
    return changed


def scoring_stale(last: str | None, today: date, open_count: int, max_age: int) -> bool:
    """Чистая часть датчика: есть что оценивать, а оценщик не ходил дольше max_age дней."""
    if not open_count:
        return False
    return last is None or (today - date.fromisoformat(last)).days > max_age


def scoring_liveness(today: date | None = None) -> tuple[bool, int, str | None]:
    """(просрочен ли оценщик, открытых прогнозов, последний проход) — для ночного датчика."""
    today = today or get_today()
    with health_db.get_conn() as conn:
        # База ещё без миграции (снимок, монитор раньше перезапуска бота): прогнозов нет — нечего стеречь.
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                            "AND name='protocol_predictions'").fetchone():
            return False, 0, None
        n, oldest = conn.execute(
            "SELECT COUNT(*), MIN(created) FROM protocol_predictions WHERE status='open'").fetchone()
    # До первого прохода отсчёт идёт от самого старого открытого прогноза, иначе датчик краснел бы
    # в первый же вечер после выкатки.
    last = config_db.get_config("protocols.scored_last") or oldest
    return scoring_stale(last, today, n, cfg("protocols.scoring_max_age_days")), n, last


# ── Калибровка и то, что видят читатели ───────────────────────────────────

def _rate(values: list) -> float | None:
    known = [v for v in values if v is not None]
    return round(fmean(known), 2) if known else None


def calibration() -> dict:
    """По всем оценённым и пропущенным прогнозам; void не входит. Промахи не прячутся."""
    with health_db.get_conn() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT status, within, beat_persistence, direction_correct FROM protocol_predictions "
            "WHERE status IN ('scored','missed')")]
    scored = [r for r in rows if r["status"] == "scored"]
    return dict(n_scored=len(scored), n_missed=len(rows) - len(scored),
                within_rate=_rate([r["within"] for r in scored]),
                beat_persistence_rate=_rate([r["beat_persistence"] for r in scored]),
                direction_rate=_rate([r["direction_correct"] for r in scored]))


def for_protocol(protocol_id: int) -> dict:
    """Карточка протокола: прогноз (если есть) и выполнение со слов ассистента."""
    with health_db.get_conn() as conn:
        pred = conn.execute("SELECT * FROM protocol_predictions WHERE protocol_id=?", (protocol_id,)).fetchone()
    pred = dict(pred) if pred else None
    window = (pred["created"], pred["horizon"]) if pred else (None, None)
    return dict(prediction=pred, adherence=adherence(protocol_id, *window))


def hypothesis_lines(memory_id: int) -> list[str]:
    """Блок для пакета оценки гипотезы консилиумом: что стало с её протоколами."""
    with health_db.get_conn() as conn:
        ids = [r[0] for r in conn.execute(
            "SELECT id FROM protocols WHERE linked_hypothesis_id=? ORDER BY id", (memory_id,))]
    lines = []
    for pid in ids:
        card = for_protocol(pid)
        a, p = card["adherence"], card["prediction"]
        done = (f"выполнение со слов ассистента: {a['done']} из {a['planned']} ({a['reports']} отчётов)"
                if a["reports"] else "о выполнении данных нет (это не значит «не выполнялся»)")
        if not p:
            lines.append(f"  протокол #{pid}: прогноза нет; {done}")
            continue
        verdict = {"open": "окно ещё не закрыто", "void": "протокол снят до горизонта",
                   "missed": "в окне нет измерений"}.get(p["status"])
        if p["status"] == "scored":
            verdict = (f"наблюдалось {p['observed']:.4g} (база {p['baseline']:.4g}); "
                       f"в интервал {'да' if p['within'] else 'нет'}, "
                       f"лучше «без изменений» {'да' if p['beat_persistence'] else 'нет'}")
        lines.append(f"  протокол #{pid}: прогноз {p['metric']} {p['change']:+.4g} "
                     f"[{p['low']:+.4g}; {p['high']:+.4g}] к {p['horizon']} — {verdict}; {done}")
    return lines
