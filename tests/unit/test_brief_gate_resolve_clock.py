"""Кулдаун переживает resolved.

Если resolved стирает last_shown, однодневный перерыв превращает повтор в
new_alert. Вымышленная карточка про тыкву возвращается через восемь дней:
этот интервал внутри кулдауна, поэтому повтор должен быть подавлен.

Разрешение находки — не повод забыть, что мы про неё уже рассказывали. Для safety
полоса другая: возврат показателя из нормы — новость, её глушить нельзя.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

import brief_gate as bg

pytestmark = pytest.mark.unit

D0 = date(2032, 4, 12)


def _today(present, worse, d):
    return {"present": present, "worse": worse, "date": d}


class _Card:
    def __init__(self, lane="routine", severity=0.9, provider="food",
                 semantic_key="food:seasonal:vegetables:тыква"):
        self.lane, self.severity = lane, severity
        self.provider, self.semantic_key = provider, semantic_key


def _shown_then_absent(d_shown=D0, d_gone=None):
    """Показали в d_shown, назавтра находка исчезла → resolved."""
    st = bg.fsm_next(None, _today(True, False, d_shown))
    assert st["state"] == "new_alert" and st["last_shown"] == d_shown
    return bg.fsm_next(st, _today(False, False, d_gone or d_shown + timedelta(days=1)))


def test_resolved_keeps_the_clock():
    """resolved НЕ обнуляет last_shown — иначе кулдаун стирается перерывом в один день."""
    res = _shown_then_absent()
    assert res["state"] == "resolved"
    assert res["last_shown"] == D0, "часы показа потеряны на разрешении находки"


def test_return_within_cooldown_is_silent_for_routine():
    """Вымышленная карточка вернулась через восемь дней после показа."""
    res = _shown_then_absent()
    back = bg.fsm_next(res, _today(True, False, D0 + timedelta(days=8)))
    assert back["days_since_shown"] == 8
    d = bg.gate_decision(_Card(lane="routine"), back)
    assert d["status"] == "suppressed_cooldown", f"повтор через 8 дней прошёл: {d}"
    assert "after_resolve" in d["gate_reason"]


def test_return_after_cooldown_is_news_again():
    """Через 30+ дней это уже законная новость — глушить навсегда мы не собирались."""
    res = _shown_then_absent()
    back = bg.fsm_next(res, _today(True, False, D0 + timedelta(days=40)))
    assert bg.gate_decision(_Card(lane="routine"), back)["status"] == "candidate"


def test_safety_return_is_never_silenced():
    """Ферритин ушёл из нормы, вернулся, снова ушёл — это новость в любой день.
    Цена ошибки здесь асимметрична: молчание дороже повтора."""
    res = _shown_then_absent()
    back = bg.fsm_next(res, _today(True, False, D0 + timedelta(days=2)))
    d = bg.gate_decision(_Card(lane="safety", severity=0.8, provider="safety_net",
                               semantic_key="safety:ferritin:low"), back)
    assert d["status"] == "candidate", f"safety-возврат заглушён: {d}"


def test_first_sighting_still_shows():
    """Позитив на противоположную ошибку: НОВАЯ находка (prev=None) показывается сразу.
    Без этого «починка» могла бы заглушить всё и выглядеть зелёной."""
    st = bg.fsm_next(None, _today(True, False, D0))
    assert st["days_since_shown"] is None
    assert bg.gate_decision(_Card(), st)["status"] == "candidate"


def test_negative_control_clock_preservation_is_alive(monkeypatch):
    """ИСПОЛНЕННЫЙ негативный контроль: возвращаем старое поведение (resolved теряет
    часы) — и позитив обязан покраснеть. Без этого зелёный набор выше неотличим от
    зелёного по совпадению (§20): он прошёл бы и на коде до правки.

    Глушим не фикстуру, а сам механизм — подменяем last_shown в prev на None, ровно
    так, как это делал код до 2026-08-04.
    """
    res = _shown_then_absent()
    broken = dict(res, last_shown=None)          # ← поведение до правки
    back = bg.fsm_next(broken, _today(True, False, D0 + timedelta(days=8)))
    assert back["days_since_shown"] is None, "признак не зависит от часов — контроль негоден"
    assert bg.gate_decision(_Card(lane="routine"), back)["status"] == "candidate", (
        "старое поведение больше не даёт повтора — значит позитив выше "
        "зеленеет по другой причине, и как оракул он ничего не стоит")
