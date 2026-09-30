"""Стол владельца не зарастает (решение владельца 23.09, нить owner-desk).

ЧТО ДОКАЗЫВАЕТСЯ. Карточка из проверки снимается, когда её находки нет в СЕГОДНЯШНЕЙ
проверке или находка стала `standing`; автор — `not_on_desk`. Несвежая проверка не
снимает ничего. Чужие карточки (нити, красная серия) не трогаются. Цикл звонит после
себя (main), а не из run().

ЧЕГО НЕ ДОКАЗЫВАЕТСЯ. Что пропавшая находка действительно решена: проверка могла
замолчать по своей поломке. Против этого стоит только требование свежей даты.
"""
import datetime as _dt
import json

import pytest

pytestmark = pytest.mark.unit

import night_cycle as nc
import parked_decisions as pd

TODAY = _dt.date(2026, 9, 23)


@pytest.fixture
def env(tmp_path, monkeypatch):
    integ = tmp_path / "integrity_latest.json"
    monkeypatch.setenv("HEALTH_INTEGRITY_LATEST", str(integ))
    monkeypatch.setenv("HEALTH_PARKED_DB", str(tmp_path / "parked.json"))

    def write(date, warnings=(), failures=()):
        integ.write_text(json.dumps({"date": date, "warnings": [list(w) for w in warnings],
                                     "failures": list(failures)}), encoding="utf-8")
    return write


def _cards():
    for gid in ("warn:живая находка", "warn:пропавшая находка",
                "integrity:пропавшее падение", "thread-stalled:нить", "fix-applier-input-returned"):
        pd.park(gid, "owner_decision", gid)


def test_gone_finding_is_retired_live_one_stays(env):
    env(TODAY.isoformat(), warnings=[("живая находка", "детали")])
    _cards()
    assert nc._retire_not_on_desk(TODAY) == 2
    gone = pd.get("warn:пропавшая находка")
    assert gone["status"] == "resolved" and gone["decided_by"] == "not_on_desk"
    assert pd.get("integrity:пропавшее падение")["status"] == "resolved"
    open_ids = {g["id"] for g in pd.list_open(TODAY)}
    assert open_ids == {"warn:живая находка", "thread-stalled:нить", "fix-applier-input-returned"}


def test_stale_check_retires_nothing(env):
    """Вчерашняя проверка — не «находок нет»: иначе умерший монитор вычистил бы стол."""
    env("2026-09-22")
    _cards()
    assert nc._retire_not_on_desk(TODAY) is None
    assert len(pd.list_open(TODAY)) == 5


def test_standing_finding_leaves_the_desk(env):
    """Класс, переведённый владельцем в standing (21.09), не лежит на столе."""
    label = "снимок MacBook не видит 1 дерев(о/а) нитей"
    env(TODAY.isoformat(), warnings=[(label, "")])
    gid = nc._slug(label, "warn")
    pd.park(gid, "owner_decision", "старый вопрос")
    assert nc._retire_not_on_desk(TODAY) == 1
    rec = pd.get(gid)
    assert rec["decided_by"] == "not_on_desk" and "standing" in rec["decision"]


def test_main_rings_after_the_cycle(monkeypatch):
    import owner_nag
    order = []
    monkeypatch.setattr(nc, "run", lambda: order.append("run") or {})
    monkeypatch.setattr(owner_nag, "ring_after_cycle",
                        lambda now=None: order.append("ring") or {"rang": True})
    assert nc.main()["bell"] == {"rang": True} and order == ["run", "ring"]
