"""tests/unit/test_recommendations_floors.py — датчик BUG-FLOORS-ORDER (BACKLOG 2026-05-22).

Инвариант: абсолютные полы/потолки срабатывают ДАЖЕ когда у домена нет
seeded signals (пустой signals_def). Регрессия, которую ловим: early return
на пустом signals_def до floors-проверки (присутствовал до Q-2 fix).

Поведенческий тест — дополняет source-presence проверку в
tests/integration/test_uc_d_02_domain_need.py, которая порядок слоёв не ловит.
"""
import pytest

pytestmark = pytest.mark.unit

from services import recommendations as rec


def _patch_db(monkeypatch, *, signals, pcts, floors, ceilings=None, protocols=None):
    monkeypatch.setattr(rec.db, "get_domain_signals", lambda d: signals)
    monkeypatch.setattr(rec.db, "get_metric_percentiles",
                        lambda baseline_days=90: pcts)
    monkeypatch.setattr(
        rec.db, "get_absolute_thresholds",
        lambda kind: {"floor": floors, "ceiling": ceilings or []}.get(kind, []))
    monkeypatch.setattr(rec.db, "get_active_protocols",
                        lambda domain=None: protocols if protocols is not None
                        else [{"id": 1, "name": "test-proto"}])
    monkeypatch.setattr(rec.db, "get_active_constraints",
                        lambda protocol_id=None: [])


def test_floor_fires_with_empty_signals_def(monkeypatch):
    """Пустой signals_def + значение ниже пола → needed=True (ядро BUG-FLOORS-ORDER)."""
    _patch_db(
        monkeypatch,
        signals=[],  # домен без seeded signals — раньше здесь был early return
        pcts={"hrv": {"percentile": 50, "value": 12.0, "mean": 22.0}},
        floors=[{"metric": "hrv", "value": 17.0,
                 "reason_template": "ВСР {val:.0f} мс — ниже абсолютного пола"}],
    )
    out = rec.evaluate_domain_need("__domain_without_signals__")
    assert out["needed"] is True, (
        "floors не сработали при пустом signals_def — регрессия BUG-FLOORS-ORDER")
    assert out["urgency"] == "required"  # pct=0 < 10 у floor-срабатывания
    assert any("пола" in r for r in out["reasons"])


def test_ceiling_fires_with_empty_signals_def(monkeypatch):
    """Симметрично: потолок срабатывает без signals."""
    _patch_db(
        monkeypatch,
        signals=[],
        pcts={"bp_systolic": {"percentile": 50, "value": 150.0, "mean": 120.0}},
        floors=[],
        ceilings=[{"metric": "bp_systolic", "value": 140.0,
                   "reason_template": "Систолическое {val:.0f} — выше потолка"}],
    )
    out = rec.evaluate_domain_need("__domain_without_signals__")
    assert out["needed"] is True
    assert any("потолка" in r for r in out["reasons"])


def test_error_path_is_observable_and_loud(monkeypatch, caplog):
    """Обзор 2026-07-02: тихий отказ (except→needed=False на debug) был багом.
    Теперь error-path: (1) ключ 'error' наблюдаем, (2) лог на ERROR."""
    import logging

    def _boom(domain):
        raise RuntimeError("db kaboom")
    monkeypatch.setattr(rec.db, "get_domain_signals", _boom)

    with caplog.at_level(logging.ERROR):
        out = rec.evaluate_domain_need("sleep")
    assert out["needed"] is False           # fail-safe для пайплайна сохранён
    assert out.get("error") == "db kaboom"  # но отказ наблюдаем
    assert any(r.levelno >= logging.ERROR for r in caplog.records), \
        "error-path не залогирован на ERROR — отказ снова немой"


def test_healthy_path_has_no_error_key(monkeypatch):
    """Здоровый вызов НЕ несёт ключ 'error' — иначе датчик даст ложный WARN."""
    _patch_db(
        monkeypatch, signals=[],
        pcts={"hrv": {"percentile": 50, "value": 25.0, "mean": 22.0}},
        floors=[{"metric": "hrv", "value": 17.0, "reason_template": "x {val}"}],
    )
    out = rec.evaluate_domain_need("sleep")
    assert "error" not in out


def test_empty_signals_and_no_breach_is_quiet(monkeypatch):
    """Пустой signals_def + значения в норме → needed=False без blocked_by.
    Фикс не должен превращать «нет сигналов» в ложные срабатывания."""
    _patch_db(
        monkeypatch,
        signals=[],
        pcts={"hrv": {"percentile": 50, "value": 25.0, "mean": 22.0}},
        floors=[{"metric": "hrv", "value": 17.0,
                 "reason_template": "ВСР {val:.0f} мс — ниже абсолютного пола"}],
    )
    out = rec.evaluate_domain_need("__domain_without_signals__")
    assert out["needed"] is False
    assert out["blocked_by"] is None


def test_bp_peak_ceiling_fires_without_percentile_history(monkeypatch):
    """Давление меряют редко: в перцентилях его нет (история < 14 дней), колонки у пика нет —
    он живёт в raw (bp_systolic_max). До 26.09 потолок брал значение ТОЛЬКО из перцентилей →
    был мёртв при любых данных. Синтетика: пик выше порога при среднем ниже него."""
    _patch_db(
        monkeypatch,
        signals=[],
        pcts={},                                   # ни одной метрики с 14 днями истории
        floors=[],
        ceilings=[{"metric": "bp_systolic_max", "value": 140.0,
                   "reason_template": "АД: систолическое до {val:.0f} — выше порога 140"}],
    )
    monkeypatch.setattr(rec.db, "metric_columns", lambda: ["bp_systolic", "sleep_total"])
    monkeypatch.setattr(rec.db, "get_day",
                        lambda d: {"bp_systolic": 124.0, "bp_systolic_max": 145})
    out = rec.evaluate_domain_need("__domain_without_signals__")
    assert out["needed"] is True
    assert any("145" in r for r in out["reasons"])


def test_column_metric_without_history_stays_silent(monkeypatch):
    """Узость фикса: колоночная метрика без истории НЕ читается из дня — у sleep_total/awake
    свой читатель (lifestyle_agents), расширение дало бы дубль тревог."""
    _patch_db(
        monkeypatch, signals=[], pcts={}, floors=[],
        ceilings=[{"metric": "sleep_total", "value": 1.0, "reason_template": "x {val}"}],
    )
    monkeypatch.setattr(rec.db, "metric_columns", lambda: ["sleep_total"])
    monkeypatch.setattr(rec.db, "get_day", lambda d: {"sleep_total": 9.0})
    out = rec.evaluate_domain_need("__domain_without_signals__")
    assert not out.get("needed")
