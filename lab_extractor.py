import llm_client
#!/usr/bin/env python3.11
"""
lab_extractor.py — LLM-экстракция структурированных данных из OCR-текста
лабораторных отчётов. Пишет pending-файл, не пишет напрямую в БД.

Почему модель выбрана такой и как её мерили: docs/explanation/medgemma_lab_extraction_eval.md.
"""
from _time_inject import get_now  # seam
import json
import hai_core
import re
import os
import logging
from datetime import datetime, timezone
from pathlib import Path

import anthropic

log = logging.getLogger(__name__)

import infra_config
HEALTH_DIR  = infra_config.cloud_dir()   # дом пути — infra_config (BL-PUB-12)
PENDING_DIR = HEALTH_DIR / "data" / "pending_labs"

EXTRACTION_PROMPT = """\
You are extracting structured lab results from hospital lab report OCR text (the report may mix scripts and languages).
The OCR is imperfect: decimal points may be missing ("152" means "15.2" for HGB),
numbers may run together ("69" means "6.9" for WBC if range is 4.5-11.0).
Use the reference range in the SAME line to validate the value scale.

Return ONLY valid JSON, no commentary. Schema:
{
  "lines_seen": <int, how many lines looked like test results>,
  "tests": [
    {
      "name": "<standard English test name>",
      "raw_line": "<exact OCR line>",
      "value": <float>,
      "unit": "<unit string or empty>",
      "ref_low": <float or null>,
      "ref_high": <float or null>,
      "flagged": <true if value outside ref range>,
      "confidence": "high" | "low"
    }
  ]
}

confidence="low" means you had to correct an OCR error to get the value.
Do NOT invent tests not present in the text. If you cannot parse a line reliably, skip it.

OCR TEXT:
{text}
"""


def _get_client() -> anthropic.Anthropic:
    return llm_client.guarded_client()


def extract_labs(summary_text: str, date: str, source: str,
                 content_hash: str, source_file: str) -> dict | None:
    """
    Вызывает Haiku для структурированной экстракции.
    Возвращает pending-dict или None при ошибке.
    """
    try:
        client = _get_client()
        resp = client.messages.create(task="lab_extractor.extract_labs",
            model=hai_core.get_model("haiku_pinned"),
            max_tokens=2000,
            temperature=0,
            messages=[{"role": "user", "content":
                        EXTRACTION_PROMPT.format(text=summary_text[:3000])}]
        )
        raw = llm_client.answer_text(resp).strip()
        # Убрать возможный markdown-блок
        raw = re.sub(r"^```json\s*|\s*```$", "", raw, flags=re.S).strip()
        extracted = json.loads(raw)
    except Exception as e:
        log.error(f"lab_extractor: Haiku call failed: {e}")
        return None

    tests = extracted.get("tests", [])
    lines_seen = extracted.get("lines_seen", 0)

    return {
        "content_hash": content_hash,
        "date": date,
        "source": source,
        "source_file": source_file,
        "visit_key": f"{date}_{source}",
        "status": "pending",
        "pending_since": get_now(timezone.utc).isoformat(),
        "lines_seen": lines_seen,
        "tests_extracted": len(tests),
        "tests": tests,
    }


def queue_pending(pending: dict) -> Path:
    """
    Атомарно сохраняет pending-файл.
    Если файл для того же visit_key уже есть — мёрджит тесты (добавляет новые).
    """
    PENDING_DIR.mkdir(parents=True, exist_ok=True)
    h = pending["content_hash"]
    path = PENDING_DIR / f"{h}.json"

    # Идемпотентность: уже существует — не трогаем
    if path.exists():
        log.info(f"pending уже существует: {h}")
        return path

    # Поиск existing pending с тем же visit_key (другой файл того же визита)
    visit_key = pending["visit_key"]
    for existing_path in PENDING_DIR.glob("*.json"):
        try:
            existing = json.loads(existing_path.read_text(encoding="utf-8"))
            if (existing.get("visit_key") == visit_key
                    and existing.get("status") == "pending"):
                # Мёрдж: добавляем тесты которых ещё нет (по name)
                existing_names = {t["name"] for t in existing.get("tests", [])}
                new_tests = [t for t in pending["tests"] if t["name"] not in existing_names]
                existing["tests"].extend(new_tests)
                existing["tests_extracted"] = len(existing["tests"])
                existing["lines_seen"] += pending.get("lines_seen", 0)
                existing["merged_hashes"] = existing.get("merged_hashes", []) + [h]
                tmp = existing_path.with_suffix(".tmp")
                tmp.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
                os.replace(tmp, existing_path)
                log.info(f"мёрдж в существующий визит {visit_key}: +{len(new_tests)} тестов")
                return existing_path
        except Exception:
            continue

    # Новый pending файл — атомарная запись
    tmp = PENDING_DIR / f"{h}.tmp"
    tmp.write_text(json.dumps(pending, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    log.info(f"pending создан: {h} ({len(pending['tests'])} тестов)")
    return path


def format_telegram_message(pending: dict) -> str:
    """Формирует текст сообщения для Telegram."""
    date = pending.get("date", "?")
    source = pending.get("source", "?")
    n_extracted = pending.get("tests_extracted", 0)
    n_seen = pending.get("lines_seen", 0)
    tests = pending.get("tests", [])

    lines = [f"📋 *Новые анализы • {source} • {date}*"]

    gap = n_seen - n_extracted
    if gap > 0:
        lines.append(f"⚠️ Извлечено {n_extracted} из ~{n_seen} строк \\(возможны пропуски\\)")
    else:
        lines.append(f"Извлечено тестов: {n_extracted}")

    low_conf = [t for t in tests if t.get("confidence") == "low"]
    if low_conf:
        lines.append(f"⚠️ Исправлений OCR: {len(low_conf)} — проверь по оригиналу")

    lines.append("")
    for t in tests:
        name = t.get("name", "?")
        val = t.get("value")
        unit = t.get("unit", "")
        ref_l = t.get("ref_low")
        ref_h = t.get("ref_high")
        flagged = t.get("flagged", False)
        conf = t.get("confidence", "high")
        raw = t.get("raw_line", "")

        ref_str = f"({ref_l}–{ref_h})" if ref_l is not None and ref_h is not None else ""
        flag_str = "⚠️ HIGH" if flagged else "✅"
        corr_str = "  \\[исправлено\\]" if conf == "low" else ""
        val_str = f"{val:.1f}" if val is not None else "?"

        lines.append(f"`{name:<20}` {val_str} {unit}  {flag_str} {ref_str}{corr_str}")
        if conf == "low":
            safe_raw = raw[:60].replace("_", "\\_").replace("*", "\\*")
            lines.append(f"  _← {safe_raw}_")

    return "\n".join(lines)


def get_all_pending() -> list[dict]:
    """Возвращает все pending-файлы со статусом 'pending'."""
    if not PENDING_DIR.exists():
        return []
    result = []
    for f in PENDING_DIR.glob("*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            if d.get("status") == "pending":
                d["_path"] = str(f)
                result.append(d)
        except Exception:
            continue
    return result


def mark_imported(content_hash: str):
    """Помечает pending-файл как импортированный."""
    path = PENDING_DIR / f"{content_hash}.json"
    if not path.exists():
        # Поиск по merged_hashes
        for f in PENDING_DIR.glob("*.json"):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
                if content_hash in d.get("merged_hashes", []):
                    path = f
                    break
            except Exception:
                continue
    if path.exists():
        d = json.loads(path.read_text(encoding="utf-8"))
        d["status"] = "imported"
        d["imported_at"] = get_now(timezone.utc).isoformat()
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)


def mark_rejected(content_hash: str, reason: str = ""):
    """Помечает pending-файл как отклонённый."""
    path = PENDING_DIR / f"{content_hash}.json"
    if path.exists():
        d = json.loads(path.read_text(encoding="utf-8"))
        d["status"] = "rejected"
        d["rejected_at"] = get_now(timezone.utc).isoformat()
        d["reject_reason"] = reason
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
