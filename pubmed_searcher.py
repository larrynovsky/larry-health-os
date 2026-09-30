#!/usr/bin/env python3.11
"""pubmed_searcher — еженедельный sweep PubMed по survivorship-темам.

Один cron-вход. Принцип: одна задача — поиск, никакой LLM-обработки.

Источник тем (diagnosis-hardcode, гибрид): floor из данных тенанта
(active_conditions → нейтральный класс-термин, КАЖДОМУ тенанту) ∪ enrichment
(per-tenant `survivorship_topics.yaml`, нюансные темы, опционально). Для каждой темы:
  - вызывает pubmed_client.search_pubmed(query, max_results=5, years_back=...)
  - дедуплицирует по PMID (фильтр через imported_docs или предыдущие отчёты)
  - сохраняет батч в agent_reports(agent_type='literature_search')

Расписание: launchd com.larry.health.literature-search (Вс 04:00).
Запуск вручную: python3.11 pubmed_searcher.py
"""
from __future__ import annotations

import json
import logging
import sys
import yaml
from datetime import date, timedelta
from _time_inject import get_today  # единый источник времени (seam)
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import health_db as db
import pubmed_client
import clinical_kb

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# diagnosis-hardcode B4: путь per-tenant (был Path.home()/health = ВСЕГДА владелец →
# процесс партнёра читал онко-темы владельца и слал их в NCBI под своим процессом,
# кросс-тенант утечка + PHI-egress, WSTG-ATHZ-04). Теперь = data-каталог СМОТРЯЩЕГО
# тенанта (db.DB_PATH.parent, изоляция по HEALTH_DATA_DIR). Партнёр без своего yaml → нет тем.
TOPICS_PATH = Path(db.DB_PATH).parent / "survivorship_topics.yaml"
MAX_PER_TOPIC = 5  # ограничение на тему за один проход


def _floor_topics() -> list[dict]:
    """Пол (floor): класс-уровневые темы из active_conditions СМОТРЯЩЕГО тенанта.
    diagnosis-hardcode: важность приходит из истории болезни тенанта, не из вшитого yaml.
    Каждый активный класс → нейтральный класс-термин (egress-safe по построению). Гарантирует
    минимум КАЖДОМУ тенанту: партнёр без yaml всё равно получает литературу по СВОИМ классам."""
    try:
        conds = clinical_kb.active_conditions()
    except Exception as e:  # незасеянная БД / нет env → нет пола, не падаем
        log.warning(f"floor: active_conditions failed: {e}")
        return []
    floor = []
    for cond in sorted(conds):
        term = pubmed_client.CONDITION_FLOOR_TERMS.get(cond)
        if not term:  # класс без литературного термина — пропуск (не навязываем)
            continue
        floor.append({
            "id": f"floor_{cond}",
            "name": f"{cond} (класс-уровень)",
            "pubmed_query": term,
            "domain": cond,
            "relevance_window_years": 3,
            "priority": "medium",
            "_source": "floor",
        })
    return floor


def _enrichment_topics() -> list[dict]:
    """Обогащение (enrichment): per-tenant нюансные темы из yaml, аддитивно к полу.
    Партнёр без yaml → пустой список (только пол). Сырьё в теме всё равно режет egress_safe в run()."""
    if not TOPICS_PATH.exists():
        log.info(f"enrichment: нет yaml ({TOPICS_PATH.name}) — только floor из данных")
        return []
    try:
        cfg = yaml.safe_load(TOPICS_PATH.read_text()) or {}
        topics = cfg.get("topics", []) or []
        for t in topics:
            if isinstance(t, dict):
                t.setdefault("_source", "enrichment")
        return topics
    except Exception as e:
        log.error(f"enrichment: yaml parse failed: {e}")
        return []


def _load_topics() -> list[dict]:
    """Пол (из данных тенанта) ∪ обогащение (per-tenant yaml), дедуп по id.
    Union покрытия, НЕ override между источниками → split-brain нет; сырьё в любой теме
    режет egress_safe в run() (defense-in-depth). enrichment поверх floor при коллизии id."""
    floor = _floor_topics()
    enrich = _enrichment_topics()
    by_id: dict[str, dict] = {}
    for t in floor + enrich:  # enrich позже floor → нюанс выигрывает у генерика при коллизии id
        tid = t.get("id") if isinstance(t, dict) else None
        if tid:
            by_id[tid] = t
    merged = list(by_id.values())
    log.info(f"topics: floor(данные)={len(floor)} ∪ enrichment(yaml)={len(enrich)} = {len(merged)} уникальных")
    return merged


def _previously_seen_pmids(window_days: int = 60) -> set[str]:
    """Собирает PMIDs из последних literature_search отчётов за window_days."""
    seen = set()
    try:
        cutoff = (get_today() - timedelta(days=window_days)).isoformat()
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT pubmed_ids FROM agent_reports "
                "WHERE agent_type='literature_search' AND date >= ?",
                (cutoff,)
            ).fetchall()
        for row in rows:
            pids_raw = row["pubmed_ids"] or "[]"
            try:
                pids = json.loads(pids_raw) if isinstance(pids_raw, str) else pids_raw
                if isinstance(pids, list):
                    seen.update(str(p) for p in pids)
            except Exception:  # silent-ok: broken row/JSON или нет данных — пропуск
                continue
    except Exception as e:
        log.warning(f"_previously_seen_pmids: {e}")
    return seen


def run() -> dict:
    """Один проход. Возвращает summary stats."""
    topics = _load_topics()
    if not topics:
        log.info("no topics to search")
        return {"topics": 0, "candidates": 0, "new_pmids": 0}

    seen = _previously_seen_pmids()
    log.info(f"loaded {len(topics)} topics, {len(seen)} PMIDs already seen in last 60d")

    all_candidates: list[dict] = []
    all_new_pmids: set[str] = set()
    attempted = failed = 0

    for t in topics:
        topic_id = t.get("id", "?")
        query = t.get("pubmed_query") or t.get("name") or ""
        years = int(t.get("relevance_window_years", 3))
        if not query:
            continue

        # diagnosis-hardcode B4/B5: egress-guard — сырая специфика (препарат/гистология/
        # номера/имя) НЕ уходит в NCBI. Тема с сырым запросом блокируется + логируется
        # (не тихий пропуск). Класс-уровень («cancer survivorship») проходит.
        if not pubmed_client.egress_safe(query):
            log.warning(f"  egress-guard заблокировал тему '{topic_id}' (сырая специфика в запросе) — пропуск")
            continue

        log.info(f"  searching: {topic_id}")
        attempted += 1
        try:
            results = pubmed_client.search_pubmed(query, max_results=MAX_PER_TOPIC, years_back=years,
                                                  strict=True) or []
        except Exception as e:
            failed += 1
            log.warning(f"  search failed for {topic_id}: {e}")
            continue

        new_for_topic = []
        for r in results:
            pmid = str(r.get("pmid", "")).strip()
            if not pmid or pmid in seen:
                continue
            new_for_topic.append({
                "pmid": pmid,
                "topic_id": topic_id,
                "title": r.get("title", "")[:300],
                "year": r.get("year", ""),
                "journal": r.get("journal", ""),
                "abstract": r.get("abstract", "")[:2000],
                "priority": t.get("priority", "medium"),
                "domain": t.get("domain", "?"),
            })
            all_new_pmids.add(pmid)

        log.info(f"    found {len(results)} total, {len(new_for_topic)} new")
        all_candidates.extend(new_for_topic)

    # Сохраняем один сводный отчёт за проход
    summary = {
        "topics_searched": len(topics),
        "candidates_total": len(all_candidates),
        "new_pmids_count": len(all_new_pmids),
        "topics_failed": failed,
    }
    if attempted and failed == attempted:
        # Источник не ответил ни на одну тему: отчёт «0 новых» был бы ложью о пустоте. Не пишем —
        # датчик свежести (check_literature_freshness / _partner) назовёт пропуск запуска.
        log.error(f"PubMed не ответил ни на одну из {attempted} тем — отчёт не сохранён")
        return summary
    try:
        db.save_agent_report(
            agent_type="literature_search",
            agent_name="survivorship_literature",
            date_str=str(get_today()),
            has_findings=1 if all_candidates else 0,
            data_queried=[t["id"] for t in topics if t.get("id")],
            pubmed_ids=list(all_new_pmids),
            peers_reviewed=[],
            changes_summary=f"Searched {len(topics)} topics, {len(all_new_pmids)} new PMIDs",
            findings=json.dumps(all_candidates, ensure_ascii=False),
            recommendations=None,
            raw_output=None,
            period_days=7,
        )
        log.info(f"saved literature_search report: {summary}")
    except Exception as e:
        log.error(f"save_agent_report failed: {e}")

    return summary


if __name__ == "__main__":
    res = run()
    print(json.dumps(res, ensure_ascii=False, indent=2))
