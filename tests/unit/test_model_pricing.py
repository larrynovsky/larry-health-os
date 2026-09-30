"""Прайс-таблицы моделей (audit 2026-06-17, C2 дополнение).

Ловит два тихих отказа:
  (1) модель из MODEL_DEFAULTS без цены в сводном отчёте → битая стоимость;
  (2) расхождение цены одной модели между модулями (протухший дубль).
"""
import pytest

import hai_core
import monthly_api_report as mar
import cbcr_hypothesis as ch

pytestmark = pytest.mark.unit


def test_all_default_models_priced_in_monthly_report():
    """Каждая модель из MODEL_DEFAULTS имеет цену в monthly_api_report.PRICES.
    Сводный отчёт считает стоимость по ролям — пропуск ключа = дефолтная/битая цена."""
    missing = [m for m in hai_core.MODEL_DEFAULTS.values() if m not in mar.PRICES]
    assert not missing, f"Модели без цены в monthly_api_report.PRICES: {missing}"


def test_pricing_consistent_across_modules():
    """Цена одной модели не должна расходиться между прайс-таблицами модулей.
    monthly_api_report.PRICES vs cbcr_hypothesis._PRICING_PER_MTOK.
    Нашло реальный дрейф: haiku $1/$5 vs устаревший $0.8/$4.0 (Haiku 3.5) —
    исправлено тем же audit 2026-06-17."""
    shared = set(mar.PRICES) & set(ch._PRICING_PER_MTOK)
    assert shared, "ожидались общие модели между таблицами"
    drift = {
        m: (mar.PRICES[m], ch._PRICING_PER_MTOK[m])
        for m in shared
        if mar.PRICES[m] != ch._PRICING_PER_MTOK[m]
    }
    assert not drift, f"Расхождение цен между модулями (протухший дубль): {drift}"
