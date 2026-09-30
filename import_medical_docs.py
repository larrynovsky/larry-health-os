#!/usr/bin/env python3.11
"""
Импортирует клинические данные из прочитанных PDF в health.db:
- lab_results: анализы с датами
- problem_list: анамнез (диагнозы)
"""
import sys, json, logging
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import health_db as db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


# ─── Сидовые данные (BL-HC-1, 2026-06-18) ───────────────────────────────────
# Исторические клинические данные (lab_results / problem_list / consultations)
# вынесены из исходника: они в БД, а сырой seed — рядом с health.db (вне git,
# как сама БД/секреты). Скрипт остаётся документированным запасным путём ручного
# импорта (пути и условия — docs/reference/data_ingestion_paths.md):
# читает seed-файл и идемпотентно вставляет.
SEED_PATH = Path(db._HEALTH_DIR) / "seed_medical_docs.json"


def _load_seed():
    """Читает seed из SEED_PATH. Нет файла → пусто (no-op; данные уже в БД)."""
    if not SEED_PATH.exists():
        log.warning(f"seed не найден: {SEED_PATH} — нечего импортировать "
                    "(данные уже в БД; восстановить можно из git-истории файла).")
        return [], [], []
    raw = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    return raw.get("lab_data", []), raw.get("problems", []), raw.get("consultations", [])


LAB_DATA, PROBLEMS, CONSULTATIONS = _load_seed()


def save_consultations():
    """
    Сохраняет исторические консультации в медкарту (events + encounters).
    Идемпотентно — пропускает уже существующие по дате + specialist_type.
    """
    db.init_db()
    inserted = 0
    for c in CONSULTATIONS:
        if db.consultation_exists(c["date"], c["specialist_type"]):
            log.info(f"  Уже есть: {c['date']} {c['specialist_type']}")
            continue
        db.save_consultation(
            date_str=c["date"],
            specialist_type=c["specialist_type"],
            specialist_name=c.get("specialist_name"),
            platform=c.get("platform"),
            key_findings=c.get("key_findings"),
            source_file=c.get("source_file"),
        )
        inserted += 1
        log.info(f"  Добавлено: {c['date']} {c['specialist_type']} ({c['platform']})")
    log.info(f"Consultations: добавлено {inserted} записей")
    return inserted

def run():
    db.init_db()

    # ── Lab results ──────────────────────────────────────────────────────────
    log.info("Импортируем lab_results...")
    inserted_labs = 0
    skipped_labs = 0
    with db.get_conn() as conn:
        for row in LAB_DATA:
            date, source, test_name, value, unit, ref_low, ref_high, status, notes = row
            # Проверяем дубликат
            exists = conn.execute(
                "SELECT 1 FROM lab_results WHERE date=? AND test_name=? AND source=?",
                (date, test_name, source)
            ).fetchone()
            if exists:
                skipped_labs += 1
                continue
            conn.execute(
                """INSERT INTO lab_results
                   (date, source, test_name, value, unit, ref_low, ref_high, status, notes)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (date, source, test_name, value, unit, ref_low, ref_high, status, notes)
            )
            inserted_labs += 1
        conn.commit()
    log.info(f"Lab results: вставлено {inserted_labs}, пропущено дублей {skipped_labs}")

    # ── Problem list ─────────────────────────────────────────────────────────
    log.info("Импортируем problem_list...")
    import re as _re
    inserted_probs = 0
    with db.get_conn() as conn:
        existing_titles = {
            r[0] for r in conn.execute("SELECT title FROM problem_list").fetchall()
        }
    for p in PROBLEMS:
        if p["title"] in existing_titles:
            log.info(f"  Уже есть: {p['title'][:60]}")
            continue
        # Генерируем slug из первых слов заголовка
        slug = _re.sub(r'[^a-z0-9]+', '_', p["title"].lower().replace("ё","е"))[:50].strip("_")
        db.upsert_problem(
            problem_id=slug,
            title=p["title"],
            status=p["status"],
            notes=p["notes"],
        )
        inserted_probs += 1
        log.info(f"  Добавлено [{slug}]: {p['title'][:60]}")
    log.info(f"Problem list: добавлено {inserted_probs} записей")

    # ── Consultations ────────────────────────────────────────────────────────
    log.info("Импортируем consultations...")
    save_consultations()


if __name__ == "__main__":
    run()
