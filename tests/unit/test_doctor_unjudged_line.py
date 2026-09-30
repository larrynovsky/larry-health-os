"""Врачу — граница «не проверено порогом»; человеку — нет (решение владельца 28.09).

После нити safety-cannot-judge «судить нечем» не идёт человеку тревогой. У врача при этом
пропала граница: молчание предохранителя по аналиту читалось бы как «в норме». Строка
живёт в едином доме границ лаб-блока (labs_db.declared_boundary) и включается флагом
только врачебными контекстами: чат и агенты брифа (patient_context, hai_context) и
конституции (горизонт «от года») её не получают.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

pytestmark = pytest.mark.unit

ROOT = pathlib.Path(__file__).resolve().parents[2]
DOCTORS = {"gp_context.py", "wellally_consult.py", "consult_prep.py",
           "hypothesis_consilium_eval.py"}
PERSON_FACING = {"patient_context.py", "hai_context.py", "labs_db.py"}


def _record(items, day="2026-09-28"):
    import config_db
    import safety_net as sn
    config_db.upsert_config(sn.CANNOT_JUDGE_KEY, value_json={"date": day, "items": items})


def test_doctor_boundary_names_unjudged(db, clock):
    clock.set("2026-09-28")
    db.add_lab_result("2026-09-01", "CEA", 2.1, unit="ng/mL")
    _record([{"metric": "CEA", "source": "lab_trend", "note": "loinc_match.py"},
             {"metric": "HGB", "source": "norm_unresolved", "note": "x"}])
    import labs_db
    note = labs_db.declared_boundary(365, unjudged=True)
    assert "НЕ ПРОВЕРЕНО ПОРОГАМИ: CEA, HGB" in note
    assert "НЕ значит «в норме»" in note
    low = note.lower()
    assert "loinc" not in low and "отображ" not in low and ".py" not in low   # без служебного


def test_without_flag_no_line(db, clock):
    """Чат и агенты брифа зовут без флага — строки нет, даже если долг открыт."""
    clock.set("2026-09-28")
    db.add_lab_result("2026-09-01", "CEA", 2.1, unit="ng/mL")
    _record([{"metric": "CEA", "source": "lab_trend", "note": "x"}])
    import labs_db
    assert "НЕ ПРОВЕРЕНО" not in labs_db.declared_boundary(365)


def test_no_or_stale_record_no_line(db, clock):
    """Нет записи или она старая — не знаем, строки нет (а не «всё проверено»)."""
    clock.set("2026-09-28")
    db.add_lab_result("2026-09-01", "CEA", 2.1, unit="ng/mL")
    import labs_db
    assert "НЕ ПРОВЕРЕНО" not in labs_db.declared_boundary(365, unjudged=True)
    _record([{"metric": "CEA", "source": "lab_trend", "note": "x"}], day="2026-09-01")
    assert "НЕ ПРОВЕРЕНО" not in labs_db.declared_boundary(365, unjudged=True)


def _flags(fname):
    """Для каждого вызова declared_boundary в файле — передан ли unjudged=True."""
    tree = ast.parse((ROOT / fname).read_text())
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and getattr(n.func, "attr", getattr(n.func, "id", "")) \
                == "declared_boundary":
            out.append(any(k.arg == "unjudged" and getattr(k.value, "value", None) is True
                           for k in n.keywords))
    return out


def test_flag_only_in_doctor_contexts():
    """Периметр вычисляется по коду: каждый вызов у врачей — с флагом, у читателей,
    чей вывод видит человек, — без (иначе строка поедет в ежедневный бриф)."""
    for f in DOCTORS:
        fl = _flags(f)
        assert fl and all(fl), f
    for f in PERSON_FACING:
        assert not any(_flags(f)), f
