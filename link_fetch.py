#!/usr/bin/env python3.11
"""link_fetch.py — файл по ссылке из облака или по пути на диске → входящие тенанта.

Один домен: «сообщение со ссылкой/путём → файл во входящих». Что это за файл, решают те же
разборщики, что и для файла из Telegram (genome_intake, распознаватель анализов, разбор
заключений) — этот модуль только доставляет байты туда, где их ждут.

Зачем: Telegram отдаёт боту файлы до 20 МБ, а сырой геном без архива весит больше; с телефона
проще поделиться ссылкой, чем упаковать zip. Путь на диске — решение владельца 24.09: «в боте
путь указать тоже можно» (бот и разборщик работают на одной машине).

Безопасность ссылки (WSTG-INPV-19, SSRF): только https, только облака из ALLOWED — сверка по
границе домена того же разобранного URL, которым потом качаем; каждое перенаправление
проверяется заново; адрес, который резолвится во внутреннюю сеть (127.x, 10.x, 100.64/10
Tailscale…), отклоняется. Качаем потоком во временный файл с потолком байт и таймаутом;
во входящие файл попадает только целиком (rename).

Очередь: бот пишет <inbox>/<id>.request.json и сразу отвечает; скачивает lab_intake_watcher
(process_requests) — долгое скачивание не держит бота.
"""
from __future__ import annotations

import base64
import i18n
import notify
import hashlib
import ipaddress
import json
import logging
import os
import re
import shutil
import socket
import time
import urllib.parse
import urllib.request
from pathlib import Path

log = logging.getLogger("link_fetch")

ALLOWED = ("drive.google.com", "docs.google.com", "drive.usercontent.google.com",
           "googleusercontent.com", "dropbox.com", "dropboxusercontent.com",
           "disk.yandex.ru", "disk.yandex.com", "disk.yandex.kz", "yadi.sk",
           "cloud-api.yandex.net", "downloader.disk.yandex.ru", "storage.yandex.net",
           "1drv.ms", "onedrive.live.com", "api.onedrive.com", "1drv.com", "sharepoint.com")
TIMEOUT = 60
REQ_SUFFIX = ".request.json"
_URL = re.compile(r"https?://[^\s<>\"']+", re.I)
PROVIDERS_TEXT = "intake.link.providers"


def display_filename(name: str) -> str:
    """Hide inbox hash suffixes in replies; stored names stay unchanged."""
    return re.sub(r"__(?:[0-9a-f]{16}|[0-9a-f]{12})(?=\.[^.]+$|$)", "", Path(name).name)


class _HumanError(ValueError):
    """An expected download refusal whose message is safe to show to the person."""


def _max_bytes() -> int:
    import genome_intake            # один потолок с разборщиком: больше он всё равно не возьмёт
    return genome_intake.MAX_DOWNLOAD_BYTES


def _host_ok(host: str | None) -> bool:
    h = (host or "").lower().rstrip(".")
    return any(h == a or h.endswith("." + a) for a in ALLOWED)


def _host_ok_for(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def _url_ok(url: str) -> bool:
    """https, без логина в адресе, хост из списка и НЕ внутренний адрес."""
    p = urllib.parse.urlsplit(url)
    if p.scheme != "https" or p.username or p.password or not _host_ok(p.hostname):
        return False
    try:
        infos = socket.getaddrinfo(p.hostname, p.port or 443, proto=socket.IPPROTO_TCP)
    except OSError:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved \
                or ip.is_multicast or ip in ipaddress.ip_network("100.64.0.0/10"):
            return False
    return True


def parse(text: str) -> dict | None:
    """Сообщение → {'kind': 'url'|'path', ...} или None. Ссылка — только на облако из списка
    (статья в разговоре не должна уехать в скачивание); путь — только если файл существует."""
    t = (text or "").strip()
    for m in _URL.finditer(t):
        url = m.group(0).rstrip(").,;»")
        if _host_ok(urllib.parse.urlsplit(url).hostname):
            return {"kind": "url", "url": url}
    cand = t.strip("\"'«» ")
    if cand.lower().startswith("file://"):
        cand = urllib.parse.unquote(urllib.parse.urlsplit(cand).path)
    if cand.startswith(("/", "~")) and "\n" not in cand:
        p = Path(os.path.expanduser(cand))
        if p.is_file():
            return {"kind": "path", "path": str(p.resolve())}
    return None


class _CheckedRedirect(urllib.request.HTTPRedirectHandler):
    """Каждое перенаправление проверяется тем же правилом, что и исходная ссылка."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        newurl = urllib.parse.urljoin(req.full_url, newurl)
        if not _url_ok(newurl):
            raise _HumanError(i18n.t("intake.link.redirect_denied", host=urllib.parse.urlsplit(newurl).hostname))
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open(url: str):
    if not _url_ok(url):
        raise _HumanError(i18n.t("intake.link.host_denied", host=urllib.parse.urlsplit(url).hostname))
    opener = urllib.request.build_opener(_CheckedRedirect)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (health-os link_fetch)"})
    return opener.open(req, timeout=TIMEOUT)


def _direct_url(url: str) -> str:
    """Ссылка «поделиться» → ссылка на сами байты (своя у каждого облака)."""
    p = urllib.parse.urlsplit(url)
    host = (p.hostname or "").lower()
    q = urllib.parse.parse_qs(p.query)
    # Точный суффикс через точку: «evilgoogle.com» — не Google (CodeQL #37–#39; _url_ok и так
    # отсёк бы такой адрес при скачивании, но разбор ссылки не должен на это опираться).
    if _host_ok_for(host, "google.com") and host != "drive.usercontent.google.com":
        m = re.search(r"/d/([\w-]{10,})", p.path)
        fid = m.group(1) if m else (q.get("id") or [None])[0]
        if fid:
            return ("https://drive.usercontent.google.com/download?"
                    + urllib.parse.urlencode({"id": fid, "export": "download", "confirm": "t"}))
    if _host_ok_for(host, "dropbox.com"):
        q.pop("dl", None)
        q["dl"] = ["1"]
        return urllib.parse.urlunsplit(p._replace(query=urllib.parse.urlencode(q, doseq=True)))
    if host in ("disk.yandex.ru", "disk.yandex.com", "disk.yandex.kz", "yadi.sk"):
        api = ("https://cloud-api.yandex.net/v1/disk/public/resources/download?"
               + urllib.parse.urlencode({"public_key": url}))
        with _open(api) as r:
            href = json.loads(r.read(64 * 1024).decode("utf-8")).get("href")
        if not href:
            raise _HumanError(i18n.t("intake.link.yandex_unavailable"))
        return href
    if host in ("1drv.ms", "onedrive.live.com"):
        token = base64.urlsafe_b64encode(url.encode()).decode().rstrip("=")
        return f"https://api.onedrive.com/v1.0/shares/u!{token}/root/content"
    return url


def _filename(resp, fallback: str) -> str:
    cd = resp.headers.get("Content-Disposition") or ""
    m = re.search(r"filename\*=(?:UTF-8'')?([^;]+)", cd, re.I) or re.search(r'filename="?([^";]+)', cd, re.I)
    name = urllib.parse.unquote(m.group(1)).strip().strip('"') if m else ""
    name = Path(name.replace("\\", "/")).name.lstrip(".")      # имя от облака путём не становится
    return name[:120] or fallback


def _ext_by_magic(head: bytes) -> str:
    return ".pdf" if head.startswith(b"%PDF") else ".zip" if head.startswith(b"PK\x03\x04") else \
           ".gz" if head.startswith(b"\x1f\x8b") else \
           ".jpg" if head.startswith(b"\xff\xd8") else ".png" if head.startswith(b"\x89PNG") else ".txt"


def _stream(resp, dest: Path, cap: int) -> int:
    n = 0
    with open(dest, "wb") as w:
        while chunk := resp.read(1 << 20):
            n += len(chunk)
            if n > cap:
                raise _HumanError(i18n.t("intake.link.too_large", size_mb=cap // (1024 * 1024)))
            w.write(chunk)
    return n


def _fetch(url: str, inbox: Path) -> Path:
    """Скачать файл по ссылке облака во входящие. Возвращает путь к файлу во входящих."""
    inbox = Path(inbox)
    tmp = inbox / f".link_{hashlib.sha256(url.encode()).hexdigest()[:12]}.part"
    try:
        r = _open(_direct_url(url))
        try:
            if "text/html" in (r.headers.get("Content-Type") or "").lower():
                page = r.read(512 * 1024).decode("utf-8", errors="ignore")
                form = re.search(r'<form[^>]+id="download-form"[^>]+action="([^"]+)"', page)
                if not form:        # Google для больших файлов показывает страницу проверки
                    raise _HumanError(i18n.t("intake.link.page_instead"))
                fields = dict(re.findall(r'name="([^"]+)" value="([^"]*)"', page))
                r.close()
                r = _open(form.group(1) + "?" + urllib.parse.urlencode(fields))
            size = _stream(r, tmp, _max_bytes())
            with open(tmp, "rb") as fh:
                head = fh.read(8)
            name = _filename(r, f"link_{tmp.stem[6:]}{_ext_by_magic(head)}")
        finally:
            r.close()
        dest = inbox / f"{Path(name).stem}__{tmp.stem[6:]}{Path(name).suffix or _ext_by_magic(head)}"
        tmp.rename(dest)
        log.info(f"link_fetch: {size} байт → {dest.name}")
        return dest
    finally:
        tmp.unlink(missing_ok=True)


def _copy_local(path: str, inbox: Path) -> Path:
    src = Path(path)
    if src.stat().st_size > _max_bytes():
        raise _HumanError(i18n.t("intake.link.too_large", size_mb=_max_bytes() // (1024 * 1024)))
    h = hashlib.sha256(str(src).encode()).hexdigest()[:12]
    dest = Path(inbox) / f"{src.stem}__{h}{src.suffix}"
    tmp = Path(inbox) / f".link_{h}.part"
    shutil.copyfile(src, tmp)
    tmp.rename(dest)
    return dest


def enqueue(req: dict, inbox: Path) -> str:
    """Бот: положить заявку и вернуть текст квитанции (скачивание — у разборщика)."""
    inbox = Path(inbox)
    inbox.mkdir(parents=True, exist_ok=True)
    key = req.get("url") or req.get("path")
    rid = hashlib.sha256(key.encode()).hexdigest()[:12]
    (inbox / f"link_{rid}{REQ_SUFFIX}").write_text(
        json.dumps({**req, "status": "pending", "at": time.time()}, ensure_ascii=False))
    if req["kind"] == "url":
        return i18n.t("intake.link.received")
    return i18n.t("intake.link.path_received", name=display_filename(Path(key).name))


def _tell(msg: str) -> None:
    try:
        import notify
        notify.notify(msg)
    except Exception as e:  # silent-ok: итог записан в заявке, доставка best-effort
        log.error(f"link_fetch: уведомление не ушло: {e}")


def process_requests(inbox: Path) -> int:
    """Проход разборщика: заявки → файлы во входящих. Возвращает число обработанных заявок."""
    inbox = Path(inbox)
    n = 0
    for rq in sorted(inbox.glob(f"*{REQ_SUFFIX}")) if inbox.is_dir() else []:
        try:
            req = json.loads(rq.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if req.get("status") != "pending":
            continue
        try:
            dest = _fetch(req["url"], inbox) if req["kind"] == "url" else _copy_local(req["path"], inbox)
            req.update(status="done", file=dest.name)
            sz = dest.stat().st_size        # живая проба 24.09: маленький файл печатался «0.0 МБ»
            _tell(i18n.t("intake.link.processing_mb", name=display_filename(dest.name), size=sz / 1e6) if sz >= 100_000
                  else i18n.t("intake.link.processing_kb", name=display_filename(dest.name), size=max(1, round(sz / 1e3))))
        except Exception as e:
            req.update(status="failed", error=str(e)[:300])
            code = getattr(e, "code", None)       # HTTPError: живой замер 24.09 — облака отвечают 404
            tech = f"link_fetch.process_requests request_id={rq.name.removesuffix(REQ_SUFFIX)}: {type(e).__name__}: {e}"
            if code in (401, 403, 404) or isinstance(e, _HumanError):
                why = i18n.t("intake.link.unavailable") if code in (401, 403, 404) else str(e)
                _tell(notify.fault(tech, person_key="intake.link.failed", reason=why,
                                   providers=i18n.t(PROVIDERS_TEXT)))
            else:
                _tell(notify.fault(tech, person_key="intake.link.retry_later"))
        rq.write_text(json.dumps(req, ensure_ascii=False))
        n += 1
    return n
