"""
UC-B-09 — Корреляции трекеров не содержат выдуманных r-значений.

Источник: USE_CASES.md §3.B → UC-B-09.
Реализация: `longitudinal_analysis.py` (`correlation_analysis.py` снят 27.09 решением владельца: его работу ведёт месячный консилиум).
Status: `partial`.

Главный инвариант: если в тексте есть `r=0.X`, оно должно быть подтверждено
вычислением (на тех же данных) или записью в `agent_reports`.
"""
from __future__ import annotations

import re

import pytest

pytestmark = pytest.mark.integration


CORR_PATTERN = re.compile(r"r\s*=\s*[+-]?0\.\d+")


def test_longitudinal_analysis_module_imports():
    """numpy может отсутствовать в test-окружении — skip OK для smoke."""
    try:
        import longitudinal_analysis
        assert longitudinal_analysis is not None
    except ImportError as e:
        pytest.skip(f"longitudinal_analysis требует {e.name} — "
                     f"запускать в Studio prod environment")


def test_correlation_pattern_extracts_numbers():
    """Sanity на паттерн."""
    assert CORR_PATTERN.findall("корреляция r=0.83 между HRV и шагами")
    assert CORR_PATTERN.findall("r=-0.74 (отрицательная)")
    assert not CORR_PATTERN.findall("r is unknown")


# ⚰️ test_known_correlations_documented — снят 2026-08-02.
#
# Он читал BLUEPRINT.md и требовал, чтобы подстрока "0.83" встречалась в файле.
# Это не оракул для «корреляция не выдумана»: он зеленел бы от строки «версия 0.83»
# или от любого другого числа 0.83 в тексте, и краснел бы от переписывания абзаца,
# не имеющего к корреляциям отношения. Ровно тот класс, что CLAUDE.md §20 —
# зелёный, причинённый окружением (наличием файла), а не проверяемым свойством.
# Обнаружен при удалении §3 BLUEPRINT: тест упал, хотя корреляции не менялись.
#
# ЧЕСТНО: после снятия у UC-B-09 живого оракула НЕТ. Заявленный инвариант
# («каждое r=0.X в тексте агента подтверждено записью в agent_reports») требует
# прогона longitudinal_analysis, который здесь скипается без numpy — то есть
# настоящая проверка возможна только на Studio. Helper ниже реализует логику,
# но исполняется на синтетике, а не на живых отчётах. Записано в CLAUDE.md § E.


def test_correlation_oracle_check_ready(db):
    """
    Oracle-логика для UC-B-09: парсим текст агента, для каждого r=X.YY
    проверяем существование в agent_reports.

    Здесь — заглушка с реализацией helper-функции.
    """
    def has_unsupported_correlations(text: str, known_corrs: dict[str, float]) -> list[str]:
        unsupported = []
        for m in CORR_PATTERN.finditer(text):
            value = float(m.group().split("=")[1].strip())
            # Проверка: есть ли корреляция близкая к value в known
            if not any(abs(v - value) < 0.05 for v in known_corrs.values()):
                unsupported.append(m.group())
        return unsupported

    # Test of helper itself
    known = {"hrv_creatinine": 0.83, "mcv_steps": -0.74}
    text = "Корреляция HRV и креатинина r=0.83 — устойчивый паттерн."
    assert has_unsupported_correlations(text, known) == []

    text2 = "Корреляция r=0.99 между сном и стрессом."
    unsupported = has_unsupported_correlations(text2, known)
    assert unsupported, "выдуманная корреляция r=0.99 не поймана"
