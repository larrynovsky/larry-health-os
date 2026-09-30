"""Схема чистой установки ⊇ схемы канона — оракул онбординга (нить onboarding, 2026-09-23).

Замер: init_db на пустом каталоге давал на 38 колонок daily_metrics меньше канона (миграции
ALTER-или таблицу до её создания и глотали ошибку), таблиц protocols/assessment_sessions не
было вовсе — у них не было дома в коде, только разовые миграции. Новый установщик получил бы
бота, падающего на «no such column».

Сверка идёт там, где есть канон (песочница test_on_studio — снимок живой БД); без канона —
skip. Таблицы, которые создают их модули лениво при первом обращении, названы поимённо вместе
с создателем: неявное стало явным, а новая «сирота» — красной.
"""
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

# таблица/колонка → модуль, который создаёт её сам при первом обращении
LAZY = {
    "conversation_history": "hai_core", "dashboard_edits": "dashboard_db",
    "genome_source": "genome_parser", "hypothesis_outcomes": "health_db (гипотезы)",
    "lab_name_loinc": "loinc_match", "lab_results": "health_db._ensure_lab_table",
    "memory": "health_db (память)", "memory_facts": "health_db (память)",
    "memory_consolidation_proposals": "memory_consolidation", "prs_scores": "prs_pipeline",
    "recognized_docs": "lab_backfill", "service_trouble": "service_trouble",
    "trait_phenotypes": "traits_pipeline", "wellness_phenotypes": "wellness_pipeline",
    "vcf_discovery": "vcf_import_pipeline", "vcf_import_log": "vcf_import_pipeline",
    "vcf_staging": "vcf_import_pipeline",
    "genome_imports.phase_h_prs_at": "prs_pipeline", "raw_snps.source": "vcf_import_pipeline",
}


def _columns(db: Path) -> set[str]:
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return {f"{t}.{col}" for (t,) in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
            for (col,) in c.execute(f"SELECT name FROM pragma_table_info('{t}')")}
    finally:
        c.close()


def _canon() -> Path | None:
    import health_db
    db = Path(health_db.DB_PATH)
    if not db.exists():
        return None
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        n = c.execute("SELECT COUNT(*) FROM daily_metrics").fetchone()[0]
    except sqlite3.Error:
        return None
    finally:
        c.close()
    return db if n > 365 else None    # живой канон — годы дней; тестовая БД — пустая


def test_свежая_установка_содержит_всю_схему_канона(tmp_path):
    canon = _canon()
    if canon is None:
        pytest.skip("канона нет (не песочница Studio) — сверять не с чем")
    env = {**os.environ, "HEALTH_DATA_DIR": str(tmp_path), "ALLOW_WRITE_NONPRIMARY": "1"}
    subprocess.run([sys.executable, "-c", "import health_db; health_db.init_db()"],
                   cwd=ROOT, env=env, check=True, capture_output=True, timeout=300)
    fresh = _columns(tmp_path / "data" / "health.db")
    missing = {x for x in _columns(canon) - fresh
               if x not in LAZY and x.split(".")[0] not in LAZY}
    assert not missing, (f"у чистой установки нет {len(missing)} колонок канона — дом в коде "
                         f"(init_db) или строка в LAZY с создателем: {sorted(missing)[:30]}")
