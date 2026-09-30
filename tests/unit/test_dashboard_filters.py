"""Sprint 5b unit-тесты для dashboard_filters (4 Jinja2 фильтра)."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

pytestmark = pytest.mark.unit


# ── _pretty_json ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("input_val,expected_substr", [
    ('{"a": 1}',         '"a": 1'),                  # valid JSON
    ('[1,2,3]',          '[\n  1,\n  2,\n  3'),      # JSON array
    ('{"кир": "ил"}',    '"кир": "ил"'),             # non-ASCII
    ('not json',         "not json"),                 # invalid → str fallback
    (None,               ""),                         # None → ""
    ("",                 ""),                         # empty string → "" (json.loads fails)
])
def test_pretty_json(input_val, expected_substr):
    from dashboard_filters import _pretty_json
    result = _pretty_json(input_val)
    assert expected_substr in result


def test_pretty_json_indents_to_2_spaces():
    from dashboard_filters import _pretty_json
    result = _pretty_json('{"a":{"b":1}}')
    assert '  "b": 1' in result


# ── _short ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("input_val,n,expected", [
    ("short", 80, "short"),
    ("a" * 81, 80, "a" * 80 + "…"),         # over limit → ellipsis
    ("a" * 80, 80, "a" * 80),                # exactly limit → no ellipsis
    (None, 80, ""),
    (12345, 3, "123…"),                       # non-string → coerce
    ("hi", 10, "hi"),
])
def test_short(input_val, n, expected):
    from dashboard_filters import _short
    assert _short(input_val, n) == expected


# ── _age_days ──────────────────────────────────────────────────────────────

def test_age_days_iso_date_format():
    from dashboard_filters import _age_days
    today = datetime.now()
    ts_7d_ago = (today - timedelta(days=7)).strftime("%Y-%m-%d")
    assert _age_days(ts_7d_ago) == 7


def test_age_days_with_time():
    from dashboard_filters import _age_days
    ts_3d_ago = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")
    assert _age_days(ts_3d_ago) == 3


@pytest.mark.parametrize("input_val", [None, "", "bogus", "20260522"])
def test_age_days_invalid_returns_none(input_val):
    from dashboard_filters import _age_days
    assert _age_days(input_val) is None


# ── _iso_date ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("input_val,expected", [
    ("2026-05-22",           "2026-05-22"),
    ("2026-05-22T10:30:00",  "2026-05-22"),
    ("2026-05-22 10:30:00",  "2026-05-22"),
    (None,                   ""),
    ("",                     ""),
    (12345,                  "12345"),  # coerce to str, takes first 10
])
def test_iso_date(input_val, expected):
    from dashboard_filters import _iso_date
    assert _iso_date(input_val) == expected
