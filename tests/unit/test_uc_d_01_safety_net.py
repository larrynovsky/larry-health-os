"""
UC-D-01 — safety_net.lab_alerts детерминированно ловит свежие lab-флаги.

Источник: USE_CASES.md §3.D → UC-D-01.
Реализация: `safety_net.py`.
Status: `partial`.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def test_safety_net_module_imports():
    import safety_net
    assert safety_net is not None


def test_safety_net_has_alert_functions():
    """Должны быть базовые функции для алертов."""
    import safety_net
    candidates = ["check_lab_alerts", "check_lab_trends",
                   "check_lifestyle_alerts", "run_safety_net"]
    found = [n for n in candidates if hasattr(safety_net, n)]
    assert len(found) >= 3, (
        f"safety_net не имеет минимум 3 из {candidates}. Found: {found}"
    )


def test_safety_net_no_date_today_in_logic():
    """date.today() должен быть только в CLI-блоке (если есть)."""
    from pathlib import Path
    src = (Path(__file__).parents[2] / "safety_net.py").read_text(encoding="utf-8")
    # После T-pre.1.3 в логике — get_today()
    main_idx = src.find('if __name__ == "__main__":')
    if main_idx == -1:
        main_idx = len(src)
    logic = src[:main_idx]
    assert "date.today()" not in logic, (
        "date.today() в logic safety_net.py — должно быть get_today()"
    )
