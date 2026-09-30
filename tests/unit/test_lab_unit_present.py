"""Оракул датчика пустой единицы (loinc-name-home, шаг A).

Датчик обещает три вещи, и каждая тут проверяется отдельным покраснением:
подсунули строку без единицы после фенса — нашлась; убрали дефект любым из трёх
законных способов (единица · до фенса · безразмерный аналит) — не нашлась.

Почему тест на чистой функции, а не на живой БД: канон на MacBook пуст, а датчик
обязан краснеть до того, как дефект туда доедет.
"""
import integrity_tests as it
import lab_canon


FENCE = "2026-07-30"


def _row(created, name="Magnesium", value=0.96, unit=None):
    return (created, name, value, unit)


# ── позитивный контроль: дефект виден ──

def test_новая_строка_без_единицы_покраснела():
    found = it.unitless_offenders([_row("2026-08-01 10:00:00")], FENCE)
    assert len(found) == 1, "строка со значением и без единицы обязана быть найдена"
    assert found[0][1] == "Magnesium"


def test_дефект_убрали_единицей_позеленело():
    assert it.unitless_offenders([_row("2026-08-01 10:00:00", unit="mg/dL")], FENCE) == []


def test_пробел_за_единицу_не_считается():
    assert len(it.unitless_offenders([_row("2026-08-01", unit="   ")], FENCE)) == 1


# ── три законных отсева ──

def test_легаси_до_фенса_молчит():
    """Строки до границы контроля не держат датчик вечно красным."""
    assert it.unitless_offenders([_row("2026-07-04 12:00:00")], FENCE) == []


def test_строка_без_числа_молчит():
    assert it.unitless_offenders([_row("2026-08-01", value=None)], FENCE) == []


def test_безразмерный_аналит_молчит():
    assert it.unitless_offenders([_row("2026-08-01", name="Chol_HDL_ratio", value=3.5)], FENCE) == []


def test_безразмерный_узнаётся_и_по_сырому_имени():
    """Иначе отсев промахивается мимо ровно тех строк, ради которых он есть."""
    assert lab_canon.is_dimensionless("коэффициент атерогенности")
    assert it.unitless_offenders(
        [_row("2026-08-01", name="коэффициент атерогенности", value=2.5)], FENCE) == []


def test_обычный_аналит_безразмерным_не_считается():
    """Границу множества держим с ДВУХ сторон: иначе один лишний член в
    DIMENSIONLESS молча освобождает настоящую потерю единицы."""
    assert not lab_canon.is_dimensionless("Magnesium")
    assert not lab_canon.is_dimensionless("PSA_free_index")   # меряется в процентах


# ── обход фенса не должен существовать ──

def test_строка_без_времени_судится():
    """Не поставить created_at — иначе готовый способ обойти границу."""
    assert len(it.unitless_offenders([_row(None)], FENCE)) == 1


def test_пустой_фенс_судит_всё():
    """Режим подсчёта легаси: без границы видны и старые строки."""
    assert len(it.unitless_offenders([_row("2023-01-01")], "")) == 1
