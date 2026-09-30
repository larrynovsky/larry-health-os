"""tests/unit/test_lab_backfill_report_fields.py — согласие по полям ДОХОДИТ до человека.

Нить `data-ingestion`, решение владельца 29.07.2026 «сначала мерить, маршрут не трогать».

ЗАЧЕМ ЭТОТ ФАЙЛ ВООБЩЕ ЕСТЬ. Коммиты 94f6b5e/251ed82 завели `unit_agreement`,
`ref_agreement` и `field_evidence` — но замер, который никто не показывает, это не замер,
а строчка в таблице. `lab_backfill_report` — единственный путь, по которому владелец видит
качество прогона; до 29.07 у самого модуля не было НИ ОДНОГО теста (проверено:
`grep -rl lab_backfill_report tests/` пусто). Значит новый блок отчёта был бы «сделан» без
оракула, а это ровно то, что запрещено.

ЧТО ДОКАЗЫВАЕТ (два теста):
  1. когда расхождения по полям ЕСТЬ — они видны в отчёте вместе с сырой парой;
  2. когда поле NOT MEASURED (NULL у строк, прогнанных до 29.07) — отчёт говорит
     «не мерилось», а НЕ «расхождений нет».

NULL в колонках вердикта означает отсутствие измерения. Если отчёт
считает такие строки согласием, решение о маршруте опирается на артефакт
отчёта. Синтетическая смесь измеренных и неизмеренных строк ловит эту ошибку.

ЧЕГО НЕ ДОКАЗЫВАЕТ: что вердикт ПРАВИЛЬНЫЙ (это A17.1/A17.2/A17.5 в
tests/unit/test_recognizer_reconcile_scope.py) и что владелец отчёт прочитал.
"""
from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.unit

_COLS = ("run_id,extractor_version,source_file,date,panel,canonical_name,raw_name,"
         "value,unit,value_agreement,unit_agreement,ref_agreement,field_evidence,review_status")
_Q = f"INSERT INTO lab_results_staging({_COLS}) VALUES({','.join('?' * len(_COLS.split(',')))})"


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path / "health"))
    monkeypatch.setenv("ALLOW_WRITE_NONPRIMARY", "1")
    (tmp_path / "health" / "data").mkdir(parents=True)
    import health_db
    monkeypatch.setattr(health_db, "DB_PATH", tmp_path / "health" / "data" / "health.db")
    health_db.init_db()
    # `report()` сверяет покрытие со старой таблицей; init_db её не создаёт
    # (тот же приём, что в tests/unit/test_lab_billing_gate.py::db_promote).
    with health_db.get_conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS lab_results(
            id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, source TEXT,
            test_name TEXT, value REAL, unit TEXT, ref_low REAL, ref_high REAL,
            status TEXT, specimen TEXT)""")
        c.commit()
    return health_db


def _insert(health_db, rows):
    with health_db.get_conn() as c:
        c.executemany(_Q, rows)
        c.commit()


def test_field_disagreement_reaches_the_report(db):
    """Расхождение по единице видно в отчёте — с вердиктом И с сырой парой."""
    import lab_backfill_report

    ev = json.dumps({"unit": ["mg/dL", "mmol/L"]}, ensure_ascii=False)
    _insert(db, [
        ("r1", "v", "lab.pdf", "2026-07-29", "chem", "Glucose", "Glucose", 5.2, "mg/dL",
         "agree", "disagree", "agree", ev, "auto"),
        ("r1", "v", "lab.pdf", "2026-07-29", "chem", "Urea", "Urea", 5.0, "mmol/L",
         "agree", "agree", "agree", None, "auto"),
    ])
    out = lab_backfill_report.report("r1")

    assert "по ЕДИНИЦЕ" in out and "disagree: 1" in out, (
        "вердикт по единице не доехал до отчёта — колонка есть, замера для человека нет:\n"
        + out[:1500]
    )
    assert "mmol/L" in out and "Glucose" in out, (
        "сырая пара не показана: число без возможности посмотреть на пары — вера, а не "
        f"замер, и завышение из-за нотаций не отличить от настоящего расхождения:\n{out[:1500]}"
    )


def test_unmeasured_rows_do_not_read_as_zero_disagreements(db):
    """Строки БЕЗ замера (NULL) отчёт называет неизмеренными, а не согласными.

    Подсчёт NULL как нулевого расхождения создаёт ложный зелёный.
    Неизмеренная строка должна оставаться явно неизмеренной.

    ОРАКУЛ объявлен до тела: в блоке по единице стоит «не мерилось» и НЕ стоит «disagree: 0».
    """
    import lab_backfill_report

    _insert(db, [
        ("r2", "v", "old.pdf", "2026-07-01", "chem", "Glucose", "Glucose", 5.2, "mg/dL",
         "agree", None, None, None, "auto"),
        ("r2", "v", "old.pdf", "2026-07-01", "chem", "Urea", "Urea", 5.0, "mmol/L",
         "agree", None, None, None, "auto"),
    ])
    out = lab_backfill_report.report("r2")

    unit_line = next((ln for ln in out.splitlines() if "по ЕДИНИЦЕ" in ln), "")
    assert unit_line, f"блок согласия по единице исчез из отчёта:\n{out[:1500]}"
    assert "не мерилось" in unit_line, (
        f"неизмеренные строки поданы как измеренные: {unit_line!r}. "
        "NULL значит «никто не смотрел», а не «расхождений нет»"
    )
    assert "disagree: 0" not in unit_line, (
        f"отчёт утверждает ноль расхождений там, где замера не было: {unit_line!r}"
    )
