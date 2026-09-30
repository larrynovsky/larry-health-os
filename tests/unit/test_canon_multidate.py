"""Одна дата на (документ, аналит, материал) — датчик П-9.

Повод 2026-08-09: в каноне нашлись строки-фантомы — полные дубли строк того же
PDF под другой датой и без референсов. Причина в форме бланка: страница
«продолжение, стр. 2 из 2» без шапки, единственная дата на ней в подвале
(`Дата выполнения исследования: <дата>` — когда отработал прибор), а дата
забора (`Взятие биоматериала: <дата> 08:00`) осталась на предыдущей странице.
Даты и значения ниже — синтетика той же формы.
"""
import integrity_tests as it

DOC = "doc:lab/panel.pdf"


def test_phantom_date_is_caught():
    """ПОЗИТИВНЫЙ КОНТРОЛЬ: ровно тот дефект, ради которого датчик заведён."""
    got = it.multidate_offenders([
        (DOC, "Troponin_I", "blood", "2021-03-10"),
        (DOC, "Troponin_I", "blood", "2021-03-14"),
    ])
    assert got == [((DOC, "Troponin_I", "blood"), ["2021-03-10", "2021-03-14"])]


def test_cleaned_canon_is_silent():
    """После удаления фантома датчик обязан замолчать — иначе он не про этот дефект."""
    assert it.multidate_offenders([(DOC, "Troponin_I", "blood", "2021-03-10")]) == []


def test_two_rows_same_date_are_NOT_an_offence():
    """НЕГАТИВНЫЙ КОНТРОЛЬ. В бланке такой формы CRP за одну дату лежит дважды:
    при потолке 1.0 и при потолке 5.0 — два разных теста. Датчик про ДАТЫ, и
    уникальный индекс по (source, test_name, specimen) запретил бы законное."""
    assert it.multidate_offenders([
        (DOC, "CRP", "blood", "2021-03-10"),
        (DOC, "CRP", "blood", "2021-03-10"),
    ]) == []


def test_specimen_stays_in_the_key():
    """НЕГАТИВНЫЙ КОНТРОЛЬ, стоивший мне двух ложных тревог: калий мочи и калий
    крови — разные ряды. Выпадет `specimen` из ключа — этот тест покраснеет."""
    assert it.multidate_offenders([
        (DOC, "Potassium", "blood", "2021-03-10"),
        (DOC, "Potassium", "urine", "2021-03-12"),
    ]) == []


def test_different_documents_do_not_mix():
    """Границей служит документ: два бланка законно меряют одно в разные дни."""
    assert it.multidate_offenders([
        (DOC, "Glucose", "blood", "2021-03-10"),
        ("doc:other.pdf", "Glucose", "blood", "2022-05-10"),
    ]) == []


def test_declarations_are_empty_because_the_row_is_gone():
    """Объявление живёт только пока существует его причина.

    После удаления исходной строки исключение нужно убрать: переиспользуемый
    ключ иначе начнёт скрывать уже другую запись."""
    assert it._MULTIDATE_DECLARED == {}, (
        "непустой словарь объявлений обязан ссылаться на ЖИВУЮ причину — "
        "проверь, что строка ещё в каноне, иначе это тихий глушитель")


def test_declaration_actually_silences_that_key_and_only_it(monkeypatch):
    """МЕХАНИЗМ ратчета проверяется подставленным объявлением, а не содержимым
    словаря: иначе тест умирает вместе с последней записью и ратчет остаётся
    без оракула ровно тогда, когда о нём забудут."""
    rows = [(DOC, "X", "blood", "2023-01-01"), (DOC, "X", "blood", "2023-02-02"),
            (DOC, "Y", "blood", "2023-01-01"), (DOC, "Y", "blood", "2023-02-02")]
    found = it.multidate_offenders(rows)
    assert len(found) == 2, "оба ключа обязаны быть находками до объявления"
    declared = {("health", DOC, "X", "blood"): "причина по бланку, проверяемая глазами"}
    monkeypatch.setattr(it, "_MULTIDATE_DECLARED", declared)
    undeclared = [(k, d) for k, d in it.multidate_offenders(rows)
                  if ("health", k[0], k[1], k[2]) not in it._MULTIDATE_DECLARED]
    assert [k[1] for k, _ in undeclared] == ["Y"], "объявление обязано гасить ТОЛЬКО свой ключ"


if __name__ == "__main__":
    for n, f in sorted(globals().items()):
        if n.startswith("test_"):
            f()
    print("П-9: все контроли зелёные")
