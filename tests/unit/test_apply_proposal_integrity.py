#!/usr/bin/env python3.11
"""apply_proposal не роняет одобрение IntegrityError'ом на «add», которое модель оформила
не тем ключом (карточка report:05cd86, 2026-10-24: IntegrityError ← _approve_proposal),
и при этом не плодит дублей: повтор того же предложения — ноль новых строк.

Значения выдуманы."""
import json

import pytest

pytestmark = pytest.mark.unit


def _proposal(changes):
    import health_db as hdb
    with hdb.get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO problem_list_proposals (source, proposed, status) VALUES (?,?,'pending')",
            ("gp_weekly", json.dumps(changes, ensure_ascii=False)))
        return cur.lastrowid


def _rows(pid):
    import health_db as hdb
    with hdb.get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT problem_id, title, description, status FROM problem_list WHERE source=? "
            "ORDER BY id", (f"proposal:{pid}",)).fetchall()]


@pytest.fixture
def prdb(db, monkeypatch):
    import proposals_db
    monkeypatch.setattr(proposals_db, "_plains_before_write", lambda pid: {})
    return proposals_db


@pytest.mark.parametrize("ch, want", [
    # id пришёл ключом "id" — он и есть problem_id
    ({"action": "add", "id": "X_ID_ONLY", "title": "Выдуманная проблема один"},
     ("X_ID_ONLY", "Выдуманная проблема один", None, "active_monitoring")),
    # ни заголовка, ни описания — заголовком становится id
    ({"action": "add", "problem_id": "X_NO_TITLE"},
     ("X_NO_TITLE", "X_NO_TITLE", None, "active_monitoring")),
    # status: null — статус по умолчанию, а не NULL
    ({"action": "add", "problem_id": "X_NULL_STATUS", "title": "Выдуманная два", "status": None},
     ("X_NULL_STATUS", "Выдуманная два", None, "active_monitoring")),
    # нет заголовка, есть описание — описание ПЕРЕНОСИТСЯ в заголовок
    ({"action": "add", "problem_id": "X_DESC", "description": "Синтетическое наблюдение"},
     ("X_DESC", "Синтетическое наблюдение", None, "active_monitoring")),
    # длинное описание: заголовок — первые 120 символов, описание остаётся целым
    ({"action": "add", "problem_id": "X_LONG", "description": "д" * 130},
     ("X_LONG", "д" * 120, "д" * 130, "active_monitoring")),
    # new_value СТРОКОЙ (замер 22.11, предложение 81 → пустая P008): строка — описание,
    # из него заголовок; раньше суть терялась и заголовком становился id
    ({"action": "add", "problem_id": "X_NV_STR", "field": "problem", "old_value": "",
      "new_value": "Выдуманное наблюдение " + "ж" * 120},
     ("X_NV_STR", ("Выдуманное наблюдение " + "ж" * 120)[:120],
      "Выдуманное наблюдение " + "ж" * 120, "active_monitoring")),
    ({"action": "add", "problem_id": "X_NV_SHORT", "new_value": "Короткое выдуманное"},
     ("X_NV_SHORT", "Короткое выдуманное", None, "active_monitoring")),
])
def test_add_variants_apply_with_exact_fields(prdb, ch, want):
    pid = _proposal([ch])
    assert prdb.apply_proposal(pid) == 1
    assert [tuple(r.values()) for r in _rows(pid)] == [want]


@pytest.mark.parametrize("ch", [
    {"action": "add", "problem_id": "X_NO_TITLE_R"},
    {"action": "add", "problem_id": "X_DESC_R", "description": "Синтетическое наблюдение повтор"},
    {"action": "add", "id": "X_ID_R", "title": "Выдуманная повтор"},
])
def test_reapplying_the_same_proposal_adds_nothing(prdb, ch):
    """Двойное «одобрить»: решение владельца 2026-09-01 — дубли записей не допускаются."""
    pid = _proposal([ch])
    assert prdb.apply_proposal(pid) == 1
    assert prdb.apply_proposal(pid) == 0
    assert len(_rows(pid)) == 1


def test_nested_new_value_with_null_id_still_gets_a_slug(prdb):
    """Контроль совместимости (зелёный и до карточки): id из new_value=None → slug из заголовка."""
    pid = _proposal([{"action": "add", "problem_id": "X_NV",
                      "new_value": {"problem_id": None, "title": "Выдуманная три"}}])
    assert prdb.apply_proposal(pid) == 1
    assert _rows(pid)[0]["problem_id"] == "выдуманная_три"
