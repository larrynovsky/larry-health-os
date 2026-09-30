"""Wave 5F-1: tests для _check_patient_view."""
from __future__ import annotations
from pathlib import Path
import sys

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT))

import pytest
import cbcr_hypothesis as ch

pytestmark = pytest.mark.unit


def _good_pv():
    return {
        "noticed":      "За 90 дней глубокий сон снизился на 25%, держится 10 дней подряд.",
        "might_mean":   "Может быть эффект нового режима тренировок. Или дисбаланс щитовидной железы.",
        "do_now":       "Сдать TSH + free T4 в ближайшие 7 дней.",
        "consult_when": "Если 21 день подряд глубокий сон ниже 40 мин — к сомнологу.",
    }


def test_missing_patient_view_flagged():
    assert ch._check_patient_view({}) == ["patient_view отсутствует или не dict — требуется для Telegram-уведомления пациенту"]


def test_non_dict_patient_view():
    assert "не dict" in ch._check_patient_view({"patient_view": "string"})[0]


def test_good_patient_view_ok():
    assert ch._check_patient_view({"patient_view": _good_pv()}) == []


def test_missing_required_keys():
    pv = _good_pv()
    del pv["do_now"]
    issues = ch._check_patient_view({"patient_view": pv})
    assert any("do_now" in i for i in issues)


def test_empty_string_field_flagged():
    pv = _good_pv()
    pv["noticed"] = "   "
    issues = ch._check_patient_view({"patient_view": pv})
    assert any("noticed пустой" in i for i in issues)


def test_jargon_subacute_flagged():
    pv = _good_pv()
    pv["noticed"] = "Sub-acute monotonic decline глубокого сна за 90 дней."
    issues = ch._check_patient_view({"patient_view": pv})
    assert any("жаргон" in i.lower() for i in issues)
    assert any("sub-acute" in str(i) for i in issues)


def test_jargon_neirotoxicity_flagged():
    pv = _good_pv()
    pv["might_mean"] = "Кумулятивная нейротоксичность препарата."
    issues = ch._check_patient_view({"patient_view": pv})
    assert any("жаргон" in i.lower() for i in issues)


def test_jargon_decision_threshold_flagged():
    pv = _good_pv()
    pv["consult_when"] = "При нарушении decision threshold — к врачу."
    issues = ch._check_patient_view({"patient_view": pv})
    assert any("жаргон" in i.lower() for i in issues)


def test_abbreviations_allowed():
    """TSH, SpO2, HRV — короткие аббревиатуры разрешены."""
    pv = _good_pv()
    pv["do_now"] = "Проверь SpO2 во сне через Oura, сдай TSH и анализ HRV."
    assert ch._check_patient_view({"patient_view": pv}) == []


def test_numbers_allowed():
    pv = _good_pv()
    pv["noticed"] = "Глубокий сон 40 минут вместо 52 за 90 дней, streak 10 дней."
    issues = ch._check_patient_view({"patient_view": pv})
    # «streak» не в blacklist, числа OK
    assert issues == []
