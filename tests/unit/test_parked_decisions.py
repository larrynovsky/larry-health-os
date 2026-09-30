"""Характеризация parked_decisions — фиксирует «верно» так, чтобы поломка
покраснела. Стор уведён в tmp через HEALTH_PARKED_DB; время заморожено через
_time_inject.set_test_clock. Health.db не трогается (ops-meta стор)."""
import datetime as _dt

import pytest

import parked_decisions as pd
from _time_inject import set_test_clock, clear_test_clock


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("HEALTH_PARKED_DB", str(tmp_path / "parked.json"))
    set_test_clock("2026-08-02")
    try:
        yield
    finally:
        clear_test_clock()


def test_park_then_open_then_resolve(store):
    pd.park("pytest:x", "dev_fix", "сломанный оракул test_clone")
    assert [r["id"] for r in pd.list_open()] == ["pytest:x"]
    pd.record_decision("pytest:x", "согласовал патч")
    assert pd.list_open() == [], "resolved не должен висеть в open"
    assert pd.get("pytest:x")["decision"] == "согласовал патч"


def test_read_is_not_reaction(store):
    """§17: тап/взгляд не гасит. Только get() (чтение) — гейт остаётся open."""
    pd.park("g", "stage_closure", "этап готов")
    pd.get("g")
    pd.list_open()
    assert [r["id"] for r in pd.list_open()] == ["g"], "чтение не должно гасить гейт"


def test_defer_hides_until_date_then_resurfaces(store):
    pd.park("g", "dev_fix", "низкий приоритет")
    pd.defer("g", _dt.date(2026, 8, 5))
    assert pd.list_open() == [], "до срока отложенный молчит"
    got = pd.list_open(today=_dt.date(2026, 8, 5))
    assert [r["id"] for r in got] == ["g"], "в день срока всплывает снова"


def test_recurrence_reopens(store):
    """Решённое, но вернувшееся падение — переоткрывается (рецидив реален)."""
    pd.park("g", "dev_fix", "первое обнаружение")
    pd.record_decision("g", "починил")
    pd.park("g", "dev_fix", "вернулось")
    got = pd.list_open()
    assert [r["id"] for r in got] == ["g"], "рецидив обязан снова висеть"
    assert got[0]["decision"] is None, "переоткрытый гейт без старого решения"


def test_defer_respected_on_reparking(store):
    """Повторное ночное обнаружение НЕ будит отложенный гейт раньше срока."""
    pd.park("g", "dev_fix", "detect 1")
    pd.defer("g", _dt.date(2026, 8, 10))
    pd.park("g", "dev_fix", "detect 2 (та же ночь+1)")
    assert pd.list_open() == [], "re-park не должен отменять defer оператора"


def test_park_idempotent(store):
    pd.park("g", "dev_fix", "a")
    pd.park("g", "dev_fix", "b")
    assert len([r for r in pd.list_open()]) == 1
    assert pd.get("g")["summary"] == "b", "summary обновляется, дубля нет"


def test_resolve_requires_nonempty_decision(store):
    pd.park("g", "stage_closure", "s")
    with pytest.raises(ValueError):
        pd.record_decision("g", "   ")


def test_defer_must_be_future(store):
    pd.park("g", "dev_fix", "s")
    with pytest.raises(ValueError):
        pd.defer("g", _dt.date(2026, 8, 2))  # today


def test_park_rejects_unknown_kind(store):
    with pytest.raises(ValueError):
        pd.park("g", "нечто", "s")


# ── Умолчание по молчанию (решение владельца 2026-09-13, вариант В) ──────────
# Оракулы на границу: что закрывается само, а что не закрывается НИКОГДА.

def test_silence_closes_card_with_full_trio(store):
    """Срок истёк и тройка полна → карточка закрыта, автор — не владелец."""
    pd.park("g", "dev_fix", "мелкий фикс",
            default="применить патч", rollback="git revert <sha>",
            auto_after=_dt.date(2026, 8, 16), executor="fix")
    assert [r["id"] for r in pd.list_open()] == ["g"], "до срока висит"
    done = []
    closed = pd.sweep_defaults(today=_dt.date(2026, 8, 16),
                               execute=lambda rec: done.append(rec["id"]) or True)
    assert done == ["g"], "вариант обязан быть ИСПОЛНЕН до записи решения"
    assert [c["id"] for c in closed] == ["g"]
    rec = pd.get("g")
    assert rec["status"] == "resolved"
    assert rec["decided_by"] == "default", "умолчание не смеет выдавать себя за владельца"
    assert "решено умолчанием 16.08.2026" in rec["decision"]
    assert "git revert <sha>" in rec["decision"], "откат обязан быть В записи"
    assert pd.list_open(today=_dt.date(2026, 8, 16)) == []


def test_silence_does_not_close_before_the_date(store):
    """Днём раньше — ничего. Негативный контроль на «sweep косит всё подряд»."""
    pd.park("g", "dev_fix", "мелкий фикс",
            default="применить патч", rollback="git revert <sha>",
            auto_after=_dt.date(2026, 8, 16), executor="fix")
    assert pd.sweep_defaults(today=_dt.date(2026, 8, 15), execute=lambda r: True) == []
    assert [r["id"] for r in pd.list_open(today=_dt.date(2026, 8, 15))] == ["g"]


def test_card_without_trio_never_closes_by_silence(store):
    """Fail-closed: карточка без тройки не закроется и через год.
    Это несущая граница «не применяется к медицинскому и необратимому»."""
    pd.park("med", "owner_decision", "порог аналита — твой оракул")
    assert pd.sweep_defaults(today=_dt.date(2027, 8, 2), execute=lambda r: True) == []
    assert [r["id"] for r in pd.list_open(today=_dt.date(2027, 8, 2))] == ["med"]
    assert pd.get("med")["status"] == "open"


def test_partial_trio_is_rejected_at_park(store):
    """Вариант без отката — обещание, которое нечем отменить. Рубим на входе."""
    with pytest.raises(ValueError, match="тройка умолчания неполна"):
        pd.park("g", "dev_fix", "s", default="применить", auto_after=_dt.date(2026, 8, 16))
    with pytest.raises(ValueError, match="тройка умолчания неполна"):
        pd.park("g", "dev_fix", "s", default="применить", rollback="revert")
    assert pd.list_open() == [], "отвергнутый park не должен оставить следа"


def test_deferred_card_is_not_overridden_by_silence(store):
    """defer — явный акт владельца «не сейчас». Умолчание его не перебивает,
    даже когда auto_after уже прошёл: иначе система спорит с человеком."""
    pd.park("g", "dev_fix", "мелкий фикс",
            default="применить патч", rollback="git revert <sha>",
            auto_after=_dt.date(2026, 8, 16), executor="fix")
    pd.defer("g", _dt.date(2026, 9, 1))
    assert pd.sweep_defaults(today=_dt.date(2026, 8, 20), execute=lambda r: True) == []
    assert pd.get("g")["status"] == "deferred"


def test_record_decision_marks_owner_by_default(store):
    """Ручной путь не изменился: без by= автор — владелец."""
    pd.park("g", "stage_closure", "этап готов")
    pd.record_decision("g", "закрываю")
    assert pd.get("g")["decided_by"] == "owner"
    with pytest.raises(ValueError, match="ожидается один из"):
        pd.record_decision("g", "что-то", by="robot")



# ── Умолчание исполняется, а не записывается (решение владельца 23.09) ─────────

def test_default_without_executor_is_rejected_at_park(store):
    """Вариант, который нечем исполнить, не решается молчанием — рубим на входе."""
    with pytest.raises(ValueError, match="без исполнителя"):
        pd.park("g", "dev_fix", "s", default="Заблокировать", rollback="разблокировать",
                auto_after=_dt.date(2026, 8, 16))


def test_unexecuted_default_is_not_recorded(store):
    """Исполнитель не смог (или его не дали) — решения нет, карточка висит. До 23.09
    здесь записывалось бы «решено умолчанием» без единого действия."""
    pd.park("g", "dev_fix", "s", default="Заблокировать", rollback="разблокировать",
            auto_after=_dt.date(2026, 8, 16), executor="block")
    assert pd.sweep_defaults(today=_dt.date(2026, 8, 20)) == []
    assert pd.sweep_defaults(today=_dt.date(2026, 8, 20), execute=lambda r: False) == []
    assert pd.get("g")["status"] == "open"


def test_drop_default_keeps_card_on_the_desk(store):
    pd.park("g", "dev_fix", "s", default="Заблокировать", rollback="разблокировать",
            auto_after=_dt.date(2026, 8, 16), executor="block")
    pd.drop_default("g")
    rec = pd.get("g")
    assert rec["status"] == "open" and "auto_after" not in rec and "default" not in rec
