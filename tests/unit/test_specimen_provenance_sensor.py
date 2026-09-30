"""Датчик сцепки «материал ↔ его ступень» (integrity_tests).

Материал без ступени провенанса неотличим от материала, назначенного по имени
панели. Проверка требует обе части, чтобы неверная атрибуция не выглядела доказанной.
"""
import integrity_tests as it


def test_clean_rows_are_silent():
    rows = [("Кровь с ЭДТА", "read_header"),
            ("мазке из зева", "read_footer"),
            ("Кровь с ЭДТА", "continuation"),
            (None, "unknown"),
            ("", "unknown")]
    assert it.specimen_provenance_offenders(rows) == {
        "материал без ступени": 0, "ступень без материала": 0, "чужие ступени": []}


def test_material_without_step_is_caught():
    """ПОЗИТИВНЫЙ КОНТРОЛЬ. Ровно тот случай, ради которого датчик: материал
    в колонке есть, а откуда он взялся — не сказано."""
    rows = [("Кровь с ЭДТА", None), ("Кал", "unknown")]
    assert it.specimen_provenance_offenders(rows)["материал без ступени"] == 2


def test_step_without_material_is_caught():
    """Обратная сторона: ступень чтения обещает прочитанное, а его нет."""
    rows = [(None, "read_header"), ("   ", "read_footer")]
    assert it.specimen_provenance_offenders(rows)["ступень без материала"] == 2


def test_alien_step_is_named_not_swallowed():
    """Множество ступеней закрыто: чужое значение обязано быть НАЗВАНО, иначе
    новая ступень войдёт молча и датчик станет украшением."""
    rows = [("Кровь", "inherited")]     # ступень старого правила, снятая 2026-07-31
    got = it.specimen_provenance_offenders(rows)
    assert got["чужие ступени"] == ["inherited"]


def test_empty_input_is_clean_not_crash():
    assert it.specimen_provenance_offenders([]) == {
        "материал без ступени": 0, "ступень без материала": 0, "чужие ступени": []}


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
    print("датчик провенанса материала: все контроли зелёные")
