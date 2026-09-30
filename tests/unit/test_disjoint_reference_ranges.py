"""Оракул для disjoint_reference_ranges — чистой функции датчика склейки имён."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def test_disjoint_ranges_are_reported():
    """Вымышленный аналит с двумя непересекающимися интервалами требует проверки."""
    from integrity_tests import disjoint_reference_ranges
    rows = [("Bilirubin_total", "fixture_unit", "blood", 0.7, 1.1),
            ("Bilirubin_total", "fixture_unit", "blood", 2.6, 3.8)]
    got = disjoint_reference_ranges(rows)
    assert [k for k, _ in got] == [("Bilirubin_total", "fixture_unit", "blood")]


def test_overlapping_ranges_are_silent():
    """ГРАНИЦА: разные лаборатории дают слегка разные нормы одного аналита, и это
    норма жизни, а не склейка. Датчик, кричащий на неё, научат игнорировать."""
    from integrity_tests import disjoint_reference_ranges
    rows = [("HGB", "g/l", "blood", 130.0, 170.0),
            ("HGB", "g/l", "blood", 132.0, 173.0)]
    assert disjoint_reference_ranges(rows) == []


def test_same_name_different_specimen_is_not_compared():
    """Материал разводит ключи ДО сравнения: калий в моче и в крови имеет
    несовместимые нормы законно, и это не склейка имён."""
    from integrity_tests import disjoint_reference_ranges
    rows = [("Potassium", "mg/l", "blood", 132.6, 195.0),
            ("Potassium", "mg/l", "urine", 375.0, 6396.0)]
    assert disjoint_reference_ranges(rows) == []


def test_missing_bounds_are_skipped_not_guessed():
    """Односторонняя норма («< 5.0») не даёт интервала. Достраивать её нулём
    значило бы придумать данные — пропускаем."""
    from integrity_tests import disjoint_reference_ranges
    rows = [("CRP", "mg/l", "blood", None, 5.0),
            ("CRP", "mg/l", "blood", None, 1.0)]
    assert disjoint_reference_ranges(rows) == []


# ── Имя берётся из canon_of, а не из снимка canonical_name (2026-07-30) ──

def _prepare(rows):
    """Та же подготовка, что в check_lab_names_not_glued: имя выводится ЗАНОВО."""
    import lab_canon, lab_promote
    out = []
    for raw, canon_snapshot, unit, panel, lo, hi in rows:
        nm = lab_promote.canon_of({"raw_name": raw, "canonical_name": canon_snapshot})
        out.append((nm, lab_canon.norm_unit(unit),
                    lab_promote.specimen_of({"panel": panel, "canonical_name": nm}), lo, hi))
    return out


def test_устаревший_снимок_имени_не_создаёт_ложной_склейки():
    """Вымышленный устаревший снимок склеивает общий и прямой билирубин.
    Текущий канонизатор разводит имена; повторная проверка не даёт ложной тревоги."""
    from integrity_tests import disjoint_reference_ranges
    rows = [("прямой билирубин", "Bilirubin_total", "fixture_unit", "chemistry", 0.7, 1.1),
            ("билирубин общий", "Bilirubin_total", "fixture_unit", "chemistry", 2.6, 3.8)]
    assert disjoint_reference_ranges(_prepare(rows)) == []


def test_настоящая_склейка_всё_ещё_краснеет():
    """ПОЗИТИВНЫЙ КОНТРОЛЬ к правке выше: датчик не должен ослепнуть. Если сырые
    имена ведут в ОДИН канон, а нормы несовместимы — находка обязана быть."""
    from integrity_tests import disjoint_reference_ranges
    rows = [("общий билирубин", "Bilirubin_total", "fixture_unit", "chemistry", 0.7, 1.1),
            ("билирубин общий", "Bilirubin_total", "fixture_unit", "chemistry", 2.6, 3.8)]
    got = disjoint_reference_ranges(_prepare(rows))
    assert [k[0] for k, _ in got] == ["Bilirubin_total"], "датчик ослеп на настоящей склейке"
