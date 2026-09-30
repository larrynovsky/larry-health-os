"""Интеграция гейта с longitudinal_analysis: отбор в саммари, ОТКАЗ при сбое, single-source.

Не требует БД: build_ai_summary и _apply_gate работают на переданных DataFrame'ах.

2026-07-26: два теста этого файла (legacy-отбор и «громкая деградация») ЗАКРЕПЛЯЛИ fail-open —
они требовали ровно того поведения, которое пускало сырую корреляцию в конституции. Инвертированы,
а не дополнены: зелёный тест, защищающий дефект, дороже отсутствующего.
"""
from __future__ import annotations

import json

import pandas as pd
import pytest

import correlation_gate
import longitudinal_analysis as la

_YEARLY = pd.DataFrame({"year": [2024, 2025]})
_PHASES_DF = pd.DataFrame()
_RECOVERY: dict = {}


def _corr(rows, gated):
    """rows: (a,b,r,p,n,gate_pass,p_perm,derived). gated=False → без gate-колонок (legacy)."""
    recs = []
    for a, b, r, p, n, gp, pp, dv in rows:
        rec = {"metric_a": a, "metric_b": b, "spearman_r": r, "p_value": p, "n": n,
               "significant": p < 0.05, "strong": abs(r) >= 0.3}
        if gated:
            rec.update({"gate_pass": gp, "p_perm": pp, "derived": dv, "coverage": 0.9})
        recs.append(rec)
    return pd.DataFrame(recs)


def _lab(rows, gated):
    recs = []
    for lab, m, r, p, gp, pp, rdt in rows:
        rec = {"lab": lab, "metric": m, "label": m, "spearman_r": r, "p_value": p,
               "n": 25, "significant": p < 0.05, "strong": abs(r) >= 0.35}
        if gated:
            rec.update({"gate_pass": gp, "p_perm": pp, "r_detrended": rdt})
        recs.append(rec)
    return pd.DataFrame(recs)


def test_summary_uses_only_gate_pass(monkeypatch):
    # Лаб-ветка проверяется при ВКЛЮЧЁННОМ scope: с 2026-07-26 (ревью R3, VG-R3-04) кадр с
    # gate_pass=True при descoped считается противоречащим манифесту и отвергается на последней
    # точке перед верой — см. test_review_r3_regressions.
    monkeypatch.setattr(la._sf, "LABS_ACTIVE", True)
    corr = _corr([("hrv", "sleep_deep", 0.6, 1e-9, 500, True, 0.001, False),
                  ("hrv", "steps", 0.5, 1e-7, 500, False, 0.30, False)], gated=True)
    lab = _lab([("Creatinine", "hrv", 0.6, 1e-4, True, 0.004, 0.58),
                ("MCV", "steps", -0.5, 1e-3, False, 0.20, -0.3)], gated=True)
    s = la.build_ai_summary(_YEARLY, _PHASES_DF, _RECOVERY, corr, lab, [])
    top = {(c["a"], c["b"]) for c in s["top_correlations"]}
    assert ("hrv", "sleep_deep") in top, "пережившая гейт пара обязана быть"
    assert ("hrv", "steps") not in top, "не прошедшая гейт пара НЕ должна попасть в конституции"
    assert all(c.get("verdict") == "gated" for c in s["top_correlations"])
    assert all("p_perm" in c for c in s["top_correlations"])
    labpairs = {(c["lab"], c["metric"]) for c in s["lab_metric_correlations"]}
    assert ("Creatinine", "hrv") in labpairs and ("MCV", "steps") not in labpairs


def test_summary_without_gate_columns_refuses():
    """ИНВЕРСИЯ 2026-07-26. Прежний `test_summary_legacy_without_gate_columns` ЗАКРЕПЛЯЛ дыру:
    он ТРЕБОВАЛ, чтобы негейтованная пара попадала в саммари (legacy strong&significant).
    Пока он был зелёным, ремонт fail-open читался как регрессия. Верный контракт — отказ."""
    corr = _corr([("hrv", "sleep_deep", 0.6, 1e-9, 500, None, None, None)], gated=False)
    lab = _lab([("Creatinine", "hrv", 0.6, 1e-4, None, None, None)], gated=False)
    with pytest.raises(ValueError, match="gate_pass"):
        la.build_ai_summary(_YEARLY, _PHASES_DF, _RECOVERY, corr, lab, [])


def test_gate_failure_never_publishes_belief(monkeypatch):
    """ИНВЕРСИЯ 2026-07-26 прежнего `test_apply_gate_degrades_loud`: он проверял, что при
    исключении возвращаются ИСХОДНЫЕ df — и этого хватало, чтобы сырьё уехало в конституции.
    Возврат исходных df остался (Excel — артефакт аналитика), но вера при отказе не строится."""
    def boom(*a, **k):
        raise RuntimeError("gate boom")
    monkeypatch.setattr(correlation_gate, "gate_correlations", boom)
    corr = _corr([("hrv", "sleep_deep", 0.6, 1e-9, 500, None, None, None)], gated=False)
    lab = _lab([("Creatinine", "hrv", 0.6, 1e-4, None, None, None)], gated=False)
    cg, lg, meta = la._apply_gate(pd.DataFrame(), pd.DataFrame(), corr, lab)
    assert meta["gate_applied"] is False and meta["status"] == "failed"
    assert "error" in meta
    assert cg is corr and lg is lab                      # Excel строится, вера — нет
    assert la._publication_decision(meta) is False
    with pytest.raises(ValueError, match="гейт"):        # точка записи отказывает сама
        la.save_to_agent_reports({"gate": meta, "top_correlations": [{"a": "hrv", "b": "steps"}]})


def test_publication_decision_controls():
    """Позитивный и негативный контроль единственного правила публикации."""
    assert la._publication_decision({"gate_applied": True, "status": "applied"}) is True
    assert la._publication_decision({"gate_applied": True}) is True   # строка схемы 1 (до 26.07)
    assert la._publication_decision({"gate_applied": False, "status": "failed"}) is False
    assert la._publication_decision({"gate_applied": True, "status": "failed"}) is False
    assert la._publication_decision({}) is False


def test_gate_run_receipt_records_failure(tmp_path):
    """Квитанция прогона делает отказ СОБЫТИЕМ: без неё «строки веры нет» и «прогона не было»
    неотличимы, а именно эта неотличимость — общий корень P1-01 и мёртвой рельсы 13.07."""
    art = tmp_path / "gate_run_receipt.json"
    assert la._write_gate_run_receipt({"gate_applied": False, "status": "failed",
                                       "error": "RuntimeError('gate boom')"}, False, art)
    rec = json.loads(art.read_text(encoding="utf-8"))
    assert rec["status"] == "failed" and rec["published"] is False
    assert "gate boom" in rec["error"]
    assert la._write_gate_run_receipt({"gate_applied": True, "status": "applied"}, True, art)
    rec = json.loads(art.read_text(encoding="utf-8"))
    assert rec["status"] == "applied" and rec["published"] is True and "error" not in rec


def test_lab_window_single_source():
    assert correlation_gate.LAB_WINDOW_DAYS == la.LAB_WINDOW_DAYS, \
        "окно лаб↔daily должно иметь единый источник (split-brain guard)"
