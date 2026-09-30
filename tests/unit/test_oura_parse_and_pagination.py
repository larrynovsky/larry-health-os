"""W5K #172/#173: тесты импортёра Oura.

#172: parse_spo2 path bug
  parse_spo2 искал s["average"]["percentage"], а Oura отдаёт
  s["spo2_percentage"]["average"]. Был молчаливый NULL в spo2_avg.

#173: oura_get pagination
  При больших окнах sleep endpoint имеет next_token. Без следования за ним
  теряли последние 12 месяцев sleep сессий молча. Привело к destructive
  backfill 2026-05-14 (потеряли данные, восстановили из backup).
"""
from __future__ import annotations

from unittest import mock

import pytest

pytestmark = pytest.mark.unit


# ── #172: parse_spo2 ───────────────────────────────────────────────────────

def test_parse_spo2_reads_spo2_percentage_average():
    """Новый формат Oura: spo2_percentage.average."""
    from import_oura import parse_spo2

    data = [
        {
            "day": "2040-04-09",
            "spo2_percentage": {"average": 97.6},
            "breathing_disturbance_index": 4,
        }
    ]
    result = parse_spo2(data)
    assert "2040-04-09" in result
    assert result["2040-04-09"]["spo2"]["avg"] == 97.6
    assert result["2040-04-09"]["breathing_disturbance"] == 4


def test_parse_spo2_handles_null_spo2_percentage():
    """Когда Oura не считал SpO2 в эту ночь (часто) — должен вернуть None,
    не упасть."""
    from import_oura import parse_spo2

    data = [
        {
            "day": "2025-06-01",
            "spo2_percentage": None,
            "breathing_disturbance_index": None,
        }
    ]
    result = parse_spo2(data)
    assert result["2025-06-01"]["spo2"]["avg"] is None
    assert result["2025-06-01"]["breathing_disturbance"] is None


def test_parse_spo2_handles_missing_spo2_key():
    """Backward compat: если ключа нет вообще — не падать."""
    from import_oura import parse_spo2

    data = [{"day": "2024-01-01", "breathing_disturbance_index": 1}]
    result = parse_spo2(data)
    assert result["2024-01-01"]["spo2"]["avg"] is None
    assert result["2024-01-01"]["breathing_disturbance"] == 1


# ── #173: oura_get pagination ─────────────────────────────────────────────

class _FakeResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_oura_get_follows_next_token_through_pages():
    """oura_get должен собрать сессии со всех страниц next_token."""
    import json
    from pathlib import Path
    import import_oura

    pages = [
        {"data": [{"day": "2024-01-01"}, {"day": "2024-02-01"}], "next_token": "page2"},
        {"data": [{"day": "2025-01-01"}, {"day": "2025-06-01"}], "next_token": "page3"},
        {"data": [{"day": "2026-05-01"}], "next_token": None},
    ]
    responses = [_FakeResponse(json.dumps(p).encode()) for p in pages]
    call_urls: list[str] = []

    def fake_urlopen(req, timeout=30):
        call_urls.append(req.full_url)
        return responses[len(call_urls) - 1]

    with mock.patch.object(import_oura, "get_token", return_value="TKN"), \
         mock.patch("import_oura.urllib.request.urlopen", side_effect=fake_urlopen):
        result = import_oura.oura_get("sleep", "2024-01-01", "2026-05-14")

    assert len(result) == 5, f"ожидали 5 сессий со всех страниц, получили {len(result)}"
    days = [r["day"] for r in result]
    assert days == ["2024-01-01", "2024-02-01", "2025-01-01", "2025-06-01", "2026-05-01"]
    # Каждый последующий URL должен содержать next_token
    assert "next_token=page2" in call_urls[1], f"page2 URL без next_token: {call_urls[1]}"
    assert "next_token=page3" in call_urls[2], f"page3 URL без next_token: {call_urls[2]}"


def test_oura_get_single_page_when_no_next_token():
    """Если next_token нет — один запрос, не пытаться идти дальше."""
    import json
    import import_oura

    response = _FakeResponse(json.dumps({"data": [{"day": "2040-04-09"}], "next_token": None}).encode())
    call_count = {"n": 0}

    def fake_urlopen(req, timeout=30):
        call_count["n"] += 1
        return response

    with mock.patch.object(import_oura, "get_token", return_value="TKN"), \
         mock.patch("import_oura.urllib.request.urlopen", side_effect=fake_urlopen):
        result = import_oura.oura_get("daily_sleep", "2040-04-09", "2040-04-09")

    assert len(result) == 1
    assert call_count["n"] == 1, "не должно быть лишних запросов когда next_token=None"


def test_oura_get_hits_safety_limit_at_20_pages():
    """Защита от бесконечного цикла: ограничение 20 страниц."""
    import json
    import import_oura

    # Сервер возвращает один и тот же next_token бесконечно
    def fake_urlopen(req, timeout=30):
        return _FakeResponse(
            json.dumps({"data": [{"day": "x"}], "next_token": "infinite"}).encode()
        )

    with mock.patch.object(import_oura, "get_token", return_value="TKN"), \
         mock.patch("import_oura.urllib.request.urlopen", side_effect=fake_urlopen):
        result = import_oura.oura_get("sleep", "2024-01-01", "2026-05-14")

    # Должно остановиться на 20 страницах (защитник от infinite loop)
    assert len(result) == 20, f"safety limit не сработал: {len(result)} pages"
