"""Дата взятия: лестница чтения и расчёт правок.

Транзакция контролем не покрыта — сказано в сайдкаре; её оракул dry-run и снапшот.
"""
import pytest

import lab_specimen as LS
from migrations import staging_date_taken_20260731 as MIG

ШАПКА = """Заявка №: {order}
Биоматериал: Кровь (сыворотка);
Взятие биоматериала: {taken} 08:00
Показатель
Результат
Тестостерон
14,20
"""
ПРОДОЛЖЕНИЕ = """Заявка №: {order}
Показатель
Результат
Кортизол
412,5
"""


def test_дата_взятия_читается_из_шапки():
    f = LS.page_facts(None, texts=[ШАПКА.format(order="111", taken="14.06.2021")])
    assert f[1]["taken"] == "2021-06-14"
    assert f[1]["taken_source"] == "read_header"


def test_дата_наследуется_внутри_своей_заявки():
    """Забор один — дата у заявки одна, и страница без своей строки «Взятие»
    получает её от своей же заявки."""
    f = LS.page_facts(None, texts=[
        ШАПКА.format(order="111", taken="14.06.2021"),
        ПРОДОЛЖЕНИЕ.format(order="111"),
    ])
    assert f[2]["taken"] == "2021-06-14"
    assert f[2]["taken_source"] == "same_order"


def test_через_чужую_заявку_дата_не_наследуется():
    """НЕГАТИВНЫЙ КОНТРОЛЬ и суть всей нити: прежнее правило отвечало «как у
    соседа» и приписывало строкам чужую заявку. Два забора в одном бланке —
    14.06 и 12.06 (синтетика), и перепутать их значит сдвинуть точку тренда на два дня."""
    f = LS.page_facts(None, texts=[
        ШАПКА.format(order="111", taken="14.06.2021"),
        ПРОДОЛЖЕНИЕ.format(order="222"),
    ])
    assert f[2]["taken"] is None
    assert f[2]["taken_source"] == "unknown"


def test_два_забора_две_даты():
    f = LS.page_facts(None, texts=[
        ШАПКА.format(order="111", taken="14.06.2021"),
        ШАПКА.format(order="222", taken="12.06.2021"),
        ПРОДОЛЖЕНИЕ.format(order="222"),
    ])
    assert [f[i]["taken"] for i in (1, 2, 3)] == ["2021-06-14", "2021-06-12", "2021-06-12"]


# --- расчёт правок --------------------------------------------------------

ФАКТЫ = {"b.pdf": {
    1: {"taken": "2021-06-14", "taken_source": "read_header"},
    2: {"taken": "2021-06-14", "taken_source": "same_order"},
    3: {"taken": None, "taken_source": "unknown"},
}}


def test_правка_только_там_где_дата_меняется():
    """Список правок отражает радиус: верная дата в него не попадает."""
    rows = [{"id": 1, "source_file": "b.pdf", "page": 1, "date": "2021-06-18"},
            {"id": 2, "source_file": "b.pdf", "page": 2, "date": "2021-06-14"}]
    upd, tally = MIG.plan_updates(rows, ФАКТЫ)
    assert upd == [("2021-06-14", "read_header", 1)]
    assert tally["2021-06-18 → 2021-06-14"] == 1
    assert tally["совпадает"] == 1


def test_страница_без_даты_в_бланке_не_трогается():
    """НЕГАТИВНЫЙ КОНТРОЛЬ: отсутствие даты — законный ответ, а не повод
    поставить дату соседа. Ровно этой подменой нить и началась."""
    rows = [{"id": 3, "source_file": "b.pdf", "page": 3, "date": "2021-06-23"}]
    upd, tally = MIG.plan_updates(rows, ФАКТЫ)
    assert upd == []
    assert tally == {"не тронуто (даты нет в бланке)": 1}


def test_документ_не_pdf_не_роняет_расчёт():
    rows = [{"id": 4, "source_file": "скан.jpeg", "page": 1, "date": "2026-01-01"}]
    assert MIG.plan_updates(rows, {})[0] == []


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
