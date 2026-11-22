"""Промоут не пускает в канон число без единицы (карточка 2026-10-21, LDL×5).

Датчик `check_lab_unit_present` требует единицу у строки канона моложе фенса;
писатель канона обязан исполнять ту же границу ДО записи, а не узнавать о ней
ночью. Значения выдуманы.
"""


def _row(**kw):
    base = {"unit": None, "_specimen": "blood", "_cname": "LDL", "canonical_name": "LDL",
            "value": 120.0, "_class": "chemistry", "_home": "canon"}
    base.update(kw)
    return base


def test_число_без_единицы_блокируется():
    import lab_promote
    assert lab_promote._block_reason(_row(unit=None)) == "no-unit"
    assert lab_promote._block_reason(_row(unit="  ")) == "no-unit"


def test_число_с_единицей_проходит():
    """НЕГАТИВНЫЙ КОНТРОЛЬ: гейт не должен стать отказом работать."""
    import lab_promote
    assert lab_promote._block_reason(_row(unit="mg/dL")) is None


def test_безразмерный_аналит_без_единицы_проходит():
    import lab_promote
    assert lab_promote._block_reason(_row(_cname="Chol_HDL_ratio", value=3.5)) is None


def test_качественный_результат_без_единицы_проходит():
    import lab_promote
    assert lab_promote._block_reason(
        _row(value=None, value_text="отрицательно", _vtext="negative")) is None

