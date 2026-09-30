"""genome_db.py — доменный модуль genome. Вынесен из health_db.py (Поток C, strangler-фасад)."""
from __future__ import annotations

import json
import os
import logging
from datetime import date, timedelta
from pathlib import Path

from _time_inject import get_utcnow

log = logging.getLogger(__name__)


def get_significant_variants(domain: str = None, limit: int = 300) -> list[dict]:
    """Возвращает клинически значимые варианты, опционально фильтруя по домену."""
    import json as _j
    exclude = ("Benign", "Likely benign", "not provided", "")
    placeholders = ",".join("?" * len(exclude))
    with _hdb.get_conn() as conn:
        rows = conn.execute(
            f"""SELECT * FROM genetic_variants
                WHERE significance NOT IN ({placeholders})
                  AND significance IS NOT NULL
                ORDER BY
                  CASE significance
                    WHEN 'Pathogenic' THEN 1
                    WHEN 'Likely pathogenic' THEN 2
                    WHEN 'Uncertain significance' THEN 3
                    ELSE 4
                  END
                LIMIT ?""",
            (*exclude, limit)
        ).fetchall()
    variants = [dict(r) for r in rows]
    if domain:
        variants = [
            v for v in variants
            if domain in _j.loads(v.get("domain_tags") or "[]")
        ]
    return variants


def get_carrier_variants(limit: int = 200) -> list[dict]:
    """Верифицированные НОСИТЕЛИ: effect_allele_status='resolved' и effect_allele
    реально присутствует в генотипе (hetero/homo). Сортировка по патогенности.

    genome_context показывает их в приоритете (Ф5), чтобы реальный сигнал не
    тонул в тысячах непроверяемых меток ClinVar.
    """
    with _hdb.get_conn() as conn:
        rows = conn.execute(
            f"""SELECT * FROM genetic_variants
                WHERE effect_allele_status='resolved'
                  AND effect_allele IS NOT NULL AND effect_allele <> ''
                  AND instr(genotype, effect_allele) > 0
                  AND significance IS NOT NULL
                ORDER BY {_hdb._PATHO_RANK_SQL}, rsid
                LIMIT ?""",
            (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def carrier_status_null_allele_violations(conn) -> list[dict]:
    """Строки, нарушающие сцепление: carrier-статус ⟹ effect_allele заполнен.

    Читатели ниже по потоку (generate_constitutions, constitution_analysis) МОЛЧА
    опираются на то, что 'resolved'/'palindromic_het_resolved' всегда несут непустой
    аллель. Если это сломается (напр. write-путь родил carrier-статус с NULL), вариант
    рискует тихо стать «не носитель» = ложная чистота (инвариант null_is_unknown_not_clean).
    Пусто = сцепление держится. Это часовой (RST check), не доказательство правоты.
    Принимает conn (тестируемо на любой БД).
    """
    rows = conn.execute(
        "SELECT rsid, effect_allele_status FROM genetic_variants "
        "WHERE effect_allele_status IN ('resolved','palindromic_het_resolved') "
        "AND (effect_allele IS NULL OR effect_allele='')"
    ).fetchall()
    return [dict(r) for r in rows]


def get_variants_by_genes(genes: list, limit: int = 100) -> list[dict]:
    """Возвращает варианты из genetic_variants по списку генов.
    Не ограничен глобальным лимитом — запрашивает целевые гены напрямую.
    Сортирует: Pathogenic > Likely pathogenic > risk factor/Drug > остальные значимые.
    Исключает Benign/Likely benign/not provided."""
    if not genes:
        return []
    exclude = ("Benign", "Likely benign", "not provided", "")
    placeholders_e = ",".join("?" * len(exclude))
    placeholders_g = ",".join("?" * len(genes))
    with _hdb.get_conn() as conn:
        rows = conn.execute(
            f"""SELECT * FROM genetic_variants
                WHERE gene IN ({placeholders_g})
                  AND significance NOT IN ({placeholders_e})
                  AND significance IS NOT NULL
                ORDER BY
                  CASE significance
                    WHEN 'Pathogenic' THEN 1
                    WHEN 'Likely pathogenic' THEN 2
                    WHEN 'risk factor' THEN 3
                    WHEN 'drug response' THEN 3
                    ELSE 4
                  END
                LIMIT ?""",
            (*genes, *exclude, limit)
        ).fetchall()
    return [dict(r) for r in rows]


def genome_summary_counts() -> dict:
    """Сводные счётчики для genome_context (Ф5). 'patho' = Pathogenic/Likely
    (LIKE %athogenic%, без Conflicting). carrier = resolved И аллель в генотипе."""
    with _hdb.get_conn() as conn:
        row = conn.execute(
            """SELECT
                 SUM(CASE WHEN ea_known AND carrier AND patho THEN 1 ELSE 0 END) AS patho_carrier,
                 SUM(CASE WHEN ea_known AND NOT carrier AND patho THEN 1 ELSE 0 END) AS patho_wildtype,
                 SUM(CASE WHEN (NOT ea_known) AND patho THEN 1 ELSE 0 END) AS patho_unverified
               FROM (
                 SELECT
                   (effect_allele_status='resolved' AND effect_allele IS NOT NULL
                      AND effect_allele <> '') AS ea_known,
                   (effect_allele IS NOT NULL AND effect_allele <> ''
                      AND instr(genotype, effect_allele) > 0) AS carrier,
                   (significance LIKE '%athogenic%' AND significance NOT LIKE '%Conflict%') AS patho
                 FROM genetic_variants WHERE significance IS NOT NULL
               )"""
        ).fetchone()
    return {
        "patho_carrier":    row["patho_carrier"] or 0,
        "patho_wildtype":   row["patho_wildtype"] or 0,
        "patho_unverified": row["patho_unverified"] or 0,
    }


def get_snps_batch(rsids: list[str]) -> dict[str, str]:
    """Возвращает {rsid: genotype} для списка rsID."""
    if not rsids:
        return {}
    placeholders = ",".join("?" * len(rsids))
    with _hdb.get_conn() as conn:
        rows = conn.execute(
            f"SELECT rsid, genotype FROM raw_snps WHERE rsid IN ({placeholders})",
            rsids
        ).fetchall()
    return {r["rsid"]: r["genotype"] for r in rows}


def upsert_genetic_variant(rsid: str, data: dict):
    """Сохраняет или обновляет аннотированный вариант."""
    import json as _j
    from datetime import datetime
    now = get_utcnow().isoformat()
    with _hdb.get_conn() as conn:
        existing = conn.execute(
            "SELECT significance FROM genetic_variants WHERE rsid=?", (rsid,)
        ).fetchone()
        prev_sig = existing["significance"] if existing else None
        conn.execute("""
            INSERT INTO genetic_variants
                (rsid, gene, genotype, significance, prev_significance,
                 conditions, domain_tags, effect_allele, clinical_summary,
                 annotation_source, annotated_at, updated_at,
                 ref_allele, assembly, effect_allele_status)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(rsid) DO UPDATE SET
                gene=excluded.gene,
                genotype=excluded.genotype,
                prev_significance=genetic_variants.significance,
                significance=excluded.significance,
                conditions=excluded.conditions,
                domain_tags=excluded.domain_tags,
                clinical_summary=excluded.clinical_summary,
                annotation_source=excluded.annotation_source,
                updated_at=excluded.updated_at,
                -- effect_allele и его метаданные пишет ТОЛЬКО strand-aware
                -- путь (backfill/аннотатор). Ре-аннотация значимости (например
                -- genome_update_agent) их не передаёт → COALESCE сохраняет
                -- ранее вычисленные значения, не затирая в NULL (genome strand-fix Ф2).
                effect_allele=COALESCE(excluded.effect_allele, genetic_variants.effect_allele),
                ref_allele=COALESCE(excluded.ref_allele, genetic_variants.ref_allele),
                assembly=COALESCE(excluded.assembly, genetic_variants.assembly),
                effect_allele_status=COALESCE(excluded.effect_allele_status, genetic_variants.effect_allele_status)
        """, (
            rsid,
            data.get("gene"),
            data.get("genotype"),
            data.get("significance"),
            prev_sig,
            _j.dumps(data.get("conditions", []), ensure_ascii=False),
            _j.dumps(data.get("domain_tags", []), ensure_ascii=False),
            data.get("effect_allele"),
            data.get("clinical_summary"),
            data.get("annotation_source", "myvariant"),
            now, now,
            data.get("ref_allele"),
            data.get("assembly"),
            data.get("effect_allele_status"),
        ))


def save_genome_update_log(data: dict) -> int:
    """Сохраняет лог месячного обновления генома."""
    import json as _j
    with _hdb.get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO genome_update_log
                (run_date, variants_checked, variants_changed, changes_json, narrative, sent_to_user)
            VALUES (?,?,?,?,?,0)
        """, (
            data["run_date"],
            data.get("variants_checked", 0),
            data.get("variants_changed", 0),
            _j.dumps(data.get("changes", []), ensure_ascii=False),
            data.get("narrative")
        ))
        return cur.lastrowid


def get_raw_snp(rsid: str) -> dict | None:
    """Возвращает генотип из raw_snps по rsID."""
    with _hdb.get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM raw_snps WHERE rsid=?", (rsid,)
        ).fetchone()
        return dict(row) if row else None


def mark_genome_log_sent(log_id: int):
    with _hdb.get_conn() as conn:
        conn.execute(
            "UPDATE genome_update_log SET sent_to_user=1 WHERE id=?", (log_id,)
        )


# health_db — В КОНЦЕ модуля (BL-TEST-COLLECT-ALONE-1, 2026-09-24): он ре-экспортирует функции
# этого модуля, и импорт наверху давал цикл, если модуль импортировали первым (28 из 29 доменных
# модулей). Имя _hdb нужно только внутри функций — к их вызову health_db уже загружен.
import health_db as _hdb  # noqa: E402
