"""
brief_state.py — Ф3 персистентность анти-повтора: FSM-состояние в context_cards.

Оркестрирует: читает строку строго ДО сегодня (date<today), гоняет чистый fsm_next
(brief_gate) + gate/slot, upsert-ит строку за сегодня. Идемпотентность (риск #1):
переход считается от строки date<today, НЕ от своей же сегодняшней → повторный прогон
(catch-up) даёт ту же строку. UNIQUE(semantic_key,date) + ON CONFLICT DO UPDATE.

Кулдаун-часы = ФАКТИЧЕСКИЙ показ: last_shown_at двигается только если карточка реально
выбрана в бриф (прошла gate И slot), не по fsm.show. Telegram-доставочный гейт
(commit-после-message_id) — в Ф4-обвязке.

ЖИВОЙ за флагом MORNING_BRIEF_GATE (вкл. в проде оба тенанта, 2026-07): plan вызывается
из generate_daily_report, commit — из send_morning_report после доставки. resolved-ветка:
находки, что были активны, но сегодня отсутствуют, → resolved; ПОКАЗЫВАЕТСЯ только если
карточку фактически показали в этом эпизоде (episode_shown → prev.shown_in_episode →
fsm_next, решение владельца 28.09), и несёт напоминание, что было (resolved_reminder). Все claim-поля пишутся как есть (JSON), trust не
хранится (derived).
"""
from __future__ import annotations

import json
from datetime import date

from brief_gate import (fsm_next, gate_decision, select_slots, is_suggestion,
                        is_worsened, WORSE_SEVERITY_DELTA)

_ACTIVE = ("new_alert", "still_active", "worsened")
_RESOLVED_LOOKBACK_DAYS = 30

_INSERT = """
INSERT INTO context_cards
  (date, provider, semantic_key, lane, origin, delivery, severity, status,
   gate_reason, recurrence_state, last_value, last_shown_at, escalation_level,
   evidence_summary, allowed_claims, forbidden_claims)
VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
ON CONFLICT(semantic_key, date) DO UPDATE SET
  provider=excluded.provider, lane=excluded.lane, origin=excluded.origin,
  delivery=excluded.delivery, severity=excluded.severity, status=excluded.status,
  gate_reason=excluded.gate_reason, recurrence_state=excluded.recurrence_state,
  last_value=excluded.last_value, last_shown_at=excluded.last_shown_at,
  escalation_level=excluded.escalation_level, evidence_summary=excluded.evidence_summary,
  allowed_claims=excluded.allowed_claims, forbidden_claims=excluded.forbidden_claims
"""


def _d(x) -> date | None:
    return date.fromisoformat(x) if isinstance(x, str) and x else (x or None)


def get_prior_state(conn, semantic_key: str, before: date) -> dict | None:
    """Строка строго ДО `before` — источник prev для fsm (гарантия идемпотентности)."""
    row = conn.execute(
        "SELECT recurrence_state, last_shown_at, escalation_level, severity "
        "FROM context_cards WHERE semantic_key=? AND date<? ORDER BY date DESC LIMIT 1",
        (semantic_key, str(before)),
    ).fetchone()
    if not row:
        return None
    return {"state": row[0], "last_shown": _d(row[1]),
            "recaps": row[2] or 0, "severity": row[3]}


def episode_shown(conn, semantic_key: str, before: date) -> dict | None:
    """Был ли ключ ФАКТИЧЕСКИ показан в текущем эпизоде (строки date<before после последнего
    resolved). Показан → {start, last_active, shown_on, evidence} (evidence — строка первого
    показа: именно её человек и читал); не показан → None.

    Зачем отдельно от prev: state new_alert/still_active пишется и отсеянной порогом или
    слотом карточке, а last_shown переживает эпизоды (кулдаун-часы, 04.08) — ни то ни другое
    не отвечает «видел ли человек ЭТОТ эпизод»."""
    last_res = conn.execute(
        "SELECT MAX(date) FROM context_cards WHERE semantic_key=? AND date<? "
        "AND recurrence_state='resolved'", (semantic_key, str(before))).fetchone()[0]
    rows = conn.execute(
        "SELECT date, status, evidence_summary FROM context_cards "
        "WHERE semantic_key=? AND date<? AND date>? "
        "AND recurrence_state IN ('new_alert','still_active','worsened') ORDER BY date",
        (semantic_key, str(before), last_res or "")).fetchall()
    shown = [r for r in rows if r[1] == "shown"]
    if not shown:
        return None
    return {"start": rows[0][0], "last_active": rows[-1][0],
            "shown_on": shown[0][0], "evidence": shown[0][2]}


def _ddmm(iso: str) -> str:
    return f"{iso[8:10]}.{iso[5:7]}"


def resolved_reminder(ep: dict) -> str:
    """Текст «прошло», читаемый без памяти (решение владельца 28.09: «я же не могу помнить
    всё, что мне говорили»): что было, с какого по какое число, когда об этом говорили."""
    span = (f"с {_ddmm(ep['start'])}" if ep["start"] == ep["last_active"]
            else f"с {_ddmm(ep['start'])} по {_ddmm(ep['last_active'])}")
    return (f"было: {ep['evidence'] or 'отклонение'} — держалось {span}, "
            f"человеку говорили {_ddmm(ep['shown_on'])}; сейчас вернулось к обычному")


def _write(conn, *, today, provider, semantic_key, lane, origin, delivery, severity,
           status, gate_reason, state, last_value, last_shown, recaps,
           evidence, allowed, forbidden):
    conn.execute(_INSERT, (
        str(today), provider, semantic_key, lane, origin, delivery, severity, status,
        gate_reason, state, last_value,
        last_shown.isoformat() if isinstance(last_shown, date) else last_shown,
        recaps, evidence,
        json.dumps(allowed, ensure_ascii=False) if allowed is not None else None,
        json.dumps(forbidden or [], ensure_ascii=False),
    ))


def plan(cards: list, today: date, conn):
    """Читает prior (date<today), считает FSM+gate+slot. НЕ пишет и НЕ коммитит.
    Возвращает (decisions, specs); specs → commit() ПОСЛЕ подтверждённой доставки
    (квитанция: кулдаун-часы двигаются только по реально доставленному брифу).
    Идемпотентно: prev всегда date<today."""
    specs: list = []
    prelim = []
    # Э6: шумовой пол ухудшения из config (override), seed — WORSE_SEVERITY_DELTA. Читаем
    # один раз, из БД ТОГО ЖЕ тенанта (conn) — иначе датчик целостности читал бы чужой порог.
    import config_db as _cfg
    try:
        worse_delta = float(_cfg.get_config("brief.worse_severity_delta",
                                            WORSE_SEVERITY_DELTA, conn=conn))
    except (TypeError, ValueError):
        worse_delta = WORSE_SEVERITY_DELTA
    for card in cards:
        prev = get_prior_state(conn, card.semantic_key, today)
        worse = is_worsened(card.severity, prev.get("severity") if prev else None,
                            card.lane, worse_delta)
        fsm = fsm_next(prev, {"present": True, "worse": worse, "date": today})
        gate = gate_decision(card, fsm)
        prelim.append((card, prev, fsm, gate))

    candidates = [c for (c, _p, _f, g) in prelim if g["status"] == "candidate"]
    shown = {id(c) for c in select_slots(candidates)}

    decisions = []
    for card, prev, fsm, gate in prelim:
        is_shown = id(card) in shown
        if is_shown:
            status, reason = "shown", gate["gate_reason"]
            last_shown, recaps = fsm["last_shown"], fsm["recaps"]
        else:
            # проиграл слот, хотя FSM показал бы — не двигаем кулдаун-часы
            if gate["status"] == "candidate":
                status, reason = "suppressed_gate", "slot_budget_full"
            else:
                status, reason = gate["status"], gate["gate_reason"]
            last_shown = (prev or {}).get("last_shown")
            recaps = (prev or {}).get("recaps", 0)
        specs.append(dict(today=today, provider=card.provider, semantic_key=card.semantic_key,
                          lane=card.lane, origin=card.origin, delivery=card.delivery,
                          severity=card.severity, status=status, gate_reason=reason,
                          state=fsm["state"], last_value=card.last_value, last_shown=last_shown,
                          recaps=recaps, evidence=card.evidence_summary,
                          allowed=card.allowed_claims, forbidden=card.forbidden_claims))
        decisions.append({"semantic_key": card.semantic_key, "status": status,
                          "state": fsm["state"], "reason": reason})

    # resolved: активные ранее, отсутствуют сегодня
    today_keys = {c.semantic_key for c in cards}
    rows = conn.execute(
        "SELECT c.semantic_key, c.provider, c.lane, c.recurrence_state, c.last_shown_at, "
        "       c.escalation_level, c.severity, c.evidence_summary, c.origin "
        "FROM context_cards c JOIN ("
        "  SELECT semantic_key, MAX(date) md FROM context_cards WHERE date<? GROUP BY semantic_key"
        ") m ON c.semantic_key=m.semantic_key AND c.date=m.md "
        "WHERE c.recurrence_state IN ('new_alert','still_active','worsened') "
        "  AND c.date >= date(?, '-' || ? || ' days')",
        (str(today), str(today), _RESOLVED_LOOKBACK_DAYS),
    ).fetchall()
    for r in rows:
        if r[0] in today_keys:
            continue
        ep = episode_shown(conn, r[0], today)
        prev = {"state": r[3], "last_shown": _d(r[4]), "recaps": r[5] or 0, "severity": r[6],
                "shown_in_episode": ep is not None}
        fsm = fsm_next(prev, {"present": False, "worse": False, "date": today})
        show, reason = fsm["show"], fsm["reason"]
        if show:
            status = "shown"
        elif fsm["state"] == "resolved":
            status = "suppressed_gate"          # отбой тревоги, которую не показывали
        else:
            status = "suppressed_cooldown"
        evidence = r[7]
        if show and ep and fsm["state"] == "resolved":
            evidence = resolved_reminder(ep)
        # Предложение не «выздоравливает»: resolved для него не показываем.
        # Строку всё же пишем (state='resolved'), чтобы ключ вышел из активного
        # множества и завтрашний JOIN не открыл его заново.
        if fsm["state"] == "resolved" and is_suggestion(r[1], r[0]):
            show, status, reason = False, "suppressed_gate", "resolved_noise_for_suggestion"
        specs.append(dict(today=today, provider=r[1], semantic_key=r[0], lane=r[2],
                          origin=r[8] or "internal", delivery="computed", severity=r[6],
                          status=status,
                          gate_reason=reason, state=fsm["state"], last_value=None,
                          last_shown=fsm["last_shown"], recaps=fsm["recaps"],
                          evidence=evidence, allowed=None, forbidden=[]))
        # provider/evidence — чтобы бриф мог сказать «прошло» С НАПОМИНАНИЕМ (канал
        # resolved в gp_agent): находки сегодня нет, и других носителей её текста нет.
        decisions.append({"semantic_key": r[0], "status": ("shown" if show else "none"),
                          "state": fsm["state"], "reason": reason,
                          "provider": r[1], "evidence": evidence, "origin": r[8],
                          # что человек читал и когда — для отбоев со своим текстом (гипотезы)
                          "was": ep["evidence"] if ep else None,
                          "shown_on": ep["shown_on"] if ep else None})

    return decisions, specs


def commit(specs: list, conn) -> None:
    """Пишет запланированные строки и коммитит — вызывать ПОСЛЕ доставки брифа."""
    for s in specs:
        _write(conn, **s)
    conn.commit()


def advance(cards: list, today: date, conn) -> list[dict]:
    """plan + немедленный commit (прайминг/shadow/on-demand preview). Возвращает decisions."""
    decisions, specs = plan(cards, today, conn)
    commit(specs, conn)
    return decisions
