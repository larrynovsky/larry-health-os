#!/usr/bin/env python3.11
"""publication_reader — ежедневный читатель свежих publication candidates.

Один контракт: на вход PMID + abstract из последнего literature_search,
на выход — structured finding в agent_reports(agent_type='publication_reading').

Использует Haiku в два этапа:
  1. cheap-triage (бинарный yes/no): «релевантно ли профилю пациента (из данных
     ЗАПУЩЕННОГО тенанта, per-tenant — не вшитый диагноз)?». Если нет —
     dismissed_at_triage, finding сохраняется минимально, дальше не идёт.
  2. structured analysis: claim, population, evidence_level, relevance_assessment.

Расписание: launchd com.larry.health.literature-read (ежедневно 04:30).
Запуск вручную: python3.11 publication_reader.py [--max N]
"""
from __future__ import annotations
import llm_client
import hai_core

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

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# Модель берётся при ВЫЗОВЕ через hai_core.get_model — до 2026-10-01 здесь стояла
# константа из MODEL_DEFAULTS, и настройка/цепочка модели в БД этот модуль не достигала
# (нить llm-provider: шесть таких модулей). Роль: haiku.
DEFAULT_MAX_PER_RUN = 10


def _patient_context() -> str:
    """Per-tenant профиль пациента из БД (brief-neutralization A1). БЫЛО: захардкоженное
    досье владельца в промпте → уходило в LLM, нарушая СЗ («в промпте не зашито») и устаревая.
    СТАЛО: профиль ЗАПУЩЕННОГО тенанта из его данных через единый нейтральный билдер (диагноз
    рендерится только если он в данных тенанта). Тенант без состояний → нейтральный профиль →
    фильтр релевантности работает мягко (ничего лишнего не отсекает и не выдумывает)."""
    try:
        import patient_context
        return patient_context.build_patient_brief()
    except Exception as e:  # silent-ok: БД недоступна → нейтральный маркер, без досье
        log.warning(f"_patient_context: {e}")
        return "Профиль пациента временно недоступен."


TRIAGE_PROMPT_TEMPLATE = (
    "Ты — фильтр релевантности медицинских публикаций.\n"
    "{patient_context}\n\n"
    "Ниже — публикация. Реши, ОТНОСИТСЯ ли её содержание к состояниям, лечению, поздним "
    "эффектам, реабилитации или ведению, релевантным ПРОФИЛЮ ПАЦИЕНТА выше. Не относится: "
    "педиатрия, темы вне профиля пациента, доклинические исследования без клинических данных, "
    "repeat-обзоры старых.\n\n"
    "Публикация:\n"
    "  Заголовок: {title}\n"
    "  Журнал: {journal} ({year})\n"
    "  Абстракт: {abstract}\n\n"
    "Ответь СТРОГО валидным JSON: "
    '{{"relevant": bool, "reason": str (1-2 предложения)}}'
)

ANALYSIS_PROMPT_TEMPLATE = (
    "Ты — медицинский аналитик.\n"
    "{patient_context}\n\n"
    "Прочитай публикацию и верни структурированный finding.\n\n"
    "Публикация:\n"
    "  PMID: {pmid}\n"
    "  Заголовок: {title}\n"
    "  Журнал: {journal} ({year})\n"
    "  Абстракт: {abstract}\n\n"
    "Тема searcher отметил как: {topic_id} (домен {domain}, priority {priority}).\n\n"
    "Ответь СТРОГО валидным JSON со следующими полями:\n"
    '  "claim": главный вывод статьи (1-2 предложения),\n'
    '  "population": какая популяция изучена (1 предложение),\n'
    '  "evidence_level": "guideline" | "systematic_review" | "meta_analysis" | "rct" | "cohort" | "case_series" | "narrative" | "other",\n'
    '  "applicability_to_me": "direct" | "partial" | "extrapolation" | "irrelevant",\n'
    '  "relevance_assessment": в чём именно эта статья значима для пациента (профиль выше) (2-3 предложения, конкретно).'
)


def _haiku_call(prompt: str, max_tokens: int = 400) -> str:
    client = get_client()
    resp = client.messages.create(task="publication_reader._haiku_call",
        model=hai_core.get_model("haiku"),
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return llm_client.answer_text(resp).strip()


def _parse_json(text: str) -> dict | None:
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def _get_pending_candidates(window_days: int = 14) -> list[dict]:
    """Берёт candidates из последних literature_search отчётов за window_days,
    исключая уже прочитанные (есть запись в publication_reading)."""
    cutoff = (get_today() - timedelta(days=window_days)).isoformat()

    seen_pmids: set[str] = set()
    try:
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT pubmed_ids FROM agent_reports "
                "WHERE agent_type='publication_reading' AND date >= ?",
                (cutoff,)
            ).fetchall()
        for r in rows:
            try:
                pids = json.loads(r["pubmed_ids"] or "[]")
                if isinstance(pids, list):
                    seen_pmids.update(str(p) for p in pids)
            except Exception:  # silent-ok: broken row/JSON или нет данных — пропуск
                continue
    except Exception as e:
        log.warning(f"seen_pmids fetch: {e}")

    candidates: list[dict] = []
    try:
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT findings FROM agent_reports "
                "WHERE agent_type='literature_search' AND date >= ? "
                "ORDER BY date DESC",
                (cutoff,)
            ).fetchall()
        for r in rows:
            try:
                lst = json.loads(r["findings"] or "[]")
                if not isinstance(lst, list):
                    continue
                for c in lst:
                    pmid = str(c.get("pmid", ""))
                    if not pmid or pmid in seen_pmids:
                        continue
                    candidates.append(c)
                    seen_pmids.add(pmid)
            except Exception:  # silent-ok: broken row/JSON или нет данных — пропуск
                continue
    except Exception as e:
        log.warning(f"candidates fetch: {e}")

    return candidates


def _triage(candidate: dict, patient_ctx: str | None = None) -> tuple[bool, str]:
    """Cheap-triage. Возвращает (relevant, reason). patient_ctx — per-tenant профиль
    (из БД тенанта); None → вычисляется на месте (для прямых вызовов/тестов)."""
    if patient_ctx is None:
        patient_ctx = _patient_context()
    prompt = TRIAGE_PROMPT_TEMPLATE.format(
        patient_context=patient_ctx,
        title=candidate.get("title", "")[:300],
        journal=candidate.get("journal", "")[:100],
        year=candidate.get("year", ""),
        abstract=(candidate.get("abstract") or "")[:1500],
    )
    try:
        text = _haiku_call(prompt, max_tokens=200)
        parsed = _parse_json(text)
        if not parsed:
            return (False, f"triage parse failed: {text[:80]}")
        return (bool(parsed.get("relevant")), str(parsed.get("reason", ""))[:200])
    except Exception as e:
        return (False, f"triage error: {e}")


def _analyze(candidate: dict, patient_ctx: str | None = None) -> dict | None:
    """Structured analysis. Возвращает finding-dict или None. patient_ctx — per-tenant
    профиль (из БД тенанта); None → вычисляется на месте (для прямых вызовов/тестов)."""
    if patient_ctx is None:
        patient_ctx = _patient_context()
    prompt = ANALYSIS_PROMPT_TEMPLATE.format(
        patient_context=patient_ctx,
        pmid=candidate.get("pmid", ""),
        title=candidate.get("title", "")[:300],
        journal=candidate.get("journal", "")[:100],
        year=candidate.get("year", ""),
        abstract=(candidate.get("abstract") or "")[:2000],
        topic_id=candidate.get("topic_id", "?"),
        domain=candidate.get("domain", "?"),
        priority=candidate.get("priority", "medium"),
    )
    try:
        text = _haiku_call(prompt, max_tokens=800)
        parsed = _parse_json(text)
        if not parsed:
            return None
        return {
            "pmid": candidate.get("pmid"),
            "title": candidate.get("title", "")[:300],
            "year": candidate.get("year"),
            "journal": candidate.get("journal", "")[:100],
            "topic_id": candidate.get("topic_id"),
            "domain": candidate.get("domain"),
            "priority": candidate.get("priority"),
            "claim": parsed.get("claim", ""),
            "population": parsed.get("population", ""),
            "evidence_level": parsed.get("evidence_level", "other"),
            "applicability_to_me": parsed.get("applicability_to_me", "extrapolation"),
            "relevance_assessment": parsed.get("relevance_assessment", ""),
            "raw_abstract": (candidate.get("abstract") or "")[:2000],
        }
    except Exception as e:
        log.warning(f"_analyze error: {e}")
        return None


def run(max_per_run: int = DEFAULT_MAX_PER_RUN) -> dict:
    candidates = _get_pending_candidates()
    if not candidates:
        log.info("no pending candidates")
        return {"candidates": 0, "kept": 0, "dismissed": 0}

    candidates = candidates[:max_per_run]
    log.info(f"reading {len(candidates)} candidates (max_per_run={max_per_run})")

    findings: list[dict] = []
    dismissed: list[dict] = []
    pmids_processed: list[str] = []

    # Per-tenant профиль — ОДИН раз на прогон (из БД запущенного тенанта), не литерал.
    patient_ctx = _patient_context()

    for c in candidates:
        pmid = str(c.get("pmid", ""))
        pmids_processed.append(pmid)

        relevant, reason = _triage(c, patient_ctx)
        log.info(f"  PMID {pmid}: relevant={relevant} — {reason[:80]}")

        if not relevant:
            dismissed.append({
                "pmid": pmid,
                "title": c.get("title", "")[:200],
                "status": "dismissed_at_triage",
                "reason": reason,
            })
            continue

        finding = _analyze(c, patient_ctx)
        if finding:
            finding["status"] = "kept"
            finding["triage_reason"] = reason
            findings.append(finding)
        else:
            dismissed.append({
                "pmid": pmid,
                "title": c.get("title", "")[:200],
                "status": "dismissed_at_analysis_parse_fail",
                "reason": "could not parse analyzer output",
            })

    # Один сводный agent_report
    try:
        db.save_agent_report(
            agent_type="publication_reading",
            agent_name="survivorship_literature",
            date_str=str(get_today()),
            has_findings=1 if findings else 0,
            data_queried=[],
            pubmed_ids=pmids_processed,
            peers_reviewed=[c.get("pmid") for c in candidates],
            changes_summary=f"Read {len(candidates)}: kept {len(findings)}, dismissed {len(dismissed)}",
            findings=json.dumps(findings, ensure_ascii=False),
            recommendations=json.dumps(dismissed, ensure_ascii=False),
            raw_output=None,
            period_days=1,
        )
    except Exception as e:
        log.error(f"save_agent_report failed: {e}")

    return {
        "candidates": len(candidates),
        "kept": len(findings),
        "dismissed": len(dismissed),
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=DEFAULT_MAX_PER_RUN)
    args = ap.parse_args()
    res = run(max_per_run=args.max)
    print(json.dumps(res, ensure_ascii=False, indent=2))
