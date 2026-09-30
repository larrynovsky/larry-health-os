"""Контракт веры: ИИ-читатель принимает только саммари с применённым гейтом.

Мутация схемы (удалить `gate` / поставить `gate_applied=False` / `status=failed`) обязана
ронять приёмку. Без этих негативных контролей правило приёмки не существует — оно просто
описано в докстринге, как это и было до 2026-07-26 (аудит validation_gate, P1-01).
"""
from __future__ import annotations

import json
import sqlite3

from belief_contract import BELIEF_SCHEMA_VERSION, read_belief

_GOOD = {"schema_version": BELIEF_SCHEMA_VERSION,
         "generated_at": "2026-07-26 03:00:00",
         "top_correlations": [{"a": "hrv", "b": "sleep_deep", "r": 0.7, "verdict": "gated"}],
         "gate": {"gate_applied": True, "status": "applied"}}


def _conn(rows):
    """rows: [(date, findings_dict)] — по порядку вставки (id растёт)."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE agent_reports (id INTEGER PRIMARY KEY AUTOINCREMENT, "
              "date TEXT, agent_type TEXT, findings TEXT)")
    for d, f in rows:
        c.execute("INSERT INTO agent_reports (date, agent_type, findings) VALUES (?,?,?)",
                  (d, "longitudinal_analysis", json.dumps(f, ensure_ascii=False)))
    return c


def _mutate(**gate):
    d = json.loads(json.dumps(_GOOD))
    if gate:
        d["gate"] = gate
    else:
        d.pop("gate")
    return d


def test_accepts_gated_belief():
    b = read_belief(_conn([("2026-07-26", _GOOD)]), receipt_path=None)
    assert b["accepted"] is True and b["reason"] == "ok"
    assert b["data"]["top_correlations"][0]["a"] == "hrv"
    assert b["generated_at"] == "2026-07-26"
    assert b["age_days"] is not None and b["age_days"] >= 0


def test_rejects_missing_gate_key():
    b = read_belief(_conn([("2026-07-26", _mutate())]))
    assert b["accepted"] is False and b["reason"] == "no_gate"
    assert b["data"] is None, "отказ обязан не отдавать данные — иначе читатель их отрендерит"


def test_rejects_gate_applied_false():
    b = read_belief(_conn([("2026-07-26", _mutate(gate_applied=False, status="failed"))]))
    assert b["accepted"] is False and b["reason"] == "gate_not_applied"


def test_rejects_failed_status_even_if_flag_true():
    """Гарда против полу-правды: флаг True, но прогон помечен упавшим."""
    b = read_belief(_conn([("2026-07-26", _mutate(gate_applied=True, status="failed"))]))
    assert b["accepted"] is False and b["reason"] == "status=failed"


def test_accepts_schema_1_row_without_status():
    """Строки до 2026-07-26 не имеют `status`, но записаны при УСПЕШНОМ гейте (иначе
    gate_applied был бы False). Отказ им означал бы потерю веры до ближайшего воскресенья."""
    b = read_belief(_conn([("2026-07-19", _mutate(gate_applied=True))]))
    assert b["accepted"] is True


def test_tiebreak_takes_last_written_of_the_day():
    """Read-your-writes: при двух строках за одну дату берётся последняя записанная."""
    old = json.loads(json.dumps(_GOOD)); old["top_correlations"][0]["a"] = "OLD"
    new = json.loads(json.dumps(_GOOD)); new["top_correlations"][0]["a"] = "NEW"
    b = read_belief(_conn([("2026-07-26", old), ("2026-07-26", new)]))
    assert b["data"]["top_correlations"][0]["a"] == "NEW"


def test_no_report_is_not_a_gate_failure():
    b = read_belief(_conn([]))
    assert b["accepted"] is False and b["reason"] == "no_report"


def test_run_failure_receipt_is_surfaced_without_dropping_belief(tmp_path):
    """Решение владельца 2026-07-26: упавший прогон НЕ стирает прошлую веру, но обязан быть
    виден читателю — иначе устаревшая вера выглядит свежей."""
    rp = tmp_path / "gate_run_receipt.json"
    rp.write_text(json.dumps({"status": "failed", "at": "2026-08-02 03:00:00",
                              "error": "RuntimeError('boom')"}), encoding="utf-8")
    b = read_belief(_conn([("2026-07-26", _GOOD)]), receipt_path=rp)
    assert b["accepted"] is True, "прошлая вера годна — она была гейтована"
    assert b["run_failed"] is True and b["failed_at"] == "2026-08-02 03:00:00"
    assert "boom" in b["error"]


def test_applied_receipt_is_not_a_failure(tmp_path):
    rp = tmp_path / "gate_run_receipt.json"
    rp.write_text(json.dumps({"status": "applied", "at": "2026-08-02 03:00:00"}), encoding="utf-8")
    b = read_belief(_conn([("2026-07-26", _GOOD)]), receipt_path=rp)
    assert b["run_failed"] is False and b["failed_at"] is None


def test_missing_receipt_is_not_a_failure(tmp_path):
    """Отсутствие квитанции — не «прогон упал», а отсутствие сведений: у него свой датчик."""
    b = read_belief(_conn([("2026-07-26", _GOOD)]), receipt_path=tmp_path / "nope.json")
    assert b["run_failed"] is False
