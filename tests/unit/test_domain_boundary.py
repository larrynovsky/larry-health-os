"""Оракул предикатов границы двух домов (работа B, 2026-08-01).

Краснеет при поломке `lab_canon.domain_boundary_violations` /
`same_measurement_two_dates`. Позитив и негатив раздельно по каждому пункту:
предикат, который ловит нарушение, но не умеет молчать на чистых данных,
негоден ровно так же, как обратный.
"""
import lab_canon


def _canon(date, name, specimen="blood", source="doc:x.pdf"):
    return {"date": date, "source": source, "specimen": specimen,
            "test_name": name}


def _spec(date, raw, specimen="blood", source="doc:x.pdf",
          canonical=None, panel="trace_elements"):
    return {"date": date, "source": source, "specimen": specimen,
            "analyte_raw": raw, "analyte_canonical": canonical,
            "panel_type": panel}


def test_boundary_clean_is_silent():
    """Негатив: разные материалы одного аналита — НЕ нарушение."""
    canon = [_canon("2032-04-12", "Ртуть, Hg", specimen="blood")]
    spec = [_spec("2032-04-12", "Ртуть, Hg", specimen="urine")]
    assert lab_canon.domain_boundary_violations(canon, spec) == []


def test_boundary_catches_same_row_in_two_homes():
    """Позитив: совпал материал — измерение живёт в двух домах."""
    canon = [_canon("2032-04-12", "Ртуть, Hg")]
    spec = [_spec("2032-04-12", "Ртуть, Hg")]
    hits = lab_canon.domain_boundary_violations(canon, spec)
    assert len(hits) == 1 and hits[0]["name"] == "Ртуть, Hg"


def test_boundary_dimension_distinguishes_measurements():
    """⭐ ADR (имя+материал+размерность): фракция электрофореза Albumin 60.2 «%»
    и биохимический Albumin 4.21 g/dL — РАЗНЫЕ измерения, флаг «два дома» на них
    ложный (FAIL датчика 2026-08-13 после починки спец-слоя)."""
    canon = [dict(_canon("2032-04-12", "Albumin"), unit="g/dL")]
    spec = [dict(_spec("2032-04-12", "Albumin", panel="electrophoresis"), unit="%")]
    assert lab_canon.domain_boundary_violations(canon, spec) == []


def test_boundary_same_dimension_still_flagged():
    """Позитив ADR: Albumin в г/л в обоих домах — одна идентичность, два
    владельца; метод в идентичность НЕ входит (решение владельца 2026-08-12)."""
    canon = [dict(_canon("2032-04-12", "Albumin"), unit="g/dL")]
    spec = [dict(_spec("2032-04-12", "Albumin", panel="electrophoresis"), unit="г/л")]
    hits = lab_canon.domain_boundary_violations(canon, spec)
    assert len(hits) == 1, "одинаковая размерность одного имени = одно измерение"


def test_boundary_unknown_units_keep_old_behavior():
    """Без единиц датчик сохраняет проверку имени и материала."""
    canon = [_canon("2032-04-12", "Ртуть, Hg")]
    spec = [_spec("2032-04-12", "Ртуть, Hg")]
    assert len(lab_canon.domain_boundary_violations(canon, spec)) == 1


def test_boundary_matches_canonical_name_too():
    """Позитив: канон переименовал строку — ключ обязан её всё равно узнать.

    Сырое имя вымышленной строки отличается от канонического написания;
    сохранённое каноническое имя должно связать оба дома.
    """
    canon = [_canon("2032-04-12", "Sodium")]
    spec = [_spec("2032-04-12", "Натрий, Na", canonical="Sodium")]
    assert len(lab_canon.domain_boundary_violations(canon, spec)) == 1


def test_boundary_matches_computed_name_when_canonical_is_empty():
    """Пустое analyte_canonical не отменяет сравнение по вычисленному имени.

    Русский синоним независимо выбранного аналита сводится к английскому имени;
    иначе две записи одного измерения останутся незамеченными.
    """
    canon = [_canon("2032-04-12", "TSH")]
    spec = [_spec("2032-04-12", "ТТГ", canonical=None, panel="hormones")]
    hits = lab_canon.domain_boundary_violations(canon, spec)
    assert len(hits) == 1, "сырое русское имя обязано сводиться к каноническому"


def test_boundary_sees_through_homoglyphs():
    """Позитив: кириллическая А и латинская A в вымышленном имени.

    Нормализация должна убрать различие алфавитов до сравнения ключей.
    """
    canon = [_canon("2032-04-12", "Fixture marker A")]
    spec = [_spec("2032-04-12", "Fixture marker А",
                  panel="microbiome")]
    assert len(lab_canon.domain_boundary_violations(canon, spec)) == 1


def test_boundary_names_delegate_to_normalize_not_a_copy():
    """Свойство, а не поведение: формы имени берутся у ОДНОГО дома.

    Если сведение имени скопируют сюда вместо вызова `normalize`, копия разъедется
    молча — и guard промоута (`lab_specialized._in_canon`), который берёт формы у
    этой же функции, ослепнет по-своему. Мутация: замени normalize на identity —
    два теста выше покраснеют.
    """
    got = lab_canon._boundary_names({"analyte_raw": "ТТГ",
                                     "analyte_canonical": None})
    assert "ТТГ" in got, "сырая форма обязана остаться"
    assert lab_canon.normalize("ТТГ") in got, "вычисленной формы нет в ключе"


def test_boundary_key_carries_material():
    """Негатив-мутация: убери материал из ключа — тест выше покраснеет.

    Пара кровь/моча вымышленного аналита с одной заявки — два измерения.
    Совпадение имени не отменяет различие материала.
    """
    canon = [_canon("2032-04-12", "Маркер Q", specimen="blood"),
             _canon("2032-04-12", "Маркер Q", specimen="urine")]
    spec = [_spec("2032-04-12", "Маркер Q", specimen="urine")]
    hits = lab_canon.domain_boundary_violations(canon, spec)
    assert [h["specimen"] for h in hits] == ["urine"]


def test_two_dates_silent_when_dates_agree():
    """Негатив: дата совпала — расхождения слоя нет."""
    canon = [_canon("2032-04-12", "Ртуть, Hg")]
    spec = [_spec("2032-04-12", "Ртуть, Hg")]
    assert lab_canon.same_measurement_two_dates(canon, spec) == []


def test_two_dates_catches_stale_layer():
    """Позитив: слой застыл на старой дате — оракул вердикта 4."""
    canon = [_canon("2032-04-12", "Ртуть, Hg")]
    spec = [_spec("2032-04-15", "Ртуть, Hg")]
    hits = lab_canon.same_measurement_two_dates(canon, spec)
    assert len(hits) == 1
    assert hits[0]["date_specialized"] == "2032-04-15"
    assert hits[0]["dates_canon"] == ["2032-04-12"]


def test_two_dates_silent_across_sources():
    """Негатив: разные документы — разные заборы, не расхождение слоя."""
    canon = [_canon("2032-04-12", "Ртуть, Hg", source="doc:a.pdf")]
    spec = [_spec("2026-05-30", "Ртуть, Hg", source="doc:b.pdf")]
    assert lab_canon.same_measurement_two_dates(canon, spec) == []


if __name__ == "__main__":
    for _n, _f in sorted(globals().items()):
        if _n.startswith("test_"):
            _f()
            print("ok", _n)
