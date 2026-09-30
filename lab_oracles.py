#!/usr/bin/env python3.11
"""
lab_oracles.py — независимые оракулы верификации распознанных анализов.

Распознавание документа может терять десятичный разделитель, сдвигать поля,
пропускать строки и оставлять единицы без конверсии. Самоотчёт модели
(флаг confidence) не заменяет независимую проверку.

Идея: качество даёт НЕ лучший распознаватель, а набор НЕЗАВИСИМЫХ оракулов.
Документ сам себе код коррекции: value + ref-диапазон + флаг H/L — три
кодировки одного факта; рассогласование = ошибка распознавания.

Публичный вход — verify(tests) -> dict. Остальное приватно (Rule: один
модуль — один публичный entry point).
"""
from __future__ import annotations

# Физиологически НЕВОЗМОЖНЫЕ границы (грубый сейф-нет, не клиническая норма).
# Ключи — canonical_name. Шире клинических диапазонов: ловим только грубый
# парсинг-мусор (потеря точки, сдвиг), не патологию.
_HARD = {
    "Glucose": (20, 800), "Calcium": (4, 15), "Calcium_ionized": (2, 8), "Sodium": (110, 170),
    "Potassium": (2, 8), "Chloride": (70, 130), "WBC": (0.5, 100),
    "RBC": (1.5, 8), "HGB": (4, 22), "HCT": (15, 65), "MCV": (50, 130),
    "MCH": (15, 45), "MCHC": (28, 40), "RDW": (9, 30), "PLT": (5, 1200),
    "ALT": (1, 5000), "AST": (1, 5000), "Creatinine": (0.1, 20),
    # Urea — мочевина в мг/дл. BUN (азот мочевины) — ДРУГОЕ вещество той же пробы,
    # ровно в 2.14 раза меньше: разведены 2026-07-29, но границ у нового имени не
    # было, то есть единственный аналит канона не проверялся на правдоподобие вовсе.
    "Urea": (5, 300), "BUN": (2, 140), "Albumin": (1, 7), "Total_Protein": (3, 12),
    "Cholesterol_Total": (40, 500), "Triglycerides": (10, 2000),
    "Iron": (5, 400), "LDH": (50, 3000), "Amylase": (5, 1500),
    "Magnesium": (0.5, 5), "Phosphorus": (0.5, 10), "Uric_acid": (1, 20),
    "Globulin": (0.5, 8), "LDL": (20, 400), "HDL": (10, 150),
}
_PCT = {"Neutrophils_pct", "Lymphocytes_pct", "Monocytes_pct",
        "Basophils_pct", "Eosinophils_pct"}

# Ядро панелей — контроль полноты (recall). Если документ содержит панель,
# но ядро неполно — recall-провал.
_PANEL_CORE = {
    "cbc": {"WBC", "RBC", "HGB", "HCT", "MCV", "MCH", "MCHC", "RDW", "PLT"},
    "chemistry": {"Glucose", "Urea", "Creatinine", "Sodium", "Potassium",
                  "Chloride"},
}

_EPS = 1e-9


def _o_bounds(t: dict) -> list[str]:
    """Физиологически невозможные значения (unit-aware: СИ→conventional перед
    проверкой границ — иначе, например, HGB 130 g/L = 13.0 g/dL ложно флагуется вне [4,22])."""
    import lab_canon
    n, v = t.get("canonical_name"), t.get("value")
    if v is None:
        return []
    v, _u = lab_canon.to_conventional(n, v, t.get("unit"))
    if n in _PCT and (v < 0 or v > 100):
        return [f"{n}={v}: процент вне [0,100]"]
    if n in _HARD:
        lo, hi = _HARD[n]
        if v < lo or v > hi:
            return [f"{n}={v}: физиологически невозможно (вне [{lo},{hi}])"]
    return []


def _o_internal(t: dict) -> list[str]:
    """Значение vs реф-диапазон vs напечатанный флаг — ловит потерю точки даром."""
    v, lo, hi, fl = (t.get("value"), t.get("ref_low"),
                     t.get("ref_high"), t.get("doc_flag"))
    n = t.get("canonical_name")
    if v is None or not fl:
        return []
    out = []
    if fl == "N":
        if lo is not None and v < lo - _EPS:
            out.append(f"{n}={v}: флаг 'норма', но < ref_low {lo}")
        if hi is not None and v > hi + _EPS:
            out.append(f"{n}={v}: флаг 'норма', но > ref_high {hi}")
    elif fl == "H" and hi is not None and v <= hi:
        out.append(f"{n}={v}: флаг 'высоко', но <= ref_high {hi}")
    elif fl == "L" and lo is not None and v >= lo:
        out.append(f"{n}={v}: флаг 'низко', но >= ref_low {lo}")
    return out


def _o_magnitude(t: dict, k: float = 5.0) -> list[str]:
    """Потеря десятичной точки: значение в k раз вне реф-диапазона."""
    v, lo, hi = t.get("value"), t.get("ref_low"), t.get("ref_high")
    n = t.get("canonical_name")
    if v is None:
        return []
    if hi is not None and hi > 0 and v > hi * k:
        return [f"{n}={v}: в {v/hi:.0f}x выше ref_high {hi} — вероятна потеря точки"]
    if lo is not None and lo > 0 and 0 < v < lo / k:
        return [f"{n}={v}: сильно ниже ref_low {lo} — вероятна потеря точки"]
    return []


def _o_units(t: dict) -> list[str]:
    """У численного результата с реф-диапазоном должна быть единица."""
    if t.get("value") is not None and (t.get("ref_low") is not None
                                       or t.get("ref_high") is not None):
        if not (t.get("unit") or "").strip():
            n = t.get("canonical_name")
            return [f"{n}: есть значение и реф-диапазон, но нет единицы"]
    return []


def _o_dedup(tests: list[dict]) -> list[str]:
    """Один аналит под >1 имени с тем же значением = дубль/алиас."""
    seen: dict = {}
    for t in tests:
        c = t.get("canonical_name")
        if not c:            # null/пустое имя — не аналит, не схлопываем
            continue
        seen.setdefault(c, []).append(t.get("value"))
    return [f"{c}: записан {len(vs)} раз(а) {vs} — дубль/алиас"
            for c, vs in seen.items() if len(vs) > 1]


def _o_completeness(tests: list[dict]) -> list[str]:
    """Recall-контроль по ядру панелей."""
    panels = {t.get("panel") for t in tests if t.get("panel")}
    present = {t.get("canonical_name") for t in tests}
    out = []
    for p in panels:
        core = _PANEL_CORE.get(p)
        if core:
            missing = core - present
            if missing:
                out.append(f"панель '{p}': отсутствует ядро {sorted(missing)} (recall-провал)")
    return out


def _o_canonical_ref(t: dict, refs: dict) -> list[str]:
    """Кросс-проверка: напечатанный в документе реф-диапазон vs канонический
    из system_config.lab_refs. Сильное расхождение → подозрение на парсинг
    либо устаревший канон. Soft-оракул (включается только если refs передан)."""
    n, lo, hi = t.get("canonical_name"), t.get("ref_low"), t.get("ref_high")
    if not refs or n not in refs:
        return []
    cmin, cmax = refs[n][0], refs[n][1]
    out = []
    for label, doc_v, can_v in (("ref_low", lo, cmin), ("ref_high", hi, cmax)):
        if doc_v is not None and can_v is not None and can_v != 0:
            if abs(doc_v - can_v) / abs(can_v) > 0.5:
                out.append(f"{n}: {label} документа {doc_v} ≠ канон {can_v} (>50%)")
    return out


def verify(tests: list[dict], canonical_refs: dict | None = None) -> dict:
    """Прогоняет все оракулы по списку распознанных тестов одного документа.

    tests — список dict с ключами: canonical_name, value, unit, ref_low,
    ref_high, doc_flag, panel.
    canonical_refs — опц. {name: (min, max, unit)} из get_lab_refs() для
    кросс-проверки.

    Возвращает {'status': 'green'|'flagged', 'issues': {oracle: [...]},
    'count': int}.
    """
    def _num(x):
        try:
            return float(x)
        except (TypeError, ValueError):
            return None
    # защита: числовые поля могут прийти строками ("8.6") — приводим
    tests = [{**t, "value": _num(t.get("value")),
              "ref_low": _num(t.get("ref_low")), "ref_high": _num(t.get("ref_high"))}
             for t in tests]
    issues = {"bounds": [], "internal": [], "magnitude": [],
              "units": [], "canonical_ref": [], "dedup": [], "completeness": []}
    for t in tests:
        issues["bounds"] += _o_bounds(t)
        issues["internal"] += _o_internal(t)
        issues["magnitude"] += _o_magnitude(t)
        issues["units"] += _o_units(t)
        if canonical_refs:
            issues["canonical_ref"] += _o_canonical_ref(t, canonical_refs)
    issues["dedup"] += _o_dedup(tests)
    issues["completeness"] += _o_completeness(tests)
    total = sum(len(v) for v in issues.values())
    return {"status": "green" if total == 0 else "flagged",
            "issues": {k: v for k, v in issues.items() if v},
            "count": total}
