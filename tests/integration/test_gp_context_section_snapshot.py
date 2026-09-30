"""
Sprint 1 — snapshot тест для безопасной декомпозиции _build_gp_context
(см. architecture_audit_2026-05-21.md F-092 / §10 Спринт 4).

Цель: зафиксировать **набор и порядок секций** вывода _build_gp_context
при «максимальном» наборе входных данных. Декомпозиция в Спринте 4
обязана сохранить этот snapshot — иначе разработчик должен явно решить,
что секция добавлена/удалена осознанно (обновить golden).

НЕ фиксирует содержимое секций (числа/даты стохастичны).

Charter: проверить, что после декомпозиции _build_gp_context в Спринт 4
все 14+ источников данных продолжают попадать в контекст GP.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
import json

import pytest


pytestmark = [pytest.mark.integration, pytest.mark.snapshot]


# Регексп для заголовка секции:
#   - первое слово ≥ 4 uppercase символа (русских или латинских) подряд,
#     отсеивает строки-данные типа "Сон:", "Bedtime:", "7d avg:";
#   - дальше может быть что угодно кроме `:` и `\n`;
#   - оканчивается на `:`.
_SECTION_HEADER = re.compile(
    r"^([A-ZА-ЯЁ]{4,}[^:\n]{0,76}):$",
    re.MULTILINE,
)


def _extract_sections(text: str) -> list[str]:
    """Возвращает список заголовков секций в порядке появления."""
    return [m.group(1).strip() for m in _SECTION_HEADER.finditer(text)]


# ── Golden snapshot — версия 1, baseline 2026-05-22 после Sprint 0 ──────────
# Если этот список меняется — разработчик обязан осознанно обновить.
# Не каждое изменение = регрессия (новая секция = feature), но каждое
# изменение требует подтверждения "Я хотел этого".
#
# Источник: фактический запуск _build_gp_context на тестовой БД
# с максимально заполненными данными (см. fixture _setup_maximal_data).
# Optional секции (mdt, longitudinal, specialist_review, genome) НЕ включены
# — для них нужно мокать внешние модули (отдельный тест в Sprint 1+).
GOLDEN_SECTIONS_V1 = [
    "АКТИВНЫЙ СПИСОК ПРОБЛЕМ (ИЗ БАЗЫ ДАННЫХ)",
    "LIFESTYLE (ДАТА | СОН | DEEP | ВСР | SCORE | ШАГИ)",
    "ТРЕНДЫ (7Д / 14Д / 30Д / 90Д AVG)",
    "СТРЕСС И НАГРУЗКА",
    "ЛАБОРАТОРНЫЕ ДАННЫЕ (ПОСЛЕДНИЕ ДОСТУПНЫЕ, ДО 2 ЛЕТ)",
    "АКТУАЛЬНОСТЬ ДАННЫХ",
    "АКТИВНЫЕ КЛИНИЧЕСКИЕ ПЕРИОДЫ",
    "МЕДИЦИНСКИЕ СОБЫТИЯ (ПОСЛЕДНИЕ 12 МЕС)",
]


def _setup_maximal_data(db, clock):
    """Заливаем достаточно данных, чтобы все «обязательные» секции
    появились в выводе.

    «Обязательные» = те, что появляются без условий на agent_reports/genome
    (которые требуют отдельной инициализации внешних модулей).
    """
    clock.set("2026-05-22")
    target = date(2026, 5, 22)

    # 7 дней метрик (для трендов и lifestyle секции)
    for offset in range(1, 15):
        d = str(target - timedelta(days=offset))
        db.add_daily_metrics(
            d,
            hrv=22 + (offset % 5),
            sleep_total=7.0 + (offset % 3) * 0.2,
            sleep_deep=0.7 + (offset % 4) * 0.05,
            sleep_rem=1.5,
            sleep_score=75 + (offset % 10),
            readiness=70 + (offset % 15),
            steps=6000 + (offset * 200),
            resting_hr=55 + (offset % 5),
            stress_high_min=120 + (offset * 5),
            recovery_high_min=180,
            stress_summary="normal",
            resilience_level="solid",
        )

    # Лабы (для блока ЛАБОРАТОРНЫЕ ДАННЫЕ — нужны 2+ key_labs)
    db.add_lab_result("2026-04-30", "CEA", 1.1, unit="ng/mL")
    db.add_lab_result("2026-04-30", "HGB", 14.6, unit="g/dL")
    db.add_lab_result("2026-04-30", "MCV", 90.1, unit="fL")
    db.add_lab_result("2026-04-30", "WBC", 6.2, unit="10³/µL")

    # Проблема (для блока АКТИВНЫЙ СПИСОК ПРОБЛЕМ)
    # Cyrillic-термин обязателен: clinical_kb.active_conditions матчит problem_list по
    # онко-триггеру (methodology/clinical_kb/_index.yaml: онко|…|ремисси|…); без него
    # онко-лабы не подтянулись бы (нить diagnosis-hardcode B6, per-tenant labs).
    # Диагноз синтетический.
    db.add_problem("P001", "Онкологический диагноз X, ремиссия",
                   status="active_monitoring", priority=1, domain="oncology")

    # Период (для блока АКТИВНЫЕ КЛИНИЧЕСКИЕ ПЕРИОДЫ)
    db.add_period("ремиссия после лечения X", type_="treatment",
                  start_date="2026-01-01")

    # Медицинское событие (для блока МЕДИЦИНСКИЕ СОБЫТИЯ)
    evt_id = db.add_event(
        event_type="encounter",
        effective_date="2026-04-15",
        status="completed",
        performer="Ivanov",
        performer_role="oncologist",
        location="Clinic",
        notes="плановый чекап",
    )
    db.add_encounter(event_id=evt_id, specialty="oncology",
                     assessment="Стабильная ремиссия")

    return target


def test_gp_context_section_snapshot_v1(db, clock):
    """Golden snapshot: набор и порядок «обязательных» секций
    _build_gp_context при максимально заполненных данных.

    Этот тест ловит регрессии при декомпозиции _build_gp_context в
    Спринт 4. Если набор/порядок секций изменится — разработчик должен
    осознанно обновить GOLDEN_SECTIONS_V1 (это решение, не cleanup).
    """
    target = _setup_maximal_data(db, clock)

    import gp_agent
    ctx = gp_agent._build_gp_context(target, period_days=7)

    actual_sections = _extract_sections(ctx)
    actual_upper = [s.upper() for s in actual_sections]

    # Проверка 1: все golden присутствуют (порядок проверяется через индекс)
    missing = [s for s in GOLDEN_SECTIONS_V1 if s not in actual_upper]
    assert not missing, (
        f"Из golden-секций пропали: {missing}\n"
        f"Фактический список: {actual_upper}"
    )

    # Проверка 2: порядок сохранён
    golden_present = [s for s in actual_upper if s in GOLDEN_SECTIONS_V1]
    assert golden_present == GOLDEN_SECTIONS_V1, (
        f"Порядок golden-секций нарушен.\n"
        f"Ожидался: {GOLDEN_SECTIONS_V1}\n"
        f"Получен:  {golden_present}"
    )


def test_gp_context_minimum_size_v1(db, clock):
    """При полностью заполненных данных контекст должен быть ≥ 2000 chars.

    Меньше — значит крупная секция выпала.
    """
    target = _setup_maximal_data(db, clock)

    import gp_agent
    ctx = gp_agent._build_gp_context(target, period_days=7)

    assert len(ctx) >= 2000, (
        f"Контекст слишком мал: {len(ctx)} chars < 2000. "
        f"Возможно секция отвалилась.\nПервые 500 chars:\n{ctx[:500]}"
    )
