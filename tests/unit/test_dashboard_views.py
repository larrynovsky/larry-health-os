"""Sprint 5b unit-тесты для dashboard_views (view-cell rendering)."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


# ── _profile_view_cell ─────────────────────────────────────────────────────

def test_profile_view_cell_text_short():
    from dashboard_views import _profile_view_cell
    out = _profile_view_cell("identity.name", "Jordan", None)
    assert "kv-value--text" in out
    assert "Jordan" in out
    assert "hx-get=" in out
    assert '/api/profile/identity.name/edit' in out


def test_profile_view_cell_text_narrative_long():
    """Текст > 60 символов → narrative class."""
    from dashboard_views import _profile_view_cell
    long_text = "x" * 100
    out = _profile_view_cell("key1", long_text, None)
    assert "kv-value--narrative" in out


def test_profile_view_cell_json():
    from dashboard_views import _profile_view_cell
    out = _profile_view_cell("medical", None, '{"diag": "x"}')
    assert "kv-value--json" in out
    assert "<pre" in out
    assert "&quot;diag&quot;" in out  # escaped


def test_profile_view_cell_empty():
    from dashboard_views import _profile_view_cell
    out = _profile_view_cell("k", None, None)
    assert "kv-value--text" in out
    assert "hx-get=" in out


def test_profile_view_cell_escapes_html_in_key():
    """SQL-injection-проксированный key с HTML — должен escaping."""
    from dashboard_views import _profile_view_cell
    out = _profile_view_cell("key<script>", "v", None)
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


# ── Whitelist constants ────────────────────────────────────────────────────
