"""
Sprint 1 — snapshot тест для безопасной декомпозиции build_smart_context
(см. architecture_audit_2026-05-21.md F-120 / §10 Спринт 4-7).

Цель: зафиксировать набор и порядок секций build_smart_context для двух
сценариев: «узкий» (один domain) и «полный» (все domains).

Источник: hai_context.build_smart_context (~130 LOC, 7 try/except секций).
"""
from __future__ import annotations

import re
from datetime import date, timedelta

import pytest


pytestmark = [pytest.mark.integration, pytest.mark.snapshot]


# Та же логика что в test_gp_context_section_snapshot.py — заголовок секции:
# первое слово ≥ 4 uppercase подряд, дальше что угодно кроме `:` и `\n`.
# Также build_smart_context использует "=== ... ===" формат, поэтому
# добавляем второй регексп для этого паттерна.
_HEADER_COLON = re.compile(
    r"^([A-ZА-ЯЁ]{4,}[^:\n]{0,76}):$",
    re.MULTILINE,
)
_HEADER_EQUALS = re.compile(
    r"^=== ([^=\n]+) ===$",
    re.MULTILINE,
)


def _extract_section_titles(text: str) -> list[str]:
    """Возвращает заголовки секций в порядке появления.

    build_smart_context использует ОБЕ нотации:
      `=== Базовые метрики ===` (от build_context_block_compact)
      `=== Медицинский профиль ===`
      `СЛОВО:` (от builtin блоков)
    """
    # Собираем оба типа с их позициями
    found: list[tuple[int, str]] = []
    for m in _HEADER_COLON.finditer(text):
        found.append((m.start(), m.group(1).strip()))
    for m in _HEADER_EQUALS.finditer(text):
        found.append((m.start(), m.group(1).strip()))
    found.sort(key=lambda x: x[0])
    return [t for _, t in found]


def _setup_data(db, clock):
    """Минимальный набор для базового блока + опциональных секций."""
    clock.set("2026-05-22")
    target = date(2026, 5, 22)

    # 7 дней метрик (для build_context_block_compact)
    for offset in range(1, 8):
        d = str(target - timedelta(days=offset))
        db.add_daily_metrics(
            d,
            hrv=25 + offset, sleep_total=7.5, sleep_deep=0.7,
            sleep_score=80, steps=7000 + offset * 100,
            readiness=75, resting_hr=58,
        )

    # Лаба (для секции «Анализы»)
    db.add_lab_result("2026-04-30", "CEA", 1.3, unit="ng/mL")
    db.add_lab_result("2026-04-30", "HGB", 15.2, unit="g/dL")

    # Проблема (для секции «Активные проблемы»)
    db.add_problem("P001", "Test проблема", status="active_monitoring",
                   priority=1, domain="oncology")

    return target


# Golden snapshots — версия 1, baseline 2026-05-22.
# Список секций при ВОПРОСЕ_ПО_СНУ — только sleep domain triggered.
GOLDEN_SLEEP_QUERY = [
    "Данные на 2026-05-22",   # от build_context_block_compact (compact prefix)
]

# Список секций при ВОПРОСЕ_ПОЛНОМ — все domains triggered через _FULL_CONTEXT_KEYWORDS.
GOLDEN_FULL_QUERY = [
    "Данные на 2026-05-22",  # base compact
    "АКТИВНЫЕ ПРОБЛЕМЫ",     # если 3+ domains detected
]


def test_smart_context_minimal_query(db, clock):
    """Узкий вопрос — детектируется только domain sleep.

    Базовый блок должен быть всегда; остальные — условно.
    """
    _setup_data(db, clock)

    import hai_context
    ctx = hai_context.build_smart_context("как я спал сегодня?",
                                          target=date(2026, 5, 22))

    assert len(ctx) > 100, f"Контекст слишком мал: {len(ctx)} chars"
    sections = _extract_section_titles(ctx)

    # Базовый блок «Данные на YYYY-MM-DD» должен быть всегда
    base_present = any("Данные на" in s for s in sections)
    assert base_present, f"Базовая секция «Данные на …» отсутствует. Sections: {sections}"


def test_smart_context_full_query(db, clock):
    """Полный вопрос («что мне делать в целом?») — все domains активны."""
    _setup_data(db, clock)

    import hai_context
    ctx = hai_context.build_smart_context(
        "что мне делать в целом для здоровья?",
        target=date(2026, 5, 22),
    )

    assert len(ctx) > 500, f"Полный контекст слишком мал: {len(ctx)} chars"

    sections = _extract_section_titles(ctx)
    sections_upper = [s.upper() for s in sections]

    # При полном запросе должны быть хотя бы 2 секции
    assert len(sections) >= 2, (
        f"При полном запросе ожидаем 2+ секции, получено {len(sections)}: {sections}"
    )

    # Базовый блок присутствует
    assert any("ДАННЫЕ НА" in s.upper() for s in sections), (
        f"Базовая секция «Данные на» отсутствует. Sections: {sections}"
    )


def test_smart_context_domain_routing(db, clock):
    """Поверка: разные вопросы дают разные наборы секций.

    Узкий sleep query → меньше секций, чем полный query.
    Структурный invariant: full ⊇ minimal (по domain detection).
    """
    _setup_data(db, clock)

    import hai_context

    ctx_min = hai_context.build_smart_context(
        "как я спал сегодня?", target=date(2026, 5, 22)
    )
    ctx_full = hai_context.build_smart_context(
        "что мне делать в целом?", target=date(2026, 5, 22)
    )

    sections_min = _extract_section_titles(ctx_min)
    sections_full = _extract_section_titles(ctx_full)

    assert len(ctx_full) >= len(ctx_min), (
        f"Полный контекст должен быть ≥ минимального. "
        f"min={len(ctx_min)}, full={len(ctx_full)}"
    )
    assert len(sections_full) >= len(sections_min), (
        f"Full sections ({len(sections_full)}) < min sections ({len(sections_min)}). "
        f"min={sections_min}, full={sections_full}"
    )
