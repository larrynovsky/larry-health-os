#!/usr/bin/env python3.11
"""survivorship_analyzer — двухнедельный сводчик данных для survivorship-curator.

Один контракт: читает живые метрики + PRO + литературу + контекст системы →
структурированные findings в agent_reports(agent_type='survivorship_analysis').
Не эскалирует — это работа survivorship_curator.

Расписание: launchd com.larry.health.survivorship-analyze (Пн 03:30 раз в 2 недели).
CLI: python3.11 survivorship_analyzer.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import yaml
from datetime import date, timedelta
from _time_inject import get_today  # seam
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import health_db as db
from assessment_scheduler import tenant_data_path

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

INSTRUMENTS_DIR = tenant_data_path("instruments")
CONFIG = tenant_data_path("survivorship_config.yaml")


def _load_instruments():
    out = []
    if not INSTRUMENTS_DIR.exists():
        return out
    for f in sorted(INSTRUMENTS_DIR.glob("*.json")):
        try:
            out.append(json.loads(f.read_text()))
        except Exception as e:  # silent-ok: broken JSON в каталоге — пропуск инструмента
            log.warning(f"failed to parse {f.name}: {e}")
    return out


def _latest_pro_per_subscale(instrument_id, window_days=180):
    """Возвращает {subscale_id: (value, date)} последнее заполнение каждой подшкалы."""
    cutoff = (get_today() - timedelta(days=window_days)).isoformat()
    out = {}
    try:
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT test_name, value, date FROM lab_results "
                "WHERE source = ? AND date >= ? "
                "ORDER BY date DESC",
                (f"instrument:{instrument_id}", cutoff),
            ).fetchall()
        for r in rows:
            name = r["test_name"]
            if name not in out:
                out[name] = (r["value"], r["date"])
    except Exception as e:
        log.warning(f"_latest_pro_per_subscale: {e}")
    return out


def _metric_window_mean(metric, end_date, window_days):
    """Среднее значение колонки daily_metrics за window_days до end_date."""
    cutoff = (end_date - timedelta(days=window_days)).isoformat()
    try:
        with db.get_conn() as conn:
            row = conn.execute(
                f"SELECT AVG({metric}) AS m, COUNT(*) AS n FROM daily_metrics "
                f"WHERE date BETWEEN ? AND ?",
                (cutoff, end_date.isoformat()),
            ).fetchone()
        if row and row["n"]:
            return (row["m"], row["n"])
    except Exception as e:
        log.warning(f"_metric_window_mean({metric}): {e}")
    return (None, 0)


def _pro_age_days(pro_date, end_date):
    """Возраст самоотчёта в днях на дату прогона. None — дата нечитаема.

    Отдельная функция, потому что её зовут двое: предел давности (ниже) и его тест.
    Нечитаемая дата НЕ считается свежей: вызывающий трактует None как «судить не на чем»
    и пропускает предел, а не молча признаёт самоотчёт годным."""
    try:
        return (end_date - date.fromisoformat(str(pro_date)[:10])).days
    except (TypeError, ValueError):
        return None


def _compute_shadow_finding(instrument, subscale_id, pro_value, pro_date, rule, end_date):
    """Сравнивает PRO с проксями. Возвращает dict-finding или None если нет данных."""
    proxies = rule.get("proxies") or []
    min_window = int(rule.get("min_window_days", 7))
    threshold = float(rule.get("divergence_threshold", 0.5))

    # Окно проксей якорится на дату опросника, а не на дату запуска.
    # Независимо выдуманный пример: самоотчёт от 2042-02-16 нельзя сравнивать
    # с окном прибора 2042-05-02–2042-05-08 как с одновременным наблюдением.
    # Разные окна могут создать ложное расхождение (§17).
    # При корректной дате у окна один якорь — дата самоотчёта.
    try:
        anchor = date.fromisoformat(str(pro_date)[:10])
    except (TypeError, ValueError):
        anchor = end_date  # дата нечитаема — ведём себя как раньше, но помечаем это в находке

    proxy_values = {}
    for p in proxies:
        m, n = _metric_window_mean(p, anchor, min_window)
        if m is not None:
            proxy_values[p] = {"mean": round(m, 2), "n": n}

    if not proxy_values:
        return None  # shadow blind — нет данных по проксям

    return {
        "type": "shadow_check",
        "instrument": instrument["id"],
        "subscale": subscale_id,
        "pro_value": pro_value,
        "pro_date": pro_date,
        "proxy_window_days": min_window,
        "proxy_window_end": anchor.isoformat(),
        "proxy_anchored_on_pro": anchor != end_date,
        "proxy_values": proxy_values,
        "divergence_threshold_config": threshold,
        "note": rule.get("note", ""),
    }


def _analyse_instrument(instrument, end_date):
    findings = []
    latest_pro = _latest_pro_per_subscale(instrument["id"])
    if not latest_pro:
        findings.append({
            "type": "no_pro_yet",
            "instrument": instrument["id"],
            "cadence_days": instrument.get("cadence_days"),
            "suggestion": f"Опросник {instrument['id']} ещё ни разу не заполнен — попросить заполнить.",
        })
        return findings

    for rule in instrument.get("shadow_rules") or []:
        target = rule.get("item_or_subscale")
        if not target:
            continue
        # Имя в lab_results — instrument_id + "_" + target (например isi_q2 или mfsi_sf_general_fatigue)
        possible_test_names = [
            f"{instrument['id']}_{target}",
            target,
        ]
        pro_record = None
        for t in possible_test_names:
            if t in latest_pro:
                pro_record = (t, latest_pro[t][0], latest_pro[t][1])
                break
        if not pro_record:
            findings.append({
                "type": "pro_missing_for_rule",
                "instrument": instrument["id"],
                "target": target,
                "note": "shadow_rule есть в каталоге, PRO для этого target не найден",
            })
            continue
        test_name, pro_value, pro_date = pro_record
        # Самоотчёт старше периодичности своего опросника в сравнение не идёт:
        # вместо конфликта рождается просьба заполнить. Дом срока — cadence_days
        # инструмента (§9). Само наличие записи в окне поиска не доказывает,
        # что она достаточно свежая для сравнения с показателями прибора.
        stale_days = _pro_age_days(pro_date, end_date)
        cadence = instrument.get("cadence_days")
        if cadence and stale_days is not None and stale_days > int(cadence):
            findings.append({
                "type": "pro_stale",
                "instrument": instrument["id"],
                "target": target,
                "pro_date": pro_date,
                "age_days": stale_days,
                "cadence_days": int(cadence),
                "suggestion": (f"Самоотчёт {instrument['id']}/{target} от {pro_date} старше "
                               f"периодичности ({stale_days}д > {cadence}д) — сравнивать его с "
                               f"приборами нечестно; попросить заполнить опросник."),
            })
            continue
        f = _compute_shadow_finding(instrument, target, pro_value, pro_date, rule, end_date)
        if f:
            f["test_name"] = test_name
            findings.append(f)
    return findings


def _global_context_findings(end_date):
    """Анализ контекста: тренды метрик + свежая литература + активные alerts."""
    findings = []

    # 1. Метрики-тренды: сравнить 7д vs 30д для ключевых метрик
    for metric in ("hrv", "sleep_deep", "readiness", "resting_hr"):
        m7, n7 = _metric_window_mean(metric, end_date, 7)
        m30, n30 = _metric_window_mean(metric, end_date, 30)
        if m7 is not None and m30 is not None and n7 >= 4 and n30 >= 14:
            delta = m7 - m30
            pct = (delta / m30 * 100) if m30 else 0
            if abs(pct) >= 10:
                findings.append({
                    "type": "metric_drift",
                    "metric": metric,
                    "mean_7d": round(m7, 2),
                    "mean_30d": round(m30, 2),
                    "delta_pct": round(pct, 1),
                    "n_7d": n7, "n_30d": n30,
                })

    # 2. Свежая литература за 14 дней
    try:
        cutoff = (end_date - timedelta(days=14)).isoformat()
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT findings FROM agent_reports "
                "WHERE agent_type='publication_reading' AND date >= ?",
                (cutoff,),
            ).fetchall()
        lit_count = 0
        high_relevance = []
        for r in rows:
            try:
                lst = json.loads(r["findings"] or "[]")
                for it in lst:
                    if it.get("status") == "kept":
                        lit_count += 1
                        if it.get("applicability_to_me") in ("direct", "partial"):
                            high_relevance.append({
                                "pmid": it.get("pmid"),
                                "title": it.get("title", "")[:120],
                                "claim": it.get("claim", "")[:200],
                                "applicability_to_me": it.get("applicability_to_me"),
                            })
            except Exception:  # silent-ok: broken JSON, skip
                continue
        if high_relevance:
            findings.append({
                "type": "recent_literature_relevant",
                "count_total_kept": lit_count,
                "high_relevance": high_relevance[:8],
            })
    except Exception as e:
        log.warning(f"recent literature scan: {e}")

    # 3. Активные survivorship alerts (для напоминания контексту)
    try:
        alerts = db.get_active_alerts(source_like="survivorship%") or []
        if alerts:
            findings.append({
                "type": "active_survivorship_rules",
                "count": len(alerts),
                "rules": [{"id": a["id"], "type": a["type"], "severity": a["severity"],
                           "source": a["source"]} for a in alerts],
            })
    except Exception as e:
        log.warning(f"alerts scan: {e}")

    return findings


def run(dry_run=False):
    cfg = {}
    if CONFIG.exists():
        try:
            cfg = yaml.safe_load(CONFIG.read_text()) or {}
        except Exception:  # silent-ok: config broken, продолжаем без него
            cfg = {}
    pilot = cfg.get("pilot_flags") or {}
    analyzer_dry = bool(pilot.get("analyzer_dry_run") or dry_run)

    end_date = get_today() - timedelta(days=1)
    log.info(f"survivorship_analyzer: end_date={end_date}, dry_run={analyzer_dry}")

    instruments = _load_instruments()
    all_findings = {
        "per_instrument": {},
        "global": _global_context_findings(end_date),
    }
    for ins in instruments:
        f = _analyse_instrument(ins, end_date)
        all_findings["per_instrument"][ins["id"]] = f

    findings_count = (
        sum(len(v) for v in all_findings["per_instrument"].values())
        + len(all_findings["global"])
    )
    log.info(f"  produced {findings_count} findings total")

    if analyzer_dry:
        log.info("DRY-RUN: not saving to agent_reports")
        print(json.dumps(all_findings, ensure_ascii=False, indent=2))
        return all_findings

    try:
        db.save_agent_report(
            agent_type="survivorship_analysis",
            agent_name="survivorship_analyzer",
            date_str=str(end_date),
            has_findings=1 if findings_count else 0,
            data_queried=["instruments", "lab_results:instrument", "daily_metrics",
                          "agent_reports:publication_reading", "alerts:survivorship"],
            pubmed_ids=[],
            peers_reviewed=[],
            changes_summary=f"{findings_count} findings ({len(instruments)} instruments + global)",
            findings=json.dumps(all_findings, ensure_ascii=False),
            recommendations=None,
            raw_output=None,
            period_days=14,
        )
        log.info("saved survivorship_analysis report")
    except Exception as e:
        log.error(f"save_agent_report failed: {e}")

    return all_findings


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    run(dry_run=args.dry_run)
