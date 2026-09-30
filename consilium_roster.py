#!/usr/bin/env python3.11
"""consilium_roster.py — ЕДИНЫЙ источник состава консилиума (мед. специалисты).

Правило (2026-07-01): берём ВСЕХ медицинских специалистов, исключаем только
ЯВНО неподходящих по демографии (пол/возраст). Убирает split-brain двух
захардкоженных списков (wellally_consult, monthly_consilium) — оба зовут отсюда.

Имена = имена файлов `specialists/<name>.md`. Lifestyle-коучи (4) добавляются
модулями отдельно и демографией НЕ фильтруются.

Публичный вход: medical_roster(profile=None) -> list[str]; roster_for(sex, age) — чистое ядро.
"""
# INTENT: consilium — консилиум: спор, а не голосование (единый источник состава).
#          Замысел и инварианты — subsystem_intent.yaml, раздел consilium.
from __future__ import annotations
from _time_inject import get_today  # seam

# Полный набор медицинских специалистов (= specialists/<name>.md).
_MEDICAL_ALL = [
    "cardiology", "dermatology", "endocrinology", "gastroenterology", "general",
    "geriatrics", "gynecology", "hematology", "nephrology", "neurology",
    "oncology", "orthopedics", "pediatrics", "psychiatry", "respiratory", "urology",
]

_MALE = {"m", "male", "муж", "мужской", "м"}
_FEMALE = {"f", "female", "жен", "женский", "ж"}


def sex_label(sex: str | None) -> str:
    """Пол из профиля (identity.sex) словом для промпта. Один дом разбора пола — тот же, что у
    ростера. Неизвестное значение не угадывается: «пол не указан», а не мужской по умолчанию."""
    s = (sex or "").strip().lower()
    if s in _MALE:
        return "мужской"
    if s in _FEMALE:
        return "женский"
    return "пол не указан"


def roster_for(sex: str | None = None, age: int | None = None) -> list[str]:
    """Медицинский ростер = все минус ЯВНО неподходящие по полу/возрасту.
    Неизвестные пол/возраст → НЕ исключаем (правило: выбрасываем только точно неподходящих)."""
    s = (sex or "").strip().lower()
    out = []
    for spec in _MEDICAL_ALL:
        if spec == "pediatrics" and age is not None and age >= 18:
            continue
        if spec == "geriatrics" and age is not None and age < 65:
            continue
        if spec == "gynecology" and s in _MALE:
            continue
        out.append(spec)
    return out


def _age_from_birth(birth_date: str | None):
    if not birth_date:
        return None
    try:
        from datetime import date
        b = date.fromisoformat(str(birth_date)[:10])
        return (get_today() - b).days // 365
    except Exception:
        return None


def medical_roster(profile: dict | None = None) -> list[str]:
    """Ростер по профилю тенанта. profile = get_profile_context()-dict; None → читает из БД.
    Пол/возраст берём из identity.{sex,birth_date}."""
    if profile is None:
        try:
            import health_db
            profile = health_db.get_profile_context()
        except Exception:
            profile = {}
    ident = (profile or {}).get("identity", {}) or {}
    return roster_for(ident.get("sex"), _age_from_birth(ident.get("birth_date")))


# ── Lifestyle-коучи ───────────────────────────────────────────────────────────
# Единый список (display, ключ-домена → specialists/lifestyle_<key>.md).
# Демографией НЕ фильтруются (в отличие от медицинских). Единый источник для
# wellally (lifestyle_agents) и monthly_consilium — свёл 4↔5 расхождение и
# инлайн-промпты monthly к каноническим файлам.
LIFESTYLE = [
    ("Sleep Coach",    "sleep"),
    ("Movement Coach", "movement"),
    ("Stress Coach",   "stress"),
    ("Energy Coach",   "energy"),
]


def lifestyle_prompt(domain_key: str, patient_brief: str = "") -> str:
    """Роль lifestyle-коуча из specialists/lifestyle_<key>.md с подстановкой
    %%PATIENT_PROFILE%%. DB-free: профиль передаёт вызывающий. Fallback если файла нет."""
    from pathlib import Path
    p = Path(__file__).parent / "specialists" / f"lifestyle_{domain_key}.md"
    if not p.exists():
        return (f"Ты — lifestyle-коуч по домену {domain_key}. "
                "Анализируй строго по своей области, отвечай по-русски, конкретно.")
    txt = p.read_text(encoding="utf-8")
    return txt.replace("%%PATIENT_PROFILE%%", patient_brief or "(профиль недоступен)")
