"""
UC-H-01 — `/consult <Q>` → 13 участников, Round A/B, coordinator.

Источник: USE_CASES.md §3.H → UC-H-01.
Реализация: `wellally_consult.py:464 run_consultation_cycle_async`.
Status: `implemented`.

Полный e2e с моками AsyncAnthropic и health_db. Это самый сложный
тест в первом пакете — может потребовать скриптинга 13 разных ответов.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e_mock


def test_council_imports():
    import wellally_consult
    assert wellally_consult is not None


def test_consultation_session_dataclass():
    """ConsultationSession должна быть импортируема."""
    from wellally_consult import ConsultationSession
    assert ConsultationSession is not None


def test_run_consultation_cycle_async_signature():
    """Сигнатура run_consultation_cycle_async соответствует ожидаемой."""
    import inspect
    from wellally_consult import run_consultation_cycle_async
    sig = inspect.signature(run_consultation_cycle_async)
    params = sig.parameters
    # Минимум: user_message, session
    assert "user_message" in params
    assert "session" in params


def test_council_roster_from_single_source():
    """Ростер консилиума — из consilium_roster (не хардкод в модуле).

    Мужчина 50 лет (вымышленный профиль) → 13 мед. специалистов (все 16 минус peds/geri/gyn), онкология в составе.
    (2026-07-01: заменил эвристику «13 в исходнике» — теперь ростер вычисляемый.)
    """
    import consilium_roster
    r = consilium_roster.roster_for("male", 50)
    assert len(r) == 13
    assert "oncology" in r and "gynecology" not in r
    # захардкоженного списка в модулях консилиума больше нет
    from pathlib import Path
    root = Path(__file__).parents[2]
    for mod in ("wellally_consult.py", "monthly_consilium.py"):
        src = (root / mod).read_text(encoding="utf-8")
        assert "consilium_roster" in src, f"{mod} не использует единый ростер"


def test_council_full_e2e_skipped_until_anthropic_setup():
    """
    Полный e2e (13 параллельных Claude-вызовов + координатор) — слишком
    тяжёлый для unit-test pyramid. Запускается отдельно через snapshot/
    self-consistency в Фазе 4.
    """
    pytest.skip("UC-H-01 полный e2e — отложено в Фазу 4 (self-consistency)")
