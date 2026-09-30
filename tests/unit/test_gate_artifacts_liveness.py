"""check_gate_artifacts_liveness — «артефакта нет» после успешного прогона больше не норма.

P2-02 аудита 2026-07-26: три соседних чека на отсутствующем файле возвращают None молча.
Обоснование «freshness longitudinal покрывает» неверно ровно для интересного случая:
longitudinal свеж, гейт применён, а writer артефакта упал (OSError / файл удалили) — и
детекторы мерцания и MC-зазора считают по пустоте, рапортуя «стабильно».

Этот чек судит только ФАКТ появления артефакта после applied-прогона; содержимое — дело
соседей. Поэтому их молчание на отсутствии остаётся законным делегированием, а не дырой.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:
    import integrity_tests as it
except Exception as e:  # noqa: BLE001 — health_db-гвард/нет БД: не наша среда
    pytest.skip(f"integrity_tests не импортируется здесь: {e}", allow_module_level=True)


def _receipt(tmp_path, status="applied", at="2026-08-02 03:00:00"):
    p = tmp_path / "gate_run_receipt.json"
    p.write_text(json.dumps({"status": status, "at": at, "published": status == "applied"}),
                 encoding="utf-8")
    return p


def _mc(tmp_path, stamp="2026-08-02"):
    p = tmp_path / "mc.json"
    p.write_text(json.dumps({"date": stamp, "mc_gap": {"families": {}}}), encoding="utf-8")
    return p


def _ps(tmp_path, stamp="2026-08-02"):
    p = tmp_path / "ps.json"
    p.write_text(json.dumps([{"date": stamp, "members": {}}]), encoding="utf-8")
    return p


def _run(monkeypatch, receipt, arts):
    cap = []
    monkeypatch.setattr(it, "warn", lambda name, detail="": cap.append((name, detail)))
    return cap, it.check_gate_artifacts_liveness(receipt=receipt, artifacts=arts)


def test_all_present_and_fresh_is_silent(monkeypatch, tmp_path):
    cap, res = _run(monkeypatch, _receipt(tmp_path),
                    {"mc_gap": _mc(tmp_path), "pass-set": _ps(tmp_path)})
    assert cap == [] and res["missing"] == [] and res["stale"] == []


def test_missing_artifact_after_applied_run_is_loud(monkeypatch, tmp_path):
    """Ключевой контроль: успешный прогон был, артефакта нет — это ОТКАЗ writer'а, а не норма."""
    cap, res = _run(monkeypatch, _receipt(tmp_path),
                    {"mc_gap": tmp_path / "нет.json", "pass-set": _ps(tmp_path)})
    assert len(cap) == 1 and "не появились" in cap[0][0]
    assert "mc_gap" in cap[0][1] and res["missing"] == ["mc_gap"]


def test_stale_artifact_after_applied_run_is_loud(monkeypatch, tmp_path):
    """Файл есть, но от прошлой недели: writer упал молча, а сосед прочитал старое как свежее."""
    cap, res = _run(monkeypatch, _receipt(tmp_path),
                    {"mc_gap": _mc(tmp_path, "2026-07-26"), "pass-set": _ps(tmp_path)})
    assert len(cap) == 1 and res["stale"] and "mc_gap" in res["stale"][0]


def test_failed_run_does_not_demand_artifacts(monkeypatch, tmp_path):
    """Прогон упал → артефактов законно нет. Иначе один отказ давал бы три алерта."""
    cap, res = _run(monkeypatch, _receipt(tmp_path, status="failed"),
                    {"mc_gap": tmp_path / "нет.json"})
    assert cap == [] and res is None


def test_no_receipt_is_delegated_to_a_sensor_that_exists(monkeypatch, tmp_path):
    """Делегирование стало настоящим (ревью R3, VG-R3-03). Этот датчик по-прежнему молчит на
    отсутствующей квитанции — но теперь ветку РЕАЛЬНО стережёт `check_gate_run_receipt`
    (см. `test_missing_receipt_after_a_real_run_is_loud`), а не несуществующий liveness.
    Раньше оба молчали, ссылаясь друг на друга."""
    cap, res = _run(monkeypatch, tmp_path / "нет-квитанции.json", {"mc_gap": tmp_path / "n.json"})
    assert cap == [] and res is None
    # адресат делегирования существует и кричит на том же входе
    cap2 = []
    monkeypatch.setattr(it, "warn", lambda name, detail="": cap2.append(name))
    it.check_gate_run_receipt(artifact=tmp_path / "нет-квитанции.json", belief_day="2026-08-02")
    assert cap2 and "пропала" in cap2[0], "делегировать некому — ветка снова в пустоте"


def test_warn_is_deliverable():
    from triage_agent import classify_warnings
    out = classify_warnings([("артефакты гейта не появились после успешного прогона", "детали")])
    assert out and "артефакты" in out[0], "warn замьючен триажем — датчик детектирует в пустоту"
