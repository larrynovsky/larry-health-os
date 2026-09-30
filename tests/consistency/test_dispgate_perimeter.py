"""tests/consistency/test_dispgate_perimeter.py — ратчет над ГРАНИЦЕЙ периметра §15.

ЗАЧЕМ. Ратчет известных обходов (`test_dispgate_known_bypasses.py`) стережёт дыры ВНУТРИ
судимой зоны. Эта проверка стережёт саму зону. Повод конкретный: §15 обещал «исключение ровно
одно и оно про отсутствие ПРЕДМЕТА — пустой `__init__.py`», а фактически вне суда лежали
семнадцать каталогов, потому что и `dispgate`, и `dupgate` берут scope из `indexer.SKIP`
(«prod-scope = scope индекса»). Обнаружено позитивным контролем 2026-07-28: сайдкар у файла
в `plans/` удалён, `dispgate --block` вернул 0. Норма обещала больше, чем делает код.

ЧТО ЭТО ДОКАЗЫВАЕТ. Что каждый каталог вне суда НАЗВАН и имеет записанную причину, и наоборот —
что записанная причина соответствует живой строке в `SKIP`. Список каталогов живёт в ОДНОМ доме
(`indexer.SKIP`), причины — в другом (`project_context/perimeter.json`); второго списка нет,
поэтому расходиться нечему, кроме пары «строка ↔ причина», и её-то и держит этот тест.

ЧЕГО НЕ ДОКАЗЫВАЕТ. Что причина осмысленна — это tacit-оракул (человек), машинно неотличимо,
ровно как `legacy`-пометка в K1. И что граница проведена ПРАВИЛЬНО: «мастерская вне суда» —
решение владельца, а не выводимая истина. Тест держит согласованность, не мудрость.

ЦЕНА. Доли секунды: чтение двух файлов, без клонов и подпроцессов.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.consistency

ROOT = Path(__file__).resolve().parents[2]
PERIMETER = ROOT / "project_context" / "perimeter.json"


def _doc() -> dict:
    return json.loads(PERIMETER.read_text(encoding="utf-8"))


def _declared() -> dict:
    return _doc()["dirs"]


def _classes() -> set[str]:
    return set(_doc()["_criterion"]["classes"])


def _actual() -> set[str]:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from project_context import indexer
    return set(indexer.SKIP)


def test_every_unjudged_directory_is_named_with_a_cause():
    """Множество каталогов вне суда РАВНО множеству объявленных причин — в обе стороны."""
    actual, declared = _actual(), _declared()

    undeclared = sorted(actual - set(declared))     # вне суда, но причина не записана
    stale = sorted(set(declared) - actual)          # причина есть, каталога в SKIP нет

    assert not undeclared and not stale, (
        "граница периметра §15 разошлась с объявленной.\n"
        + (f"  ВНЕ СУДА БЕЗ ПРИЧИНЫ (допиши в project_context/perimeter.json либо убери из "
           f"indexer.SKIP): {undeclared}\n" if undeclared else "")
        + (f"  ПРИЧИНА БЕЗ КАТАЛОГА (каталог вернулся под суд — вычеркни причину): {stale}\n"
           if stale else "")
        + "  Оба случая — решение человека: каталог уходит из-под суда двух гейтов сразу "
          "(§15 одноразовость и дубль-гейт), молча это делать нельзя."
    )


@pytest.mark.parametrize("name", sorted(_declared()))
def test_cause_is_present_and_classified(name):
    """Пустая причина = отсутствие причины. Осмысленность машинно не судится (tacit)."""
    rec = _declared()[name]
    assert rec.get("cause", "").strip(), f"{name}: причина пуста — это не объявление, а умолчание"
    assert rec.get("class", "").strip(), f"{name}: не назван класс — под какой пункт критерия"


@pytest.mark.parametrize("name", sorted(_declared()))
def test_class_comes_from_the_closed_criterion(name):
    """Список классов ЗАКРЫТ: каталог уходит из-под суда, только назвав, какое из четырёх
    условий критерия он не выполняет.

    Без этого файл вырождается в список исключений с прозой рядом — а список растёт молча,
    и через год никто не помнит, почему там каждая строка. Новый класс = изменение критерия
    = решение владельца, и оно обязано пройти через правку `_criterion`, а не через дописанное
    поле у одной папки."""
    klass = _declared()[name].get("class", "")
    allowed = _classes()
    assert klass in allowed, (
        f"{name}: класс {klass!r} не входит в закрытый список критерия {sorted(allowed)}. "
        f"Либо подведи каталог под существующий пункт, либо меняй сам критерий в "
        f"project_context/perimeter.json::_criterion — это решение владельца, не правка по дороге."
    )


@pytest.mark.parametrize("klass", sorted(_doc()["_criterion"]["classes"]))
def test_each_class_says_what_it_exempts_from(klass):
    """Два требования — два периметра: класс обязан ответить ОТДЕЛЬНО про K1 и про K2.

    До ред. 2 класс освобождал целиком: файл, которому не нужен характеризационный тест,
    уходил из-под суда вместе с обязанностью объявить контракт и вынести вердикт. Одна
    граница на два разных вопроса — и была источником неправды."""
    rec = _doc()["_criterion"]["classes"][klass]
    assert isinstance(rec, dict), f"{klass}: класс описан строкой — нечем ответить про K1/K2 раздельно"
    for k in ("k1", "k2"):
        assert isinstance(rec.get(k), bool), (
            f"{klass}: поле {k} обязано быть true/false — «не сказано» освобождением не является"
        )
    assert str(rec.get("why") or "").strip(), f"{klass}: нет обоснования"


def test_every_class_decided_about_canon_influence():
    """Каждый класс обязан быть РОВНО в одном списке: перекрывается влиянием на канон или нет.

    Ред. 3 привязала строгость к влиянию, а не к раскладке каталогов. Значит у каждого класса
    появился второй вопрос: снимается ли его освобождение, когда файл дотягивается до боевой
    базы. Разбиение полное и непересекающееся — иначе новый класс молча получит умолчание,
    а умолчание здесь и есть тот способ, которым дыры заводятся."""
    canon = _doc()["_canon_access"]
    over = set(canon.get("overrides_exemption_for") or ())
    never = set(canon.get("never_overrides") or ())
    classes = _classes()

    both = sorted(over & never)
    missing = sorted(classes - over - never)
    stray = sorted((over | never) - classes)
    assert not both and not missing and not stray, (
        "решение про влияние на канон принято не для всех классов ровно один раз.\n"
        + (f"  В ОБОИХ СПИСКАХ: {both} — противоречие, а не решение\n" if both else "")
        + (f"  НЕ РЕШЕНО: {missing} — класс есть, ответа про канон нет\n" if missing else "")
        + (f"  ЛИШНИЕ ИМЕНА: {stray} — класса не существует\n" if stray else "")
    )
    assert canon.get("modules"), "не названо, ЧЕМ открывают канон — признак вырожден в пустой"


def test_criterion_is_declared_closed():
    """Сам критерий обязан объявлять себя закрытым — иначе «закрытость» держится на моей памяти."""
    crit = _doc()["_criterion"]
    assert crit.get("classes_are_closed") is True, (
        "критерий перестал объявлять список классов закрытым — граница снова открыта на дописывание"
    )
    assert crit.get("positive", "").strip(), "нет положительной формулировки: что ИМЕННО судится"


@pytest.mark.owner_data
def test_which_unjudged_dirs_actually_hold_python_matches_the_measurement():
    """Сторожится ФОРМА замера, а не числа.

    Замер 2026-07-29 показал: из семнадцати каталогов вне суда `.py` есть ровно в трёх.
    Счётчики растут каждый день (тесты), и сверять их значило бы держать вечно красный тест —
    шум, который научат игнорировать. А вот СОСТАВ каталогов с кодом меняться молча не должен:
    новый каталог вне суда, в котором завёлся Python, — ровно то событие, ради которого этот
    файл написан. Числа в `_measured_*` остаются снимком с датой и инструкцией, как повторить.
    """
    import subprocess
    declared = set(_doc()["_measured_2026_07_29"]["dirs_with_any_py"])
    # `git ls-files` — предпочтительный источник (только отслеживаемые файлы), но
    # прогон идёт и на КОПИИ дерева без `.git` (scripts/test_on_studio.sh синкает
    # WIP на Studio, исключая `.git`). Там git отдавал rc=128, и тест был красным
    # ВСЕГДА — то есть в единственном месте, где гоняется весь набор, он не проверял
    # ничего, а приучал не смотреть на красноту. Наблюдалось 2026-07-29 в прогоне.
    try:
        tracked = subprocess.check_output(["git", "ls-files", "*.py"], cwd=str(ROOT),
                                          text=True).splitlines()
    except (subprocess.CalledProcessError, FileNotFoundError):
        tracked = [str(p.relative_to(ROOT)) for p in ROOT.rglob("*.py")
                   if "__pycache__" not in p.parts and ".git" not in p.parts]
    outside = _actual()
    # Атрибуция по ВНЕШНЕМУ совпадению, а не по всем сразу. `tests/snapshots/x.py` выпал из суда
    # из-за `tests`, и приписывать его ещё и `snapshots` значит объявить кодом вне суда каталог,
    # который к этому файлу отношения не имеет. Первая редакция теста считала все компоненты и
    # немедленно покраснела на ровном месте — поймала не дыру, а собственную арифметику.
    live = set()
    for f in tracked:
        outer = next((p for p in f.split("/")[:-1] if p in outside), None)
        if outer:
            live.add(outer)

    appeared, emptied = sorted(live - declared), sorted(declared - live)
    assert not appeared and not emptied, (
        "состав каталогов вне суда, содержащих Python, разошёлся с замером.\n"
        + (f"  ЗАВЁЛСЯ КОД ВНЕ СУДА: {appeared} — посмотри, что это за файлы, и реши: они правда "
           f"не выполняют условие критерия, или каталог пора судить\n" if appeared else "")
        + (f"  КОД УШЁЛ: {emptied} — обнови замер в perimeter.json\n" if emptied else "")
        + "  Числа в замере не сверяются намеренно (растут каждый день); сверяется состав."
    )


def test_the_number_in_the_norm_matches_the_live_list():
    """Число в тексте нормы — такое же утверждение, как строка кода, и протухает так же.

    §15 называет, сколько каталогов вне суда. Это не иллюстрация: читатель по нему судит о
    масштабе. Расхождение с живым `SKIP` = норма врёт цифрой, и заметить это без сверки нельзя."""
    import re as _re
    norm = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    m = _re.search(r"каталогов вне суда:\s*(\d+)", norm)
    assert m, "в §15 больше нет машинно-читаемого числа каталогов вне суда — сверять нечего"
    assert int(m.group(1)) == len(_actual()), (
        f"§15 говорит «каталогов вне суда: {m.group(1)}», в indexer.SKIP их {len(_actual())}. "
        f"Кто-то изменил список и не поправил норму — либо наоборот."
    )


def test_norm_points_the_reader_at_the_perimeter_file():
    """Вторая половина связки: читатель §15 обязан узнать, ГДЕ смотреть границу.

    Без этого механизм честен, а норма по-прежнему обещает «исключение ровно одно» —
    и расхождение переезжает из кода в текст, вместо того чтобы закрыться.
    """
    norm = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert "project_context/perimeter.json" in norm, (
        "§15 не называет perimeter.json — граница снова видна только машине"
    )
