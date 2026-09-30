"""§20-контроль для specialized_canon_waiting (нить data-ingestion, FU2, п.3).

Судит МЕХАНИЗМ разложения канон-ждущих строк по идентичности ИМЯ+МАТЕРИАЛ+РАЗМЕРНОСТЬ,
не живой канон: строки и canon_keys инъектируются. Идентичность живёт в ОДНОМ доме —
`lab_canon.identity_name` (2026-08-12); ловушки, найденные сверкой с бланками
(моча-WBC, Инсулин %, RDW %/фл), проверены как НЕ-stuck — иначе сенсор назвал бы
разную величину «уже в каноне» и его red-сигнал стал бы ложным.
"""
from __future__ import annotations

import pytest

import integrity_tests as IT
import lab_canon as LC

pytestmark = pytest.mark.unit


def _ckey(name, spec, unit):
    """Ключ канона тем же правилом, что строит датчик: (identity_name, specimen)."""
    ident = LC.identity_name(name, unit)
    assert ident is not None, "фикстура обязана давать сводимую строку канона"
    return (ident, spec)


def test_stuck_identity_already_in_canon():
    canon = {_ckey("CA 242", "blood", "Ед/мл")}
    st = IT.specialized_canon_waiting([("tumor_markers", "CA 242", "blood", "Ед/мл")], canon)
    assert st["stuck"] == 1 and st["pending"] == 0 and st["unresolvable"] == 0


def test_stuck_canon_holds_resolved_name():
    """Канон принял под сведённым именем (`CA242`), спец-строка несёт сырое
    `CA 242` — идентичность одна, дубль обязан быть виден (замер 2026-08-12:
    именно так дубли висели невидимыми для guard)."""
    canon = {_ckey("CA242", "blood", "Ед/мл")}
    st = IT.specialized_canon_waiting([("tumor_markers", "CA 242", "blood", "Ед/мл")], canon)
    assert st["stuck"] == 1


def test_stuck_suffixed_dimension_twin():
    """Слепота ПРЕЖНЕГО ключа `(normalize, spec, dimension_key)`: канон держит
    `Immature_granulocytes_abs`, спец-строка — «Незрелые гранулоциты (IG)»
    10^9/л. Идентичность одна; старый ключ нёс имя дважды и несогласованно,
    звал это pending — такие дубли висели невидимыми."""
    canon = {_ckey("Immature_granulocytes_abs", "blood", "10^9/л")}
    st = IT.specialized_canon_waiting(
        [("cbc", "Незрелые гранулоциты (IG)", "blood", "10^9/л")], canon)
    assert st["stuck"] == 1


def test_trap_urine_leukocytes_not_stuck():
    # канон имеет WBC КРОВИ; строка — лейкоциты осадка МОЧИ. Материал разводит: не баг.
    canon = {_ckey("Лейкоциты", "blood", "10^9/л")}
    st = IT.specialized_canon_waiting([("urine", "Лейкоциты (колич.)", "urine", "кл/мкл")], canon)
    assert st["stuck"] == 0 and st["pending"] == 1


def test_trap_percent_insulin_not_stuck():
    """Гард голого %: канон держит ГОРМОН (мкМЕ/мл), строка — анти-инсулиновое
    антитело в % (один бланк: антитело в % при гормоне Insulin в мкМЕ/мл).
    Краснеет при снятии гарда в `identity_name` — исполняемый негативный контроль."""
    canon = {_ckey("Инсулин", "blood", "мкМЕ/мл")}
    st = IT.specialized_canon_waiting([("autoantibodies", "Инсулин", "blood", "%")], canon)
    assert st["stuck"] == 0
    assert st["pending"] == 1


def test_trap_rdw_percent_and_fl_not_glued():
    """RDW печатается дважды: в % (RDW-CV) и во фл (RDW-SD) — обе величины
    настоящие. Голый % на RDW к идентичности не сводится → %-строка не
    склеивается с фл-строкой канона (ложный дроп был бы потерей измерения)."""
    canon = {_ckey("RDW", "blood", "фл")}
    st = IT.specialized_canon_waiting([("cbc", "RDW", "blood", "%")], canon)
    assert st["stuck"] == 0 and st["pending"] == 1


def test_identity_guard_oracle_direct():
    """Оракул гарда напрямую (краснеет на поломке механизма, не окружения §20)."""
    assert LC.identity_name("Инсулин", "%") is None          # антитело ≠ гормон
    assert LC.identity_name("RDW", "%") is None              # RDW-CV не судим авто
    # объявленная пара abs/pct: % законен и уточняет имя
    assert LC.identity_name("Незрелые гранулоциты (IG)", "%") == "Immature_granulocytes_pct"
    # процентность в самом имени (`_pct`): % законен
    assert LC.identity_name("Neutrophils %", "%") == "Neutrophils_pct"
    # безразмерный по природе индекс: единицы нет, и это не потеря
    assert LC.identity_name("INR", "") == "INR"
    # незнакомое имя не судится вовсе
    assert LC.identity_name("Зюзюблик обыкновенный", "мг/л") is None


def test_unresolvable_is_naming_debt():
    st = IT.specialized_canon_waiting(
        [("metabolomics", "неведомый метаболит", "blood", "мкмоль/л")], set())
    assert st["unresolvable"] == 1 and st["stuck"] == 0 and st["pending"] == 0


def test_negative_control_garbage_not_resolvable():
    # НЕГАТИВНЫЙ КОНТРОЛЬ: мусор не смеет считаться сводимым (normalize возвращает вход).
    st = IT.specialized_canon_waiting([("other", "Зюзюблик обыкновенный", "blood", "%")], set())
    assert st["unresolvable"] == 1


def test_name_resolves_but_identity_absent_is_pending():
    st = IT.specialized_canon_waiting(
        [("tumor_markers", "CA 242", "blood", "Ед/мл")], set())   # канон пуст
    assert st["stuck"] == 0 and st["pending"] == 1


def test_empty_is_zero():
    assert IT.specialized_canon_waiting([], set()) == {
        "stuck": 0, "pending": 0, "unresolvable": 0, "by_panel": {}, "total": 0}
