"""
tests/integration/test_lab_monitoring_schedule.py

Покрывает новый функционал (коммит 23b175f):
  - lab_monitoring_schedule таблица: upsert + приоритет (manual > encounter > default)
  - get_effective_lab_schedule
  - gp_agent._check_lab_freshness читает из DB-расписания
  - lab_schedule_extractor.extract_monitoring_rules с мокнутым Anthropic
  - gp_agent._build_gp_context включает plan из encounter
"""
from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

pytestmark = pytest.mark.integration


# ── helpers ───────────────────────────────────────────────────────────────────

def _seed_rule(test_name: str, days: int, priority: str = "medium",
               source: str = "default") -> None:
    import health_db as db
    db.upsert_monitoring_rule(test_name, days, priority, source, None, None)


def _get_schedule() -> dict:
    import health_db as db
    return db.get_effective_lab_schedule()


# ── 1. Базовый upsert ─────────────────────────────────────────────────────────

def test_upsert_creates_rule(db):
    _seed_rule("CEA", 90, "critical", "default")
    sched = _get_schedule()
    assert "CEA" in sched
    assert sched["CEA"]["interval_days"] == 90
    assert sched["CEA"]["priority"] == "critical"
    assert sched["CEA"]["source"] == "default"


def test_upsert_updates_same_source(db):
    _seed_rule("CEA", 90, "critical", "default")
    _seed_rule("CEA", 120, "high", "default")  # обновление default → ok
    sched = _get_schedule()
    assert sched["CEA"]["interval_days"] == 120


# ── 2. Приоритет: manual > encounter > default ────────────────────────────────

def test_default_does_not_overwrite_encounter(db):
    """encounter-правило не перезаписывается дефолтом."""
    import health_db as db_mod
    db_mod.upsert_monitoring_rule("CA19-9", 105, "critical", "encounter:17", None, None)
    db_mod.upsert_monitoring_rule("CA19-9", 90, "critical", "default", None, None)
    sched = _get_schedule()
    assert sched["CA19-9"]["source"] == "encounter:17"
    assert sched["CA19-9"]["interval_days"] == 105


def test_default_does_not_overwrite_manual(db):
    """manual-правило не перезаписывается дефолтом."""
    import health_db as db_mod
    db_mod.upsert_monitoring_rule("CA125", 120, "low", "manual", None, None)
    db_mod.upsert_monitoring_rule("CA125", 90, "high", "default", None, None)
    sched = _get_schedule()
    assert sched["CA125"]["source"] == "manual"
    assert sched["CA125"]["interval_days"] == 120


def test_encounter_does_not_overwrite_manual(db):
    """manual-правило не перезаписывается encounter."""
    import health_db as db_mod
    db_mod.upsert_monitoring_rule("CA125", 120, "low", "manual", None, None)
    db_mod.upsert_monitoring_rule("CA125", 90, "high", "encounter:19", None, None)
    sched = _get_schedule()
    assert sched["CA125"]["source"] == "manual"


def test_newer_encounter_overwrites_older_encounter(db):
    """Более новый encounter перезаписывает старый (оба source='encounter:*')."""
    import health_db as db_mod
    db_mod.upsert_monitoring_rule("CEA", 90, "critical", "encounter:19", None, None)
    db_mod.upsert_monitoring_rule("CEA", 105, "critical", "encounter:17", None, None)
    # encounter vs encounter — оба допустимы к перезаписи (нет guard'а между ними)
    sched = _get_schedule()
    assert sched["CEA"]["interval_days"] == 105  # последний upsert победил


# ── 3. _check_lab_freshness использует DB-расписание ─────────────────────────

def test_check_lab_freshness_uses_db_schedule(db, clock):
    """DB-расписание имеет приоритет над DATA_FRESHNESS-дефолтом.

    Подобрано так, чтобы исход ОТЛИЧАЛСЯ от дефолта (CEA default=90д):
    DB ставит CEA=20д, лаб 30 дней назад → по DB просрочен (30>20), по дефолту
    был бы свеж (30<90). CEA попадает в overdue ТОЛЬКО если читается DB-расписание.
    Регрессия BL-GP-2 (2026-06-28): раньше `db` не импортировался в _check_lab_freshness
    → db_sched={} (NameError→except) → молча использовались дефолты. Старый ассерт
    («CEA не в overdue») проходил ИМЕННО из-за бага (дефолт 90, 30/90<0.5).
    """
    import health_db as db_mod
    import gp_agent

    clock.set("2026-05-20")
    ref = date(2026, 5, 20)
    recent_labs = [{"test_name": "CEA", "value": 2.5, "date": "2026-04-20",
                    "unit": "ng/mL", "ref_low": None, "ref_high": None,
                    "status": "normal", "notes": None}]

    # DB: CEA каждые 20 дней — лаб 30 дней назад → просрочен ТОЛЬКО по DB-расписанию.
    db_mod.upsert_monitoring_rule("CEA", 20, "critical", "default", None, None)

    freshness_block, overdue = gp_agent._check_lab_freshness(recent_labs, ref)
    overdue_names = {o["test"] for o in overdue}
    assert "CEA" in overdue_names, (
        f"CEA должен быть overdue по DB-расписанию (20д), но его нет → "
        f"читаются дефолты, не DB: {overdue}"
    )


def test_check_lab_freshness_alerts_when_overdue(db, clock):
    """Если данные старше интервала — CEA попадает в overdue."""
    import health_db as db_mod
    import gp_agent

    clock.set("2026-05-20")
    ref = date(2026, 5, 20)
    old_date = (ref - timedelta(days=100)).isoformat()
    recent_labs = [{"test_name": "CEA", "value": 2.5, "date": old_date,
                    "unit": "ng/mL", "ref_low": None, "ref_high": None,
                    "status": "normal", "notes": None}]

    # В DB: CEA каждые 60 дней → 100 > 60 → overdue
    db_mod.upsert_monitoring_rule("CEA", 60, "critical", "default", None, None)

    freshness_block, overdue = gp_agent._check_lab_freshness(recent_labs, ref)
    overdue_names = {o["test"] for o in overdue}
    assert "CEA" in overdue_names or len(freshness_block) > 0


def test_check_lab_freshness_fallback_when_no_db_rule(db, clock):
    """Если для теста нет DB-правила — используется DATA_FRESHNESS как fallback."""
    import gp_agent

    clock.set("2026-05-20")
    ref = date(2026, 5, 20)
    old_date = (ref - timedelta(days=100)).isoformat()
    # HGB 100 дней назад, DATA_FRESHNESS = 90d для HGB → должен попасть в overdue
    recent_labs = [{"test_name": "HGB", "value": 130.0, "date": old_date,
                    "unit": "g/L", "ref_low": None, "ref_high": None,
                    "status": "normal", "notes": None}]

    freshness_block, overdue = gp_agent._check_lab_freshness(recent_labs, ref)
    # overdue или блок с упоминанием HGB как устаревшего
    overdue_names = {o["test"] for o in overdue}
    assert "HGB" in overdue_names or "HGB" in freshness_block


# ── 4. _build_gp_context включает plan из encounter ──────────────────────────

def test_build_gp_context_includes_encounter_plan(db, clock):
    """Если у encounter есть plan — он появляется в GP-контексте."""
    clock.set("2026-05-20")
    event_id = db.add_event("encounter", "2026-05-10",
                             performer="Врач Иванов",
                             performer_role="physician")
    db.add_encounter(event_id,
                     assessment="Диагноз X, стабилизация",
                     plan="маркеры каждые 3 месяца, следующий визит через 3 мес")

    import gp_agent
    ctx = gp_agent._build_gp_context(date(2026, 5, 19))
    assert "ПЛАН" in ctx or "план" in ctx.lower() or "3 мес" in ctx


# ── 5. extract_monitoring_rules с мокнутым Anthropic ─────────────────────────

def test_extract_monitoring_rules_parses_json(db, anthropic_mock):
    """extract_monitoring_rules возвращает правила из корректного JSON-ответа."""
    haiku_response = json.dumps([
        {"test": "CEA",   "interval_days": 90,  "priority": "critical",
         "note": "маркер каждые 3 месяца"},
        {"test": "CA19-9","interval_days": 90,  "priority": "critical",
         "note": "маркер каждые 3 месяца"},
    ])
    anthropic_mock.script("мониторинга", haiku_response)
    anthropic_mock.script("monitoring",  haiku_response)
    anthropic_mock.script("plan",        haiku_response)

    import lab_schedule_extractor as lse
    rules = lse.extract_monitoring_rules(
        "маркеры каждые 3 месяца, следующий визит через 3 мес",
        event_id=99
    )
    assert len(rules) == 2
    names = {r["test"] for r in rules}
    assert "CEA" in names
    assert "CA19-9" in names
    assert rules[0]["interval_days"] == 90


def test_extract_monitoring_rules_returns_empty_on_bad_json(db, anthropic_mock):
    """При невалидном JSON — пустой список, не исключение."""
    anthropic_mock.script("plan", "не JSON вовсе")

    import lab_schedule_extractor as lse
    rules = lse.extract_monitoring_rules("какой-то план", event_id=99)
    assert rules == []


def test_process_encounter_plan_upserts_to_db(db, anthropic_mock):
    """process_encounter_plan сохраняет извлечённые правила в DB."""
    import health_db as db_mod
    haiku_response = json.dumps([
        {"test": "CEA", "interval_days": 105, "priority": "critical",
         "note": "из encounter 99"}
    ])
    anthropic_mock.script("план", haiku_response)
    anthropic_mock.script("plan", haiku_response)
    anthropic_mock.script("мониторинг", haiku_response)

    import lab_schedule_extractor as lse
    n = lse.process_encounter_plan(99, "маркеры каждые 3-4 месяца", "2026-05-10")
    assert n >= 1

    sched = db_mod.get_effective_lab_schedule()
    assert "CEA" in sched
    assert sched["CEA"]["source"] == "encounter:99"
    assert sched["CEA"]["interval_days"] == 105
