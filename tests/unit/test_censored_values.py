"""Оператор сравнения: «< X» — потолок, а не результат.

Повод 2026-07-31: цензурированное значение на бланке (вида `Analyte  < X`, ниже
предела измерения прибора) ложилось в канон как точное число X — тренд
аналита завышался. Значения в тестах — синтетика той же формы.
"""
import integrity_tests as it
import lab_recognizer as lr


def test_clean_rows_are_silent():
    assert it.censored_value_offenders([(2.0, "<"), (5.1, None), (None, None),
                                        (0.014, ">"), (3.0, "<=")]) == []


def test_operator_without_value_is_caught():
    """ПОЗИТИВНЫЙ КОНТРОЛЬ: оператор без числа бессмыслен — «меньше чего?»."""
    assert it.censored_value_offenders([(None, "<")]) == [("оператор без значения", "<")]


def test_alien_operator_is_named_not_swallowed():
    """Множество закрыто: «≤» юникодом или «lt» вошли бы молча и разъехались с
    читателем, который сравнивает со строкой '<'."""
    got = it.censored_value_offenders([(2.0, "≤"), (2.0, "lt")])
    assert got == [("чужой оператор", "≤"), ("чужой оператор", "lt")]


def test_value_without_operator_is_NOT_an_offence():
    """НЕГАТИВНЫЙ КОНТРОЛЬ. Большинство строк — точные измерения, у них оператора
    нет и быть не должно. Требуй датчик оператор везде — он краснел бы всегда,
    и его перестали бы читать."""
    assert it.censored_value_offenders([(v, None) for v in (1, 2, 3, None)]) == []


def test_recognizer_prompt_asks_for_the_operator():
    """Починка в точке ГЕНЕРАЦИИ, а не заплатка на данных: без этого поля
    следующий бланк с цензурированным значением потеряет оператор ровно так же.

    Контроль слаб честно — он проверяет НАЛИЧИЕ требования в промпте, а не то,
    что модель ему следует. Последнее доказывается только живым бланком."""
    schema = lr._SCHEMA
    assert "value_op" in schema
    assert "< 2.0" in schema          # пример, а не абстрактное требование
    assert "ref_high" in schema and "НЕ путай" in schema.replace("Не путай", "НЕ путай")


if __name__ == "__main__":
    for n, f in sorted(globals().items()):
        if n.startswith("test_"):
            f()
    print("оператор сравнения: все контроли зелёные")


# ── F-20 (объявлен 2026-07-29, закрыт 2026-07-31) ──

def test_a19_1_censored_value_keeps_its_operator(tmp_path, monkeypatch):
    """СКВОЗНОЙ контроль дефекта F-20: оператор доезжает от staging до канона.

    Был объявлен ожидаемо-красным 29.07: носителя для «< 2.0» не существовало,
    и число-потолок попадало в канон как измерение (замер 31.07: точки тренда
    завышены).

    Тест сквозной НАМЕРЕННО: колонка, писатель и промоут по отдельности могут быть
    исправны, а знание всё равно потеряется на стыке — именно там оно и терялось.
    """
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path / "h"))
    monkeypatch.setenv("ALLOW_WRITE_NONPRIMARY", "1")
    (tmp_path / "h" / "data").mkdir(parents=True)
    import health_db
    monkeypatch.setattr(health_db, "DB_PATH", tmp_path / "h" / "data" / "health.db")
    health_db.init_db()
    # Схема канона — ПРОДАКШН-функциями (tests/conftest.py::canon_schema), а не копией
    # DDL здесь: копия разъезжается с продом молча (замер 2026-08-08 — value_text).
    from tests.conftest import canon_schema
    canon_schema(health_db)
    with health_db.get_conn() as c:
        cols = ("run_id,extractor_version,source_file,date,panel,canonical_name,"
                "raw_name,value,unit,value_agreement,review_status,value_op")
        c.executemany(
            f"INSERT INTO lab_results_staging({cols}) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", [
            ("r1", "v", "synthetic.pdf", "2021-03-15", "chemistry", "CRP", "CRP",
             0.5, "mg/L", "agree", "pending", "<"),
            ("r1", "v", "synthetic.pdf", "2021-03-15", "chemistry", "Ferritin", "Ferritin",
             48.2, "ng/mL", "agree", "pending", None),
        ])
        c.commit()

    import lab_promote
    lab_promote.plan("r1", None, execute=True)

    with health_db.get_conn() as c:
        got = dict(c.execute(
            "SELECT test_name, value_op FROM lab_results").fetchall())
        val = c.execute("SELECT value FROM lab_results WHERE test_name='CRP'").fetchone()[0]
    assert got["CRP"] == "<", "оператор потерян на пути staging → канон (F-20)"
    assert val == 0.5, "значение обязано доехать потолком, а не исчезнуть"
    assert got["Ferritin"] is None, "точному измерению оператор приписывать нельзя"
