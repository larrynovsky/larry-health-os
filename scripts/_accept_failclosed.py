#!/usr/bin/env python3.11
"""Живая приёмка fail-closed (Ш1) на Studio. Только чтение канона + tmp-артефакты.

Печатает четыре факта:
  1. инъекция исключения в гейт → publication_decision False, квитанция отказа записана;
  2. точка записи веры отказывает (ДО обращения к БД — строка не появляется);
  3. build_ai_summary отказывается строить веру из негейтованного кадра;
  4. живая вера в каноне ПРИНИМАЕТСЯ контрактом (позитивный контроль: правило не запрещает всё).
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # чек обязан запускаться без PYTHONPATH

import pandas as pd

import correlation_gate
import longitudinal_analysis as la
from belief_contract import read_belief

out = {}

# 1. Отказ гейта
_orig = correlation_gate.gate_correlations
correlation_gate.gate_correlations = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("ACCEPT boom"))
corr = pd.DataFrame([{"metric_a": "hrv", "metric_b": "sleep_deep", "spearman_r": 0.6,
                      "p_value": 1e-9, "n": 500, "significant": True, "strong": True}])
lab = pd.DataFrame(columns=["lab", "metric", "spearman_r", "p_value", "strong", "significant"])
cg, lg, meta = la._apply_gate(pd.DataFrame(), pd.DataFrame(), corr, lab)
correlation_gate.gate_correlations = _orig

tmp = Path(tempfile.mkdtemp()) / "gate_run_receipt.json"
la._write_gate_run_receipt(meta, la._publication_decision(meta), tmp)
out["1_gate_failed"] = {"status": meta.get("status"),
                        "publish": la._publication_decision(meta),
                        "receipt": json.loads(tmp.read_text())["status"]}

# 2. Точка записи отказывает
try:
    la.save_to_agent_reports({"gate": meta, "top_correlations": [{"a": "hrv", "b": "steps"}]})
    out["2_writer_refuses"] = False
except ValueError:
    out["2_writer_refuses"] = True

# 3. Саммари не строится без гейта
try:
    la.build_ai_summary(pd.DataFrame({"year": [2024, 2025]}), pd.DataFrame(), {}, corr, lab, [])
    out["3_summary_refuses"] = False
except ValueError:
    out["3_summary_refuses"] = True

# 4. Позитивный контроль на ЖИВОЙ вере (только чтение)
b = read_belief()
out["4_live_belief"] = {"accepted": b["accepted"], "reason": b["reason"],
                        "generated_at": b["generated_at"], "age_days": b["age_days"],
                        "run_failed": b["run_failed"],
                        "pairs": len((b["data"] or {}).get("top_correlations", []))}

print(json.dumps(out, ensure_ascii=False, indent=2))
