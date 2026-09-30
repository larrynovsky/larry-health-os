"""Провенанс стадий сна: отсутствие стадии НЕ становится измеренным нулём.

Источники сна могут возвращать разный набор полей: длительность бывает известна
при отсутствии стадий. Нельзя превращать отсутствующие поля в измеренные нули.
Ниже независимо придуманные записи; имя источника сохраняет проверяемый контракт.

Механически ноль появлялся у обоих писателей одинаково: `get(ключ, 0)` превращал ОТСУТСТВИЕ
поля в число. `metrics_db.upsert_metrics_from_json` фильтрует только `is not None`, поэтому
ноль проходил в колонку, а `COUNT()` считал его данными.

Инвариант ОДИН, писателей ДВА, поэтому дом теста — свойство, а не модуль: появится третий
источник сна — его случай добавляется сюда же, а не в третий файл.

Каждый позитивный тест здесь — мутация наоборот: верни `get(ключ, 0)` вместо `get(ключ)`,
и соответствующий тест обязан покраснеть. Проверено исполнением при написании.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


# ── Писатель 1: Oura ───────────────────────────────────────────────────────

def _oura_session(**over):
    s = {
        "type": "long_sleep",
        "day": "2041-02-18",
        "total_sleep_duration": 7 * 3600,
        "bedtime_start": "2041-02-17T23:00:00+03:00",
        "bedtime_end": "2041-02-18T06:00:00+03:00",
    }
    s.update(over)
    return s


def test_oura_missing_stage_is_none_not_zero():
    """Поля стадии нет в ответе API → None. Ноль означал бы «прибор измерил ноль»."""
    from import_oura import parse_sleep

    got = parse_sleep([_oura_session()], [])["2041-02-18"]
    assert got["deep"] is None, f"отсутствие deep стало {got['deep']!r} — дыра выдана за измерение"
    assert got["rem"] is None
    assert got["core"] is None
    assert got["awake"] is None
    assert got["inBed"] is None, "inBed считается из awake — без awake он неизвестен, а не равен total"


def test_oura_explicit_zero_stays_zero():
    """Различие должно работать в ОБЕ стороны: явный ноль от прибора остаётся нулём."""
    from import_oura import parse_sleep

    got = parse_sleep([_oura_session(
        deep_sleep_duration=0, rem_sleep_duration=0,
        light_sleep_duration=0, awake_time=0,
    )], [])["2041-02-18"]
    assert got["deep"] == 0.0, "явный ноль прибора не должен превращаться в None"
    assert got["awake"] == 0.0
    assert got["inBed"] == 7.0, "awake=0 измерен, значит inBed = total"


def test_oura_normal_session_unchanged():
    """Характеризация: обычная сессия считается как раньше (защита от переусердствования)."""
    from import_oura import parse_sleep

    got = parse_sleep([_oura_session(
        deep_sleep_duration=3600, rem_sleep_duration=5400,
        light_sleep_duration=16200, awake_time=1800,
    )], [])["2041-02-18"]
    assert got["totalSleep"] == 7.0
    assert got["deep"] == 1.0
    assert got["rem"] == 1.5
    assert got["core"] == 4.5
    assert got["awake"] == 0.5
    assert got["inBed"] == 7.5


# ── Писатель 2: Apple Health (сюда приходит Sleep Cycle) ───────────────────

def test_apple_sleep_cycle_record_gives_no_fake_stages():
    """Придуманная запись Sleep Cycle: длительность есть, стадии не переданы."""
    from import_apple_health import aggregate_metric_by_day

    daily: dict = {}
    aggregate_metric_by_day("sleep_analysis", [{
        "date": "2041-02-18 08:20:00 +0300",
        "totalSleep": 6.25,
        "inBed": 7.0,
        "source": "Sleep Cycle",
    }], daily)

    got = daily["2041-02-18"]["sleep"]
    assert got["source"] == "Sleep Cycle"
    assert got["totalSleep"] == 6.25, "измеренное поле обязано сохраниться"
    assert got["inBed"] == 7.0
    for stage in ("deep", "rem", "core", "awake"):
        assert got[stage] is None, (
            f"{stage} стал {got[stage]!r}: прибор его не измеряет, "
            "а ноль неотличим от измерения"
        )


def test_apple_explicit_zero_stays_zero():
    """Обратная сторона: если источник ЯВНО прислал ноль — он остаётся нулём."""
    from import_apple_health import aggregate_metric_by_day

    daily: dict = {}
    aggregate_metric_by_day("sleep_analysis", [{
        "date": "2041-02-18 08:20:00 +0300",
        "totalSleep": 7.0, "inBed": 7.5,
        "deep": 0, "rem": 0, "core": 7.0, "awake": 0.5,
        "source": "Apple Watch",
    }], daily)

    got = daily["2041-02-18"]["sleep"]
    assert got["deep"] == 0.0
    assert got["rem"] == 0.0
    assert got["core"] == 7.0


# ── Сцепление с записью в БД ───────────────────────────────────────────────

def test_none_stage_never_reaches_column():
    """Замыкание инварианта: None из парсера не должен доехать до колонки.

    Тест смотрит на фильтр писателя в БД, а не на парсер — иначе доказывали бы
    промежуточное звено вместо предмета.
    """
    import inspect
    import metrics_db

    src = inspect.getsource(metrics_db.upsert_metrics_from_json)
    assert "if v is not None" in src, (
        "фильтр None в upsert_metrics_from_json исчез — тогда None поедет в колонку "
        "и заменит собой существующее значение"
    )
