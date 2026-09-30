"""
test_food_rule_review.py — месячная сводка рулбука (data-in-code-9 инкремент 4).

Backstop-видимость перед флипом: diff shadow↔active, сводка без жаргона, floor-fail помечен.
Питание — не медицина (решение владельца): врача не трогаем, эскалаций нет.
"""
from __future__ import annotations

import pytest

import food_rule_review as frr

pytestmark = pytest.mark.unit


def _rule(key, payload, *, floor_ok=True, version=1, status="shadow"):
    return {"rule_key": key, "version": version, "status": status, "payload": payload,
            "floor_ok": floor_ok, "critique": "", "generated_by": "test", "model": "",
            "model_version_date": "", "data_sources": []}


def test_build_diff_proposes_shadow(monkeypatch):
    shadow = [_rule("a", {"label": "правило А"}), _rule("b", {"label": "правило Б"}, floor_ok=False)]
    monkeypatch.setattr(frr._store, "get_rules",
                        lambda status=None, conn=None, **k: shadow if status == "shadow" else [])
    d = frr.build_diff()
    assert {x["rule_key"] for x in d["proposed"]} == {"a", "b"}
    assert d["n_active"] == 0
    assert any(x["rule_key"] == "b" and not x["floor_ok"] for x in d["proposed"])


def test_build_diff_detects_change(monkeypatch):
    active = [_rule("a", {"label": "старое"}, status="active")]
    shadow = [_rule("a", {"label": "новое"}, version=2)]
    monkeypatch.setattr(frr._store, "get_rules",
                        lambda status=None, conn=None, **k: active if status == "active" else shadow)
    d = frr.build_diff()
    assert [x["rule_key"] for x in d["changed"]] == ["a"]
    assert not d["proposed"]


def test_rule_label_uses_condition_label_not_lever():
    """Реальная схема генератора: condition — dict с label, lever — enum. На старом коде
    метка = «modify_enabling_condition» (первое строковое поле) — красный."""
    r = _rule("mthfr", {"id": "mthfr", "condition": {"label": "Гетерозиготы MTHFR (метилирование)",
                                                       "detect": {"regex": "MTHFR"}},
                        "lever": "modify_enabling_condition", "frame": {"energy": "gain"}})
    assert frr._rule_label(r) == "Гетерозиготы MTHFR (метилирование)"


def test_rule_label_fallback_has_no_underscores():
    # без label — ключ, но пригодный для Telegram Markdown (без `_` → курсив)
    r = _rule("x_post_surgery", {"lever": "management", "frame": {}})
    lbl = frr._rule_label(r)
    assert "_" not in lbl and "management" not in lbl and "surgery" in lbl


def test_build_diff_lists_rejected_of_same_run(monkeypatch):
    shadow = [_rule("a", {"label": "правило А"})]
    rej = [{**_rule("z", {"condition": {"label": "ферритин"}}, status="rejected"),
            "critique": "dead:не срабатывает на пациенте", "created_at": "2026-09-01 07:00:00"}]
    shadow[0]["created_at"] = "2026-09-01 07:00:00"

    def _get(status=None, conn=None, **k):
        return {"shadow": shadow, "rejected": rej}.get(status, [])
    monkeypatch.setattr(frr._store, "get_rules", _get)
    d = frr.build_diff()
    assert [x["rule_key"] for x in d["rejected"]] == ["z"]
    txt = frr.render_summary(d)
    assert "Отклонено критиком" in txt and "dead:" in txt
    assert "Будет снято при флипе" not in txt          # removed пуст


def test_render_summary_removed_heading_is_honest():
    d = {"proposed": [], "changed": [], "rejected": [],
         "removed": [{"rule_key": "a", "version": 1, "label": "старое", "floor_ok": True}],
         "n_active": 1, "n_shadow": 0}
    txt = frr.render_summary(d)
    assert "Будет снято при флипе" in txt and "Больше не предлагается" not in txt


def test_render_summary_empty():
    txt = frr.render_summary({"proposed": [], "changed": [], "removed": [],
                              "n_active": 0, "n_shadow": 0})
    assert "Изменений нет" in txt


def test_render_summary_marks_floor_fail():
    d = {"proposed": [{"rule_key": "a", "version": 1, "label": "постное при операции X", "floor_ok": False}],
         "changed": [], "removed": [], "n_active": 0, "n_shadow": 1}
    txt = frr.render_summary(d)
    assert "не прошло пол" in txt          # floor-fail виден, но это не медицина/эскалация


def test_render_summary_no_jargon_for_clean_rules():
    d = {"proposed": [{"rule_key": "a", "version": 1, "label": "больше рыбы и овощей", "floor_ok": True}],
         "changed": [], "removed": [], "n_active": 0, "n_shadow": 1}
    txt = frr.render_summary(d)
    assert "больше рыбы" in txt
    assert "жаргон" not in txt.lower()


def test_render_summary_flags_jargon_in_rule_label():
    d = {"proposed": [{"rule_key": "a", "version": 1, "label": "illness script для нутриента", "floor_ok": True}],
         "changed": [], "removed": [], "n_active": 0, "n_shadow": 1}
    txt = frr.render_summary(d)
    assert "жаргон" in txt.lower()          # жаргон в метке — виден как дефект, не спрятан


def test_no_doctor_escalation_surface():
    # питание не медицина: модуль НЕ содержит эскалации врачу
    assert not hasattr(frr, "escalate_high_stakes")
    assert not hasattr(frr, "is_high_stakes")
