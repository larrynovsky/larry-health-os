"""Почтовый транспорт: что уходит, чего не уходит, и что не уходит НИКОГДА.

Заказ BL-STALLED-THREADS-1: «положить готовый текстовый блок в письмо владельцу».
Здесь оракулы на три вещи, каждая из которых уже стоила проекту или могла стоить:

  • тема письма собирается из текста, который писала МОДЕЛЬ → перевод строки в
    заголовке = инъекция заголовков SMTP (WSTG-INPV-10);
  • нет секретов → канал молчит и возвращает False, а не падает и не шлёт
    полуфабрикат;
  • §19 — значение пароля не покидает машину: его нет ни в возврате, ни в stderr.

Настоящий SMTP не дёргается: smtplib.SMTP_SSL подменён. Секреты — файлы в tmp.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import notify  # noqa: E402

SECRET = "паароль-которого-не-должно-быть-в-выводе"


class _FakeSMTP:
    sent = []
    logins = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port = host, port

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def login(self, user, pwd):
        _FakeSMTP.logins.append((user, pwd))

    def send_message(self, m):
        _FakeSMTP.sent.append(m)


@pytest.fixture
def smtp(tmp_path, monkeypatch):
    import smtplib
    _FakeSMTP.sent, _FakeSMTP.logins = [], []
    monkeypatch.setattr(smtplib, "SMTP_SSL", _FakeSMTP)
    d = tmp_path / "secrets"; d.mkdir()
    monkeypatch.setattr(notify, "owner_secrets_dir", lambda: d)
    return d


def _configure(d):
    (d / "smtp_host").write_text("smtp.example.org")
    (d / "smtp_user").write_text("owner@example.org")
    (d / "smtp_password").write_text(SECRET)


def test_sends_when_configured(smtp):
    _configure(smtp)
    assert notify.email_owner("Недельная сводка", "тело отчёта") is True
    m = _FakeSMTP.sent[0]
    assert m["Subject"] == "Недельная сводка"
    assert m["To"] == "owner@example.org"
    assert "тело отчёта" in m.get_content()


def test_crlf_in_subject_cannot_inject_headers(smtp):
    """WSTG-INPV-10. Тему пишет модель; перевод строки в ней добавил бы свой
    заголовок — например Bcc. Письмо обязано уйти С ОДНИМ адресатом."""
    _configure(smtp)
    assert notify.email_owner(
        "Сводка\r\nBcc: attacker@evil.example\r\nX-Injected: 1", "тело") is True
    m = _FakeSMTP.sent[0]
    assert m["Bcc"] is None, "инъекция заголовка прошла — это утечка адресата"
    assert m["X-Injected"] is None
    assert "\n" not in m["Subject"] and "\r" not in m["Subject"]
    assert "Bcc" in m["Subject"], "текст не выброшен, он обезврежен в одну строку"


def test_unconfigured_channel_is_silent_false(smtp, capsys):
    """Нет секретов — не падаем и не шлём: канал просто не настроен."""
    assert notify.email_owner("тема", "тело") is False
    assert _FakeSMTP.sent == []
    assert "не настроен" in capsys.readouterr().err


def test_partial_config_is_not_enough(smtp):
    """Хост без пароля — не «почти настроено», а не настроено (fail-closed)."""
    (smtp / "smtp_host").write_text("smtp.example.org")
    (smtp / "smtp_user").write_text("owner@example.org")
    assert notify.email_owner("тема", "тело") is False


def test_secret_value_never_leaves(smtp, capsys):
    """§19. Пароль используется в рантайме и НЕ появляется в выводе — ни при
    успехе, ни при провале доставки."""
    _configure(smtp)

    def boom(*a, **k):
        raise OSError("connection refused")

    import smtplib
    monkey = smtplib.SMTP_SSL
    try:
        smtplib.SMTP_SSL = boom
        assert notify.email_owner("тема", "тело") is False
    finally:
        smtplib.SMTP_SSL = monkey
    out = capsys.readouterr()
    assert SECRET not in out.err and SECRET not in out.out
    assert "не ушло" in out.err, "провал доставки обязан быть виден в логе"


def test_failed_delivery_returns_false_not_raises(smtp):
    """Отчёт не имеет права ронять вызывающего."""
    _configure(smtp)

    class Boom(_FakeSMTP):
        def send_message(self, m):
            raise TimeoutError("сеть легла")

    import smtplib
    monkey = smtplib.SMTP_SSL
    try:
        smtplib.SMTP_SSL = Boom
        assert notify.email_owner("тема", "тело") is False
    finally:
        smtplib.SMTP_SSL = monkey
