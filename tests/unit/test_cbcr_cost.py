"""W5A-INT-7: tests для cost estimation."""
from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT))

import pytest
import cbcr_hypothesis as ch

pytestmark = pytest.mark.unit


def test_estimate_cost_sonnet():
    """Sonnet 4.6: 1000 input + 500 output → $0.003 + $0.0075 = $0.0105."""
    usage = SimpleNamespace(input_tokens=1000, output_tokens=500)
    cost = ch._estimate_cost_usd(usage, "claude-sonnet-4-6")
    assert cost == 0.0105


def test_estimate_cost_haiku():
    """Haiku 4.5: 1000 input + 500 output → $0.001 + $0.0025 = $0.0035.
    Прайс $1/$5 за MTok (Anthropic). Раньше тут был устаревший $0.8/$4.0
    (Haiku 3.5) → 0.0028; исправлено audit-тестами 2026-06-17."""
    usage = SimpleNamespace(input_tokens=1000, output_tokens=500)
    cost = ch._estimate_cost_usd(usage, "claude-haiku-4-5")
    assert cost == 0.0035


def test_estimate_cost_unknown_model_uses_sonnet_default():
    """Неизвестная модель — fallback на Sonnet pricing."""
    usage = SimpleNamespace(input_tokens=1000, output_tokens=500)
    cost = ch._estimate_cost_usd(usage, "claude-future-99")
    assert cost == 0.0105


def test_estimate_cost_dict_usage():
    """usage может прийти как dict."""
    usage = {"input_tokens": 2000, "output_tokens": 1000}
    cost = ch._estimate_cost_usd(usage, "claude-sonnet-4-6")
    assert cost == 0.021  # 0.006 + 0.015


def test_estimate_cost_none_usage():
    """None usage → 0 cost."""
    assert ch._estimate_cost_usd(None, "claude-sonnet-4-6") == 0.0


def test_estimate_cost_zero_tokens():
    """Нулевые токены → нулевая цена."""
    usage = SimpleNamespace(input_tokens=0, output_tokens=0)
    assert ch._estimate_cost_usd(usage, "claude-sonnet-4-6") == 0.0
