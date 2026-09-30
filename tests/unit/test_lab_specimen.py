"""Характеризация lab_specimen: лестница «что бланк говорит о себе».

Тексты страниц — синтетика той же формы, что у многостраничного бланка
(вёрстка шапок, продолжений и подвалов; замер формы 2026-07-31).
Негативные контроли краснеют ровно на возврате прежнего поведения:
безусловного наследования шапки через границу отчёта.
"""
import lab_specimen


HEADER_BLOOD = (
    "Заявка №: 1000000001\nИсполнитель: ООО \"Лаборатория-Пример\"\n"
    "Биоматериал: Кровь с ЭДТА;\nВзятие биоматериала: 09.11.2020 07:30\n"
    "Микроэлементы (ИСП-МС)\nЛитий, Li***\n5,112\nмкг/л\n"
)
CONT_SAME_ORDER = (
    "Заявка №: 1000000001 (продолжение, стр. 2 из 3)\n"
    "Фамилия И.О.: ИВАНОВ И. И.\nМедь, Cu\n1043\nмкг/л\n"
)
CONT_OTHER_ORDER = (
    "Заявка №: 1000000002 (продолжение, стр. 2 из 3)\n"
    "Фамилия И.О.: ИВАНОВ И. И.\nБифидобактерии\n10^7\n"
)
FOOTER_BLOOD = (
    "Иванов И. И.  55 лет\n№Микроорганизм, 105 клеток/грамм\nПроба\nНорма\n"
    "Bacillus cereus\n0\n17\nРезультат одобрил\nПетрова А.А.\n"
    "Анализ микробиоты методом масс-спектрометрии микробных маркеров в крови\n"
)
FOOTER_THROAT = (
    "Иванов И. И.  55 лет\n№Микроорганизм, 105 клеток/грамм\nПроба\nНорма\n"
    "Bacillus cereus\n0\n29\nРезультат одобрил\nПетрова А.А.\n"
    "Анализ микробиоты методом масс-спектрометрии микробных маркеров в мазке из зева\n"
)
CHART_DYSBIOSIS = "Диаграмма дисбиоза\n№\nМикроорганизм\nБаланс\nBacillus cereus\n-17\n"
CHART_ORGANS = (
    "Оценка состояния основных органов и систем человека на основе изменений\n"
    "-50\n0\n70\nОБМ\nGFAP\nОтклонение от уровня индивидуальной нормы (%)\n"
    "Индивидуальные профили иммунореактивности\n"
)
ELI_PAGE = (
    "Результат лабораторного исследования не является диагнозом.\n"
    "Функционально-клинические характеристики\nGFAP\n0\n%\n"
)
HEADER_EN = "Patient: J. D.\nSample type: Serum\nGlucose\n5.1\nmmol/L\n"


def _facts(*pages):
    return lab_specimen.page_facts("не-читается", texts=list(pages))


def test_own_header_is_read():
    f = _facts(HEADER_BLOOD)
    assert f[1]["specimen"] == "Кровь с ЭДТА"
    assert f[1]["specimen_source"] == "read_header"


def test_english_header_is_read():
    f = _facts(HEADER_EN)
    assert f[1]["specimen"] == "Serum"
    assert f[1]["specimen_source"] == "read_header"


def test_continuation_of_same_order_inherits():
    f = _facts(HEADER_BLOOD, CONT_SAME_ORDER)
    assert f[2]["specimen"] == "Кровь с ЭДТА"
    assert f[2]["specimen_source"] == "continuation"


def test_footer_names_material_itself():
    f = _facts(HEADER_BLOOD, FOOTER_BLOOD)
    assert f[2]["specimen"] == "крови"
    assert f[2]["specimen_source"] == "read_footer"


def test_footer_wins_over_neighbour_header():
    """Негативный контроль: материал из подвала старше чужой шапки."""
    f = _facts(HEADER_BLOOD, FOOTER_THROAT)
    assert f[2]["specimen"] == "мазке из зева"
    assert f[2]["specimen_source"] == "read_footer"
    assert f[2]["specimen"] != "Кровь с ЭДТА"


def test_page_without_order_and_header_is_unknown():
    """НЕГАТИВНЫЙ КОНТРОЛЬ. Страница чужой вёрстки без шапки, без заявки и без
    подвала обязана остаться без материала, а не взять его у соседа."""
    f = _facts(HEADER_BLOOD, ELI_PAGE)
    assert f[2]["specimen"] is None
    assert f[2]["specimen_source"] == "unknown"


def test_continuation_of_other_order_does_not_inherit():
    """НЕГАТИВНЫЙ КОНТРОЛЬ. Номер заявки сверяется, а не игнорируется."""
    f = _facts(HEADER_BLOOD, CONT_OTHER_ORDER)
    assert f[2]["specimen"] is None
    assert f[2]["specimen_source"] == "unknown"


def test_chart_pages_get_derived_role():
    f = _facts(FOOTER_BLOOD, CHART_DYSBIOSIS, CHART_ORGANS)
    assert f[1]["role"] == "data"
    assert f[2]["role"] == "derived_chart"
    assert f[3]["role"] == "derived_chart"


def test_role_and_specimen_are_independent():
    """Диаграмма всё равно получает материал: роль решает, импортировать ли,
    материал — чей это результат. Смешать их значило бы потерять одно из двух."""
    f = _facts(HEADER_BLOOD, CONT_SAME_ORDER + CHART_DYSBIOSIS)
    assert f[2]["role"] == "derived_chart"
    assert f[2]["specimen"] == "Кровь с ЭДТА"


def test_unknown_page_does_not_poison_later_inheritance():
    """После страницы чужой вёрстки продолжение своей заявки по-прежнему наследует."""
    f = _facts(HEADER_BLOOD, ELI_PAGE, CONT_SAME_ORDER)
    assert f[3]["specimen"] == "Кровь с ЭДТА"
    assert f[3]["specimen_source"] == "continuation"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
    print("lab_specimen: все контроли зелёные")
