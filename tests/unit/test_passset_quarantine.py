"""Карантин мерцающих пар (Ф4 предохранитель, Коммит B): build_ai_summary ставит online_status,
читатели (конституция/gp_context) подают пару НЕ как находку. Единый источник — _quarantined_pairs.

build_ai_summary тянет numpy → на MacBook ПРОПУСК.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
pytest.importorskip("numpy")
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:
    import longitudinal_analysis as L
    import generate_constitutions as GC
    import correlation_gate as G          # единый источник слов метки семьи
except Exception as e:  # noqa: BLE001
    pytest.skip(f"модули не импортируются здесь: {e}", allow_module_level=True)


def _minimal_summary(quarantined):
    yearly = pd.DataFrame([{"year": 2024, "sleep_total_mean": 7.0},
                           {"year": 2025, "sleep_total_mean": 7.1}])
    corr = pd.DataFrame([
        {"metric_a": "sleep_total", "metric_b": "hrv", "spearman_r": 0.6, "p_value": 1e-6,
         "p_perm": 0.001, "gate_pass": True, "verdict_family": "A-lever", "strong": True, "significant": True},
        {"metric_a": "steps", "metric_b": "active_kcal", "spearman_r": 0.7, "p_value": 1e-6,
         "p_perm": 0.001, "gate_pass": True, "verdict_family": "D-only", "strong": True, "significant": True},
    ])
    lab = pd.DataFrame(columns=["lab", "metric", "r", "p", "strong", "significant"])
    return L.build_ai_summary(yearly, pd.DataFrame(), {}, corr, lab, [], gate_meta={"gate_applied": True},
                              quarantined=quarantined)


def test_marks_flickering_pair_only():
    s = _minimal_summary({"sleep_total×hrv"})   # эта пара замерцала, steps×active_kcal стабильна
    by_pair = {f"{c['a']}×{c['b']}": c for c in s["top_correlations"]}
    assert by_pair["sleep_total×hrv"].get("online_status") == "pending_adjudication"
    assert "online_status" not in by_pair["steps×active_kcal"]        # стабильная — не карантин (parity)


def test_no_quarantine_when_empty():
    s = _minimal_summary(set())
    assert all("online_status" not in c for c in s["top_correlations"])


def _fake_conn(findings_json):
    class _Row(dict):
        pass
    class _Conn:
        def execute(self, *a, **k):
            class _Cur:
                def fetchone(_self):
                    # `date` (не created_at): с 2026-07-26 веру читает belief_contract своим
                    # запросом — ORDER BY date DESC, id DESC.
                    return {"findings": findings_json, "date": "2026-07-26",
                            "created_at": "2026-07-26"}
            return _Cur()
        def __enter__(self): return self
        def __exit__(self, *a): return False
    return _Conn()


def test_constitution_reader_shows_quarantine_note(monkeypatch):
    """⭐ Карантин доходит до ЖИВОЙ поверхности: конституция подаёт мерцающую пару как «не находка»,
    БЕЗ family_label «устойчивая связь». RST: пометка без доставки в текст = карантин, который не карантинит."""
    import json as _json
    import health_db
    findings = _json.dumps({
        "top_correlations": [
            {"a": "sleep_total", "b": "hrv", "r": 0.6, "p": 1e-6, "verdict_family": "A-lever",
             "online_status": "pending_adjudication"},
            {"a": "steps", "b": "active_kcal", "r": 0.7, "p": 1e-6, "verdict_family": "D-only"},
        ],
        # С 2026-07-26 читатель принимает только гейтованную веру (belief_contract).
        "gate": {"gate_applied": True, "status": "applied"}})
    monkeypatch.setattr(health_db, "get_conn", lambda *a, **k: _fake_conn(findings))
    txt = GC._get_longitudinal_context()
    assert "sleep_total ↔ hrv" in txt and "ждёт онлайн-контроллера" in txt
    # Мерцающая НЕ подана меткой семьи A. Метку берём из ЕДИНОГО источника, а не литералом:
    # 2026-07-31 формулировка A-lever изменилась, и захардкоженная строка сделала бы этот
    # assert пусто-зелёным — он проходил бы всегда, ничего не стерегя.
    assert G.family_label("A-lever") not in txt.split("sleep_total ↔ hrv")[1].split("\n")[0]
