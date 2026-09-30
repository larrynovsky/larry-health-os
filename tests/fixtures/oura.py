"""
tests/fixtures/oura.py — mock JSON-ответов Oura API v2.

`import_oura.py` делает запросы к sleep/daily_activity/daily_readiness/daily_stress.
Mock возвращает заранее построенный JSON по дате.

Ключевая прагматика для UC-A-04: сон считается на дату пробуждения
(`summary_date` в Oura sleep — это дата УТРА, когда сон закончился).

Использование:

    def test_oura_sleep_target_date(oura_mock, clock):
        clock.set("2026-05-08")
        oura_mock.add_sleep(summary_date="2026-05-08", total=7.5*3600, deep=45*60)
        # ... import_oura_via_mock
        # daily_metrics[2026-05-08] должен иметь sleep_total=7.5
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pytest


@dataclass
class OuraMock:
    sleep_records: list = field(default_factory=list)
    activity_records: list = field(default_factory=list)
    readiness_records: list = field(default_factory=list)
    stress_records: list = field(default_factory=list)

    # ── builders ────────────────────────────────────────────────────────────
    def add_sleep(self, summary_date: str, total: int = 7 * 3600,
                  deep: int = 60 * 60, rem: int = 90 * 60,
                  hrv_avg: int = 25, sleep_score: int = 75,
                  bedtime_start: Optional[str] = None,
                  bedtime_end: Optional[str] = None) -> None:
        """Добавить сон. total/deep/rem — в секундах (как в Oura API)."""
        self.sleep_records.append({
            "id": f"sleep_{summary_date}",
            "day": summary_date,           # Oura v2 называет это `day`
            "summary_date": summary_date,  # старый алиас
            "total_sleep_duration": total,
            "deep_sleep_duration": deep,
            "rem_sleep_duration": rem,
            "average_hrv": hrv_avg,
            "score": sleep_score,
            "bedtime_start": bedtime_start or f"{summary_date}T22:30:00",
            "bedtime_end": bedtime_end or f"{summary_date}T06:30:00",
        })

    def add_activity(self, day: str, steps: int = 8000,
                     active_calories: int = 400, score: int = 80) -> None:
        self.activity_records.append({
            "id": f"activity_{day}",
            "day": day,
            "summary_date": day,
            "steps": steps,
            "active_calories": active_calories,
            "score": score,
        })

    def add_readiness(self, day: str, score: int = 75,
                       hrv_balance: int = 70) -> None:
        self.readiness_records.append({
            "id": f"readiness_{day}",
            "day": day,
            "summary_date": day,
            "score": score,
            "contributors": {"hrv_balance": hrv_balance},
        })

    def add_stress(self, day: str, stress_high: int = 1200,
                   recovery_high: int = 2400, day_summary: str = "balanced") -> None:
        """stress_high/recovery_high в секундах."""
        self.stress_records.append({
            "id": f"stress_{day}",
            "day": day,
            "summary_date": day,
            "stress_high": stress_high,
            "recovery_high": recovery_high,
            "day_summary": day_summary,
        })

    # ── имитация API endpoint ──────────────────────────────────────────────
    def get(self, endpoint: str, start: str = None, end: str = None) -> dict:
        """Возвращает {data: [...]} в формате Oura API v2."""
        mapping = {
            "sleep": self.sleep_records,
            "daily_activity": self.activity_records,
            "daily_readiness": self.readiness_records,
            "daily_stress": self.stress_records,
        }
        records = mapping.get(endpoint, [])
        if start and end:
            records = [r for r in records if start <= r.get("day", "") <= end]
        return {"data": records, "next_token": None}


@pytest.fixture
def oura_mock() -> OuraMock:
    return OuraMock()
