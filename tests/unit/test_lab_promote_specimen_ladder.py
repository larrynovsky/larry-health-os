"""Лестница материала в промоуте: прочитанное из бланка старше выведенного.

Негативные контроли проверяют приоритет прочитанного материала над
эвристикой панели. При конфликте полей имя панели не должно менять материал.
"""
import lab_promote


def test_class_from_raw_blank_string():
    assert lab_promote.specimen_class("Кровь с ЭДТА") == "blood"
    assert lab_promote.specimen_class("Кровь (сыворотка) -20С") == "blood"
    assert lab_promote.specimen_class("Моча (разовая)") == "urine"
    assert lab_promote.specimen_class("Кал") == "stool"
    assert lab_promote.specimen_class("Слюна") == "saliva"
    assert lab_promote.specimen_class("мазке из зева") == "throat_swab"
    assert lab_promote.specimen_class("Serum") == "blood"
    assert lab_promote.specimen_class("EDTA Whole Blood") == "blood"


def test_two_tubes_of_one_draw_are_one_class():
    """Классы КРУПНЫЕ намеренно: развести пробирки значило бы получить два
    тренда одного аналита у человека, сдавшего один анализ."""
    assert lab_promote.specimen_class("Кровь с ЭДТА") == \
           lab_promote.specimen_class("Кровь (сыворотка)") == \
           lab_promote.specimen_class("Кровь с гепарином")


def test_unrecognised_raw_is_none_not_other():
    """None ≠ 'other': неузнанное имя материала обязано быть отличимо от
    «панель ничего не сказала», иначе новый материал войдёт молча."""
    assert lab_promote.specimen_class("Ликвор") is None
    assert lab_promote.specimen_class("") is None
    assert lab_promote.specimen_class(None) is None


def test_read_material_beats_panel():
    """НЕГАТИВНЫЙ КОНТРОЛЬ. Прежняя ветка отдала бы здесь `stool` (panel=microbiome)."""
    row = {"panel": "microbiome", "specimen": "мазке из зева",
           "specimen_source": "read_footer"}
    assert lab_promote.specimen_of(row) == "throat_swab"


def test_panel_still_answers_when_blank_is_silent():
    """Бланк без поля материала (такие бывают у двухстраничных бланков) — прежняя ступень жива."""
    row = {"panel": "microbiome", "specimen": None, "specimen_source": "unknown"}
    assert lab_promote.specimen_of(row) == "stool"


def test_unknown_step_does_not_let_material_through():
    """НЕГАТИВНЫЙ КОНТРОЛЬ сцепки: материал без ступени чтения не считается
    прочитанным, даже если строка в колонке есть. Иначе ступень была бы украшением."""
    row = {"panel": "microbiome", "specimen": "мазке из зева",
           "specimen_source": "unknown"}
    assert lab_promote.specimen_of(row) == "stool"


def test_unrecognised_read_material_falls_back_not_crashes():
    row = {"panel": "chemistry", "specimen": "Ликвор", "specimen_source": "read_header"}
    assert lab_promote.specimen_of(row) == "blood"   # ступень панели


def test_urine_name_rule_survives():
    row = {"panel": "other", "canonical_name": "Urine_RBC"}
    assert lab_promote.specimen_of(row) == "urine"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
    print("лестница материала: все контроли зелёные")
