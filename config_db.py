"""config_db.py — доменный модуль config. Вынесен из health_db.py (Поток C, strangler-фасад)."""
from __future__ import annotations

import json
import os
import logging
from datetime import date, timedelta
from pathlib import Path


log = logging.getLogger(__name__)


def default_visit_specialist() -> str:
    """Дефолтный специалист для /visit — per-tenant из system_config, иначе нейтральный 'general'.
    Убирает зашитый 'oncologist' (диагнозозависимость, нить diagnosis-hardcode): тенант с онко
    задаёт ключ default_visit_specialist='oncologist', тенант без него падает в нейтральный general.
    Дефолт следует из ДАННЫХ тенанта, не из литерала в коде."""
    return get_config("default_visit_specialist", "general")


def get_config(key: str, default=None, conn=None):
    """Читает параметр конфигурации из БД.
    Возвращает value_json (распарсенный) > value_num > value_text > default.

    `conn` — соединение вызывающего (2026-07-31). Без него берётся БД текущего
    процесса. Параметр заведён не для удобства: датчик целостности обходит
    тенантов read-only, и порог/настройка обязаны читаться из БД ТОГО ЖЕ тенанта.
    Без него у `system_config` появился бы второй читающий SQL в lab_promote —
    дубль-гейт поймал ровно это на коммите b6571b1.
    """
    import json as _json
    sql = "SELECT value_text, value_num, value_json FROM system_config WHERE key=?"
    if conn is not None:
        row = conn.execute(sql, (key,)).fetchone()
    else:
        with _hdb.get_conn() as own:
            row = own.execute(sql, (key,)).fetchone()
    if row is None:
        return default
    if row["value_json"]:
        try:
            return _json.loads(row["value_json"])
        except Exception:
            pass
    if row["value_num"] is not None:
        return row["value_num"]
    if row["value_text"] is not None:
        return row["value_text"]
    return default


def get_routing_keywords() -> dict[str, list[str]]:
    """Возвращает {domain: [keyword,...]}. Special key '__full__' содержит
    keywords которые триггерят полный контекст (все domains).
    """
    with _hdb.get_conn() as conn:
        rows = conn.execute(
            "SELECT domain, keyword FROM routing_keywords ORDER BY domain, keyword"
        ).fetchall()
    result: dict[str, list[str]] = {}
    for r in rows:
        result.setdefault(r["domain"], []).append(r["keyword"])
    return result


def get_domain_signals(domain: str) -> list:
    """Возвращает domain-signals для evaluate_domain_need.
    Читает из system_config (список dict: metric/label/r/threshold_pct),
    fallback возвращает пустой list."""
    val = get_config(f"domain_signals.{domain}")
    if val is not None:
        return val
    return []


def get_doc_patterns() -> list:
    """Возвращает все паттерны классификации документов из БД.
    Список dict: pattern, doc_type, match_on, match_type, specialist_name."""
    with _hdb.get_conn() as conn:
        rows = conn.execute(
            """SELECT pattern, doc_type, match_on, match_type, specialist_name
               FROM doc_patterns ORDER BY id"""
        ).fetchall()
    return [dict(r) for r in rows]


def mark_imported(source_file: str, doc_type: str) -> None:
    """Помечает файл как импортированный. INSERT OR IGNORE — безопасен при гонках."""
    with _hdb.get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO imported_docs (source_file, doc_type) VALUES (?, ?)",
            (source_file, doc_type),
        )


def get_imported_sources() -> set:
    """Возвращает множество полных относительных путей уже импортированных файлов."""
    with _hdb.get_conn() as conn:
        rows = conn.execute("SELECT source_file FROM imported_docs").fetchall()
    return {r[0] for r in rows}


def upsert_config(key: str, value_text=None, value_num=None, value_json=None,
                  category: str = None, source: str = "manual") -> None:
    """Записывает или обновляет параметр конфигурации системы."""
    import json as _json
    if isinstance(value_json, (dict, list)):
        value_json = _json.dumps(value_json, ensure_ascii=False)
    with _hdb.get_conn() as conn:
        conn.execute(
            """INSERT INTO system_config (key, value_text, value_num, value_json, category, updated_at, source)
               VALUES (?, ?, ?, ?, ?, datetime('now'), ?)
               ON CONFLICT(key) DO UPDATE SET
                   value_text = excluded.value_text,
                   value_num  = excluded.value_num,
                   value_json = excluded.value_json,
                   category   = excluded.category,
                   updated_at = excluded.updated_at,
                   source     = excluded.source""",
            (key, value_text, value_num, value_json, category, source),
        )


# Резервы — ЗЕРКАЛА сидов system_config (health_db._migrate_patient_profile_and_config), равенство
# держит coherence-тест; читать через функции ниже, не константы. Решение владельца 29.09: языки
# распознавания и маркеры типов документов — данные тенанта, в публичном коде ни одного
# «своего» языка или учреждения.
_OCR_LANGUAGES_FALLBACK = "eng+rus"
_DOC_TYPE_MARKERS_FALLBACK: dict = {}


def _installed_ocr_languages() -> set[str] | None:
    """Языки, для которых у tesseract есть пакеты; None — tesseract недоступен."""
    import shutil
    import subprocess
    exe = shutil.which("tesseract") or "/opt/homebrew/bin/tesseract"
    try:
        out = subprocess.run([exe, "--list-langs"], capture_output=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    lines = out.stdout.decode(errors="replace").splitlines()
    return {ln.strip() for ln in lines[1:] if ln.strip()}


def ocr_languages(conn=None) -> str:
    """Языки OCR тенанта для `tesseract -l` (system_config `ocr.languages`, вид «eng+rus»).

    Язык без установленного пакета отбрасывается с громким warning: без пакета tesseract
    даёт мусор или падает. Нет БД — резерв, тоже громко (§14)."""
    try:
        want = str(get_config("ocr.languages", _OCR_LANGUAGES_FALLBACK, conn=conn))
    except Exception:  # noqa: BLE001 — нет БД/таблицы: резерв, но ГРОМКО
        log.warning("ocr.languages недоступен в БД, взят резерв %s (§14)", _OCR_LANGUAGES_FALLBACK)
        want = _OCR_LANGUAGES_FALLBACK
    langs = [x for x in want.split("+") if x]
    have = _installed_ocr_languages()
    if have is not None:
        missing = [x for x in langs if x not in have]
        if missing:
            log.warning("OCR: нет пакетов tesseract для %s — распознаю без них", missing)
        langs = [x for x in langs if x in have] or ["eng"]
    return "+".join(langs)


def doc_type_markers(conn=None) -> dict:
    """Маркеры типа документа тенанта (system_config `docs.type_markers`):
    {"name": {doc_type: [подстроки имени файла]}, "text": {doc_type: [подстроки текста]}}.
    Для слов на языках и бланков конкретных учреждений — они данные тенанта, не код."""
    try:
        raw = get_config("docs.type_markers", _DOC_TYPE_MARKERS_FALLBACK, conn=conn)
    except Exception:  # noqa: BLE001 — нет БД/таблицы: резерв, но ГРОМКО
        log.warning("docs.type_markers недоступен в БД, взят пустой резерв (§14)")
        raw = _DOC_TYPE_MARKERS_FALLBACK
    return raw if isinstance(raw, dict) else {}


def marker_doc_type(markers: dict, where: str, haystack: str) -> str | None:
    """Первый doc_type, чья подстрока (без учёта регистра) есть в haystack; where = name|text."""
    low = haystack.lower()
    for doc_type, subs in (markers.get(where) or {}).items():
        if any(s and s.lower() in low for s in subs):
            return doc_type
    return None


# health_db — В КОНЦЕ модуля (BL-TEST-COLLECT-ALONE-1, 2026-09-24): он ре-экспортирует функции
# этого модуля, и импорт наверху давал цикл, если модуль импортировали первым (28 из 29 доменных
# модулей). Имя _hdb нужно только внутри функций — к их вызову health_db уже загружен.
import health_db as _hdb  # noqa: E402
