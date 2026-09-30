"""Скелет воспроизводимой квалификации FDR-гейта (вердикт §10).

СТАТУС: scaffold. Здесь — СТРУКТУРА: две ступени (oracle-null + digital-twin), реестр
обязательных сценариев с позитивными контролями, метрики и stress-критерий. Численная
квалификация НЕ запускается, пока не заморожен data manifest (§12.2 STOP-gate): release_ready()
вернёт False, qualify() откажется. Строит на примитивах
methodology/validation_gate/fdr_harness.py (mechanism proof, НЕ release: его Stage A — оценённый
eff-df, §8).

Каркас намеренно лёгкий: numpy/fdr_harness импортируются ЛЕНИВО внутри функций, чтобы структуру
(реестр/статус) можно было инспектировать где угодно, даже без научного стека.
"""
from __future__ import annotations

from pathlib import Path

import yaml

_PROTOCOL_PATH = Path(__file__).resolve().parent / "methodology" / "validation_gate" / "qualification_protocol.yaml"


def load_protocol() -> dict:
    return yaml.safe_load(_PROTOCOL_PATH.read_text(encoding="utf-8"))


PROTOCOL = load_protocol()
MANDATORY_SCENARIOS = [s["id"] for s in PROTOCOL["mandatory_scenarios"]]


class ScenarioNotBuilt(NotImplementedError):
    """Часть протокола объявлена (§10), но ещё не построена. Пессимизм честнее ложного holds."""


def _stub(scenario_id: str):
    def gen(*_a, **_k):
        raise ScenarioNotBuilt(
            f"Сценарий '{scenario_id}' объявлен в протоколе §10, но не построен (scaffold)."
        )
    return gen


# ── Реестр обязательных сценариев: id → генератор (стаб) + позитивный контроль + флаг built ──
SCENARIO_REGISTRY = {
    s["id"]: {"generator": _stub(s["id"]), "positive_control": s["positive_control"], "built": False}
    for s in PROTOCOL["mandatory_scenarios"]
}


def _lazy_stage_a():
    """Ленивая загрузка numeric-движка Stage A (numpy/scipy) из methodology/validation_gate/."""
    import sys
    d = str(Path(__file__).resolve().parent / "methodology" / "validation_gate")
    if d not in sys.path:
        sys.path.insert(0, d)
    import fdr_stage_a
    return fdr_stage_a


# ── Построенные сценарии (Stage A oracle): global/partial-null + mixed-sign ──
def _scn_global_null(dependence="independent", m=45, rho=0.5, q=0.10, trials=2000, seed=1):
    return _lazy_stage_a().run_oracle_cell(m, 0, dependence, rho, 0.0, q, trials, seed)


def _scn_partial_null(dependence="independent", m=45, m1=8, rho=0.5, effect=3.5, q=0.10, trials=2000, seed=2):
    return _lazy_stage_a().run_oracle_cell(m, m1, dependence, rho, effect, q, trials, seed)


def _scn_mixed_sign(m=45, m1=8, rho=0.5, effect=3.5, q=0.10, trials=2000, seed=3):
    return _lazy_stage_a().run_oracle_cell(m, m1, "mixed_sign", rho, effect, q, trials, seed)


def _scn_common_latent(m=45, m1=0, rho=0.6, effect=0.0, q=0.10, trials=2000, seed=4):
    # Общий скрытый драйвер коррелирует ВСЕ метрики. Под global-null (m1=0) не должен рождать
    # ложных открытий сверх q. Это СТАТИСТИЧЕСКИЙ common-latent; композит×его-вход (readiness из
    # HRV/сна) — отдельный structural/QC-контур гейта (derived-фильтр), не здесь.
    return _lazy_stage_a().run_oracle_cell(m, m1, "common_latent", rho, effect, q, trials, seed)


_STAGE_A_BUILT = {
    "global_null": _scn_global_null,
    "partial_null": _scn_partial_null,
    "mixed_sign_dependence": _scn_mixed_sign,
    "common_latent_drivers": _scn_common_latent,
}
for _id, _fn in _STAGE_A_BUILT.items():
    SCENARIO_REGISTRY[_id]["generator"] = _fn
    SCENARIO_REGISTRY[_id]["built"] = True


# ── Stage B (digital-twin, methodology/validation_gate/stage_b_scenarios.py) — ПОСТРОЕНЫ и прогнаны
#    release 2026-07-13 (protocol.release_result, ACCEPTANCE=True). repeated_looks/data_triggered → Ф4. ──
_STAGE_B_BUILT = {
    "local_trends": "stage_b_scenarios.trend_broken",
    "variance_shifts": "stage_b_scenarios.variance_shifts",
    "changepoints": "stage_b_scenarios.combined_nonstat + changepoint",
    "overlapping_windows": "stage_b_scenarios.overlapping",
}
for _id, _ref in _STAGE_B_BUILT.items():
    if _id in SCENARIO_REGISTRY:
        SCENARIO_REGISTRY[_id]["built"] = True
        SCENARIO_REGISTRY[_id]["ref"] = _ref


# ── Две ступени (§10) — интерфейсы-стабы ──
def oracle_null_pvalues(m, m1, dependence="independent", rho=0.5, effect=0.0, seed=0):
    """Stage A: p из ИЗВЕСТНОГО null (marginal Uniform + Σ-copula), НЕ оценённый eff-df (§8).

    ПОСТРОЕНО (fdr_stage_a.oracle_pvalues). Возвращает (p, truth).
    """
    import numpy as _np
    return _lazy_stage_a().oracle_pvalues(m, m1, dependence, rho, effect, _np.random.default_rng(seed))


def digital_twin_run(*_a, **_k):
    """Stage B: end-to-end twin (пароспецифичные ACF/спектры, режимы терапии, changepoints,
    missingness, ML-selection, p-engine, контроллер). НЕ построено — нужен замороженный манифест.
    """
    raise ScenarioNotBuilt("digital_twin не построен (нужен data manifest: ACF/режимы/пропуски).")


# ── Метрики (§10) — чистый python, без numpy ──
def metrics_from_trial(rejected: list, tested_pairs: list, truth_set: set) -> dict:
    """FDP / power / any-false по одному прогону.

    rejected[i] — отклонена ли пара tested_pairs[i]; truth_set — истинно-ненулевые пары.
    """
    R = sum(1 for r in rejected if r)
    V_false = sum(1 for r, p in zip(rejected, tested_pairs) if r and p not in truth_set)
    TP = sum(1 for r, p in zip(rejected, tested_pairs) if r and p in truth_set)
    fdp = V_false / R if R else 0.0
    power = (TP / len(truth_set)) if truth_set else float("nan")
    return {"R": R, "V_false": V_false, "TP": TP, "fdp": fdp, "power": power, "any_false": V_false >= 1}


# ── Готовность и приёмка ──
def release_ready() -> bool:
    """Release-квалификация разрешена ТОЛЬКО когда манифест заморожен И все сценарии построены."""
    return bool(PROTOCOL.get("frozen_manifest")) and all(v["built"] for v in SCENARIO_REGISTRY.values())


def stress_criterion_met(simultaneous_bounds: dict, q: float, delta: float) -> bool:
    """§10 stress: каждая одновременная верхняя граница выбранной error-метрики ≤ q + δ."""
    return all(b <= q + delta for b in simultaneous_bounds.values())


def qualify(*_a, **_k):
    """Полная release-квалификация. Отказывается, пока не release_ready() — иначе числа
    иллюстративны, не release (§8, §12.2)."""
    if not release_ready():
        missing = [k for k, v in SCENARIO_REGISTRY.items() if not v["built"]]
        raise ScenarioNotBuilt(
            f"qualify() заблокирован: frozen_manifest={PROTOCOL.get('frozen_manifest')}, "
            f"не построены сценарии {missing}. Прогон дал бы иллюстративные, не release-числа."
        )
    rr = PROTOCOL.get("release_result")
    if not rr or not rr.get("acceptance_all_holm_le_q_delta"):
        raise ScenarioNotBuilt("release_result отсутствует или stress-критерий не пройден — не qualified.")
    return {"qualified": True, "acceptance_all_holm_le_q_delta": True,
            "run_at": rr.get("run_at"), "scenarios": rr.get("scenarios")}


def status() -> dict:
    rr = PROTOCOL.get("release_result") or {}
    return {
        "protocol_version": PROTOCOL["version"],
        "status": PROTOCOL["status"],
        "frozen_manifest": bool(PROTOCOL.get("frozen_manifest")),
        "scenarios_total": len(SCENARIO_REGISTRY),
        "scenarios_built": sum(v["built"] for v in SCENARIO_REGISTRY.values()),
        "release_ready": release_ready(),
        "qualified": bool(rr.get("acceptance_all_holm_le_q_delta")) and release_ready(),
        "deferred_online_Ф4": [s["id"] for s in PROTOCOL.get("deferred_online_scenarios", [])],
    }


if __name__ == "__main__":
    import json
    print(json.dumps(status(), ensure_ascii=False, indent=2))
    print("mandatory scenarios:", MANDATORY_SCENARIOS)
