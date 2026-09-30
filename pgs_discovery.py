"""pgs_discovery.py — Discover and auto-import new PGS Catalog scoring files.

Public interface (one module = one function rule):
    discover_and_import_new_scores(conn=None) -> dict

Scans ALL available scores in PGS Catalog REST API, filters by quality
(GRCh38 harmonized available + num_variants >= 50), auto-imports new ones,
runs Phase H once after all imports, returns summary.

Quality filter rationale:
  - GRCh38 harmonized: required for coordinate-based matching with genome_snps
  - num_variants >= 50: eliminates toy/research models with near-zero coverage
  - Phase H coverage filter (post-import) is the second quality gate

Block D2 (2026-06-27): ответ на вопрос «есть ли PGS модели, которые меня касаются
и которые мы ещё не скачали». Не ограничиваем EFO — Phase H coverage сам отфильтрует
нерелевантные (снпов нет → coverage ~0 → score отображается со статусом low).

Safety guards (2026-06-27, инцидент DB corruption):
  MAX_IMPORT_PER_RUN — не больше N новых моделей за один прогон (R1).
                        Предотвращает рост pgs_weights в сотни GB за одну ночь.
  DB_SIZE_LIMIT_MB   — аварийное прерывание если БД уже больше N MB (R2).
                        Триггер до начала импортов: лучше шумно остановиться,
                        чем тихо испортить данные.

Usage (Studio, full scan в несколько прогонов):
    python3.11 pgs_discovery.py          # импортирует до 100 новых моделей
    python3.11 pgs_discovery.py          # следующий прогон — ещё 100
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from typing import Optional

import requests

import genome_weights as gw
import prs_pipeline as pp

log = logging.getLogger(__name__)

PGS_API_SCORE_LIST = "https://www.pgscatalog.org/rest/score/all/"
MIN_VARIANTS       = 50
LIST_TIMEOUT       = 30   # per pagination request
MAX_IMPORT_PER_RUN = 100  # R1 guard: не больше N новых моделей за один прогон
SNAP_RETENTION     = 5    # R2 retention: держим последние N pre-op снапшотов
                          # reference-БД (регенерируема). Иначе навес ~1.5/день,
                          # 2.4GB за 17 дней (инцидент 2026-07-19).
DB_SIZE_LIMIT_MB   = 20000  # R2 guard: reference-БД весов (2026-07-02). 104 шкалы ≈ 2.4GB;
                            # 20GB = ~8x headroom, ловит runaway в «сотни GB». Мерится по
                            # pgs_reference.ref_db_path(), НЕ по health.db (веса вынесены).


# ── Public entry point ────────────────────────────────────────────────────────

def discover_and_import_new_scores(
    conn: Optional[sqlite3.Connection] = None,
) -> dict:
    """Scan PGS Catalog, import new scoring files, run Phase H.

    Returns:
        {
          imported:         list[{pgs_id, trait, n_inserted}],
          skipped_existing: int,
          skipped_quality:  int,
          errors:           list[{pgs_id, error}],
          capped:           bool,   # True если достигнут MAX_IMPORT_PER_RUN
          phase_h_run:      bool,
          phase_h_results:  dict | None,
        }
    """
    _conn, _owned = _get_conn(conn)
    # Каталог/веса вынесены в общую reference-БД (2026-07-02). Канон (_conn) —
    # только raw_snps/prs_scores/genome_imports. Discovery читает/пишет каталог в _ref.
    import pgs_reference
    _ref = pgs_reference.get_ref_conn()

    # ── R2 guard: размер REFERENCE-БД весов до начала любых импортов ──────────
    try:
        db_size_mb = os.path.getsize(str(pgs_reference.ref_db_path())) / 1024 / 1024
        if db_size_mb > DB_SIZE_LIMIT_MB:
            msg = (
                f"discover: DB size {db_size_mb:.0f}MB > limit {DB_SIZE_LIMIT_MB}MB — "
                f"импорт прерван. Сожми или проверь pgs_weights перед следующим прогоном."
            )
            log.error(msg)
            _ref.close()
            if _owned:
                _conn.close()
            return {
                "imported": [], "skipped_existing": 0, "skipped_quality": 0,
                "errors": [{"pgs_id": "R2_guard", "error": msg}],
                "capped": False, "phase_h_run": False, "phase_h_results": None,
            }
        log.info("discover: DB size %.1f MB (limit %d MB) — ok", db_size_mb, DB_SIZE_LIMIT_MB)
    except Exception as exc:
        log.warning("discover: DB size check failed: %s — continuing", exc)

    # ── Step 1: что уже есть ─────────────────────────────────────────────────
    known_ids: set[str] = set()
    try:
        rows = _ref.execute("SELECT pgs_id FROM pgs_catalog").fetchall()
        known_ids = {r[0] for r in rows}
        log.info("discover: %d already in pgs_catalog", len(known_ids))
    except Exception as exc:  # silent-ok: таблица ещё не инициализирована
        log.warning("discover: pgs_catalog read failed: %s", exc)

    # ── Step 2: скачиваем листинг из PGS Catalog (пагинация) ─────────────────
    all_scores: list[dict] = []
    url: Optional[str] = PGS_API_SCORE_LIST
    params: dict = {"limit": 100}

    while url:
        try:
            resp = requests.get(url, params=params, timeout=LIST_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            log.error("discover: PGS Catalog listing failed at %s: %s", url, exc)
            break
        all_scores.extend(data.get("results", []))
        url = data.get("next")   # None когда последняя страница
        params = {}              # next URL уже содержит все query params

    log.info("discover: fetched %d scores from PGS Catalog", len(all_scores))

    # ── Step 3: фильтруем кандидатов ─────────────────────────────────────────
    skipped_existing = 0
    skipped_quality  = 0
    candidates: list[dict] = []

    for s in all_scores:
        pid = s.get("id", "")
        if pid in known_ids:
            skipped_existing += 1
            continue
        has_grch38 = bool((s.get("ftp_harmonized_scoring_files") or {}).get("GRCh38"))
        nv = s.get("variants_number") or 0
        if not has_grch38 or nv < MIN_VARIANTS:
            skipped_quality += 1
            continue
        candidates.append({
            "pgs_id": pid,
            "trait":  s.get("trait_reported", pid),
        })

    log.info(
        "discover: %d candidates to import (%d existing, %d quality-filtered)",
        len(candidates), skipped_existing, skipped_quality,
    )

    # ── Pre-op snapshot (backup policy R2) ────────────────────────────────────
    snapshot_error: Optional[str] = None
    if candidates:
        try:
            import health_db as _hdb_snap
            backup_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(str(_hdb_snap.DB_PATH)))),
                "backups",
            )
            os.makedirs(backup_dir, exist_ok=True)
            import time as _time_snap
            ts = _time_snap.strftime("%Y%m%d_%H%M")
            # снапшот REFERENCE-БД (её модифицируют импорты), не канона
            snap_path = os.path.join(backup_dir, f"pgs_reference.before_discovery_{ts}.db")
            snap_conn = sqlite3.connect(snap_path)
            # SEC db_perms канон: снапшот .db строго 600. sqlite3.connect создаёт
            # файл под umask (обычно → 644) → сенсор security:db_perms тлеет.
            # Сужаем сразу после создания, до записи данных.
            os.chmod(snap_path, 0o600)
            _ref.backup(snap_conn)
            snap_conn.close()
            log.info("discover: pre-op snapshot → %s", os.path.basename(snap_path))
            # R2 retention: оставляем последние SNAP_RETENTION снапшотов,
            # старые удаляем. Имя содержит _YYYYMMDD_HHMM → лексикосорт = хроно.
            try:
                import glob as _glob
                snaps = sorted(_glob.glob(os.path.join(
                    backup_dir, "pgs_reference.before_discovery_*.db")))
                for _old in snaps[:-SNAP_RETENTION]:
                    os.remove(_old)
                    log.info("discover: retention — удалён %s",
                             os.path.basename(_old))
            except Exception as _exc:  # чистка не критична для импорта
                log.warning("discover: retention prune failed: %s", _exc)
        except Exception as exc:
            snapshot_error = str(exc)
            log.warning("discover: pre-op snapshot failed: %s — продолжаем без снимка", exc)

    # ── Step 4: импортируем кандидатов (с R1 лимитом) ────────────────────────
    imported: list[dict] = []
    errors:   list[dict] = []
    capped = False

    for cand in candidates:
        # R1 guard: не больше MAX_IMPORT_PER_RUN новых моделей за прогон
        if len(imported) + len(errors) >= MAX_IMPORT_PER_RUN:
            capped = True
            log.info(
                "discover: R1 guard — MAX_IMPORT_PER_RUN=%d reached, "
                "останавливаемся. Следующий прогон продолжит с оставшихся %d.",
                MAX_IMPORT_PER_RUN, len(candidates) - MAX_IMPORT_PER_RUN,
            )
            break

        pid = cand["pgs_id"]
        try:
            result = gw.download_and_import(pid, trait_name=cand["trait"], conn=_ref)
            if result.get("status") == "ok":
                imported.append({
                    "pgs_id":     pid,
                    "trait":      cand["trait"],
                    "n_inserted": result.get("n_inserted", 0),
                })
                log.info("discover: imported %s (+%d SNPs)", pid, result.get("n_inserted", 0))
            else:
                errors.append({"pgs_id": pid, "error": result.get("status")})
                log.warning("discover: %s → status=%s", pid, result.get("status"))
        except Exception as exc:  # silent-ok: один плохой файл не роняет весь batch
            log.warning("discover: %s import failed: %s", pid, exc)
            errors.append({"pgs_id": pid, "error": str(exc)})

    # ── Step 5: Phase H — один раз для всех новых ────────────────────────────
    phase_h_run     = False
    phase_h_results = None

    # Пересчёт — если скачано новое ИЛИ у этого человека есть непосчитанные модели общего
    # справочника (их скачали по данным другого человека установки, 28.09).
    try:
        gid_row = _conn.execute("SELECT MAX(id) FROM genome_imports").fetchone()
        _gid = gid_row[0] if gid_row else None
        behind = pp.unscored_models(_conn, _gid) if _gid else []
    except Exception as exc:  # silent-ok: без генома или справочника — как раньше, по imported
        log.warning("discover: проверка непосчитанных моделей не выполнилась: %s", exc)
        behind = []
    if behind and not imported:
        log.info("discover: у этого генома не посчитано %d моделей справочника — Phase H", len(behind))

    if imported or behind:
        try:
            gid_row = _conn.execute("SELECT MAX(id) FROM genome_imports").fetchone()
            gid = gid_row[0] if gid_row else None
            if gid:
                phase_h_results = pp.run(_conn, gid)
                phase_h_run     = True
                log.info(
                    "discover: Phase H complete (%d scores computed)",
                    len((phase_h_results or {}).get("results", {})),
                )
            else:
                log.warning("discover: genome_imports пуст — Phase H пропущена")
        except Exception as exc:  # silent-ok: Phase H failure не откатывает импорт
            log.error("discover: Phase H failed: %s", exc, exc_info=True)

    _ref.close()
    if _owned:
        _conn.close()

    return {
        "imported":         imported,
        "skipped_existing": skipped_existing,
        "skipped_quality":  skipped_quality,
        "errors":           errors,
        "capped":           capped,
        "phase_h_run":      phase_h_run,
        "phase_h_results":  phase_h_results,
        "snapshot_error":   snapshot_error,  # None если снимок создан успешно
    }


# ── Internal helpers ──────────────────────────────────────────────────────────

def _get_conn(conn: Optional[sqlite3.Connection]):
    if conn is not None:
        return conn, False
    import health_db
    return health_db.get_conn(), True


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json as _json
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    import health_db
    c = health_db.get_conn()
    result = discover_and_import_new_scores(c)
    c.close()

    print(_json.dumps({
        "imported":         len(result["imported"]),
        "skipped_existing": result["skipped_existing"],
        "skipped_quality":  result["skipped_quality"],
        "errors":           len(result["errors"]),
        "capped":           result["capped"],
        "phase_h_run":      result["phase_h_run"],
    }, indent=2))

    if result["capped"]:
        print(f"\n⚠️  R1 guard: достигнут лимит MAX_IMPORT_PER_RUN={MAX_IMPORT_PER_RUN}.")
        print("   Запусти pgs_discovery.py ещё раз для продолжения.")

    if result["errors"]:
        print("\nErrors:")
        for e in result["errors"][:10]:
            print(f"  {e['pgs_id']}: {e['error']}")
