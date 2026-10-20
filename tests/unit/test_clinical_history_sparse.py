"""Пустые `periods` не роняют отчёт ни у кого (решение владельца 20.10, вариант 3а).

Раньше падение оставляли установке с папкой данных `health`, а у Docker-установки
постороннего она называется так же (`/home/health/health`) — утренний отчёт не собирался
ни разу. Теперь пустота видна строкой в контексте и сбоем в журнале.
Мокаем historical_periods → [] (тестируем ветку gp_context, не сам periods_db).
"""
import json

import pytest


@pytest.mark.parametrize("data_dir", ["/x/health_partner", "/x/health", "/home/health/health"])
def test_empty_periods_degrade_and_leave_a_fault(monkeypatch, tmp_path, data_dir):
    import gp_context
    import health_db
    journal = tmp_path / "faults.jsonl"
    monkeypatch.setenv("HEALTH_FAULTS_JOURNAL", str(journal))
    monkeypatch.setattr(health_db, "historical_periods", lambda **k: [])
    monkeypatch.setenv("HEALTH_DATA_DIR", data_dir)
    out = gp_context._build_clinical_history()
    assert "не заполнена" in out                          # отчёт собирается дальше
    rec = json.loads(journal.read_text(encoding="utf-8").splitlines()[-1])
    assert rec["where"] == "gp_context._build_clinical_history"
    assert rec["frames"] == ["gp_context.py:_build_clinical_history"]
