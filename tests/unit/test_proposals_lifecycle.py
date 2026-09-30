#!/usr/bin/env python3.11
"""
test_proposals_lifecycle — supersede-at-generation + auto-expiry.

Системный дренаж очереди problem_list_proposals (2026-07-04): вместо ручной
чистки — supersede дублей от того же source и авто-expiry >N дней.
Использует фикстуру `db` (tmp-БД из health_schema.sql).
"""
import pytest

pytestmark = pytest.mark.unit


def test_supersede_same_source_same_problem(db):
    """2026-09-01: та же правка того же поля → не superseded, а ТА ЖЕ строка (refresh, repeats+1).
    Прежняя семантика (гасить старое) убивала соседние правки пачки — см. тесты ниже."""
    import problems_db as pdb
    import proposals_db as prdb
    id1 = pdb.save_problem_proposal("gp_weekly", [{"action": "update_field", "problem_id": "P005", "field": "notes"}])
    id2 = pdb.save_problem_proposal("gp_weekly", [{"action": "update_field", "problem_id": "P005", "field": "notes"}])
    pending = {p["id"]: p for p in prdb.get_pending_proposals()}
    assert id1 == id2 and len(pending) == 1 and pending[id1]["repeats"] == 2


def test_no_supersede_different_source(db):
    import problems_db as pdb
    import proposals_db as prdb
    id1 = pdb.save_problem_proposal("gp_weekly", [{"problem_id": "P001"}])
    id2 = pdb.save_problem_proposal("literature_curator", [{"problem_id": "P001"}])
    pending = {p["id"] for p in prdb.get_pending_proposals()}
    assert id1 in pending and id2 in pending, "разные source не суперсидят друг друга"


def test_no_supersede_without_problem_id(db):
    """literature-advisory без problem_id — не суперсидит (их досушивает expiry)."""
    import problems_db as pdb
    import proposals_db as prdb
    id1 = pdb.save_problem_proposal("literature_curator", [{"action": "literature_review_required", "summary": "a"}])
    id2 = pdb.save_problem_proposal("literature_curator", [{"action": "literature_review_required", "summary": "b"}])
    pending = {p["id"] for p in prdb.get_pending_proposals()}
    assert id1 in pending and id2 in pending


def test_expire_aged(db):
    import problems_db as pdb
    import proposals_db as prdb
    import health_db as hdb
    pid = pdb.save_problem_proposal("literature_curator", [{"action": "literature_review_required", "summary": "old"}])
    with hdb.get_conn() as conn:
        conn.execute("UPDATE problem_list_proposals SET created_at=date('now','-100 day'), "
                     "last_proposed_at=NULL WHERE id=?", (pid,))   # строка до миграции: без last_proposed_at
    n = prdb.expire_aged_proposals(60)
    assert n == 1, f"ожидалось истечь 1, истекло {n}"
    assert pid not in {p["id"] for p in prdb.get_pending_proposals()}


def test_expire_keeps_fresh(db):
    """Свежие (<N дней) не истекают."""
    import problems_db as pdb
    import proposals_db as prdb
    pid = pdb.save_problem_proposal("gp_weekly", [{"problem_id": "P003"}])
    n = prdb.expire_aged_proposals(60)
    assert n == 0 and pid in {p["id"] for p in prdb.get_pending_proposals()}


def test_expire_source_aware_literature_30d(db):
    """40д: literature_curator (порог 30д) истекает, gp_weekly (default 60д) — нет."""
    import problems_db as pdb
    import proposals_db as prdb
    import health_db as hdb
    lit = pdb.save_problem_proposal("literature_curator", [{"action": "literature_review_required", "summary": "old-lit"}])
    gp = pdb.save_problem_proposal("gp_weekly", [{"problem_id": "P009"}])
    with hdb.get_conn() as conn:
        conn.execute("UPDATE problem_list_proposals SET created_at=date('now','-40 day'), "
                     "last_proposed_at=date('now','-40 day') WHERE id=?", (lit,))
        conn.execute("UPDATE problem_list_proposals SET created_at=date('now','-40 day'), "
                     "last_proposed_at=date('now','-40 day') WHERE id=?", (gp,))
    n = prdb.expire_aged_proposals(60)
    pending = {p["id"] for p in prdb.get_pending_proposals()}
    assert n == 1, f"истечь должен только literature (30д), истекло {n}"
    assert lit not in pending, "literature_curator 40д > 30д → expired"
    assert gp in pending, "gp_weekly 40д < 60д → остаётся"


# ── 2026-09-01: одна правка — одна строка; повтор — счётчик; дубли записей ─────────────────
# Замер 01.09: одно и то же предложение «добавить» приходило неделями подряд под разными id —
# и ни разу не доживало до человека: пачка гасилась следующей пачкой по пересечению id, либо
# отклонялась целиком из-за соседней правки. Пример ниже — учебный (железодефицит).

def _pending():
    import proposals_db as prdb
    return {p["id"]: p for p in prdb.get_pending_proposals()}


def test_bundle_splits_into_one_row_per_change(db):
    import problems_db as pdb
    pdb.save_problem_proposal("gp_weekly", [
        {"action": "update_field", "problem_id": "P003", "field": "notes", "new_value": "a"},
        {"action": "update_field", "problem_id": "P005", "field": "watch_deadline", "new_value": "2026-12-31"},
        {"action": "add", "problem_id": "P007", "title": "Проблема В", "reason": "r"},
    ])
    assert len(_pending()) == 3, "на старом коде — 1 строка-пачка"


@pytest.mark.owner_data
def test_repeat_add_same_condition_bumps_repeats_not_rows(db):
    """Три формулировки GP за три недели → ОДНА строка, repeats=3, текст свежий.
    Идентичность — условие clinical_kb (iron_deficiency), не id модели."""
    import problems_db as pdb
    pdb.save_problem_proposal("gp_weekly", [{"action": "add", "problem_id": "new_ferritin_gap",
                                              "title": "Учебный пример: низкий ферритин, гемоглобин в норме"}])
    pdb.save_problem_proposal("gp_weekly", [{"action": "add", "problem_id": "P007",
                                              "title": "Дефицит железа (ферритин 7.6) — без анемии"}])
    pdb.save_problem_proposal("gp_weekly", [{"action": "add", "problem_id": "P007",
                                              "title": "Учебный железодефицит без анемии (IDWA): ферритин ниже референса"}])
    pend = _pending()
    assert len(pend) == 1
    row = next(iter(pend.values()))
    assert row["repeats"] == 3 and row["dedup_key"] == "add|cond:iron_deficiency"
    assert "IDWA" in row["proposed"]                      # текст — последний


def test_update_of_other_problem_does_not_kill_pending_add(db):
    """Старый убийца: пачка с P003 гасила pending «добавить P007» по пересечению problem_id."""
    import problems_db as pdb
    add_id = pdb.save_problem_proposal("gp_weekly", [{"action": "add", "problem_id": "P007",
                                                       "title": "Проблема Д — маркер выше нормы"}])
    pdb.save_problem_proposal("gp_weekly", [
        {"action": "update_field", "problem_id": "P003", "field": "notes", "new_value": "x"},
        {"action": "update_field", "problem_id": "P007", "field": "notes", "new_value": "y"},
    ])
    assert add_id in _pending()


def test_same_field_update_refreshes_not_duplicates(db):
    import problems_db as pdb
    a = pdb.save_problem_proposal("gp_weekly", [{"action": "update_field", "problem_id": "P005",
                                                  "field": "notes", "new_value": "v1"}])
    b = pdb.save_problem_proposal("gp_weekly", [{"action": "update_field", "problem_id": "P005",
                                                  "field": "notes", "new_value": "v2"}])
    pend = _pending()
    assert a == b and len(pend) == 1 and pend[a]["repeats"] == 2 and '"v2"' in pend[a]["proposed"]


@pytest.mark.owner_data
def test_add_covered_by_live_problem_is_not_saved(db):
    """Дубль записи медкарты: живая проблема с тем же условием → «добавить» не сохраняется."""
    import problems_db as pdb
    db.add_problem("invented_iron_case", "Дефицит железа (ферритин 10.4, 09.04.2040)")
    rid = pdb.save_problem_proposal("gp_weekly", [{"action": "add", "problem_id": "P009",
                                                    "title": "Учебный железодефицит без анемии (IDWA)"}])
    assert rid == 0 and not _pending()


def test_durable_condition_does_not_dedup_new_events(db):
    """Онкология — durable: «новый онко-эпизод» НЕ дубль «диагноза X», хотя оба матчат oncology."""
    import problems_db as pdb
    db.add_problem("px", "Диагноз X (онко) — ремиссия")
    rid = pdb.save_problem_proposal("gp_weekly", [{"action": "add", "problem_id": "P010",
                                                    "title": "Новый онко-эпизод Y, REG-A"}])
    assert rid and rid in _pending()
    assert _pending()[rid]["dedup_key"].startswith("add|title:")


@pytest.mark.owner_data
def test_apply_add_refuses_duplicate_of_live_problem(db):
    """Даже если строка проскочила (до миграции), apply не создаст вторую запись."""
    import problems_db as pdb, proposals_db as prdb, health_db as hdb
    db.add_problem("invented_iron_case", "Дефицит железа (ферритин 10.4)")
    with hdb.get_conn() as conn:
        cur = conn.execute("INSERT INTO problem_list_proposals (source, proposed) VALUES (?, ?)",
                           ("gp_weekly", '[{"action":"add","problem_id":"P011","title":"Учебный пример: низкий ферритин, гемоглобин в норме"}]'))
        pid = cur.lastrowid
    assert prdb.apply_proposal(pid) == 0
    with hdb.get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM problem_list").fetchone()[0] == 1


def test_expire_uses_last_proposed_at(db):
    """Строка создана 100 дней назад, но предлагалась вчера → не истекает."""
    import problems_db as pdb, proposals_db as prdb, health_db as hdb
    pid = pdb.save_problem_proposal("gp_weekly", [{"action": "add", "problem_id": "P007", "title": "Проблема Д"}])
    with hdb.get_conn() as conn:
        conn.execute("UPDATE problem_list_proposals SET created_at=date('now','-100 day'), "
                     "last_proposed_at=date('now','-1 day') WHERE id=?", (pid,))
    assert prdb.expire_aged_proposals(60) == 0


def test_apply_add_with_taken_problem_id_gets_own_slug(db):
    """Модель выдала P007 и железу, и второму состоянию: второй add — не дубль, а коллизия имени."""
    import proposals_db as prdb, health_db as hdb
    with hdb.get_conn() as conn:
        a = conn.execute("INSERT INTO problem_list_proposals (source, proposed) VALUES (?, ?)",
                         ("gp_weekly", '[{"action":"add","problem_id":"P007","title":"Дефицит железа (ферритин 7.6)"}]')).lastrowid
        b = conn.execute("INSERT INTO problem_list_proposals (source, proposed) VALUES (?, ?)",
                         ("gp_weekly", '[{"action":"add","problem_id":"P007","title":"Состояние Д — маркер X"}]')).lastrowid
    assert prdb.apply_proposal(a) == 1 and prdb.apply_proposal(b) == 1
    with hdb.get_conn() as conn:
        ids = [r[0] for r in conn.execute("SELECT problem_id FROM problem_list ORDER BY id")]
    assert len(ids) == 2 and ids[0] == "P007" and ids[1] != "P007" and "состояние_д" in ids[1]
