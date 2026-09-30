"""«Прошло» — только про показанное в эпизоде и с напоминанием.

Состояние new_alert возникает и у отсеянной порогом карточки. Поэтому один
resolved не доказывает, что человек видел тревогу. Вымышленные эпизоды ниже
проверяют молчание для непоказанного и напоминание для показанного.
"""
from __future__ import annotations

from datetime import date

import pytest

import brief_cards as bc
import brief_gate as bg
import brief_state as bs
import gp_agent

pytestmark = pytest.mark.unit

_K = "drift:fixture_metric:down"


def _drift(sev, ev="Тестовый показатель down -13% за 4д"):
    return bc.Card(provider="drift", semantic_key=_K, lane="routine", severity=sev,
                   evidence_summary=ev)


def _row(db, d):
    return db.fetchone("SELECT recurrence_state, status, gate_reason, evidence_summary "
                       "FROM context_cards WHERE semantic_key=? AND date=?", (_K, str(d)))


def _decision(decs):
    return next(x for x in decs if x["semantic_key"] == _K)


def test_suppressed_alert_resolves_silently(db):
    """Вымышленная тревога отсеяна порогом и исчезла на следующий день.
    Ключ закрывается, но уведомление об отбое не отправляется."""
    bs.advance([_drift(0.3)], date(2032, 4, 27), db.conn())
    assert _row(db, date(2032, 4, 27))["status"] != "shown"
    decs = bs.advance([], date(2032, 4, 28), db.conn())
    row = _row(db, date(2032, 4, 28))
    assert row["recurrence_state"] == "resolved"
    assert row["status"] != "shown"
    assert _decision(decs)["status"] == "none"


def test_shown_alert_resolves_with_reminder(db):
    """Вымышленная тревога показана, затем исчезла; отбой напоминает её содержание
    и границы эпизода."""
    bs.advance([_drift(0.9, "Тестовый показатель down -37%")], date(2032, 4, 25), db.conn())
    bs.advance([_drift(0.9, "Тестовый показатель down -43%")], date(2032, 4, 26), db.conn())
    decs = bs.advance([], date(2032, 4, 27), db.conn())
    d = _decision(decs)
    assert d["status"] == "shown" and d["state"] == "resolved"
    assert "Тестовый показатель down -37%" in d["evidence"]        # то, что человек читал
    assert "с 25.04 по 26.04" in d["evidence"]
    assert "говорили 25.04" in d["evidence"]
    assert _row(db, date(2032, 4, 27))["evidence_summary"] == d["evidence"]


def test_old_episode_show_does_not_license_new_resolve(db):
    """Показ в прошлом эпизоде не разрешает отбой нового, скрытого кулдауном."""
    bs.advance([_drift(0.9)], date(2032, 3, 29), db.conn())   # показано
    bs.advance([], date(2032, 3, 30), db.conn())              # прошло (эпизод 1 закрыт)
    bs.advance([_drift(0.9)], date(2032, 4, 10), db.conn())   # вернулось: кулдаун глушит
    assert _row(db, date(2032, 4, 10))["status"] != "shown"
    decs = bs.advance([], date(2032, 4, 11), db.conn())
    assert _row(db, date(2032, 4, 11))["recurrence_state"] == "resolved"
    assert _decision(decs)["status"] == "none"


def test_fsm_default_without_episode_field():
    """Вызывающий без поля shown_in_episode: грубый дефолт — «не показывали ни разу» →
    отбой молчит; показывали когда-либо → прежнее поведение."""
    never = bg.fsm_next({"state": "new_alert", "last_shown": None, "recaps": 0},
                        {"present": False, "worse": False, "date": date(2032, 4, 28)})
    assert never["state"] == "resolved" and never["show"] is False
    once = bg.fsm_next({"state": "new_alert", "last_shown": date(2032, 4, 27), "recaps": 0},
                       {"present": False, "worse": False, "date": date(2032, 4, 28)})
    assert once["state"] == "resolved" and once["show"] is True


def test_context_block_only_body_lanes():
    """Блок «прошло» для модели: только показанные отбои дрейфа и безопасности;
    гипотеза и непоказанный отбой в текст не идут; нет отбоев — нет блока."""
    decs = [
        {"semantic_key": "drift:x", "status": "shown", "state": "resolved",
         "provider": "drift", "evidence": "было: X", "origin": "internal"},
        {"semantic_key": "safety:hrv:?", "status": "shown", "state": "resolved",
         "provider": "safety_net", "evidence": "было: ВСР", "origin": "internal"},
        {"semantic_key": "safety:ca19-9:up", "status": "shown", "state": "resolved",
         "provider": "safety_net", "evidence": "было: онкомаркер", "origin": "personal_external"},
        {"semantic_key": "hypothesis:1", "status": "shown", "state": "resolved",
         "provider": "hypotheses", "evidence": "было: гипотеза", "origin": "internal"},
        {"semantic_key": "drift:y", "status": "none", "state": "resolved",
         "provider": "drift", "evidence": "было: Y", "origin": "internal"},
    ]
    text = "\n".join(gp_agent._resolved_context_lines(decs))
    assert "было: X" in text and "было: ВСР" in text
    assert "гипотеза" not in text and "было: Y" not in text
    assert "онкомаркер" not in text          # анализ не «проходит» — новый не сдан
    assert gp_agent._resolved_context_lines([]) == []


def test_safety_origin_by_source():
    """Лаб-алерт — внешний документ, носимый — ежедневный замер; неизвестный источник —
    внешний (fail-closed: «прошло» про него не скажем)."""
    base = {"metric": "HRV", "direction": "?", "level": "warn", "value": 23}
    assert bc.from_safety({**base, "source": "lifestyle"}).origin == "internal"
    for src in ("lab", "lab_trend", "norm_unresolved", None):
        assert bc.from_safety({**base, "source": src}).origin == "personal_external"


def test_lab_resolve_carries_origin_to_decision(db):
    """Отбой лаб-карточки несёт origin до решения — иначе канал «прошло» его не отсеет."""
    card = bc.from_safety({"source": "lab_trend", "metric": "CA19-9", "direction": "up",
                           "level": "warn", "value": 8.25})
    bs.advance([card], date(2032, 4, 3), db.conn())
    decs = bs.advance([], date(2032, 4, 4), db.conn())
    d = next(x for x in decs if x["semantic_key"] == "safety:ca19-9:up")
    assert d["origin"] == "personal_external"
    assert gp_agent._resolved_context_lines(decs) == []
