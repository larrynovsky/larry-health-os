"""Sprint 4b unit-тесты для DB config API (Sprint 2 / Р-1 миграции).

Покрывает:
  - absolute_thresholds: миграция, seed, get_absolute_thresholds
  - routing_keywords: миграция, seed, get_routing_keywords
  - lab_monitoring_schedule: _seed_data_freshness idempotency
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


# ── absolute_thresholds ────────────────────────────────────────────────────

def test_absolute_thresholds_migration_creates_table(db):
    """_migrate_absolute_thresholds создаёт таблицу + индекс."""
    import health_db
    health_db._migrate_absolute_thresholds()
    rows = db.fetchall(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='absolute_thresholds'"
    )
    assert len(rows) == 1


def test_absolute_thresholds_seed_populates_records(db):
    """Seed создаёт 11 floors (6 absolute: hrv/readiness/sleep_deep/sleep_score/sleep_total/spo2
    + 5 relative/singles: hrv×3, steps×2) + 3 ceilings (bp_sys/bp_dia + персонализируемый sleep_awake)."""
    import health_db
    health_db._migrate_absolute_thresholds()
    health_db._seed_absolute_thresholds()
    floors = db.fetchall("SELECT * FROM absolute_thresholds WHERE direction='floor'")
    ceilings = db.fetchall("SELECT * FROM absolute_thresholds WHERE direction='ceiling'")
    assert len(floors) == 11
    assert len(ceilings) == 3


def test_absolute_thresholds_seed_is_idempotent(db):
    """Двойной seed не дублирует записи (INSERT OR IGNORE по UNIQUE)."""
    import health_db
    health_db._migrate_absolute_thresholds()
    health_db._seed_absolute_thresholds()
    health_db._seed_absolute_thresholds()
    health_db._seed_absolute_thresholds()
    rows = db.fetchall("SELECT * FROM absolute_thresholds")
    assert len(rows) == 14


def test_get_absolute_thresholds_returns_floors_only(db):
    """direction='floor' возвращает только полы."""
    import health_db
    health_db._migrate_absolute_thresholds()
    health_db._seed_absolute_thresholds()
    floors = health_db.get_absolute_thresholds("floor")
    assert len(floors) == 11
    assert all(f["direction"] == "floor" for f in floors)
    metrics = {f["metric"] for f in floors}
    assert metrics == {"hrv", "readiness", "sleep_deep", "sleep_score", "sleep_total", "steps", "spo2"}


def test_get_absolute_thresholds_returns_ceilings_only(db):
    import health_db
    health_db._migrate_absolute_thresholds()
    health_db._seed_absolute_thresholds()
    ceilings = health_db.get_absolute_thresholds("ceiling")
    assert len(ceilings) == 3
    metrics = {c["metric"] for c in ceilings}
    assert metrics == {"bp_systolic_max", "bp_diastolic_max", "sleep_awake"}  # пик дня (26.09)


def test_get_absolute_thresholds_no_filter_returns_all(db):
    import health_db
    health_db._migrate_absolute_thresholds()
    health_db._seed_absolute_thresholds()
    all_thr = health_db.get_absolute_thresholds()
    assert len(all_thr) == 14   # 11 floors + 3 ceilings


def test_get_absolute_thresholds_ignores_inactive(db):
    """active=0 → не попадает в результат."""
    import health_db
    health_db._migrate_absolute_thresholds()
    health_db._seed_absolute_thresholds()
    with db.conn() as c:
        c.execute("UPDATE absolute_thresholds SET active=0 WHERE metric='hrv'")
    floors = health_db.get_absolute_thresholds("floor")
    assert len(floors) == 7  # readiness, sleep_deep, sleep_score, sleep_total, steps×2, spo2; hrv (4 строки) деактивированы
    assert "hrv" not in {f["metric"] for f in floors}


# ── routing_keywords ───────────────────────────────────────────────────────

def test_routing_keywords_migration_creates_table(db):
    import health_db
    health_db._migrate_routing_keywords()
    rows = db.fetchall(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='routing_keywords'"
    )
    assert len(rows) == 1


def test_routing_keywords_seed_populates_all_domains(db):
    """Seed создаёт keywords для всех 10 domains (sleep/genome/stress/.../__full__)."""
    import health_db
    health_db._migrate_routing_keywords()
    health_db._seed_routing_keywords()
    rows = db.fetchall("SELECT DISTINCT domain FROM routing_keywords")
    domains = {r["domain"] for r in rows}
    expected = {"sleep", "genome", "stress", "energy", "movement",
                "labs", "profile", "memory", "problems", "__full__"}
    assert domains == expected


def test_routing_keywords_seed_idempotent(db):
    """Двойной seed → no duplicates."""
    import health_db
    health_db._migrate_routing_keywords()
    health_db._seed_routing_keywords()
    n1 = db.count("routing_keywords")
    health_db._seed_routing_keywords()
    health_db._seed_routing_keywords()
    n2 = db.count("routing_keywords")
    assert n1 == n2 == 110  # 10 domains × variable keywords = 110 total


def test_get_routing_keywords_returns_grouped_dict(db):
    """get_routing_keywords возвращает {domain: [kw, ...]}."""
    import health_db
    health_db._migrate_routing_keywords()
    health_db._seed_routing_keywords()
    kws = health_db.get_routing_keywords()
    assert "sleep" in kws
    assert "__full__" in kws
    assert isinstance(kws["sleep"], list)
    assert "сон" in kws["sleep"]
    assert "deep" in kws["sleep"]
    assert "что мне делать" in kws["__full__"]


def test_get_routing_keywords_empty_on_fresh_db(db):
    """Без seed возвращает пустой dict."""
    import health_db
    health_db._migrate_routing_keywords()
    kws = health_db.get_routing_keywords()
    assert kws == {}


# ── lab_monitoring_schedule (Sprint 2 step 1) ──────────────────────────────

def test_seed_data_freshness_populates_base_only(db):
    """_seed_data_freshness заливает НЕЙТРАЛЬНУЮ базу (BASE_FRESHNESS). Онкомаркеры НЕ в
    code_seed — навязали бы каденцию всем тенантам (нить diagnosis-hardcode B6). Условные
    тесты приходят per-tenant из reader (labs_db.effective_freshness по active_conditions)."""
    import health_db
    import labs_db
    health_db.init_db()  # creates lab_monitoring_schedule + seeds BASE
    sched = health_db.get_effective_lab_schedule()
    # seed = только BASE, без онкомаркеров
    assert "HGB" in sched and "ALT" in sched
    assert "CEA" not in sched, "условное правило не должно попадать в нейтральный seed"
    assert sched["ALT"]["priority"] == "medium"  # BASE-нейтраль; онко-boost — через reader
    # per-tenant reader: онко-тенант получает CEA critical + печёночный boost
    onco = labs_db.effective_freshness({"oncology"})
    assert onco["CEA"]["priority"] == "critical"
    assert onco["ALT"]["priority"] == "high"
    # Вымышленный профиль B без активного класса не получает условные правила.
    assert "CEA" not in labs_db.effective_freshness(set())


def test_seed_data_freshness_idempotent(db):
    """Повторный seed не дублирует и не перезаписывает manual правила."""
    import health_db
    health_db.init_db()
    n1 = db.count("lab_monitoring_schedule")
    # Поставим manual override для CEA с другими значениями
    health_db.upsert_monitoring_rule("CEA", interval_days=30, priority="manual_custom",
                                      source="manual")
    # Повторный seed
    health_db._seed_data_freshness()
    sched = health_db.get_effective_lab_schedule()
    assert sched["CEA"]["interval_days"] == 30  # manual выиграл
    assert sched["CEA"]["priority"] == "manual_custom"
