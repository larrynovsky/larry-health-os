#!/usr/bin/env python3.11
"""Живая приёмка Ш2 (лаб-путь) на Studio: только чтение канона.

Печатает:
  1. режим лаб-пути под РЕАЛЬНЫМ манифестом: статус, сколько посчитано, сколько прошло;
  2. паритет выборок producer↔гейт по каждому аналиту на ЖИВЫХ данных (оракул P2-01).
     Паритет меряется при временно включённом scope — это ИЗМЕРЕНИЕ, не публикация:
     ничего никуда не пишется, вера строится только в `run()`.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

import correlation_gate as G
import longitudinal_analysis as la
import signal_family as sf

daily = la.annotate_phases(la.load_daily_df(), la.load_phases())
labs = la.load_labs_df()
lc = la.lab_metric_correlations(daily, labs)
empty_corr = pd.DataFrame(columns=["metric_a", "metric_b", "spearman_r", "p_value",
                                   "significant", "strong"])

out = {}

# 1. Реальный режим
_, lg, meta = G.gate_correlations(daily, labs, empty_corr, lc)
out["1_real_mode"] = {"scope": meta["lab_scope"], "status": meta["lab_status"],
                      "lab_tested": meta["lab_tested"], "lab_pass": meta["lab_pass"],
                      "pairs_in_frame": int(len(lg)),
                      "p_perm_computed": int(lg["p_perm"].notna().sum())}

# 2. Паритет выборок (измерение при временно активном пути)
sf.LABS_ACTIVE = True
_, _, meta_on = G.gate_correlations(daily, labs, empty_corr, lc)
sf.LABS_ACTIVE = sf.LABS_SCOPE == "active"
prod = lc.attrs.get("producer_obs_n", {})
gate = meta_on["lab_obs_n"]
mismatch = {k: [prod.get(k), gate.get(k)] for k in sorted(set(prod) | set(gate))
            if prod.get(k) != gate.get(k)}
out["2_sample_parity"] = {"analytes": len(gate), "mismatch": mismatch,
                          "hgb": [prod.get("HGB"), gate.get("HGB")]}

print(json.dumps(out, ensure_ascii=False, indent=2))
