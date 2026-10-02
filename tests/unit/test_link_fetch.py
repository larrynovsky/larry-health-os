"""link_fetch: файл по ссылке облака или пути на диске → входящие (нить genome-link, 24.09).

Сеть в тестах не трогается: DNS и ответы облаков подменены. Главные контроли — отрицательные:
SSRF (WSTG-INPV-19) — внутренний адрес, обход списка через похожий домен и «@», перенаправление
на внутренний адрес; имя файла от облака не становится путём; оборванная закачка не оставляет
полфайла во входящих.
"""
from __future__ import annotations

import io
import json
import socket
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def fake_dns(monkeypatch):
    table = {"evil.io": "203.0.113.9", "intranet.drive.google.com": "127.0.0.1",
             "tail.dropbox.com": "100.101.5.7"}
    def gai(host, port, *a, **k):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (table.get(host, "142.250.1.1"), port))]
    monkeypatch.setattr(socket, "getaddrinfo", gai)


@pytest.mark.parametrize("text,kind", [
    ("вот мой геном https://drive.google.com/file/d/1AbCdEfGhIjK/view?usp=sharing", "url"),
    ("https://disk.yandex.ru/d/AbCd123", "url"),
    ("https://www.dropbox.com/s/abc/genome.zip?dl=0", "url"),
    ("почитай https://www.nature.com/articles/x — интересно", None),     # статья, не облако
    ("https://drive.google.com.evil.io/file/d/123", None),
    ("https://drive.google.com@evil.io/file", None),
])
def test_parse_links(text, kind):
    import link_fetch
    r = link_fetch.parse(text)
    assert (r or {}).get("kind") == kind, r


def test_parse_paths(tmp_path):
    import link_fetch
    f = tmp_path / "мой геном.txt"
    f.write_text("x")
    assert link_fetch.parse(str(f)) == {"kind": "path", "path": str(f.resolve())}
    assert link_fetch.parse(f'"{f}"')["kind"] == "path"                 # путь в кавычках
    assert link_fetch.parse("file://" + str(f).replace(" ", "%20"))["kind"] == "path"
    with pytest.raises(link_fetch._HumanError):
        link_fetch.parse(str(tmp_path / "нет.txt"))
    assert link_fetch.parse("/done 12 ответ") is None                     # команда, не файл


@pytest.mark.parametrize("lang", ["ru", "en"])
@pytest.mark.parametrize("home_relative", [False, True])
def test_unavailable_file_path_replies_without_chat(tmp_path, monkeypatch, lang, home_relative):
    import i18n, link_fetch
    from pathlib import Path
    from handlers import messages
    from bot import helpers
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(link_fetch.os.path, "expanduser", lambda p: str(tmp_path / p[2:]) if p.startswith("~/") else p)
    monkeypatch.setattr(i18n, "lang_of", lambda: lang)
    chat = Mock(return_value="ordinary chat")
    monkeypatch.setattr(messages.ai, "chat", chat)
    monkeypatch.setattr(messages.db, "get_open_tasks", lambda n: [])
    monkeypatch.setattr(messages.ck, "checkin_state", SimpleNamespace(active=False))
    monkeypatch.setattr(messages.abh, "get_active_assessment", lambda cid: None)
    monkeypatch.setattr(messages, "send_long", AsyncMock())
    monkeypatch.setattr(helpers, "_run_arbiter_background", AsyncMock())
    monkeypatch.setattr(helpers, "_service_trouble_background", AsyncMock())
    enqueue = Mock()
    monkeypatch.setattr(link_fetch, "enqueue", enqueue)
    text = "~/genome.txt" if home_relative else str(tmp_path / "missing.txt")
    message = SimpleNamespace(text=text, reply_to_message=None, reply_text=AsyncMock(),
                              chat=SimpleNamespace(id=7, send_action=AsyncMock()), get_bot=lambda: None)
    update = SimpleNamespace(message=message, effective_chat=message.chat)
    asyncio.run(messages.handle_text(update, SimpleNamespace(user_data={})))
    message.reply_text.assert_awaited_once_with(i18n.t("intake.link.path_unavailable", lang))
    chat.assert_not_called()
    enqueue.assert_not_called()


@pytest.mark.parametrize("text", ["/done", "/done 12", "/folder", "/folder/",
                                      "/missing.txt is a file", "~/folder", "relative.txt"])
def test_path_heuristic_leaves_commands_and_prose_as_chat(text, monkeypatch):
    import link_fetch
    monkeypatch.setattr(link_fetch.Path, "is_file", lambda p: False)
    assert link_fetch.parse(text) is None


@pytest.mark.parametrize("url,ok", [
    ("https://drive.google.com/x", True),
    ("http://drive.google.com/x", False),                  # только https
    ("https://intranet.drive.google.com/x", False),        # резолвится в 127.0.0.1
    ("https://tail.dropbox.com/x", False),                 # 100.64/10 — адрес Tailscale
    ("https://127.0.0.1:8000/", False),
    ("file:///etc/passwd", False),
])
def test_url_guard(url, ok):
    import link_fetch
    assert link_fetch._url_ok(url) is ok


def test_direct_urls(monkeypatch):
    import link_fetch
    g = link_fetch._direct_url("https://drive.google.com/file/d/1AbCdEfGhIjK/view?usp=sharing")
    assert g.startswith("https://drive.usercontent.google.com/download?") and "id=1AbCdEfGhIjK" in g
    d = link_fetch._direct_url("https://www.dropbox.com/s/abc/genome.zip?dl=0")
    assert "dl=1" in d and "dl=0" not in d
    o = link_fetch._direct_url("https://1drv.ms/u/s!Abc")
    assert o.startswith("https://api.onedrive.com/v1.0/shares/u!") and "=" not in o.split("u!")[1]
    monkeypatch.setattr(link_fetch, "_open", lambda u: _Resp(json.dumps(
        {"href": "https://downloader.disk.yandex.ru/disk/abc"}).encode()))
    assert link_fetch._direct_url("https://disk.yandex.ru/d/Ab").startswith("https://downloader.")


def test_redirect_to_internal_is_refused():
    import urllib.request
    import link_fetch
    h = link_fetch._CheckedRedirect()
    req = urllib.request.Request("https://drive.google.com/x")
    with pytest.raises(ValueError):
        h.redirect_request(req, None, 302, "Found", {}, "http://127.0.0.1:8765/admin")
    with pytest.raises(ValueError):
        h.redirect_request(req, None, 302, "Found", {}, "https://evil.io/x")


class _Resp(io.BytesIO):
    def __init__(self, body: bytes, headers: dict | None = None):
        super().__init__(body)
        self.headers = headers or {}


def test_fetch_names_file_safely_and_caps_size(tmp_path, monkeypatch):
    import link_fetch
    body = b"PK\x03\x04" + b"0" * 5000
    monkeypatch.setattr(link_fetch, "_open", lambda u: _Resp(
        body, {"Content-Type": "application/zip",
               "Content-Disposition": 'attachment; filename="../../evil genome.zip"'}))
    dest = link_fetch._fetch("https://drive.google.com/file/d/1AbCdEfGhIjK/view", tmp_path)
    assert dest.parent == tmp_path and dest.name.startswith("evil genome__") and dest.suffix == ".zip"
    assert dest.read_bytes() == body
    monkeypatch.setattr(link_fetch, "_max_bytes", lambda: 1000)
    with pytest.raises(ValueError):
        link_fetch._fetch("https://drive.google.com/file/d/2ZzZzZzZzZzZ/view", tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == [dest.name], "полфайла не остаётся"


def test_google_confirmation_page_is_followed(tmp_path, monkeypatch):
    import link_fetch
    page = ('<form id="download-form" action="https://drive.usercontent.google.com/download" method="get">'
            '<input type="hidden" name="id" value="1Ab"><input type="hidden" name="confirm" value="t">'
            '<input type="hidden" name="uuid" value="u-1"></form>').encode()
    seen = []
    def fake_open(u):
        seen.append(u)
        return _Resp(page, {"Content-Type": "text/html"}) if len(seen) == 1 else _Resp(b"%PDF-1.4 x")
    monkeypatch.setattr(link_fetch, "_open", fake_open)
    dest = link_fetch._fetch("https://drive.google.com/file/d/1AbCdEfGhIjK/view", tmp_path)
    assert "uuid=u-1" in seen[1] and dest.suffix == ".pdf"


def test_request_queue_path_to_genome(tmp_path, monkeypatch):
    """Путь из бота → заявка → разборщик копирует во входящие → genome_intake узнаёт геном."""
    import link_fetch, genome_intake
    told = []
    monkeypatch.setattr(link_fetch, "_tell", told.append)
    src = tmp_path / "home" / "genome_x.txt"
    src.parent.mkdir()
    src.write_text("# 23andMe\n" + "".join(f"rs{i}\t1\t{100 + i}\tAG\n" for i in range(30)))
    inbox = tmp_path / "incoming"
    receipt = link_fetch.enqueue(link_fetch.parse(str(src)), inbox)
    assert "genome_x.txt" in receipt
    assert link_fetch.process_requests(inbox) == 1
    copied = [p for p in inbox.iterdir() if p.suffix == ".txt"]
    assert len(copied) == 1 and genome_intake.sniff(copied[0])["supported"]
    assert link_fetch.process_requests(inbox) == 0                        # заявка закрыта
    assert told and "genome_x" in told[0]


def test_failed_request_is_loud_and_closed(tmp_path, monkeypatch):
    import link_fetch
    told = []
    monkeypatch.setattr(link_fetch, "_tell", told.append)
    def boom(u):
        raise ValueError("облако вернуло страницу вместо файла")
    monkeypatch.setattr(link_fetch, "_open", boom)
    link_fetch.enqueue({"kind": "url", "url": "https://www.dropbox.com/s/x/g.zip?dl=0"}, tmp_path)
    link_fetch.process_requests(tmp_path)
    link_fetch.process_requests(tmp_path)
    assert len(told) == 1 and "Не смог забрать файл" in told[0]
    st = json.loads(next(tmp_path.glob("*.request.json")).read_text())
    assert st["status"] == "failed"


def test_http_404_is_explained_in_words(tmp_path, monkeypatch):
    """Живой замер 24.09: Google, Яндекс и OneDrive на закрытую/несуществующую ссылку отвечают 404."""
    import urllib.error
    import link_fetch
    told = []
    monkeypatch.setattr(link_fetch, "_tell", told.append)
    def nf(u):
        raise urllib.error.HTTPError(u, 404, "Not Found", {}, None)
    monkeypatch.setattr(link_fetch, "_open", nf)
    link_fetch.enqueue({"kind": "url", "url": "https://disk.yandex.ru/d/x"}, tmp_path)
    link_fetch.process_requests(tmp_path)
    assert "не найден или доступ по ссылке закрыт" in told[0]


def test_lookalike_hosts_are_not_cloud(monkeypatch):
    """CodeQL #37–#39: «evilgoogle.com» и «notdropbox.com» не переписываются в ссылку облака.
    Падение = проверка хоста снова по голому суффиксу без точки."""
    import link_fetch
    for url in ("https://evilgoogle.com/file/d/1AbCdEfGhIjK/view", "https://notdropbox.com/s/abc/x.zip?dl=0"):
        assert link_fetch._direct_url(url) == url
