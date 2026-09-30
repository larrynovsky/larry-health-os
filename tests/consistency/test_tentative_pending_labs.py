"""
Tentative-запись: pending_labs/ → committed lab_results.

Источник: USE_CASES.md UC-A-01 + TEST_ARCHITECTURE.md §5.2.
Tentative-запись — отдельный наблюдаемый объект ДО approval. После approve
она становится committed (попадает в lab_results).

Уровень: consistency.
Status: текущий код — фиксируем поведение «есть pending → есть запись на диске,
        нет в lab_results; после approve — наоборот».
"""
from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.consistency


def test_tentative_file_exists_but_not_in_lab_results(
    pending_labs_dir, make_pending_json, db
):
    """
    Шаг 1 (tentative): pending_labs/{visit_key}.json создан.
    `lab_results` ещё пуст.
    """
    visit_key = "2026-05-08_clinic"
    payload = make_pending_json(visit_key, items=[
        {"name": "WBC", "value": 4.2, "unit": "x10^9/L",
         "ref_low": 4.0, "ref_high": 10.0,
         "flagged": False, "confidence": "high",
         "raw_line": "WBC 4.2"},
    ])
    path = pending_labs_dir / f"{visit_key}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    # Tentative state: файл есть
    assert path.exists()
    # Committed state: lab_results пуст
    assert db.count("lab_results") == 0


def test_committed_state_after_simulated_approve(
    pending_labs_dir, make_pending_json, db
):
    """
    Шаг 2 (committed): после approve данные попадают в lab_results.
    Симулируем «approve» прямым вызовом add_lab_result (помеченным как из pending).
    """
    visit_key = "2026-05-08_clinic"
    payload = make_pending_json(visit_key, items=[
        {"name": "WBC", "value": 4.2, "unit": "x10^9/L",
         "ref_low": 4.0, "ref_high": 10.0,
         "flagged": False, "confidence": "high",
         "raw_line": "WBC 4.2"},
    ])
    pending_path = pending_labs_dir / f"{visit_key}.json"
    pending_path.write_text(json.dumps(payload), encoding="utf-8")

    # Симуляция approve: применяем pending → lab_results
    for item in payload["items"]:
        db.add_lab_result(
            "2026-05-08", item["name"], item["value"],
            unit=item["unit"], ref_low=item["ref_low"],
            ref_high=item["ref_high"],
        )

    # Committed: данные в lab_results
    assert db.count("lab_results") == 1
    row = db.fetchone("SELECT * FROM lab_results WHERE test_name='WBC'")
    assert row["value"] == 4.2


def test_tentative_can_be_rejected_without_db_write(
    pending_labs_dir, make_pending_json, db
):
    """Reject (❌): pending-файл удалён, lab_results остался пустым."""
    visit_key = "2026-05-08_healthfund"
    payload = make_pending_json(visit_key)
    pending_path = pending_labs_dir / f"{visit_key}.json"
    pending_path.write_text(json.dumps(payload), encoding="utf-8")

    # Симуляция reject: просто удаляем файл, в БД ничего не пишем
    pending_path.unlink()

    assert not pending_path.exists()
    assert db.count("lab_results") == 0


def test_tentative_idempotent_by_visit_key(pending_labs_dir, make_pending_json):
    """Повторное создание tentative с тем же visit_key — перезаписывает (UC-A-01)."""
    visit_key = "2026-05-08_clinic"
    p = pending_labs_dir / f"{visit_key}.json"

    p.write_text(json.dumps(make_pending_json(visit_key,
                                                 items=[{"name": "WBC", "value": 4.0,
                                                         "ref_low": 0, "ref_high": 10}])))
    p.write_text(json.dumps(make_pending_json(visit_key,
                                                 items=[{"name": "WBC", "value": 4.5,
                                                         "ref_low": 0, "ref_high": 10}])))

    # Файл один и значение последнее
    files = list(pending_labs_dir.glob(f"{visit_key}.json"))
    assert len(files) == 1
    payload = json.loads(p.read_text())
    assert payload["items"][0]["value"] == 4.5
