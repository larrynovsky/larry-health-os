"""K8: Russian snapshots and tenant language, using isolated source functions.

No application imports, live profiles, databases, secrets or transports. Pending
keys are overlaid only here; after review the same checks use the merged catalogs.
"""
import __future__
import ast
import asyncio
import hashlib
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from string import Formatter
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
import yaml

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]
_K8_KEYS = (
    "consult_prep.report.visit_heading", "consult_prep.report.hypothesis_heading",
    "food_rule_review.summary.heading", "food_rule_review.summary.note",
    "food_rule_review.summary.unchanged", "food_rule_review.summary.proposed",
    "food_rule_review.summary.floor_failed", "food_rule_review.summary.changed",
    "food_rule_review.summary.removed", "food_rule_review.summary.rejected",
    "food_rule_review.summary.jargon", "constitutions.notice.alert_review",
    "hypothesis_consilium.error.timeout", "hypothesis_consilium.error.no_revision",
    "hypothesis_resolution.error.consilium", "hypothesis_resolution.error.detail",
    "monthly_consilium.notice.new_hypothesis", "monthly_consilium.notice.supporters",
    "monthly_consilium.notice.cause", "monthly_consilium.notice.message",
)


def _k8_catalogs():
    tables = {lang: yaml.safe_load((ROOT / f"methodology/i18n/{lang}.yaml").read_text())
              for lang in ("ru", "en")}
    pending = ROOT / "plans/K8_NEW_KEYS_2026-09-29.yaml"
    if pending.exists():
        overlay = yaml.safe_load(pending.read_text())
        assert set(overlay) == {"ru", "en"}
        for lang in tables:
            assert set(overlay[lang]) == set(_K8_KEYS)
            for key, value in overlay[lang].items():
                assert key not in tables[lang] or tables[lang][key] == value, key
            tables[lang].update(overlay[lang])
    return tables


def _k8_function_namespace(rel, names, **bindings):
    tree = ast.parse((ROOT / rel).read_text())
    body = [node for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
    assert {node.name for node in body} == set(names)
    exec(compile(ast.Module(body=body, type_ignores=[]), rel, "exec",
                 flags=__future__.annotations.compiler_flag), bindings)
    return bindings


def _k8_translator(monkeypatch, profile):
    translator = ModuleType("i18n")
    translator.__file__ = str(ROOT / "i18n.py")
    exec(compile((ROOT / "i18n.py").read_text(), "i18n.py", "exec"), translator.__dict__)
    translator._table = _k8_catalogs().__getitem__
    # Keep the real lang_of: only its tenant profile reader is replaced.
    monkeypatch.setitem(sys.modules, "profile_db",
                        SimpleNamespace(get_patient_profile=lambda: dict(profile)))
    monkeypatch.setitem(sys.modules, "i18n", translator)
    return translator


def test_k8_dictionary_snapshot_placeholders_and_person_vocabulary():
    tables = _k8_catalogs()
    ru = {key: tables["ru"][key] for key in _K8_KEYS}
    snapshot = json.dumps(ru, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    # Original literals preserved except the forbidden constitution term in the notice.
    # 29.09, приёмка: «Ревью алертов … Домены» → «Разбор предупреждений … Разделы» (внутренняя лексика).
    assert hashlib.sha256(snapshot.encode()).hexdigest() == "4ea25ac729777d4f257054cad82ba98d1cb57e84796df3249372f05212c9ddc3"
    for key in _K8_KEYS:
        fields = [sorted((field, spec, conv) for _, field, spec, conv
                         in Formatter().parse(tables[lang][key]) if field is not None)
                  for lang in ("ru", "en")]
        assert fields[0] == fields[1], key
        assert not re.search("[А-Яа-яЁё]", tables["en"][key]), key
    # Reuse the actual vocabulary oracle, including its future changes.
    name = "test_person_copy_has_no_internal_vocabulary_or_formal_address"
    ns = _k8_function_namespace("tests/unit/test_i18n.py", {name},
                                re=re, _yaml=tables.__getitem__)
    ns[name]()


def test_k8_food_summary_switches_language_and_keeps_all_sections(monkeypatch):
    profile = {}
    translator = _k8_translator(monkeypatch, profile)
    ns = _k8_function_namespace("food_rule_review.py", {"render_summary"},
                                i18n=translator, check_jargon=Mock(return_value=[]))
    empty = {"proposed": [], "changed": [], "removed": [], "rejected": [],
             "n_active": 2, "n_shadow": 3}
    populated = {**empty,
                 "proposed": [{"label": "fixture allowed", "floor_ok": True},
                              {"label": "fixture blocked", "floor_ok": False}],
                 "changed": [{"label": "fixture changed"}],
                 "removed": [{"label": "fixture removed"}],
                 "rejected": [{"label": "fixture rejected", "reasons": "fixture reason"}]}
    before = json.dumps(populated, sort_keys=True)
    for language in (None, "en", "ru"):
        profile.clear()
        if language:
            profile["identity.language"] = language
        lang = language or "ru"
        tr = lambda suffix, **kw: translator.t("food_rule_review.summary." + suffix, lang, **kw)
        assert ns["render_summary"](empty) == "\n".join([
            tr("heading"), tr("note"), "", tr("unchanged", active=2, proposed=3)])
        for terms in ([], ["fixture term"] * 6):
            ns["check_jargon"].return_value = terms
            rendered = ns["render_summary"](populated)
            expected = [tr("heading"), tr("note"), "", tr("proposed"), "- fixture allowed",
                        "- fixture blocked" + tr("floor_failed"), "", tr("changed"),
                        "- fixture changed", "", tr("removed"), "- fixture removed",
                        "", tr("rejected"), "- fixture rejected · fixture reason"]
            if terms:
                expected += ["", tr("jargon", terms=terms[:5])]
            assert rendered == "\n".join(expected)
            if lang == "en":
                assert not re.search("[А-Яа-яЁё]", rendered)
    assert json.dumps(populated, sort_keys=True) == before


def test_k8_monthly_notice_uses_tenant_language_in_body_and_button(monkeypatch):
    profile = {}
    translator = _k8_translator(monkeypatch, profile)
    delivery = Mock()
    monkeypatch.setitem(sys.modules, "hai_hypotheses", SimpleNamespace(_notify_specialist=delivery))
    actions = SimpleNamespace(button=lambda *args: args, keyboard=lambda buttons: buttons)
    monkeypatch.setitem(sys.modules, "bot", SimpleNamespace(actions=actions))
    ns = _k8_function_namespace("monthly_consilium.py", {"_notify_consilium_hypothesis"})
    causes = [{"cause": f"fixture cause {i}", "how_to_check": f"fixture check {i}",
               "supported_by": ["fixture supporter"] if i % 2 else []} for i in range(5)]
    hypothesis = {"patient_view": {"possible_causes": causes, "noticed": "fixture observation",
                                   "do_now": "fixture action", "consult_when": "fixture timing"},
                  "consensus": {"count": 7, "specialists_supporting": [f"fixture {i}" for i in range(7)]}}
    before = json.dumps(hypothesis, sort_keys=True)
    for language in (None, "en", "ru"):
        profile.clear()
        if language:
            profile["identity.language"] = language
        lang = language or "ru"
        tr = lambda suffix, **kw: translator.t("monthly_consilium.notice." + suffix, lang, **kw)
        for hyp in ({}, hypothesis, {**hypothesis, "theme": "fixture theme"}):
            ns["_notify_consilium_hypothesis"](7, hyp)
            pv, consensus = hyp.get("patient_view", {}), hyp.get("consensus", {})
            expected_causes = "".join(tr("cause", cause=c["cause"], check=c["how_to_check"],
                supporters=tr("supporters", supporters=", ".join(c["supported_by"]))
                if c["supported_by"] else "") for c in pv.get("possible_causes", [])[:4])
            expected = tr("message", memory_id=7, theme=hyp.get("theme") or tr("new_hypothesis"),
                          count=consensus.get("count", 0),
                          supporters=", ".join(consensus.get("specialists_supporting", [])[:5]),
                          noticed=pv.get("noticed", "—"), causes=expected_causes,
                          do_now=pv.get("do_now", "—"), consult_when=pv.get("consult_when", "—"))
            delivery.assert_called_with(expected, reply_markup=[
                (translator.t("actions.hypothesis.query", lang), "hq", 7)])
            if lang == "en":
                assert not re.search("[А-Яа-яЁё]", expected)
    assert json.dumps(hypothesis, sort_keys=True) == before


def test_k8_consult_saved_headings_and_missing_hypothesis(monkeypatch, tmp_path):
    profile = {}
    translator = _k8_translator(monkeypatch, profile)
    db = SimpleNamespace(init_db=Mock(), get_last_consultation=Mock(return_value=None),
                         get_memory=Mock(), get_profile_context=Mock(return_value={}))
    monkeypatch.setitem(sys.modules, "patient_context", SimpleNamespace(reasoning_block=lambda: ""))
    monkeypatch.setitem(sys.modules, "treatment_summary", SimpleNamespace(treatment_text=lambda **kw: ""))
    client = SimpleNamespace(messages=SimpleNamespace(create=Mock(return_value=
        SimpleNamespace(content=[SimpleNamespace(text="fixture report")]))))
    ns = _k8_function_namespace("consult_prep.py",
        {"prepare_visit_report", "prepare_hypothesis_query", "_fmt_date"},
        i18n=translator, db=db, json=json, date=date, timedelta=timedelta,
        get_today=lambda: date(2099, 1, 1), REPORTS_DIR=tmp_path,
        _pc_frame=lambda medical: [], _pc_unset=lambda value: not value,
        _build_vitals_block=Mock(return_value="fixture vitals"),
        _build_labs_block=Mock(return_value="fixture labs"),
        _build_labs_for_hyp=Mock(return_value="fixture labs"),
        _build_medications_block=Mock(return_value="fixture context"),
        _build_prev_consilium_block=Mock(return_value="fixture context"),
        _build_specialist_questions_block=Mock(return_value="fixture questions"),
        build_genetic_context_block=Mock(return_value="fixture context"),
        _get_client=lambda: client, log=Mock(),
        hai_core=SimpleNamespace(get_model=lambda role: "fixture model", answer_language=lambda: ""))
    for language in (None, "en", "ru"):
        profile.clear()
        if language:
            profile["identity.language"] = language
        lang = language or "ru"
        db.get_memory.return_value = []
        assert ns["prepare_hypothesis_query"](7) == translator.t(
            "hypotheses.error.not_found", lang, hypothesis_id=7)
        assert ns["prepare_visit_report"]("2099-01-02", "fixture-specialist") == "fixture report"
        assert (tmp_path / "consult_fixture-specialist_2099-01-02.md").read_text() == translator.t(
            "consult_prep.report.visit_heading", lang, specialist_type="fixture-specialist",
            date="02.01.2099") + "fixture report\n"
        db.get_memory.return_value = [{"id": 7, "value": json.dumps({"observation": "x" * 100})}]
        assert ns["prepare_hypothesis_query"](7) == "fixture report"
        assert (tmp_path / "consult_query_hyp7_2099-01-01.md").read_text() == translator.t(
            "consult_prep.report.hypothesis_heading", lang, hypothesis_id=7,
            observation="x" * 80) + "fixture report\n"


def test_k8_consilium_errors_switch_language(monkeypatch):
    profile = {}
    translator = _k8_translator(monkeypatch, profile)
    monkeypatch.setitem(sys.modules, "anthropic", ModuleType("anthropic"))
    monkeypatch.setitem(sys.modules, "wellally_consult",
                        SimpleNamespace(_read_specialist_prompt=lambda name: "fixture prompt"))
    db = SimpleNamespace(get_memory=Mock(), save_hypothesis_outcome=Mock())
    client = SimpleNamespace(messages=SimpleNamespace(create=AsyncMock(side_effect=asyncio.TimeoutError)))
    ns = _k8_function_namespace("hypothesis_consilium_eval.py",
        {"evaluate_hypothesis_via_consilium", "_call_eval_coordinator_async"},
        i18n=translator, db=db, _json=json, asyncio=asyncio, log=Mock(),
        hai_core=SimpleNamespace(get_model=lambda role: "fixture model", answer_language=lambda: ""),
        llm_client=SimpleNamespace(guarded_client=lambda **kw: client),
        _COORDINATOR_EVAL_PROMPT="fixture prompt", _build_eval_data_package=Mock(return_value="fixture data"),
        _run_eval_round=AsyncMock(return_value={}), _arbiter_extract_verdict=Mock())
    coordinator = ns["_call_eval_coordinator_async"]
    for lang in ("ru", "en", "ru"):
        profile["identity.language"] = lang
        with pytest.raises(RuntimeError) as timeout:
            asyncio.run(coordinator(client, {}, "fixture data"))
        assert str(timeout.value) == translator.t("hypothesis_consilium.error.timeout", lang)
        db.get_memory.return_value = []
        with pytest.raises(ValueError) as missing:
            asyncio.run(ns["evaluate_hypothesis_via_consilium"](7))
        assert str(missing.value) == translator.t(
            "hypotheses.error.not_found", lang, hypothesis_id=7).removesuffix(".")
        db.get_memory.return_value = [{"id": 7, "value": "{}"}]
        ns["_call_eval_coordinator_async"] = AsyncMock(return_value="fixture synthesis")
        for verdict in ("confirmed", "partial", "rejected"):
            ns["_arbiter_extract_verdict"].return_value = {"verdict": verdict}
            result = asyncio.run(ns["evaluate_hypothesis_via_consilium"](7))
            if verdict == "rejected":
                assert result["verdict"] == verdict
            else:
                expected = translator.t("hypothesis_consilium.error.no_revision", lang)
                assert result["verdict"] == "error" and result["reasoning"] == expected
                assert db.save_hypothesis_outcome.call_args.kwargs["reasoning"] == expected


def test_k8_resolution_errors_keep_stored_and_returned_contracts(monkeypatch):
    profile = {}
    translator = _k8_translator(monkeypatch, profile)
    evaluate = AsyncMock()
    monkeypatch.setitem(sys.modules, "hypothesis_consilium_eval",
                        SimpleNamespace(evaluate_hypothesis_via_consilium=evaluate))
    stored = Mock()
    ns = _k8_function_namespace("hypothesis_resolution.py", {"run_consilium_and_resolve"},
        i18n=translator, log=Mock(), set_eval_error=stored, resolve_hypothesis=Mock())
    for lang in ("ru", "en", "ru"):
        profile["identity.language"] = lang
        evaluate.side_effect = None
        for reason in (None, "fixture reason"):
            evaluate.return_value = {"verdict": "error", "reasoning": reason}
            expected = reason or translator.t("hypothesis_resolution.error.consilium", lang)
            assert ns["run_consilium_and_resolve"](7)["message"] == expected
            stored.assert_called_with(7, expected)
        evaluate.side_effect = RuntimeError("fixture failure")
        assert ns["run_consilium_and_resolve"](7)["message"] == "fixture failure"
        stored.assert_called_with(7, translator.t(
            "hypothesis_resolution.error.detail", lang, error="fixture failure"))
        ns["resolve_hypothesis"].assert_not_called()


def test_k8_constitution_notice_uses_tenant_profile(monkeypatch):
    profile = {}
    translator = _k8_translator(monkeypatch, profile)
    db = SimpleNamespace(get_constitution=Mock(return_value={"body_md": "## What changed\nfixture changes"}),
                         get_active_protocols=Mock(return_value=[]), get_conn=MagicMock())
    monkeypatch.setitem(sys.modules, "health_db", db)
    client = SimpleNamespace(messages=SimpleNamespace(create=Mock(return_value=
        SimpleNamespace(content=[SimpleNamespace(text="fixture review")]))))
    delivery = Mock(return_value=True)
    ns = _k8_function_namespace("generate_constitutions.py", {"_run_alert_review"},
        i18n=translator, DOMAINS={"fixture": {"title": "fixture domain"}},
        _CHANGED_HEADS=("## What changed",), _build_alert_config_excerpt=lambda: "fixture config",
        _load_epistemic=lambda: "", llm_client=SimpleNamespace(guarded_client=lambda: client),
        hai_core=SimpleNamespace(get_model=lambda role: "fixture model"),
        get_today=lambda: date(2099, 1, 1), get_now=lambda: datetime(2099, 1, 1),
        _tg_notify=delivery)
    for lang in ("ru", "en", "ru"):
        profile["identity.language"] = lang
        ns["_run_alert_review"](["fixture"])
        delivery.assert_called_with(translator.t(
            "constitutions.notice.alert_review", lang, domains="fixture", review="fixture review"))
