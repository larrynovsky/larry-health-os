"""Имя, которого нет в словаре, перестаёт плодить дубли (нить loinc-name-home).

Придуманные названия с кириллическими и латинскими одиночными буквами
должны совпадать после фолдинга; целые слова остаются читаемыми.
"""
import lab_canon as lc


def test_homoglyph_single_letter_folds_to_latin():
    assert lc.normalize("Образец А (учебный)") == lc.normalize("Образец A (учебный)")
    assert lc.normalize("Проба В1 (учебная)") == lc.normalize("Проба B1 (учебная)")
    assert lc.normalize("Образец К2") == lc.normalize("Образец K2")
    assert lc.normalize("Проба Е") == lc.normalize("Проба E")
    assert lc.normalize("Образец С") == \
           lc.normalize("Образец C")


def test_trailing_footnote_stars_are_dropped():
    """Обе формы обязаны дать ОДНО имя — неважно, известное словарю или сырое."""
    assert lc.normalize("Хромогранин А**") == lc.normalize("Хромогранин А")
    assert lc.normalize("Нейронспецифическая енолаза*") == \
           lc.normalize("Нейронспецифическая енолаза")
    assert lc.normalize("ПСА свободный*") == lc.normalize("ПСА свободный")


def test_cyrillic_and_latin_spelling_find_the_SAME_dictionary_entry():
    """Фолдинг работает и ДО словаря: `Хромогранин A` латиницей обязан найти ту же
    статью, что кириллический вариант. Иначе одно имя расходится на каноническое
    и сырое — дубль, только теперь замаскированный канонизацией."""
    assert lc.normalize("Хромогранин A") == lc.normalize("Хромогранин А**")


def test_whole_russian_word_is_NOT_transliterated():
    """НЕГАТИВНЫЙ КОНТРОЛЬ. Фолди мы слово целиком, «Сорбит» стал бы «Copбит» —
    детерминированно, но нечитаемо. Правило узкое намеренно.
    Слова, которых нет в словаре, — иначе тест мерил бы канонизацию, а не фолдинг."""
    # Проба берёт слова без статьи в словаре: иначе она проверяла бы
    # канонизацию вместо фолдинга.
    for w in ("Сорбит", "Escherichia coli", "Сорбитол", "Церулоплазмин смесь"):
        assert lc.normalize(w) == w, w


def test_known_names_still_resolve_through_the_dictionary():
    """Правка трогает ТОЛЬКО ветку «имени нет в словаре». Известные имена обязаны
    ходить прежним путём, иначе фолдинг подменил бы канонизацию."""
    assert lc.normalize("Гемоглобин") == lc.normalize("Hemoglobin")
    assert lc.normalize("РЭА") == lc.normalize("CEA")


def test_folding_is_idempotent():
    once = lc.normalize("Образец А (учебный)")
    assert lc.normalize(once) == once


def test_stars_inside_the_name_survive():
    """Снимаем ТОЛЬКО хвостовую сноску: звёздочка внутри имени может быть частью
    обозначения, и терять её молча — та же потеря, что и дубль."""
    assert "*" in lc.normalize("Некий *маркер* особый")


def test_empty_and_none_do_not_crash():
    assert lc.normalize("") == ""
    assert lc.normalize(None) is None


if __name__ == "__main__":
    for n, f in sorted(globals().items()):
        if n.startswith("test_"):
            f()
    print("гомоглифы и сноски: все контроли зелёные")


def test_кириллическая_буква_во_ВХОДЕ_находит_канон():
    """Фолдинг входа и словаря должен быть одинаковым, иначе одно
    обозначение с разной письменностью буквы создаёт два ключа."""
    import lab_canon as C
    assert C.normalize("C-пептид") == "C_peptide"   # лат. C
    assert C.normalize("С-пептид") == "C_peptide"   # кир. С


def test_фолдинг_входа_не_меняет_уже_найденное():
    """НЕГАТИВНЫЙ КОНТРОЛЬ на порядок. Фолдинг входа уже пробовали в этой нити и он
    ломал соседей: «С-реактивный белок» переставал находить CRP, русские предлоги
    уезжали в латиницу. Ветка достижима ТОЛЬКО после промаха обычного поиска —
    значит умеет превращать «не знаю» в имя и не умеет менять найденное."""
    import lab_canon as C
    assert C.normalize("С-реактивный белок") == "CRP"
    assert C.normalize("Тестостерон") == "Testosterone"
    # 2026-08-01: «Тестостерон, связанный с альбумином» получил свой канон
    # (`Testosterone_albumin_bound`), поэтому как проба на СЫРОЙ возврат он больше
    # не годится. Утверждение то же, носитель другой — имя без статьи в словаре.
    assert C.normalize("Тестостерон, связанный с глобулином") != "Testosterone"
    # предлог «с» внутри имени остаётся кириллическим, а не превращается в латинское c
    assert "с глобулином" in C.normalize("Тестостерон, связанный с глобулином")
