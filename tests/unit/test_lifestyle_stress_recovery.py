"""Позит-контроль (RST, оба направления): ветка стресс/восстановление после развилки-1.

Прежняя ветка `recovery==0 → «стресс без восстановления»` ложно приписывала стрессу ДЫРУ в
данных (в проде recovery==0 не встречается — только NULL, код превращал NULL в 0). Заменена на
честное «данных о восстановлении нет» при recovery_high отсутствующем (None). Решение владельца.
"""
from __future__ import annotations

from datetime import date

import lifestyle_agents


def _card(stress_block):
    day = {
        "hrv": {"avg": 55.0},
        "resting_heart_rate": 52,
        "stress": stress_block,
        "resilience": {},
    }
    stats = {"avg_hrv": 55, "avg_rhr": 52, "n_days": 7}
    return lifestyle_agents.StressAgent().generate_brief(day, stats, stats, date(2026, 7, 17))


def test_missing_recovery_says_no_data_not_stress(db):
    """recovery_high отсутствует (None) + есть стресс → честное «данных нет», НЕ «стресс без
    восстановления» (не приписываем стрессу дыру в данных)."""
    out = _card({"stress_high": 4800})           # 80 мин стресса, recovery_high вообще нет
    assert "данных о восстановлении за день нет" in out
    assert "без восстанов" not in out            # старый ложный флаг снят


def test_present_recovery_high_ratio_fires(db):
    """recovery присутствует и ratio > 2.5 → флаг ratio; «данных нет» НЕ появляется."""
    out = _card({"stress_high": 6000, "recovery_high": 2100})   # 100м/35м = 2.86 > 2.5
    assert "стресс/recovery ratio" in out
    assert "данных о восстановлении за день нет" not in out


def test_balanced_recovery_silent(db):
    """recovery присутствует, ratio в норме → ни ratio-флага, ни «данных нет»."""
    out = _card({"stress_high": 3600, "recovery_high": 3600})   # 60м/60м = 1.0
    assert "стресс/recovery ratio" not in out
    assert "данных о восстановлении за день нет" not in out
