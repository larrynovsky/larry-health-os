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
  R2 (место)         — импорт не начинается, если на диске нет места на снимок
                        справочника + ожидаемый рост (pgs_reference.import_room).
                        Лучше шумно остановиться, чем тихо испортить данные.
  Один писатель      — импортирует только процесс, которому справочник доступен
                        на запись (pgs_reference.writable); остальные — Phase H.

Usage (Studio, full scan в несколько прогонов):
    python3.11 pgs_discovery.py          # импортирует до 100 новых моделей
    python3.11 pgs_discovery.py          # следующий прогон — ещё 100
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from pathlib import Path
from typing import Optional

import requests

import genome_weights as gw
import prs_pipeline as pp

log = logging.getLogger(__name__)

PGS_API_SCORE_LIST = "https://www.pgscatalog.org/rest/score/all/"
MIN_VARIANTS       = 50
LIST_TIMEOUT       = 30   # per pagination request
MAX_IMPORT_PER_RUN = 100  # R1 guard: не больше N новых моделей за один прогон
# R2 (02.11.2026): место под прогон вычисляет pgs_reference.import_room() — снимок +
# рост; прежний литерал DB_SIZE_LIMIT_MB=20000 снят. Снимок — в pgs_reference.
# snapshot_dir(), живёт только до quick_check после импорта (SNAP_RETENTION=5 снят).


# ── Public entry point ────────────────────────────────────────────────────────

def discover_and_import_new_scores(
    conn: Optional[sqlite3.Connection] = None,
    next_due_at: Optional[str] = None,
) -> dict:
    """Scan PGS Catalog, import new scoring files, run Phase H.

    next_due_at — UTC ISO, позже которого следующий прогон считается опоздавшим; его знает
    расписание вызывающего (jobs/scheduled.py), а не докачка (ритм — в квитанции, C-50).

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

    # ── Кто импортирует: один писатель на машину (02.11.2026) ─────────────────
    # Справочник общий. Импортирует только процесс, которому он доступен на запись;
    # остальные тенанты (контейнер владельца видит его :ro) делают лишь Phase H.
    # До 02.11 все три запуска качали по 100 файлов и снимали по копии 16 ГБ, а
    # контейнер ещё и падал на каждой записи «readonly database» (100 ошибок в канал).
    import_errors: list[dict] = []
    blocked: Optional[str] = None
    if not pgs_reference.writable():
        blocked = "read_only"
        log.info("discover: справочник только для чтения — импорт делает писатель машины, "
                 "здесь только Phase H")
    else:
        # R2: хватит ли места на снимок + рост (вычисляется, порога-литерала нет)
        room = pgs_reference.import_room(MAX_IMPORT_PER_RUN)
        if not room["ok"]:
            blocked = "no_room"
            msg = (f"discover: на диске {room['free'] / 1e9:.1f} ГБ, прогону нужно "
                   f"{room['need'] / 1e9:.1f} ГБ (снимок {room['ref_size'] / 1e9:.1f} + рост) "
                   f"— импорт не начат")
            log.error(msg)
            import_errors.append({"pgs_id": "R2_guard", "error": msg})
        else:
            log.info("discover: место ok — свободно %.1f ГБ, нужно %.1f ГБ",
                     room["free"] / 1e9, room["need"] / 1e9)
        # Снимок, оставшийся с прошлого прогона = тот прогон не подтвердил целость.
        # Решаем сами: справочник цел → снимок не нужен; не цел → стоп, снимок держим.
        leftovers = sorted(pgs_reference.snapshot_dir().glob("*.db"))
        if leftovers and blocked is None:
            if _ref_ok(_ref):
                for f in leftovers:
                    _remove_snapshot(f)
                log.info("discover: справочник цел — удалено %d снимков прошлого прогона",
                         len(leftovers))
            else:
                blocked = "damaged"
                msg = (f"discover: справочник не прошёл quick_check, снимок прошлого прогона "
                       f"сохранён ({leftovers[-1].name}) — импорт не начат, нужен откат")
                log.error(msg)
                import_errors.append({"pgs_id": "R2_ref_check", "error": msg})

    # ── Step 1: что уже есть ─────────────────────────────────────────────────
    known_ids: set[str] = set()
    try:
        rows = _ref.execute("SELECT pgs_id FROM pgs_catalog").fetchall()
        known_ids = {r[0] for r in rows}
        log.info("discover: %d already in pgs_catalog", len(known_ids))
    except Exception as exc:  # silent-ok: таблица ещё не инициализирована
        log.warning("discover: pgs_catalog read failed: %s", exc)

    # ── Step 1б: отбор по покрытию (решение владельца 02.11.2026) ────────────
    # На чип-геномах установки 706 из 772 шкал дали <40% покрытия и несли ~все 16,5 ГБ.
    # Бесполезные ДЛЯ ВСЕХ тенантов шкалы удаляются (pgs_rejected помнит, чтобы не качать
    # снова), новые крупнее границы не качаются. Выход из круга «не качаем крупные — нет
    # доказательств, что крупные работают»: появился геном больше тех, на которых шкалу
    # отвергли, — отказы пересматриваются и граница снимается.
    to_prune: list[tuple] = []
    bound: Optional[int] = None
    skipped_size = 0
    if blocked is None:
        pgs_reference.ensure_writer_tables(_ref)
        # Шкала без весов и веса без шкалы — следы оборванной загрузки. Первая числилась
        # «уже скачанной» и не скачивалась и не судилась никогда (26 таких от 02–05.08,
        # замер 03.11); вторые занимают место без читателя. Обе — вон, первая снова кандидат.
        hollow = [r[0] for r in _ref.execute(
            "SELECT pgs_id FROM pgs_catalog c WHERE NOT EXISTS "
            "(SELECT 1 FROM pgs_weights w WHERE w.pgs_id=c.pgs_id)")]
        if hollow:
            _ref.executemany("DELETE FROM pgs_catalog WHERE pgs_id=?", [(p,) for p in hollow])
            known_ids -= set(hollow)
        orphans = _ref.execute("DELETE FROM pgs_weights WHERE pgs_id NOT IN "
                               "(SELECT pgs_id FROM pgs_catalog)").rowcount
        _ref.commit()
        if hollow or orphans:
            log.info("discover: снято %d шкал без весов, %d весов без шкалы", len(hollow), orphans)
        rej = pgs_reference.rejected(_ref)
        evidence = _coverage_evidence()
        now_pos = max((e["positions"] for e in evidence), default=0)
        reopened = [r[0] for r in _ref.execute(
            "SELECT pgs_id FROM pgs_rejected WHERE reason='coverage' AND genome_positions < ?",
            (now_pos,))]
        if reopened:
            _ref.executemany("DELETE FROM pgs_rejected WHERE pgs_id=?", [(p,) for p in reopened])
            _ref.commit()
            log.info("discover: геном больше прежних (%d позиций) — пересмотр %d отказов, "
                     "граница размера снята", now_pos, len(reopened))
            rej = {k: v for k, v in rej.items() if k not in reopened}
        catalog = dict(_ref.execute("SELECT pgs_id, num_variants FROM pgs_catalog"))
        to_prune = _judge(evidence, catalog)
        bound = None if reopened else _size_bound(evidence, catalog)
        known_ids |= set(rej)
        log.info("discover: тенантов-свидетелей %d, к удалению %d, граница размера %s",
                 len(evidence), len(to_prune), bound)

    # ── Step 2: скачиваем листинг из PGS Catalog (пагинация) ─────────────────
    all_scores: list[dict] = []
    # Не писатель / нет места / справочник не цел → каталог не качаем вовсе
    url: Optional[str] = PGS_API_SCORE_LIST if blocked is None else None
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
        if bound is not None and nv > bound:
            skipped_size += 1
            continue
        candidates.append({
            "pgs_id": pid,
            "trait":  s.get("trait_reported", pid),
            "nv":     nv,
        })

    log.info(
        "discover: %d candidates to import (%d existing, %d quality-filtered, "
        "%d larger than bound)",
        len(candidates), skipped_existing, skipped_quality, skipped_size,
    )

    # ── Pre-op snapshot (backup policy R2) ────────────────────────────────────
    # Снимок живёт ТОЛЬКО на время риска (решение владельца 02.11.2026, «0 в покое»):
    # создаётся в доме справочника перед импортом и удаляется, как только справочник
    # прошёл quick_check после импорта. Справочник перекачиваем из PGS Catalog,
    # поэтому держать 16 ГБ «на всякий случай» между прогонами незачем.
    snapshot_error: Optional[str] = None
    snap_path: Optional[str] = None
    if candidates or to_prune:      # удаление весов — такая же запись, как импорт
        try:
            snap_dir = pgs_reference.snapshot_dir()
            snap_dir.mkdir(mode=0o700, exist_ok=True)
            import time as _time_snap
            ts = _time_snap.strftime("%Y%m%d_%H%M")
            # снапшот REFERENCE-БД (её модифицируют импорты), не канона
            snap_path = str(snap_dir / f"pgs_reference.before_discovery_{ts}.db")
            snap_conn = sqlite3.connect(snap_path)
            # SEC db_perms канон: снапшот .db строго 600. sqlite3.connect создаёт
            # файл под umask (обычно → 644) → сенсор security:db_perms тлеет.
            # Сужаем сразу после создания, до записи данных.
            os.chmod(snap_path, 0o600)
            _ref.backup(snap_conn)
            snap_conn.close()
            log.info("discover: pre-op snapshot → %s", os.path.basename(snap_path))
        except Exception as exc:
            snapshot_error = str(exc)
            log.warning("discover: pre-op snapshot failed: %s — продолжаем без снимка", exc)
            if snap_path:   # недописанная копия не страхует, а занимает место
                _remove_snapshot(snap_path)
                snap_path = None

    # ── Step 3б: удаляем бесполезные для всех шкалы ──────────────────────────
    pruned: list[str] = []
    for pid, nv, best, positions in to_prune:
        try:
            _prune_model(_ref, pid, nv, best, positions)
            pruned.append(pid)
        except sqlite3.Error as exc:
            log.error("discover: удаление %s не удалось: %s", pid, exc)
            import_errors.append({"pgs_id": "R2_prune", "error": f"{pid}: {exc}"})
            break
    if pruned:
        log.info("discover: удалено %d шкал, бесполезных для всех тенантов", len(pruned))

    # ── Step 4: импортируем кандидатов (с R1 лимитом) ────────────────────────
    imported: list[dict] = []
    errors:   list[dict] = []
    skipped_format = 0
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
            elif result.get("status") == "unsupported_format":
                # Не ошибка и не повод качать снова: шкала в неаддитивной форме (03.11.2026:
                # 8 из 100 — веса dosage_0/1/2_weight). Запоминаем отказ с причиной format.
                import time as _t
                _ref.execute(
                    "INSERT OR REPLACE INTO pgs_rejected (pgs_id, num_variants, best_coverage_pct,"
                    " genome_positions, rejected_at, reason) VALUES (?,?,0,0,?,'format')",
                    (pid, cand.get("nv", 0), _t.strftime("%Y-%m-%dT%H:%M:%S")))
                _ref.commit()
                skipped_format += 1
                log.info("discover: %s — формат шкалы не аддитивный, запомнен отказ", pid)
            else:
                errors.append({"pgs_id": pid, "error": result.get("status")})
                log.warning("discover: %s → status=%s", pid, result.get("status"))
        except Exception as exc:  # silent-ok: один плохой файл не роняет весь batch
            log.warning("discover: %s import failed: %s", pid, exc)
            errors.append({"pgs_id": pid, "error": str(exc)})

    # ── Снимок отслужил: справочник цел → удалить; не цел → держать и кричать ─
    if snap_path:
        if _ref_ok(_ref):
            _remove_snapshot(snap_path)
            log.info("discover: справочник цел после импорта — снимок удалён")
        else:
            msg = (f"discover: справочник не прошёл quick_check после импорта — снимок "
                   f"{os.path.basename(snap_path)} сохранён для отката")
            log.error(msg)
            import_errors.append({"pgs_id": "R2_ref_check", "error": msg})

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

    # ── Квитанция писателя (02.11.2026): по ней ночной датчик видит, что докачка жива ──
    if blocked is None:
        try:
            import datetime as _dt
            import health_db as _hdb
            from _time_inject import get_now
            _ref.execute(
                "INSERT INTO discovery_runs VALUES (?,?,?,?,?,?)",
                (get_now(_dt.timezone.utc).isoformat(timespec="seconds"),
                 Path(str(_hdb.DB_PATH)).parent.parent.name,
                 len(imported), len(pruned), int(capped), next_due_at))
            _ref.commit()
        except sqlite3.Error as exc:
            log.error("discover: квитанция прогона не записана: %s", exc)
            import_errors.append({"pgs_id": "R2_receipt", "error": str(exc)})

    _ref.close()
    if _owned:
        _conn.close()

    return {
        "imported":         imported,
        "skipped_existing": skipped_existing,
        "skipped_quality":  skipped_quality,
        "skipped_size":     skipped_size,
        "skipped_format":   skipped_format,
        "size_bound":       bound,
        "pruned":           pruned,
        "errors":           import_errors + errors,
        "capped":           capped,
        "phase_h_run":      phase_h_run,
        "phase_h_results":  phase_h_results,
        "snapshot_error":   snapshot_error,  # None если снимок создан успешно
    }


# ── Internal helpers ──────────────────────────────────────────────────────────

def _evidence_db_paths() -> list:
    """Каноны тенантов, чьё покрытие судит отбор (реестр установки, C-19)."""
    import secrets_paths
    import health_db
    return secrets_paths.tenant_db_paths(current=Path(str(health_db.DB_PATH)))


def _coverage_evidence() -> list[dict]:
    """По каждому видимому тенанту: число позиций генома и покрытие каждой шкалы
    последнего импорта генома — {positions, cov: {pgs_id: (matched, total)}}.

    Граница видимости (02.11.2026): тенант в контейнере здесь не виден (его канон в томе
    VM); на день замера его покрытие совпадало с партнёром по всем 772 шкалам."""
    out = []
    for p in _evidence_db_paths():
        try:
            c = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
            gid = c.execute("SELECT MAX(id) FROM genome_imports").fetchone()[0]
            if gid is None:
                c.close()
                continue
            positions = c.execute("SELECT COUNT(*) FROM raw_snps").fetchone()[0]
            cov = {pid: (m, t) for pid, m, t in c.execute(
                "SELECT pgs_id, snps_matched, snps_total FROM prs_scores "
                "WHERE genome_import_id=?", (gid,))}
            c.close()
        except sqlite3.Error as exc:   # у тенанта нет генома/шкал — он не голосует
            log.info("discover: покрытие %s не прочитано: %s", p, exc)
            continue
        out.append({"positions": positions, "cov": cov})
    return out


def _best_coverage(evidence: list[dict], pid: str) -> Optional[float]:
    """Лучшее покрытие шкалы среди тенантов; None — кто-то её ещё не посчитал."""
    covs = []
    for e in evidence:
        mt = e["cov"].get(pid)
        if not mt or not mt[1]:
            return None
        covs.append(100.0 * mt[0] / mt[1])
    return max(covs) if covs else None


def _judge(evidence: list[dict], catalog: dict) -> list[tuple]:
    """Шкалы, бесполезные ДЛЯ ВСЕХ тенантов: (pgs_id, num_variants, best_cov, positions)."""
    from prs_context import COVERAGE_USABLE_PCT
    if not evidence:
        return []
    positions = max(e["positions"] for e in evidence)
    out = []
    for pid, nv in catalog.items():
        best = _best_coverage(evidence, pid)
        if best is not None and best < COVERAGE_USABLE_PCT:
            out.append((pid, nv or 0, best, positions))
    return out


def _size_bound(evidence: list[dict], catalog: dict) -> Optional[int]:
    """Наибольший размер шкалы, давшей пригодное покрытие хоть у кого-то. Новые шкалы
    крупнее границы не качаются: замер 02.11 — из 439 шкал крупнее 1 тыс. вариантов ни
    одна не дотянула до 40% на чип-геномах. Нет доказательств → границы нет."""
    from prs_context import COVERAGE_USABLE_PCT
    usable = [nv for pid, nv in catalog.items()
              if nv and (_best_coverage(evidence, pid) or 0) >= COVERAGE_USABLE_PCT]
    return max(usable) if usable else None


def _prune_model(ref: sqlite3.Connection, pid: str, nv: int, best: float, positions: int) -> None:
    """Веса бесполезной шкалы — вон, запись об отказе — в pgs_rejected (чтобы не качать
    снова). По одной шкале на транзакцию + чекпойнт: удаление 226 млн строк одной
    транзакцией раздуло бы WAL до размера справочника (урок 06-27)."""
    import time as _t
    ref.execute("DELETE FROM pgs_weights WHERE pgs_id=?", (pid,))
    ref.execute("DELETE FROM pgs_catalog WHERE pgs_id=?", (pid,))
    ref.execute("INSERT OR REPLACE INTO pgs_rejected (pgs_id, num_variants, best_coverage_pct,"
                " genome_positions, rejected_at, reason) VALUES (?,?,?,?,?,'coverage')",
                (pid, nv, round(best, 1), positions, _t.strftime("%Y-%m-%dT%H:%M:%S")))
    ref.commit()
    ref.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def _ref_ok(ref: sqlite3.Connection) -> bool:
    """Справочник цел? PRAGMA quick_check (минуты на 16 ГБ — раз в месяц, в потоке)."""
    try:
        row = ref.execute("PRAGMA quick_check").fetchone()
        return bool(row) and row[0] == "ok"
    except sqlite3.Error as exc:
        log.error("discover: quick_check справочника упал: %s", exc)
        return False


def _remove_snapshot(path) -> None:
    for p in (str(path), f"{path}-wal", f"{path}-shm"):
        try:
            os.remove(p)
        except FileNotFoundError:
            pass


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
