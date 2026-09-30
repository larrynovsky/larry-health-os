"""Решённое перестаёт звонить, рецидив звонит снова (2026-08-10).

Повод: владелец вынес вердикт по r=0.42 («выдумана глубина базы, чиним промпт
GP»), решение записано в `parked_decisions`, причина закрыта. А ратчет UC-B-09
продолжал ставить тот же гейт каждым прогоном: журнальная строка отчёта 01.08
историческая и исчезнуть не может, а третьего состояния у значения не было —
только «в базе» и «новое».

Колокол, который звонит после принятого решения, учит не брать трубку. Но
гасить всё подряд нельзя: новый отчёт с тем же коэффициентом — новая находка.
Отсюда граница по ДАТЕ отчёта, а не по значению.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def _prep(tmp_path, monkeypatch, *, rows, decision=None):
    """Журнал и стор решений — во временном тенанте, живого канона не касаемся."""
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HEALTH_SECRETS_DIR", str(tmp_path))
    monkeypatch.setenv("ALLOW_WRITE_NONPRIMARY", "1")
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    import health_db
    monkeypatch.setattr(health_db, "DB_PATH", tmp_path / "data" / "health.db")
    with health_db.get_conn() as c:
        c.execute("CREATE TABLE IF NOT EXISTS ungrounded_correlations ("
                  "id INTEGER PRIMARY KEY, report_date TEXT, agent_type TEXT, "
                  "value REAL, belief_n INT, belief_accepted INT, is_baseline INT, "
                  "logged_at TEXT)")
        c.executemany("INSERT INTO ungrounded_correlations"
                      "(report_date, agent_type, value, is_baseline) VALUES (?,?,?,?)", rows)
        c.commit()
    import parked_decisions as pd
    monkeypatch.setattr(pd, "_store_path", lambda: tmp_path / "logs" / "parked.json")
    if decision:
        gate_id, resolved_at = decision
        pd.park(gate_id, "owner_decision", "тест")
        pd.record_decision(gate_id, "вердикт вынесен")
        data = pd._load()
        data[gate_id]["resolved_at"] = resolved_at
        pd._save(data)
    import integrity_tests as I
    monkeypatch.setattr(I, "warn", lambda *a, **k: None)
    monkeypatch.setattr(I, "_park_owner_gate", lambda *a, **k: None)
    return I


def test_novel_value_without_decision_still_rings(tmp_path, monkeypatch):
    """Негативный контроль: без решения находка обязана оставаться находкой."""
    I = _prep(tmp_path, monkeypatch,
              rows=[("2026-01-01", "gp", 0.51, 1), ("2026-08-01", "gp", 0.42, 0)])
    r = I.check_ungrounded_corr_ratchet()
    assert r["novel"] == 1 and r["adjudicated"] == 0, r


def test_decided_value_stops_ringing(tmp_path, monkeypatch):
    """⭐ Ровно случай r=0.42: решение записано, отчёт старше решения → тишина."""
    I = _prep(tmp_path, monkeypatch,
              rows=[("2026-01-01", "gp", 0.51, 1), ("2026-08-01", "gp", 0.42, 0)],
              decision=("ungrounded_corr_0.42", "2026-08-10"))
    r = I.check_ungrounded_corr_ratchet()
    assert r["novel"] == 0 and r["adjudicated"] == 1, r


def test_recurrence_after_the_decision_rings_again(tmp_path, monkeypatch):
    """⭐ Гаситель не имеет права глушить БУДУЩЕЕ: отчёт свежее решения — снова находка.

    Без этой границы вердикт превратился бы в вечную индульгенцию значению,
    и следующая выдумка того же коэффициента прошла бы молча.
    """
    I = _prep(tmp_path, monkeypatch,
              rows=[("2026-01-01", "gp", 0.51, 1),
                    ("2026-08-01", "gp", 0.42, 0),
                    ("2026-09-15", "gp", 0.42, 0)],
              decision=("ungrounded_corr_0.42", "2026-08-10"))
    r = I.check_ungrounded_corr_ratchet()
    assert r["novel"] == 1 and r["adjudicated"] == 0, r


def _window_warns(I, monkeypatch, items):
    """Прогон ОКОННОГО датчика (check_correlations_grounded) на подставных находках.
    collect_ungrounded и журнал замокированы: предмет теста — гашение колокола
    вердиктом, а не сбор из agent_reports (он покрыт своим домом)."""
    monkeypatch.setattr(I, "collect_ungrounded",
                        lambda days: (items, 1, True, 3, ""))
    monkeypatch.setattr(I, "_log_ungrounded", lambda *a, **k: 0)
    cap = []
    monkeypatch.setattr(I, "warn", lambda n, d="": cap.append((n, d)))
    r = I.check_correlations_grounded()
    return r, cap


def test_window_check_decided_value_stops_ringing(tmp_path, monkeypatch):
    """⭐ 2026-08-12: оконный датчик тоже знает третье состояние. До правки он
    звонил про решённый r=+0.42 до истечения 30-дневного окна (отчёт 01.08
    исторический) — колокол после принятого решения учит не брать трубку."""
    I = _prep(tmp_path, monkeypatch, rows=[("2026-01-01", "gp", 0.51, 1)],
              decision=("ungrounded_corr_0.42", "2026-08-10"))
    r, cap = _window_warns(I, monkeypatch,
                           [("2026-08-01", "gp", 0.42, "+0.42")])
    assert r["ungrounded"] == 0 and cap == [], (r, cap)


def test_window_check_recurrence_after_decision_rings(tmp_path, monkeypatch):
    """Рецидив: отчёт СВЕЖЕЕ дня решения — оконный датчик обязан звонить."""
    I = _prep(tmp_path, monkeypatch, rows=[("2026-01-01", "gp", 0.51, 1)],
              decision=("ungrounded_corr_0.42", "2026-08-10"))
    r, cap = _window_warns(I, monkeypatch,
                           [("2026-08-11", "gp", 0.42, "+0.42")])
    assert r["ungrounded"] == 1 and len(cap) == 1, (r, cap)


def test_unresolved_gate_does_not_silence(tmp_path, monkeypatch):
    """Гейт поставлен, но решения НЕТ — звонок обязан продолжаться.
    «Посмотрел» не считается: гаснет только записанное решение."""
    import parked_decisions as pd
    I = _prep(tmp_path, monkeypatch,
              rows=[("2026-01-01", "gp", 0.51, 1), ("2026-08-01", "gp", 0.42, 0)])
    pd.park("ungrounded_corr_0.42", "owner_decision", "висит без решения")
    r = I.check_ungrounded_corr_ratchet()
    assert r["novel"] == 1 and r["adjudicated"] == 0, r
