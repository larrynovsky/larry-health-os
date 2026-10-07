"""Инструменты чтения Health OS для Claude/ChatGPT (нить mcp-gateway, шаг 1, 06.10).

Тревога владельца — выдумка поверх данных. Поэтому каждый тест держит одно обещание: пустота —
явное «нет данных», у ответа есть источник, путь бланка и имя наружу не уходят, тенант не
выбирается аргументом, а аргумент модели не роняет сервер.
"""
from __future__ import annotations

import json
import re
import sys
import types

import pytest

import health_mcp
import mcp_tools

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def fake_readers(monkeypatch):
    """Читатели *_db подменены синтетикой: тест судит слой инструментов, а не базу."""
    state = {"series": [], "history": [], "day": {}, "problems": [], "facts": []}
    state["trend"] = {"points": [], "issues": ["unmapped"], "unresolved": []}
    labs = types.SimpleNamespace(get_lab_series=lambda name, n_days: state["series"],
                                 get_lab_history=lambda days: state["history"],
                                 get_lab_trend_by_component=lambda name, n: state["trend"])
    metrics = types.SimpleNamespace(get_day=lambda d: state["day"])
    probs = types.SimpleNamespace(get_problem_list=lambda st=None: state["problems"])
    mem = types.SimpleNamespace(get_facts=lambda mem_class=None: state["facts"])
    for name, mod in (("labs_db", labs), ("metrics_db", metrics), ("problems_db", probs),
                      ("memory_facts_db", mem)):
        monkeypatch.setitem(sys.modules, name, mod)
    monkeypatch.setattr(mcp_tools, "_redactor_cache", [re.compile("Testovich|Example Person", re.I)])
    return state


@pytest.mark.parametrize("name, args", [
    ("lab_tests", {}), ("lab_results", {"test_name": "LDL"}), ("day_metrics", {"date": "2026-01-02"}),
    ("problems", {}), ("facts", {}),
])
def test_empty_is_explicit_no_data(name, args):
    text, err = mcp_tools.call(name, args)
    assert not err and text.startswith("нет данных")


def test_lab_results_carry_source_and_date_but_not_document_path(fake_readers):
    fake_readers["series"] = [{"test_name": "LDL", "value": 3.1, "unit": "mmol/L", "date": "2025-07-01",
                               "ref_low": None, "ref_high": 3.0,
                               "source": "doc:CR/Example Person 12345.pdf"}]
    text, err = mcp_tools.call("lab_results", {"test_name": "LDL"})
    assert not err and text.startswith("Источник: Health OS")
    assert "2025-07-01" in text and "3.1" in text
    assert "CR/" not in text and ".pdf" not in text and "12345" not in text


def test_identity_is_redacted_everywhere(fake_readers):
    fake_readers["problems"] = [{"title": "Сон", "status": "active", "plain_summary": "Testovich спит мало"}]
    text, _ = mcp_tools.call("problems", {})
    assert "Testovich" not in text and "[скрыто]" in text


def test_transient_facts_do_not_leak(fake_readers):
    fake_readers["facts"] = [
        {"key": "group", "value": "A+", "valid_from": "2020-01-01", "temporal_class": "durable"},
        {"key": "night", "value": "плохо спал 03.10", "valid_from": "2026-10-03", "temporal_class": "transient"}]
    text, _ = mcp_tools.call("facts", {})
    assert "A+" in text and "плохо спал" not in text


@pytest.mark.parametrize("name, args", [
    ("lab_results", {}), ("lab_results", {"test_name": "x" * 101}), ("lab_results", {"test_name": "LDL", "days": "all"}),
    ("day_metrics", {"date": "вчера"}), ("day_metrics", {"date": "2026-02-30"}), ("problems", {"status": "active"}),
])
def test_bad_args_are_answers_not_crashes(name, args):
    text, err = mcp_tools.call(name, args)
    assert err and text.startswith("неверный аргумент")


def test_reader_failure_gives_no_data_and_no_trace(monkeypatch):
    def boom(d):
        raise RuntimeError("секрет из трассы")
    monkeypatch.setitem(sys.modules, "metrics_db", types.SimpleNamespace(get_day=boom))
    text, err = mcp_tools.call("day_metrics", {"date": "2026-01-02"})
    assert err and "секрет" not in text


def test_no_tool_chooses_tenant_or_path():
    forbidden = {"tenant", "db", "path", "data_dir", "file", "user"}
    for t in mcp_tools.TOOLS:
        assert not forbidden & set(t["inputSchema"].get("properties", {})), t["name"]


def test_server_lists_and_calls_tools():
    listed = health_mcp.rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]
    assert {"ping", *mcp_tools.NAMES} == {t["name"] for t in listed}
    res = health_mcp.rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                          "params": {"name": "lab_tests", "arguments": {}}})["result"]
    assert res["content"][0]["text"].startswith("нет данных") and res.get("isError") is False


def test_lab_results_prefer_substance_and_say_when_falling_back(fake_readers):
    """Решение владельца 06.10: ряд по веществу (LOINC); по названию — только с пометкой."""
    fake_readers["trend"] = {"points": [
        {"date": "2099-01-01", "value": 3.1, "unit": "mmol/L", "test_name": "LDL", "ref_low": None, "ref_high": 3.0},
        {"date": "2099-02-01", "value": 120, "unit": "mg/dL", "test_name": "ЛПНП", "ref_low": None, "ref_high": 116,
         "converted": True, "raw_unit": "mg/dL"}], "issues": [], "unresolved": []}
    text, _ = mcp_tools.call("lab_results", {"test_name": "LDL"})
    assert "LOINC" in text and "ЛПНП" in text and "LDL" in text
    fake_readers["trend"] = {"points": [], "issues": ["unmapped"], "unresolved": []}
    fake_readers["series"] = [{"test_name": "LDL", "value": 3.1, "unit": "mmol/L", "date": "2099-01-01"}]
    text, _ = mcp_tools.call("lab_results", {"test_name": "LDL"})
    assert "БЕЗ сведения" in text and "неполным" in text


def test_tools_layer_writes_nothing():
    """Шаг 1 — только чтение: запись в память идёт отдельным кодом и только через «да» владельца.
    Сторож по тексту: вызов писателя (save_*/add_*/set_*) или SQL записи в слое инструментов — красный.
    Граница: ловит прямой вызов в этих двух файлах, а не запись внутри вызванного читателя *_db."""
    import pathlib
    for f in ("mcp_tools.py", "health_mcp.py"):
        src = pathlib.Path(__file__).resolve().parents[2].joinpath(f).read_text(encoding="utf-8")
        hits = re.findall(r"\b(?:save|add|set|upsert|delete)_\w+\(|\b(?:INSERT|UPDATE|DELETE)\s", src)
        assert not hits, (f, hits)
# ── 07.10: одно окно с ботом ──
def test_health_brief_is_the_bots_brief(monkeypatch):
    """Облако видит тот же бриф, что бот, — не второй читатель со своей сборкой."""
    monkeypatch.setitem(sys.modules, "patient_context",
                        types.SimpleNamespace(build_patient_brief=lambda: "Пациент, N лет.\nЛечение: X"))
    text, err = mcp_tools.call("health_brief", {})
    assert not err and text.startswith("Источник: Health OS") and "Пациент, N лет.\nЛечение: X" in text
    monkeypatch.setitem(sys.modules, "patient_context", types.SimpleNamespace(build_patient_brief=lambda: " "))
    assert mcp_tools.call("health_brief", {})[0].startswith("нет данных")


def test_nutrition_frame_marks_unapproved_rules(monkeypatch):
    monkeypatch.setitem(sys.modules, "food_profile", types.SimpleNamespace(medical_frame=lambda: {
        "energy": "gain", "constraints": {"lactose_free", "low_GI"}, "reasons": ["медкарта: условие X"]}))
    monkeypatch.setitem(sys.modules, "generated_food_rules", types.SimpleNamespace(get_rules=lambda status=None: [
        {"payload": {"condition": {"label": "Состояние Y"}, "frame": {"energy": "gain"},
                     "evidence": {"source": "WCRF", "weight": "strong", "why": "минимум переработанного мяса"}},
         "created_at": "2026-10-01"}] if status == "shadow" else []))
    text, err = mcp_tools.call("nutrition_frame", {})
    assert not err
    body = json.loads(text.split("\n", 1)[1])
    assert body["frame"]["constraints"] == ["lactose_free", "low_GI"] and body["frame"]["energy"] == "gain"
    (rule,) = body["not_approved_rules"]
    assert rule["status"] == mcp_tools.NOT_APPROVED and "НЕ одобрено" in rule["status"]
    assert rule["why"] == "минимум переработанного мяса"


def test_problems_default_hides_resolved_like_the_bot(fake_readers):
    fake_readers["problems"] = [{"title": "Проблема A (снята)", "status": "resolved"},
                                {"title": "Проблема B", "status": "active"}]
    text, _ = mcp_tools.call("problems", {})
    assert "Проблема B" in text and "Проблема A" not in text and "status=resolved" in text
    # статусы живой базы (07.10): наблюдение в любом виде — действующая проблема
    fake_readers["problems"] = [{"title": "Наблюдение C", "status": "watchful_waiting"},
                                {"title": "Проблема A", "status": "resolved"}]
    assert "Наблюдение C" in mcp_tools.call("problems", {})[0]
    assert "Проблема A" in mcp_tools.call("problems", {"status": "resolved"})[0]
    assert "Наблюдение C" not in mcp_tools.call("problems", {"status": "resolved"})[0]
