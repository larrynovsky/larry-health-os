#!/usr/bin/env python3.11
"""survivorship_curator — решает что делать с каждым survivorship-finding.

Контракт идентичен literature_curator: finding + текущий контекст → одна из
пяти точек эскалации:
  - hypothesis (cbcr_hypothesis + semantic dedup + save_hypothesis)
  - problem_proposal (save_problem_proposal)
  - task (save_task с fingerprint)
  - note (save_memory category='survivorship_note')
  ⚰ constitution_conflict снят 28.09.2026 (см. _CONSTITUTION_HORIZON_NOTE)

Запускается после survivorship_analyzer. Раз в две недели Пн 04:00.

CLI: python3.11 survivorship_curator.py [--max N] [--dry-run]
"""
from __future__ import annotations
import hai_core
import i18n

import argparse
import json
import logging
import re
import sys
from datetime import date, timedelta
from _time_inject import get_today  # seam
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import health_db as db
from hai_core import get_client
import hai_hypotheses as hh
import hypothesis_semantic_check as semcheck

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

HAIKU_MODEL = hai_core.MODEL_DEFAULTS["haiku"]
DEFAULT_MAX_PER_RUN = 10

DECISION_PROMPT = """Ты — curator survivorship-данных. На вход тебе дан structured finding
от survivorship_analyzer и краткий контекст пациента. Реши, КУДА эскалировать.

Четыре варианта:
1. "hypothesis" — есть тестируемая гипотеза о механизме (например, "shadow расхождение
   между sleep_deep и PRO sleep_quality показывает X").
2. "problem_proposal" — finding ставит под вопрос АКТИВНУЮ проблему или активный
   протокол. Например, новые литературные данные о препарате в активном протоколе.
3. "task" — конкретное действие в окне времени (например, "пересдать FE-1",
   "сравнить трend HRV с лабораторией").
4. "note" — полезная информация для будущего контекста, действия не требует.

КОНТЕКСТ ПАЦИЕНТА:
{context}

FINDING:
{finding_json}

Ответь СТРОГО валидным JSON:
{{
  "action": "hypothesis"|"problem_proposal"|"task"|"note",
  "rationale": "1-2 предложения почему",
  "summary": "что эскалируем (1-2 предложения, конкретно)",
  "linked_problem_id": int|null,
  "task_type": "lab_test"|"action"|"consult"|null,
  "task_deadline_days": int|null
}}
"""


SKIP_FINDING_TYPES = {"recent_literature_relevant", "active_survivorship_rules", "pro_missing_for_rule"}


def _build_short_context():
    lines = []
    try:
        profile = db.get_patient_profile() or {}
        med = {k: v for k, v in profile.items() if k.startswith("medical.")}
        for k, v in list(med.items())[:6]:
            lines.append(f"  {k}: {str(v)[:80]}")
    except Exception:  # silent-ok: profile может быть пуст
        pass
    try:
        probs = [p for p in (db.get_problem_list() or []) if p.get("status") in ("active", "active_monitoring", "watchful_waiting")]
        if probs:
            lines.append("Активные проблемы:")
            for p in probs[:8]:
                lines.append(f"  #{p.get('problem_id', p.get('id'))}: {p.get('title', '')[:80]}")
    except Exception:  # silent-ok: контекст-getter, пропускаем при отсутствии данных
        pass
    try:
        protos = db.get_active_protocols() or []
        if protos:
            lines.append("Активные протоколы:")
            for pr in protos[:5]:
                lines.append(f"  #{pr.get('id')}: {pr.get('title', '')[:80]}")
    except Exception:  # silent-ok: контекст-getter, пропускаем при отсутствии данных
        pass
    try:
        # SX-16: read encounters (новые онко-визиты) + legacy consultations
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT ev.effective_date, en.specialty, ev.performer "
                "FROM encounters en JOIN events ev ON ev.id = en.event_id "
                "WHERE ev.effective_date >= date('now', '-365 days') "
                "ORDER BY ev.effective_date DESC LIMIT 5"
            ).fetchall()
        if rows:
            lines.append("Недавние врач-визиты (encounters):")
            for r in rows:
                lines.append(f"  {r['effective_date']} {r['specialty']}: {r['performer']}")
    except Exception:  # silent-ok: encounters может не быть на test-БД
        pass
    # Блок «Legacy consultations» снят 27.09 (BL-CONSULT-SECOND-HOME-1): подпись «до 2025»
    # врала — он печатал три ПОСЛЕДНИХ строки копии, то есть те же приёмы, что блок выше.
    try:
        hyps = hh.get_open_hypotheses(n=5) or []
        if hyps:
            lines.append("Открытые гипотезы:")
            for h in hyps:
                lines.append(f"  #{h.get('memory_id')}: {(h.get('observation') or '')[:80]}")
    except Exception:  # silent-ok: контекст-getter, пропускаем при отсутствии данных
        pass
    try:
        alerts = db.get_active_alerts(source_like="survivorship%") or []
        if alerts:
            lines.append("Survivorship-правила:")
            for a in alerts[:5]:
                lines.append(f"  #{a['id']} [{a['severity']}]: {a['message'][:80]}")
    except Exception:  # silent-ok: контекст-getter, пропускаем при отсутствии данных
        pass
    return "\n".join(lines) if lines else "(контекст недоступен)"


def _haiku_decide(finding, context):
    prompt = DECISION_PROMPT.format(
        context=context,
        finding_json=json.dumps(finding, ensure_ascii=False, indent=2)[:2000],
    )
    try:
        client = get_client()
        resp = client.messages.create(
            model=HAIKU_MODEL,
            max_tokens=500,
            messages=[{"role": "user", "content": prompt + hai_core.answer_language()}],
        )
        text = resp.content[0].text.strip()
        m = re.search(r"\{[\s\S]*\}", text)
        if not m:
            return None
        return json.loads(m.group(0))
    except Exception as e:
        log.warning(f"_haiku_decide error: {e}")
        return None


def _execute_hypothesis(finding, decision):
    import cbcr_hypothesis as cbcr
    observation_text = decision.get("summary") or json.dumps(finding, ensure_ascii=False)[:300]
    obs = {
        "trigger": "survivorship",
        "summary": observation_text[:400],
        "details": {"finding": finding},
    }
    try:
        is_dup, existing_id, reason = semcheck.check(observation_text[:400])
        if is_dup:
            log.info(f"  dedup skip: existing #{existing_id} — {reason[:60]}")
            return None
    except Exception as e:
        log.warning(f"semcheck failed: {e}")
    try:
        cbcr_dict = cbcr.generate_hypothesis_with_critique(obs)
        flat = cbcr.flatten_cbcr_payload(cbcr_dict)
    except Exception as e:
        log.warning(f"CBCR failed: {e}")
        return None
    if not flat.get("observation") or not flat.get("mechanism"):
        return None
    mid = hh.save_hypothesis(
        observation=flat["observation"],
        mechanism=flat["mechanism"],
        prediction=flat["prediction"],
        test=flat["test"],
        trigger="survivorship",
        resolution_type=flat.get("resolution_type") or "self_managed",
    )
    sc = cbcr_dict.get("structural_confidence") or {}
    prov = cbcr_dict.get("provenance") or {}
    try:
        db.save_cbcr_payload(
            memory_id=mid,
            payload_json=json.dumps(cbcr_dict, ensure_ascii=False),
            structural_score=int(sc.get("score") or 0),
            confidence_level=sc.get("confidence") or "unknown",
            generated_by="survivorship_curator",
            model=prov.get("model") or "haiku+sonnet",
        )
    except Exception as e:
        log.warning(f"save_cbcr_payload failed: {e}")
    if flat.get("resolution_type") == "needs_specialist":
        try:
            hh._notify_patient_view(mid, cbcr_dict, trigger_label="survivorship")
        except Exception as e:
            log.warning(f"notify failed: {e}")
    return mid


def _execute_proposal(finding, decision):
    proposed = [{
        "action": "survivorship_review_required",
        "problem_id": decision.get("linked_problem_id"),
        "summary": decision.get("summary"),
        "rationale": decision.get("rationale"),
        "source_finding": finding,
    }]
    try:
        return db.save_problem_proposal(source="survivorship_curator", changes=proposed)
    except Exception as e:
        log.warning(f"save_problem_proposal failed: {e}")
        return None


def _execute_task(finding, decision):
    deadline_days = decision.get("task_deadline_days") or 14
    task_type = decision.get("task_type") or "action"
    content = i18n.t("survivorship.task.content", summary=decision.get("summary", ""),
                     reason=decision.get("rationale", ""))
    fingerprint = f"survivorship:{finding.get('type','x')}:{(decision.get('summary') or '')[:30]}"
    try:
        return db.save_task(
            source="survivorship_curator",
            type_=task_type,
            content=content[:1000],
            priority="medium",
        )
    except Exception as e:
        log.warning(f"save_task failed: {e}")
        return None


def _execute_note(finding, decision):
    value = json.dumps({
        "finding": finding,
        "curator_summary": decision.get("summary"),
        "curator_rationale": decision.get("rationale"),
    }, ensure_ascii=False)
    try:
        return db.save_memory(
            category="survivorship_note",
            key=f"surv_{get_today()}_{finding.get('type', 'x')}",
            value=value,
            confidence=0.5,
            source="survivorship_curator",
        )
    except Exception as e:
        log.warning(f"save_memory(survivorship_note) failed: {e}")
        return None


# Действие constitution_conflict снято: короткие окна survivorship_analyzer
# не соответствуют длительному горизонту конституции и её ограничению на
# субъективные данные. Такие находки не должны копиться как конфликты без читателя.
# Ответ модели constitution_conflict читается как note — сигнал остаётся
# для месячного консилиума (блок «Самоотчёт»).
_CONSTITUTION_HORIZON_NOTE = "constitution_conflict → note: находка короче горизонта конституции"


def _get_actionable_findings(window_days=21):
    cutoff = (get_today() - timedelta(days=window_days)).isoformat()
    out = []
    try:
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT findings FROM agent_reports "
                "WHERE agent_type='survivorship_analysis' AND date >= ? "
                "ORDER BY date DESC LIMIT 3",
                (cutoff,),
            ).fetchall()
        for r in rows:
            try:
                d = json.loads(r["findings"] or "{}")
            except Exception:  # silent-ok: broken JSON
                continue
            for inst_id, fs in (d.get("per_instrument") or {}).items():
                for f in fs:
                    if f.get("type") not in SKIP_FINDING_TYPES:
                        out.append(f)
            for f in d.get("global") or []:
                if f.get("type") not in SKIP_FINDING_TYPES:
                    out.append(f)
    except Exception as e:
        log.warning(f"_get_actionable_findings: {e}")
    return out


def run(max_per_run=DEFAULT_MAX_PER_RUN, dry_run=False):
    findings = _get_actionable_findings()
    if not findings:
        log.info("no actionable findings")
        return {"findings": 0, "actions": {}}
    findings = findings[:max_per_run]
    log.info(f"curating {len(findings)} findings")
    context = _build_short_context()
    counts = {"hypothesis": 0, "problem_proposal": 0, "task": 0, "note": 0, "skipped": 0}
    audit = []
    for f in findings:
        decision = _haiku_decide(f, context)
        if not decision:
            counts["skipped"] += 1
            audit.append({"finding_type": f.get("type"), "action": "skipped", "reason": "no decision"})
            continue
        action = decision.get("action", "note")
        if action == "constitution_conflict":   # старый ответ модели — см. _CONSTITUTION_HORIZON_NOTE
            action = "note"
        log.info(f"  {f.get('type', '?')}: action={action}")
        if dry_run:
            audit.append({"finding_type": f.get("type"), "action": action,
                          "summary": decision.get("summary"), "dry_run": True})
            counts[action] = counts.get(action, 0) + 1
            continue
        result_id = None
        if action == "hypothesis":
            result_id = _execute_hypothesis(f, decision)
        elif action == "problem_proposal":
            result_id = _execute_proposal(f, decision)
        elif action == "task":
            result_id = _execute_task(f, decision)
        elif action == "note":
            result_id = _execute_note(f, decision)
        else:
            counts["skipped"] += 1
            audit.append({"finding_type": f.get("type"), "action": "skipped",
                          "reason": f"unknown action {action}"})
            continue
        if result_id is None and action == "hypothesis":
            counts["skipped"] += 1
            audit.append({"finding_type": f.get("type"), "action": "hypothesis_skipped",
                          "reason": "dedup or generation failed"})
        else:
            counts[action] = counts.get(action, 0) + 1
            audit.append({"finding_type": f.get("type"), "action": action,
                          "result_id": result_id, "summary": decision.get("summary")})
    if not dry_run:
        try:
            db.save_agent_report(
                agent_type="survivorship_curator",
                agent_name="survivorship_curator",
                date_str=str(get_today()),
                has_findings=1 if any(c > 0 for c in counts.values()) else 0,
                data_queried=["survivorship_analysis"],
                pubmed_ids=[],
                peers_reviewed=[],
                changes_summary=", ".join(f"{k}={v}" for k, v in counts.items()),
                findings=json.dumps(audit, ensure_ascii=False),
                recommendations=None,
                raw_output=None,
                period_days=14,
            )
        except Exception as e:
            log.error(f"save_agent_report failed: {e}")
    return {"findings": len(findings), "actions": counts}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=DEFAULT_MAX_PER_RUN)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    res = run(max_per_run=args.max, dry_run=args.dry_run)
    print(json.dumps(res, ensure_ascii=False, indent=2))
