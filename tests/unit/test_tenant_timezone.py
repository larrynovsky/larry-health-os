"""tenant_timezone: IANA-tz из свежего GPS → дом → env → UTC (местные расписания).
Мутационный: каждый шаг fallback-цепи под контролем; tf мокается (MacBook без пакета)."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


class _FakeTF:
    """timezone_at по широте: ~52 → Берлин, иначе дом."""
    def timezone_at(self, lat, lng):
        return "Europe/Berlin" if abs(float(lat) - 52.5) < 1.5 else "Atlantic/Reykjavik"


def test_tz_from_fresh_gps(monkeypatch):
    """Свежий GPS Берлин → Europe/Berlin (несущий: путь свежего GPS)."""
    import location_signal as ls
    monkeypatch.setattr(ls, "_tf_instance", lambda: _FakeTF())
    monkeypatch.setattr(ls, "resolve_place",
                        lambda *a, **k: {"known": True, "fresh": True, "lat": 52.52, "lon": 13.41})
    assert ls.tenant_timezone() == "Europe/Berlin"


def test_stale_gps_falls_to_home(monkeypatch):
    """Протухший GPS (fresh=False) → НЕ доверяем координатам, идём на дом.
    Падение = устаревшему сигналу поверили (фантазия места)."""
    import location_signal as ls
    monkeypatch.setattr(ls, "_tf_instance", lambda: _FakeTF())
    monkeypatch.setattr(ls, "resolve_place",
                        lambda *a, **k: {"known": True, "fresh": False, "lat": 52.52, "lon": 13.41})
    monkeypatch.setattr(ls, "home_anchor", lambda: (64.15, -21.94, 15.0))
    assert ls.tenant_timezone() == "Atlantic/Reykjavik"


def test_no_gps_falls_to_home(monkeypatch):
    """Нет сигнала (known=False) → дом."""
    import location_signal as ls
    monkeypatch.setattr(ls, "_tf_instance", lambda: _FakeTF())
    monkeypatch.setattr(ls, "resolve_place",
                        lambda *a, **k: {"known": False, "fresh": False, "lat": None, "lon": None})
    monkeypatch.setattr(ls, "home_anchor", lambda: (64.15, -21.94, 15.0))
    assert ls.tenant_timezone() == "Atlantic/Reykjavik"


def test_no_tf_uses_env_then_utc(monkeypatch):
    """Пакет timezonefinder недоступен → env HEALTH_TZ; нет env → UTC."""
    import location_signal as ls
    monkeypatch.setattr(ls, "_tf_instance", lambda: None)
    monkeypatch.setenv("HEALTH_TZ", "Europe/Berlin")
    assert ls.tenant_timezone() == "Europe/Berlin"
    monkeypatch.delenv("HEALTH_TZ", raising=False)
    assert ls.tenant_timezone() == "UTC"
