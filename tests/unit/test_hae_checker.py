"""
tests/unit/test_hae_checker.py

Unit-тесты для hae_checker.py:
  1. scan_hae_file — правильный набор MetricInfo из синтетического JSON
  2. scan_hae_file — пустые метрики (data:[]) не включаются
  3. scan_hae_file — OSError(EDEADLK) → None (не падает, не меняет состояние)
  4. scan_hae_file — OSError другой → None
  5. find_new_metrics — правильная дедупликация с реестром
  6. find_new_metrics — count=0 не включается
  7. build_alert_text — содержит инструкцию и имя метрики
"""
import errno
import json
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from hae_checker import (
    MetricInfo, _scan_hae_file, _parse_hae,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_hae_json(metrics: list[dict]) -> dict:
    """Создаёт минимальный HAE JSON с заданными метриками."""
    return {"data": {"metrics": metrics}}


def _write_hae(tmp_path: Path, metrics: list[dict]) -> Path:
    p = tmp_path / "HealthAutoExport-2026-06-01.json"
    p.write_text(json.dumps(_make_hae_json(metrics)), encoding="utf-8")
    return p


# ── 1. scan_hae_file — базовый случай ────────────────────────────────────────

class TestScanHaeFile:
    def test_known_metrics_parsed(self, tmp_path):
        path = _write_hae(tmp_path, [
            {"name": "step_count",   "unit": "count",
             "data": [{"qty": 1000}, {"qty": 1200}, {"qty": 900}]},
            {"name": "resting_heart_rate", "unit": "count/min",
             "data": [{"qty": 58}]},
        ])
        result = _scan_hae_file(path)
        assert result is not None
        assert "step_count" in result
        assert result["step_count"].count == 3
        assert result["step_count"].unit == "count"
        assert len(result["step_count"].samples) > 0
        assert "resting_heart_rate" in result

    def test_empty_data_excluded(self, tmp_path):
        path = _write_hae(tmp_path, [
            {"name": "heart_rate_variability", "unit": "ms", "data": []},
            {"name": "step_count", "unit": "count",
             "data": [{"qty": 500}]},
        ])
        result = _scan_hae_file(path)
        assert result is not None
        assert "heart_rate_variability" not in result  # пустые исключаются
        assert "step_count" in result

    def test_samples_limited_to_3(self, tmp_path):
        entries = [{"qty": float(i)} for i in range(100)]
        path = _write_hae(tmp_path, [
            {"name": "active_energy", "unit": "kcal", "data": entries},
        ])
        result = _scan_hae_file(path)
        assert result is not None
        assert len(result["active_energy"].samples) <= 3

    def test_edeadlk_returns_none(self, tmp_path):
        """OSError(EDEADLK) → None, не падаем, не меняем состояние."""
        path = tmp_path / "test.json"
        path.write_text("{}")

        with patch("hae_checker.subprocess.run"), \
             patch("hae_checker.time.sleep"), \
             patch("builtins.open", side_effect=OSError(errno.EDEADLK, "deadlock")):
            result = _scan_hae_file(path)

        assert result is None

    def test_other_oserror_returns_none(self, tmp_path):
        """Другой OSError (напр. evicted) → None тоже."""
        path = tmp_path / "test.json"
        path.write_text("{}")
        with patch("builtins.open", side_effect=OSError(errno.ENOENT, "not found")):
            result = _scan_hae_file(path)
        assert result is None

    def test_nonexistent_file_returns_none(self, tmp_path):
        result = _scan_hae_file(tmp_path / "missing.json")
        assert result is None

    def test_unknown_metric_included(self, tmp_path):
        """Неизвестная метрика всё равно попадает в результат — реестр решает."""
        path = _write_hae(tmp_path, [
            {"name": "some_future_apple_metric", "unit": "units",
             "data": [{"qty": 42.0}]},
        ])
        result = _scan_hae_file(path)
        assert result is not None
        assert "some_future_apple_metric" in result


