"""Лаб-инструменты MCP объявляют границу ответа настоящим генератором на синтетическом каноне.

Ночная карточка 2026-10-18-c52e6892: проба границы контекста покраснела — `lab_tests` и
`lab_results` читали канон окном и молчали о нём, а пустой ответ велел модели «сказать, что
в Health OS этого нет». Ревью ночной правки показало, почему мок `declared_boundary` здесь
не годится: настоящий текст для пустого ответа про ОДИН анализ говорил «выше показан ВЕСЬ
канон … значит не сдавался». Поэтому здесь labs_db настоящий, база — фикстура `db`.
"""
from __future__ import annotations

import pytest

import mcp_tools

pytestmark = pytest.mark.unit

LIE = "этого нет"


def _no_component(monkeypatch):
    import labs_db
    monkeypatch.setattr(labs_db, "get_lab_trend_by_component",
                        lambda name, n: {"points": [], "issues": ["unmapped"], "unresolved": []})


def test_lab_tests_empty_window_with_old_history_is_not_absence(db, clock):
    clock.set("2026-10-18")
    db.add_lab_result("2005-03-01", "TSH", 1.2, unit="мкМЕ/мл")     # старше окна 7300 дн.
    text, err = mcp_tools.call("lab_tests", {})
    assert not err and text.startswith("нет данных")
    assert "СТАРШЕ" in text and "2005-03-01" in text and LIE not in text


def test_lab_tests_list_carries_boundary(db, clock):
    clock.set("2026-10-18")
    db.add_lab_result("2026-10-01", "TSH", 1.2, unit="мкМЕ/мл")
    text, err = mcp_tools.call("lab_tests", {})
    assert not err and "TSH" in text and "ГРАНИЦА" in text


def test_lab_results_unknown_name_never_says_whole_canon(db, clock, monkeypatch):
    """Свежие анализы есть, нужного имени нет вовсе: ни «весь канон», ни «не сдавался»."""
    _no_component(monkeypatch)
    clock.set("2026-10-18")
    db.add_lab_result("2026-10-01", "TSH", 1.2, unit="мкМЕ/мл")
    text, err = mcp_tools.call("lab_results", {"test_name": "Insulin", "days": 365})
    assert not err and text.startswith("нет данных")
    assert "ни за какой срок" in text and "ВЕСЬ канон" not in text and LIE not in text


def test_lab_results_old_rows_named_by_date(db, clock, monkeypatch):
    _no_component(monkeypatch)
    clock.set("2026-10-18")
    db.add_lab_result("2021-06-14", "Insulin", 6.3, unit="мкЕд/мл")
    text, err = mcp_tools.call("lab_results", {"test_name": "Insulin", "days": 365})
    assert not err and text.startswith("нет данных")
    assert "СТАРШЕ" in text and "2021-06-14" in text and LIE not in text


def test_lab_results_nonempty_exact_name_carries_boundary(db, clock, monkeypatch):
    _no_component(monkeypatch)
    clock.set("2026-10-18")
    db.add_lab_result("2026-10-01", "Insulin", 6.3, unit="мкЕд/мл")
    db.add_lab_result("2021-06-14", "Insulin", 7.0, unit="мкЕд/мл")
    text, err = mcp_tools.call("lab_results", {"test_name": "Insulin", "days": 365})
    assert not err and "6.3" in text
    assert "ГРАНИЦА ОКНА" in text and "2021-06-14" in text


def test_lab_results_component_path_both_branches(db, clock, monkeypatch):
    """Путь по веществу: граница считается по ВСЕМ написаниям ряда, и в пустом, и в полном ответе."""
    import labs_db
    clock.set("2026-10-18")
    db.add_lab_result("2020-01-10", "LDL calculated", 3.1, unit="ммоль/л")
    db.add_lab_result("2026-10-01", "ЛПНП", 2.9, unit="ммоль/л")
    pts = [{"date": "2020-01-10", "value": 3.1, "unit": "ммоль/л", "test_name": "LDL calculated"},
           {"date": "2026-10-01", "value": 2.9, "unit": "ммоль/л", "test_name": "ЛПНП"}]
    monkeypatch.setattr(labs_db, "get_lab_trend_by_component",
                        lambda name, n: {"points": pts, "issues": [], "unresolved": []})
    text, err = mcp_tools.call("lab_results", {"test_name": "LDL", "days": 365})
    assert not err and "2.9" in text and "ГРАНИЦА ОКНА" in text and "2020-01-10" in text
    monkeypatch.setattr(labs_db, "get_lab_trend_by_component",
                        lambda name, n: {"points": pts[:1], "issues": [], "unresolved": []})
    text, err = mcp_tools.call("lab_results", {"test_name": "LDL", "days": 365})
    assert not err and text.startswith("нет данных") and "2020-01-10" in text and LIE not in text
