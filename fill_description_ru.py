#!/usr/bin/env python3.11
"""
fill_description_ru.py
Заполняет genetic_variants.description_ru русскими описаниями через Claude Haiku.
Обрабатывает уникальные (gene, conditions) пары пакетами по BATCH_SIZE.

Использует hai_core.get_model / get_client (нет литералов выбора модели).
Позиционный матчинг ответов — не полагаемся на точное воспроизведение
conditions-строки от модели.
"""
import llm_client
import sys
import json
import time
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import health_db as _hdb
import hai_core

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

BATCH_SIZE = 50

SIGNIFICANCE_FILTER = (
    "Pathogenic", "Likely_pathogenic", "Pathogenic/Likely_pathogenic",
    "Likely pathogenic", "Pathogenic, low penetrance"
)

SYSTEM_PROMPT = (
    "Ты медицинский редактор. Тебе дан JSON-массив объектов с полями gene и conditions. "
    "Для каждого объекта верни то же самое, добавив поле description_ru — "
    "одно короткое описание на русском (≤25 слов, без HGVS-нотации, без геномных кодов, "
    "понятным языком для пациента). "
    "Верни РОВНО СТОЛЬКО объектов сколько получил, В ТОМ ЖЕ ПОРЯДКЕ. "
    "Ответ: ТОЛЬКО валидный JSON-массив, без markdown, без пояснений."
)


def _call_haiku(pairs: list[dict]) -> list[str]:
    """Возвращает список description_ru той же длины что pairs (позиционно)."""
    client = hai_core.get_client()
    model = hai_core.get_model("haiku_pinned")
    resp = client.messages.create(task="fill_description_ru._call_haiku",
        model=model,
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": json.dumps(pairs, ensure_ascii=False)}],
    )
    text = llm_client.answer_text(resp).strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])
    results = json.loads(text)
    return [r.get("description_ru", "").strip() for r in results]


def run():
    conn = _hdb.get_conn()

    placeholders = ",".join("?" for _ in SIGNIFICANCE_FILTER)
    rows = conn.execute(f"""
        SELECT DISTINCT gene, COALESCE(conditions, '') AS conditions
        FROM genetic_variants
        WHERE significance IN ({placeholders})
          AND gene IS NOT NULL
          AND description_ru IS NULL
        ORDER BY gene, conditions
    """, SIGNIFICANCE_FILTER).fetchall()

    total = len(rows)
    log.info("Нужно заполнить %d уникальных пар (gene, conditions)", total)
    if total == 0:
        log.info("Всё уже заполнено — выход.")
        return

    batches = [rows[i:i + BATCH_SIZE] for i in range(0, total, BATCH_SIZE)]
    log.info("Батчей: %d (по %d)", len(batches), BATCH_SIZE)

    total_updated = 0
    errors = 0

    for batch_idx, batch in enumerate(batches, 1):
        pairs = [{"gene": r[0], "conditions": r[1]} for r in batch]
        log.info("Батч %d/%d (%d пар)...", batch_idx, len(batches), len(pairs))
        try:
            descriptions = _call_haiku(pairs)
            if len(descriptions) != len(batch):
                log.warning(
                    "  ⚠ Haiku вернул %d элементов вместо %d — пропускаем батч",
                    len(descriptions), len(batch)
                )
                errors += 1
                continue
            batch_updated = 0
            for (gene, conditions), desc in zip(batch, descriptions):
                if not desc:
                    continue
                cur = conn.execute(
                    "UPDATE genetic_variants SET description_ru=? "
                    "WHERE gene=? AND COALESCE(conditions,'')=?",
                    (desc, gene, conditions),
                )
                batch_updated += cur.rowcount
            conn.commit()
            total_updated += batch_updated
            log.info("  ✓ rowcount=%d обновлено в DB", batch_updated)
        except Exception as exc:
            errors += 1
            log.error("  ✗ Батч %d ошибка: %s", batch_idx, exc)
        time.sleep(0.3)

    conn.close()
    log.info(
        "Готово: %d строк обновлено, ошибок батчей: %d из %d",
        total_updated, errors, len(batches)
    )


if __name__ == "__main__":
    run()
