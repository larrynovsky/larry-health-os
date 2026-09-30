"""
tests/unit/test_memory_facts.py — Фаза 1 (2026-07-04): типизированная память.

Пины:
  save_fact/get_facts базовое; upsert по key = supersede (valid_to, не delete);
  get_profile отдаёт классы; R11 — subject='third_party' исключён из профиля;
  бэкофилл-маппинг category → mem_class.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def test_save_and_get_fact(db):
    import memory_facts_db as mf
    mf.save_fact("fact", "диагноз X", key="diagnosis")
    facts = mf.get_facts("fact")
    assert any(f["value"] == "диагноз X" and f["key"] == "diagnosis" for f in facts)


def test_save_fact_rejects_confab_on_authoritative_topic(db):
    """Синтетический контроль: конфаб арбитра (source=conversation) на авторитет-топик локации
    отклоняется В ТОЧКЕ ЗАПИСИ; device_gps — пишется. Не даём родиться ложной вере."""
    import memory_facts_db as mf
    rid = mf.save_fact("fact", "Учебный город Ветрополь", key="current_location", source="conversation")
    assert rid == -1, "conversation-локация не должна записаться (конфаб)"
    ok = mf.save_fact("fact", "Учебный город Лунолесье", key="current_location", source="device_gps")
    assert ok > 0, "авторитетный источник device_gps пишется"
    vals = {(f["key"], f["value"]) for f in mf.get_facts("fact")}
    assert ("current_location", "Учебный город Лунолесье") in vals
    assert ("current_location", "Учебный город Ветрополь") not in vals, "конфаб не просочился"


def test_save_fact_allows_non_authoritative_keys_from_conversation(db):
    """Гвард узок: обычные lifestyle-факты из conversation пишутся как и раньше."""
    import memory_facts_db as mf
    rid = mf.save_fact("fact", "melatonin 5mg", key="melatonin", source="conversation")
    assert rid > 0, "не-авторитетный ключ из conversation пишется нормально"


def test_upsert_by_key_supersedes_not_deletes(db):
    import memory_facts_db as mf
    mf.save_fact("fact", "вес 82", key="weight")
    mf.save_fact("fact", "вес 78", key="weight")
    active = mf.get_facts("fact")
    weights = [f for f in active if f["key"] == "weight"]
    assert len(weights) == 1 and weights[0]["value"] == "вес 78", "актуальна одна — новая"
    # старая не удалена, а помечена valid_to (би-темпоральность/история веса)
    import health_db
    with health_db.get_conn() as conn:
        allrows = conn.execute(
            "SELECT value, valid_to FROM memory_facts WHERE key='weight'"
        ).fetchall()
    assert len(allrows) == 2, "старая версия сохранена как история"
    assert any(r["valid_to"] is not None for r in allrows), "старая помечена valid_to"


def test_third_party_excluded_from_profile(db):
    import memory_facts_db as mf
    mf.save_fact("state", "я плохо сплю", key=None, subject="self")
    mf.save_fact("state", "партнёр плохо спит", key=None, subject="third_party")
    prof = mf.get_profile()
    vals = [s["value"] for s in prof["states"]]
    assert "я плохо сплю" in vals
    assert "партнёр плохо спит" not in vals, "R11: чужой факт не в профиле"


def test_get_profile_shape(db):
    import memory_facts_db as mf
    mf.save_fact("fact", "f", key="k")
    mf.save_fact("question", "открытый вопрос?")
    prof = mf.get_profile()
    assert set(prof) == {"facts", "states", "preferences", "open_questions"}
    assert any(q["value"] == "открытый вопрос?" for q in prof["open_questions"])


def test_invalid_class_raises(db):
    import memory_facts_db as mf
    with pytest.raises(ValueError):
        mf.save_fact("garbage", "x")


def test_chat_context_surfaces_facts_not_states(db):
    """R3 payoff: после консолидации бриф ВИДИТ facts+вопросы+рекомендации;
    шумные states НЕ выводятся; чужие факты исключены (R11)."""
    import memory_facts_db as mf
    import patient_context as pc
    mf.save_fact("question", "что с железом?")
    mf.save_fact("recommendation", "[supplement] магний 400мг", key="supplement")
    mf.save_fact("fact", "none", key="alcohol_consumption")               # чистый факт
    mf.save_fact("state", "readiness 82")                                 # шумный state
    mf.save_fact("fact", "партнёрский факт", key="x", subject="third_party")
    line = pc._chat_context_line()
    assert "что с железом?" in line
    assert "магний 400мг" in line
    assert "alcohol_consumption: none" in line, "R3: чистые facts теперь в брифе"
    assert "readiness 82" not in line, "шумные states не в бриф"
    assert "партнёрский факт" not in line, "R11: чужой факт не в бриф"


def test_daily_report_reads_data_quality_notes(db):
    """Придуманная заметка об ошибке прибора. _recent_chat_notes
    вытаскивает свежие states, ОСОБЕННО про качество данных, для генератора отчёта."""
    import memory_facts_db as mf
    mf.save_fact("state", "Учебный датчик записал интервал с ошибкой — потерял отметку окончания")
    mf.save_fact("state", "чужая заметка", subject="third_party")
    import gp_agent
    notes = gp_agent._recent_chat_notes(None)
    assert "Учебный датчик" in notes and "ошибкой" in notes
    assert "чужая заметка" not in notes, "R11: чужое не в отчёт"
    assert "не доверяй" in notes.lower() or "учитывай" in notes.lower()


def test_fmt_fact_renders_json_facets(db):
    """Канонический факт (value=JSON) рендерится как читаемые фасеты, не сырой JSON."""
    import patient_context as pc
    line = pc._fmt_fact("stimdev", '{"current_duration_min": 15, "current_power": 10}')
    assert "stimdev:" in line
    assert "current_duration_min=15" in line and "current_power=10" in line
    assert "{" not in line, "JSON развёрнут в фасеты, не сырой"


def test_chat_context_recency_filters_stale_questions(db):
    """Свежесть: устаревшие открытые вопросы (>45д) не попадают в бриф консилиума."""
    import memory_facts_db as mf
    import patient_context as pc
    import health_db
    mf.save_fact("question", "свежий вопрос?")
    with health_db.get_conn() as conn:
        conn.execute(
            "INSERT INTO memory_facts (mem_class, value, valid_from) "
            "VALUES ('question', 'древний вопрос до 12 апреля?', date('now','-200 days'))"
        )
    line = pc._chat_context_line()
    assert "свежий вопрос?" in line
    assert "древний вопрос" not in line, "устаревшие вопросы (>45д) не в бриф"


def test_backfill_mapping_and_idempotent(db):
    """Бэкофилл копирует разговорные категории, не трогает hypothesis, идемпотентен."""
    import health_db
    with health_db.get_conn() as conn:
        conn.executescript(
            "INSERT INTO memory (category, key, value) VALUES "
            "('profile_update','diagnosis','X'),"
            "('observation',NULL,'спал плохо'),"
            "('open_question',NULL,'что с железом?'),"
            "('hypothesis','h1','гипотеза');"
        )
    import importlib.util
    from pathlib import Path
    p = Path(health_db.__file__).parent / "scripts" / "2026_07_04_memory_facts_backfill.py"
    spec = importlib.util.spec_from_file_location("mf_backfill", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    assert mod.main(apply=True) == 0
    import memory_facts_db as mf
    facts = mf.get_facts("fact")
    states = mf.get_facts("state")
    questions = mf.get_facts("question")
    assert any(f["value"] == "X" for f in facts)
    assert any(s["value"] == "спал плохо" for s in states)
    assert any(q["value"] == "что с железом?" for q in questions)
    # hypothesis НЕ мигрировала
    with health_db.get_conn() as conn:
        n_exp = conn.execute(
            "SELECT COUNT(*) FROM memory_facts WHERE mem_class='experiment'"
        ).fetchone()[0]
        n_hyp = conn.execute(
            "SELECT COUNT(*) FROM memory_facts WHERE value='гипотеза'"
        ).fetchone()[0]
    assert n_hyp == 0, "hypothesis не должна попасть в memory_facts"
    # идемпотентность: второй прогон не дублирует
    before = len(mf.get_facts("fact"))
    mod.main(apply=True)
    assert len(mf.get_facts("fact")) == before, "повторный apply не дублирует"
