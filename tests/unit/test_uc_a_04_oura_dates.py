"""
UC-A-04 — Oura: сон по дате пробуждения, активность/HRV по правильной дате.

Источник: USE_CASES.md §4.A → UC-A-04 (alias `UC-OURA-001`).
Реализация: `import_oura.py` (parse_sleep/parse_activity/parse_readiness).
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

from import_oura import (
    parse_sleep,
    parse_readiness,
    parse_activity,
)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_sleep_session(day: str, total_sec: int = 27000,
                         deep: int = 2700, rem: int = 5400,
                         type_: str = "long_sleep") -> dict:
    return {
        "day": day,
        "type": type_,
        "total_sleep_duration": total_sec,
        "deep_sleep_duration": deep,
        "rem_sleep_duration": rem,
        "light_sleep_duration": total_sec - deep - rem,
        "awake_time": 1800,
        "bedtime_start": f"{day}T22:30:00",
        "bedtime_end": f"{day}T06:30:00",
    }


def _make_score(day: str, score: int = 75) -> dict:
    return {"day": day, "score": score, "contributors": {
        "deep_sleep": 60, "rem_sleep": 70, "efficiency": 80,
        "timing": 75, "restfulness": 65, "total_sleep": 80,
    }}


# ── E (cross-check): структура ─────────────────────────────────────────────


def test_parse_sleep_uses_day_as_key():
    """Сон, завершившийся 8 мая утром, → ключ 2026-05-08."""
    sessions = [_make_sleep_session("2026-05-08")]
    scores = [_make_score("2026-05-08")]
    res = parse_sleep(sessions, scores)
    assert "2026-05-08" in res
    assert "2026-05-07" not in res, \
        "сон не должен класться на дату ДО пробуждения"


def test_parse_sleep_converts_seconds_to_hours():
    """27000с = 7.5ч."""
    sessions = [_make_sleep_session("2026-05-08", total_sec=27000)]
    res = parse_sleep(sessions, [_make_score("2026-05-08")])
    assert res["2026-05-08"]["totalSleep"] == pytest.approx(7.5, rel=1e-3)


def test_parse_sleep_filters_short_sessions():
    """Сессия <1 часа → отбрасывается."""
    sessions = [_make_sleep_session("2026-05-08", total_sec=3000)]  # 50 мин
    res = parse_sleep(sessions, [])
    assert "2026-05-08" not in res


def test_parse_sleep_ignores_naps():
    """type='nap' / 'deep_nap' / 'rest' — не попадают в результат."""
    sessions = [
        _make_sleep_session("2026-05-08", total_sec=3600, type_="nap"),
        _make_sleep_session("2026-05-08", total_sec=3600, type_="rest"),
    ]
    res = parse_sleep(sessions, [])
    assert "2026-05-08" not in res


def test_parse_sleep_includes_long_sleep_and_sleep_types():
    sessions = [_make_sleep_session("2026-05-08", total_sec=25200, type_="long_sleep")]
    res = parse_sleep(sessions, [_make_score("2026-05-08")])
    assert res["2026-05-08"]["totalSleep"] == pytest.approx(7.0, rel=1e-3)


def test_parse_sleep_picks_longest_session_per_day():
    """Если несколько сессий за один day — берём с большим total_sleep_duration."""
    sessions = [
        _make_sleep_session("2026-05-08", total_sec=18000),  # 5ч
        _make_sleep_session("2026-05-08", total_sec=27000),  # 7.5ч
    ]
    res = parse_sleep(sessions, [_make_score("2026-05-08")])
    assert res["2026-05-08"]["total_sec"] == 27000


def test_parse_sleep_includes_score():
    sessions = [_make_sleep_session("2026-05-08")]
    scores = [_make_score("2026-05-08", score=82)]
    res = parse_sleep(sessions, scores)
    assert res["2026-05-08"]["sleep_score"] == 82


def test_parse_sleep_no_score_returns_none():
    """Если нет scores — sleep_score=None, но запись остаётся."""
    sessions = [_make_sleep_session("2026-05-08")]
    res = parse_sleep(sessions, [])
    assert res["2026-05-08"]["sleep_score"] is None


# ── parse_activity / parse_readiness: ключ = day ─────────────────────────


def test_parse_activity_uses_day_as_key():
    data = [{"day": "2026-05-07", "score": 80, "steps": 8000,
             "active_calories": 400, "total_calories": 2200,
             "equivalent_walking_distance": 5500,
             "low_activity_time": 100, "medium_activity_time": 60,
             "high_activity_time": 30, "sedentary_time": 600,
             "non_wear_time": 0, "resting_time": 200, "average_met_minutes": 50,
             "contributors": {}}]
    res = parse_activity(data)
    assert "2026-05-07" in res, "активность должна быть на дату day"


def test_parse_readiness_uses_day_as_key():
    data = [{"day": "2026-05-08", "score": 75, "temperature_deviation": 0.1,
             "temperature_trend_deviation": 0.05,
             "contributors": {"hrv_balance": 70, "sleep_balance": 75,
                              "previous_night": 80, "activity_balance": 70,
                              "previous_day_activity": 60, "recovery_index": 75,
                              "resting_heart_rate": 80, "body_temperature": 70,
                              "sleep_regularity": 75}}]
    res = parse_readiness(data)
    assert "2026-05-08" in res


# ── B: AST-проверка что date.today() в logic-коде нет (только CLI) ──────────


def test_oura_logic_does_not_use_date_today():
    """`date.today()` встречается только в `if __name__ == "__main__":` блоке."""
    src = (Path(__file__).parents[2] / "import_oura.py").read_text(encoding="utf-8")
    tree = ast.parse(src)

    main_block_lineno = None
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.If):
            test_src = ast.unparse(node.test) if hasattr(ast, "unparse") else ""
            if "__name__" in test_src and "__main__" in test_src:
                main_block_lineno = node.lineno
                break

    today_calls = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            try:
                src_call = ast.unparse(node.func)
            except Exception:
                continue
            if src_call == "date.today":
                today_calls.append(node.lineno)

    if not today_calls:
        return  # вообще нет — ОК

    if main_block_lineno is None:
        pytest.fail("date.today() в коде, но нет __main__ блока — все вызовы в logic, "
                     "это сломает test injection")

    # Все вызовы date.today() должны быть после if __name__ == "__main__":
    in_logic = [ln for ln in today_calls if ln < main_block_lineno]
    assert not in_logic, (
        f"date.today() в logic-коде (вне __main__) на строках {in_logic}. "
        f"Используй `_time_inject.get_today()` для тестируемости."
    )
