"""
Ф6 приёмка анти-повтора — постоянные сторожа против регресса (fixture-DB).

Ловят возврат ежедневного PER3, потерю наблюдаемости и залипание safety. Это не
дубль юнитов модулей: здесь сквозной прогон pipeline-состояния на реальной схеме
context_cards за много дней — приёмка A1/A8/observability на поведении, не на функции.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

import brief_cards as bc
import brief_gate as bg
import brief_state as bs

pytestmark = pytest.mark.unit


def _card(key="genome:x:per3", provider="genome", lane="routine", sev=0.9):
    return bc.Card(provider=provider, semantic_key=key, lane=lane, severity=sev)


def test_a1_chronic_finding_not_daily(db):
    """Хроническая находка, присутствующая КАЖДЫЙ день 40 дней → показов ≤ MAX_RECAPS+1
    (регресс к ежедневному PER3 дал бы 40)."""
    conn = db.conn()
    d0 = date(2026, 1, 1)
    shows = 0
    for i in range(40):
        decs = bs.advance([_card()], d0 + timedelta(days=i), conn)
        if any(x["semantic_key"] == "genome:x:per3" and x["status"] == "shown" for x in decs):
            shows += 1
    assert shows <= bg.MAX_RECAPS + 1, f"хроника показана {shows}/40 — повтор вернулся"


def test_observability_every_suppression_has_reason(db):
    """Каждое подавление несёт gate_reason — иначе не докажешь, что молчим правильно."""
    conn = db.conn()
    d0 = date(2026, 1, 1)
    bs.advance([_card()], d0, conn)
    decs = bs.advance([_card()], d0 + timedelta(days=1), conn)
    supp = [x for x in decs if x["status"] != "shown"]
    assert supp, "ожидали подавление на 2-й день (кулдаун)"
    assert all(x.get("reason") for x in supp), "подавление без gate_reason"


def test_a8_safety_worsening_pierces_cooldown(db):
    """Стабильная safety в кулдауне молчит, но УХУДШЕНИЕ пробивает немедленно."""
    conn = db.conn()
    d0 = date(2026, 1, 1)
    k = "safety:ferritin:low"
    bs.advance([_card(k, "safety_net", "safety", 0.5)], d0, conn)  # показано
    decs2 = bs.advance([_card(k, "safety_net", "safety", 0.5)], d0 + timedelta(days=2), conn)
    assert all(x["status"] != "shown" for x in decs2 if x["semantic_key"] == k), "должно молчать в кулдауне"
    decs3 = bs.advance([_card(k, "safety_net", "safety", 0.9)], d0 + timedelta(days=3), conn)
    assert any(x["status"] == "shown" for x in decs3 if x["semantic_key"] == k), "ухудшение должно пробить кулдаун"
