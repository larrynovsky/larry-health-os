"""Ревью алертов после пересборки конституций доходит, даже если модель сломала Markdown.

25.09 первое же ревью упало на HTTP 400 (непарная разметка в тексте модели) и не дошло до
владельца. Оракул: 400 на Markdown → тот же кусок уходит простым текстом; прочая ошибка → False.
Сеть подменена: тест не имеет права дойти до настоящего Telegram.
"""
import io
import json
import urllib.error
import urllib.request

import generate_constitutions as gc


def _secrets(tmp_path, monkeypatch):
    (tmp_path / "telegram_token").write_text("T")
    (tmp_path / "telegram_chat_id").write_text("1")
    import secrets_paths
    monkeypatch.setattr(secrets_paths, "secrets_dir", lambda *a, **k: tmp_path)


def test_markdown_400_falls_back_to_plain(tmp_path, monkeypatch):
    _secrets(tmp_path, monkeypatch)
    sent = []

    def fake(req, timeout=10):
        body = json.loads(req.data)
        sent.append(body)
        if "parse_mode" in body:
            raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", {}, io.BytesIO(b""))
        return io.BytesIO(b"{}")

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    assert gc._tg_notify("ревью *без пары") is True
    assert [("parse_mode" in b) for b in sent] == [True, False]


def test_other_error_is_reported_not_retried(tmp_path, monkeypatch):
    _secrets(tmp_path, monkeypatch)
    calls = []

    def fake(req, timeout=10):
        calls.append(1)
        raise urllib.error.URLError("сеть лежит")

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    assert gc._tg_notify("текст") is False
    assert len(calls) == 1
