"""Actionable tenant mail. See contracts/agent_signals.json for attempt semantics.

# INTENT: agent_signals — deterministic, optional assistant notifications.
"""
from __future__ import annotations

from datetime import date, timedelta, timezone
import hashlib
import json
import math
import re
from typing import TypedDict

from _time_inject import get_now
import agent_mail
import config_db
import health_db
import i18n
import lab_canon
import labs_db
import profile_db
import safety_net
import treatment_db


class Signal(TypedDict):
    key: str
    what: str
    why: str
    proposal: str
    urgency: str
    facts: dict


def policy() -> dict:
    """Validated tenant policy; missing seeds fail visibly, never use numeric fallbacks."""
    value = config_db.get_config("agent_signals.policy")
    keys = ("baseline_days", "min_samples", "stress_min_samples", "sleep_nights",
            "sleep_percentile", "stress_percentile", "lab_lead_days",
            "morning_offset_minutes", "stress_import_hour", "stress_offset_minutes", "max_age_hours")
    if not isinstance(value, dict):
        raise ValueError("agent_signals: missing policy")
    for key in keys:
        n = value.get(key)
        if type(n) is not int or n < 0:
            raise ValueError(f"agent_signals: invalid policy {key}")
    if (min(value[k] for k in ("baseline_days", "min_samples", "stress_min_samples",
                              "sleep_nights", "max_age_hours")) < 1
            or not 0 < value["sleep_percentile"] <= 100
            or not 0 < value["stress_percentile"] <= 100
            or value["stress_import_hour"] >= 24):
        raise ValueError("agent_signals: invalid policy range")
    return value


def _recipient() -> str:
    value = config_db.get_config("agent_mail.assistant", "")
    if not isinstance(value, str):
        raise ValueError("agent_signals: invalid assistant")
    value = value.strip()
    if value and not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", value):
        raise ValueError("agent_signals: invalid assistant address")
    return value


def _signal(key, what, why, proposal, facts, urgency="сегодня") -> Signal:
    return Signal(key=key, what=what, why=why, proposal=proposal,
                  urgency=urgency, facts=facts)


def _number(value):
    if value is None:
        return None
    value = float(value)
    if not math.isfinite(value) or value < 0:
        raise ValueError("agent_signals: invalid metric")
    return value


def state_signals(day: date) -> list[Signal]:
    """Evaluate the specified observation day; history strictly precedes it.

    No model, sends or writes. safety_net supplies illness indications unchanged.
    Missing observations/baselines cannot produce a positive finding.
    """
    cfg = policy()
    rows = health_db.get_window(day + timedelta(days=1), cfg["sleep_nights"])
    current = rows[0]
    signals = []
    for rule in health_db.get_absolute_thresholds_for_person("floor"):
        metric = rule["metric"]
        if (metric not in ("sleep_total", "readiness") or rule["kind"] != "absolute"
                or rule["source"] != "p10_personal" or rule["variant"] or rule["band_label"]):
            continue
        value, floor = _number(current.get(metric)), _number(rule["value"])
        if value is None or floor is None or value >= floor:
            continue
        if metric == "sleep_total":
            what = f"Короткий сон — {value:g} ч при личной нижней границе {floor:g} ч"
            why = "После короткой ночи может быть труднее выдержать обычную нагрузку."
            proposal = "Предлагаю облегчить день и перенести тяжёлые задачи."
        else:
            what = f"Готовность {value:g} ниже личной нижней границы {floor:g}"
            why = "Восстановление ниже привычного, обычная нагрузка может даваться тяжелее."
            proposal = "Предлагаю оставить больше пауз и обсудить снижение нагрузки."
        signals.append(_signal(metric, what, why, proposal,
                               dict(day=str(day), value=value, floor=floor, source=rule["source"])))

    median = health_db.get_metric_percentile("sleep_total", cfg["sleep_percentile"],
                                             cfg["baseline_days"], cfg["min_samples"], day)
    nights = [_number(row.get("sleep_total")) for row in rows]
    if median is not None and all(v is not None and v < median for v in nights):
        signals.append(_signal("sleep_series", f"{len(nights)} ночи подряд короче личной медианы",
                               "Недосып повторяется несколько ночей подряд.",
                               "Предлагаю освободить вечер для отдыха и сна.",
                               dict(day=str(day), nights=nights, median=median)))

    for alert in safety_net.check_lifestyle_alerts(day, initialize=False):
        if alert["metric"] not in ("ЧСС покоя", "HRV"):
            continue
        key = "illness_rhr" if alert["metric"] == "ЧСС покоя" else "illness_hrv"
        signals.append(_signal(key, alert["note"],
                               "Такое изменение может сопровождать начало болезни или перегрузку.",
                               "Предлагаю уточнить самочувствие и обсудить более спокойный день.",
                               dict(day=str(day), **alert)))

    stress = _number(current.get("stress_high_min"))
    p90 = health_db.get_metric_percentile("stress_high_min", cfg["stress_percentile"],
                                          cfg["baseline_days"], cfg["stress_min_samples"], day)
    if stress is not None and p90 is not None and stress > p90:
        signals.append(_signal("stress", f"Высокий стресс — {stress:g} мин, личный p{cfg['stress_percentile']} — {p90:g} мин",
                               "Накопившийся стресс может мешать восстановлению.",
                               "Предлагаю найти время для прогулки и снять необязательную нагрузку.",
                               dict(day=str(day), minutes=stress, percentile=p90)))
    return signals


def _blocks(signals: list[Signal]) -> str:
    blocks = []
    for signal in signals:
        blocks.append(f"Что случилось: {signal['what']}\nПочему важно: {signal['why']}\n"
                      f"Что предлагаю: {signal['proposal']}\nСрочность: {signal['urgency']}\n"
                      "Факты: " + json.dumps(signal["facts"], ensure_ascii=False, sort_keys=True))
    return "\n\n".join(blocks) + "\n\nРешение — за ассистентом вместе с пользователем."


def _ledger_key(recipient: str, kind: str) -> str:
    return "agent_signals.ledger." + recipient + "." + kind


def _publish(recipient, kind, day, signals, *, phase="", snapshot=None):
    """Reserve attempts atomically BEFORE send. A crash can lose mail, never retry it.

    system_config owns storage mechanics; this module owns these ledger values.
    A transport id acknowledges spool publication only, not recipient delivery.
    """
    if not signals:
        return None
    ledger_key = _ledger_key(recipient, kind)
    with health_db.get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        ledger = config_db.get_config(ledger_key, {"tokens": [], "slots": []}, conn=conn)
        slot = f"{day}:{phase}"
        if kind == "state" and slot in ledger["slots"]:
            return None
        if kind == "lab_due" and str(day) in ledger["slots"]:
            return None
        if kind == "regimen" and snapshot == ledger.get("snapshot"):
            return None
        fresh = [s for s in signals if s["key"] not in ledger["tokens"]]
        if kind == "regimen":
            fresh = signals   # A -> B -> A is a change too.
        if not fresh:
            return None
        ledger["tokens"].extend(s["key"] for s in fresh)
        ledger["slots"].append(str(day) if kind == "lab_due" else slot)
        if snapshot is not None:
            ledger["snapshot"] = snapshot
        config_db.upsert_config(ledger_key, value_json=ledger, category="agent_mail",
                                source="agent_signals", conn=conn)
    subject = f"health: {fresh[0]['what'].splitlines()[0]} ({day})"
    return agent_mail.send([recipient], subject, _blocks(fresh), kind=kind)


def _regimen(recipient, day):
    rows = treatment_db.get_medications(include_proposed=True)
    snapshot = sorted([{k: row.get(k) or "" for k in ("name", "dosage", "frequency", "notes")}
                       for row in rows if row["modality"] == "supplement" and row["status"] == "active"],
                      key=lambda row: json.dumps(row, sort_keys=True, ensure_ascii=False))
    previous = config_db.get_config(_ledger_key(recipient, "regimen"), {}).get("snapshot")
    if previous == snapshot:
        return None
    before, after = {}, {}
    for row in previous or []:
        before.setdefault(row["name"], []).append(row)
    for row in snapshot:
        after.setdefault(row["name"], []).append(row)
    changes = []
    for name in sorted(before.keys() - after.keys()):
        changes.append(f"Убрано: {name}")
    for name in sorted(after.keys() - before.keys()):
        changes.append(f"Добавлено: {name}")
    for name in sorted(before.keys() & after.keys()):
        if before[name] != after[name]:
            changes.append(f"Поменялась схема: {name}\nБыло: "
                           + "; ".join(" | ".join(row.values()) for row in before[name]))
    lines = [" | ".join(row.values()) for row in snapshot]
    body = "\n".join([*changes, "Текущая карточка добавок", *lines])
    if not snapshot:
        body += "\nАктивных добавок нет."
    fingerprint = hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    signal = _signal(fingerprint, body, "Напоминания должны соответствовать текущей схеме.",
                     "Предлагаю сверить и обновить напоминания. Примечания к схеме приведены дословно.",
                     dict(fingerprint=fingerprint), "когда удобно")
    return _publish(recipient, "regimen", day, [signal], snapshot=snapshot)


def _labs(recipient, day):
    # tenant_lab_schedule is the single home of the lab-interval rule (same as the GP report).
    # GP freshness offers prose/overdue only; this reader also needs the exact due date.
    cfg = policy()
    profile = profile_db.get_patient_profile()
    lang = i18n.lang_of(profile)
    fasting = str(profile.get("routine.fasting_labs", "")).lower() == "true"
    signals = []
    # Аналит без последней точки в ответ lab_due не попадает: срок повтора не вычисляется.
    for name, d in sorted(labs_db.lab_due(labs_db.tenant_lab_schedule(), day).items()):
        due, last, interval, test = d["due"], d["last"], d["interval_days"], name
        if (due - day).days > cfg["lab_lead_days"]:
            continue
        russian = next((s for s in lab_canon.synonyms_for(name) if re.search("[а-яА-ЯёЁ]", s)), test)
        origin = labs_db.schedule_origin(d["source"])
        what = f"{russian[:1].upper() + russian[1:]} — срок {due}, последний раз {last}"
        what += "\n" + i18n.t("agent_signals.lab.interval_origin", lang,
                              origin=i18n.t(f"labs.schedule_origin.{origin}", lang))
        urgency = "today" if due <= day else "this_week"
        if origin == "system" and (day - due).days > interval:
            urgency = "next_draw"
        proposal = "Предлагаю согласовать запись на анализ" + (" натощак." if fasting else ".")
        signals.append(_signal(f"{name}:{due}", what, "Подходит срок планового контроля.",
                               proposal, dict(test=name, last=last, due=str(due), interval_days=interval,
                                              source=d["source"], origin=origin),
                               i18n.t(f"agent_signals.urgency.{urgency}", lang)))
    return _publish(recipient, "lab_due", day, signals)


def run(day: date, *, phase: str = "morning") -> list[str]:
    """Morning runs all three kinds; afternoon sends only a new stress key.

    Empty recipient disables all reading/evaluation and mail. Heartbeat records
    completed passes, including normal/no-data days. No delivery receipt is implied.
    """
    recipient = _recipient()
    if not recipient:
        return []
    if phase not in ("morning", "afternoon"):
        raise ValueError("agent_signals: unknown phase")
    signals = state_signals(day)
    if phase == "afternoon":
        signals = [s for s in signals if s["key"] == "stress"]
    signals = [{**s, "key": f"{day}:{s['key']}"} for s in signals]
    sent = [_publish(recipient, "state", day, signals, phase=phase)]
    if phase == "morning":
        sent.extend([_regimen(recipient, day), _labs(recipient, day)])
    config_db.upsert_config(f"agent_signals.last_{phase}", value_num=get_now(timezone.utc).timestamp(),
                            category="agent_mail", source="agent_signals")
    return [mid for mid in sent if mid]


def last_run_age_hours() -> float | None:
    """Read model for integrity. None means disabled; missing morning pass is infinite."""
    if not _recipient():
        return None
    stamp = config_db.get_config("agent_signals.last_morning")
    if stamp is None:
        return math.inf
    return (get_now(timezone.utc).timestamp() - float(stamp)) / 3600
