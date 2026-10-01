#!/usr/bin/env python3.11
"""literature_curator — решает что делать с каждым literature finding.

Один контракт: finding + текущий контекст системы → одна из четырёх точек
эскалации:
  - hypothesis (через cbcr_hypothesis + semantic dedup + save_hypothesis)
  - problem_proposal (через save_problem_proposal)
  - task (через save_task с fingerprint)
  - note (через save_memory category='literature_note')

Запускается после publication_reader. Расписание: тот же launchd
com.larry.health.literature-read (после reader в одном bash-скрипте).

CLI: python3.11 literature_curator.py [--max N]
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
from _time_inject import get_today  # единый источник времени (seam)
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

DECISION_PROMPT = """Ты — curator медицинской литературы. На вход тебе дан structured finding
от reader-агента и краткий контекст пациента. Реши, КУДА эскалировать эту находку.

Четыре варианта:
1. "hypothesis" — статья формулирует тестируемый механизм или риск для популяции пациента.
   Используй когда: есть конкретное предсказание + способ проверить + резонирует с твоей картиной.
2. "problem_proposal" — статья ставит под вопрос одну из АКТИВНЫХ проблем или протоколов
   пациента (например, новые рекомендации мониторинга, опровержение текущей практики).
3. "task" — статья требует конкретного однократного действия в окне времени
   (например, "сдать FE-1", "обсудить препарат с врачом").
4. "note" — полезная информация, действия не требует. Просто фиксация для будущих агентов.

КОНТЕКСТ ПАЦИЕНТА:
{context}

FINDING:
  PMID: {pmid}
  Title: {title} ({year}, {journal})
  Topic: {topic_id} / {domain}
  Claim: {claim}
  Population: {population}
  Evidence level: {evidence_level}
  Applicability to me: {applicability_to_me}
  Relevance assessment: {relevance_assessment}

summary и rationale ЧИТАЕТ САМ ЧЕЛОВЕК в Telegram, а не врач и не агент. Поэтому:
- обращайся к нему на «ты», не «пациент»;
- простыми словами, как объяснил бы знакомый врач; медицинский термин — только с пояснением в скобках;
- без английских слов и сокращений (HRV, readiness, CBT-I и т.п.) — пиши по-русски, что это;
- без внутренних номеров (#…, id проблем, гипотез, протоколов) — называй тему словами.

Ответь СТРОГО валидным JSON:
{{
  "action": "hypothesis" | "problem_proposal" | "task" | "note",
  "rationale": "1-2 предложения почему именно эта эскалация",
  "summary": "что эскалируем (1-2 предложения, конкретно)",
  "linked_problem_id": str|null,        // для problem_proposal — problem_id ИЗ СПИСКА «Активные проблемы» (не номер гипотезы/протокола), иначе null
  "task_type": "lab_test"|"action"|"consult"|null,  // для task
  "task_deadline_days": int|null        // для task — через сколько дней дедлайн
}}
"""


def _build_short_context() -> str:
    """Краткий контекст системы для decision-промпта."""
    lines = []
    try:
        profile = db.get_patient_profile() or {}
        med = {k: v for k, v in profile.items() if k.startswith("medical.")}
        for k, v in list(med.items())[:6]:
            lines.append(f"  {k}: {str(v)[:80]}")
    except Exception:  # silent-ok: broken row/JSON или нет данных — пропуск
        pass

    try:
        probs = [p for p in (db.get_problem_list() or []) if p.get("status") in ("active", "active_monitoring", "watchful_waiting")]
        if probs:
            lines.append("Активные проблемы:")
            for p in probs[:8]:
                lines.append(f"  problem_id={p.get('problem_id', p.get('id'))}: {p.get('title', '')[:80]}")
    except Exception:  # silent-ok: broken row/JSON или нет данных — пропуск
        pass

    try:
        protos = db.get_active_protocols() or []
        if protos:
            lines.append("Активные протоколы:")
            for pr in protos[:5]:
                lines.append(f"  - {pr.get('title', '')[:80]}")
    except Exception:  # silent-ok: broken row/JSON или нет данных — пропуск
        pass

    # Решение человека — терминальное состояние темы. Перед новым предложением
    # куратор читает решения и прежние предложения, чтобы не повторять тему.
    try:
        decs = db.active_surveillance_decisions() or []
        if decs:
            lines.append("РЕШЕНИЯ ВРАЧА ПО НАБЛЮДЕНИЮ (действуют — не предлагай повторно):")
            lines.extend(db.format_surveillance_decisions(decs))
    except Exception as e:  # noqa: BLE001 — контекст деградирует, прогон не падает
        log.warning(f"surveillance_decisions недоступны: {e}")
    try:
        prior = prior_proposal_topics(days=PRIOR_PROPOSAL_WINDOW_DAYS)
        if prior:
            lines.append(f"УЖЕ ПРЕДЛАГАЛОСЬ за {PRIOR_PROPOSAL_WINDOW_DAYS} дн. (статус, причина отказа):")
            for pr in prior[:12]:
                lines.append(f"  [{pr['status']}] {pr['observation'][:100]}"
                             + (f" — отказ: {pr['note'][:80]}" if pr.get("note") else ""))
    except Exception as e:  # noqa: BLE001
        log.warning(f"prior proposals недоступны: {e}")

    try:
        hyps = hh.get_open_hypotheses(n=8) or []
        if hyps:
            lines.append("Открытые гипотезы:")
            for h in hyps[:5]:
                lines.append(f"  - {(h.get('observation') or '')[:80]}")
    except Exception:  # silent-ok: broken row/JSON или нет данных — пропуск
        pass

    return "\n".join(lines) if lines else "(контекст недоступен)"


def _haiku_decide(finding: dict, context: str) -> dict | None:
    prompt = DECISION_PROMPT.format(
        context=context,
        pmid=finding.get("pmid", "?"),
        title=finding.get("title", "")[:200],
        year=finding.get("year", ""),
        journal=finding.get("journal", "")[:80],
        topic_id=finding.get("topic_id", "?"),
        domain=finding.get("domain", "?"),
        claim=finding.get("claim", ""),
        population=finding.get("population", ""),
        evidence_level=finding.get("evidence_level", ""),
        applicability_to_me=finding.get("applicability_to_me", ""),
        relevance_assessment=finding.get("relevance_assessment", ""),
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
        log.warning(f"_haiku_decide error for PMID {finding.get('pmid')}: {e}")
        return None


def _execute_hypothesis(finding: dict, decision: dict) -> int | None:
    """Создать гипотезу через cbcr_hypothesis + semantic dedup."""
    import cbcr_hypothesis as cbcr

    observation_text = decision.get("summary") or finding.get("relevance_assessment") or ""
    obs = {
        "trigger": "literature",
        "summary": observation_text[:400],
        "details": {
            "pmid": finding.get("pmid"),
            "title": finding.get("title"),
            "year": finding.get("year"),
            "journal": finding.get("journal"),
            "claim": finding.get("claim"),
            "evidence_level": finding.get("evidence_level"),
            "applicability_to_me": finding.get("applicability_to_me"),
        },
    }

    # Semantic dedup
    try:
        is_dup, existing_id, reason = semcheck.check(observation_text[:400])
        if is_dup:
            log.info(f"  dedup skip: existing #{existing_id} — {reason[:60]}")
            return None
    except Exception as e:
        log.warning(f"semcheck failed: {e} (proceeding anyway)")

    # CBCR generation
    try:
        cbcr_dict = cbcr.generate_hypothesis_with_critique(obs)
        flat = cbcr.flatten_cbcr_payload(cbcr_dict)
    except Exception as e:
        log.warning(f"CBCR failed: {e}")
        return None
    if not flat.get("observation") or not flat.get("mechanism"):
        log.warning("CBCR returned empty fields")
        return None

    payload_with_pmid = dict(cbcr_dict)
    payload_with_pmid["linked_pmid"] = finding.get("pmid")

    mid = hh.save_hypothesis(
        observation=flat["observation"],
        mechanism=flat["mechanism"],
        prediction=flat["prediction"],
        test=flat["test"],
        trigger="literature",
        resolution_type=flat.get("resolution_type") or "self_managed",
    )

    sc = (cbcr_dict.get("structural_confidence") or {})
    prov = (cbcr_dict.get("provenance") or {})
    try:
        db.save_cbcr_payload(
            memory_id=mid,
            payload_json=json.dumps(payload_with_pmid, ensure_ascii=False),
            structural_score=int(sc.get("score") or 0),
            confidence_level=sc.get("confidence") or "unknown",
            generated_by="literature_curator",
            model=prov.get("model") or "haiku+sonnet",
        )
    except Exception as e:
        log.warning(f"save_cbcr_payload failed: {e}")

    # patient_view notification если applicable
    if flat.get("resolution_type") == "needs_specialist":
        try:
            hh._notify_patient_view(mid, cbcr_dict, trigger_label=f"literature PMID {finding.get('pmid')}")
        except Exception as e:
            log.warning(f"notify failed: {e}")

    return mid


PRIOR_PROPOSAL_WINDOW_DAYS = 180


def prior_proposal_topics(days: int = PRIOR_PROPOSAL_WINDOW_DAYS) -> list[dict]:
    """Прошлые предложения куратора (любой статус) за окно + действующие решения врача —
    в форме кандидатов для hypothesis_semantic_check._haiku_compare
    ({memory_id, status, observation}). Отказ владельца несёт причину (review_note)."""
    cutoff = (get_today() - timedelta(days=days)).isoformat()
    out = []
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, status, proposed, review_note FROM problem_list_proposals "
            "WHERE source='literature_curator' AND created_at >= ? ORDER BY id DESC", (cutoff,)).fetchall()
    for r in rows:
        try:
            changes = json.loads(r["proposed"] or "[]")
        except Exception:  # silent-ok: битый JSON одной строки не валит список
            continue
        summ = " ".join((ch.get("summary") or "") for ch in changes if isinstance(ch, dict))
        if summ.strip():
            out.append({"memory_id": r["id"], "status": r["status"], "observation": summ[:300],
                        "note": r["review_note"] or ""})
    try:
        for dc in db.active_surveillance_decisions() or []:
            out.append({"memory_id": -dc["id"], "status": f"решение врача:{dc['decision']}",
                        "observation": f"{dc['title']} — {dc['decided_by']} {dc['decided_on']}, "
                                       f"до {dc.get('valid_until') or 'бессрочно'}",
                        "note": dc.get("rationale") or ""})
    except Exception as e:  # noqa: BLE001
        log.warning(f"surveillance_decisions в дедупе недоступны: {e}")
    return out


def _duplicate_of_prior(decision: dict) -> tuple[bool, str]:
    """Семантический дедуп предложения против прошлых предложений и решений врача.
    Тот же судья, что у гипотез (_haiku_compare) — второго не заводим."""
    cand = (decision.get("summary") or "") + " " + (decision.get("rationale") or "")
    prior = prior_proposal_topics()
    if not prior:
        return False, "no prior"
    is_dup, existing_id, reason = semcheck._haiku_compare(cand, prior)
    return bool(is_dup), f"#{existing_id}: {reason}"


def _execute_proposal(finding: dict, decision: dict) -> int | None:
    # Дедуп по ТЕМЕ (не по PMID): четыре статьи об одном — одно предложение, не четыре.
    is_dup, why = _duplicate_of_prior(decision)
    if is_dup:
        log.info(f"  proposal dedup skip: {why[:100]}")
        decision["_dedup"] = why
        return None
    # Предложение без problem_id некому применить (apply_proposal его не знает) — это мысль,
    # а не правка; ей место в заметках, а не в очереди на approve.
    # Граница доверия: id пишет модель. 01.10 замер — номер гипотезы (203069) вместо
    # problem_id → карточка «этой проблемы нет». Несуществующий id = мысль без адресата.
    _pid = decision.get("linked_problem_id")
    if _pid not in (None, "", "null") and not any(
            str(p.get("problem_id")) == str(_pid) for p in (db.get_problem_list() or [])):
        log.info(f"  linked_problem_id={_pid!r} нет в списке проблем → заметка")
        _pid = None
    if _pid in (None, "", "null"):
        decision["_routed"] = "note"
        return _execute_note(finding, decision)
    proposed = [{
        "action": "literature_review_required",
        "problem_id": decision.get("linked_problem_id"),
        "summary": decision.get("summary"),
        "rationale": decision.get("rationale"),
        "source_pmid": finding.get("pmid"),
        "source_title": finding.get("title"),
    }]
    try:
        return db.save_problem_proposal(
            source="literature_curator",
            changes=proposed,
        )
    except Exception as e:
        log.warning(f"save_problem_proposal failed: {e}")
        return None


def _execute_task(finding: dict, decision: dict) -> int | None:
    deadline_days = decision.get("task_deadline_days") or 14
    deadline = (get_today() + timedelta(days=int(deadline_days))).isoformat()
    task_type = decision.get("task_type") or "action"
    pmid = finding.get("pmid", "")
    fingerprint = f"literature:PMID{pmid}:{(decision.get('summary') or '')[:30]}"

    content = i18n.t("literature.task.content", summary=decision.get("summary", ""),
                     pmid=pmid, title=finding.get("title", "")[:150], year=finding.get("year", ""),
                     reason=decision.get("rationale", ""))
    try:
        return db.save_task(
            source="literature_curator",
            type_=task_type,
            content=content[:1000],
            priority="medium",
        )
    except Exception as e:
        log.warning(f"save_task failed: {e}")
        return None


def _execute_note(finding: dict, decision: dict) -> int | None:
    value = json.dumps({
        "pmid": finding.get("pmid"),
        "title": finding.get("title"),
        "year": finding.get("year"),
        "journal": finding.get("journal"),
        "claim": finding.get("claim"),
        "applicability_to_me": finding.get("applicability_to_me"),
        "relevance_assessment": finding.get("relevance_assessment"),
        "curator_summary": decision.get("summary"),
        "curator_rationale": decision.get("rationale"),
    }, ensure_ascii=False)
    try:
        return db.save_memory(
            category="literature_note",
            key=f"lit_{finding.get('pmid')}_{get_today()}",
            value=value,
            confidence=0.5,
            source="literature_curator",
        )
    except Exception as e:
        log.warning(f"save_memory(literature_note) failed: {e}")
        return None


def _get_unprocessed_findings(window_days: int = 14) -> list[dict]:
    cutoff = (get_today() - timedelta(days=window_days)).isoformat()

    seen_pmids: set[str] = set()
    try:
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT pubmed_ids FROM agent_reports "
                "WHERE agent_type='literature_curator' AND date >= ?",
                (cutoff,)
            ).fetchall()
        for r in rows:
            try:
                pids = json.loads(r["pubmed_ids"] or "[]")
                if isinstance(pids, list):
                    seen_pmids.update(str(p) for p in pids)
            except Exception:  # silent-ok: broken row/JSON или нет данных — пропуск
                continue
    except Exception:  # silent-ok: broken row/JSON или нет данных — пропуск
        pass

    findings: list[dict] = []
    try:
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT findings FROM agent_reports "
                "WHERE agent_type='publication_reading' AND date >= ? "
                "ORDER BY date DESC",
                (cutoff,)
            ).fetchall()
        for r in rows:
            try:
                lst = json.loads(r["findings"] or "[]")
                if not isinstance(lst, list):
                    continue
                for f in lst:
                    pmid = str(f.get("pmid", ""))
                    if not pmid or pmid in seen_pmids:
                        continue
                    findings.append(f)
                    seen_pmids.add(pmid)
            except Exception:  # silent-ok: broken row/JSON или нет данных — пропуск
                continue
    except Exception:  # silent-ok: broken row/JSON или нет данных — пропуск
        pass

    return findings


def run(max_per_run: int = DEFAULT_MAX_PER_RUN) -> dict:
    findings = _get_unprocessed_findings()
    if not findings:
        log.info("no unprocessed findings")
        return {"findings": 0, "actions": {}}

    findings = findings[:max_per_run]
    log.info(f"curating {len(findings)} findings")

    context = _build_short_context()
    counts = {"hypothesis": 0, "problem_proposal": 0, "task": 0, "note": 0, "skipped": 0}
    audit: list[dict] = []
    processed_pmids: list[str] = []

    for f in findings:
        pmid = str(f.get("pmid", ""))
        processed_pmids.append(pmid)

        decision = _haiku_decide(f, context)
        if not decision:
            audit.append({"pmid": pmid, "action": "skipped", "reason": "decision parse failed"})
            counts["skipped"] += 1
            continue

        action = decision.get("action", "note")
        log.info(f"  PMID {pmid}: action={action} — {(decision.get('rationale') or '')[:80]}")

        result_id: int | None = None
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
            audit.append({"pmid": pmid, "action": "skipped", "reason": f"unknown action {action}"})
            continue

        if result_id is None and action == "hypothesis":
            counts["skipped"] += 1
            audit.append({"pmid": pmid, "action": "hypothesis_skipped", "reason": "dedup or generation failed"})
        elif action == "problem_proposal" and decision.get("_dedup"):
            counts["skipped"] += 1
            audit.append({"pmid": pmid, "action": "proposal_dedup_skipped", "reason": decision["_dedup"][:200]})
        elif action == "problem_proposal" and decision.get("_routed") == "note":
            counts["note"] = counts.get("note", 0) + 1
            audit.append({"pmid": pmid, "action": "note", "result_id": result_id,
                          "reason": "предложение без problem_id → заметка", "summary": decision.get("summary")})
        else:
            counts[action] = counts.get(action, 0) + 1
            audit.append({
                "pmid": pmid,
                "action": action,
                "result_id": result_id,
                "summary": decision.get("summary"),
            })

    try:
        db.save_agent_report(
            agent_type="literature_curator",
            agent_name="survivorship_literature",
            date_str=str(get_today()),
            has_findings=1 if any(c > 0 for c in counts.values()) else 0,
            data_queried=[],
            pubmed_ids=processed_pmids,
            peers_reviewed=[],
            changes_summary=f"Curated {len(findings)}: " + ", ".join(f"{k}={v}" for k, v in counts.items()),
            findings=json.dumps(audit, ensure_ascii=False),
            recommendations=None,
            raw_output=None,
            period_days=1,
        )
    except Exception as e:
        log.error(f"save_agent_report failed: {e}")

    return {"findings": len(findings), "actions": counts}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=DEFAULT_MAX_PER_RUN)
    args = ap.parse_args()
    res = run(max_per_run=args.max)
    print(json.dumps(res, ensure_ascii=False, indent=2))
