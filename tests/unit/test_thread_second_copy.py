"""Оракул: работа нити не остаётся в одном экземпляре, и молчание об этом запрещено.

ЗАМЕР, РАДИ КОТОРОГО ЭТО ЕСТЬ (16.09.2026). `post-commit` пушит ТОЛЬКО main —
«не main → выход ДО push» написано в самом хуке, — а `backup.sh` снимал одну лишь
главную копию. Значит закоммиченная работа нити от первого коммита до слияния
лежала на ОДНОМ диске: в момент замера так лежали 992 строки двух нитей
(`close-rebase-once` 757 строк / 22 ч, `dead-root-loud` 235 / 62 ч), и
`git cat-file -e` на Studio отвечал «нет» для обоих tip'ов.

Отсрочка 6 ч (решение владельца) = два периода снимка `backup-wip`. Нить живёт
медиану 12 минут (18 слияний за 30 дней), поэтому порог меньше периода звенел бы
на каждой здоровой нити — §13.

ЧТО ЗДЕСЬ НЕ ПРОВЕРЯЕТСЯ. Что push из `backup.sh` реально доезжает до Studio:
у юнита нет второй машины. Это проверено живым прогоном (снимок нити) и стережётся
самим датчиком — отсутствие объекта он называет вслух.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

ЧАС = 3600


def _стенд(monkeypatch, *, сообщение, есть_объект, cap):
    """Подменяет единственный шов наружу — git_facts._git (§20)."""
    import integrity_tests as I
    import git_facts as gf
    monkeypatch.setattr(I, "warn", lambda n, d="": cap.append((n, d)))

    def fake(args):
        if args[0] == "log":
            return сообщение
        if args[0] == "cat-file":
            return "" if есть_объект(args[-1]) else None
        raise AssertionError(f"датчик позвал git неожиданно: {args}")

    monkeypatch.setattr(gf, "_git", fake)
    return I


def _поле(*ветки):
    """ветки: (slug, sha, часов_назад)."""
    now = int(time.time())
    return ",".join(f"{s}:{sha}:{now - int(h * ЧАС)}" for s, sha, h in ветки)


def _снимок(поле):
    return f"snapshot: wip backup 2026-09-16 (вне main) worktrees_uncovered=2 threads={поле}"


def test_ветка_старше_отсрочки_без_копии_названа(monkeypatch):
    """⭐ Воспроизведён случай 16.09: две ветки, копий нет, обе старше отсрочки."""
    cap = []
    поле = _поле(("close-rebase-once", "8ef630c804b7", 22),
                 ("dead-root-loud", "28d65b706431", 62))
    I = _стенд(monkeypatch, сообщение=_снимок(поле), есть_объект=lambda _: False, cap=cap)
    r = I.check_thread_work_has_a_second_copy()
    assert cap, "работа в одном экземпляре — и датчик промолчал"
    имя, _ = cap[0]
    assert "close-rebase-once" in имя and "dead-root-loud" in имя, (
        f"нити не названы поимённо — чинить нечего:\n{имя}")
    assert len(r["missing"]) == 2


def test_свежая_ветка_не_звенит(monkeypatch):
    """Нить живёт медиану 12 минут: порог меньше периода снимка = шум на каждой."""
    cap = []
    поле = _поле(("только-что", "aaaaaaaaaaaa", 0.5))
    I = _стенд(monkeypatch, сообщение=_снимок(поле), есть_объект=lambda _: False, cap=cap)
    r = I.check_thread_work_has_a_second_copy()
    assert cap == [], f"датчик звенит на нити моложе отсрочки: {cap}"
    assert r["missing"] == []


def test_копия_доехала_молчание(monkeypatch):
    cap = []
    поле = _поле(("старая-нить", "bbbbbbbbbbbb", 30))
    I = _стенд(monkeypatch, сообщение=_снимок(поле), есть_объект=lambda _: True, cap=cap)
    r = I.check_thread_work_has_a_second_copy()
    assert cap == [], f"копия есть, а датчик звенит: {cap}"
    assert r["missing"] == []


def test_нитей_нет_молчание(monkeypatch):
    cap = []
    I = _стенд(monkeypatch, сообщение=_снимок("нет"),
               есть_объект=lambda _: False, cap=cap)
    r = I.check_thread_work_has_a_second_copy()
    assert cap == [] and r["missing"] == []


def test_снимок_без_поля_threads_громкий(monkeypatch):
    """«Не знаю» не равно «копии есть».

    Старый `backup.sh` пишет снимок без поля `threads=`. Если такой снимок читать
    как «нитей нет», датчик замолчит ровно там, где он и нужен: старая джоба не
    копирует ветки вовсе. Это тот же класс, что blind_sensor_is_loud.
    """
    cap = []
    I = _стенд(monkeypatch,
               сообщение="snapshot: wip backup 2026-09-10 (вне main) worktrees_uncovered=0",
               есть_объект=lambda _: False, cap=cap)
    r = I.check_thread_work_has_a_second_copy()
    assert cap, "снимок без списка веток принят за «всё хорошо»"
    assert r["threads_field"] is None


def test_граница_отсрочки_судится(monkeypatch):
    """Ровно на пороге ветка уже судится: иначе граница необъявлена."""
    import integrity_tests as I

    def нет(_):
        return False

    ровно, _ = I.thread_copies_missing(_поле(("край", "cccccccccccc", 6.0)),
                                       нет, time.time())
    раньше, _ = I.thread_copies_missing(_поле(("край", "cccccccccccc", 5.5)),
                                        нет, time.time())
    assert len(ровно) == 1, "на пороге 6 ч ветка не судится — граница потеряна"
    assert раньше == [], "судится то, что моложе порога"


def test_битая_запись_не_роняет_датчик(monkeypatch):
    """Мусор в поле не должен глушить ОСТАЛЬНЫЕ записи — иначе одна битая строка
    делает датчик слепым ко всем нитям разом."""
    import integrity_tests as I
    now = int(time.time())
    поле = f"кривая-запись,норм:dddddddddddd:{now - 10 * ЧАС}"
    out, broken = I.thread_copies_missing(поле, lambda _: False, time.time())
    assert [s for s, _sha, _a in out] == ["норм"], f"разбор поля потерял живую запись: {out}"
    assert broken == ["кривая-запись"], (
        f"битая запись проглочена молча — про эту нить датчик не знает, и это не слышно: {broken}")


def test_битая_запись_названа_вслух(monkeypatch):
    """Испорченный список = «про эти нити не знаю», и это обязано звучать.

    Первая редакция делала `continue` на неразобранной записи — тихий обработчик,
    и ратчет тихих обработчиков поймал его на полном прогоне Studio. Он прав по
    сути: молчание о непонятой записи неотличимо от «копия есть».
    """
    cap = []
    now = int(time.time())
    поле = f"мусор,норм:eeeeeeeeeeee:{now - 10 * ЧАС}"
    I = _стенд(monkeypatch, сообщение=_снимок(поле), есть_объект=lambda _: True, cap=cap)
    r = I.check_thread_work_has_a_second_copy()
    assert any("неразобранн" in имя for имя, _ in cap), (
        f"битая запись не названа: {cap}")
    assert r["broken"] == ["мусор"]



# ── Нить-сирота: ветка жива, строки в INDEX нет (22.09, решение владельца) ──────
# Живой случай: `dead-root-loud` 9 дней без строки — две нити повторили её работу.

_INDEX = "| Нить | Название | ID | Статус |\n|---|---|---|---|\n| `есть` | x | `есть@1` | open |\n"


def test_сирота_старше_порога_видна_а_записанная_и_свежая_нет():
    import integrity_tests as I
    now = time.time()
    поле = ",".join([f"сирота:aaaaaaaaaaaa:{int(now - 216 * ЧАС)}",   # 9 суток, как dead-root-loud
                     f"есть:bbbbbbbbbbbb:{int(now - 216 * ЧАС)}",     # строка в INDEX есть
                     f"свежая:cccccccccccc:{int(now - 47 * ЧАС)}"])   # моложе 48 ч
    orphans, broken = I.threads_without_index_row(поле, _INDEX, now)
    assert [s for s, _ in orphans] == ["сирота"] and broken == []


def test_сирота_битая_запись_названа_а_не_проглочена():
    import integrity_tests as I
    orphans, broken = I.threads_without_index_row("кривая-запись", _INDEX, time.time())
    assert orphans == [] and broken == ["кривая-запись"]
    assert I.threads_without_index_row(None, _INDEX, time.time()) is None


@pytest.mark.owner_data
def test_сирота_разбор_настоящего_INDEX():
    """Формат строки реестра читается на НАСТОЯЩЕМ INDEX, а не только на образце:
    сменись разметка таблицы — все нити стали бы сиротами (или ни одна)."""
    import integrity_tests as I
    text = (ROOT / "docs/handoff/INDEX.md").read_text(encoding="utf-8")
    now = time.time()
    поле = f"night-cycle:aaaaaaaaaaaa:{int(now - 100 * ЧАС)},нет-такой-нити:bbbbbbbbbbbb:{int(now - 100 * ЧАС)}"
    orphans, _ = I.threads_without_index_row(поле, text, now)
    assert [s for s, _ in orphans] == ["нет-такой-нити"]


@pytest.mark.owner_data
def test_сирота_датчик_называет_поимённо(monkeypatch):
    """⭐ Датчик целиком (снимок → реестр → warn): случай 22.09 воспроизведён на
    НАСТОЯЩЕМ INDEX с веткой, которой в нём нет. Имя нити — в тексте warn."""
    cap = []
    поле = _поле(("нет-такой-нити-в-реестре", "28d65b706431", 216))
    I = _стенд(monkeypatch, сообщение=_снимок(поле), есть_объект=lambda _: True, cap=cap)
    r = I.check_threads_have_index_row()
    assert [s for s, _ in r["orphans"]] == ["нет-такой-нити-в-реестре"]
    assert any("нет-такой-нити-в-реестре" in имя for имя, _ in cap), cap


def test_сирота_снимок_без_поля_не_молчит(monkeypatch):
    """Снимок старого backup.sh без threads= — «не знаю», а не «сирот нет»."""
    cap = []
    I = _стенд(monkeypatch, сообщение="snapshot: wip backup без поля", есть_объект=lambda _: True, cap=cap)
    assert I.check_threads_have_index_row() is None
    assert any("судить нечем" in имя for имя, _ in cap), cap
