import llm_client
#!/usr/bin/env python3.11
"""lab_schedule_extractor.py — извлечение расписания мониторинга из encounter.plan."""

import json, logging
import hai_core
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent))
import health_db as db

log = logging.getLogger(__name__)

EXTRACTION_PROMPT = """Ты — медицинский аналитик. Из плана врача извлеки упоминания
регулярного мониторинга лабораторных показателей с указанием частоты повторений.

Каноническое имя теста (только эти): CEA, CA19-9, CA125, HGB, WBC, PLT, MCV, LDH,
Amylase, Lipase, Albumin, Creatinine, ALT, AST, GGT, CRP, Glucose, Ferritin,
Cholesterol, Triglycerides, LDL, HDL, B12, Folate, VitaminD, TSH, HbA1c

Правила:
- Только повторяющийся мониторинг с явной частотой
- "сдать разово/сейчас" — НЕ извлекать
- "каждые 3-4 месяца" → 105, "раз в квартал"/"every 3 months" → 90
- "раз в месяц" → 30, "два раза в год" → 182, "ежегодно" → 365
- "опухолевые маркеры" = CEA + CA19-9 (не CA125 для мужчин, если не указано явно)
- Приоритет: critical (CEA, CA19-9), high (HGB, WBC, PLT, LDH), medium (остальные)

Ответь ТОЛЬКО JSON-массивом (или [] если нет):
[{"test": "CEA", "interval_days": 90, "priority": "critical", "note": "маркеры каждые 3 мес"}]"""


def _get_client():
    from anthropic import Anthropic

    return llm_client.guarded_client()


def extract_monitoring_rules(plan_text: str, event_id: int) -> list:
    if not plan_text or not plan_text.strip():
        return []
    try:
        client = _get_client()
        response = client.messages.create(task="lab_schedule_extractor.extract_monitoring_rules",
            model=hai_core.get_model("haiku_pinned"),
            max_tokens=400,
            system=EXTRACTION_PROMPT,
            messages=[{"role": "user", "content": f"ПЛАН ВРАЧА:\n{plan_text}"}],
        )
        raw = llm_client.answer_text(response).strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"): raw = raw[4:]
        return json.loads(raw)
    except Exception as e:
        log.warning(f"extract_monitoring_rules(event_id={event_id}): {e}")
        return []


def process_encounter_plan(event_id: int, plan_text: str, effective_date: str) -> int:
    """Извлечь правила из плана и сохранить в БД. Возвращает кол-во правил."""
    rules = extract_monitoring_rules(plan_text, event_id)
    applied = 0
    for rule in rules:
        test = rule.get("test", "").strip()
        interval = rule.get("interval_days")
        if not test or not interval:
            continue
        priority = rule.get("priority", "medium")
        note = rule.get("note", "")
        db.upsert_monitoring_rule(
            test_name=test,
            interval_days=int(interval),
            priority=priority,
            source=f"encounter:{event_id}",
            effective_from=effective_date,
            note=note,
        )
        applied += 1
        log.info(f"  monitoring rule: {test} every {interval}d from encounter #{event_id}")
    return applied


def extract_all_encounter_plans() -> dict:
    """Ретроспективный прогон по всем encounter plans (хронологически)."""
    with db.get_conn() as conn:
        rows = conn.execute("""
            SELECT e.id, e.effective_date, enc.plan
            FROM events e JOIN encounters enc ON enc.event_id = e.id
            WHERE enc.plan IS NOT NULL AND enc.plan != ''
            ORDER BY e.effective_date ASC
        """).fetchall()
    total = 0
    for row in rows:
        applied = process_encounter_plan(row["id"], row["plan"], row["effective_date"])
        total += applied
        print(f"  encounter #{row['id']} ({row['effective_date']}): {applied} правил")
    return {"encounters": len(rows), "rules_applied": total}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    db.init_db()
    print("Ретроспективная экстракция...")
    result = extract_all_encounter_plans()
    print(f"\nИтог: {result}")
    print("\nИтоговое расписание:")
    for test, cfg in sorted(db.get_effective_lab_schedule().items()):
        print(f"  {test:20} {cfg['interval_days']:4}д [{cfg['priority']:8}] {cfg['source']}")
