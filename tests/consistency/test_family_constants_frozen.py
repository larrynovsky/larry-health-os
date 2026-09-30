"""D1 (аудит «замысел vs код» 2026-07-18): пороги жизнеспособности семьи сигналов.

Инвариант family_frozen_in_params holds для FDR_Q / DERIVED_KNOWN (читаются из
signal_family.yaml), НО пороги жизнеспособности пары/панели живут ЛИТЕРАЛАМИ в
correlation_gate.py и longitudinal_analysis.py — вне yaml. Маркеры сторожа
(_sf.FDR_Q, _sf.DERIVED_KNOWN) их не видят: сторож зелёный, а часть семьи
захардкожена (ложно-зелёный по букве шире кода).

Это §4-сторож (риск → runnable check): он НЕ выносит пороги в yaml — это
методологическое решение (минимум выборки для корреляции; оракул — инженер).
Он ЗАМОРАЖИВАЕТ известные литералы и ловит их молчаливое изменение или
появление рядом новых, пока значения не вынесены в signal_family.yaml (долг D1).

Структурный source-guard (не поведенческий): считает вхождения известных
порогов. Позитивный контроль: поменяй в correlation_gate.py `mm.sum() < 30`
на `< 25` — этот тест покраснеет.
"""
from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent

# Реестр D1: известные пороги жизнеспособности семьи, живущие литералами в коде.
# (файл, regex, ожидаемое_число_вхождений, смысл-и-оракул)
# Изменение числа вхождений (±) → пересмотри: не пора ли вынести в signal_family.yaml.
_KNOWN_FAMILY_THRESHOLDS = [
    # A3 (2026-09-22): порог пары 30 (×3) вынесен в signal_family.yaml → gate_params.pair_min_overlap,
    # морозится ПО ЗНАЧЕНИЮ ниже (test_pair_min_overlap_frozen_in_yaml). Здесь остался один литерал —
    # и он НЕ порог семьи: минимум дней БАЗЫ в recovery_trajectory (сравнение «до болезни ↔ сейчас»).
    # Число то же, смысл другой; сведение в pair_min_overlap было бы ложным одним домом. Морозится,
    # чтобы его сдвиг не прошёл молча, пока у него нет своего дома (правило «нет порогов-литералов»).
    ("longitudinal_analysis.py", r"len\(b_vals\)\s*<\s*30",           1, "min дней базы recovery = 30 (НЕ семья)"),
]
# D1 ЗАКРЫТ для порога панели (2026-07-23): панель-80 (notna≥80 ×3 + len<80 ×1) вынесен из
# литералов correlation_gate.py в signal_family.yaml → gate_params.panel_min_days. Теперь
# морозится ПО ЗНАЧЕНИЮ в yaml (test_panel_min_days_frozen_in_yaml ниже) + регресс-гвард на
# возврат литерала. Остаток D1 (overlap=30) ЗАКРЫТ 2026-09-22 (A3): pair_min_overlap в yaml, ×3 читателя.


def test_known_family_thresholds_frozen():
    """Каждый известный порог семьи присутствует ровно ожидаемое число раз.

    FAIL означает: порог жизнеспособности изменён/сдвинут в коде, а он НЕ в
    signal_family.yaml (вне оракула-реестра). Реши осознанно: вынести значение
    в yaml (предпочтительно — §9) или обновить этот D1-реестр с обоснованием.
    """
    problems = []
    for fname, pattern, expected, meaning in _KNOWN_FAMILY_THRESHOLDS:
        path = _ROOT / fname
        assert path.exists(), f"{fname} не найден по пути {path}"
        text = path.read_text(encoding="utf-8")
        found = len(re.findall(pattern, text))
        if found != expected:
            problems.append(
                f"{fname}: порог «{meaning}» (/{pattern}/) найден {found}×, ждали {expected}"
            )
    assert not problems, (
        "D1: пороги жизнеспособности семьи сигналов вне signal_family.yaml сдвинулись.\n"
        + "\n".join(problems)
        + "\nЭто не в yaml → нет оракула. Реши: вынести в signal_family.yaml или "
          "обновить _KNOWN_FAMILY_THRESHOLDS с обоснованием (D1-долг, аудит 2026-07-18)."
    )


def test_panel_min_days_frozen_in_yaml():
    """D1-закрытие порога панели: значение 80 живёт в signal_family.yaml (единый оракул),
    а НЕ литералом в correlation_gate.py. Морозит значение + ловит регресс (возврат литерала).

    Позит-контроль: поменяй panel_min_days в yaml на 70 — покраснеет (ждём 80). Второй:
    верни `>= 80` / `< 80` панель-литерал в correlation_gate.py — покраснеет (литерал вернулся).
    """
    import sys
    sys.path.insert(0, str(_ROOT))
    import signal_family as _sf
    assert _sf.PANEL_MIN_DAYS == 80, f"panel_min_days в yaml = {_sf.PANEL_MIN_DAYS}, заморожено 80"

    cg = (_ROOT / "correlation_gate.py").read_text(encoding="utf-8")
    stray = re.findall(r"notna\(\)\.sum\(\)\s*>=\s*80\b", cg) + re.findall(r"len\(sub\)\s*<\s*80\b", cg)
    assert not stray, (
        f"D1-регресс: панель-порог 80 вернулся литералом в correlation_gate.py ({len(stray)}×) — "
        "используй PANEL_MIN_DAYS из signal_family.yaml, не хардкод."
    )


def test_pair_min_overlap_frozen_in_yaml():
    """A3 (2026-09-22): порог пары 30 живёт в signal_family.yaml (один дом), не литералом в коде.

    До выноса реестр выше держал два литерала семьи (третий его пункт, `b_vals`, —
    вообще не семья), а ТРЕТИЙ читатель — дефолт `correlation_matrix(min_pairs=30)` —
    был ему невидим: regex искал `len(paired) < 30`, а число стояло в сигнатуре.
    Сторож был зелёным при незамороженном пороге семьи (corr_all идёт в гейт).

    Позит-контроль: pair_min_overlap в yaml → 25 — покраснеет (ждём 30). Регресс:
    верни `< 30` / `min_pairs: int = 30` в код — покраснеет.
    """
    import inspect
    import sys
    sys.path.insert(0, str(_ROOT))
    import signal_family as _sf
    assert _sf.PAIR_MIN_OVERLAP == 30, f"pair_min_overlap в yaml = {_sf.PAIR_MIN_OVERLAP}, заморожено 30"

    stray = []
    for fname, pat in [("correlation_gate.py", r"mm\.sum\(\)\s*<\s*30\b"),
                       ("longitudinal_analysis.py", r"len\(paired\)\s*<\s*30\b"),
                       ("longitudinal_analysis.py", r"min_pairs\s*:\s*int\s*=\s*30\b"),
                       ("longitudinal_analysis.py", r"^LAB_WINDOW_DAYS\s*=\s*\d")]:
        n = len(re.findall(pat, (_ROOT / fname).read_text(encoding="utf-8"), flags=re.M))
        if n:
            stray.append(f"{fname}: /{pat}/ ×{n}")
    assert not stray, "A3-регресс: порог семьи вернулся литералом — читай из signal_family: " + "; ".join(stray)

    import longitudinal_analysis as la
    assert inspect.signature(la.correlation_matrix).parameters["min_pairs"].default == _sf.PAIR_MIN_OVERLAP
    assert la.LAB_WINDOW_DAYS == _sf.LAB_WINDOW_DAYS


def test_epoch_thresholds_frozen_in_yaml():
    """A4 (2026-09-23, нить family-epoch): пропуск короткой эпохи (16) и пол перекрытия внутри
    эпохи (12) живут в signal_family.yaml. До 23.09 — литералами ×3 и ×3 в correlation_gate
    (гейт strat_hi, профиль эпох, нулевое распределение, причинное уточнение), и совпадение
    гейта с профилем держалось только комментарием. Морозит значения + ловит возврат литерала."""
    import sys
    sys.path.insert(0, str(_ROOT))
    import signal_family as _sf
    assert (_sf.EPOCH_MIN_LEN, _sf.EPOCH_MIN_OVERLAP) == (16, 12), \
        f"epoch_min_len/epoch_min_overlap в yaml = {(_sf.EPOCH_MIN_LEN, _sf.EPOCH_MIN_OVERLAP)}, заморожено (16, 12)"
    cg = (_ROOT / "correlation_gate.py").read_text(encoding="utf-8")
    stray = (re.findall(r"\bL\s*<\s*16\b", cg) + re.findall(r"len\(idx\)\s*<\s*16\b", cg)
             + re.findall(r"\.sum\(\)\s*<\s*12\b", cg))
    assert not stray, f"литерал порога эпохи вернулся в correlation_gate.py: {stray}"


def test_quarantine_epoch_does_not_follow_family_version(monkeypatch):
    """A4: эпоха метода карантина — отдельное поле. Ре-объявление семьи (version↑) без смены
    метода не делает pending-строки карантина «чужими» (иначе пара теряет защиту молча)."""
    import sys
    sys.path.insert(0, str(_ROOT))
    import quarantine_db
    import signal_family as _sf
    assert quarantine_db.method_epoch() == "signal_family_v7", \
        "эпоха карантина сменилась — строки signal_family_v7 стали чужими"
    monkeypatch.setattr(_sf, "VERSION", 99)
    assert quarantine_db.method_epoch() == "signal_family_v7", \
        "эпоха карантина снова следует за version семьи"
