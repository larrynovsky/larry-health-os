"""Конституция «Питание» видит рамку питания (нить constitution-frame, 07.10).

Замер 07.10: генератор писал о питании, не видя рамки (частота и объём приёмов, исключённое) —
отсюда «3–4 приёма» против рамки «dumping_aware». Оракулы: рамка доходит до промпта «Питания» и
только его; у сборки промпта нет входа для прошлой версии; смена одной рамки пересобирает
один домен, а не пять.
"""
import inspect
import sys
import types

import pytest

import generate_constitutions as gc

pytestmark = pytest.mark.unit

FRAME = "  коды ограничений: dumping_aware, reflux_aware"


@pytest.fixture
def inputs(monkeypatch):
    monkeypatch.setattr(gc, "_get_snp_data", lambda cfg: {
        "bad": [{"rsid": "rs1", "gene": "G", "significance": "bad", "clinical_summary": "x"}],
        "good": [], "unknown": []})
    monkeypatch.setattr(gc, "_get_metric_summary", lambda keys: "ряд")
    monkeypatch.setattr(gc, "_get_medical_history", lambda: "  анамнез: без деталей")
    monkeypatch.setattr(gc, "_read_previous", lambda d: "")
    monkeypatch.setattr(gc, "horizon_days", lambda: 365)
    monkeypatch.setattr(gc, "_frame_text", lambda: FRAME)


def test_nutrition_prompt_carries_frame_and_only_it(inputs, monkeypatch):
    built = {}
    real = gc._build_prompt
    def spy(domain_key, *a, **k):
        built[domain_key] = real(domain_key, *a, **k)   # настоящая сборка; печать dry-run обрезана
        return built[domain_key]
    monkeypatch.setattr(gc, "_build_prompt", spy)
    gc._generate_one("nutrition", dry_run=True)
    gc._generate_one("sleep", dry_run=True)
    assert "dumping_aware" in built["nutrition"], "рамка не дошла до промпта «Питания»"
    assert "dumping_aware" not in built["sleep"], "рамка питания утекла в чужой домен"


def test_build_prompt_has_no_input_for_previous_version():
    params = set(inspect.signature(gc._build_prompt).parameters)
    assert params == {"domain_key", "snp", "metrics_txt", "medical_txt", "horizon", "frame_txt"}, params
    assert "_read_previous" not in inspect.getsource(gc._build_prompt)


def _run_if_changed(monkeypatch, new_snap):
    import config_db
    built, writes = [], {}
    monkeypatch.setitem(sys.modules, "profile_reconciler",
                        types.SimpleNamespace(reconcile=lambda: []))
    monkeypatch.setattr(gc, "_current_fingerprint", lambda: ("fp2", new_snap))
    monkeypatch.setattr(config_db, "get_config",
                        lambda k, d=None, conn=None: {"fp": "fp1", "snap": {"links": ["a×b"]}}
                        if k == gc.FP_KEY else d)
    monkeypatch.setattr(config_db, "upsert_config", lambda k, **kw: writes.__setitem__(k, kw))
    monkeypatch.setattr(gc, "_generate_one", lambda d, dry_run=False: built.append(d) or "текст")
    monkeypatch.setattr(gc, "_save", lambda d, t: None)
    monkeypatch.setattr(sys, "argv", ["generate_constitutions.py", "--if-changed"])
    gc.main()
    return built, writes


def test_frame_only_change_rebuilds_nutrition_alone(monkeypatch):
    built, writes = _run_if_changed(monkeypatch, {"links": ["a×b"], "nutrition_frame": {"energy": "gain"}})
    assert built == ["nutrition"], f"смена рамки пересобрала {built}"
    assert gc.FP_KEY in writes, "отпечаток не записан — триггер пересобирал бы «Питание» каждую неделю"


def test_frame_and_shared_change_rebuild_all(monkeypatch):
    built, _ = _run_if_changed(monkeypatch, {"links": ["a×b", "c×d"], "nutrition_frame": {"energy": "gain"}})
    assert built == list(gc.DOMAINS), "общая вводная сменилась, а пересобрано не всё"
