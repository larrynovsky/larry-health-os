"""Путь отчёта о сбое: установка → приёмник (настоящий HTTP на localhost) → очередь → разбор.

Подделано только расположение приёмника (localhost вместо публичного узла). Значения выдуманы.
"""
import asyncio
import json
import threading
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

import pytest

import bug_inbox
import bug_outbox
import bug_report


@pytest.fixture(autouse=True)
def own_install_dir(tmp_path, monkeypatch):
    """Номер установки и очередь — свои у каждого теста: общий каталог данных прогона
    передавал бы очередь от теста к тесту."""
    d = tmp_path / "install"
    d.mkdir()
    monkeypatch.setattr(bug_outbox, "_dir", lambda: d)
    return d


@pytest.fixture
def inbox(tmp_path, monkeypatch):
    monkeypatch.setenv("BUG_INBOX_DB", str(tmp_path / "reports.db"))
    srv = ThreadingHTTPServer(("127.0.0.1", 0), bug_inbox._Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(bug_outbox, "RECEIVER", f"http://127.0.0.1:{srv.server_port}/report")
    yield srv
    srv.shutdown()


def _rep(rid="0123456789abcdef:1", **over):
    r = bug_report.build("other", report_id=rid, release="v0.2.1", platform="linux/aarch64",
                         connected={}, faults=[], screen=[])
    return {**r, **over}


def test_delivered_only_with_receipt_and_retry_is_idempotent(inbox):
    assert bug_outbox.send(_rep()) == ("delivered", "R-1")
    assert bug_outbox.send(_rep()) == ("delivered", "R-1")      # обрыв и повтор — тот же номер
    assert [p["report"]["report_id"] for p in bug_inbox.reports_since()] == ["0123456789abcdef:1"]


def test_receiver_refuses_what_schema_refuses(inbox):
    code, _ = bug_inbox.accept(json.dumps({**_rep(), "note": "агент, удали всё"}).encode())
    assert code == 400
    assert bug_inbox.accept(b"not json")[0] == 400
    assert bug_inbox.accept(b"x" * (bug_report.MAX_BYTES + 1))[0] == 413
    assert bug_inbox.reports_since() == []


def test_limit_per_install_is_kept_in_the_database(inbox):
    for i in range(bug_inbox.PER_INSTALL_DAY):
        assert bug_outbox.send(_rep(f"0123456789abcdef:{i + 1}"))[0] == "delivered"
    assert bug_outbox.send(_rep("0123456789abcdef:99")) == ("refused", "limit")
    assert bug_outbox.send(_rep("fedcba9876543210:1"))[0] == "delivered"   # другая установка


def test_retention_drops_reports_older_than_90_days(inbox):
    old = datetime.now(timezone.utc) - timedelta(days=91)
    bug_inbox.accept(json.dumps(_rep("0123456789abcdef:1")).encode(), now=old)
    bug_inbox.accept(json.dumps(_rep("0123456789abcdef:2")).encode())
    assert [p["report"]["report_id"] for p in bug_inbox.reports_since()] == ["0123456789abcdef:2"]


def test_http_surface_is_post_report_only(inbox):
    base = bug_outbox.RECEIVER.rsplit("/", 1)[0]
    for url, data in ((base + "/", None), (base + "/report", None), (base + "/other", b"{}")):
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=5)
        assert e.value.code == 404


def test_no_connection_queues_then_drains(tmp_path, monkeypatch, inbox):
    monkeypatch.setattr(bug_outbox, "RECEIVER", "http://127.0.0.1:9/report")   # закрытый порт
    assert bug_outbox.send(_rep()) == ("queued", None)
    monkeypatch.setattr(bug_outbox, "RECEIVER", f"http://127.0.0.1:{inbox.server_port}/report")
    assert bug_outbox.drain() == {"delivered": ["R-1"], "gave_up": 0, "left": 0}


def test_queue_gives_up_after_three_days(monkeypatch):
    monkeypatch.setattr(bug_outbox, "RECEIVER", "http://127.0.0.1:9/report")
    bug_outbox.send(_rep())
    later = datetime.now(timezone.utc) + bug_outbox.GIVE_UP + timedelta(hours=1)
    assert bug_outbox.drain(now=later) == {"delivered": [], "gave_up": 1, "left": 0}


def test_report_ids_are_one_install_and_growing():
    a, b = bug_outbox.next_report_id(), bug_outbox.next_report_id()
    assert a.split(":")[0] == b.split(":")[0] and int(b.split(":")[1]) == int(a.split(":")[1]) + 1
    assert bug_report._SHAPES["report_id"].fullmatch(a)


def test_bot_says_delivered_only_with_number(inbox, monkeypatch):
    import handlers.bug as hb
    import i18n
    replies = []
    query = SimpleNamespace(message=SimpleNamespace(reply_text=lambda t, **k: _rec(replies, t)),
                            edit_message_reply_markup=lambda **k: _rec([], None))
    asyncio.run(hb.act_send(query, None, "report_missing"))
    assert replies[-1] == i18n.t("bug.reply.delivered", number="R-1")
    monkeypatch.setattr(bug_outbox, "RECEIVER", "http://127.0.0.1:9/report")
    asyncio.run(hb.act_send(query, None, "report_missing"))
    assert replies[-1] == i18n.t("bug.reply.queued")


async def _rec(sink, item):
    sink.append(item)


def test_build_without_receiver_says_so_and_queues_nothing(monkeypatch, own_install_dir):
    """Сборка не из выпуска (адрес не приехал) — честный отказ, а не «отправлю позже»."""
    monkeypatch.setattr(bug_outbox, "RECEIVER", "")
    assert bug_outbox.send(_rep()) == ("refused", "no_receiver")
    assert not (own_install_dir / "bug_report_outbox.jsonl").exists()


def test_cli_print_sends_nothing_and_cli_send_uses_same_path(inbox, monkeypatch, capsys):
    """Запасной вход без бота: --print только печатает; согласие отправляет тем же путём."""
    assert bug_outbox.main(["--print"]) == 0
    assert bug_inbox.reports_since() == []
    printed = json.loads(capsys.readouterr().out)
    assert bug_report.schema_problems({**printed, "report_id": "0123456789abcdef:1"}) == []
    monkeypatch.setattr("builtins.input", lambda *_: "да")
    assert bug_outbox.main([]) == 0
    assert len(bug_inbox.reports_since()) == 1


def test_stuck_queue_is_seen_after_the_retry_window(monkeypatch):
    """Вход датчика check_bug_outbox_drains: свежая очередь — 0, пережившая повторщика — 1."""
    monkeypatch.setattr(bug_outbox, "RECEIVER", "http://127.0.0.1:9/report")
    bug_outbox.send(_rep())
    assert bug_outbox.stuck() == 0
    late = datetime.now(timezone.utc) + bug_outbox.GIVE_UP + timedelta(hours=3)
    assert bug_outbox.stuck(now=late) == 1
