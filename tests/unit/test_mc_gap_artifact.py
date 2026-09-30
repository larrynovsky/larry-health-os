"""_write_mc_gap_artifact (Коммит3) — штамп (дата через _time_inject + HEAD sha) для
кросс-процессного freshness-гварда читателя check_mc_gap.

Т4: штамп-поля присутствуют; дата берётся из seam get_today (инжектируемо); gate_applied=False
или отсутствие mc_gap → артефакт НЕ пишется (нечего стеречь). Импорт longitudinal тянет numpy →
на MacBook ПРОПУСК.
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
pytest.importorskip("numpy")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:
    import longitudinal_analysis as L
except Exception as e:  # noqa: BLE001
    pytest.skip(f"longitudinal_analysis не импортируется здесь: {e}", allow_module_level=True)


def test_writes_stamped_artifact(monkeypatch, tmp_path):
    monkeypatch.setattr(L, "get_today", lambda: date(2026, 7, 24))
    art = tmp_path / "gate_mc_gap_latest.json"
    meta = {"gate_applied": True,
            "mc_gap": {"rule": "…", "families": {"D": {"evaluated": True, "flagged": []},
                                                 "A": None, "q_lag": None}}}
    assert L._write_mc_gap_artifact(meta, artifact=art) is True
    payload = json.loads(art.read_text(encoding="utf-8"))
    assert payload["date"] == "2026-07-24"
    assert "head_sha" in payload and payload["head_sha"]     # sha или "unknown", но поле есть
    assert payload["mc_gap"]["families"]["A"] is None        # None сохранён, не потерян


def test_skips_when_gate_not_applied(tmp_path):
    art = tmp_path / "a.json"
    assert L._write_mc_gap_artifact({"gate_applied": False, "error": "x"}, artifact=art) is False
    assert not art.exists()


def test_skips_when_no_mc_gap_key(tmp_path):
    art = tmp_path / "a.json"
    assert L._write_mc_gap_artifact({"gate_applied": True}, artifact=art) is False
    assert not art.exists()
