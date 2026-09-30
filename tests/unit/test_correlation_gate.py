"""Контракт-тест correlation_gate (синтетика, без БД).

Гарантирует три свойства гейта, который фильтрует longitudinal-корреляции до конституций:
  1. посаженная реальная связь (низкая автокорреляция) — ПРОХОДИТ;
  2. два независимых AR(1)-ряда (автокорреляционный фантом) — НЕ проходят;
  3. derived-метрика (функция других) — помечается и исключается.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from scipy.stats import spearmanr

import correlation_gate as G

_EMPTY_LAB = pd.DataFrame(columns=["lab", "metric", "label", "spearman_r",
                                   "p_value", "n", "significant", "strong"])
_EMPTY_LABS = pd.DataFrame(columns=["date", "test_name", "value", "unit"])


def _mkcorr(rows):
    return pd.DataFrame([{"metric_a": a, "metric_b": b, "spearman_r": r, "p_value": p,
                          "n": n, "significant": p < 0.05, "strong": abs(r) >= 0.3}
                         for a, b, r, p, n in rows])


def _ar1(phi, n, rng):
    x = np.zeros(n)
    for t in range(1, n):
        x[t] = phi * x[t - 1] + rng.normal()
    return x


def _sp(x, y):
    r, p = spearmanr(x, y)
    return float(r), float(p)


def test_planted_real_passes():
    rng = np.random.default_rng(42)
    days = pd.date_range("2022-01-01", periods=600, freq="D")
    a = rng.normal(size=600)
    b = a * 0.7 + rng.normal(scale=0.7, size=600)
    e = _ar1(0.95, 600, rng)
    f = _ar1(0.95, 600, rng)
    daily = pd.DataFrame({"date": days, "hrv": a, "sleep_deep": b, "steps": e, "active_kcal": f})
    r_ab, p_ab = _sp(a, b)
    r_ef, p_ef = _sp(e, f)
    corr = _mkcorr([("hrv", "sleep_deep", r_ab, p_ab, 600),
                    ("steps", "active_kcal", r_ef, min(p_ef, 0.0001), 600)])
    cg, _, meta = G.gate_correlations(daily, _EMPTY_LABS, corr, _EMPTY_LAB)
    ab = cg[(cg.metric_a == "hrv") & (cg.metric_b == "sleep_deep")].iloc[0]
    ef = cg[(cg.metric_a == "steps") & (cg.metric_b == "active_kcal")].iloc[0]
    assert bool(ab.gate_pass) is True, "посаженная реальная связь обязана пройти"
    assert bool(ef.gate_pass) is False, "AR(1)-фантом обязан упасть"
    assert meta["gate_applied"] is True


def test_derived_excluded():
    rng = np.random.default_rng(7)
    days = pd.date_range("2022-01-01", periods=600, freq="D")
    x = rng.normal(size=600)
    y = rng.normal(size=600)
    z = x + y
    daily = pd.DataFrame({"date": days, "x": x, "y": y, "z": z})
    rxz, pxz = _sp(x, z)
    corr = _mkcorr([("x", "z", rxz, pxz, 600)])
    cg, _, _ = G.gate_correlations(daily, _EMPTY_LABS, corr, _EMPTY_LAB)
    xz = cg[(cg.metric_a == "x") & (cg.metric_b == "z")].iloc[0]
    assert bool(xz.derived) is True, "derived-метрика обязана быть помечена"
    assert bool(xz.gate_pass) is False, "derived-пара не должна проходить гейт"


def test_empty_inputs_safe():
    cg, lg, meta = G.gate_correlations(
        pd.DataFrame({"date": pd.to_datetime([])}), _EMPTY_LABS, _EMPTY_LAB, _EMPTY_LAB)
    assert "gate_pass" in cg.columns and "gate_pass" in lg.columns
    assert meta["gate_applied"] is True


def test_r2_full_family_tested():
    """R2 (§6/R2): permutation-p считается для ВСЕЙ жизнеспособной семьи, даже если
    сырой скрин p<0.05 её не выделил. BH-знаменатель = полная семья, не data-screened.

    Позит.контроль встроен: все пары помечены significant=False. При СТАРОМ поведении
    (screen по significant) tested==0 → assert краснеет. При R2 tested==число пар ≥30 overlap.
    Реинтродукция скрина → тест падает (break→red)."""
    rng = np.random.default_rng(1)
    days = pd.date_range("2022-01-01", periods=400, freq="D")
    daily = pd.DataFrame({"date": days,
                          "hrv": rng.normal(size=400), "sleep_deep": rng.normal(size=400),
                          "steps": rng.normal(size=400), "active_kcal": rng.normal(size=400)})
    pairs = [("hrv", "sleep_deep"), ("hrv", "steps"), ("hrv", "active_kcal"),
             ("sleep_deep", "steps"), ("sleep_deep", "active_kcal"), ("steps", "active_kcal")]
    corr = pd.DataFrame([{"metric_a": a, "metric_b": b, "spearman_r": 0.0, "p_value": 0.9,
                          "n": 400, "significant": False, "strong": False} for a, b in pairs])
    cg, _, _ = G.gate_correlations(daily, _EMPTY_LABS, corr, _EMPTY_LAB)
    tested = int(cg["p_perm"].notna().sum())
    assert tested == len(pairs), (
        f"R2: вся семья обязана тестироваться (ожидалось {len(pairs)}, получено {tested}); "
        "значимость сырого p не должна усекать FDR-семью")


def test_bh_threshold_denominator_anticonservative():
    """R2-механизм: усечение семьи до значимых занижает m → поднимает BH-порог
    (анти-консервативно). На одних и тех же p полная семья даёт НЕ больший порог."""
    p_full = np.array([0.001, 0.02, 0.03, 0.2, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95])
    p_screened = p_full[p_full < 0.05]        # что «видел» бы сырой скрин p<0.05
    thr_full = G._bh_threshold(p_full, 0.10)
    thr_screened = G._bh_threshold(p_screened, 0.10)
    assert thr_full <= thr_screened, (
        "усечение семьи до значимых поднимает порог (анти-консервативно): "
        f"full={thr_full:.4f} screened={thr_screened:.4f}")


# ── Ф3: BH→BY + derived вне FDR-семьи (2026-07-13) ───────────────────────────

def test_by_harmonic_and_monotonic():
    """Т1 — H_m точное; BY-порог (q/H_m) ≤ BH-порог (q) на тех же p; m=1 → BY=BH."""
    assert abs(G._H(1) - 1.0) < 1e-12
    assert abs(G._H(3) - (1 + 0.5 + 1/3)) < 1e-12
    assert abs(G._H(65) - 4.7593) < 1e-3
    p = np.array([0.001, 0.001, 0.02, 0.03, 0.2, 0.5, 0.7, 0.9])
    thr_bh = G._bh_threshold(p, 0.10)
    thr_by = G._bh_threshold(p, 0.10 / G._H(len(p)))
    assert thr_by <= thr_bh, f"BY не либеральнее BH: BY={thr_by} BH={thr_bh}"
    p1 = np.array([0.02])
    assert G._bh_threshold(p1, 0.10) == G._bh_threshold(p1, 0.10 / G._H(1)), "m=1: BY≡BH"


def test_by_strictly_stricter_than_bh_synthetic():
    """Т2 (позит.контроль, real-parity ПУСТ) — данные, где BY отклоняет СТРОГО меньше BH.
    Break-back (не делить q на H) → BH-поведение (больше отклонений) → тест ловит именно BY."""
    m = 10
    p = np.array([0.006] + [0.30] * (m - 1))
    thr_bh = G._bh_threshold(p, 0.10)                 # 0.006 ≤ 0.10·1/10=0.01 → 1 отклонён
    thr_by = G._bh_threshold(p, 0.10 / G._H(m))       # q/(m·H)=0.00341; 0.006>0.00341 → 0
    n_bh, n_by = int((p <= thr_bh).sum()), int((p <= thr_by).sum())
    assert n_bh == 1 and n_by == 0, f"BY строго строже: BH={n_bh} BY={n_by}"


def test_by_parity_gate_vs_harness():
    """Т4 (consistency) — BY через живой гейт (_bh_threshold при q/H) == harness by_reject маска.
    Один смысл, две реализации; стережёт split-brain контроллеров."""
    import sys, pathlib
    sys.path.insert(0, str(pathlib.Path(G.__file__).resolve().parent / "methodology" / "validation_gate"))
    from fdr_harness import by_reject
    rng = np.random.default_rng(0)
    for _ in range(20):
        m = int(rng.integers(3, 40))
        p = rng.random(m)
        thr = G._bh_threshold(p, 0.10 / G._H(m))
        assert np.array_equal(p <= thr, by_reject(p, 0.10)), f"gate BY ≠ harness by_reject (m={m})"


def test_derived_excluded_from_fdr_family_m():
    """Ф3 ПОВЕДЕНЧЕСКИЙ (закрывает слабость source-presence сторожа): derived-пары ВНЕ
    знаменателя FDR-семьи. m_family = число non-derived пар, НЕ всех tested.
    Break-back (вернуть derived в m) → m_family вырос бы → тест краснеет."""
    rng = np.random.default_rng(3)
    n = 400   # v10: ≥ min_overlap_days (365), иначе пары «не тестировались» и m=0
    days = pd.date_range("2022-01-01", periods=n, freq="D")
    # sleep_score ∈ KNOWN_DERIVED; hrv, sleep_deep — нет
    daily = pd.DataFrame({"date": days, "hrv": rng.normal(size=n),
                          "sleep_deep": rng.normal(size=n), "sleep_score": rng.normal(size=n)})
    pairs = [("hrv", "sleep_deep"), ("hrv", "sleep_score"), ("sleep_deep", "sleep_score")]
    corr = pd.DataFrame([{"metric_a": a, "metric_b": b, "spearman_r": 0.0, "p_value": 0.5,
                          "n": n, "significant": False, "strong": False} for a, b in pairs])
    cg, _, _ = G.gate_correlations(daily, _EMPTY_LABS, corr, _EMPTY_LAB)
    assert int(cg["derived"].sum()) == 2, "две пары с sleep_score обязаны быть derived"
    assert cg.attrs["m_family"] == 1, \
        f"m_family = non-derived (ожидалось 1, получено {cg.attrs['m_family']}); derived в знаменателе?"


def test_daily_and_lab_by_source_guard():
    """daily И lab пути = BY (q/_H). Читает реальный correlation_gate.py — ловит регрессию
    «путь вернулся к BH» (потеря _H). Для двусторонних тестов PRDS не доказан,
    поэтому оба пути обязаны применять поправку BY."""
    import pathlib
    import re
    src = pathlib.Path(G.__file__).read_text(encoding="utf-8")

    def _slice(fn):
        m = re.search(r"\ndef " + fn + r"\(.*?(?=\ndef )", src, re.S)
        return m.group(0) if m else ""

    daily, lab = _slice("_gate_daily"), _slice("_gate_labs")
    assert daily and lab, "не найдены _gate_daily/_gate_labs (структура изменилась?)"
    assert "FDR_Q / _H(" in daily, "daily-путь обязан быть BY (FDR_Q / _H)"
    assert "FDR_Q / _H(" in lab, "lab-путь = BY (FDR_Q / _H) с 2026-07-21 (спека §6.3, re-gate)"
    assert "FDR_Q" in lab, "lab-путь должен использовать FDR_Q"


# ── Gate 0 полнота: часть-целое на уровне ПАРЫ (2026-08-06, validation-gate-repair) ──────────

def test_part_whole_pair_blocked_gate0():
    """ПРЯМАЯ пара часть-целое (компонент⊂целое) исключается из семьи-открытий и gate_pass,
    хотя _derived_metrics её НЕ ловит: sleep_light не метрика → total из deep+rem R²-детектом
    не реконструируется. Единственный дом факта — реестр part_whole_pairs (signal_family.yaml).

    Если компонент и целое проходят как открытие, модель получает тавтологию.
    Break-back (опустошить part_whole_pairs) → total×deep снова проходит находкой → тест краснеет.
    Негативный контроль ИСПОЛНЕН: содержательная посаженная пара (hrv×sleep_deep) НЕ structural
    и проходит — иначе метка ловила бы всё подряд.
    """
    rng = np.random.default_rng(11)
    n = 600
    days = pd.date_range("2022-01-01", periods=n, freq="D")
    deep = rng.normal(size=n)
    rem = rng.normal(size=n)
    light = rng.normal(size=n) * 3.0                    # большая доля → total НЕ R²-реконструируем из deep+rem
    total = deep + rem + light                          # deep⊂total, rem⊂total ОПРЕДЕЛЕНИЕМ
    hrv = deep * 0.6 + rng.normal(scale=0.8, size=n)    # посаженная СОДЕРЖАТЕЛЬНАЯ связь (не часть-целое)
    daily = pd.DataFrame({"date": days, "sleep_total": total, "sleep_deep": deep,
                          "sleep_rem": rem, "hrv": hrv})
    r_td, p_td = _sp(total, deep)
    r_hd, p_hd = _sp(hrv, deep)
    corr = _mkcorr([("sleep_total", "sleep_deep", r_td, min(p_td, 1e-4), n),
                    ("hrv", "sleep_deep", r_hd, min(p_hd, 1e-4), n)])
    cg, _, meta = G.gate_correlations(daily, _EMPTY_LABS, corr, _EMPTY_LAB)
    td = cg[(cg.metric_a == "sleep_total") & (cg.metric_b == "sleep_deep")].iloc[0]
    hd = cg[(cg.metric_a == "hrv") & (cg.metric_b == "sleep_deep")].iloc[0]
    assert bool(td.structural) is True, "часть-целое обязана быть помечена structural"
    assert bool(td.gate_pass) is False, "часть-целое не должна проходить гейт как находка"
    assert bool(hd.structural) is False, "содержательная пара НЕ структурна (негативный контроль)"
    assert bool(hd.gate_pass) is True, "посаженная содержательная связь обязана пройти"
    assert meta["daily_structural"] >= 1, "блок часть-целое обязан быть виден числом в meta (§14)"


def test_algorithmic_derived_pair_blocked_gate0():
    """Пара «алгоритмическая производная одного конвейера» (steps×active_kcal) исключается из
    gate_pass реестром algorithmic_derived_pairs (решение владельца 2026-08-13, нить
    validation-gate-repair): kcal ВЫЧИСЛЯЕТСЯ трекером из движения — связь о приборе, не о теле.
    Числовой порог может пропустить умеренную алгоритмическую зависимость.
    Единственный дом определения: реестр signal_family.yaml, читатель STRUCTURAL_PAIRS.

    Break-back (опустошить algorithmic_derived_pairs) → steps×active_kcal снова проходит
    находкой → тест краснеет. Негативный контроль: посаженная содержательная пара
    (hrv×steps) НЕ structural и проходит.
    """
    rng = np.random.default_rng(13)
    n = 600
    days = pd.date_range("2022-01-01", periods=n, freq="D")
    steps = rng.normal(size=n)
    kcal = steps * 0.7 + rng.normal(scale=0.7, size=n)   # шум велик: R²-детект метрики НЕ ловит
    hrv = steps * 0.6 + rng.normal(scale=0.8, size=n)    # посаженная СОДЕРЖАТЕЛЬНАЯ связь
    daily = pd.DataFrame({"date": days, "steps": steps, "active_kcal": kcal, "hrv": hrv})
    r_sk, p_sk = _sp(steps, kcal)
    r_hs, p_hs = _sp(hrv, steps)
    corr = _mkcorr([("steps", "active_kcal", r_sk, min(p_sk, 1e-4), n),
                    ("hrv", "steps", r_hs, min(p_hs, 1e-4), n)])
    cg, _, meta = G.gate_correlations(daily, _EMPTY_LABS, corr, _EMPTY_LAB)
    sk = cg[(cg.metric_a == "steps") & (cg.metric_b == "active_kcal")].iloc[0]
    hs = cg[(cg.metric_a == "hrv") & (cg.metric_b == "steps")].iloc[0]
    assert bool(sk.structural) is True, "алгоритмическая производная обязана быть помечена structural"
    assert bool(sk.gate_pass) is False, "steps×active_kcal не должна проходить гейт как находка"
    assert bool(hs.structural) is False, "содержательная пара НЕ структурна (негативный контроль)"
    assert bool(hs.gate_pass) is True, "посаженная содержательная связь обязана пройти"
    assert meta["daily_structural"] >= 1, "блок обязан быть виден числом в meta (§14)"


# ── Gate 2 «Period Profile» как БЛОК (ВКЛЮЧЁН 2026-08-08, решение владельца «Global») ─────────

def test_gate2_blocks_weak_and_unstable():
    """Предикат Gate 2: блок при слабой величине ИЛИ переворотe знака по эпохам; сильная+стабильная
    проходит; НЕТ профиля (median NaN — тенант/мало эпох) НЕ блокируется (Gate 2 неприменим, fail-open).
    Каждая ветка краснеет отдельно. Порог — из gate2_profile (zero-false), не хардкод.
    Break-back (< на >, потеря isnan-охраны, хардкод порога) → тест краснеет."""
    import numpy as np
    med = [0.121, 0.30, 0.25, np.nan, 0.05]   # слаб.величина | знак<1 | ок | нет профиля | слаб
    sig = [0.70,  0.80, 1.00, np.nan, 1.00]
    blocked = list(G._gate2_blocked(med, sig))
    assert blocked == [True, True, False, False, True], f"ветки Gate 2 разъехались: {blocked}"
    assert G._sf.GATE2_R_MIN == 0.225 and G._sf.GATE2_SIGN_MIN == 1.0, \
        "порог Gate 2 не равен калиброванной zero-false точке (0.225 / s=1.0)"


def test_gate2_judges_negative_relationship_by_magnitude():
    """Отрицательная связь судится по модулю медианы, как положительная (аудит фильтра 25.09):
    до правки медиана −0.38 при знаке 1.0 блокировалась «слабой величиной» — все отрицательные
    связи были закрыты для веры навсегда."""
    import numpy as np
    med = [-0.382, -0.121, 0.382, -0.30]
    sig = [1.00, 1.00, 1.00, 0.80]
    assert list(G._gate2_blocked(med, sig)) == [False, True, False, True]


# ── Gate 0.5 «Suppression Detector» (ВКЛЮЧЁН 2026-08-08) ─────────────────────────────────────

@pytest.mark.owner_data   # synthetic_spec.yaml — данные установки (приватная зона)
def test_gate05_suppression_blocks_tautology_not_common_cause():
    """Скрытая тавтология Y=X/Z: контроль Z=inbed РАСКРЫВАЕТ near-determinism (частная≫сырой) → блок.
    Честная общая причина (X,Y от общего Z, но НЕ Y=X/Z): контроль Z ГАСИТ связь (частная→0) → НЕ блок.
    Это и есть различение суппрессии и настоящего конфаундера. synthetic_controls автора — оракул.
    Break-back (Спирмен вместо Пирсона / потеря ratio>1 / потеря isnan) → соответствующая ветка краснеет."""
    import sys
    import pathlib
    import numpy as np
    sys.path.insert(0, str(pathlib.Path(G.__file__).resolve().parent / "methodology" / "validation_gate"))
    import synthetic_controls as sc

    # (1) Тавтология Y=X/Z (missing=False для детерминизма теста). Автор: частная ≈ 0.82.
    xt, yt, zt = sc.control_series("tautological", 42, missing=False)
    raw_t, par_t = G._partial_corr_pearson(xt, yt, zt)
    assert abs(par_t) >= 0.80 and abs(par_t) > abs(raw_t), \
        f"тавтология не распознана: сырой={raw_t:.3f} частная={par_t:.3f}"
    assert G._gate05_suppressed(raw_t, par_t) is True, "тавтология Y=X/Z обязана блокироваться"

    # (2) Общая ПРИЧИНА (не отношение): x=z+e, y=z+e2. Контроль Z убирает конфаундер → частная≈0.
    rng = np.random.default_rng(1)
    z = rng.normal(size=800)
    x = z + rng.normal(size=800)
    y = z + rng.normal(size=800)
    raw_c, par_c = G._partial_corr_pearson(x, y, z)
    assert abs(par_c) < 0.80, f"общая причина не должна давать near-determinism: частная={par_c:.3f}"
    assert G._gate05_suppressed(raw_c, par_c) is False, "честный конфаундер НЕ суппрессия — не блок"

    # (3) N/A: троек <30 → не блок (fail-open); порог — из gate0_5, не хардкод.
    assert G._gate05_suppressed(float("nan"), float("nan")) is False
    assert G._sf.GATE05_PARTIAL_MIN == 0.80, "порог Gate 0.5 не равен 0.80 (R²>0.64)"


# ── Held-out (SPEC §4, семантика A — ADR adr_heldout_train_confirm_split, 2026-08-19) ─────────

def test_heldout_discovery_is_train_only(monkeypatch):
    """Семантика A: discovery судит ТОЛЬКО train (date<=frozen_at). Пара, посаженная
    ИСКЛЮЧИТЕЛЬНО в пост-freeze хвост (80% ряда!), находкой НЕ становится — отбор её
    физически не видел. Break-back (снять усечение daily_train в gate_correlations) →
    полный ряд несёт сильную связь → пара проходит BY → тест краснеет.
    Заодно контракт: D пуста → heldout.status = nothing_to_judge (штатно, не поломка)."""
    rng = np.random.default_rng(17)
    n_train, n_tail = 120, 480
    n = n_train + n_tail
    days = pd.date_range("2022-01-01", periods=n, freq="D")
    a = rng.normal(size=n)
    b_signal = a * 0.8 + rng.normal(scale=0.6, size=n)
    b = np.where(np.arange(n) < n_train, rng.normal(size=n), b_signal)
    daily = pd.DataFrame({"date": days, "hrv": a, "sleep_deep": b})
    monkeypatch.setattr(G._sf, "FROZEN_AT", str(days[n_train - 1].date()))
    # train 120д < min_overlap_days → пара «не тестировалась» и тест прошёл бы вакуумно;
    # снимаем порог, чтобы судил именно разрез train/confirm.
    monkeypatch.setattr(G._sf, "MIN_OVERLAP_DAYS", 30)
    r, p = _sp(a, b)
    assert abs(r) > 0.4, "sanity: на ПОЛНОМ ряду связь сильная — иначе break-back не доказателен"
    corr = _mkcorr([("hrv", "sleep_deep", r, min(p, 1e-4), n)])
    cg, _, meta = G.gate_correlations(daily, _EMPTY_LABS, corr, _EMPTY_LAB)
    row = cg.iloc[0]
    assert bool(row.gate_pass) is False, \
        "связь только в хвосте не должна стать находкой: discovery обязан судить train"
    assert meta["heldout"]["status"] == "nothing_to_judge", \
        "D пуста → суд confirm штатно не состоялся (контракт, урок exit 3)"
    assert meta["heldout"]["window_days"] >= 400, "окно confirm обязано быть видно числом"


def test_heldout_confirm_verdicts(monkeypatch):
    """Confirm-стадия: посаженный УСТОЙЧИВЫЙ сигнал (train+хвост) — passed; сигнал,
    умерший после freeze, — insufficient (не хороним с одного тихого окна, SPEC
    «адаптивно»); ЗНАЧИМО перевернувший знак — failed. Окно 60д >= пола 42д (Э1a).
    Каждая ветка — отдельным утверждением; суд виден числом (§14). Break-back
    (confirm судит полный ряд, а не хвост) → у dead-пары p станет значимым с тем же
    знаком → insufficient превратится в passed → тест краснеет."""
    rng = np.random.default_rng(23)
    n_train, n_tail = 600, 60
    n = n_train + n_tail
    days = pd.date_range("2022-01-01", periods=n, freq="D")
    is_tail = np.arange(n) >= n_train
    a = rng.normal(size=n)
    stable = a * 0.8 + rng.normal(scale=0.5, size=n)
    c = rng.normal(size=n)
    dead = np.where(is_tail, rng.normal(size=n), c * 0.8 + rng.normal(scale=0.5, size=n))
    e = rng.normal(size=n)
    flip = np.where(is_tail, -e * 0.9 + rng.normal(scale=0.3, size=n),
                    e * 0.8 + rng.normal(scale=0.5, size=n))
    # Имена — вне DERIVED_KNOWN (readiness/sleep_score — композиты, выпали бы из семьи)
    # и вне STRUCTURAL_PAIRS (total×deep/rem, steps×active_kcal).
    daily = pd.DataFrame({"date": days, "hrv": a, "sleep_deep": stable,
                          "resting_hr": c, "spo2_avg": dead,
                          "steps": e, "sleep_rem": flip})
    monkeypatch.setattr(G._sf, "FROZEN_AT", str(days[n_train - 1].date()))
    rows = []
    for x, y, na, nb in ((a, stable, "hrv", "sleep_deep"),
                         (c, dead, "resting_hr", "spo2_avg"),
                         (e, flip, "steps", "sleep_rem")):
        r, p = _sp(x, y)
        rows.append((na, nb, r, min(p, 1e-4), n))
    corr = _mkcorr(rows)
    cg, _, meta = G.gate_correlations(daily, _EMPTY_LABS, corr, _EMPTY_LAB)
    assert bool(cg["gate_pass"].all()), "sanity: все три пары обязаны пройти discovery на train"
    ho = meta["heldout"]
    assert ho["status"] == "judged" and ho["window_days"] == n_tail
    assert ho["verdicts"]["hrv×sleep_deep"]["verdict"] == "passed", \
        "устойчивый сигнал обязан пережить confirm"
    assert ho["verdicts"]["resting_hr×spo2_avg"]["verdict"] == "insufficient", \
        "умерший в хвосте — insufficient, не похороны с одного окна"
    assert ho["verdicts"]["steps×sleep_rem"]["verdict"] == "failed", \
        "значимый обратный знак — сильная улика, failed"
    assert ho["counts"] == {"passed": 1, "failed": 1, "insufficient": 1}
    # Пол окна: хвост короче пола валидности нуля → суд не выносится вовсе.
    monkeypatch.setattr(G._sf, "HELDOUT_FLOOR_WEEKS", 10)   # 70д > 60д хвоста
    _, _, meta2 = G.gate_correlations(daily, _EMPTY_LABS, corr, _EMPTY_LAB)
    assert meta2["heldout"]["status"] == "insufficient_window", \
        "хвост короче пола → insufficient_window, а не тихий суд на невалидном нуле"
