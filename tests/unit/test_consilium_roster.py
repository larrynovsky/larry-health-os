"""Тесты единого ростера консилиума (consilium_roster).

Правило: все мед. специалисты минус ЯВНО неподходящие по полу/возрасту.
+ датчик: каждое имя ростера имеет промпт specialists/<name>.md.
"""
from pathlib import Path

import consilium_roster as cr


def test_male_adult_excludes_peds_geri_gyn():
    r = cr.roster_for("male", 54)
    assert "pediatrics" not in r
    assert "geriatrics" not in r
    assert "gynecology" not in r
    assert {"cardiology", "oncology", "urology", "general", "dermatology"} <= set(r)
    assert len(r) == 13   # 16 всего − 3


def test_female_keeps_gynecology():
    r = cr.roster_for("female", 40)
    assert "gynecology" in r
    assert "pediatrics" not in r and "geriatrics" not in r


def test_geriatric_age_keeps_geriatrics():
    r = cr.roster_for("male", 70)
    assert "geriatrics" in r and "pediatrics" not in r


def test_child_keeps_pediatrics():
    r = cr.roster_for("female", 8)
    assert "pediatrics" in r
    assert "geriatrics" not in r and "gynecology" in r


def test_unknown_is_conservative_all_included():
    r = cr.roster_for(None, None)
    assert set(r) == set(cr._MEDICAL_ALL)   # ничего не исключаем при неизвестном


def test_medical_roster_from_profile():
    r = cr.medical_roster({"identity": {"sex": "male", "birth_date": "1975-01-01"}})
    assert "gynecology" not in r and "pediatrics" not in r
    assert "cardiology" in r


def test_every_roster_name_has_prompt_file():
    specs = Path(__file__).resolve().parents[2] / "specialists"
    missing = [n for n in cr._MEDICAL_ALL if not (specs / f"{n}.md").exists()]
    assert not missing, f"нет промптов для: {missing}"


# ── Lifestyle: единый источник (свёл 4↔5 и инлайн-промпты monthly) ────────────

def test_lifestyle_roster_matches_files():
    specs = Path(__file__).resolve().parents[2] / "specialists"
    assert len(cr.LIFESTYLE) == 4
    keys = [k for _d, k in cr.LIFESTYLE]
    assert keys == ["sleep", "movement", "stress", "energy"]
    missing = [k for k in keys if not (specs / f"lifestyle_{k}.md").exists()]
    assert not missing, f"нет lifestyle-промптов: {missing}"


def test_lifestyle_prompt_fills_profile():
    p = cr.lifestyle_prompt("sleep", "БРИФ_XYZ_МАРКЕР")
    assert "%%PATIENT_PROFILE%%" not in p, "плейсхолдер не подставлен"
    assert "БРИФ_XYZ_МАРКЕР" in p


def test_lifestyle_prompt_fallback_unknown_domain():
    p = cr.lifestyle_prompt("nope_domain", "")
    assert "nope_domain" in p  # fallback, без падения


def test_lifestyle_single_source_no_inline_in_monthly():
    root = Path(__file__).resolve().parents[2]
    monthly = (root / "monthly_consilium.py").read_text(encoding="utf-8")
    # инлайн-роли lifestyle убраны
    assert "Ты эксперт по качеству сна" not in monthly, "остался инлайн lifestyle-промпт"
    assert "SPECIALISTS_LIFESTYLE" not in monthly, "остался инлайн-список lifestyle"
    # источник — единый ростер
    assert "consilium_roster.LIFESTYLE" in monthly


def test_lifestyle_agents_use_shared_loader():
    root = Path(__file__).resolve().parents[2]
    agents = (root / "lifestyle_agents.py").read_text(encoding="utf-8")
    assert "consilium_roster.lifestyle_prompt" in agents, "агенты не используют единый загрузчик"
