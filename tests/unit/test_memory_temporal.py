"""tests/unit/test_memory_temporal.py — Ф1 (2026-07-07): временнáя семантика памяти.

Проверяет деривацию temporal_class из структуры и наличие правила
абсолютного времени в промпте. Все заметки ниже придуманы для теста.
"""
from __future__ import annotations
import pytest

pytestmark = pytest.mark.unit

import memory_facts_db as mf


def test_dated_observation_is_transient():
    """Привязка к конкретной дате/ночи → разовое событие → тускнеет."""
    assert mf._derive_temporal_class("2040-04-09: Учебный датчик потерял отметку начала", 0) == "transient"
    assert mf._derive_temporal_class("Day 12 stress spike", 0) == "transient"
    assert mf._derive_temporal_class("14 февраля: score 71", 0) == "transient"


def test_frozen_relative_day_is_transient():
    """Легаси с замороженным «сегодня/вчера» → transient (момент), не standing.
    Ловит стухшие относительные заметки до Ф4-миграции."""
    assert mf._derive_temporal_class("Сон сегодня: Учебный датчик потерял отметку начала", 0) == "transient"
    assert mf._derive_temporal_class("вчера плохо спал", 0) == "transient"
    # «сейчас» НЕ берём — может быть стоячий статус
    assert mf._derive_temporal_class("на поддерживающей диете сейчас", 0) == "standing"


def test_critical_is_durable():
    """critical_flag (генетика/анатомия/статус) → durable, никогда не тускнет."""
    assert mf._derive_temporal_class("FTO variant", 1) == "durable"


def test_plain_status_defaults_standing():
    """Без даты и не critical → консервативный дефолт standing (не тускнеть ошибочно)."""
    assert mf._derive_temporal_class("User prefers flexible sleep schedule", 0) == "standing"
    assert mf._derive_temporal_class("на поддерживающей диете", 0) == "standing"


def test_arbiter_prompt_has_time_binding():
    """Сторож: промпт запрещает морозить относительное время."""
    import hai_chat
    p = hai_chat.ARBITER_PROMPT.lower()
    assert "абсолютное" in p and "не оставляй" in p, "исчезло правило время-биндинга"
    assert "протух" in p, "исчезло обоснование (относительное время протухает)"


def _fake_states(monkeypatch, rows):
    import memory_facts_db as mf
    monkeypatch.setattr(mf, "get_facts", lambda *a, **k: rows)


def test_recent_notes_transient_to_history_not_current(monkeypatch):
    """Синтетический контроль: датированное разовое событие 3 дня назад → ИСТОРИЯ,
    не «текущий контекст», и со штампом возраста. Standing → в текущий."""
    import patient_context as pc
    from datetime import date, timedelta
    d3 = (date.today() - timedelta(days=3)).isoformat()
    _fake_states(monkeypatch, [
        {"value": "2040-04-09: учебный датчик: сбой отметки времени", "valid_from": d3,
         "temporal_class": "transient", "critical_flag": 0},
        {"value": "на поддерживающей диете", "valid_from": d3, "temporal_class": "standing", "critical_flag": 0},
    ])
    out = pc.recent_notes()
    assert "3 дн. назад" in out, "нет штампа возраста"
    h = out.lower().index("разовые события")
    assert "сбой отметки времени" in out[h:], "transient не в истории"
    assert "на поддерживающей диете" in out[:h], "standing не в текущем контексте"


def test_recent_notes_null_class_dated_derived_to_history(monkeypatch):
    """Немигрированная (temporal_class=NULL) датированная заметка → класс деривится на лету
    → уходит в историю (работает ДО Ф4-миграции)."""
    import patient_context as pc
    from datetime import date, timedelta
    d2 = (date.today() - timedelta(days=2)).isoformat()
    _fake_states(monkeypatch, [
        {"value": "2040-04-09: Учебный датчик продублировал отметку времени", "valid_from": d2,
         "temporal_class": None, "critical_flag": 0},
    ])
    out = pc.recent_notes()
    assert "разовые события" in out.lower() and "Учебный датчик" in out


def test_transient_beyond_ttl_dropped(monkeypatch):
    """Ф3: transient старше TTL уходит из подачи; в пределах TTL — остаётся."""
    import patient_context as pc
    from datetime import date, timedelta
    old = (date.today() - timedelta(days=10)).isoformat()
    fresh = (date.today() - timedelta(days=2)).isoformat()
    _fake_states(monkeypatch, [
        {"value": "старое разовое", "valid_from": old, "temporal_class": "transient", "critical_flag": 0},
        {"value": "свежее разовое", "valid_from": fresh, "temporal_class": "transient", "critical_flag": 0},
    ])
    out = pc.recent_notes(days=14, transient_ttl_days=7)
    assert "свежее разовое" in out
    assert "старое разовое" not in out, "transient старше TTL не отсеян"


def test_route_notes_transient_never_in_current():
    """A-t2: чистая маршрутизация — transient НИКОГДА не в current (инвариант датчика)."""
    import patient_context as pc
    from datetime import date, timedelta
    d2 = (date.today() - timedelta(days=2)).isoformat()
    states = [
        {"value": "разовое событие", "valid_from": d2, "temporal_class": "transient", "critical_flag": 0},
        {"value": "статус", "valid_from": d2, "temporal_class": "standing", "critical_flag": 0},
    ]
    current, history = pc._route_notes(states, 7)
    assert all(eff != "transient" for eff, _ in current), "transient просочился в current"
    assert any(eff == "transient" for eff, _ in history)


def test_route_notes_monotonic_dropped_stays_dropped():
    """A-t3 монотонность: transient старше TTL не в подаче и не воскресает при повторе."""
    import patient_context as pc
    from datetime import date, timedelta
    old = (date.today() - timedelta(days=20)).isoformat()
    states = [{"value": "старое разовое", "valid_from": old, "temporal_class": "transient", "critical_flag": 0}]
    for _ in range(3):
        current, history = pc._route_notes(states, 7)
        assert not current and not history, "стухший transient всплыл (немонотонно)"


def test_route_notes_standing_durable_survive_old():
    """A-t3 never-invariant: standing/durable в current даже старые."""
    import patient_context as pc
    from datetime import date, timedelta
    old = (date.today() - timedelta(days=200)).isoformat()
    states = [
        {"value": "на поддерживающей диете", "valid_from": old, "temporal_class": "standing", "critical_flag": 0},
        {"value": "FTO variant", "valid_from": old, "temporal_class": "durable", "critical_flag": 1},
    ]
    current, _ = pc._route_notes(states, 7)
    vals = " ".join(l for _, l in current)
    assert "поддерживающей" in vals and "FTO" in vals


def test_save_fact_dedups_exact_value(db):
    """A-t1: одинаковый state дважды → одна active-запись (прежняя суперсиднута)."""
    import memory_facts_db as mf
    import health_db
    mf.save_fact("state", "одно и то же событие 2040-04-09")
    mf.save_fact("state", "одно и то же событие 2040-04-09")
    with health_db.get_conn() as conn:
        n = conn.execute("SELECT COUNT(*) FROM memory_facts WHERE mem_class='state' "
                         "AND value='одно и то же событие 2040-04-09' AND active=1").fetchone()[0]
    assert n == 1, f"дедуп не сработал: {n} активных дублей"


def test_apply_confirmations_idempotent(db, monkeypatch):
    """B1: подтверждённый state → confirmations=1, повторный прогон НЕ инкрементит (идемпотентно)."""
    import memory_truthcheck as mt
    import health_db
    with health_db.get_conn() as conn:
        rid = conn.execute("INSERT INTO memory_facts (mem_class, value, confirmations, active) "
                           "VALUES ('state', 'подтверждаемое', 0, 1)").lastrowid
    monkeypatch.setattr(mt, "run_shadow", lambda: {"confirm": [(rid, 0, None, "x")], "disagree": []})
    assert mt.apply_confirmations()["bumped"] == 1
    monkeypatch.setattr(mt, "run_shadow", lambda: {"confirm": [(rid, 1, None, "x")], "disagree": []})
    assert mt.apply_confirmations()["bumped"] == 0
    with health_db.get_conn() as conn:
        assert conn.execute("SELECT confirmations FROM memory_facts WHERE id=?", (rid,)).fetchone()[0] == 1


def test_frozen_relative_sensor_warns(db):
    """Датчик incident-сигнатуры: state с «сегодня» но не transient → warn."""
    import health_db
    with health_db.get_conn() as conn:
        conn.execute("INSERT INTO memory_facts (mem_class, value, temporal_class, active) "
                     "VALUES ('state', 'Сон сегодня: всё ок', 'standing', 1)")
    import integrity_tests as it
    it._warnings.clear()
    it.check_frozen_relative_not_current()
    assert any("замороженное" in w[0] for w in it._warnings), "датчик не поймал frozen-relative не-transient"


def test_temporal_coverage_sensor_warns_on_null(db):
    """Датчик покрытия ловит active state с NULL temporal_class (писатель обошёл деривацию).
    Это тот регресс-класс, что the-end поймал руками — теперь под живым сторожем."""
    import health_db
    with health_db.get_conn() as conn:
        conn.execute("INSERT INTO memory_facts (mem_class, value, temporal_class, active) "
                     "VALUES ('state', 'бесклассовая заметка', NULL, 1)")
    import integrity_tests as it
    it._warnings.clear()
    it.check_temporal_class_coverage()
    assert any("temporal_class не проставлен" in w[0] for w in it._warnings), \
        "датчик не поймал NULL temporal_class"


def test_ttl_never_drops_standing_or_durable(monkeypatch):
    """Ф3 never-invariant (RST): standing и durable НЕ отсеиваются по возрасту, даже старые.
    Главный риск плана — over-aggressive TTL съедает стоячий статус/генетику."""
    import patient_context as pc
    from datetime import date, timedelta
    old = (date.today() - timedelta(days=90)).isoformat()
    _fake_states(monkeypatch, [
        {"value": "на поддерживающей диете", "valid_from": old, "temporal_class": "standing", "critical_flag": 0},
        {"value": "FTO variant", "valid_from": old, "temporal_class": "durable", "critical_flag": 1},
    ])
    out = pc.recent_notes(days=365, transient_ttl_days=7)
    assert "на поддерживающей диете" in out, "standing отсеян по возрасту (нарушен never-invariant)"
    assert "FTO" in out, "durable отсеян по возрасту"
