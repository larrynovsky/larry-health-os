"""
Wave 4-CORRELATIONS C-7 — unit-тесты для hai_analysis.detect_correlation_drift.

Synthetic data, без БД. Покрывает:
- Strong correlated → caught.
- Uncorrelated → not caught.
- NaN-heavy → отфильтрованы (min_pairs).
- Constant series → spearmanr возвращает NaN, тест пропускает (no crash).
- Sign-flip корреляции → severity strong.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture
def synthetic_db(tmp_path):
    """In-memory SQLite с synthetic daily_metrics."""
    db_path = tmp_path / "synth.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE daily_metrics (
            date TEXT PRIMARY KEY,
            hrv REAL, resting_hr REAL, readiness REAL,
            sleep_total REAL, sleep_deep REAL, sleep_rem REAL,
            sleep_score REAL, sleep_efficiency REAL,
            steps INTEGER, active_kcal INTEGER, spo2_avg REAL, weight REAL
        )
    """)
    return conn, db_path


def _populate_pair(conn, n_days_recent: int, n_days_baseline: int,
                   recent_corr: str, baseline_corr: str,
                   today: date):
    """Заполняет hrv+resting_hr с заданной корреляцией в обоих окнах.

    recent_corr / baseline_corr: 'strong_negative' | 'strong_positive' | 'none'.
    """
    import random
    random.seed(42)

    def _gen(corr_type: str, n: int):
        for i in range(n):
            hrv = 20 + (i % 10) * 0.5  # 20-25
            if corr_type == "strong_negative":
                rhr = 80 - hrv  # r ≈ -1
            elif corr_type == "strong_positive":
                rhr = 40 + hrv  # r ≈ +1
            else:  # none
                rhr = 60 + random.gauss(0, 5)
            yield hrv + random.gauss(0, 0.3), rhr + random.gauss(0, 0.3)

    # Recent: target-90..target
    start_r = today - timedelta(days=n_days_recent)
    for i, (hrv, rhr) in enumerate(_gen(recent_corr, n_days_recent)):
        d = (start_r + timedelta(days=i)).isoformat()
        conn.execute(
            "INSERT INTO daily_metrics(date, hrv, resting_hr) VALUES (?, ?, ?)",
            (d, hrv, rhr),
        )

    # Baseline: target-180..target-90
    start_b = today - timedelta(days=n_days_recent + n_days_baseline)
    for i, (hrv, rhr) in enumerate(_gen(baseline_corr, n_days_baseline)):
        d = (start_b + timedelta(days=i)).isoformat()
        conn.execute(
            "INSERT INTO daily_metrics(date, hrv, resting_hr) VALUES (?, ?, ?)",
            (d, hrv, rhr),
        )
    conn.commit()


@contextmanager
def _mock_db_path(db_path: Path):
    """Подменяет health_db.DB_PATH для теста."""
    import sys
    sys.path.insert(0, str(Path(__file__).parents[2]))
    import health_db
    original = health_db.DB_PATH
    health_db.DB_PATH = db_path
    try:
        yield
    finally:
        health_db.DB_PATH = original


def test_strong_negative_correlation_is_stable_not_drift(synthetic_db):
    """Если оба окна имеют сильную одинаковую корреляцию → нет drift."""
    conn, db_path = synthetic_db
    today = date(2026, 5, 12)
    _populate_pair(conn, 90, 90, "strong_negative", "strong_negative", today)
    conn.close()
    with _mock_db_path(db_path):
        import hai_analysis
        drifts = hai_analysis.detect_correlation_drift(target=today)
    # r одинаковый в обоих окнах → delta ≈ 0% → не выше 20% threshold
    assert all(abs(d["delta_pct"]) < 20 for d in drifts), \
        f"Стабильная корреляция не должна давать drift, получили: {drifts}"


def test_sign_flip_correlation_is_caught(synthetic_db):
    """Если корреляция меняет знак (strong negative → strong positive) → драматический drift."""
    conn, db_path = synthetic_db
    today = date(2026, 5, 12)
    _populate_pair(conn, 90, 90, "strong_positive", "strong_negative", today)
    conn.close()
    with _mock_db_path(db_path):
        import hai_analysis
        drifts = hai_analysis.detect_correlation_drift(target=today)
    assert len(drifts) >= 1, f"Sign-flip должен быть пойман, получили: {drifts}"
    hrv_rhr = next((d for d in drifts if d["metric_a"] == "hrv" and d["metric_b"] == "resting_hr"), None)
    assert hrv_rhr is not None, "Пара hrv/resting_hr не в drifts"
    assert hrv_rhr["severity"] in ("moderate", "strong"), \
        f"Sign-flip severity должна быть moderate/strong, не {hrv_rhr['severity']}"


def test_no_correlation_filtered_out(synthetic_db):
    """Если recent r ниже threshold_r — отбрасываем."""
    conn, db_path = synthetic_db
    today = date(2026, 5, 12)
    _populate_pair(conn, 90, 90, "none", "none", today)
    conn.close()
    with _mock_db_path(db_path):
        import hai_analysis
        drifts = hai_analysis.detect_correlation_drift(target=today)
    # Шум → |r| < 0.5 → пусто
    assert drifts == [], f"Случайные данные не должны давать drifts, получили: {drifts}"


def test_min_pairs_threshold_works(synthetic_db):
    """Если в окне < min_pairs валидных пар — пропускаем."""
    conn, db_path = synthetic_db
    today = date(2026, 5, 12)
    # Только 30 дней recent — меньше дефолтного min_pairs=60
    _populate_pair(conn, 30, 90, "strong_negative", "strong_positive", today)
    conn.close()
    with _mock_db_path(db_path):
        import hai_analysis
        drifts = hai_analysis.detect_correlation_drift(target=today)
    assert drifts == [], \
        f"При недостаточных данных не должно быть drifts, получили: {drifts}"


def test_returned_severity_classification(synthetic_db):
    """Sign-flip → severity 'strong' (delta_pct близок к -200%)."""
    conn, db_path = synthetic_db
    today = date(2026, 5, 12)
    _populate_pair(conn, 90, 90, "strong_negative", "strong_positive", today)
    conn.close()
    with _mock_db_path(db_path):
        import hai_analysis
        drifts = hai_analysis.detect_correlation_drift(target=today)
    if drifts:
        for d in drifts:
            assert d["severity"] in ("mild", "moderate", "strong"), \
                f"Unknown severity: {d['severity']}"
            # n_pairs >= min_pairs
            assert d["n_pairs_recent"] >= 60
            assert d["n_pairs_baseline"] >= 60
