"""Статистический гейт для longitudinal-корреляций перед тем как они кормят конституции.

Зачем гейт устроен так и какие развилки были: docs/explanation/correlation_gate.md
(оттуда же — жизненный цикл сигнала: docs/explanation/signal_validation_lifecycle.md).
Причинное уточнение A-рычагов (_causal_verdict, _fast_residual, causal_label):
docs/explanation/causal_refinement.md.

Честный метод (валидирован 2026-06-22, см. methodology/signal_validation_lifecycle_SPEC.md):
  daily<->daily — masked циркулярно-сдвиговый permutation-null БЕЗ импутации
                  (в каждое сравнение только реально измеренные дни) + BY-FDR
                  + фильтр derived (R^2 от остальных >= .90 ИЛИ known Oura-composite)
                  + порог покрытия.
  lab<->daily   — линейный детренд + КАЛЕНДАРНЫЙ циркулярный сдвиг + BY-FDR + порог n.
                  Оценивается ТОЛЬКО при labs.scope=active (2026-07-26): пока путь descoped,
                  семья возвращает not_evaluated и в веру не попадает ничем.
  A (эпохи) / q_lag (направленные лаги) — отдельные confirmatory-семьи, owner-only.

FDR — Benjamini-Yekutieli на ОБОИХ путях: daily с 2026-07-13, lab с 2026-07-21. Этот докстринг
до 2026-07-26 говорил «BH» — доки пережили флип метода (аудит validation_gate, P2-04).

Главный вход: gate_correlations(daily_df, labs_df, corr_all, lab_corr). Публичны также
человеческие метки вердиктов: family_label, causal_label, lag_label (их читают конституции
и gp_context — единый источник формулировки, не пересказ у каждого читателя).
Возвращает (corr_all2, lab_corr2, meta) — те же df с добавленными колонками
[coverage, p_perm, derived, gate_pass] (daily) / [r_detrended, p_perm, gate_pass] (lab)
и meta-словарём со счётчиками. Ничего не пишет, не зависит от БД.
"""
# INTENT: validation_gate — замысел и открытые решения: docs/explanation/validation_gate.md
from __future__ import annotations

from datetime import timedelta

import numpy as np
import pandas as pd
from scipy.stats import rankdata, spearmanr

import lab_canon              # канон имён аналитов: ОДИН канонизатор у producer и у гейта (P2-01)
import signal_family as _sf   # замороженная семья + параметры (§12.3): methodology/validation_gate/signal_family.yaml

KNOWN_DERIVED = _sf.DERIVED_KNOWN              # Oura composite-формулы + под-индексы readiness (R^2 их не всегда ловит)
R2_DERIVED_THRESH = _sf.R2_DERIVED_THRESH
LAB_N_MIN = _sf.LAB_N_MIN
PANEL_MIN_DAYS = _sf.PANEL_MIN_DAYS            # D1: минимум дней данных для панели/derived (из yaml, не литерал)
LAB_WINDOW_DAYS = _sf.LAB_WINDOW_DAYS          # mirror longitudinal_analysis.lab_metric_correlations
FDR_Q = _sf.FDR_Q
GUARD = _sf.GUARD
B_PERM = _sf.B_PERM
C_STAR_STRAT = _sf.C_STAR_STRATIFIED          # семья A: τ̂/L порог валидности эпохи
B_STRAT = _sf.B_PERM_STRATIFIED               # семья A: MC-пул strat_hi
EPOCHS = _sf.EPOCHS                            # frozen терап. эпохи (changepoints)
BASELINE_ACF = _sf.BASELINE_ACF               # frozen φ1 per метрика (baseline, не full-grid)
CAUSAL_ALPHA = _sf.CAUSAL_ALPHA               # причинное уточнение (Группа 2): clarity-cut суб-теста
CAUSAL_B_PERM = _sf.CAUSAL_B_PERM             # MC-пул причинных суб-тестов
CAUSAL_RESID_WINDOW = _sf.CAUSAL_RESID_WINDOW # ±дней локального среднего (быстрый остаток)
CAUSAL_LAG_DAYS = _sf.CAUSAL_LAG_DAYS         # смещение суб-теста специфичности (±lag)
CAUSAL_ASYM_RATIO = _sf.CAUSAL_ASYM_RATIO     # |r| ведущего лага / обратного для вердикта 'рычаг'
FDR_Q_LAG = _sf.FDR_Q_LAG                     # направленная лаг-семья q_lag (BY-уровень)
LAG_PUBLISHED = _sf.LAGGED.get("status") == "gated_q_lag"   # иначе q_lag считается, но в веру не едет
LAGGED = _sf.LAGGED                           # predictors/targets/lags направленной семьи


def _bh_threshold(pvals: np.ndarray, q: float) -> float:
    """Step-up порог: наибольший p, проходящий p<=q*rank/m. BH при q=FDR_Q;
    BY при q=FDR_Q/H_m (см. _H) — тот же контроллер, что fdr_harness.bh_reject/by_reject."""
    m = len(pvals)
    if m == 0:
        return 0.0
    order = np.argsort(pvals)
    thr = 0.0
    for rank, idx in enumerate(order, 1):
        if pvals[idx] <= q * rank / m:
            thr = float(pvals[idx])
    return thr


def _H(m: int) -> float:
    """Гармоническое число H_m = Σ_{i=1..m} 1/i (точная сумма, НЕ ln-приближение).
    Множитель Benjamini-Yekutieli: BY-порог = BH при q→q/H_m (контроль FDR под зависимостью)."""
    return float(np.sum(1.0 / np.arange(1, m + 1))) if m > 0 else 0.0


def _mc_se(p: float, B: int) -> float:
    """MC-ошибка перестановочного p̂=(1+cnt)/(B+1): биномиальная √(p̂(1−p̂)/B)."""
    p = min(max(float(p), 0.0), 1.0)
    return float(np.sqrt(p * (1.0 - p) / B)) if B > 0 else 0.0


def _mc_gap_family(pass_rows, q: float, m: int, B: int, k_star: int) -> dict:
    """Детектор «находки на MC-зазоре» — триггер (а) Фаз 1/3-а (PLAN_remaining Шаг 3).

    pass_rows: [(label, p̂), ...] — ТОЛЬКО прошедшие гейт пары семьи (направление риска:
    ложное ОТКРЫТИЕ на шуме Монте-Карло; почти-прошедшие остаются вне веры — консервативно).
    Флаг: |p̂ − L| < 2·MCSE(p̂,B), где L = (q/H_m)·k*/m — ОПЕРАЦИОННАЯ BY-линия step-up
    (k* = число BY-отклонений), НЕ возврат _bh_threshold. Тот возвращает max проходящий p̂:
    для граничной пары |p̂−thr|≡0 по построению; при совпадающих максимальных p̂
    так помечались бы все такие пары независимо от запаса до линии отбора.
    «открытие» к шуму МК ⟺ p̂ в пределах 2·MCSE от линии, которая его впустила.

    ЧИСТОЕ ЧТЕНИЕ готовых p̂/q/m/k*: ни одного обращения к rng — замороженный нуль не тронут.
    Возвращает {"evaluated", "passed", "B", "m", "k_star", "line", "flagged"}; пустой
    flagged ≠ отсутствие ключа (канон empty-not-absent)."""
    out = {"evaluated": True, "passed": len(pass_rows), "B": int(B), "m": int(m),
           "k_star": int(k_star), "line": None, "flagged": []}
    if not pass_rows or m <= 0 or k_star <= 0:
        return out
    line = (q / _H(m)) * k_star / m
    out["line"] = round(float(line), 8)
    for label, p in pass_rows:
        if p is None or (isinstance(p, float) and np.isnan(p)):
            continue
        se = _mc_se(p, B)
        if abs(float(p) - line) < 2.0 * se:
            out["flagged"].append({"pair": label, "p": round(float(p), 8),
                                   "line": round(float(line), 8), "mcse": round(se, 8)})
    return out


def _non_structural(df: pd.DataFrame) -> pd.DataFrame:
    """Строки без структурных пар (STRUCTURAL_PAIRS: часть-целое + арифметика прибора), 28.09.
    Такие пары в веру не доходят (режутся на рендере), значит их близость к линии отбора ничего
    не решает — MC-зазор по ним был бы ложным вызовом владельца."""
    if df is None or len(df) == 0 or "metric_a" not in df.columns:
        return df
    keep = [frozenset((a, b)) not in _sf.STRUCTURAL_PAIRS
            for a, b in zip(df["metric_a"], df["metric_b"])]
    return df[keep]


MCGAP_TRIGGER_DOC = "BACKLOG.md#BL-MCGAP-FLIP-1"


def mc_resolution_fragile(families: dict) -> list:
    """Триггер BL-MCGAP-FLIP-1 (решение владельца 23.09): семья ПРОПУСКАЕТ пары, а её пол
    перестановочного p̂ = 1/(B+1) выше шага BY ранга 1 = q/(H_m·m). Тогда ни одна пара не может
    пройти одна — только «в компании» соседей на том же полу, и решение держится на разрешении
    Монте-Карло, которое датчик MC-зазора (_mc_gap_family) не видит: он смотрит на расстояние
    пары до линии, а не на то, что вся группа стоит на шумовом полу.

    Живой прецедент: до 02.08 семья D держала 8 пар — все на полу 1/1001 при шаге ~0.0005
    (m=44). Чистое чтение готового артефакта mc_gap: ни БД, ни rng. Один дом предиката —
    его зовут ночной check_mc_gap и еженедельная задача-триггер владельца.
    Возврат: список строк-причин; пустой = триггер не сработал."""
    out = []
    for fam, d in (families or {}).items():
        if not isinstance(d, dict):
            continue
        passed, B, m = int(d.get("passed") or 0), int(d.get("B") or 0), int(d.get("m") or 0)
        if d.get("exact"):
            continue    # v12: точный перебор — Монте-Карло нет, шумового пола нет
        if passed <= 0 or B <= 0 or m <= 0:
            continue
        q = FDR_Q_LAG if fam == "q_lag" else FDR_Q
        floor, step = 1.0 / (B + 1), q / (_H(m) * m)
        if floor > step:
            out.append(f"{fam}: прошло {passed} при B={B} — пол p̂={floor:.2e} выше шага BY "
                       f"ранга 1 ({step:.2e}, m={m}): пары проходят только группой на шумовом полу")
    return out


def _grank(series: pd.Series) -> np.ndarray:
    """Глобальные центрированные ранги; пропуски остаются NaN (НЕ импутируются)."""
    v = series.to_numpy(dtype=float)
    m = ~np.isnan(v)
    r = np.full(len(v), np.nan)
    if m.sum() == 0:
        return r
    r[m] = rankdata(v[m])
    r[m] = r[m] - r[m].mean()
    return r


def _masked_corr(ra: np.ndarray, rb: np.ndarray):
    """Pearson по дням, где оба не-NaN (Spearman на глоб. рангах). None если мало."""
    mm = ~np.isnan(ra) & ~np.isnan(rb)
    if mm.sum() < _sf.PAIR_MIN_OVERLAP:
        return None
    a = ra[mm]
    b = rb[mm]
    a = a - a.mean()
    b = b - b.mean()
    d = np.sqrt((a * a).sum() * (b * b).sum())
    return float((a * b).sum() / d) if d > 0 else 0.0


def _pair_perm_p(ra: np.ndarray, rb: np.ndarray, r_obs: float, rng, b_perm: "int | None") -> float:
    """v10 (1): перестановочный p пары — циркулярный сдвиг ВНУТРИ общего окна пары (первый..
    последний общий день), p = (1+cnt)/(1+годных) по сдвигам с общими днями ≥ PAIR_MIN_OVERLAP —
    та же форма, что в лаб-пути и strat. Старый расчёт сдвигал по всей сетке и считал негодный
    сдвиг «не превысил» → у пар, покрывающих часть сетки, p занижался. Нет годных → NaN."""
    joint = np.flatnonzero(~np.isnan(ra) & ~np.isnan(rb))
    if joint.size == 0:
        return np.nan
    lo, hi = int(joint[0]), int(joint[-1]) + 1
    a, bw = ra[lo:hi], rb[lo:hi]
    L = hi - lo
    if L - GUARD <= GUARD:
        return np.nan
    cnt = tot = 0
    # v12 (27.09, решение владельца): b_perm=None — ТОЧНЫЙ перебор всех годных сдвигов, без
    # Монте-Карло. p детерминирован; пол = 1/(1+годных), а не 1/(B+1). rng не расходуется.
    shifts = range(GUARD, L - GUARD) if b_perm is None else rng.integers(GUARD, L - GUARD, size=b_perm)
    for k in shifts:
        rc = _masked_corr(a, np.roll(bw, int(k)))
        if rc is not None:
            tot += 1
            if abs(rc) >= abs(r_obs):
                cnt += 1
    return (1 + cnt) / (1 + tot) if tot else np.nan


def _derived_metrics(daily_df: pd.DataFrame, metrics: list) -> set:
    """Метрика 'derived' если предсказывается из остальных с R^2>=порога (same-day)."""
    derived = set(KNOWN_DERIVED) & set(metrics)
    for j, m in enumerate(metrics):
        cols = [x for k, x in enumerate(metrics) if k != j]
        if not cols:
            continue
        sub = daily_df[[m] + cols].dropna()
        if len(sub) < PANEL_MIN_DAYS:
            continue
        y = sub[m].to_numpy(float)
        X = np.column_stack([np.ones(len(sub)), sub[cols].to_numpy(float)])
        beta, _, _, _ = np.linalg.lstsq(X, y, rcond=None)
        ss_res = float(((y - X @ beta) ** 2).sum())
        ss_tot = float(((y - y.mean()) ** 2).sum())
        if ss_tot > 0 and (1 - ss_res / ss_tot) >= R2_DERIVED_THRESH:
            derived.add(m)
    return derived


def _epoch_profile(ra: np.ndarray, rb: np.ndarray, epi: list, tau: float, c_star: float) -> list:
    """Связь ВНУТРИ каждой допущенной эпохи: [(индекс эпохи, overlap, r), ...].

    Общая корреляция длинных рядов может отражать общий дрейф между периодами,
    а не связь внутри каждого периода. Ошибочно заполненные нулями пропуски
    тоже способны создавать совместное движение независимых показателей.
    Структурный гейт такие пары не исключит: нужен разрез по периодам.

    Допуск эпохи ТОТ ЖЕ, что у strat_hi (`_strat_pvalue`): L≥16 · ярусный гейт τ̂/L≤c*
    · overlap≥12. Второй набор правил допуска означал бы, что «период» в системе значит
    два разных, и профиль расходился бы со стратифицированной семьёй молча.

    Ранги ГЛОБАЛЬНЫЕ, центрирование внутри эпохи — конвенция канона статистика, не выбор
    автора: локальный Спирмен был бы вторым статистиком рядом с существующим.
    """
    out = []
    tau_r = float(np.ceil(tau))
    for ei, idx in enumerate(epi):
        L = len(idx)
        if L < _sf.EPOCH_MIN_LEN or (tau_r / L if L > 0 else np.inf) > c_star:
            continue
        a, b = ra[idx], rb[idx]
        m0 = ~np.isnan(a) & ~np.isnan(b)
        if m0.sum() < _sf.EPOCH_MIN_OVERLAP:
            continue
        x = a[m0] - a[m0].mean()
        y = b[m0] - b[m0].mean()
        den = float(np.sqrt(float((x * x).sum()) * float((y * y).sum())))
        if den <= 0:
            continue
        out.append((ei, int(m0.sum()), float((x * y).sum()) / den))
    return out


def _epoch_summary(prof: list) -> tuple:
    """Сводка профиля: (число эпох, медиана r, ближайшая к нулю, доля согласного знака).

    Слабейшая берётся по модулю, а не как арифметический минимум: вопрос «держится ли
    связь везде», и отвечает на него самая слабая эпоха, а не самая отрицательная.

    Доля согласного знака (2026-08-04, достройка Gate 2 до правила автора «устойчивость
    ЗНАКА и величины по периодам»): доля допущенных эпох, знак r которых совпадает со
    знаком медианы профиля. Формулы у автора НЕТ — определение наше, названо в limits
    инварианта `profile_threshold_calibrated_offline`, а не выдано за цитату. Правила:
    r = 0 не несёт знака и считается НЕсовпадением; медиана = 0 даёт долю 0 — щедрость
    в нуле завышала бы устойчивость. Якорь — медиана самого профиля, а не общий r:
    общий r живёт вне профиля, и якорить на него значило бы завести в сводке второй
    источник (на живом каноне знак медианы совпадает со знаком большинства эпох).
    ЧИСЛА, а не вердикт: пороги двухкомпонентного правила калибруются на синтетике.
    """
    rs = [r for _, _, r in prof]
    if not rs:
        return 0, np.nan, np.nan, np.nan
    med = float(np.median(rs))
    agree = sum(1 for r in rs if r != 0.0 and (r > 0) == (med > 0)) if med != 0.0 else 0
    return (len(rs), round(med, 3), round(min(rs, key=abs), 3),
            round(agree / len(rs), 3))


def _gate2_blocked(r_epoch_median, r_epoch_sign_share):
    """Gate 2 «Period Profile» блок (ВКЛЮЧЁН 2026-08-08, решение владельца «Global»): профиль ЕСТЬ
    И (|медиана r| < r_min ИЛИ доля согласного знака < sign_min). Нет профиля (median NaN — нет
    эпох / тенант) → НЕ блок (Gate 2 неприменим, fail-open). Пороги — из gate2_profile (signal_family),
    не хардкод. Векторный (принимает Series/массив/скаляр).

    Оракул — tests/unit/test_correlation_gate.py::test_gate2_blocks_weak_and_unstable (величина/знак/
    NaN — каждая ветка краснеет)."""
    m = np.asarray(r_epoch_median, dtype=float)
    s = np.asarray(r_epoch_sign_share, dtype=float)
    # Сравниваем МОДУЛЬ медианы: знак отрицательной связи сам по себе
    # не означает слабую связь. Проверка медианы со знаком отвергала бы её.
    with np.errstate(invalid="ignore"):
        return (~np.isnan(m)) & ((np.abs(m) < _sf.GATE2_R_MIN) | (s < _sf.GATE2_SIGN_MIN))


def _partial_corr_pearson(x, y, z):
    """Частная корреляция Пирсона (X,Y | Z) по ПОЛНЫМ тройкам. Возврат (сырая, частная).
    <30 троек / нулевая дисперсия / вырожденный знаменатель → частная = NaN. Пирсон (не ранги):
    порог Gate 0.5 обоснован через R² (доля объяснённой дисперсии), а это линейная корреляция."""
    x = np.asarray(x, dtype=float); y = np.asarray(y, dtype=float); z = np.asarray(z, dtype=float)
    m = ~(np.isnan(x) | np.isnan(y) | np.isnan(z))
    if int(m.sum()) < 30:
        return np.nan, np.nan
    x, y, z = x[m], y[m], z[m]

    def _r(a, b):
        if a.std() == 0 or b.std() == 0:
            return np.nan
        return float(np.corrcoef(a, b)[0, 1])

    rxy, rxz, ryz = _r(x, y), _r(x, z), _r(y, z)
    if np.isnan(rxy) or np.isnan(rxz) or np.isnan(ryz):
        return rxy, np.nan
    den = np.sqrt((1.0 - rxz ** 2) * (1.0 - ryz ** 2))
    if den <= 0:
        return rxy, np.nan
    return rxy, (rxy - rxz * ryz) / den


def _gate05_suppressed(raw, partial):
    """Gate 0.5 «Suppression»: скрытая тавтология Y=X/Z. Блок ⟺ near-deterministic ПОСЛЕ контроля Z
    (|частная|≥partial_min) И контроль Z РАСКРЫЛ связь (|частная|>|сырой|, ratio>1). Оба вместе:
    ratio>1 отличает суппрессию от честной сильной пары (у той частная ≈ сырая). NaN → не блок."""
    if raw is None or partial is None or np.isnan(raw) or np.isnan(partial):
        return False
    return bool(abs(partial) >= _sf.GATE05_PARTIAL_MIN and abs(partial) > abs(raw))


def _gate_daily(daily_df: pd.DataFrame, corr_all: pd.DataFrame, seed: int,
                control_z: "pd.Series | None" = None) -> pd.DataFrame:
    df = corr_all.copy()
    if df.empty:
        for c in ("coverage", "overlap_days", "p_perm", "r_train", "r_epoch_median", "r_epoch_weakest",
                  "r_epoch_sign_share"):
            df[c] = pd.Series(dtype=float)
        df["epochs_n"] = pd.Series(dtype=int)
        for c in ("derived", "structural", "gate2_fail", "gate05_fail", "gate_pass"):
            df[c] = pd.Series(dtype=bool)
        return df
    grid = daily_df.sort_values("date").set_index("date").asfreq("D")
    # Панель для derived-детекта и рангов — ВСЕ числовые метрики с достаточным покрытием,
    # а не только попавшие в пары corr_all: иначе composite (sleep_score) не отловится,
    # если в текущем наборе пар нет всех его входов.
    metrics = [c for c in grid.columns
               if pd.api.types.is_numeric_dtype(grid[c]) and grid[c].notna().sum() >= PANEL_MIN_DAYS]
    derived = _derived_metrics(grid[metrics], metrics)
    R = {m: _grank(grid[m]) for m in metrics}
    cover = {m: float(grid[m].notna().mean()) for m in metrics}
    rng = np.random.default_rng(seed)

    # Профиль по эпохам (Gate 2 «Period Profile» — словарь methodology/validation_gate/
    # gates.yaml; до 2026-08-01 мы звали его Gate 3) как ИЗМЕРЕНИЕ: разрез по тем же периодам,
    # на которых работает стратифицированная семья A. Ничего не блокирует — отдаёт числа.
    epi = _epoch_indices(grid.index)

    p_perm, coverage, is_derived, g05, overlap = [], [], [], [], []
    r_train = []   # r гейта на train (held-out A): то же r_obs, что судят перестановки, — в колонку
    ep_n, ep_med, ep_weak, ep_sign = [], [], [], []
    # Gate 0.5 контроль Z=время в постели (sleep_inbed), выровнен на сетку grid; None → гейт N/A.
    z_arr = (control_z.reindex(grid.index).to_numpy(dtype=float) if control_z is not None else None)
    for _, r in df.iterrows():
        a, b = r["metric_a"], r["metric_b"]
        coverage.append(round(min(cover.get(a, 0.0), cover.get(b, 0.0)), 3))
        overlap.append(0)
        is_derived.append(bool(a in derived or b in derived))
        # Gate 0.5: частная Пирсон-корр X,Y|Z по СЫРЫМ значениям grid (не рангам). N/A → False.
        if z_arr is not None and a in grid.columns and b in grid.columns:
            _raw05, _par05 = _partial_corr_pearson(
                grid[a].to_numpy(dtype=float), grid[b].to_numpy(dtype=float), z_arr)
            g05.append(_gate05_suppressed(_raw05, _par05))
        else:
            g05.append(False)
        if a in R and b in R:
            _n, _med, _weak, _sign = _epoch_summary(
                _epoch_profile(R[a], R[b], epi, _tau_pair(a, b), C_STAR_STRAT))
        else:
            _n, _med, _weak, _sign = 0, np.nan, np.nan, np.nan
        ep_n.append(_n)
        ep_med.append(_med)
        ep_weak.append(_weak)
        ep_sign.append(_sign)
        # R2 (§6/R2 вердикта): permutation-p для ВСЕЙ замороженной семьи (все жизнеспособные
        # пары), а НЕ только прошедших сырой скрин p<0.05. Иначе BH делит на data-screened
        # подмножество m<|семья| → порог q·rank/m завышен → невалидный (анти-консервативный) FDR.
        # Полнота семьи заморожена в signal_family.yaml (viable ≥30 overlap). Скрин по significant убран.
        if a not in R or b not in R:
            p_perm.append(np.nan)
            r_train.append(np.nan)
            continue
        _ov = int((~np.isnan(R[a]) & ~np.isnan(R[b])).sum())
        overlap[-1] = _ov
        r_obs = _masked_corr(R[a], R[b])
        if r_obs is None:
            p_perm.append(np.nan)
            r_train.append(np.nan)
            continue
        r_train.append(float(r_obs))
        # v10 (2): меньше года общих дней — «не тестировалась» (до BY, в m не входит). Правило
        # про доступность данных, не про исход: r считаем и показываем, p — нет.
        if _ov < _sf.MIN_OVERLAP_DAYS:
            p_perm.append(np.nan)
            continue
        p_perm.append(_pair_perm_p(R[a], R[b], r_obs, rng, None if _sf.D_PERM_EXACT else B_PERM))

    df["coverage"] = coverage
    df["overlap_days"] = overlap
    df["p_perm"] = p_perm
    df["r_train"] = r_train
    df["derived"] = is_derived
    # Gate 0 на уровне ПАРЫ, два реестра одного потребителя (STRUCTURAL_PAIRS): часть-целое
    # (deep/rem⊂total) + алгоритмическая производная одного конвейера (steps×active_kcal, решение
    # владельца 2026-08-13 — kcal считается трекером из движения, связь — свойство арифметики
    # устройства). _derived_metrics (метрика R²-предсказуема) их НЕ ловит: sleep_light не метрика,
    # а R² steps→kcal заметно ниже порога. Порядко-независимо (frozenset). Транзитивность НЕ применяем.
    df["structural"] = [frozenset((a, b)) in _sf.STRUCTURAL_PAIRS
                        for a, b in zip(df["metric_a"], df["metric_b"])]
    df["gate05_fail"] = g05
    df["epochs_n"] = ep_n
    df["r_epoch_median"] = ep_med
    df["r_epoch_weakest"] = ep_weak
    df["r_epoch_sign_share"] = ep_sign
    tested = df["p_perm"].notna()
    # Ф3 (2026-07-13): FDR-семья = ТОЛЬКО non-derived пары (вердикт §6: композит×вход вне семьи —
    # раньше derived сидели в знаменателе). И Benjamini-Yekutieli вместо BH: BY = step-up при
    # q → q/H_m (контроль FDR под зависимостью, вердикт R3/§4). На кластере даёт те же открытия,
    # что BH (заземлено 2026-07-13: 8 пар), но с гарантией под зависимостью и слепотой к одиночкам.
    fam = tested & (~df["derived"])
    m_fam = int(fam.sum())
    q_by = FDR_Q / _H(m_fam) if m_fam > 0 else FDR_Q
    thr = _bh_threshold(df.loc[fam, "p_perm"].to_numpy(), q_by) if m_fam > 0 else 0.0
    # Gate 0 «часть-целое» (структурный блок рендера, 2026-08-06): пара компонент⊂целое — НЕ находка.
    # Режем ЗДЕСЬ (после FDR), а НЕ в fam: семья и m остаются РОВНО как в проде → порог реальных пар не
    # сдвигается, метод-эпоха карантина (signal_family.VERSION) не churn-ится. Тот же слой «post-BY», что
    # coverage-фильтр. build_ai_summary рендерит по gate_pass → структурная пара не доедет до LLM.
    # Gate 2 «Period Profile» (ВКЛЮЧЁН как блок 2026-08-08, решение владельца Global): |медианы r| по
    # эпохам ≥ порога И доля согласного знака ≥ порога (gate2_profile в signal_family.yaml, точка zero-
    # false). Пост-BY фильтр, как выше. N/A когда профиля нет (median=NaN — нет эпох/тенант) → НЕ блокирует
    # (Gate 2 неприменим, fail-open). Порог 0/0 (снимок без gate2_profile) → _g2_fail пуст → выключен.
    df["gate2_fail"] = _gate2_blocked(df["r_epoch_median"], df["r_epoch_sign_share"])
    # v10: coverage-фильтр (доля всей сетки) снят — его роль (прикрывать заниженный p у частично
    # покрытых пар) закрыта самим p (_pair_perm_p), доступность — min_overlap_days до BY.
    df["gate_pass"] = (fam & (df["p_perm"] <= thr)
                       & (~df["structural"]) & (~df["gate2_fail"]) & (~df["gate05_fail"]))
    df.attrs["fdr_thr"] = thr
    df.attrs["fdr_method"] = "BY"
    df.attrs["m_family"] = m_fam
    return df


def _detrend(vals: np.ndarray, t: np.ndarray) -> np.ndarray:
    t = t.astype(float)
    if np.ptp(t) == 0:
        return vals - vals.mean()
    A = np.polyfit(t, vals, 1)
    return vals - (A[0] * t + A[1])


def _gate_labs(daily_df: pd.DataFrame, labs_df: pd.DataFrame,
               lab_corr: pd.DataFrame, seed: int,
               window_days: int = LAB_WINDOW_DAYS) -> pd.DataFrame:
    df = lab_corr.copy()
    if df.empty:
        df["r_detrended"] = pd.Series(dtype=float)
        df["p_perm"] = pd.Series(dtype=float)
        df["gate_pass"] = pd.Series(dtype=bool)
        return df
    # Матчинг ПО КАНОНУ — тот же канонизатор, что у producer'а (P2-01 аудита 2026-07-26).
    # Было: `labs_df["test_name"] == lab_name` буквально, при том что producer объединяет
    # варианты через lab_canon.normalize. Producer и гейт видели разное число наблюдений одного аналита; сырой
    # `spearman_r` в вере считался на одном наборе, а `p_perm`/`r_detrended` — на другом.
    # Это не только потеря мощности: одна строка веры несла числа из двух разных выборок.
    _canon_col = (labs_df["test_name"].map(lab_canon.normalize)
                  if "test_name" in labs_df.columns else pd.Series(dtype=object))
    daily_idx = daily_df.set_index("date").sort_index()
    metric_cols = [c for c in daily_idx.columns if c in set(df["metric"])]
    # Плотный календарь для НУЛЯ (fdr-online 2026-07-21): полная перестановка пар НЕВАЛИДНА под
    # автокоррелированные лаб-маркеры и плотный daily (обмениваемости нет, дисперсия занижена →
    # анти-консерв, Romano–Tirlea; спека null_gate_spec §6.1). Замена = КАЛЕНДАРНЫЙ ЦИРКУЛЯРНЫЙ
    # СДВИГ плотного daily при фиксированном лабе (§6.2.1): сохраняет автокорреляцию ряда, ломает
    # лишь выравнивание лаб↔daily. Точный перебор конечной группы → p_min = 1/(G_lab+1). Измерено
    # на каноне 2026-07-21: HGB↔sleep_deep p 0.005 (старый перемеш) → 0.104 (валидный сдвиг), ~21×.
    grid = daily_idx.asfreq("D")
    cal_idx = {d: i for i, d in enumerate(grid.index)}
    N_grid = len(grid.index)
    arrs = {_m: grid[_m].to_numpy(dtype=float) for _m in metric_cols}

    def _winmean(a, i):
        lo = max(0, i - window_days)
        hi = min(N_grid, i + window_days + 1)
        w = a[lo:hi]
        w = w[~np.isnan(w)]
        return w.mean() if len(w) >= 2 else np.nan

    cache = {}
    _obs_n: dict[str, int] = {}   # аналит → сколько наблюдений увидел ГЕЙТ (паритет с producer)

    def lab_frame(lab_name: str) -> pd.DataFrame:
        if lab_name in cache:
            return cache[lab_name]
        lv = (labs_df[_canon_col == lab_canon.normalize(lab_name)]
              .dropna(subset=["value"]).sort_values("date"))
        _obs_n[lab_name] = int(len(lv))   # провенанс: сколько наблюдений вошло в гейт
        rows = []
        for _, lr in lv.iterrows():
            d = lr["date"]
            w = daily_idx.loc[(daily_idx.index >= d - timedelta(days=window_days)) &
                              (daily_idx.index <= d + timedelta(days=window_days))]
            if len(w) < 2:
                continue
            agg = {"lab_value": lr["value"], "lab_date": d}
            for m in metric_cols:
                if m in w.columns:
                    agg[m] = w[m].mean()
            rows.append(agg)
        cache[lab_name] = pd.DataFrame(rows)
        return cache[lab_name]

    r_dt_col, p_perm = [], []
    for _, r in df.iterrows():
        lab, m = r["lab"], r["metric"]
        dfl = lab_frame(lab)
        if dfl.empty or m not in dfl.columns or m not in arrs:
            r_dt_col.append(np.nan)
            p_perm.append(np.nan)
            continue
        pair = dfl[["lab_value", m, "lab_date"]].dropna()
        if len(pair) < LAB_N_MIN:
            r_dt_col.append(np.nan)
            p_perm.append(np.nan)
            continue
        to = (pd.to_datetime(pair["lab_date"]).astype("int64") // 10**9 // 86400).to_numpy().astype(float)
        lidx = pair["lab_date"].map(cal_idx).to_numpy()
        lval = pair["lab_value"].to_numpy(float)
        arr_m = arrs[m]

        def _stat(a):
            yy = np.array([_winmean(a, int(i)) for i in lidx])
            mm = ~np.isnan(yy)
            if mm.sum() < LAB_N_MIN:
                return np.nan
            xr = _detrend(lval[mm], to[mm])
            yr = _detrend(yy[mm], to[mm])
            rr, _ = spearmanr(xr, yr)
            return float(rr) if rr == rr else np.nan

        r_obs = _stat(arr_m)
        if r_obs != r_obs:
            r_dt_col.append(np.nan)
            p_perm.append(np.nan)
            continue
        # точный перебор допустимой группы календарных циркулярных сдвигов плотного daily
        cnt = tot = 0
        for k in range(GUARD, N_grid - GUARD):
            rr = _stat(np.roll(arr_m, k))
            if rr == rr:
                tot += 1
                if abs(rr) >= abs(r_obs):
                    cnt += 1
        r_dt_col.append(round(r_obs, 3))
        p_perm.append((1 + cnt) / (1 + tot) if tot > 0 else np.nan)

    df["r_detrended"] = r_dt_col
    df["p_perm"] = p_perm
    tested = df["p_perm"].notna()
    # BY (не BH): PRDS для двусторонних не доказан (спека null_gate_spec §6.3, контрпример 2026)
    # → q → q/H_m (FDR-контроль под зависимостью). Флип BH→BY 2026-07-21 (решение владельца после
    # re-gate); сторож test_daily_and_lab_by_source_guard обновлён. Согласовано с daily-путём (Ф3).
    m_lab = int(tested.sum())
    q_by = FDR_Q / _H(m_lab) if m_lab > 0 else FDR_Q
    thr = _bh_threshold(df.loc[tested, "p_perm"].to_numpy(), q_by) if m_lab > 0 else 0.0
    df["gate_pass"] = tested & (df["p_perm"] <= thr)
    df.attrs["fdr_thr"] = thr
    df.attrs["fdr_method"] = "BY"
    df.attrs["lab_null"] = "calendar_circular_shift"
    df.attrs["lab_obs_n"] = dict(_obs_n)   # провенанс выборки: сверяется с producer'ом
    return df


def _lab_obs_counts(labs_df: pd.DataFrame, names) -> dict:
    """Сколько наблюдений видит ГЕЙТ по каждому аналиту — по канону, без всякой статистики.

    Считается и при выключенном лаб-пути (ревью R3, VG-R3-06): иначе провенанс выборки
    существует только в режиме `active`, и паритет producer↔гейт нельзя измерить, не включив
    путь. Оракул обязан работать на том режиме, в котором система живёт."""
    if "test_name" not in getattr(labs_df, "columns", []):
        return {}
    # VG-R4-06: NULL-значения ОТБРАСЫВАЮТСЯ — ровно как у producer (`dropna(subset=["value"])`
    # в lab_metric_correlations). Раньше гейт считал строки, а producer — наблюдения; аналит с
    # одним измеренным и одним пустым значением давал lab_obs_n=2 против producer_obs_n=1. Оба
    # числа ехали в веру как «сколько данных за этим выводом», и они были про разные множества.
    # Паритет — не косметика: это единственный числовой контроль того, что канонизация имени
    # у писателя и у гейта сошлась.
    _df = labs_df.dropna(subset=["value"]) if "value" in labs_df.columns else labs_df
    canon = _df["test_name"].map(lab_canon.normalize)
    out = {}
    for name in names:
        out[name] = int((canon == lab_canon.normalize(name)).sum())
    return out


def _labs_not_evaluated(lab_corr: pd.DataFrame, labs_df: "pd.DataFrame | None" = None) -> pd.DataFrame:
    """Лаб-путь вне release-scope → семья НЕ оценивается (а не «оценивается и не проходит»).

    Разница принципиальная. Пустая маска означала бы «посчитали, ничего не прошло» — и первая
    же пара с малым p попала бы в веру до человеческого ре-объявления (аудит P1-04). Здесь пары
    остаются в кадре (Excel и глаз аналитика их видят), но `gate_pass` заведомо False и
    `p_perm` не считается вовсе: публиковать нечего, потому что не оценивали.
    """
    df = lab_corr.copy()
    df["r_detrended"] = np.nan
    df["p_perm"] = np.nan
    df["gate_pass"] = False
    df.attrs["fdr_thr"] = None
    df.attrs["fdr_method"] = "not_evaluated"
    df.attrs["lab_scope"] = _sf.LABS_SCOPE
    # Провенанс выборки считается и здесь: паритет producer↔гейт должен быть измерим в том
    # режиме, в котором система работает, а не только при временно включённом scope (VG-R3-06).
    if labs_df is not None:
        df.attrs["lab_obs_n"] = _lab_obs_counts(labs_df, _sf.LAB_METRICS)
    return df


# ─────────────────────────── СЕМЬЯ A: стратифицированный confirmatory-гейт ───────────────────────────
# КАНОН = strat_hi (docs/explanation/null_gate_appendix_code.md §2): равный вес СЫРЫХ
# per-эпоха центрированных кросс-произведений глобальных рангов, БЕЗ 1/Var.
# Нормировка и inv-var меняют вклад эпох; доли вклада и p зависят от входного ряда,
# а не являются универсальными свойствами выбранной схемы.
# Эта функция НЕ подключена к конституциям — проводка A-рычага = фаза C (инвариант A-до-демоута-D).


def _epoch_indices(dates: pd.DatetimeIndex) -> list:
    """Индексы дней по frozen терап.эпохам (data_manifest). Открытый конец → до последней даты."""
    epi = []
    for s0, s1 in EPOCHS:
        s = pd.Timestamp(s0)
        e = pd.Timestamp(s1) if s1 else dates.max()
        epi.append(np.where((dates >= s) & (dates <= e))[0])
    return epi


def _tau_pair(metric_a: str, metric_b: str) -> float:
    """τ̂_pair (дни) из frozen baseline-ACF по формуле Бартлетта AR(1): q̂=φx·φy, τ̂=-1/ln q̂ (§3.2.4).
    Оценка по baseline-окну (не по короткой эпохе: смещение Юла-Уокера занизило бы τ̂). q̂<=0 → 0 (нет
    инертности → гейт эпох не бьёт). Округление ВВЕРХ (консервативно) делает вызывающий."""
    px = BASELINE_ACF.get(metric_a, 0.0)
    py = BASELINE_ACF.get(metric_b, 0.0)
    q = px * py
    if q <= 0.0:
        return 0.0
    return -1.0 / np.log(q)


def _strat_pvalue(ra: np.ndarray, rb: np.ndarray, epi: list, tau: float,
                  c_star: float, B: int, rng) -> tuple:
    """strat_hi + ярусный гейт s_e=τ̂/L_e + шовная поправка. Возвращает (p, baseline_share, n_epochs).

    Пул СЫРЫХ центрированных кросс-произведений глобальных рангов, равный вес эпох.
    Гейт (§3.2.3, Таблица 3.1): эпоха с s_e>c_star исключается ДО пула; при
    0.05<s_e<=c_star — шовная поправка ×sqrt((L-τ̂)/(L-2τ̂)) на per-эпохную нуль-таблицу (obs не трогаем).
    A-priori пропуск: L_e<EPOCH_MIN_LEN или overlap<EPOCH_MIN_OVERLAP (signal_family). p = MC-пул сумм таблиц, пол 1/(B+1)."""
    tau_r = float(np.ceil(tau))            # округление вверх (§3.2.4)
    obs = 0.0
    tabs = []
    contribs = []                          # (epoch_index, |raw cross-product sum|) для baseline_share
    for ei, idx in enumerate(epi):
        L = len(idx)
        if L < _sf.EPOCH_MIN_LEN:          # a-priori пропуск короткой эпохи (A4: signal_family)
            continue
        s_e = tau_r / L if L > 0 else np.inf
        if s_e > c_star:                   # ярус III / серая зона исключены (governance П10)
            continue
        a = ra[idx]
        b = rb[idx]
        m0 = ~np.isnan(a) & ~np.isnan(b)
        if m0.sum() < _sf.EPOCH_MIN_OVERLAP:   # overlap floor (A4: signal_family)
            continue
        ec = float(((a[m0] - a[m0].mean()) * (b[m0] - b[m0].mean())).sum())
        seam = 1.0                         # шовная поправка только в ярусе 0.05<s_e<=c_star
        if s_e > 0.05:
            denom = L - 2.0 * tau_r
            if denom > 0:
                seam = float(np.sqrt((L - tau_r) / denom))
        vals = []
        for k in range(GUARD, max(GUARD + 1, L - GUARD)):
            bb = np.roll(b, k)
            mk = ~np.isnan(a) & ~np.isnan(bb)
            if mk.sum() < _sf.EPOCH_MIN_OVERLAP:   # тот же пол, что у наблюдённой суммы (A4)
                continue
            vals.append(float(((a[mk] - a[mk].mean()) * (bb[mk] - bb[mk].mean())).sum()))
        if not vals:
            continue
        obs += ec
        tabs.append(seam * np.asarray(vals))
        contribs.append((ei, abs(ec)))
    if not tabs:
        return np.nan, np.nan, 0
    draws = np.zeros(B)
    for t in tabs:
        draws += t[rng.integers(0, len(t), size=B)]
    cnt = int(np.sum(np.abs(draws) >= abs(obs)))
    tot = sum(c for _, c in contribs)
    base = next((c for ei, c in contribs if ei == 0), 0.0)
    baseline_share = 100.0 * base / tot if tot > 0 else float("nan")
    return (1 + cnt) / (1 + B), baseline_share, len(tabs)


# ─────────────── Направленный лаг-класс (Группа 2 ч.2 fdr-online) — DORMANT, идёт валидация ───────────────
# predictor@T → target@T+lag как первый класс гипотез (сейчас status=pending_harness_validation в
# signal_family: наивный lagged_correlations считается для отчёта, В ВЕРУ НЕ идёт). Здесь — КАНОН статистика
# (strat_hi на направленной паре), чтобы twin гонял РЕАЛЬНЫЙ прод-статистик, не суррогат (ловушка
# exp_family_a_mechanism). НЕ подключено к gate_correlations — валидируем нуль twin'ом (twin_lag_directed.py),
# промоут+бюджет m = отдельный шаг по решению владельца. Лаг ВНУТРИ эпохи (не течём через границу: последние lag
# дней эпохи → NaN), иначе общий η-уровень соседней эпохи протёк бы в направленный тест.


def _lag_within_epochs(arr: np.ndarray, epi: list, lag: int) -> np.ndarray:
    """Выровнять target[t+lag] на позицию t ВНУТРИ каждой эпохи (эпохи непрерывны в календаре asfreq-D).
    Последние lag дней эпохи → NaN: направленная пара не пересекает границу режима."""
    out = np.full(len(arr), np.nan)
    for idx in epi:
        if len(idx) <= lag:
            continue
        out[idx[:-lag]] = arr[idx[lag:]]
    return out


def _strat_pvalue_lagged(ra, rb, epi, tau, c_star, B, rng, lag) -> tuple:
    """strat_hi на направленной паре ra@T ↔ rb@T+lag (target сдвинут внутри эпохи). Тот же нуль
    (per-эпоха циркул. сдвиг), что contemporaneous семьи A — один канон. Возвращает (p, baseline_share, n)."""
    rb_lag = _lag_within_epochs(rb, epi, lag)
    return _strat_pvalue(ra, rb_lag, epi, tau, c_star, B, rng)


# ─────────────── Причинное уточнение A-рычагов (Группа 2 fdr-online) — ОПИСАТЕЛЬНОЕ ───────────────
# Поверх A-рычага (устойчивая связь ВНУТРИ режима) отвечает на вопрос, который A НЕ решает: это
# управляемый рычаг или совпадение той же ночи? A снимает ЭПОХОВЫЙ конфаунд (ступени режима), но НЕ
# со-причину той же ночи (спокойный вечер поднял и сон, и HRV — внутри эпохи A проходит, дёргать не за что)
# и не смотрит предшествование во времени. Суб-тесты на БЫСТРОМ остатке (снят DOW + локальный ±w дрейф →
# осталась суточная флуктуация): исчезла совсем → жила в медленном фоне → совпадение; НАПРАВЛЕННАЯ лаг-
# асимметрия (предшествование T→T+lag) → похоже на рычаг; только одновременная связь → рычаг от со-причины
# той же ночи не отделить на N=1 → честно не_знаю. НЕ error-rate (вне бюджета q_A/q_D): clarity-cut =
# CAUSAL_ALPHA (пол неопределённости владельца); 'рычаг' — строгая планка (асимметрия). Пробник 07-22. Оракул — владелец.


def _shift_nan(a: np.ndarray, k: int) -> np.ndarray:
    """Сдвиг на k дней с NaN-заполнением (без циркулярного заворота — не смешиваем концы рядов)."""
    if k == 0:
        return a.copy()
    out = np.full(len(a), np.nan)
    if k > 0:
        out[k:] = a[:-k]
    else:
        out[:k] = a[-k:]
    return out


def _fast_residual(arr: np.ndarray, epi: list, dow: np.ndarray, window: int) -> np.ndarray:
    """Быстрый остаток ВНУТРИ каждой эпохи: снять DOW-среднее + локальное ±window среднее (медленный
    дрейф/недельный ритм) → остаётся суточная флуктуация. Дни вне валидных эпох / без оценки → NaN.
    Агрессивно (может занулить мощность) — это безопасная сторона: нет мощности → вердикт «не_знаю»."""
    out = np.full(len(arr), np.nan)
    for idx in epi:
        if len(idx) < _sf.EPOCH_MIN_LEN:          # тот же пропуск короткой эпохи, что у гейта (A4)
            continue
        a = arr[idx].astype(float)
        d = dow[idx]
        obs = ~np.isnan(a)
        if obs.sum() < 8:
            continue
        a2 = a.copy()
        for wd in range(7):                       # снять среднее по дню недели (в пределах эпохи)
            sel = obs & (d == wd)
            if sel.sum() >= 2:
                a2[sel] = a[sel] - a[sel].mean()
            else:
                a2[sel] = np.nan                  # <2 наблюдений на DOW — не оценить → NaN (консервативно)
        res = np.full(len(idx), np.nan)
        for i in range(len(idx)):                 # снять локальный ±window дрейф на DOW-очищенном
            if np.isnan(a2[i]):
                continue
            lo = max(0, i - window)
            hi = min(len(idx), i + window + 1)
            w = a2[lo:hi]
            w = w[~np.isnan(w)]
            if len(w) >= 3:
                res[i] = a2[i] - w.mean()
        out[idx] = res
    return out


def _causal_verdict(ra, rb, epi, dow, tau, alpha, c_star, B, rng, lag) -> tuple:
    """Вердикт причинного уточнения A-рычага: 'рычаг' | 'совпадение' | 'не_знаю' + провенанс.

    На БЫСТРОМ остатке (медленный/недельный общий фон снят) три исхода — и планка для рискованного
    вывода честно высокая (N=1, Oracle-Problem):
      • исчезает совсем (ни одновременно, ни со сдвигом) → жила в медленном фоне → 'совпадение';
      • НАПРАВЛЕННАЯ асимметрия (значим лаг в одну сторону, не в другую) → есть предшествование во
        времени → 'рычаг' (единственная наблюдательная подпись управляемости на N=1);
      • только ОДНОВРЕМЕННАЯ / симметричная связь → рычаг от со-причины той же ночи (спокойный вечер
        поднял и сон, и HRV) НЕ отделить без вмешательства → честно 'не_знаю'.
    Асимметрия потерь: 'рычаг' требует направленной асимметрии (строгая планка, ложный рычаг дорог);
    'совпадение' — пустоты всех суб-тестов; всё спорное падает в 'не_знаю'. clarity-cut = alpha."""
    ura = _fast_residual(ra, epi, dow, CAUSAL_RESID_WINDOW)
    urb = _fast_residual(rb, epi, dow, CAUSAL_RESID_WINDOW)
    p0, _, ne0 = _strat_pvalue(ura, urb, epi, tau, c_star, B, rng)
    prov = {"p0": None if p0 != p0 else round(float(p0), 5), "n_epochs": int(ne0)}
    if ne0 == 0 or p0 != p0:
        prov["why"] = "нет валидных эпох на быстром остатке (мощности нет)"
        return "не_знаю", prov
    # ±lag: значимость направленного сдвига (permutation) + ВЕЛИЧИНА эффекта (детерминир.). Направление
    # решаем по величине |r|, НЕ по p: у MC-пола p значим в обе стороны и «ровно один значим» — хрупко.
    b_fwd = _shift_nan(urb, -lag)                 # a[t] ↔ b[t+lag]: a ведёт b
    b_rev = _shift_nan(urb, +lag)                 # a[t] ↔ b[t-lag]: b ведёт a
    p_fwd, _, _ = _strat_pvalue(ura, b_fwd, epi, tau, c_star, B, rng)
    p_rev, _, _ = _strat_pvalue(ura, b_rev, epi, tau, c_star, B, rng)
    r0 = _masked_corr(ura, urb)
    rf = _masked_corr(ura, b_fwd)
    rr = _masked_corr(ura, b_rev)
    a0, af, ar = (abs(r0) if r0 is not None else 0.0,
                  abs(rf) if rf is not None else 0.0,
                  abs(rr) if rr is not None else 0.0)
    prov["p_fwd"] = None if p_fwd != p_fwd else round(float(p_fwd), 5)
    prov["p_rev"] = None if p_rev != p_rev else round(float(p_rev), 5)
    prov["r0"], prov["r_fwd"], prov["r_rev"] = round(a0, 3), round(af, 3), round(ar, 3)
    sig0 = p0 <= alpha
    sigf = (p_fwd == p_fwd) and (p_fwd <= alpha)
    sigr = (p_rev == p_rev) and (p_rev <= alpha)
    if not sig0 and not sigf and not sigr:
        prov["why"] = "исчезает на быстром остатке → медленный общий фон"
        return "совпадение", prov
    # 'рычаг' — направленная асимметрия ПО ВЕЛИЧИНЕ: ведущий лаг сильнее обратного в CAUSAL_ASYM_RATIO раз,
    # сильнее одновременного и значим. Иначе (одновременная/симметричная) рычаг от со-причины не отделить.
    if sigf and af > a0 and af >= CAUSAL_ASYM_RATIO * ar:
        prov["why"] = "предшествование a→b (ведущий лаг сильнее обратного) → похоже на рычаг"
        return "рычаг", prov
    if sigr and ar > a0 and ar >= CAUSAL_ASYM_RATIO * af:
        prov["why"] = "предшествование b→a (ведущий лаг сильнее обратного) → похоже на рычаг"
        return "рычаг", prov
    prov["why"] = "только одновременная/симметричная связь — рычаг от со-ночной причины не отделить"
    return "не_знаю", prov


def _gate_daily_stratified(daily_df: pd.DataFrame, corr_all: pd.DataFrame, seed: int) -> pd.DataFrame:
    """Семья A: каждую non-derived пару → A-рычаг | D-only через strat_hi + BY на удержанной семье.

    Добавляет колонки [p_strat, baseline_share, strat_epochs, derived, a_lever, verdict_family,
    verdict_causal, causal_p0]. Ничего не пишет. Дискриминатор двух семей: пара с высокой η но
    нулевой внутриэпоховой связью → p_strat НЕ значим (в отличие от _gate_daily=D). verdict_causal
    (Группа 2) — ОПИСАТЕЛЬНОЕ уточнение ТОЛЬКО A-рычагов (рычаг|совпадение|не_знаю), вне бюджета."""
    df = corr_all.copy()
    if df.empty:
        for c in ("p_strat", "baseline_share", "causal_p0"):
            df[c] = pd.Series(dtype=float)
        df["strat_epochs"] = pd.Series(dtype=int)
        for c in ("derived", "a_lever"):
            df[c] = pd.Series(dtype=bool)
        for c in ("verdict_family", "verdict_causal"):
            df[c] = pd.Series(dtype=object)
        return df
    grid = daily_df.sort_values("date").set_index("date").asfreq("D")
    metrics = [c for c in grid.columns
               if pd.api.types.is_numeric_dtype(grid[c]) and grid[c].notna().sum() >= PANEL_MIN_DAYS]
    derived = _derived_metrics(grid[metrics], metrics)
    R = {m: _grank(grid[m]) for m in metrics}
    epi = _epoch_indices(grid.index)
    rng = np.random.default_rng(seed)

    p_strat, bshare, neps, is_derived = [], [], [], []
    for _, r in df.iterrows():
        a, b = r["metric_a"], r["metric_b"]
        der = bool(a in derived or b in derived)
        is_derived.append(der)
        if der or a not in R or b not in R:
            p_strat.append(np.nan)
            bshare.append(np.nan)
            neps.append(0)
            continue
        tau = _tau_pair(a, b)
        p, bs, ne = _strat_pvalue(R[a], R[b], epi, tau, C_STAR_STRAT, B_STRAT, rng)
        p_strat.append(p)
        bshare.append(bs)
        neps.append(ne)

    df["p_strat"] = p_strat
    df["baseline_share"] = bshare
    df["strat_epochs"] = neps
    df["derived"] = is_derived
    # FDR-семья A = non-derived пары с валидным p_strat; отбор BY (не сырой p<0.05), как в _gate_daily.
    tested = df["p_strat"].notna()
    fam = tested & (~df["derived"])
    m_fam = int(fam.sum())
    q_by = FDR_Q / _H(m_fam) if m_fam > 0 else FDR_Q
    thr = _bh_threshold(df.loc[fam, "p_strat"].to_numpy(), q_by) if m_fam > 0 else 0.0
    df["a_lever"] = fam & (df["p_strat"] <= thr)
    df["verdict_family"] = np.where(df["a_lever"], "A-lever",
                                    np.where(fam, "D-only", ""))
    # ── Причинное уточнение (Группа 2): рычаг|совпадение|не_знаю ТОЛЬКО поверх A-рычагов ──
    # ОПИСАТЕЛЬНОЕ (вне бюджета q_A/q_D). Отдельный поток rng (seed+1), чтобы не сдвинуть strat-нуль.
    dow = np.asarray(grid.index.dayofweek)
    crng = np.random.default_rng(seed + 1)
    a_lever_arr = df["a_lever"].to_numpy()
    vcausal, cp0 = [], []
    for pos, (_, r) in enumerate(df.iterrows()):
        if not bool(a_lever_arr[pos]):
            vcausal.append("")
            cp0.append(np.nan)
            continue
        a, b = r["metric_a"], r["metric_b"]
        vc, prov = _causal_verdict(R[a], R[b], epi, dow, _tau_pair(a, b),
                                   CAUSAL_ALPHA, C_STAR_STRAT, CAUSAL_B_PERM, crng, CAUSAL_LAG_DAYS)
        vcausal.append(vc)
        _p0 = prov.get("p0")
        cp0.append(np.nan if _p0 is None else float(_p0))
    df["verdict_causal"] = vcausal
    df["causal_p0"] = cp0
    df.attrs["strat_thr"] = thr
    df.attrs["strat_method"] = "strat_hi(raw,equal-epoch)+BY"
    df.attrs["m_family"] = m_fam
    df.attrs["b_strat"] = B_STRAT
    df.attrs["c_star"] = C_STAR_STRAT
    return df


def _gate_lagged(daily_df: pd.DataFrame, seed: int) -> pd.DataFrame:
    """Направленная лаг-семья q_lag: predictor@T → target@T+lag через strat_hi (лаг внутри эпохи) + BY.

    СВОЙ набор направленных пар из signal_family.LAGGED (predictors×targets×lags); self-пары и derived
    (композит×вход, §6/R1) исключены → m≈18. p = _strat_pvalue_lagged (нуль валидирован twin_lag_directed).
    Отбор BY на уровне FDR_Q_LAG (ОТДЕЛЬНАЯ семья, не смешана с A/D). owner-only (вызывающий под is_owner).
    Возвращает df [predictor, target, lag_days, p_lag, gate_pass_lag, verdict_lag]. Наивный lagged_correlations
    (spearman) сюда НЕ входит — он только в Excel-отчёте, вне веры."""
    preds = [m for m in LAGGED["predictors"] if m not in KNOWN_DERIVED]
    tgts = [m for m in LAGGED["targets"] if m not in KNOWN_DERIVED]
    lags = list(LAGGED["lags_days"])
    _empty = pd.DataFrame(columns=["predictor", "target", "lag_days", "p_lag", "gate_pass_lag", "verdict_lag"])
    grid = daily_df.sort_values("date").set_index("date").asfreq("D")
    metrics = [c for c in grid.columns
               if pd.api.types.is_numeric_dtype(grid[c]) and grid[c].notna().sum() >= PANEL_MIN_DAYS]
    R = {m: _grank(grid[m]) for m in metrics}
    epi = _epoch_indices(grid.index)
    rng = np.random.default_rng(seed)
    rows = []
    for lag in lags:
        for p in preds:
            for t in tgts:
                if p == t or p not in R or t not in R:
                    continue
                pv, _, _ne = _strat_pvalue_lagged(R[p], R[t], epi, _tau_pair(p, t),
                                                  C_STAR_STRAT, B_STRAT, rng, lag)
                rows.append({"predictor": p, "target": t, "lag_days": lag, "p_lag": pv})
    if not rows:
        return _empty
    df = pd.DataFrame(rows)
    tested = df["p_lag"].notna()
    m_fam = int(tested.sum())
    q_by = FDR_Q_LAG / _H(m_fam) if m_fam > 0 else FDR_Q_LAG
    thr = _bh_threshold(df.loc[tested, "p_lag"].to_numpy(), q_by) if m_fam > 0 else 0.0
    df["gate_pass_lag"] = tested & (df["p_lag"] <= thr)
    df["verdict_lag"] = np.where(df["gate_pass_lag"], "lag-lever", "")
    df.attrs["lag_thr"] = thr
    df.attrs["lag_m"] = m_fam
    return df


def _heldout_confirm(daily_df: pd.DataFrame, cg: pd.DataFrame,
                     frozen_at: "pd.Timestamp | None", seed: int) -> dict:
    """Held-out подтверждение (SPEC §4, семантика A —
    ADR docs/explanation/adr_heldout_train_confirm_split.md: почему train/confirm split,
    а не post-hoc; там же мощностная граница окна из симуляции Э1a).

    Пары, прошедшие все ворота на train (gate_pass), судятся на пост-freeze хвосте,
    которого discovery не видел: знак r обязан совпасть со знаком train, перестановочный
    p (циркулярный сдвиг, как у discovery) — пробить HELDOUT_CONFIRM_ALPHA.

    ИЗМЕРЕНИЕ, не блок (прецедент Gate 3 31.07): вердикты едут в gate_meta и веру,
    публикацию не трогают — блокировка отдельным решением владельца на живом материале.

    Контракт статусов (урок exit 3: «нечего судить» ≠ поломка ≠ провал):
      judged              — суд состоялся, вердикты по парам ниже;
      nothing_to_judge    — D пуста (штатно при пустой вере);
      insufficient_window — хвост короче пола валидности нуля (Э1a: 42д);
      no_frozen_at        — семья без frozen_at (старый снимок) — суда нет.
    Вердикты пары: passed (знак совпал И p<=alpha) · failed (ЗНАЧИМЫЙ обратный знак —
    сильная улика) · insufficient (не пробил — окно копит мощность, SPEC «адаптивно»;
    гистерезис-дух: не хоронить с одного тихого окна). Мощностная граница длиннее пола
    валидности (r=0.5/φ=0.5 → 56д, r<=0.3 → >84д) — insufficient месяцами ОЖИДАЕМ.
    Оракул: tests/unit/test_correlation_gate.py::test_heldout_* (посаженный устойчивый
    сигнал обязан пережить, посаженный train-only артефакт — не пережить)."""
    floor_days = int(_sf.HELDOUT_FLOOR_WEEKS * 7)
    out = {"frozen_at": (str(frozen_at.date()) if frozen_at is not None else None),
           "window_days": 0, "floor_days": floor_days,
           "alpha": _sf.HELDOUT_CONFIRM_ALPHA, "status": None, "verdicts": {}}
    if frozen_at is None:
        out["status"] = "no_frozen_at"
        return out
    tail = (daily_df[pd.to_datetime(daily_df["date"]) > frozen_at]
            if (not daily_df.empty and "date" in daily_df.columns) else daily_df.iloc[0:0])
    out["window_days"] = int(pd.to_datetime(tail["date"]).nunique()) if not tail.empty else 0
    d_rows = (cg[cg["gate_pass"].fillna(False).astype(bool)]
              if "gate_pass" in cg.columns else cg.iloc[0:0])
    if d_rows.empty:
        out["status"] = "nothing_to_judge"
        return out
    if out["window_days"] < floor_days:
        out["status"] = "insufficient_window"
        return out
    grid = tail.sort_values("date").set_index("date").asfreq("D")
    R = {m: _grank(grid[m]) for m in grid.columns if pd.api.types.is_numeric_dtype(grid[m])}
    rng = np.random.default_rng(seed + 20260814)  # свой поток: discovery-rng не сдвигается
    for row in d_rows.itertuples():
        a, b, key = row.metric_a, row.metric_b, f"{row.metric_a}×{row.metric_b}"
        r_conf = _masked_corr(R[a], R[b]) if (a in R and b in R) else None
        if r_conf is None:
            out["verdicts"][key] = {"verdict": "insufficient", "why": "overlap на окне мал"}
            continue
        # v10 (1): тот же несмещённый p, что у discovery (окно пары, только годные сдвиги).
        p_conf = _pair_perm_p(R[a], R[b], r_conf, rng, B_PERM)
        if p_conf != p_conf:
            out["verdicts"][key] = {"verdict": "insufficient", "why": "нет годных сдвигов на окне"}
            continue
        # Знак train: r_train — то самое r, которое судил discovery (не spearman_r полного ряда).
        r_tr = getattr(row, "r_train", None)
        sign_ok = (r_tr is not None and not pd.isna(r_tr) and np.sign(r_conf) == np.sign(r_tr))
        if p_conf <= _sf.HELDOUT_CONFIRM_ALPHA and sign_ok:
            verdict = "passed"
        elif p_conf <= _sf.HELDOUT_CONFIRM_ALPHA and not sign_ok:
            verdict = "failed"
        else:
            verdict = "insufficient"
        out["verdicts"][key] = {"verdict": verdict, "r_confirm": round(float(r_conf), 4),
                                "p_confirm": round(float(p_conf), 5),
                                "r_train": (None if r_tr is None or pd.isna(r_tr)
                                            else round(float(r_tr), 4))}
    out["status"] = "judged"
    # §14: суд виден числом, не только словарём.
    out["counts"] = {v: sum(1 for x in out["verdicts"].values() if x["verdict"] == v)
                     for v in ("passed", "failed", "insufficient")}
    return out


def gate_correlations(daily_df: pd.DataFrame, labs_df: pd.DataFrame,
                      corr_all: pd.DataFrame, lab_corr: pd.DataFrame, *,
                      seed: int = 0, lab_window_days: int = LAB_WINDOW_DAYS,
                      enable_stratified: bool = False, control_z: "pd.Series | None" = None):
    """Публичный вход. Возвращает (corr_all2, lab_corr2, meta).

    lab_window_days инжектится из longitudinal_analysis.LAB_WINDOW_DAYS (единый источник);
    дефолт нужен только для standalone/тестов. Консистентность парности с
    lab_metric_correlations проверяет tests/integration/test_longitudinal_gate.py.

    enable_stratified: OWNER-ONLY (общая конфигурация эпох не определяет эпохи
    другого тенанта). Вызывающий (_apply_gate) передаёт secrets_paths.is_owner().
    При True — доразметка КАЖДОЙ пары семьёй A/D (колонка verdict_family), СОСТАВ и gate_pass
    семьи D НЕ меняются. Эпохи не совпали → мусор, поэтому у тенанта строго False.
    """
    # Held-out семантика A (ADR adr_heldout_train_confirm_split, решение владельца 2026-08-19):
    # discovery судит ТОЛЬКО train (date <= frozen_at); пост-freeze хвост — confirm-окно,
    # которого отбор физически не видел. Разрез в ОДНОЙ точке — здесь, на границе правды;
    # вызывающие передают полный ряд. frozen_at пуст (старый снимок семьи) → разреза нет.
    # Радиус на живом каноне 2026-08-14 — ноль (Э1b: семьи поимённо идентичны, хвост 33д/4000).
    _frozen = pd.Timestamp(_sf.FROZEN_AT) if _sf.FROZEN_AT else None
    daily_train = daily_df
    if _frozen is not None and not daily_df.empty and "date" in daily_df.columns:
        daily_train = daily_df[pd.to_datetime(daily_df["date"]) <= _frozen]
    cg = _gate_daily(daily_train, corr_all, seed, control_z=control_z)
    # Scope лаб-пути — РЕЖИМ, а не запись в манифесте (P1-04): пока descoped, семья не
    # оценивается вовсе, и ни одна лаб-пара не может попасть в веру, каким бы малым ни был её p.
    # Train и здесь: лаб-discovery при реактивации не должен видеть confirm-хвост.
    lg = (_gate_labs(daily_train, labs_df, lab_corr, seed, lab_window_days)
          if _sf.LABS_ACTIVE else _labs_not_evaluated(lab_corr, labs_df))
    # Confirm-стадия held-out: суд пар D на пост-freeze хвосте. ИЗМЕРЕНИЕ, не блок
    # (прецедент Gate 3 31.07): вердикт едет в meta/веру, публикацию не трогает —
    # блокировка отдельным решением владельца на живом материале.
    _ho = _heldout_confirm(daily_df, cg, _frozen, seed)
    if enable_stratified:
        # Доразметка семьи A поверх D: колонки align по индексу (cg и sg оба из corr_all.copy()
        # → один индекс). attrs cg (fdr_thr/m_family) сохраняются — колоночное присваивание, не merge.
        sg = _gate_daily_stratified(daily_train, corr_all, seed)
        for _c in ("p_strat", "baseline_share", "strat_epochs", "a_lever",
                   "verdict_family", "verdict_causal", "causal_p0"):
            if _c in sg.columns:
                cg[_c] = sg[_c]
    # Направленная лаг-семья q_lag (owner-only): СВОЙ набор пар (не из corr_all=contemporaneous),
    # поэтому уезжает в meta для build_ai_summary, а не колонкой в cg. Наивный lagged_correlations вне.
    _lag_gated = []
    if enable_stratified:
        _lg = _gate_lagged(daily_train, seed)
        # Публикация q_lag в веру — ТОЛЬКО при status=gated_q_lag (решение владельца 22.09, BL-TWIN-BAND-1:
        # валидация нуля стояла на мягкой полосе → семья приостановлена до перепроверки). Измерение
        # (p_lag, mc_gap, pass-set) идёт как прежде: приостановлен вывод, не счёт.
        if LAG_PUBLISHED and "gate_pass_lag" in _lg.columns:
            for _, r in _lg[_lg["gate_pass_lag"]].iterrows():
                _lag_gated.append({"predictor": r["predictor"], "target": r["target"],
                                   "lag_days": int(r["lag_days"]),
                                   "p_lag": None if pd.isna(r["p_lag"]) else round(float(r["p_lag"]), 5),
                                   "verdict_lag": r["verdict_lag"]})
    # ── MC-зазор (Коммит3, триггер (а) Фаз 1/3-а): хрупкость открытий к разрешению МК ──
    # Чистый post-hoc по готовым p̂/attrs (rng не трогается — pass-set бит-в-бит прежний).
    def _pass_rows(df_, mask_col, pcol, fmt):
        if mask_col not in df_.columns or pcol not in df_.columns:
            return []
        sub = df_[df_[mask_col].fillna(False).astype(bool)]
        return [(fmt(r), float(getattr(r, pcol))) for r in sub.itertuples()]

    def _k_star(df_, pcol, thr, drop_derived):
        """Число BY-отклонений семьи (p̂ ≤ thr) — определяет операционную линию L.
        Может превышать len(pass_rows) (напр. coverage-фильтр у D режет поверх BY)."""
        if pcol not in df_.columns or thr is None:
            return 0
        fam_ = df_[pcol].notna()
        if drop_derived and "derived" in df_.columns:
            fam_ &= ~df_["derived"].astype(bool)
        return int((df_.loc[fam_, pcol] <= float(thr)).sum())

    def _pair_lbl(r):
        return f"{r.metric_a}×{r.metric_b}"

    _gap = {"D": _mc_gap_family(_pass_rows(cg, "gate_pass", "p_perm", _pair_lbl),
                                FDR_Q, int(cg.attrs.get("m_family", 0)), B_PERM,
                                _k_star(cg, "p_perm", cg.attrs.get("fdr_thr"), True)),
            # None = семья не считалась (enable_stratified выключен) ≠ пустой flagged
            "A": None, "q_lag": None}
    if _sf.D_PERM_EXACT:
        # v12: у D нет Монте-Карло — MC-зазор и шумовой пол к ней неприменимы. B=0 + явный флаг,
        # а не отсутствие ключа: «не MC» ≠ «не считали» (mc_resolution_fragile её пропускает).
        _gap["D"]["B"] = 0
        _gap["D"]["exact"] = True
        _gap["D"]["flagged"] = []
    if enable_stratified:
        # 28.09: структурные пары (арифметика прибора, часть-целое) в веру не доходят — их близость
        # к линии A не решает ничего, и MC-зазор по ним будил бы владельца впустую (hrv×recovery_high_min).
        _gap["A"] = _mc_gap_family(_pass_rows(_non_structural(sg), "a_lever", "p_strat", _pair_lbl),
                                   FDR_Q, int(sg.attrs.get("m_family", 0)), B_STRAT,
                                   _k_star(sg, "p_strat", sg.attrs.get("strat_thr"), True))
        _gap["q_lag"] = _mc_gap_family(
            _pass_rows(_lg, "gate_pass_lag", "p_lag",
                       lambda r: f"{r.predictor}→{r.target}+{int(r.lag_days)}д"),
            FDR_Q_LAG, int(_lg.attrs.get("lag_m", 0)), B_STRAT,
            _k_star(_lg, "p_lag", _lg.attrs.get("lag_thr"), False))

    _causal_counts = ({v: int((cg["verdict_causal"] == v).sum())
                       for v in ("рычаг", "совпадение", "не_знаю")}
                      if (enable_stratified and "verdict_causal" in cg.columns) else {})
    meta = {
        "gate_applied": True,
        "stratified": {"applied": bool(enable_stratified),
                       "a_levers": int(cg["a_lever"].sum()) if (enable_stratified and "a_lever" in cg.columns) else 0,
                       "causal": _causal_counts,
                       "lagged": _lag_gated, "lag_levers": len(_lag_gated),
                       # «0 лаг-рычагов» при приостановке ≠ «проверили, не нашли» — статус рядом с числом.
                       "lag_status": _sf.LAGGED.get("status")},
        "mc_gap": {"rule": "flag if |p−L| < 2·MCSE(p,B); L = (q/H_m)·k*/m — операционная BY-линия",
                   "families": _gap},
        "method": "masked_circular_perm+BY_FDR non-derived family (daily); linear_detrend+calendar_circular_shift+BY (lab)",
        "params": {"B": B_PERM, "d_perm": "exact" if _sf.D_PERM_EXACT else "mc", "fdr_q": FDR_Q, "guard": GUARD,
                   "min_overlap_days": _sf.MIN_OVERLAP_DAYS, "lab_n_min": LAB_N_MIN,
                   "frozen_at": _sf.FROZEN_AT, "frozen_at_declared": _sf.FROZEN_AT_DECLARED,
                   "r2_derived_thresh": R2_DERIVED_THRESH},
        "daily_pass": int(cg["gate_pass"].sum()) if "gate_pass" in cg else 0,
        "daily_tested": int(cg["p_perm"].notna().sum()) if "p_perm" in cg else 0,
        # Held-out confirm (семантика A): статус/окно/вердикты. Измерение, не блок.
        "heldout": _ho,
        # Сколько пар заблокировано Gate 0 «часть-целое» (§14: блок виден числом, не тишиной).
        "daily_structural": int(cg["structural"].sum()) if "structural" in cg else 0,
        # Сколько пар заблокировано Gate 2 (слабая величина/переворот знака по эпохам).
        "daily_gate2_blocked": int(cg["gate2_fail"].sum()) if "gate2_fail" in cg else 0,
        # Сколько пар заблокировано Gate 0.5 (скрытая тавтология Y=X/Z: суппрессия при контроле inbed).
        "daily_gate05_blocked": int(cg["gate05_fail"].sum()) if "gate05_fail" in cg else 0,
        # A5 (22.09): стоял ли Gate 0.5 вообще. Без этого «0 заблокировано» при control_z=None
        # (контроль не прочитан → гейт N/A) неотличимо от «гейт стоял и не нашёл тавтологий».
        "daily_gate05_applied": control_z is not None,
        # ЗНАМЕНАТЕЛЬ BY ≠ число посчитанных p: derived-пары считаются, но в семью не входят
        # (§6 вердикта). Без этого числа лог «8/65» читается как m=65 — 2026-07-25 я сам поднял
        # по нему ложную тревогу «невалидный FDR». Пишем оба, чтобы следующий читатель не повторил.
        "daily_family_m": int(cg.attrs.get("m_family", 0)),
        "lab_pass": int(lg["gate_pass"].sum()) if "gate_pass" in lg else 0,
        "lab_tested": int(lg["p_perm"].notna().sum()) if "p_perm" in lg else 0,
        # Провенанс лаб-пути: почему lab_tested=0 — «не оценивали» или «оценили, не прошло».
        # Без этого различия ноль читается одинаково в обоих случаях (класс «тишина=норма»).
        "lab_scope": _sf.LABS_SCOPE,
        "lab_status": "evaluated" if _sf.LABS_ACTIVE else "not_evaluated",
        "lab_obs_n": dict(lg.attrs.get("lab_obs_n", {})),
    }
    return cg, lg, meta


# ЕДИНЫЙ источник человеческой формулировки семьи A/D (читают generate_constitutions + gp_context;
# метка verdict_family кладётся ОДИН раз в build_ai_summary, слова — здесь). Честно: формулировка
# НЕ заявляет причинность/управляемость (решение владельца, Фаза C 2026-07-22) — «устойчивая связь», а
# не «потяни за это». Тривиальные пары (steps×active_kcal) помечаются так же честно, без хардкод-списка.
_FAMILY_LABELS = {
    # strat_hi проверяет, что связь не сводится к межпериодному сдвигу.
    # Значимый p сам по себе не гарантирует большой величины связи.
    # Метка описывает процедуру, величину сообщает `epoch_label`.
    "A-lever": "не сводится к смене периода (проверено по периодам отдельно)",
    "D-only": "совместное движение (может быть следствием смены периода)",
    # Тенант без пер-режимной стратификации (нет своих эпох): A/D различить НЕЛЬЗЯ, поэтому честно —
    # ВСЁ это со-движение, ни одно не подтверждено как рычаг. НЕ «видимость»: явный пол «не рычаг».
    "D-nostrat": "совместное движение (без пер-режимной проверки — не подтверждён как рычаг)",
}


def epoch_label(item: dict) -> str:
    """Формулировка профиля по эпохам для конституций и gp_context — ЕДИНЫЙ источник слов.

    Подаётся как ФАКТ, не как вердикт: «по периодам: медиана r=…, слабейший …» — без
    «слабая», «ненадёжная», «зато». Порог, отделяющий держится от не держится, ждёт
    калибровки на синтетике; до неё оценочное слово было бы вердиктом, выданным без
    основания. Число же не требует калибровки — и молчать о нём тоже нельзя: общий |r|
    не отличает постоянную связь от общего межпериодного дрейфа.

    Пусто для старого снимка/тенанта без эпох → рендер как раньше (backward-compat).
    """
    if not isinstance(item, dict) or item.get("r_epoch_median") is None:
        return ""
    n = item.get("epochs_n") or 0
    if n <= 0:
        return ""
    s = (f"по периодам ({n}): медиана r={item['r_epoch_median']:+.2f}, "
         f"ближайший к нулю {item['r_epoch_weakest']:+.2f}")
    # Доля согласного знака (2026-08-04): факт, не вердикт — «тот же знак в k из n».
    # Старый снимок веры без этого поля рендерится как раньше (backward-compat).
    share = item.get("r_epoch_sign_share")
    if share is not None and not (isinstance(share, float) and np.isnan(share)):
        s += f", тот же знак в {int(round(float(share) * n))}/{n}"
    return s


def family_label(verdict_family) -> str:
    """Формулировка семьи A/D для конституций и брифа. Пустая строка для отсутствия/неизвестного
    (старый снимок JSON или тенант без семьи A) → рендер как раньше, без пометки (backward-compat)."""
    return _FAMILY_LABELS.get(verdict_family, "")


# ЕДИНЫЙ источник слов причинного уточнения A-рычага (Группа 2). ОПИСАТЕЛЬНОЕ, не error-rate: честная
# стрелка «управляемо/совпадение/не знаю», а не директива. «не_знаю» обязан читаться как «не знаю»
# (пол неопределённости владельца). Прозой, без жаргона — читают конституции + gp_context, кладётся в
# build_ai_summary один раз. Пусто для не-A-рычага/тенанта/старого снимка (backward-compat).
_CAUSAL_LABELS = {
    "рычаг": "похоже на управляемую связь — есть предшествование во времени (день→день)",
    "совпадение": "похоже на совпадение — связь исчезает, если убрать медленный общий фон",
    "не_знаю": "неясно — одновременная связь, рычаг от общей причины пока не отделить",
}


def causal_label(verdict_causal) -> str:
    """Формулировка причинного уточнения (рычаг|совпадение|не_знаю) для конституций и брифа.
    Пустая строка для отсутствия/неизвестного → рендер как раньше, без пометки (backward-compat)."""
    return _CAUSAL_LABELS.get(verdict_causal, "")


# ЕДИНЫЙ источник слов направленной лаг-семьи q_lag (промоут Группа 2 ч.2). confirmatory (прошёл BY на
# валидированном нуле), но формулировка честная: «предшествование во времени», НЕ доказанная причинность —
# направленная связь необходима, не достаточна для рычага (человек/action-gate = оракул). Прозой, без жаргона.
_LAG_LABELS = {
    "lag-lever": "устойчивое предшествование во времени (раньше связано с позже; предшествование, не доказанная причинность)",
}


def lag_label(verdict_lag) -> str:
    """Формулировка направленной лаг-находки для конституций и брифа. Пусто для отсутствия
    (тенант/старый снимок/не прошло гейт) → рендер как раньше (backward-compat)."""
    return _LAG_LABELS.get(verdict_lag, "")
