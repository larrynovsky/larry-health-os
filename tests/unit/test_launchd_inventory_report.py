"""check_launchd_inventory: находка о росте числа джоб без копии не называет «новыми» чужие.

Карточка 04.10 («29 > 23 — новая: wellally-check, withings-import, healthbot.morningwake»):
датчик помнит ЧИСЛО, а не имена, и подставлял под «новая» три последних по алфавиту.
`com.larry.healthbot.*` сортируется после всех `com.larry.health.*`, поэтому утренний
будильник попадал в «новые» при любом росте. Дежурный сверял бы не те три задачи.
Теперь находка печатает весь список и честно говорит, что новых имён датчик не знает.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# Без try/skip (ревью починки 09.10): любой сбой импорта — SyntaxError, NameError, сломанная
# зависимость — обязан краснеть, а не превращать модуль в пропуск.
import integrity_tests as it

import plist_env_liveness as pl


def _run(monkeypatch, live_only):
    captured = []
    monkeypatch.setattr(it, "warn", lambda name, detail="": captured.append((name, detail)))
    monkeypatch.setattr(pl, "repo_plist_drift",
                        lambda repo_dir=None: {"drifted": [], "live_only": live_only,
                                               "repo_only": []})
    it.check_launchd_inventory()
    return [c for c in captured if "без копии" in c[0]]


def test_growth_lists_every_uncovered_job_and_claims_no_new_names(monkeypatch):
    base = it._LAUNCHD_UNCOVERED_BASELINE
    # Настоящая новая джоба — в начале алфавита; хвост алфавита — старые.
    labels = sorted([f"com.larry.health.old-{i:02d}" for i in range(base)]
                    + ["com.larry.health.aaa-really-new", "com.larry.healthbot.morningwake"])
    found = _run(monkeypatch, labels)
    assert len(found) == 1
    label, detail = found[0]
    assert f"({len(labels)} > {base})" in label
    for lbl in labels:
        assert lbl in detail, f"{lbl} не назван — дежурному не с чем сверять"
    assert "новая:" not in detail, "датчик не знает, какие имена новые, и не вправе их называть"


def test_at_baseline_silent(monkeypatch):
    labels = [f"com.larry.health.j{i}" for i in range(it._LAUNCHD_UNCOVERED_BASELINE)]
    assert _run(monkeypatch, labels) == []
