"""Характеризация синтетических контролей: что генератор РЕАЛЬНО производит.

Зелёный прогон здесь означает «ряды имеют заявленные свойства», а не «спецификация
автора верна». Два из трёх контролей расходятся с его ожиданиями, и расхождение
зафиксировано ЗДЕСЬ намеренно: придёт поправка — тест покраснеет, и это будет сигналом,
а не неожиданностью.

Мутации, на которых каждый тест обязан покраснеть, названы в теле.
"""
from __future__ import annotations

import numpy as np
import pytest

pytestmark = [pytest.mark.unit, pytest.mark.owner_data]   # спецификация — данные установки (приватная зона)

from methodology.validation_gate import synthetic_controls as sc

REPS = 15


def _sp(a, b):
    from scipy import stats
    m = ~(np.isnan(a) | np.isnan(b))
    return float(stats.spearmanr(a[m], b[m]).statistic)


def _mean(kind, fn, missing=True):
    return float(np.mean([fn(*sc.control_series(kind, 42 + i, missing=missing)[:2])
                          for i in range(REPS)]))


def test_positive_has_the_declared_strength():
    """Мутация: ALPHA → 0.3 — связь станет вдвое слабее заявленной."""
    r = _mean("positive", _sp)
    assert 0.28 < r < 0.42, f"Positive Control обязан нести связь ~0.35, получено {r:+.3f}"


def test_missingness_yields_the_target_effective_size():
    """Мутация: MISS_FRAC → 0.0 — N_eff вырастет втрое, и вся калибровка мощности поедет.

    Целевое N_eff ≈ 300 — ГЛАВНОЕ требование автора (его §3-3): ради него поднят φ.
    """
    obs = []
    for i in range(REPS):
        x, _y, _z = sc.control_series("positive", 42 + i)
        obs.append(int((~np.isnan(x)).sum()))
    n_obs = float(np.mean(obs))
    q = sc.PHI * sc.PHI
    n_eff = n_obs * (1 - q) / (1 + q)
    assert 1450 < n_obs < 1720, f"наблюдений после пропусков {n_obs:.0f}, спецификация ждёт ~1585"
    assert 260 < n_eff < 345, f"N_eff {n_eff:.0f}, спецификация ждёт ~300"


def test_negative_is_one_strong_period_among_empty_ones():
    """РЕДАКЦИЯ 2: связь сосредоточена в одном из заданных периодов.

    Первая редакция спецификации задавала сдвиг среднего, и этот тест фиксировал
    расхождение с автором (внутри периода получался ноль). Автор согласился с замером и
    сменил конструкцию на общий фактор — тест переписан ВСЛЕД за поправкой, как и было
    объявлено. Прежний вариант остался в истории писем, а не в коде.

    Предмет: профиль по периодам обязан отличить «связь в одном периоде» от «связь
    везде», при том что общий r у обоих случаев может совпадать.

    Мутация: применить фактор ко всему ряду, а не к срезу периода — остальные периоды
    перестанут быть пустыми, и контроль потеряет смысл.
    """
    from scipy import stats
    sl = sc.period_index(sc.INJECT_PERIOD)
    other = sc.period_index(6)          # длинный период вне инъекции (245 наблюдений)
    inside, outside, overall, overall_p = [], [], [], []
    for i in range(REPS):
        x, y, _ = sc.control_series("negative", 42 + i, missing=False)
        inside.append(_sp(x[sl], y[sl]))
        outside.append(_sp(x[other], y[other]))
        overall.append(_sp(x, y))
        overall_p.append(float(stats.pearsonr(x, y).statistic))
    r_in, r_out, r_all = (float(np.mean(v)) for v in (inside, outside, overall))
    assert 0.50 < r_in < 0.68, f"в периоде инъекции ждём ~0.60, получено {r_in:+.3f}"
    assert abs(r_out) < 0.12, f"вне периода инъекции связи быть не должно, получено {r_out:+.3f}"
    assert r_in - abs(r_out) > 0.35, (
        "смысл контроля — один сильный период на фоне пустых; если разрыв исчез, "
        "профиль по периодам ложную находку не отличит")
    # Общий r сверяется с автором ПИРСОНОМ (его 0.44), а гейту достаётся СПИРМЕН (0.378).
    # Разрыв 0.07 — не дефект: внутри периода инъекции дисперсия в 2.5 раза выше, ранги
    # считаются глобально, и смесь режимов бьёт по ранговой корреляции сильнее.
    # Мутация: сверить его 0.44 Спирменом — получишь несуществующее расхождение.
    assert 0.40 < float(np.mean(overall_p)) < 0.49, (
        f"Пирсон общий {np.mean(overall_p):+.3f}, спецификация автора ждёт ~0.44")
    assert 0.33 < r_all < 0.43, (
        f"Спирмен общий {r_all:+.3f} — это то, что увидит гейт; ниже пирсонова 0.44 и "
        "должен быть ниже")


def test_tautological_shows_suppression():
    """РЕДАКЦИЯ 2: частная корреляция обязана ПРЕВЫСИТЬ порог блокировки 0.80.

    В первой редакции клетка с ожидаемой частной корреляцией в документе автора
    обрывалась, а его же σ_noise = 0.05 давал 0.653 — контроль, обязанный блокироваться
    детектором подавления, им не блокировался. Автор выбрал уменьшить шум до 0.03 и
    отказался опускать границу: 0.80 выражает «почти детерминистическая связь»
    (R² > 0.64), и 0.65 размыл бы её до сильной физиологической.

    Здесь проверяется КОНТРОЛЬ, а не порог гейта: если контроль перестанет достигать
    0.80, калибровать детектор подавления станет не на чем.

    Мутация: вернуть σ_noise = 0.05 — частная упадёт до ~0.65 и тест покраснеет.
    """
    from scipy import stats
    raw, part = [], []
    for i in range(REPS):
        x, y, z = sc.control_series("tautological", 42 + i, missing=False)
        raw.append(_sp(x, y))
        rx = x - np.polyval(np.polyfit(z, x, 1), z)
        ry = y - np.polyval(np.polyfit(z, y, 1), z)
        part.append(float(stats.pearsonr(rx, ry).statistic))
    r_raw, r_part = float(np.mean(raw)), float(np.mean(part))
    assert r_part > r_raw, f"подавления нет: частная {r_part:+.3f} не выше сырой {r_raw:+.3f}"
    assert r_part > 0.80, (
        f"частная {r_part:+.3f} не превышает порог блокировки 0.80 — тавтологический "
        "контроль детектором подавления не блокируется, и калибровать его не на чем")
    assert r_part / abs(r_raw) > 1.5, (
        f"отношение частная/сырая {r_part / abs(r_raw):.2f} ниже порога автора 1.5 — "
        "контроль не сработал бы как тавтологический даже по второму условию")


def test_same_seed_same_series():
    """Мутация: завести rng внутри _ar1 — воспроизводимость исчезнет, и «1000 прогонов
    с seed 42+i» перестанет быть проверяемым утверждением."""
    a1, b1, _ = sc.control_series("positive", 42)
    a2, b2, _ = sc.control_series("positive", 42)
    assert np.allclose(a1, a2, equal_nan=True) and np.allclose(b1, b2, equal_nan=True)
    a3, _, _ = sc.control_series("positive", 43)
    assert not np.allclose(a1, a3, equal_nan=True), "разные зёрна обязаны давать разные ряды"


def test_spec_guard_blocks_silent_drift(monkeypatch, tmp_path):
    """Мутация: снять проверку spec_date в _periods — подменённая спецификация станет тихой."""
    bad = tmp_path / "synthetic_spec.yaml"
    bad.write_text('spec_date: "2026-01-01"\nperiods: [100, 200]\n', encoding="utf-8")
    monkeypatch.setattr(sc, "SPEC_PATH", bad)
    sc._periods.cache_clear()
    try:
        with pytest.raises(ValueError, match="спецификация синтетики разошлась"):
            sc.control_series("positive", 42)
    finally:
        monkeypatch.undo()
        sc._periods.cache_clear()


def test_unknown_control_is_refused():
    with pytest.raises(ValueError, match="неизвестный контроль"):
        sc.control_series("suppression", 42)
