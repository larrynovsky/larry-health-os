"""Проектный слой периметра §15 (2026-09-12).

ЗАЧЕМ. Движок домен-агностичен везде, кроме `perimeter.json`: карта каталогов и имя канона
написаны под health_scripts. Второму проекту это стоило гейта целиком — в соседнем проекте миграции и
оснастки живут в `scripts/`, каталога с таким именем в движковой карте нет, и каждый новый
файл там требовал бы характеризационный тест, то есть заглушку ради зелёного (замер 12.09:
одиннадцать ложных блоков из тридцати шести новых файлов за месяц). Сторож, который регулярно
неправ, учит писать `--no-verify`.

ЧТО ДОКАЗЫВАЕТСЯ ЗДЕСЬ. Что проектный слой добавляет каталоги и канон — и что три его границы
держатся: классы остаются закрытым списком движка, движковое освобождение не перекрывается
проектом, а неизвестный класс не освобождает вовсе.

ПОЧЕМУ ГРАНИЦЫ ВАЖНЕЕ САМОЙ ВОЗМОЖНОСТИ. Без них «проектная политика» становится способом
вывести из-под суда что угодно, написав рядом любое слово. Тогда гейт есть, а судить он
перестаёт — и это хуже, чем его отсутствие, потому что выглядит как защита.
"""
from __future__ import annotations

import json
import os

import pytest

from project_context import dispgate

pytestmark = pytest.mark.unit


def _project(tmp_path, perimeter: dict | None):
    """Каталог проекта со своей политикой (или без неё)."""
    if perimeter is not None:
        (tmp_path / "project_context.json").write_text(
            json.dumps({"perimeter": perimeter}, ensure_ascii=False), encoding="utf-8")
    dispgate._PERIMETER_CACHE.clear()
    return str(tmp_path)


def test_project_directory_with_known_class_is_exempt(tmp_path):
    """`scripts/` в соседнем проекте — дом миграций и оснасток: контракт спрашиваем, тест нет."""
    root = _project(tmp_path, {"dirs": {"scripts": {"class": "not_executed_in_operation",
                                                    "cause": "оснастки и миграции проекта"}}})
    pol = dispgate.perimeter_policy(root)
    k1, k2 = dispgate.demands_for("scripts/migrate_thing.py", pol)
    assert (k1, k2) == (True, False), "проектный каталог не применился"


def test_unknown_class_does_not_exempt_anything(tmp_path):
    """Неизвестный класс — не освобождение, а мусор. Fail-closed: судим полностью.

    Проверяем саму карту, а не только приговор: `demands_for` возвращает (True, True) и для
    неизвестного класса, поэтому по одному приговору мутацию «пускать любой класс» не видно.
    Красным должно становиться попадание мусора в карту.
    """
    root = _project(tmp_path, {"dirs": {"scripts": {"class": "мастерская",
                                                    "cause": "потому что так хочется"}}})
    pol = dispgate.perimeter_policy(root)
    assert "scripts" not in pol["dirs"], "неизвестный класс просочился в карту каталогов"
    assert dispgate.demands_for("scripts/x.py", pol) == (True, True)


def test_project_cannot_override_engine_directory(tmp_path):
    """`tests/` остаётся judgement_harness, чем бы проект его ни назвал.

    Иначе освобождение стало бы переносимым: любой проект мог бы объявить свои тесты
    обычными модулями и потребовать у них контракт — или наоборот, назвать прод-каталог
    оснасткой суждения и увести его из-под гейта целиком.
    """
    root = _project(tmp_path, {"dirs": {"tests": {"class": "not_rewritable", "cause": "нет"}}})
    pol = dispgate.perimeter_policy(root)
    assert pol["dirs"]["tests"] == "judgement_harness"


def test_project_canon_is_added_not_replaced(tmp_path):
    """Проект называет свой канон, движковый при этом не исчезает."""
    root = _project(tmp_path, {"canon": {"modules": ["models"]}})
    pol = dispgate.perimeter_policy(root)
    mods = set(pol["canon"].get("modules") or ())
    assert "models" in mods, "проектный канон не подхвачен"
    assert "health_db" in mods, "движковый канон затёрт — правка стала подменой, а не слоем"


def test_project_without_policy_behaves_exactly_as_before(tmp_path):
    """Проект без своей карты судится движковой — регрессии для health_scripts нет."""
    root = _project(tmp_path, None)
    pol = dispgate.perimeter_policy(root)
    assert dispgate.demands_for("plans/probe.py", pol) == (True, False)
    assert dispgate.demands_for("scripts/x.py", pol) == (True, True)


def test_broken_project_json_no_longer_pretends_to_be_the_safe_side(tmp_path):
    """НАДГРОБИЕ прежнего утверждения. Здесь стояло «битый json — безопасная сторона».

    Это было неправдой, и тест её не ловил: он судил `scripts/x.py`, то есть файл БЕЗ
    движкового освобождения, где fallback и правда строг. Утверждение же формулировалось
    широко. Внешний аудит 12.09.2026 нашёл контрпример на пересечении движкового
    освобождения с проектным усилением (F-01) — и он доходил до настоящего успешного
    коммита. Нынешнее поведение: негодный файл — отказ, а не пустота; полный набор входов
    проверяет тест ниже. Имя оставлено узнаваемым намеренно: у исправленной неправды должен
    быть адрес.
    """
    (tmp_path / "project_context.json").write_text("{ это не json", encoding="utf-8")
    dispgate._PERIMETER_CACHE.clear()
    with pytest.raises(dispgate.indexer.ProjectManifestUnusable):
        dispgate.perimeter_policy(str(tmp_path))


# ── дом чтения проектного файла ровно один ───────────────────────────────────

def test_project_context_json_has_exactly_one_reader():
    """`project_context.json` открывает ровно `indexer.project_manifest`.

    Судим по AST-вызову `open(...)`, а не по грепу имени: файл законно УПОМИНАЕТСЯ в
    pathspec'е стейджа и в подсказке CLI, и греп по имени краснел бы на них. Здесь ловится
    ровно то, что вредно — вторая редакция «безопасной стороны» (нет файла/битый json → ?),
    которая при первой же правке разъедется с первой.
    """
    import ast as _ast
    import pathlib

    repo = pathlib.Path(__file__).resolve().parents[2]
    allowed = {"project_context/indexer.py"}
    bad = []
    for path in sorted((repo / "project_context").rglob("*.py")):
        rel = str(path.relative_to(repo))
        if rel in allowed:
            continue
        try:
            tree = _ast.parse(path.read_text(encoding="utf-8"))
        except Exception:
            continue  # silent-ok: нечитаемый/несобираемый файл не судим
        for node in _ast.walk(tree):
            if not (isinstance(node, _ast.Call) and isinstance(node.func, _ast.Name)
                    and node.func.id == "open" and node.args):
                continue
            if any(isinstance(s, _ast.Constant) and s.value == "project_context.json"
                   for s in _ast.walk(node.args[0])):
                bad.append(f"{rel}:{node.lineno}")
    assert not bad, "чтение project_context.json в обход project_manifest: " + ", ".join(bad)


# ── негодная проектная политика: ремонт F-01 (внешнее ревью 12.09.2026) ──────

@pytest.mark.parametrize("payload, что_сломано", [
    ("{ это не json", "json не разбирается"),
    ("null", "верхний уровень null"),
    ("[]", "верхний уровень список"),
    ('{"perimeter": "нет"}', "perimeter не объект"),
    ('{"perimeter": {"dirs": "нет"}}', "dirs не объект"),
    ('{"perimeter": {"canon": "нет"}}', "canon не объект"),
    ('{"perimeter": {"canon": {"modules": "models"}}}', "modules строка вместо списка"),
    ('{"perimeter": {"canon": {"modules": [1, 2]}}}', "modules список не строк"),
])
def test_unusable_project_policy_is_a_refusal_not_an_empty_one(tmp_path, payload, что_сломано):
    """Негодный проектный файл — отказ инструмента суждения, а не «политики нет».

    ПОЧЕМУ ЭТО НЕ ПРИДИРКА К ТИПАМ. Прежняя редакция сводила «файла нет» и «файл негоден»
    к одному пустому словарю, опираясь на посылку «без политики всегда строже». Посылка
    неверна: проектный слой не только ДОБАВЛЯЕТ освобождения, он ещё и УСИЛИВАЕТ строгость,
    называя канон проекта. Потеряв канон, но сохранив движковое освобождение каталога
    `migrations`, гейт переставал требовать характеризацию с миграции, которая импортирует
    канон, — и пропускал её до настоящего успешного `git commit` (внешний аудит F-01).

    ЧЕМУ УЧИТ ПРОМАХ ПЕРВОГО ДАТЧИКА. `test_broken_project_json_is_the_safe_side` проверял
    битый json на `scripts/x.py` — файле БЕЗ движкового освобождения, где fallback
    действительно строг. Он зелёный и при сломанном механизме: узкое утверждение верно,
    широкий вывод «битый json безопасен» — нет. Контрпример живёт ровно на пересечении
    движкового освобождения и проектного усиления, и искать его надо было там.
    """
    (tmp_path / "project_context.json").write_text(payload, encoding="utf-8")
    dispgate._PERIMETER_CACHE.clear()
    with pytest.raises(dispgate.indexer.ProjectManifestUnusable):
        dispgate.perimeter_policy(str(tmp_path))


def test_absent_project_policy_is_not_a_refusal(tmp_path):
    """Отсутствие файла — законное состояние, а не отказ: движок домен-агностичен.

    Граница ремонта именно здесь. Сделать негодный файл блоком легко; легко и переусердствовать,
    потребовав манифест у проекта, которому он не нужен. Тогда движок перестал бы работать на
    чужом репозитории вовсе — то есть починка одного класса ложных пропусков завела бы класс
    ложных блоков."""
    dispgate._PERIMETER_CACHE.clear()
    pol = dispgate.perimeter_policy(str(tmp_path))
    assert pol is not None, "проект без политики обязан судиться движковой картой"
    assert dispgate.demands_for("scripts/x.py", pol) == (True, True)


def test_other_readers_keep_their_own_failure_policy(tmp_path):
    """Потребители ЧИСЕЛ негодный файл по-прежнему читают как «политики нет».

    Строгость подняли ровно одному потребителю — тому, у кого негодный вход МЕНЯЕТ строгость
    суждения. У остальных (пороги, списки-дополнения) политика отказа своя и принята раньше;
    менять её заодно значило бы протащить чужое решение под видом починки."""
    (tmp_path / "project_context.json").write_text("{ это не json", encoding="utf-8")
    assert dispgate.indexer.project_manifest(str(tmp_path)) == {}
    pol = dispgate.indexer.disposability_policy(str(tmp_path))
    assert pol["limits"], "дефолты движка обязаны остаться читаемыми"


# ── ремонт по раунду 2 внешнего ревью (12.09.2026) ───────────────────────────

@pytest.mark.parametrize("payload, что_сломано", [
    ('{"perimeter": {"dirs": {"scripts": "нет"}}}', "запись каталога строкой"),
    ('{"perimeter": {"dirs": {"scripts": [1]}}}', "запись каталога списком"),
    ('{"perimeter": {"dirs": {"scripts": 7}}}', "запись каталога числом"),
])
def test_broken_directory_record_is_named_not_swallowed(tmp_path, payload, что_сломано):
    """Негодная ЗАПИСЬ каталога — тоже отказ, и назван проектный файл (F-R2-03).

    Проверялся сам `dirs`, а его записи — нет: строка вместо объекта улетала в общий
    `except`, тот отдавал None, и человек получал блок с именем ДВИЖКОВОГО `perimeter.json`
    — целого файла из чужого репозитория. Суждение при этом не слабело (None блокирует
    всё), ломалось другое: человека звали чинить не тот файл.
    """
    (tmp_path / "project_context.json").write_text(payload, encoding="utf-8")
    dispgate._PERIMETER_CACHE.clear()
    with pytest.raises(dispgate.indexer.ProjectManifestUnusable) as e:
        dispgate.perimeter_policy(str(tmp_path))
    assert "project_context.json" in str(e.value), "отказ не называет испорченный файл"


@pytest.mark.parametrize("payload", [
    '{"perimeter": "нет"}',
    '{"perimeter": {"dirs": "нет"}}',
    '{"perimeter": {"canon": []}}',
    '{"perimeter": {"canon": {"modules": "models"}}}',
])
def test_refusal_always_names_the_project_file(tmp_path, payload):
    """У отказа есть АДРЕС (F-R2-04).

    Шесть форм негодности блокировали, не упомянув ни пути, ни имени файла: «perimeter: str,
    а не объект» — и всё. Соседний ридер (`indexer`) путь при этом нёс; асимметрия жила
    внутри одного пути отказа. Имя намеренно не абсолютный путь: строгий читатель судит
    одноразовый снимок индекса, и путь вёл бы в удалённый `/tmp/pc-index-*` (F-R2-05).
    """
    (tmp_path / "project_context.json").write_text(payload, encoding="utf-8")
    dispgate._PERIMETER_CACHE.clear()
    with pytest.raises(dispgate.indexer.ProjectManifestUnusable) as e:
        dispgate.perimeter_policy(str(tmp_path))
    assert "project_context.json" in str(e.value)


@pytest.mark.parametrize("klass", ["judgement_harness", "not_source", "not_python_module"])
def test_project_cannot_hand_itself_a_never_overridden_class(tmp_path, klass):
    """Третья граница слоя: классы, которые не снимаются каноном, проект не назначает (F-R2-06).

    Такой класс сильнее любого движкового освобождения: файл не читается вовсе, и касание
    канона до него не дотягивается. Раунд 2 показал это исполнением — `scripts` →
    `judgement_harness`, и миграция, импортирующая канон проекта, проходила без контракта,
    вердикта и теста. Прежняя формулировка границы перечисляла два «не может» и молчала про
    третье — ровно то занижение возможностей проекта, из которого вырос F-01.
    """
    root = _project(tmp_path, {"dirs": {klass + "_home": {"class": klass, "cause": "мой каталог"}}})
    with pytest.raises(dispgate.indexer.ProjectManifestUnusable) as e:
        dispgate.perimeter_policy(root)
    assert klass in str(e.value), "отказ не называет запрещённый класс"


def test_project_still_may_use_overridable_classes(tmp_path):
    """Позитивный контроль к предыдущему: перекрываемые каноном классы проекту доступны.

    Без этого теста запрет легко переусердствовал бы и отнял у проекта сам слой — тогда
    починка ложного пропуска завела бы класс ложных блоков (та же граница, что у
    `test_absent_project_policy_is_not_a_refusal`).
    """
    root = _project(tmp_path, {"dirs": {"scripts": {"class": "not_rewritable", "cause": "миграции"}}})
    pol = dispgate.perimeter_policy(root)
    assert pol["dirs"]["scripts"] == "not_rewritable"


def test_symlinked_policy_is_a_refusal(tmp_path):
    """Политика симлинком — отказ для строгого читателя (F-R2-08).

    `git checkout-index` кладёт в снимок ИМЯ ссылки, а байты живут снаружи индекса: при
    неизменном блобе вердикт менялся трижды (канон есть / канон снят / битый json). Дыра
    была названа в `staged.py` ещё в июле, но до проектного слоя она двигала только
    числа-ориентиры; после F-01 она двигает периметр.
    """
    (tmp_path / "real.json").write_text('{"perimeter": {"canon": {"modules": ["models"]}}}',
                                        encoding="utf-8")
    os.symlink(tmp_path / "real.json", tmp_path / "project_context.json")
    dispgate._PERIMETER_CACHE.clear()
    with pytest.raises(dispgate.indexer.ProjectManifestUnusable) as e:
        dispgate.perimeter_policy(str(tmp_path))
    assert "ссылка" in str(e.value)
    # Потребители ЧИСЕЛ не затронуты: у них своя принятая политика отказа.
    assert dispgate.indexer.project_manifest(str(tmp_path))["perimeter"]["canon"]["modules"] == ["models"]


def test_unresolvable_canon_name_is_found(tmp_path):
    """Опечатка в имени канона видна (F-R2-07).

    Весь гейт соседний проект висит на строке `"models"`. `"modelz"`, `[]`, `{}` формально годны по
    типам и молча снимают строгость: канон перестаёт опознаваться, и миграция, которая его
    импортирует, проходит без характеризации. Для ЧИСЕЛ политики движок опечатку уже ловил
    (`unknown`), для ИМЁН — нет, хотя имя резолвится против готового индекса.
    """
    ix = {"PROJ": {"models", "dal/queries"}, "by_stem": {"models": {"models"},
                                                         "queries": {"dal/queries"}}}
    assert dispgate.unresolved_canon_names(ix, ["models"]) == []
    assert dispgate.unresolved_canon_names(ix, ["queries"]) == [], "стем обязан резолвиться"
    assert dispgate.unresolved_canon_names(ix, ["modelz", "models"]) == ["modelz"]
    assert dispgate.unresolved_canon_names(ix, []) == [], "пустой список — не опечатка сам по себе"


@pytest.mark.parametrize("canon", [{}, {"modules": []}, {"modules": [], "callables": []}])
def test_empty_canon_section_is_a_refusal(tmp_path, canon):
    """Секция канона без единого имени — опечатка, а не решение (F-R2-07, вторая половина).

    Проверки типов такой вход пропускали: список на месте, он просто пуст. Для проекта это
    значит, что признак касания канона мёртв — движковые имена в чужом репозитории ничего не
    защищают. Различитель у машины ровно один: секцию написали намеренно, значит намеревались
    что-то назвать. Канона правда нет — секции быть не должно (см. тест ниже).
    """
    (tmp_path / "project_context.json").write_text(
        json.dumps({"perimeter": {"canon": canon}}, ensure_ascii=False), encoding="utf-8")
    dispgate._PERIMETER_CACHE.clear()
    with pytest.raises(dispgate.indexer.ProjectManifestUnusable) as e:
        dispgate.perimeter_policy(str(tmp_path))
    assert "ни одного имени" in str(e.value)


def test_policy_without_canon_section_stays_legal(tmp_path):
    """Позитивный контроль к предыдущему: политика БЕЗ секции канона законна.

    Иначе запрет пустоты превратился бы в требование канона у каждого проекта — тот же
    перекос в ложные блоки, от которого гейт однажды уже выключали.
    """
    root = _project(tmp_path, {"dirs": {"scripts": {"class": "not_rewritable", "cause": "миграции"}}})
    pol = dispgate.perimeter_policy(root)
    assert pol["dirs"]["scripts"] == "not_rewritable"
    assert "health_db" in set(pol["canon"].get("modules") or ()), "движковый канон обязан уцелеть"
