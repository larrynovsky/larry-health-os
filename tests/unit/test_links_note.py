"""Сообщение человеку о связях (решение владельца 25.09: «хочу видеть про связи»).
Оракул: пустота объявляется; новая связь помечена; ушедшая названа; слова о направлении по знаку."""
import pytest

pytestmark = pytest.mark.unit


def test_empty_is_stated():
    import longitudinal_analysis as la
    t = la.links_note([], [], 251, "2026-09-27")
    assert "проверено пар — 251" in t and "Подтверждённых связей нет" in t


def test_new_gone_and_direction():
    import longitudinal_analysis as la
    t = la.links_note(["a×b"], [("sleep_total×sleep_end", "Сон, ч", "Подъём", 0.23),
                                ("sleep_core×sleep_start", "Лёгкий сон", "Отбой", -0.5)], 251, "d")
    assert "Сон, ч ↔ Подъём: r=+0.23 (чем больше одно, тем больше другое) — новая" in t
    assert "тем меньше другое" in t
    assert "Больше не подтверждаются: a×b" in t


def test_no_history_marks_nothing_new():
    import longitudinal_analysis as la
    t = la.links_note(None, [("x×y", "X", "Y", 0.3)], 10, "d")
    assert "новая" not in t
