"""tests/unit/test_beliefs.py — B: модель веры, резолв топика (Ярус-1 checks).

Ключевой инвариант: неподтверждённый факт НИЖЕ профиля; GPS авторитетнее
разговорной догадки. География и сообщения ниже независимо придуманы.
"""
from __future__ import annotations
from datetime import date, timedelta
import pytest

pytestmark = pytest.mark.unit


def _seed_fact(key: str, value: str, days_ago: int, confirmations: int):
    import health_db
    vf = (date.today() - timedelta(days=days_ago)).isoformat()
    with health_db.get_conn() as c:
        c.execute("INSERT INTO memory_facts (mem_class, key, value, valid_from, active, confirmations) "
                  "VALUES ('fact', ?, ?, ?, 1, ?)", (key, value, vf, confirmations))


def _profile(monkeypatch, loc):
    import health_db
    monkeypatch.setattr(health_db, "get_profile_context", lambda: {"identity": {"location": loc}})


def test_profile_only(db, monkeypatch):
    _profile(monkeypatch, "Тестовая страна")
    import beliefs
    b = beliefs.resolve("location")
    assert b.value == "Тестовая страна" and b.source == "profile" and b.confirmed


def test_unconfirmed_fact_loses_to_profile(db, monkeypatch):
    """Вымышленный пример: конфабуляция current_location (confirmations=0) НИЖE профиля."""
    _profile(monkeypatch, "Тестовая страна")
    _seed_fact("current_location", "Mapleburg", days_ago=0, confirmations=0)
    import beliefs
    b = beliefs.resolve("location")
    assert b.value == "Тестовая страна", "неподтверждённый факт не должен перекрывать профиль"
    assert any(c.value == "Mapleburg" for c in b.history), "Mapleburg в demoted-истории, не стёрт"


def test_confirmed_fresh_fact_overrides_profile(db, monkeypatch):
    """Реально переехал: подтверждённый свежий факт перекрывает профиль."""
    _profile(monkeypatch, "Тестовая страна")
    _seed_fact("current_location", "Сосновск", days_ago=0, confirmations=1)
    import beliefs
    b = beliefs.resolve("location")
    assert b.value == "Сосновск" and b.confirmed


def test_monotonic_older_does_not_override(db, monkeypatch):
    """Метаморфный [distsys monotonic]: старое подтверждённое НЕ меняет при свежайшем."""
    _profile(monkeypatch, "Тестовая страна")
    _seed_fact("current_location", "Кленоград", days_ago=90, confirmations=1)
    _seed_fact("current_location", "Ельск", days_ago=1, confirmations=1)
    import beliefs
    b = beliefs.resolve("location")
    assert b.value == "Ельск", "свежайшее подтверждённое выигрывает"


def test_no_candidates(db, monkeypatch):
    import health_db
    monkeypatch.setattr(health_db, "get_profile_context", lambda: {})
    import beliefs
    b = beliefs.resolve("location")
    assert b.value is None and b.confirmed is False


def test_render_header_authoritative(db, monkeypatch):
    """Заголовок ТЕКУЩЕЙ ВЕРЫ авторитетен над транскриптом (адресует петлю без мутации истории)."""
    _profile(monkeypatch, "Тестовая страна")
    import beliefs
    h = beliefs.render_header()
    assert "ТЕКУЩАЯ ВЕРА" in h and "локация: Тестовая страна" in h and "верь ЭТОМУ" in h


def test_header_wired_into_chat_payload(db, monkeypatch):
    """B реально в промпте: собранный контекст несёт авторитетную веру, не мёртвый код."""
    _profile(monkeypatch, "Тестовая страна")
    import hai_chat
    txt = hai_chat.assembled_context_text("как я?", include_data=False)
    assert txt.count("ТЕКУЩАЯ ВЕРА") == 1, "заголовок ровно один раз (нет дубля после переноса в brief)"
    assert "локация: Тестовая страна" in txt


def _seed_state(value: str, days_ago: int, confirmations: int = 0):
    import health_db
    vf = (date.today() - timedelta(days=days_ago)).isoformat()
    with health_db.get_conn() as c:
        c.execute("INSERT INTO memory_facts (mem_class, value, valid_from, active, confirmations) "
                  "VALUES ('state', ?, ?, 1, ?)", (value, vf, confirmations))


def test_travel_default_planned(db, monkeypatch):
    _profile(monkeypatch, "Тестовая страна")
    _seed_fact("travel_plans", "Pineville trip planned", days_ago=0, confirmations=0)
    import beliefs
    assert beliefs.resolve("travel").status == "planned"


def test_travel_cancelled_from_keyless_state(db, monkeypatch):
    """Правда лежит в keyless-стейте — резолвер его читает по паттерну."""
    _profile(monkeypatch, "Тестовая страна")
    _seed_fact("travel_plans", "Pineville trip planned", days_ago=2, confirmations=0)
    _seed_state("2032-04-12 night: early wake due to cancelled flight", days_ago=0)
    import beliefs
    assert beliefs.resolve("travel").status == "cancelled"


def test_travel_unconfirmed_flew_NOT_occurred(db, monkeypatch):
    """Неподтверждённое сообщение: неподтверждённое «вылетел» НЕ ставит occurred — безопасный дефолт."""
    _profile(monkeypatch, "Тестовая страна")
    _seed_state("2032-04-12: вылетел в Сосновск рейсом 11:45", days_ago=0, confirmations=0)
    import beliefs
    assert beliefs.resolve("travel").status != "occurred"


def test_travel_confirmed_flew_occurred(db, monkeypatch):
    _profile(monkeypatch, "Тестовая страна")
    _seed_state("вылетел в Сосновск, уже на месте", days_ago=0, confirmations=1)
    import beliefs
    assert beliefs.resolve("travel").status == "occurred"


def test_header_travel_says_not_occurred(db, monkeypatch):
    _profile(monkeypatch, "Тестовая страна")
    _seed_state("cancelled flight, slept at home", days_ago=0)
    import beliefs
    h = beliefs.render_header()
    assert "НЕ состоялось" in h and "НЕ считай, что это произошло" in h


def test_belief_header_in_all_profile_readers(db, monkeypatch):
    """Reader-census guard: ВСЕ основные читатели профиля несут авторитетную веру (регресс —
    если рефактор уронит заголовок из читателя, баг вернётся в этот канал)."""
    _profile(monkeypatch, "Тестовая страна")
    import patient_context, hai_core
    assert "ТЕКУЩАЯ ВЕРА" in patient_context.build_patient_brief(), "build_patient_brief потерял веру"
    assert "ТЕКУЩАЯ ВЕРА" in hai_core._build_patient_profile(), "_build_patient_profile потерял веру"


def test_arbiter_unverified_never_confirmed(db, monkeypatch):
    """E∘B: конфабуляция (source=arbiter_unverified) НЕ даёт occurred даже при confirmations>0."""
    _profile(monkeypatch, "Тестовая страна")
    import health_db
    with health_db.get_conn() as c:
        c.execute("INSERT INTO memory_facts (mem_class, value, valid_from, active, "
                  "confirmations, source) VALUES ('state', 'вылетел в Сосновск', ?, 1, 5, 'arbiter_unverified')",
                  (date.today().isoformat(),))
    import beliefs
    assert beliefs.resolve("travel").status != "occurred", "unverified не поднимает occurred"


def _seed_src(key: str, value: str, days_ago: int, confirmations: int, source: str):
    import health_db
    vf = (date.today() - timedelta(days=days_ago)).isoformat()
    with health_db.get_conn() as c:
        c.execute("INSERT INTO memory_facts (mem_class, key, value, valid_from, active, "
                  "confirmations, source) VALUES ('fact', ?, ?, ?, 1, ?, ?)",
                  (key, value, vf, confirmations, source))


def test_authoritative_keys_covers_location():
    """L2: единый источник изъятия для консолидатора — location-топик со всеми синонимами."""
    import beliefs
    assert beliefs.authoritative_keys() == {"current_location", "location"}


def test_authoritative_source_for_key():
    """L3: гвард записи знает, какой источник владеет ключом."""
    import beliefs
    assert beliefs.authoritative_source_for_key("current_location") == "device_gps"
    assert beliefs.authoritative_source_for_key("location") == "device_gps"
    assert beliefs.authoritative_source_for_key("melatonin") is None
    assert beliefs.authoritative_source_for_key(None) is None


def test_gps_authoritative_over_profile_and_fresher_conversation(db, monkeypatch):
    """Приоритет источника: device_gps — единственный авторитет локации. GPS выигрывает
    даже у профиля (confirmed) И у более СВЕЖЕГО conversation-факта (конфабуляция)."""
    _profile(monkeypatch, "Тестовая страна")
    _seed_src("current_location", "Сосновск", days_ago=1, confirmations=0, source="device_gps")
    _seed_src("location", "Липоград", days_ago=0, confirmations=0, source="conversation")  # свежее, но не авторитет
    import beliefs
    b = beliefs.resolve("location")
    assert b.value == "Сосновск", "GPS авторитетнее профиля и свежего conversation-конфаба"
    assert b.source == "device_gps"
    assert any(c.value == "Липоград" for c in b.history), "конфаб демотнут, не стёрт"


def test_no_gps_profile_beats_conversation_confab(db, monkeypatch):
    """Нет GPS-факта → падаем на (confirmed, свежесть): профиль бьёт неподтв. conversation."""
    _profile(monkeypatch, "Тестовая страна")
    _seed_src("location", "Липоград", days_ago=0, confirmations=0, source="conversation")
    import beliefs
    b = beliefs.resolve("location")
    assert b.value == "Тестовая страна", "без авторитета конфаб не должен побеждать профиль"


def test_header_location_says_by_gps(db, monkeypatch):
    """Заголовок показывает источник авторитета, а не голое «подтверждено»."""
    _profile(monkeypatch, "Тестовая страна")
    _seed_src("current_location", "Сосновск", days_ago=0, confirmations=0, source="device_gps")
    import beliefs
    h = beliefs.render_header()
    assert "Сосновск" in h and "GPS" in h, "локация из GPS с пометкой источника"
