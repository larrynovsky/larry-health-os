"""Среда С-2: адаптеры → карточки. Жара по сезонному отклонению, не абсолюту."""
from __future__ import annotations

import pytest

import env_context as ec

pytestmark = pytest.mark.unit


def test_july_normal_heat_no_card():
    """35°C в июле (норма ~33) — не аномалия → карточки жары нет."""
    cards = ec.from_weather({"temp_max": 35.3}, month=7)
    assert not any(c.semantic_key == "weather:heat:above_seasonal" for c in cards)


@pytest.mark.owner_data
def test_july_heatwave_makes_card():
    """41°C в июле — заметно выше нормы → карточка есть."""
    cards = ec.from_weather({"temp_max": 41.0}, month=7)
    heat = [c for c in cards if c.semantic_key == "weather:heat:above_seasonal"]
    assert heat and heat[0].severity >= 0.5


@pytest.mark.owner_data
def test_winter_same_absolute_would_be_anomaly():
    """25°C в январе (норма ~17) — аномалия → карточка (относительность, не абсолют)."""
    cards = ec.from_weather({"temp_max": 25.0}, month=1)
    assert any(c.semantic_key == "weather:heat:above_seasonal" for c in cards)


def test_dust_event_card_safety_lane():
    cards = ec.from_weather({"dust": 150.0}, month=7)
    dust = [c for c in cards if c.semantic_key == "weather:dust:elevated"]
    assert dust and dust[0].lane == "safety"


def test_no_dust_no_card():
    assert not any(c.semantic_key == "weather:dust:elevated"
                   for c in ec.from_weather({"dust": 0.0}, month=7))


def test_uv_high_protective_card():
    cards = ec.from_weather({"uv_max": 10.0}, month=7)
    assert any(c.semantic_key == "weather:uv:high" for c in cards)


def test_air_elevated_aqi():
    assert ec.from_air({"aqi": 160})[0].semantic_key == "air:aqi:elevated"
    assert ec.from_air({"aqi": 40}) == []


def test_marine_swimmable_warm():
    c = ec.from_marine({"sea_temp": 27.8, "wave_max": 0.6})
    assert c and c[0].semantic_key == "sea:temp:swimmable"


def test_marine_cold_no_card():
    assert ec.from_marine({"sea_temp": 18.0, "wave_max": 0.4}) == []


def test_marine_waves_override_swim():
    """Тёплое море, но волны ≥1.25 → карта ВОЛН, не приглашение купаться (не зовём в шторм)."""
    c = ec.from_marine({"sea_temp": 28.0, "wave_max": 1.6})
    assert c and c[0].semantic_key == "sea:waves:high"
    assert not any(x.semantic_key == "sea:temp:swimmable" for x in c)


def test_marine_storm_safety_lane():
    c = ec.from_marine({"sea_temp": 24.0, "wave_max": 3.0})
    assert c and c[0].lane == "safety"


def test_env_cards_clear_gate_theta():
    """UV-очень-высокий и плохой AQI обязаны ПРОЙТИ порог релевантности гейта —
    иначе среда молчала бы всегда (сегодняшний UV 8.65 ловил ровно это)."""
    import brief_gate as bg
    uv = [c for c in ec.from_weather({"uv_max": 8.65}, 7) if c.semantic_key == "weather:uv:high"][0]
    air = ec.from_air({"aqi": 110})[0]
    assert uv.severity >= bg.ROUTINE_SEVERITY_THETA
    assert air.severity >= bg.ROUTINE_SEVERITY_THETA


# ── Квантование env-severity (нить profile-staleness, п.2) ───────────────────
def test_env_severity_is_discrete_label():
    """severity env — дискретный ЯРЛЫК класса (routine=0.5), а не сырой индекс.
    Два UV с разным индексом → ОДНА severity: дрожь 0.46↔0.47 убита в источнике
    (не полом Э6, а тем, что поле перестало быть непрерывным)."""
    uv1 = [c for c in ec.from_weather({"uv_max": 8.85}, month=7)
           if c.semantic_key == "weather:uv:high"][0]
    uv2 = [c for c in ec.from_weather({"uv_max": 11.0}, month=7)
           if c.semantic_key == "weather:uv:high"][0]
    assert uv1.severity == uv2.severity == 0.5
    assert uv1.lane == "routine"


def test_env_relevance_carries_magnitude_for_ranking():
    """Континуальная острота переехала в relevance — ею соревнуется слот. Порядок
    сохранён: UV-11 острее UV-8.85, хотя ярлык severity у них одинаков."""
    uv1 = [c for c in ec.from_weather({"uv_max": 8.85}, month=7)
           if c.semantic_key == "weather:uv:high"][0]
    uv2 = [c for c in ec.from_weather({"uv_max": 11.0}, month=7)
           if c.semantic_key == "weather:uv:high"][0]
    assert uv1.relevance is not None and uv2.relevance is not None
    assert uv1.relevance < uv2.relevance


@pytest.mark.owner_data
def test_env_safety_label_distinct_from_routine():
    """safety-полоса env (жара ≥2σ) получает ярлык 0.7, routine — 0.5: класс, не индекс."""
    hot = [c for c in ec.from_weather({"temp_max": 41.0}, month=7)
           if c.semantic_key == "weather:heat:above_seasonal"][0]
    assert hot.lane == "safety" and hot.severity == 0.7
    assert hot.relevance is not None            # магнитуда сохранена
