"""«Передал на починку» — только когда дорога до ремонта доказана (инцидент 03.10).

03.10 08:30 бот написал «Утренний отчёт сегодня не получился. Я уже передал это на починку»,
хотя ночной разбор в 08:00 упал на том же сбое и до ремонта сбой доехать не мог. Обещание
стояло в строке без проверки: проверка жила только у common.error.our_side и смотрела на
отметку ремонта, а не на разбор. Оракулы:
  • сцена 03.10 (квитанция разбора вчерашняя, сейчас 08:30) → строка без обещания;
  • та же минута, квитанция сегодняшняя → обещание;
  • у каждой строки с обещанием есть пара `.unrepaired` в ru и en (храповик для новых строк).
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest
import yaml

import _time_inject

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def scene(fault_journal):
    """Установка владельца утром 03.10: ремонт жив (отметка свежая), часы — 08:30."""
    import notify
    now = datetime(2026, 10, 3, 8, 30, 21)
    _time_inject.set_test_clock(now)
    (fault_journal.parent / notify.REPAIR_SEEN).write_text(str(int(now.timestamp()) - 3600))
    yield fault_journal.parent / notify.NIGHT_CYCLE_RECEIPT
    _time_inject.clear_test_clock()


def _cycle(receipt: Path, ran_at: str) -> None:
    receipt.write_text(json.dumps({"ran_at": ran_at}), encoding="utf-8")


def test_scene_0310_no_promise_when_cycle_missed_its_run(scene):
    import notify
    _cycle(scene, "2026-10-02T08:01:35.155929")          # вчерашний прогон; сегодняшний упал
    text = notify.fault("scheduled.send_morning_report: boom", person_key="jobs.morning.failed")
    assert "передал" not in text and "не работает" in text


def test_promise_when_cycle_covered_todays_run(scene):
    import notify
    _cycle(scene, "2026-10-03T08:01:40")
    text = notify.fault("scheduled.send_morning_report: boom", person_key="jobs.morning.failed")
    assert "передал это на починку" in text


def test_no_receipt_is_no_promise(scene):
    import notify
    scene.unlink(missing_ok=True)
    assert notify.honest_key("jobs.morning.failed") == "jobs.morning.failed.unrepaired"


def test_reader_and_writer_meet_on_one_file(tmp_path, monkeypatch):
    """Квитанцию пишет night_cycle._heartbeat_path, читает notify — один файл, а не два."""
    import night_cycle
    import notify
    monkeypatch.delenv("HEALTH_NIGHT_CYCLE_RECEIPT", raising=False)
    monkeypatch.setenv("HEALTH_FAULTS_JOURNAL", str(Path(night_cycle.__file__).parent / "logs" / "faults.jsonl"))
    assert night_cycle._heartbeat_path().name == notify.NIGHT_CYCLE_RECEIPT
    assert night_cycle._heartbeat_path().parent == notify._faults_journal().parent


def test_every_repair_promise_has_unrepaired_pair():
    ru = yaml.safe_load((ROOT / "methodology/i18n/ru.yaml").read_text(encoding="utf-8"))
    en = yaml.safe_load((ROOT / "methodology/i18n/en.yaml").read_text(encoding="utf-8"))
    promises = [k for k, v in ru.items() if "передал" in str(v) and "починк" in str(v)
                and not k.endswith(".unrepaired") and k != "common.error.our_side"]
    assert promises, "сторож ослеп: строк с обещанием не нашлось"
    missing = [k for k in promises if f"{k}.unrepaired" not in ru or f"{k}.unrepaired" not in en]
    assert not missing, f"строка обещает починку без честной пары .unrepaired (ru и en): {missing}"
