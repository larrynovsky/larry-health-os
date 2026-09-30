#!/usr/bin/env python3.11
"""
memory_truthcheck.py — A1, писатель подтверждений (Фаза 2 хвост / прог-2).

Сверяет state-утверждения памяти с объективным каноном (daily_metrics / lab_results):
  • confirm     — значение совпало → кандидат бампнуть confirmations (устойчивое → promote)
  • disagree    — канон есть, значение врёт → кандидат в супёрсид (человек-гейт, как #126)
  • unverifiable — нет проверяемой метрики/даты → не трогаем

Дисциплина shadow-first (как R3): run_shadow() ТОЛЬКО считает и возвращает разбор,
НИЧЕГО не пишет. Применение (бамп confirmations / супёрсид) — отдельный шаг с человек-гейтом.

Парсер валидирован 2026-07-06 (98% на сырых метриках; на нём же пойман ложный #126).
Публичный вход: run_shadow(). Порог промоута и запись — во втором инкременте.
"""
from __future__ import annotations
import re
from datetime import date, timedelta

import health_db as db

_MONTHS = {
    'января':1,'февраля':2,'марта':3,'апреля':4,'мая':5,'июня':6,'июля':7,
    'августа':8,'сентября':9,'октября':10,'ноября':11,'декабря':12,
    'january':1,'february':2,'march':3,'april':4,'may':5,'june':6,'july':7,
    'august':8,'september':9,'october':10,'november':11,'december':12,
}
_TOL = {'score':2, 'deep_min':6, 'hrv':2, 'readiness':3, 'total_h':0.4}
# «N часов» = сон ТОЛЬКО при контексте сна в тексте. Иначе «sunset within 1 hour»,
# «через 2 часа приём» ложно читались как «1ч/2ч сна» и сверялись с Oura (баг #631).
_SLEEP_CTX = re.compile(r'спал|сон|высп|проспал|отоспал|в\s*постел|sleep|slept|in\s*bed|nap|дремал', re.I)


def _to_min(x):
    """daily_metrics: deep в часах, total в секундах — робастно в минуты."""
    if x is None:
        return None
    x = float(x)
    return x*60 if x < 24 else (x if x < 1440 else x/60)


def _extract_dates(text: str):
    out = []
    for m in re.finditer(r'(\d{4})-(\d{2})-(\d{2})', text):
        out.append((int(m.group(2)), int(m.group(3)), int(m.group(1))))
    for m in re.finditer(r'\b(\d{1,2})\s+([А-Яа-яA-Za-z]+)', text):
        mon = _MONTHS.get(m.group(2).lower())
        if mon:
            out.append((mon, int(m.group(1)), None))
    for m in re.finditer(r'\b([A-Za-z]+)\s+(\d{1,2})\b', text):
        mon = _MONTHS.get(m.group(1).lower())
        if mon:
            out.append((mon, int(m.group(2)), None))
    return out


def _extract_claims(text: str) -> dict:
    """Явные числовые утверждения. readiness ПЕРВЫМ (чтобы 'readiness score N' не утёк в score)."""
    c, t = {}, text.lower()
    m = re.search(r'readiness\D{0,10}(\d{1,3})', t)
    if m: c['readiness'] = int(m.group(1))
    m = re.search(r'sleep\s*score\D{0,4}(\d{1,3})', t)
    if m:
        c['score'] = int(m.group(1))
    else:
        for mm in re.finditer(r'(\w+\s+)?score\D{0,4}(\d{1,3})', t):
            if (mm.group(1) or '').strip() != 'readiness':
                c['score'] = int(mm.group(2)); break
    m = (re.search(r'deep\s*sleep\D{0,14}(\d{1,3})\s*(?:min|minute|минут|м\b|m\b)', t)
         or re.search(r'глубок\w*\s+сон\D{0,14}(\d{1,3})\s*минут', t)
         or re.search(r'deep\s*(\d{1,3})\s*(?:min|минут|м\b)', t))
    if m: c['deep_min'] = int(m.group(1))
    m = re.search(r'(?:hrv|вср|всс)\D{0,8}(\d{1,3})\s*(?:ms|мс)', t)
    if m: c['hrv'] = int(m.group(1))
    m = re.search(r'(?<!за )(\d(?:[.,]\d)?)\s*(?:h\b|ч\b|hour|часа|часов|hours)', t)
    if m and 'за ' not in t[max(0, m.start()-4):m.start()] and _SLEEP_CTX.search(t):
        c['total_h'] = float(m.group(1).replace(',', '.'))
    return c


def _canon_day(conn, y, m, d):
    try:
        dt = date(y, m, d)
    except ValueError:
        return None
    r = conn.execute("SELECT sleep_score, sleep_deep, sleep_total, hrv, readiness "
                     "FROM daily_metrics WHERE date=?", (dt.isoformat(),)).fetchone()
    if not r:
        return None
    return {'score':r[0], 'deep_min':_to_min(r[1]),
            'total_h':(_to_min(r[2]) or 0)/60 if r[2] else None,
            'hrv':r[3], 'readiness':r[4], 'date':dt.isoformat()}


def _verdict_row(conn, value: str):
    """Возвращает (verdict, detail) для одного state-value. verdict ∈ confirm/disagree/unverifiable."""
    claims, dates = _extract_claims(value), _extract_dates(value)
    if not claims or not dates:
        return 'unverifiable', None
    best = None  # (n_ok, n_checked, details, canon_date)
    for (mon, day, yr) in dates:
        for y in ([yr] if yr else [2026, 2025]):
            for off in (0, -1, 1):   # ночь ±1: oura лейблит по дате пробуждения
                dt = date(y, mon, day) + timedelta(days=off) if _valid(y, mon, day) else None
                if not dt:
                    continue
                canon = _canon_day(conn, dt.year, dt.month, dt.day)
                if not canon:
                    continue
                det = [(k, v, round(float(canon[k]), 1),
                        abs(float(v)-float(canon[k])) <= _TOL[k])
                       for k, v in claims.items() if canon.get(k) is not None]
                if not det:
                    continue
                nok = sum(1 for *_, ok in det if ok)
                if best is None or nok > best[0]:
                    best = (nok, len(det), det, canon['date'])
    if best is None:
        return 'unverifiable', None
    if best[0] == best[1]:
        return 'confirm', best[3]
    return 'disagree', best[2]


def _valid(y, m, d):
    try:
        date(y, m, d); return True
    except ValueError:
        return False


def run_shadow() -> dict:
    """SHADOW: сверяет весь active state с каноном, НИЧЕГО не пишет.
    Возвращает {confirm, disagree, unverifiable: [...], counts}. Точка потребления — ревью."""
    conn = db.get_conn()
    rows = conn.execute("SELECT id, value, confirmations FROM memory_facts "
                        "WHERE mem_class='state' AND active=1 ORDER BY id").fetchall()
    out = {'confirm': [], 'disagree': [], 'unverifiable': 0}
    for rid, val, conf in rows:
        if not val:
            continue
        verdict, detail = _verdict_row(conn, val)
        if verdict == 'unverifiable':
            out['unverifiable'] += 1
        elif verdict == 'confirm':
            out['confirm'].append((rid, conf or 0, detail, val[:60]))
        else:
            out['disagree'].append((rid, detail, val[:70]))
    out['counts'] = {'confirm': len(out['confirm']), 'disagree': len(out['disagree']),
                     'unverifiable': out['unverifiable'], 'total': len(rows)}
    return out


def apply_confirmations() -> dict:
    """A1 инкремент 2: подтверждённый каноном state получает confirmations=1 — ИДЕМПОТЕНТНО
    (только где было 0). Производитель заполняет confirmations по сверке с каноном.
    Меняется только счётчик подтверждения; value и класс остаются прежними.
    Промоут state→fact и disagree→супёрсид — человек-гейт, ОТДЕЛЬНО (не здесь). Возвращает
    {bumped, confirmed_total, disagree_ids}."""
    conn = db.get_conn()
    shadow = run_shadow()
    bumped = 0
    for rid, conf, _detail, _txt in shadow['confirm']:
        if not conf:  # confirmations == 0 → отметить объективно подтверждённым
            conn.execute("UPDATE memory_facts SET confirmations=1, updated_at=datetime('now') "
                         "WHERE id=? AND confirmations=0 AND active=1", (rid,))
            bumped += 1
    conn.commit()
    return {'bumped': bumped, 'confirmed_total': len(shadow['confirm']),
            'disagree_ids': [d[0] for d in shadow['disagree']]}


_HUMAN_METRIC = {"total_h": "общий сон, ч", "deep_min": "глубокий, мин", "rem_min": "REM, мин",
                 "hrv": "ВСР, мс", "readiness": "готовность", "resting_hr": "пульс покоя"}


def propose_oura_disagreements() -> dict:
    """A1-доставка (закрывает detection-without-delivery): расхождения state↔Oura из
    run_shadow() → SUPERSEDE-предложения в memory_consolidation_proposals. Едут по
    СУЩЕСТВУЮЩЕМУ Telegram-гейту (jobs.scheduled.run_nightly_consolidation →
    handlers.callbacks apply_supersede/reject). Идемпотентно: не дублирует pending и не
    воскрешает rejected (человек сказал «оставить»); applied-факт уже active=0 → run_shadow
    его не вернёт. Человек решает: снять (ошибка/галлюцинация) или оставить (субъективный
    отчёт vs прибор). Возвращает {proposed, skipped}."""
    import memory_consolidation as _mc
    _mc._ensure_proposals_table()
    shadow = run_shadow()
    proposed = skipped = 0
    conn = db.get_conn()
    for rid, det, txt in shadow['disagree']:
        exists = conn.execute(
            "SELECT 1 FROM memory_consolidation_proposals WHERE fact_id=? "
            "AND authority_source='Oura canon' AND status IN ('pending','rejected') LIMIT 1",
            (rid,)).fetchone()
        if exists:
            skipped += 1
            continue
        mism = "; ".join(f"{_HUMAN_METRIC.get(k, k)}: заявлено {v}, Oura {cv}"
                         for k, v, cv, ok in det if not ok)
        conn.execute(
            "INSERT INTO memory_consolidation_proposals "
            "(fact_id, current_value, action, authority_source, medical_flag, rationale, status) "
            "VALUES (?,?,?,?,?,?, 'pending')",
            (rid, txt, "SUPERSEDE", "Oura canon", 1,
             f"⚠️ Факт противоречит объективным данным Oura: {mism}. Снять как ошибочный?"))
        proposed += 1
    conn.commit()
    return {"proposed": proposed, "skipped": skipped}


if __name__ == "__main__":
    r = run_shadow()
    c = r['counts']
    print(f"=== A1 shadow: state {c['total']} | confirm {c['confirm']} / "
          f"disagree {c['disagree']} / unverifiable {c['unverifiable']} ===")
    print("\n-- DISAGREE (кандидаты в супёрсид, человек-гейт) --")
    for rid, det, txt in r['disagree']:
        diffs = ', '.join(f"{k}:{v}vs{cv}{'✗' if not ok else ''}" for k, v, cv, ok in det)
        print(f"  #{rid} [{txt}] {diffs}")
    print(f"\n-- CONFIRM: {c['confirm']} строк совпали с каноном (кандидаты бампнуть confirmations) --")
