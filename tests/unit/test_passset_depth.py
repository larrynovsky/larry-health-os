"""Глубина истории pass-set: тихая потеря базы сравнения (2026-07-25).

Мерцание считается против ПРЕДЫДУЩЕГО снимка. Если историю подчистить, детектор снова слеп,
но рапортует «стабильно» — тот же тихий отказ, что снятый sha-ключ. Этот чек делает потерю
громкой. Позит-контроли ниже доказывают, что он РАЗЛИЧАЕТ полную историю и прореженную.
"""
from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def _hist(path: Path, dates):
    path.write_text(json.dumps([{"date": d, "head_sha": "s", "members": {"D": ["a×b"]}}
                                for d in dates], ensure_ascii=False), encoding="utf-8")


def _weekly(n, start=date(2026, 1, 4)):
    return [str(start + timedelta(days=7 * i)) for i in range(n)]


def _run(tmp_path, monkeypatch, dates):
    """Возвращает (пойманные warn, результат). Подмена warn — как в test_passset_flicker.py."""
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HEALTH_SECRETS_DIR", str(tmp_path))
    import integrity_tests as I
    cap: list[tuple[str, str]] = []
    monkeypatch.setattr(I, "warn", lambda name, detail="": cap.append((name, detail)))
    art = tmp_path / f"h{len(dates)}.json"
    _hist(art, dates)
    return cap, I.check_passset_history_depth(artifact=art)


def test_full_history_silent(tmp_path, monkeypatch):
    """12 недельных снимков подряд → история полна, датчик молчит."""
    cap, r = _run(tmp_path, monkeypatch, _weekly(12))
    assert r["snapshots"] == 12 and r["expected"] == 12 and r["capped"] is False
    assert cap == []


def test_thinned_history_flags(tmp_path, monkeypatch):
    """⭐ ПОЗИТ-КОНТРОЛЬ: те же 12 недель, но снимков осталось 4 (историю подчистили) → warn.
    Без этого «датчик молчит» ничего не доказывает."""
    dates = _weekly(12)
    thinned = [dates[0], dates[3], dates[7], dates[11]]
    cap, r = _run(tmp_path, monkeypatch, thinned)
    assert r["snapshots"] == 4 and r["expected"] == 12
    assert any("история pass-set неполна" in n for n, _ in cap), cap


def test_too_short_history_silent(tmp_path, monkeypatch):
    """2 снимка — база ещё набирается, судить рано (иначе датчик кричал бы на старте)."""
    cap, r = _run(tmp_path, monkeypatch, _weekly(2))
    assert r is None and cap == []


def test_missing_artifact_silent(tmp_path, monkeypatch):
    """Файла нет — молчим: «гейт ни разу не бегал» покрыто check_longitudinal_freshness."""
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HEALTH_SECRETS_DIR", str(tmp_path))
    import integrity_tests as I
    assert I.check_passset_history_depth(artifact=tmp_path / "nope.json") is None
