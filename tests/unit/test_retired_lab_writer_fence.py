"""Фенс ретайрнутого писателя канона: `coordinate_import` → `lab_results`.

Решение владельца 2026-07-29: маршрут один, старый демонтируется. Старый путь
писал в `lab_results` НАПРЯМУЮ (`DELETE`+`INSERT` по source), минуя
`lab_results_staging` и человеческое ревью — открытый F-01. Пока он был жив,
объявленная safety boundary держалась не на всех путях, то есть была декоративной.

Проверяется свойство: писатель молчит, а документ НЕ теряется — он уезжает на
новый маршрут. Именно связка обязательна: фенс без делегирования означал бы тихую
потерю анализов, что хуже обхода staging.
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def test_retired_writer_does_not_touch_the_canon(monkeypatch, tmp_path):
    """Ключевое: ретайрнутая функция не открывает соединение с БД вообще.
    Проверяем не «не записала строк», а «не подошла к канону» — первое зеленело бы
    и на пустом результате разбора, то есть по неверной причине."""
    import import_coordinator as ic

    opened = []
    monkeypatch.setattr(ic.db, "get_conn",
                        lambda *a, **k: opened.append(1) or (_ for _ in ()).throw(
                            AssertionError("ретайрнутый писатель открыл канон")))

    class _R:
        known = [object()]
        unknown = []
    ic._write_known_to_db(_R(), Path("/tmp/бланк.pdf"), date_str="2026-07-17")
    assert opened == []


def test_retired_body_is_not_reachable_from_the_live_name():
    """Тело оставлено читаемым один цикл под другим именем. Живое имя обязано
    вести к фенсу, а не к телу — иначе «ретайр» это переименование, не фенс."""
    import inspect

    import import_coordinator as ic
    src = inspect.getsource(ic._write_known_to_db)
    assert "INSERT INTO lab_results" not in src
    assert "РЕТАЙРНУТ" in src


def test_lab_document_is_delegated_not_dropped(monkeypatch, tmp_path):
    """Вторая половина связки: основной цикл импорта отдаёт лабораторный документ
    вотчеру. Без этой проверки фенс выглядел бы успешным ровно в тот момент,
    когда он молча теряет анализы.

    Читаем ИСХОДНИК ветки, а не исполняем main(): исполнение потянуло бы весь
    обход iCloud и секреты тенанта. Честная граница: проверяется, что старый
    вызов ушёл и появился новый, а не что делегирование сработало в проде —
    последнее закрыто tests/unit/test_confirmed_type_reaches_watcher.py.
    """
    import inspect

    import import_all

    import ast
    import textwrap

    src = textwrap.dedent(inspect.getsource(import_all.main))
    tree = ast.parse(src)
    called = {n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "")
              for n in ast.walk(tree) if isinstance(n, ast.Call)}
    # Судим ВЫЗОВ, а не написание: слово coordinate_import осталось в комментарии,
    # который объясняет, почему ветка убрана, и подстрочная проверка ловила бы его.
    # Тот же урок, что в сторожах проекта: ассерть связку, не наличие строки.
    assert "force_lab" in called, "лабораторная ветка не делегирует вотчеру"
    assert "coordinate_import" not in called, "старый маршрут всё ещё зовётся из main()"


def test_fence_date_is_declared_for_the_sensor():
    """Дата фенса отделяет легаси от рецидива; без неё граница отсутствует."""
    import integrity_tests as it
    from datetime import date
    assert date.fromisoformat(it.RETIRED_LAB_WRITER_FENCE)
