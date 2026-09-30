"""check_passset_flicker (Ф4 предохранитель, Коммит B) — читает ГОТОВОЕ поле flicker_vs_prev
последнего снимка (единый источник; sha-гвард в писателе longitudinal). Проверяем ветки читателя:
флаги→warn / пусто→тишина / устарел→liveness / нет/битый→покрыто/fail-loud + delivery-гард.

Импорт integrity_tests требует health_db (Studio-гвард) → на MacBook ПРОПУСК. Штамп — monkeypatch it.today.
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:
    import integrity_tests as it
except Exception as e:  # noqa: BLE001
    pytest.skip(f"integrity_tests не импортируется здесь: {e}", allow_module_level=True)

TODAY = date(2026, 7, 26)


def _snap(d, sha="s", flicker=None):
    return {"date": d, "head_sha": sha, "members": {}, "flicker_vs_prev": flicker or {}}


def _write(path, snaps):
    path.write_text(json.dumps(snaps), encoding="utf-8")


def _run(monkeypatch, art):
    cap = []
    monkeypatch.setattr(it, "warn", lambda name, detail="": cap.append((name, detail)))
    monkeypatch.setattr(it, "today", TODAY)
    res = it.check_passset_flicker(artifact=art)
    return cap, res


def test_flicker_field_warns(monkeypatch, tmp_path):
    art = tmp_path / "h.json"
    _write(art, [_snap("2026-07-26", flicker={"D": {"entered": ["e×f"], "left": ["c×d"]}})])
    cap, res = _run(monkeypatch, art)
    assert len(cap) == 1 and "замерцал" in cap[0][0]
    assert "вошли: e×f" in cap[0][1] and "убыли: c×d" in cap[0][1]
    assert res["flicker"]


def test_empty_field_silent(monkeypatch, tmp_path):
    art = tmp_path / "h.json"
    _write(art, [_snap("2026-07-26", flicker={})])
    cap, res = _run(monkeypatch, art)
    assert cap == [] and res["flicker"] == []


def test_missing_field_silent(monkeypatch, tmp_path):
    """Снимок без flicker_vs_prev (первый под sha / старый формат) → тишина, не крэш."""
    art = tmp_path / "h.json"
    art.write_text(json.dumps([{"date": "2026-07-26", "head_sha": "s", "members": {}}]), encoding="utf-8")
    cap, res = _run(monkeypatch, art)
    assert cap == [] and res["flicker"] == []


def test_stale_snapshot_liveness(monkeypatch, tmp_path):
    art = tmp_path / "h.json"
    _write(art, [_snap("2026-07-01", flicker={"D": {"entered": ["x×y"], "left": []}})])  # 25д > 8
    cap, res = _run(monkeypatch, art)
    assert len(cap) == 1 and "замолк" in cap[0][0]
    assert res is None   # устаревшему содержимому не доверяем (даже если поле есть)


def test_missing_file_silent(monkeypatch, tmp_path):
    cap, res = _run(monkeypatch, tmp_path / "nope.json")
    assert cap == [] and res is None


def test_corrupt_fails_loud(monkeypatch, tmp_path):
    art = tmp_path / "h.json"
    art.write_text("{ не json", encoding="utf-8")
    cap, res = _run(monkeypatch, art)
    assert len(cap) == 1 and "не читается" in cap[0][0]


def test_bad_stamp_fails_loud(monkeypatch, tmp_path):
    art = tmp_path / "h.json"
    _write(art, [_snap("не-дата", flicker={"D": {"entered": ["x×y"], "left": []}})])
    cap, res = _run(monkeypatch, art)
    assert len(cap) == 1 and "штамп" in cap[0][0]


def test_warn_not_muted_delivery_guard():
    ta = pytest.importorskip("triage_agent")
    out = ta.classify_warnings([
        ("pass-set замерцал: 1 семей(ья) сменили членство", "d"),
        ("pass-set снимок замолк: 9д (плановый запуск 20.09 03:00 не отметился)", "d"),
        ("pass-set снимок: живость не судима", "d"),
    ])
    assert len(out) == 3, "все warn обязаны дойти (не в MUTE)"


def test_future_stamp_warns(monkeypatch, tmp_path):
    """Штамп снимка из будущего (age<0) → warn, не доверяет как «свежему» (находка ревью 2026-07-24)."""
    art = tmp_path / "h.json"
    _write(art, [_snap("2026-08-01", flicker={"D": {"entered": ["x×y"], "left": []}})])  # в будущем
    cap, res = _run(monkeypatch, art)
    assert len(cap) == 1 and "будущего" in cap[0][0]
    assert res is None


# ── Находки внешнего ревью 2026-07-25 ─────────────────────────────────────────
def test_non_dict_element_warns_not_crashes(monkeypatch, tmp_path):
    """P2: валидный JSON-список `[42]` проходил верхний гард и падал на `.get` (AttributeError).
    Тот же класс чинили в check_mc_gap @417feac — фикс был точечным, sibling остался."""
    import json as _j
    import integrity_tests as it
    cap = []
    monkeypatch.setattr(it, "warn", lambda name, detail="": cap.append((name, detail)))
    art = tmp_path / "a.json"
    art.write_text("[42]", encoding="utf-8")
    assert it.check_passset_flicker(artifact=art) is None      # не крэш
    assert any("неформатен" in n for n, _ in cap), cap
    del _j


def test_history_reset_is_loud(monkeypatch, tmp_path):
    """P1: писатель начал историю заново (битый артефакт) → читатель обязан кричать, иначе
    «стабильно» на одном снимке неотличимо от настоящей стабильности."""
    import json as _j
    import integrity_tests as it
    cap = []
    monkeypatch.setattr(it, "warn", lambda name, detail="": cap.append((name, detail)))
    art = tmp_path / "b.json"
    art.write_text(_j.dumps([{"date": str(it.today), "head_sha": "s", "members": {"D": []},
                              "flicker_vs_prev": {}, "history_reset": "артефакт не читается: ValueError"}]),
                   encoding="utf-8")
    it.check_passset_flicker(artifact=art)
    assert any("история сброшена" in n for n, _ in cap), cap
