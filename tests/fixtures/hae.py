"""
tests/fixtures/hae.py — mock JSON-файлов Apple Health через Health Auto Export.

`import_apple_health.py` читает дневные JSON-файлы из iCloud HAE контейнера.
Fixture создаёт tmp-директорию с JSON-файлами в формате HAE и подменяет
HAE_DAILY_DIR.

Ключевые прагматики:
- UC-A-03: merge без затирания Oura (HAE не должен перезаписывать существующий
  hrv от Oura, если HAE сам hrv не прислал).
- UC-I-03: NULL → "нет данных", не "0 шагов".

Использование:

    def test_hae_merge(hae_mock, db, clock):
        # Pre-fill Oura данные за вчера
        db.add_daily_metrics("2026-05-07", hrv=25, sleep_total=7.5)

        # HAE прислал только steps, без hrv
        hae_mock.add_daily(date="2026-05-07", steps=8000)

        # ... import_apple_health.import_daily_new_automation()
        # row hrv должен остаться 25, не None
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest


@dataclass
class HaeMock:
    """tmp-директория с дневными JSON HAE."""
    dir: Path

    def add_daily(self, date: str, steps: int = None, hrv: float = None,
                   sleep_total: float = None, sleep_deep: float = None,
                   bp_systolic: float = None, bp_diastolic: float = None,
                   spo2_avg: float = None, **extra: Any) -> Path:
        """Создать файл `HealthAutoExport-{date}.json`.

        None-значения опускаются (не записываются в JSON) — это симулирует
        случай «HAE не прислал это поле», важно для UC-A-03 теста merge.
        """
        payload = {"data": {"metrics": []}}

        def _push(name: str, value: Any, unit: str = ""):
            if value is None:
                return
            payload["data"]["metrics"].append({
                "name": name,
                "units": unit,
                "data": [{"date": f"{date} 00:00:00 +0000",
                          "qty": value}]
            })

        _push("step_count", steps, "count")
        _push("heart_rate_variability", hrv, "ms")
        _push("sleep_analysis", sleep_total, "hr")
        _push("sleep_deep", sleep_deep, "hr")
        _push("blood_pressure_systolic", bp_systolic, "mmHg")
        _push("blood_pressure_diastolic", bp_diastolic, "mmHg")
        _push("oxygen_saturation", spo2_avg, "%")
        for k, v in extra.items():
            _push(k, v)

        path = self.dir / f"HealthAutoExport-{date}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                         encoding="utf-8")
        return path

    def list_files(self) -> list[Path]:
        return sorted(self.dir.glob("HealthAutoExport-*.json"))


@pytest.fixture
def hae_mock(tmp_path: Path) -> HaeMock:
    """tmp HAE-директория, готова принимать `add_daily(...)`."""
    hae_dir = tmp_path / "hae_daily"
    hae_dir.mkdir(parents=True, exist_ok=True)
    return HaeMock(dir=hae_dir)
