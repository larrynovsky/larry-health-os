#!/usr/bin/env python3.11
"""
lab_backfill.py — массовое перераспознавание анализов в lab_results_staging.

Конвейер (дизайн lab_pipeline_design_2026-06-29):
  imported lab-документ → lab_recognizer.recognize (ансамбль) →
  lab_oracles.verify → запись в lab_results_staging с провенансом и
  маршрутизацией: полное согласие проходов + зелёные оракулы → 'auto',
  иначе → 'pending' (человек-гейт).

Канон lab_results НЕ трогаем. Промоут confirmed/auto → отдельный шаг.

Список документов берём из JSON-прослойки biochemical: она хранит
source_file оригинала и дату. В набор для перечитывания входят только
лабораторные документы; счета и прочие типы исключаются.

Запуск на Studio:
  /opt/homebrew/bin/python3.11 lab_backfill.py --run-id R1 --max 3
  /opt/homebrew/bin/python3.11 lab_backfill.py --run-id R1 --only "CR/lab eng.pdf"
"""
from __future__ import annotations
from _time_inject import get_now  # seam
import argparse
import glob
import json
import logging
import os
from pathlib import Path

import sqlite3
import health_db
import labs_db
import lab_recognizer
import lab_oracles
import lab_specimen

log = logging.getLogger("lab_backfill")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

import infra_config
ICLOUD_ROOT = infra_config.cloud_dir()   # дом пути — infra_config (BL-PUB-12)
BIOCHEM_DIR = Path(os.environ.get("HEALTH_DATA_DIR", str(Path.home() / "health"))) / "data" / "biochemical"

_STAGING_COLS = [
    "run_id", "extractor_version", "source_file", "page", "raw_line", "bbox",
    "date", "panel", "raw_name", "canonical_name", "value", "value_text",
    "unit", "ref_low", "ref_high", "doc_flag", "pass1_value", "pass2_value",
    "parser_value", "value_agreement", "unit_agreement", "ref_agreement", "field_evidence",
    "oracle_status", "oracle_notes", "confidence",
    "review_status", "date_source",
    "specimen", "specimen_source", "page_role", "value_op",
]


def doc_list() -> list[tuple[str, str]]:
    """Уникальные (source_file_оригинала, date) из biochemical JSON-прослойки."""
    seen: dict[str, str] = {}
    for jf in glob.glob(str(BIOCHEM_DIR / "**" / "*.json"), recursive=True):
        try:
            d = json.load(open(jf))
        except Exception:
            continue
        sf, date = d.get("source_file"), d.get("date")
        if sf and date and sf not in seen:
            seen[sf] = date
    return sorted(seen.items())


def _tenant_root() -> Path:
    """Корень тенанта ТЕКУЩЕГО процесса. Дефолт — владелец (прежнее поведение)."""
    return Path(os.environ.get("HEALTH_DATA_DIR") or str(Path.home() / "health")).resolve()


def _assert_belongs_to_tenant(path: Path) -> None:
    """Документ обязан принадлежать тенанту процесса. Иначе — ОТКАЗ, не предупреждение.

    Разрыв, закрытый 2026-08-08 прогоном линз. Тенант выбирается ПЕРЕМЕННОЙ
    ОКРУЖЕНИЯ (`HEALTH_DATA_DIR`), а документ приезжает АБСОЛЮТНЫМ путём — и до этой
    правки ничто их не сверяло. Запуск разбора партнёрского бланка из привычной
    сессии (без `HEALTH_DATA_DIR=~/health_partner`) положил бы анализы партнёра в
    канон владельца, а промоут потом «покрыл» бы ими владельческие строки по
    ключу (дата, аналит, материал). Жертвы обе: партнёр — данные не там, владелец —
    чужие значения во врачебном отчёте.

    Это НЕ гипотеза: в тот же вечер я сам гонял `--doc-path` без указания тенанта, и
    это сработало ровно потому, что владелец — умолчание.

    Общий iCloud/CR владельца разрешён отдельной веткой: он не принадлежит ни одному
    тенанту физически, но исторически является инбоксом владельца. Для НЕ-владельца
    он запрещён — иначе правило снимало бы само себя.
    """
    root = _tenant_root()
    rp = path.resolve()
    if rp.is_relative_to(root):
        return
    is_owner = root == (Path.home() / "health").resolve()
    if is_owner and rp.is_relative_to(ICLOUD_ROOT.resolve()):
        return
    raise ValueError(
        f"документ вне тенанта: {rp} не лежит в {root}. "
        f"Тенант берётся из HEALTH_DATA_DIR; для партнёра запускай "
        f"HEALTH_DATA_DIR=~/health_partner HEALTH_SECRETS_DIR=~/.health_secrets_partner")


def resolve_document(source_file: str) -> Path | None:
    """Резолв пути документа. Multitenancy (2026-06-30): помимо владельцаной
    iCloud/CR ищем в инбоксе тенанта (HEALTH_DATA_DIR/incoming) и принимаем
    абсолютный путь — файлы партнёра живут в его тенанте, не в iCloud.
    source_file в JSON неоднороден: то с префиксом CR/, то голое имя."""
    p = Path(source_file)
    if p.is_absolute() and p.exists():
        _assert_belongs_to_tenant(p)
        return p
    inbox = Path(os.environ.get("HEALTH_DATA_DIR") or str(Path.home() / "health")) / "incoming"
    cands = [inbox / source_file,
             inbox / Path(source_file).name,
             ICLOUD_ROOT / source_file,
             ICLOUD_ROOT / "CR" / source_file,
             ICLOUD_ROOT / "CR" / Path(source_file).name]
    for c in cands:
        if c.exists():
            _assert_belongs_to_tenant(c)     # и относительное имя может увести в чужой дом
            return c
    for root in (inbox, ICLOUD_ROOT / "CR"):
        hits = glob.glob(str(root / "**" / Path(source_file).name), recursive=True)
        if hits:
            found = Path(hits[0])
            # Поиск ПО ИМЕНИ особенно опасен: одинаково названный бланк в чужом
            # тенанте находится молча. Гейт стоит и здесь, а не только у абсолютных.
            _assert_belongs_to_tenant(found)
            return found
    return None


# Имя стало публичным 2026-07-31: в этот резолвер УЖЕ лезли двое снаружи
# (`lab_reconcile`, `lab_review_sheet`), то есть приватность была объявлена, а не
# соблюдена. Оба читателя переведены на публичное имя тем же днём, поэтому
# переходный алиас `_resolve` снят — иначе он пережил бы повод своего появления.


def _route(verdict: dict, stats: dict) -> str:
    full_agree = stats.get("disagreements", 1) == 0 and stats.get("singles", 1) == 0
    return "auto" if verdict["status"] == "green" and full_agree else "pending"


def _write_rows(run_id: str, source_file: str, res: dict, verdict: dict) -> int:
    review = _route(verdict, res["stats"])
    notes = json.dumps(verdict.get("issues", {}), ensure_ascii=False)
    # 2026-07-31: материал пробы читается ИЗ БЛАНКА в точке разбора, а не назначается
    # позже по имени панели. Один проход по документу на весь набор строк.
    # Отказ чтения не должен ронять импорт: без фактов строки получат 'unknown', и
    # это увидит датчик check_staging_specimen_provenance — тихой дырой не станет.
    facts: dict = {}
    _p = resolve_document(source_file)
    if _p is not None and _p.suffix.lower() == ".pdf":
        try:
            facts = lab_specimen.page_facts(_p)
        except Exception as e:  # silent-ok: материал не обязателен для импорта строки
            log.warning("lab_specimen: материал не прочитан из %s: %s", source_file, e)
    rows = []
    skipped_derived = 0
    for t in res["tests"]:
        pf = facts.get(t.get("page")) or {}
        if pf.get("role") == "derived_chart":
            # Производный график повторяет исходные измерения; повторно не импортируем.
            skipped_derived += 1
            continue
        rows.append({
            "run_id": run_id,
            "extractor_version": res["extractor_version"],
            "source_file": source_file,
            "page": t.get("page"),
            "raw_line": t.get("raw_name"),
            "bbox": None,
            "date": t.get("date") or res["date"],
            "panel": t.get("panel"),
            "raw_name": t.get("raw_name"),
            "canonical_name": t.get("canonical_name"),
            "value": t.get("value"),
            # 2026-08-08: было прибито к None. Даже вернув «отрицательно», распознаватель
            # терял результат ЗДЕСЬ — четвёртый обрыв той же трубы, найден при починке
            # первых трёх. Сырое слово едет в staging как есть; к словарю его сводит
            # промоут (симметрия с именем: raw_name → canonical_name там же).
            "value_text": (t.get("value_text") or None),
            "unit": t.get("unit"),
            "ref_low": t.get("ref_low"),
            "ref_high": t.get("ref_high"),
            "doc_flag": t.get("doc_flag"),
            "pass1_value": t.get("pass1_value"),
            "pass2_value": t.get("pass2_value"),
            "parser_value": t.get("parser_value"),
            "value_agreement": t.get("value_agreement"),
            "unit_agreement": t.get("unit_agreement"),
            "ref_agreement": t.get("ref_agreement"),
            "field_evidence": t.get("field_evidence"),
            "oracle_status": verdict["status"],
            "oracle_notes": notes,
            "confidence": t.get("confidence"),
            "review_status": review,
            "date_source": t.get("date_source"),
            "specimen": pf.get("specimen"),
            "specimen_source": pf.get("specimen_source") or lab_specimen.UNKNOWN,
            "page_role": pf.get("role") or "data",
            # оператор сравнения: «< 2.0» — потолок, а не результат
            "value_op": (t.get("value_op") or None),
        })
    if skipped_derived:
        log.info("%s: не импортировано %d строк со страниц-графиков (derived_chart)",
                 source_file, skipped_derived)
    placeholders = ",".join("?" for _ in _STAGING_COLS)
    sql = f"INSERT INTO lab_results_staging ({','.join(_STAGING_COLS)}) VALUES ({placeholders})"
    with health_db.get_conn() as conn:
        # идемпотентность: перезапуск того же (run_id, source_file) не дублирует
        conn.execute("DELETE FROM lab_results_staging WHERE run_id=? AND source_file=?",
                     (run_id, source_file))
        conn.executemany(sql, [[r[c] for c in _STAGING_COLS] for r in rows])
        conn.commit()
    return len(rows)


# ОБЩИЙ словарь-справочник имён аналитов (директива 2026-07-01: словарь общий,
# новые параметры ДОБАВЛЯЕМ, не отсеиваем). Имена аналитов — reference-данные,
# НЕ пациентские, поэтому живут вне тенантов, в общем сторе, и видны всем.
_SHARED_VOCAB_DB = Path.home() / ".health_shared" / "lab_vocab.db"


def _shared_vocab_conn() -> sqlite3.Connection:
    _SHARED_VOCAB_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_SHARED_VOCAB_DB))
    conn.execute("CREATE TABLE IF NOT EXISTS lab_vocab ("
                 "canonical_name TEXT PRIMARY KEY, added_at TEXT, source TEXT)")
    return conn


def _load_vocab() -> list[str]:
    """Словарь для распознавателя: базовый lab_recognizer._VOCAB ∪ ОБЩИЙ lab_vocab.
    Самопополнение — add_vocab() из ревью, общее для всех тенантов."""
    names = list(lab_recognizer._VOCAB)
    try:
        conn = _shared_vocab_conn()
        extra = [r[0] for r in conn.execute("SELECT canonical_name FROM lab_vocab")]
        conn.close()
        for n in extra:
            if n and n not in names:
                names.append(n)
    except Exception as e:
        log.warning(f"_load_vocab: shared lab_vocab read failed: {e}")
    return names


def add_vocab(canonical_name: str, source: str = "review") -> bool:
    """Добавить канон-имя в ОБЩИЙ самопополняющийся словарь (idempotent).
    Зовётся из ревью при подтверждении неизвестного аналита → далее маппится
    у ВСЕХ тенантов."""
    canonical_name = (canonical_name or "").strip()
    if not canonical_name:
        return False
    import datetime as _d
    conn = _shared_vocab_conn()
    conn.execute(
        "INSERT OR IGNORE INTO lab_vocab(canonical_name, added_at, source) VALUES (?,?,?)",
        (canonical_name, get_now().isoformat(timespec="seconds"), source),
    )
    conn.commit()
    conn.close()
    return True


def apply_assignment(run_id: str, source_file: str, raw_name: str,
                     value, canonical: str) -> int:
    """Ревью: назначить канон неизвестному аналиту → обновить staging + добавить
    в ОБЩИЙ словарь (дальше маппится сам у всех тенантов). Возврат: rowcount."""
    canonical = (canonical or "").strip()
    if not canonical:
        return 0
    add_vocab(canonical, source="review")
    try:
        fval = float(value)
    except (TypeError, ValueError):
        fval = None
    with health_db.get_conn() as conn:
        cur = conn.execute(
            "UPDATE lab_results_staging SET canonical_name=? "
            "WHERE run_id=? AND source_file=? AND (raw_line=? OR raw_name=?) "
            "AND ABS(COALESCE(value,-1e30) - ?) < 1e-6",
            (canonical, run_id, source_file, raw_name, raw_name,
             fval if fval is not None else -1e30))
        conn.commit()
        return cur.rowcount


def _ensure_recognized_docs(conn) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS recognized_docs ("
        "sha256 TEXT PRIMARY KEY, source_file TEXT, run_id TEXT, recognized_at TEXT)"
    )


def _file_sha256(path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def already_recognized(sha: str) -> bool:
    """True если файл с таким content-hash уже распознавался (не переразбирать)."""
    with health_db.get_conn() as conn:
        _ensure_recognized_docs(conn)
        return conn.execute(
            "SELECT 1 FROM recognized_docs WHERE sha256=?", (sha,)).fetchone() is not None


def _record_recognized(sha: str, source_file: str, run_id: str) -> None:
    import datetime as _d
    with health_db.get_conn() as conn:
        _ensure_recognized_docs(conn)
        conn.execute(
            "INSERT OR IGNORE INTO recognized_docs(sha256, source_file, run_id, recognized_at) "
            "VALUES(?,?,?,?)",
            (sha, source_file, run_id, get_now().isoformat(timespec="seconds")))
        conn.commit()


def run_backfill(run_id: str, max_docs: int | None = None,
                 only: str | None = None, doc_path: str | None = None,
                 date: str | None = None, force: bool = False,
                 pages: list[int] | None = None) -> dict:
    """`pages` — адресное перечитывание конкретных страниц (номера с 1).

    Позволяет исправить отдельную страницу без vision-прогона всего документа.
    Промоут удаляет только покрытое новым, поэтому частичный прогон заменяет
    ровно прочитанное.
    """
    health_db.init_db()
    refs = labs_db.get_lab_refs()
    vocab = _load_vocab()
    if doc_path:
        # явный документ (нет в JSON-прослойке: clinic-named источники)
        docs = [(doc_path, date or "")]
    else:
        docs = doc_list()
        if only:
            docs = [(sf, dt) for sf, dt in docs if sf == only]
        if max_docs:
            docs = docs[:max_docs]

    summary = {"run_id": run_id, "docs": 0, "skipped_missing": 0, "skipped_dup": 0,
               "rows": 0, "auto": 0, "pending": 0, "errors": 0, "detail": []}
    for source_file, date in docs:
        abs_path = resolve_document(source_file)
        if abs_path is None:
            summary["skipped_missing"] += 1
            log.warning(f"SKIP missing: {source_file}")
            continue
        sha = _file_sha256(abs_path)
        if not force and already_recognized(sha):
            summary["skipped_dup"] += 1
            log.info(f"SKIP dup (уже распознан по content-hash): {source_file}")
            continue
        try:
            res = lab_recognizer.recognize(abs_path, date, vocab=vocab, pages=pages)
            tests = [{
                "canonical_name": t.get("canonical_name"), "value": t.get("value"),
                "unit": t.get("unit"), "ref_low": t.get("ref_low"),
                "ref_high": t.get("ref_high"), "doc_flag": t.get("doc_flag"),
                "panel": t.get("panel"),
            } for t in res["tests"]]
            verdict = lab_oracles.verify(tests, canonical_refs=refs)
            n = _write_rows(run_id, source_file, res, verdict)
            _record_recognized(sha, source_file, run_id)   # дедуп: больше не переразбирать
            review = _route(verdict, res["stats"])
            summary["docs"] += 1
            summary["rows"] += n
            summary[review] += 1
            summary["detail"].append({
                "source_file": source_file, "rows": n, "review": review,
                "oracle": verdict["count"], **res["stats"],
            })
            log.info(f"{source_file}: {n} rows, {review}, oracle={verdict['count']}, "
                     f"disagree={res['stats']['disagreements']}")
        except Exception as e:
            summary["errors"] += 1
            log.error(f"ERROR {source_file}: {e}")
    return summary


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--max", type=int, default=None)
    ap.add_argument("--only", default=None)
    ap.add_argument("--doc-path", default=None, help="явный документ (отн. путь), не из JSON-прослойки")
    ap.add_argument("--date", default=None, help="дата-фолбэк для --doc-path")
    ap.add_argument("--force", action="store_true",
                    help="переразобрать даже если content-hash уже распознан")
    ap.add_argument("--pages", default=None,
                    help="только эти страницы (с 1, через запятую): адресное перечитывание")
    a = ap.parse_args()
    _pages = [int(x) for x in a.pages.split(",")] if a.pages else None
    s = run_backfill(a.run_id, a.max, a.only, a.doc_path, a.date, force=a.force,
                     pages=_pages)
    print(json.dumps(s, ensure_ascii=False, indent=2))
