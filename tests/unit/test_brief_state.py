"""
Ф3 brief_state — персистентность FSM в context_cards. Fixture-DB.

Главный позитив-контроль: прогон дважды за одну дату (catch-up) → строка идентична,
дубля нет (риск #1 на уровне SQL). Плюс: кулдаун-часы не двигаются без реального показа;
resolved когда активная находка исчезла; never-shown → не resolved.
"""
from __future__ import annotations

from datetime import date

import pytest

import brief_cards as bc
import brief_state as bs

pytestmark = pytest.mark.unit

_KEY = "genome:sleep:cry1"


def _card(sev=0.9):
    return bc.Card(provider="genome", semantic_key=_KEY, lane="routine", severity=sev,
                   last_value="AG", evidence_summary="CRY1 AG",
                   allowed_claims=["CRY1 AG Pathogenic"])


def test_new_finding_shown_and_recorded(db):
    d = date(2026, 7, 13)
    bs.advance([_card()], d, db.conn())
    row = db.fetchone("SELECT recurrence_state, status, last_shown_at FROM context_cards "
                      "WHERE semantic_key=? AND date=?", (_KEY, str(d)))
    assert row["recurrence_state"] == "new_alert"
    assert row["status"] == "shown"
    assert row["last_shown_at"] == str(d)


def test_idempotent_double_run(db):
    """Catch-up: второй прогон за ту же дату → та же строка, без дубля (риск #1)."""
    d = date(2026, 7, 13)
    cols = "recurrence_state, last_shown_at, status, escalation_level"
    bs.advance([_card()], d, db.conn())
    r1 = tuple(db.fetchone(f"SELECT {cols} FROM context_cards WHERE semantic_key=? AND date=?", (_KEY, str(d))))
    bs.advance([_card()], d, db.conn())
    r2 = tuple(db.fetchone(f"SELECT {cols} FROM context_cards WHERE semantic_key=? AND date=?", (_KEY, str(d))))
    assert db.count("context_cards", "semantic_key=? AND date=?", (_KEY, str(d))) == 1
    assert r1 == r2


def test_chronic_suppressed_cooldown_clock_frozen(db):
    d0 = date(2026, 7, 1)
    bs.advance([_card()], d0, db.conn())          # показано
    d2 = date(2026, 7, 3)                          # внутри кулдауна 14
    bs.advance([_card()], d2, db.conn())
    row = db.fetchone("SELECT status, last_shown_at FROM context_cards WHERE semantic_key=? AND date=?",
                      (_KEY, str(d2)))
    assert row["status"] != "shown"               # подавлено в кулдауне
    assert row["last_shown_at"] == str(d0)        # часы не сдвинулись — показа не было


def test_resolved_when_absent_after_shown(db):
    d0 = date(2026, 7, 1)
    bs.advance([_card()], d0, db.conn())          # показано
    d1 = date(2026, 7, 2)
    bs.advance([], d1, db.conn())                 # находка исчезла
    row = db.fetchone("SELECT recurrence_state FROM context_cards WHERE semantic_key=? AND date=?",
                      (_KEY, str(d1)))
    assert row["recurrence_state"] == "resolved"


def test_never_shown_no_resolved(db):
    bs.advance([], date(2026, 7, 13), db.conn())
    assert db.count("context_cards") == 0


def test_suggestion_resolved_suppressed(db):
    """Э3: карточка-предложение (еда) показана вчера, отсутствует сегодня → resolved
    ПИШЕТСЯ (ключ выходит из активного множества, завтра JOIN не переоткроет), но
    владельцу НЕ едет. Предложение не «выздоравливает» — resolved про него был 2 из 10."""
    food = bc.Card(provider="food", semantic_key="food:beans", lane="routine",
                   severity=0.9, evidence_summary="бобы")
    d0 = date(2026, 7, 1)
    bs.advance([food], d0, db.conn())
    d1 = date(2026, 7, 2)
    bs.advance([], d1, db.conn())
    row = db.fetchone("SELECT recurrence_state, status FROM context_cards "
                      "WHERE semantic_key=? AND date=?", ("food:beans", str(d1)))
    assert row["recurrence_state"] == "resolved"     # строка записана
    assert row["status"] != "shown"                  # но подавлена — не едет владельцу


def test_measurement_resolved_still_shown(db):
    """Позитив-контроль: у ИЗМЕРИТЕЛЬНОГО канала (drift/pulse) «вернулось в норму» —
    настоящая новость, resolved обязан ОСТАТЬСЯ показанным. Иначе Э3 душил бы и его."""
    drift = bc.Card(provider="drift", semantic_key="drift:rem_min:down", lane="routine",
                    severity=0.9, evidence_summary="REM упал")
    d0 = date(2026, 7, 1)
    bs.advance([drift], d0, db.conn())
    d1 = date(2026, 7, 2)
    bs.advance([], d1, db.conn())
    row = db.fetchone("SELECT recurrence_state, status FROM context_cards "
                      "WHERE semantic_key=? AND date=?", ("drift:rem_min:down", str(d1)))
    assert row["recurrence_state"] == "resolved"
    assert row["status"] == "shown"                  # вернулось в норму — это новость


def _uv(sev):
    return bc.Card(provider="env", semantic_key="weather:uv:high", lane="routine",
                   severity=sev, evidence_summary="UV высокий")


def test_worse_routine_subquantum_not_worsened(db):
    """Э6 e2e: routine-severity 0.46→0.47 (суб-квантовая дрожь UV) НЕ даёт 'worsened' —
    не пробивает кулдаун. Это и были три UV-повтора."""
    d0 = date(2026, 7, 1)
    bs.advance([_uv(0.46)], d0, db.conn())
    d1 = date(2026, 7, 2)
    bs.advance([_uv(0.47)], d1, db.conn())
    row = db.fetchone("SELECT recurrence_state FROM context_cards WHERE semantic_key=? AND date=?",
                      ("weather:uv:high", str(d1)))
    assert row["recurrence_state"] != "worsened"


def test_worse_routine_real_step_is_worsened(db):
    """Позитив-контроль: routine 0.3→0.6 (настоящий шаг уровня) ДАЁТ 'worsened' —
    порог не глушит реальное ухудшение."""
    def dr(sev):
        return bc.Card(provider="drift", semantic_key="drift:rem_min:down", lane="routine",
                       severity=sev, evidence_summary="REM упал")
    d0 = date(2026, 7, 1)
    bs.advance([dr(0.3)], d0, db.conn())
    d1 = date(2026, 7, 2)
    bs.advance([dr(0.6)], d1, db.conn())
    row = db.fetchone("SELECT recurrence_state FROM context_cards WHERE semantic_key=? AND date=?",
                      ("drift:rem_min:down", str(d1)))
    assert row["recurrence_state"] == "worsened"
