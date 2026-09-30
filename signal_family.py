"""Единый загрузчик замороженной семьи сигналов валидационного гейта.

Источник истины — methodology/validation_gate/signal_family.yaml (R1: семья объявлена
ОДИН раз, до анализа, §12.3 вердикта). correlation_gate.py и longitudinal_analysis.py
читают состав семьи и параметры теста ОТСЮДА, а не хардкодят константы. Менять — только
ре-объявлением в YAML (version↑ + дата + причина), никогда не подстраивая под результат.

Модуль намеренно лёгкий (только pathlib+yaml, без numpy) — импортируется где угодно.
"""
from __future__ import annotations

from pathlib import Path

import yaml

_PATH = Path(__file__).resolve().parent / "methodology" / "validation_gate" / "signal_family.yaml"
_d = yaml.safe_load(_PATH.read_text(encoding="utf-8"))

VERSION = _d["version"]                        # счётчик ре-объявлений семьи
QUARANTINE_EPOCH = int(_d["quarantine_epoch"])  # эпоха метода карантина (с 23.09 — отдельно от VERSION)
FROZEN_AT_DECLARED = _d.get("frozen_at")
MIN_TAIL_DAYS = int((_d.get("frozen_advance") or {}).get("min_tail_days", 0))


def effective_frozen_at(declared, min_tail_days, today):
    """v10 (3): действующий frozen_at = max(объявленный, последний конец квартала qe с
    today − qe ≥ min_tail_days). Назад не ходит: max с объявленным. min_tail_days=0 → правило
    выключено (старый снимок без frozen_advance) → объявленный как есть. ISO-строка или None."""
    from datetime import date, timedelta
    if not declared or min_tail_days <= 0:
        return declared
    cut = today - timedelta(days=min_tail_days)
    q_start = date(cut.year, 3 * ((cut.month - 1) // 3) + 1, 1)   # начало квартала, где лежит cut
    qe = q_start - timedelta(days=1)                               # конец предыдущего квартала ≤ cut
    if cut.month in (3, 6, 9, 12) and (cut + timedelta(days=1)).day == 1:
        qe = cut                                                   # cut сам — конец квартала
    return max(str(declared), qe.isoformat())


def _today():
    from _time_inject import get_today
    return get_today()


FROZEN_AT = effective_frozen_at(FROZEN_AT_DECLARED, MIN_TAIL_DAYS, _today())

# Состав семьи
DAILY_METRICS = list(_d["daily_metrics"])            # contemporaneous: тестируются все пары
LAB_METRICS = list(_d["lab_metrics"])
LAGGED = dict(_d["lagged"])   # status в YAML: gated_q_lag → семья q_lag АКТИВНА в вере с @69621ed
# (2026-07-23). Комментарий «pending_harness_validation → НЕ в гейт/веру» был ложным с того дня
# и держался до аудита 2026-07-26: докстринг пережил промоут (класс «сторож=подстрока, не смысл»).
DERIVED_KNOWN = set(_d["derived_composites"]["known"])
R2_DERIVED_THRESH = float(_d["derived_composites"]["r2_threshold"])

# Прямые пары часть-целое (Gate 0): компонент ⊂ целое. Порядко-независимы (frozenset), т.к.
# пара (a,b) в гейте не упорядочена. Пусто в YAML → пустой набор (backward-compat старого снимка).
PART_WHOLE_PAIRS = {frozenset(p) for p in (_d.get("part_whole_pairs") or [])}

# Пары «алгоритмическая производная одного конвейера» (Gate 0, решение владельца 2026-08-13):
# метрика B вычисляется алгоритмом трекера из A (steps→active_kcal) — структурность по ПРОВЕНАНСУ
# вычисления, не по числу (связь умеренная — числовые детекторы честно не ловят). Пусто в YAML → пустой
# набор (backward-compat старого снимка). Потребитель обоих реестров один — STRUCTURAL_PAIRS.
ALGO_DERIVED_PAIRS = {frozenset(p) for p in (_d.get("algorithmic_derived_pairs") or [])}
STRUCTURAL_PAIRS = PART_WHOLE_PAIRS | ALGO_DERIVED_PAIRS

# Gate 2 «Period Profile» порог (ВКЛЮЧЁН как блок 2026-08-08): двухкомпонентный величина+знак.
# Отсутствие блока в YAML → 0.0/0.0 = гейт выключен (backward-compat старого снимка): медиана≥0 и
# доля_знака≥0 истинны всегда, _g2_fail никогда → gate_pass как до включения.
_g2 = _d.get("gate2_profile") or {}
GATE2_R_MIN = float(_g2.get("r_min", 0.0))
GATE2_SIGN_MIN = float(_g2.get("sign_min", 0.0))

# Gate 0.5 «Suppression Detector» (ВКЛЮЧЁН 2026-08-08): пол |частной Пирсон-корр| при Z=sleep_inbed.
# Отсутствие блока в YAML → 1.01 = выключен (|корр|≤1, порог недостижим). Backward-compat снимка.
_g05 = _d.get("gate0_5") or {}
GATE05_PARTIAL_MIN = float(_g05.get("partial_min", 1.01))

# Held-out confirm (семантика A, решение владельца 2026-08-19): discovery судит train
# (date<=FROZEN_AT), confirm — пост-freeze хвост. Порог перестановочного p на confirm-окне;
# пол окна — HELDOUT_FLOOR_WEEKS ниже (тот же дом, что у гасителя: с Э1a это граница
# валидности нуля, не провизорное назначение). Отсутствие в YAML → 0.05.
HELDOUT_CONFIRM_ALPHA = float((_d.get("heldout") or {}).get("confirm_alpha", 0.05))

# Held-out готовность (гаситель нити validation-gate-repair) И пол валидности confirm-окна
# (замер Э1a 14.08: от 42д перестановочный нуль честен). Отсутствие → 6.
HELDOUT_FLOOR_WEEKS = int((_d.get("heldout") or {}).get("floor_weeks", 6))

# Параметры теста
_g = _d["gate_params"]
FDR_Q = float(_g["fdr_q"])
B_PERM = int(_g["b_perm"])
D_PERM_EXACT = bool(_g["d_perm_exact"])        # v12: p семьи D точным перебором сдвигов (B_PERM — только held-out)
GUARD = int(_g["guard_days"])
MIN_OVERLAP_DAYS = int(_g["min_overlap_days"])   # v10: общих дней пары на train для суда (до BY)
LAB_WINDOW_DAYS = int(_g["lab_window_days"])
LAB_N_MIN = int(_g["lab_n_min"])
PAIR_MIN_OVERLAP = int(_g["pair_min_overlap"])   # A3: минимум общих дней пары для корреляции семьи (вынос из ×3 литералов)
PANEL_MIN_DAYS = int(_g["panel_min_days"])   # D1: минимум дней данных для панели/derived-регрессии (вынос из литерала correlation_gate)

# ── Стратифицированный A-гейт (Фаза 2 fdr-online): frozen-параметры confirmatory-движка ──
C_STAR_STRATIFIED = float(_g["c_star_stratified"])   # τ̂/L порог валидности эпохи (governance П10)
B_PERM_STRATIFIED = int(_g["b_perm_stratified"])     # MC-пул strat_hi
EPOCH_MIN_LEN = int(_g["epoch_min_len"])             # A4: пропуск короткой эпохи (гейт и профиль — одно число)
EPOCH_MIN_OVERLAP = int(_g["epoch_min_overlap"])     # A4: пол общих дней пары внутри эпохи

# ── Причинное уточнение A-рычагов (Группа 2 fdr-online) — ОПИСАТЕЛЬНОЕ, вне бюджета q_A/q_D ──
CAUSAL_ALPHA = float(_g["causal_alpha"])             # clarity-cut суб-теста (пол неопределённости)
CAUSAL_B_PERM = int(_g["causal_b_perm"])             # MC-пул причинных суб-тестов
CAUSAL_RESID_WINDOW = int(_g["causal_resid_window"]) # ±дней локального среднего (быстрый остаток)
CAUSAL_LAG_DAYS = int(_g["causal_lag_days"])         # направленный дискриминатор T→T+lag
CAUSAL_ASYM_RATIO = float(_g["causal_asym_ratio"])   # |r| ведущего лага / обратного для 'рычага'

# ── Направленная лаг-семья q_lag (промоут Группа 2 ч.2) — ОТДЕЛЬНАЯ confirmatory-семья в вере ──
FDR_Q_LAG = float(_g["fdr_q_lag"])                   # FDR-уровень направленной лаг-семьи (BY)

# Замороженные changepoints + baseline-ACF из data_manifest (пререгистрация τ̂-оценщика, §3.2.4/§3.5.1).
# baseline_acf — НАСТОЯЩАЯ суточная инертность (окно без эпох-конфаунда), НЕ full-grid (раздут эпохами).
_MPATH = _PATH.parent / "data_manifest.yaml"
_m = yaml.safe_load(_MPATH.read_text(encoding="utf-8"))
EPOCHS = [tuple(e["range"]) for e in _m["epochs"]]                       # [(start, end|None), ...] терап. эпохи
_bacf = _m["twin_baseline_core"]["baseline_acf"]
BASELINE_ACF = {k: float(v["acf1"]) for k, v in _bacf.items()}          # φ1 per метрика (для τ̂_pair Бартлетта)
if "weight" in _m["twin_baseline_core"]:
    BASELINE_ACF["weight"] = float(_m["twin_baseline_core"]["weight"]["acf1"])

# ── Scope лаб-пути: РЕЖИМ РАБОТЫ, а не запись в документе ────────────────────────────────────
# Решение владельца 2026-07-12: лаб-путь выведен из release-scope (данных нет). До 2026-07-26 это
# знал ровно один потребитель — датчик реактивации; сам гейт всегда считал лаб-семью и всегда
# подавал прошедшие пары в веру (аудит validation_gate, P1-04: «documented, not enforced»).
# Теперь scope читает гейт: пока путь descoped, лаб-вердиктов в вере быть не может.
# Возврат — осознанным ре-объявлением (§12.3): scope: active + version↑ + причина.
_LABS_SCOPES = {"active", "descoped_pending_data"}


def _validated_scope(value: str) -> str:
    """Fail-static: неизвестное значение НЕ должно молча означать «публикуй».

    Опечатка в манифесте обязана валить импорт, а не тихо открывать лаб-путь в веру.
    Вынесено функцией, чтобы контроль проверялся напрямую, без reload модуля."""
    if value not in _LABS_SCOPES:
        raise ValueError(f"data_manifest.labs.scope='{value}' вне {sorted(_LABS_SCOPES)} — "
                         "неизвестный режим лаб-пути; поправь манифест осознанно (§12.3)")
    return value


LABS_SCOPE = _validated_scope(str(_m["labs"]["scope"]))
LABS_ACTIVE = LABS_SCOPE == "active"
