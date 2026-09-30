"""tests/unit/test_symptom_chronology.py — Ф4.5-B v2 (2026-07-07): квалификатор хронологии.

Заменяет сырой счётчик рецидива (availability-bias) на иерархию severity>частота.
Ключевые: red-flag всегда всплывает; хронически-стабильное подавлено; счёт по датам не строкам.
Даты относительны today → детерминизм без заморозки часов.
"""
from __future__ import annotations
from datetime import date, timedelta
import pytest

pytestmark = pytest.mark.unit


def _ins(conn, value, days_ago):
    d = (date.today() - timedelta(days=days_ago)).isoformat()
    conn.execute("INSERT INTO memory_facts (mem_class, value, valid_from, active, temporal_class) "
                 "VALUES ('state', ?, ?, 1, 'transient')", (value, d))


def test_lexicon_editable_via_db_no_deploy(db):
    """Правка лексикона в system_config (БД) меняет поведение БЕЗ деплоя — суть фикса
    «параметры не в коде». Заменяем red-flag список → старый терм больше не тревожный."""
    import config_db
    import memory_salience as ms
    config_db.upsert_config("memory_lexicon.red_flag_terms",
                            value_json=["боль в животе"], category="memory_lexicon")
    assert ms.is_red_flag("сильная боль в животе"), "новый терм из БД не подхватился"
    assert not ms.is_red_flag("боль в груди"), "код-дефолт не заменён БД-значением"


def test_red_flag_always_surfaces_even_once(db):
    """severity бьёт frequency: тревожный симптом ×1 всплывает."""
    import patient_context as pc
    import health_db
    with health_db.get_conn() as c:
        _ins(c, "сегодня была боль в груди", 1)
    assert "red-flag" in pc.symptom_chronology().lower()


def test_chronic_stable_suppressed(db):
    """Анти-availability-bias (главный): симптом на уровне базы НЕ всплывает как паттерн."""
    import patient_context as pc
    import health_db
    with health_db.get_conn() as c:
        for i in (2, 10, 20):                 # recent: 3 дня
            _ins(c, "изжога после еды", i)
        for i in range(40, 175, 9):           # baseline: ~15 дат (rate ~3/30д)
            _ins(c, "изжога вечером", i)
    assert "изжог" not in pc.symptom_chronology()


def test_counts_by_date_not_rows(db):
    """5 строк одного дня = 1 событие (многословие диалога не раздувает частоту)."""
    import patient_context as pc
    import health_db
    with health_db.get_conn() as c:
        _ins(c, "простуда началась", 90)      # baseline-данные (другой симптом-день) есть
        for _ in range(5):                    # 5 строк ОДНОГО recent-дня
            _ins(c, "изжога", 3)
    out = pc.symptom_chronology()
    assert "1 дн" in out and "5 дн" not in out


def test_negation_not_counted(db):
    import patient_context as pc
    import health_db
    with health_db.get_conn() as c:
        _ins(c, "изжоги нет, всё хорошо", 1)
    assert "изжог" not in pc.symptom_chronology()


def test_absence_list_negation_not_red_flag(db):
    """Придуманный список с отрицанием: отсутствие тревожных признаков
    не должно порождать red-flag."""
    import patient_context as pc
    import health_db
    with health_db.get_conn() as c:
        _ins(c, "Отсутствие зуда, обмороков и жжения в учебной записи", 2)
    assert "red-flag" not in pc.symptom_chronology().lower()


def test_stated_recurrence(db):
    """Явная хронология из слов пользователя («опять») → рецидив заявлен."""
    import patient_context as pc
    import health_db
    with health_db.get_conn() as c:
        _ins(c, "опять изжога сегодня", 1)
    assert "заявлен" in pc.symptom_chronology()


def test_above_baseline_surfaces(db):
    """recent много выше редкой базы → «чаще обычного»."""
    import patient_context as pc
    import health_db
    with health_db.get_conn() as c:
        for i in (2, 5, 8, 12, 20):           # recent 5 дней
            _ins(c, "диарея", i)
        for i in (60, 120):                   # baseline 2 (редко)
            _ins(c, "диарея", i)
    out = pc.symptom_chronology()
    assert "диаре" in out and "чаще обычного" in out


def test_metrics_not_counted_as_symptom(db):
    """Метрики (HRV/сон/readiness) — не симптомы: строка чисел не всплывает как хронология.
    Мигрировано из test_recurring_symptoms (RST-риск «в»), выведенного при рефакторе."""
    import patient_context as pc
    import health_db
    with health_db.get_conn() as c:
        for i in (1, 2, 3, 4, 5):
            _ins(c, "HRV 29ms deep sleep 53m readiness 80", i)
    out = pc.symptom_chronology()
    assert "HRV" not in out and "readiness" not in out


def test_surfaced_in_reasoning_block(db):
    """Потребитель реально видит хронологию в reasoning_block (анти-detection-without-delivery).
    Мигрировано из test_recurring_symptoms под новый заголовок «СИМПТОМЫ — хронология»."""
    import patient_context as pc
    import health_db
    with health_db.get_conn() as c:
        _ins(c, "сегодня была боль в груди", 1)   # red-flag → всегда всплывает
    rb = pc.reasoning_block()
    assert "СИМПТОМЫ — хронология" in rb and "red-flag" in rb.lower()
