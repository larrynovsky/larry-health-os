#!/usr/bin/env python3.11
"""check_partner_epochs_ready (Группа 3 триггер) — ПОРОГ + PHI-безопасность + tenant-петля (C-19).

Триггер возврата к пер-тенант стратификации: сиблинг накопил ≥ min_daily_days daily_metrics
И ≥ min_periods периодов → warn (структурный, без PHI). Тестируем через monkeypatch _iter_tenant_ro
(контролируем tenant'ов) + перехват warn. Импорт integrity_tests требует health_db+БД (Studio) →
на не-Studio (MacBook, health_db-гвард) тест скипается целиком.
"""
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:
    import integrity_tests as it
except Exception as e:  # noqa: BLE001 — health_db-гвард/нет БД: не наша среда
    pytest.skip(f"integrity_tests не импортируется здесь: {e}", allow_module_level=True)


def _mk_conn(n_days: int, n_periods: int) -> sqlite3.Connection:
    """In-memory тенант-БД: n_days РАЗНЫХ дат в daily_metrics + n_periods строк в periods
    (с ИМЕНАМИ периодов — чтобы доказать, что датчик их НЕ утаскивает в сообщение)."""
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE daily_metrics (date TEXT, hrv REAL)")
    c.executemany(
        "INSERT INTO daily_metrics(date, hrv) VALUES (?, ?)",
        [((date(2024, 1, 1) + timedelta(days=i)).isoformat(), 50.0) for i in range(n_days)],
    )
    c.execute("CREATE TABLE periods (name TEXT, start TEXT)")
    c.executemany(
        "INSERT INTO periods(name, start) VALUES (?, ?)",
        [(f"СЕКРЕТНЫЙ-период-{j}", "2024-01-01") for j in range(n_periods)],
    )
    c.commit()
    return c


def _run(monkeypatch, tenants):
    """tenants: [(tag, conn, is_current)]. Возвращает список перехваченных warn (name, detail)."""
    captured = []
    monkeypatch.setattr(it, "_iter_tenant_ro", lambda: iter(tenants))
    monkeypatch.setattr(it, "warn", lambda name, detail="": captured.append((name, detail)))
    res = it.check_partner_epochs_ready()
    return captured, res


@pytest.mark.owner_data
def test_ready_partner_fires(monkeypatch):
    """Сиблинг 400д/2периода (≥365/≥2) → warn срабатывает."""
    caps, res = _run(monkeypatch, [("health_partner", _mk_conn(400, 2), False)])
    assert res["ready"] == 1 and res["checked"] == 1, res
    assert len(caps) == 1 and "health_partner" in caps[0][0], caps


def test_not_ready_below_thresholds_silent(monkeypatch):
    """Мало дней ИЛИ мало периодов → тишина (порог — И, не ИЛИ)."""
    caps_days, _ = _run(monkeypatch, [("health_partner", _mk_conn(300, 2), False)])   # дней мало
    assert caps_days == [], caps_days
    caps_per, _ = _run(monkeypatch, [("health_partner", _mk_conn(400, 1), False)])    # периодов мало
    assert caps_per == [], caps_per


def test_owner_is_skipped(monkeypatch):
    """Владелец (is_current=True) пропущен — у него эпохи уже есть, триггер не про него."""
    caps, res = _run(monkeypatch, [("health", _mk_conn(4000, 17), True)])
    assert caps == [] and res["checked"] == 0, (caps, res)


@pytest.mark.owner_data
def test_message_carries_no_phi(monkeypatch):
    """PHI-безопасность: сообщение = [tag] + счётчики, БЕЗ имён периодов (иначе монитор = утечка)."""
    caps, _ = _run(monkeypatch, [("health_partner", _mk_conn(400, 3), False)])
    blob = " ".join(caps[0])
    assert "СЕКРЕТНЫЙ" not in blob, f"имя периода утекло в алерт: {blob}"
    assert "400" in blob and "3" in blob, f"нет структурных счётчиков: {blob}"


@pytest.mark.owner_data
def test_all_siblings_scanned_not_one(monkeypatch):
    """C-19 (tenant-слепота): петля по ВСЕМ сиблингам. Готовый среди нескольких — найден."""
    caps, res = _run(monkeypatch, [
        ("health", _mk_conn(4000, 17), True),          # владелец — пропуск
        ("health_a", _mk_conn(100, 0), False),         # не готов
        ("health_partner", _mk_conn(500, 2), False),   # готов
    ])
    assert res["checked"] == 2, res                    # 2 сиблинга (владелец не в счёте)
    assert len(caps) == 1 and "health_partner" in caps[0][0], caps


if __name__ == "__main__":
    print("run via pytest")
