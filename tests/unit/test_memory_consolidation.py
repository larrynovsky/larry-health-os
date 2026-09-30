"""
tests/unit/test_memory_consolidation.py — R3 shadow-движок (2026-07-04).

Ключевой пин: propose() пишет предложения, но НЕ применяет к memory_facts
(shadow-режим — самый важный инвариант безопасности R3).
"""
from __future__ import annotations

import json
import pytest

pytestmark = pytest.mark.unit


_PROPOSALS = [
    {"current_key": "device", "action": "MERGE", "canonical_key": "fixture_device",
     "proposed_value": "power 7, duration 12 min (maintained)",
     "medical_flag": True, "rationale": "разросшийся ключ про Fixture Device"},
    {"current_key": "genotype_GENEX", "action": "DEFER",
     "authority_source": "genome", "medical_flag": True,
     "rationale": "генетику решает геномный пайплайн, не чат"},
    {"current_key": "travel_status", "action": "STALE", "medical_flag": False,
     "rationale": "поездка в прошлом"},
]


def _arm(anthropic_mock, monkeypatch):
    import anthropic, memory_consolidation as mc
    monkeypatch.setattr(mc.hai_core, "get_client",
                        lambda: anthropic.Anthropic(api_key="fake"))
    anthropic_mock.script(match=lambda p: "Факты" in p,
                          response=json.dumps(_PROPOSALS, ensure_ascii=False))
    return mc


def _seed(db):
    import memory_facts_db as mf
    mf.save_fact("fact", "FIXTURE DEVICE", key="device")
    mf.save_fact("fact", "p.Gly12Asp heterozygous (synthetic)", key="genotype_GENEX")
    mf.save_fact("fact", "traveling until March 3", key="travel_status")


def test_propose_writes_proposals_but_applies_nothing(db, anthropic_mock, monkeypatch):
    mc = _arm(anthropic_mock, monkeypatch)
    _seed(db)

    import memory_facts_db as mf
    before = {(f["key"], f["value"], f["valid_to"]) for f in mf.get_facts("fact")}

    proposals = mc.propose()
    assert len(proposals) == 3

    # SHADOW: memory_facts не изменилась ни на строку
    after = {(f["key"], f["value"], f["valid_to"]) for f in mf.get_facts("fact")}
    assert before == after, "shadow-движок НЕ должен трогать memory_facts"

    # предложения записаны как pending
    summ = mc.summary()
    assert summ.get("MERGE") == 1 and summ.get("DEFER") == 1 and summ.get("STALE") == 1


def test_propose_persists_dict_value_as_json(db, anthropic_mock, monkeypatch):
    """Структурированный ответ LLM должен сериализоваться без потери истории. Придуманный прибор и его настройки проверяют границу dict/SQLite."""
    import anthropic
    import memory_consolidation as mc
    monkeypatch.setattr(mc.hai_core, "get_client",
                        lambda: anthropic.Anthropic(api_key="fake"))
    dict_prop = [{"current_key": "device", "action": "MERGE",
                  "canonical_key": "fixture_device",
                  "proposed_value": {"power": 7, "duration_min": 12,
                                     "history": ["28→12 мин"]},
                  "medical_flag": True, "rationale": "фасеты"}]
    anthropic_mock.script(match=lambda p: "Факты" in p,
                          response=json.dumps(dict_prop, ensure_ascii=False))
    _seed(db)
    proposals = mc.propose(persist=True)
    assert len(proposals) == 1, "джоб не имеет права упасть на структурированном значении"
    rows = db.execute("SELECT proposed_value FROM memory_consolidation_proposals").fetchall()
    stored = json.loads(rows[0][0])
    assert stored["power"] == 7 and stored["history"] == ["28→12 мин"], \
        "dict сериализуется в JSON-строку без потери содержимого"


def test_run_nightly_survives_missing_table(db, anthropic_mock, monkeypatch):
    """Легаси-схема без таблицы предложений: ensure должен предшествовать DELETE."""
    mc = _arm(anthropic_mock, monkeypatch)
    _seed(db)
    db.execute("DROP TABLE memory_consolidation_proposals")
    summary = mc.run_nightly()
    assert "proposed" in summary, "run_nightly обязан пересоздать таблицу и отработать"


def test_facts_for_consolidation_excludes_authoritative_topics(db):
    """Топики location/current_location не идут в LLM-батч: их источник — device_gps. Вымышленные места ниже проверяют только маршрутизацию."""
    import memory_facts_db as mf, memory_consolidation as mc
    mf.save_fact("fact", "Учебный город Лунолесье", key="current_location")
    mf.save_fact("fact", "Учебный город Ветрополь", key="location")
    mf.save_fact("fact", "FIXTURE DEVICE", key="device")
    keys = {f["key"] for f in mc._facts_for_consolidation()}
    assert "current_location" not in keys and "location" not in keys, "локация изъята из батча"
    assert "device" in keys, "обычные lifestyle-факты остаются в консолидации"


def test_apply_safe_applies_defer_stale_holds_merge(db, anthropic_mock, monkeypatch):
    """R3-d вариант 2: DEFER+STALE применяются (active=0+valid_to, обратимо),
    MERGE остаётся pending (человек-гейт)."""
    mc = _arm(anthropic_mock, monkeypatch)
    _seed(db)
    db.add_genetic_variant("rs0000001", gene="GENEX")  # D-guard: GENEX реально в геноме → DEFER применим
    mc.propose()

    assert mc.apply_safe(dry_run=True) == {"DEFER": 1, "STALE": 1}
    res = mc.apply_safe(dry_run=False)
    assert res == {"DEFER": 1, "STALE": 1, "DEFER_kept_no_authority": 0}

    import memory_facts_db as mf
    active_keys = {f["key"] for f in mf.get_facts("fact")}
    # DEFER (genotype_GENEX) и STALE (travel_status) ушли из активных
    assert "genotype_GENEX" not in active_keys
    assert "travel_status" not in active_keys
    # MERGE (device) остался — на человек-гейт
    assert "device" in active_keys

    import health_db
    with health_db.get_conn() as conn:
        merge_status = conn.execute(
            "SELECT status FROM memory_consolidation_proposals WHERE current_key='device'"
        ).fetchone()[0]
        defer_status = conn.execute(
            "SELECT status FROM memory_consolidation_proposals WHERE current_key='genotype_GENEX'"
        ).fetchone()[0]
    assert merge_status == "pending", "MERGE не применяется авто"
    assert defer_status == "applied"

    # обратимость: старая строка не удалена, помечена valid_to
    with health_db.get_conn() as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM memory_facts WHERE key='genotype_GENEX' AND valid_to IS NOT NULL"
        ).fetchone()[0]
    assert n == 1, "DEFER обратим — строка помечена, не удалена"


def test_propose_merges_entity_level_structured(db, anthropic_mock, monkeypatch):
    """Вариант A: propose_merges сводит фрагменты в ОДНУ сущность со структурным
    JSON-значением, заменяя прежние фрагментированные MERGE-предложения."""
    import anthropic, memory_consolidation as mc, memory_facts_db as mf, health_db, json as _j
    # Независимо придуманные фрагменты про fixture_device + прежние фрагментированные MERGE-предложения
    mf.save_fact("fact", "28 min", key="current_fixture_device_dose")
    mf.save_fact("fact", "power 7, duration 12 min (maintained)", key="fixture_device_settings")
    mc._ensure_proposals_table()
    with health_db.get_conn() as conn:
        for k in ("current_fixture_device_dose", "fixture_device_settings"):
            conn.execute("INSERT INTO memory_consolidation_proposals "
                         "(current_key, action, canonical_key, status) "
                         "VALUES (?, 'MERGE', 'fixture_device_x', 'pending')", (k,))

    entity = [{
        "canonical_key": "fixture_device",
        "value": {"current_duration_min": 12, "current_power": 7,
                  "history": "28→12 мин", "status": "active"},
        "current_note": "текущее 12 мин (maintained); 28 устарело",
        "medical_flag": True,
        "source_keys": ["current_fixture_device_dose", "fixture_device_settings"],
    }]
    monkeypatch.setattr(mc.hai_core, "get_client",
                        lambda: anthropic.Anthropic(api_key="fake"))
    anthropic_mock.script(match=lambda p: "Исходные lifestyle" in p,
                          response=_j.dumps(entity, ensure_ascii=False))

    out = mc.propose_merges()
    assert len(out) == 1

    with health_db.get_conn() as conn:
        rows = conn.execute(
            "SELECT canonical_key, proposed_value, source_keys, medical_flag "
            "FROM memory_consolidation_proposals WHERE action='MERGE' AND status='pending'"
        ).fetchall()
    assert len(rows) == 1, "фрагментированные MERGE заменены на одну сущность"
    r = rows[0]
    assert r["canonical_key"] == "fixture_device"
    val = _j.loads(r["proposed_value"])
    assert val["current_duration_min"] == 12 and "history" in val, "структурное значение, 28-vs-12 сведено"
    assert set(_j.loads(r["source_keys"])) == {"current_fixture_device_dose", "fixture_device_settings"}
    assert r["medical_flag"] == 1


def test_apply_merges_creates_canonical_and_retires_sources(db):
    """apply_merges: создаёт канонический факт (структурный value) + помечает
    исходные valid_to (обратимо), не удаляя."""
    import memory_consolidation as mc, memory_facts_db as mf, health_db, json as _j
    mf.save_fact("fact", "28 min", key="current_fixture_device_dose")
    mf.save_fact("fact", "power 7, 12 min (maintained)", key="fixture_device_settings")
    mc._ensure_proposals_table()
    with health_db.get_conn() as conn:
        conn.execute(
            "INSERT INTO memory_consolidation_proposals "
            "(action, canonical_key, proposed_value, source_keys, medical_flag, status) "
            "VALUES ('MERGE', 'fixture_device', ?, ?, 1, 'pending')",
            (_j.dumps({"current_duration_min": 12, "history": "28→12"}, ensure_ascii=False),
             _j.dumps(["current_fixture_device_dose", "fixture_device_settings"])))

    assert mc.apply_merges(dry_run=True) == {"entities": 1, "source_keys": 2}
    res = mc.apply_merges(dry_run=False)
    assert res == {"created": 1, "retired": 2}

    active = {f["key"]: f for f in mf.get_facts("fact")}
    assert "fixture_device" in active, "канонический факт создан и активен"
    assert _j.loads(active["fixture_device"]["value"])["current_duration_min"] == 12
    assert "current_fixture_device_dose" not in active and "fixture_device_settings" not in active
    # обратимость: исходные не удалены
    with health_db.get_conn() as conn:
        n = conn.execute("SELECT COUNT(*) FROM memory_facts WHERE key='fixture_device_settings' "
                         "AND valid_to IS NOT NULL").fetchone()[0]
    assert n == 1


def test_apply_supersede_and_reject(db):
    """A: apply_supersede помечает факт valid_to (обратимо); reject не трогает факт."""
    import memory_consolidation as mc, memory_facts_db as mf, health_db
    mf.save_fact("fact", "active 12min", key="fixture_device")
    fid = mf.save_fact("fact", "discontinued", key="medications")
    mc._ensure_proposals_table()
    with health_db.get_conn() as conn:
        # SUPERSEDE на fixture_device (по ключу), и одно на reject
        nk = conn.execute("SELECT id FROM memory_facts WHERE key='fixture_device'").fetchone()[0]
        conn.execute("INSERT INTO memory_consolidation_proposals "
                     "(fact_id, current_key, action, status) VALUES (?, 'fixture_device', 'SUPERSEDE', 'pending')", (nk,))
        pid_ok = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute("INSERT INTO memory_consolidation_proposals "
                     "(current_key, action, status) VALUES ('x', 'SUPERSEDE', 'pending')")
        pid_rej = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    assert len(mc.pending_supersedes()) == 2
    assert mc.apply_supersede(pid_ok) is True
    active = {f["key"] for f in mf.get_facts("fact")}
    assert "fixture_device" not in active, "SUPERSEDE снял устаревший активный fixture_device"
    assert "medications" in active, "новый факт остался"
    # обратимость
    with health_db.get_conn() as conn:
        n = conn.execute("SELECT COUNT(*) FROM memory_facts WHERE key='fixture_device' AND valid_to IS NOT NULL").fetchone()[0]
    assert n == 1

    assert mc.reject(pid_rej) is True
    with health_db.get_conn() as conn:
        st = conn.execute("SELECT status FROM memory_consolidation_proposals WHERE id=?", (pid_rej,)).fetchone()[0]
    assert st == "rejected"
    assert len(mc.pending_supersedes()) == 0


def test_run_nightly_auto_applies_safe_gates_supersede(db, anthropic_mock, monkeypatch):
    """run_nightly: DEFER+STALE авто, SUPERSEDE остаётся pending на гейт."""
    import anthropic, memory_consolidation as mc, memory_facts_db as mf, json as _j
    mf.save_fact("fact", "power 7, 12min active", key="fixture_device")
    mf.save_fact("fact", "discontinued 5mo ago", key="medications")
    mf.save_fact("fact", "traveling until March", key="travel_status")
    payload = [
        {"current_key": "fixture_device", "action": "SUPERSEDE", "medical_flag": True,
         "rationale": "medications говорит отменён, а тут active"},
        {"current_key": "medications", "action": "KEEP", "medical_flag": True, "rationale": "актуально"},
        {"current_key": "travel_status", "action": "STALE", "medical_flag": False, "rationale": "прошлое"},
    ]
    monkeypatch.setattr(mc.hai_core, "get_client", lambda: anthropic.Anthropic(api_key="fake"))
    anthropic_mock.script(match=lambda p: "Факты" in p, response=_j.dumps(payload, ensure_ascii=False))

    res = mc.run_nightly()
    assert res["auto_applied"].get("STALE") == 1
    assert res["pending_supersede"] == 1
    # SUPERSEDE НЕ применён авто — fixture_device ещё активен до гейта
    active = {f["key"] for f in mf.get_facts("fact")}
    assert "fixture_device" in active, "SUPERSEDE не авто — ждёт человека"
    assert "travel_status" not in active, "STALE применён авто"


def test_unanswered_card_is_sent_once(db, anthropic_mock, monkeypatch):
    """28.09.2026: неотвеченная карточка «Обновление памяти» приходила заново каждую ночь —
    run_nightly стирал pending и пересоздавал его под новым id. Отправленная ждёт ответа
    под своим id; следующей ночью к отправке — ничего; «Оставить» тоже не переспрашивается."""
    import anthropic, memory_consolidation as mc, memory_facts_db as mf, json as _j
    mf.save_fact("fact", "power 7, 12min active", key="fixture_device")
    payload = [{"current_key": "fixture_device", "action": "SUPERSEDE", "medical_flag": True,
                "rationale": "противоречит другому факту"}]
    monkeypatch.setattr(mc.hai_core, "get_client", lambda: anthropic.Anthropic(api_key="fake"))
    anthropic_mock.script(match=lambda p: "Факты" in p, response=_j.dumps(payload, ensure_ascii=False))

    mc.run_nightly()
    first = mc.pending_supersedes()
    assert len(first) == 1
    mc.mark_supersede_sent(first[0]["id"])

    mc.run_nightly()                       # вторая ночь, модель предлагает то же
    assert mc.pending_supersedes() == [], "отправленную карточку не шлём повторно"
    assert mc.apply_supersede(first[0]["id"]) is True, "кнопка первой карточки жива"

    mf.save_fact("fact", "power 9, 16min active", key="fixture_device")
    mc.run_nightly()
    again = mc.pending_supersedes()
    assert len(again) == 1
    mc.mark_supersede_sent(again[0]["id"])
    assert mc.reject(again[0]["id"]) is True   # «Оставить»
    mc.run_nightly()
    assert mc.pending_supersedes() == [], "после «Оставить» про тот же факт не спрашиваем"


def test_defer_flags_medical(db, anthropic_mock, monkeypatch):
    mc = _arm(anthropic_mock, monkeypatch)
    _seed(db)
    mc.propose()
    import health_db
    with health_db.get_conn() as conn:
        row = conn.execute(
            "SELECT authority_source, medical_flag FROM memory_consolidation_proposals "
            "WHERE current_key='genotype_GENEX'"
        ).fetchone()
    assert row["authority_source"] == "genome"
    assert row["medical_flag"] == 1, "DEFER генетики обязан быть medical_flag"
