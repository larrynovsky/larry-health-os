"""check_gate_run_receipt — отказ гейта виден, а не растворяется в отсутствии строки.

4 ветки читателя: упал → warn; применён → молчит; квитанции нет → молчит (liveness — отдельный
датчик); битая квитанция → кричит. Плюс delivery-гард: warn не попадает в MUTE_WARN_SUBSTRINGS —
детект без доставки датчиком не является (урок рельсы 13.07).

Импорт integrity_tests требует health_db+БД (Studio-гвард) → на MacBook ПРОПУСК целиком.
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


def _run(monkeypatch, artifact, belief_day=None):
    """belief_day инжектируется: якорь «прогон точно был» — дата последней веры."""
    captured = []
    monkeypatch.setattr(it, "warn", lambda name, detail="": captured.append((name, detail)))
    res = it.check_gate_run_receipt(artifact=artifact, belief_day=belief_day)
    return captured, res


# ── VG-R4-03: сверка ИДЕНТИЧНОСТИ прогона, а не даты ──

def _receipt(tmp_path, **over):
    art = tmp_path / "gate_run_receipt.json"
    rec = {"status": "applied", "phase": "closed", "at": "2026-08-02 03:00:00",
           "published": True, "run_id": "2026-08-02-aaaaaaaa"}
    rec.update(over)
    art.write_text(json.dumps(rec), encoding="utf-8")
    return art


def test_receipt_and_belief_from_different_runs_warns(monkeypatch, tmp_path):
    """Дата — НЕ идентичность. Два прогона в одни сутки (ручной перезапуск, параллельная
    сессия) по дате неразличимы, и квитанция ОДНОГО подтверждала веру ДРУГОГО. Проверено
    вживую 2026-07-26 на staging: подмена run_id в квитанции → датчик кричит."""
    monkeypatch.setattr(it, "_last_belief_run_id", lambda: "2026-08-02-bbbbbbbb")
    cap, _ = _run(monkeypatch, _receipt(tmp_path), belief_day="2026-08-02")
    assert any("РАЗНЫХ прогонов" in n for n, _ in cap), \
        "совпадение ДАТЫ при разных run_id больше не должно считаться подтверждением"


def test_matching_run_ids_are_silent(monkeypatch, tmp_path):
    """Негативный контроль: без него предыдущий тест проходил бы и на «кричать всегда»."""
    monkeypatch.setattr(it, "_last_belief_run_id", lambda: "2026-08-02-aaaaaaaa")
    cap, _ = _run(monkeypatch, _receipt(tmp_path), belief_day="2026-08-02")
    assert cap == []


def test_started_receipt_warns_run_did_not_return(monkeypatch, tmp_path):
    """Конверт открыт и не закрыт: прогон убит на полпути. Раньше такого состояния не
    существовало — на диске оставалась ПРОШЛАЯ applied, и «прогона не было» читалось как
    «всё в порядке»."""
    cap, res = _run(monkeypatch, _receipt(tmp_path, status="started", phase="started",
                                          published=False), belief_day="2026-08-02")
    assert any("не закрыл квитанцию" in n for n, _ in cap)
    assert res["status"] == "started"


def test_old_format_belief_falls_back_to_date(monkeypatch, tmp_path):
    """Вера до 2026-07-26 не несёт run_id. Сверка по дате слабее, но лучше молчания —
    отказ от неё оставил бы старые строки без всякого читателя."""
    monkeypatch.setattr(it, "_last_belief_run_id", lambda: None)
    cap, _ = _run(monkeypatch, _receipt(tmp_path, at="2026-08-01 03:00:00"),
                  belief_day="2026-08-02")
    assert any("отстала от веры" in n for n, _ in cap)


def test_failed_run_warns(monkeypatch, tmp_path):
    art = tmp_path / "gate_run_receipt.json"
    art.write_text(json.dumps({"status": "failed", "at": "2026-08-02 03:00:00",
                               "published": False, "error": "RuntimeError('boom')"}),
                   encoding="utf-8")
    cap, res = _run(monkeypatch, art)
    assert len(cap) == 1 and "гейт УПАЛ" in cap[0][0]
    assert "boom" in cap[0][1] and "2026-08-02" in cap[0][1]
    assert res["status"] == "failed"


def test_applied_run_is_silent(monkeypatch, tmp_path):
    """ПОЛНАЯ форма квитанции: applied + phase=closed + published + run_id. Ревью R5 (VG-R5-05):
    раньше тест писал огрызок без phase/run_id и требовал молчания — то есть замораживал как
    норму ровно ту форму, которую живая продовая квитанция имела по ошибке."""
    cap, res = _run(monkeypatch, _receipt(tmp_path))
    assert cap == [] and res["status"] == "applied"


def test_legacy_dryrun_receipt_in_production_path_is_loud(monkeypatch, tmp_path):
    """Форма ЖИВОЙ продовой квитанции на 2026-07-26, снятая ревьюером read-only:
    `applied + dry_run=true + published=false + phase=null`, run_id отсутствует.
    Датчик принимал её молча — «код задеплоен» читалось как «путь исполнялся»."""
    art = _receipt(tmp_path, dry_run=True, published=False, phase=None)
    art.write_text(art.read_text(encoding="utf-8").replace(
        '"run_id": "2026-08-02-aaaaaaaa"', '"run_id": null'), encoding="utf-8")
    cap, res = _run(monkeypatch, art)
    assert len(cap) == 1 and "не описывает завершённый боевой прогон" in cap[0][0]
    for _sign in ("dry_run=true", "published=false", "phase=None", "run_id отсутствует"):
        assert _sign in cap[0][1], f"в тексте нет признака {_sign}: {cap[0][1]}"


def test_dryrun_receipt_in_its_own_sandbox_is_silent(monkeypatch, tmp_path):
    """Позитивный контроль: репетиция в СВОЕЙ песочнице (logs/dryrun/) законна и молчит.
    Без него правило «dry_run=true → крик» било бы по штатному пути и его научились бы гасить."""
    sandbox = tmp_path / "dryrun"
    sandbox.mkdir()
    art = sandbox / "gate_run_receipt.json"
    art.write_text(json.dumps({"status": "applied", "phase": "closed", "dry_run": True,
                               "published": True, "at": "2026-08-02 03:00:00",
                               "run_id": "2026-08-02-aaaaaaaa"}), encoding="utf-8")
    monkeypatch.setattr(it, "_last_belief_run_id", lambda: "2026-08-02-aaaaaaaa")
    cap, res = _run(monkeypatch, art)
    assert cap == [] and res["status"] == "applied"


def test_missing_receipt_after_a_real_run_is_loud(monkeypatch, tmp_path):
    """ИНВЕРСИЯ 2026-07-26 (ревью R3, VG-R3-03). Прежний тест `test_missing_receipt_is_silent_here`
    ТРЕБОВАЛ молчания, ссылаясь на «отдельный liveness-датчик» — которого не существовало:
    `check_gate_artifacts_liveness` делегировал ту же ветку обратно сюда. Два датчика молчали,
    а тесты закрепляли это как правильное поведение — ровно тот приём, с удаления которого
    начинался Ш1. Якорь «прогон был» — наличие веры."""
    cap, res = _run(monkeypatch, tmp_path / "nope.json", belief_day="2026-08-02")
    assert len(cap) == 1 and "пропала" in cap[0][0]
    assert "2026-08-02" in cap[0][1] and res is None


def test_missing_receipt_without_any_belief_is_silent(monkeypatch, tmp_path):
    """Позитивный контроль к предыдущему: на свежей машине прогонов ещё не было, квитанции
    законно нет. Без этого различия датчик кричал бы на каждом новом деплое и его научились
    бы игнорировать."""
    cap, res = _run(monkeypatch, tmp_path / "nope.json", belief_day=None)
    assert cap == [] and res is None


def test_receipt_older_than_belief_is_loud(monkeypatch, tmp_path):
    """Квитанция от прошлого прогона при свежей вере = нынешний прогон свою не оставил."""
    cap, res = _run(monkeypatch, _receipt(tmp_path, at="2026-07-26 03:00:00"),
                    belief_day="2026-08-02")
    assert len(cap) == 1 and "отстала" in cap[0][0]
    assert res["status"] == "applied"


def test_receipt_in_step_with_belief_is_silent(monkeypatch, tmp_path):
    cap, _ = _run(monkeypatch, _receipt(tmp_path), belief_day="2026-08-02")
    assert cap == []


def test_corrupt_receipt_is_loud(monkeypatch, tmp_path):
    """Битая квитанция ≠ отсутствующая: иначе отказ гейта прячется за повреждением файла."""
    art = tmp_path / "gate_run_receipt.json"
    art.write_text("{ не json", encoding="utf-8")
    cap, res = _run(monkeypatch, art)
    assert len(cap) == 1 and "не читается" in cap[0][0]
    assert res is None


def test_warn_is_deliverable():
    """Delivery-гард: сообщение датчика обязано пережить mute-list триажа."""
    from triage_agent import classify_warnings
    out = classify_warnings([("longitudinal: гейт УПАЛ, вера не обновлена", "детали отказа")])
    assert out and "гейт УПАЛ" in out[0], \
        "warn замьючен триажем — датчик детектирует в пустоту"
