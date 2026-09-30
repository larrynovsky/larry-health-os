#!/usr/bin/env python3.11
"""
notify.py — доставка алертов с резервным НЕЗАВИСИМЫМ каналом.

Telegram = единая точка отказа: бот лёг / токен протух / нет сети → алерт
пропадает молча. Здесь: если Telegram-отправка не прошла, best-effort пинг
healthchecks.io/fail с текстом → email (канал, не зависящий от Telegram).

ВНИМАНИЕ: healthcheck_url — тот же, что у dead-man's switch run_checks. Пинг
/fail пометит его down → придёт email; следующий чистый прогон 07:50 вернёт
success. Конфляция осознанная: лучше «лишний» email, чем тихая потеря алерта.

notify(msg) / notify_operator(msg) — личное и требующее внимания, в Telegram;
weekly(line) — сведения в понедельничную очередь; fault(tech, person_key=None) —
технический отказ в журнал ночного цикла, без отправки человеку.
email_owner(subject, body) — редкое и читаемое целиком, письмом (заказ
BL-STALLED-THREADS-1, решение владельца 13.09). Почта здесь ТРАНСПОРТ, а не второй
отправитель: 01.08 два отправителя с разными дросселями дали 19278 сообщений, и
выключение одного не выключало другой. Один дом — один рубильник.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# Multitenancy (2026-06-30): per-tenant секреты через HEALTH_SECRETS_DIR
# (default ~/.health_secrets). Алёрт тенанта уходит на ЕГО telegram_chat_id;
# без fallback на чужой каталог.
from secrets_paths import owner_secrets_dir, secrets_dir
_SECRETS = secrets_dir()
FALLBACK_TEXT_LIMIT = 1000


def _telegram(msg: str, secrets=None, reply_markup=None) -> bool:
    s = secrets or _SECRETS
    tok = s / "telegram_token"
    cid = s / "telegram_chat_id"
    if not (tok.exists() and cid.exists()):
        return False
    try:
        markup_args = (["--data-urlencode", f"reply_markup={reply_markup.to_json()}"]
                       if reply_markup is not None else [])
        r = subprocess.run(
            ["curl", "-sS", "--fail", "-m", "15", "-X", "POST",
             f"https://api.telegram.org/bot{tok.read_text().strip()}/sendMessage",
             "-d", f"chat_id={cid.read_text().strip()}",
             "--data-urlencode", f"text={msg}"] + markup_args,
            capture_output=True, timeout=25)
        import json
        receipt = json.loads(r.stdout) if r.returncode == 0 else {}
        result = receipt.get("result") if isinstance(receipt, dict) else None
        return (receipt.get("ok") is True and isinstance(result, dict)
                and isinstance(result.get("message_id"), int)
                and not isinstance(result["message_id"], bool) and result["message_id"] > 0)
    except Exception:  # silent-ok: любой сбой = Telegram недоступен → fallback решит notify()
        return False


def _healthcheck_fail(msg: str, secrets=None) -> bool:
    url = (secrets or _SECRETS) / "healthcheck_url"
    if not url.exists():
        return False
    try:
        r = subprocess.run(
            ["curl", "-fsS", "-m", "10", "--data-raw", msg[:FALLBACK_TEXT_LIMIT],
             url.read_text().strip().rstrip("/") + "/fail"],
            capture_output=True, timeout=15)
        return r.returncode == 0
    except Exception:  # silent-ok: резервный канал недоступен → notify() вернёт 'none'
        return False


def _email_settings(secrets=None):
    """(host, port, user, password, to) или None, если канал не настроен.

    §19: значения читаются в рантайме и НЕ покидают машину — ни в лог, ни в
    возврат. Наружу уходит только факт «настроено / не настроено»."""
    s = secrets or owner_secrets_dir()
    need = ("smtp_host", "smtp_user", "smtp_password")
    if not all((s / n).exists() for n in need):
        return None
    host = (s / "smtp_host").read_text().strip()
    user = (s / "smtp_user").read_text().strip()
    pwd = (s / "smtp_password").read_text().strip()
    port_f = s / "smtp_port"
    port = int(port_f.read_text().strip()) if port_f.exists() else 465
    to_f = s / "email_to"
    to = to_f.read_text().strip() if to_f.exists() else user
    if not (host and user and pwd and to):
        return None
    return host, port, user, pwd, to


def email_owner(subject: str, body: str) -> bool:
    """Положить текстовый блок в письмо владельцу. True — ушло.

    ЗАЧЕМ ОТДЕЛЬНЫЙ ВЫХОД, А НЕ ВТОРОЙ ОТПРАВИТЕЛЬ. Заказ BL-STALLED-THREADS-1:
    еженедельный отчёт нужен письмом, потому что к письму возвращаются, а
    сообщение в ленте пролистывают. Но «система пишет владельцу» обязано остаться
    ОДНИМ домом: 01.08 два отправителя с разными дросселями дали 19278 сообщений,
    и выключение одного не выключало другой. Поэтому это транспорт здесь, рядом с
    telegram и healthchecks, а не новый модуль со своим расписанием.

    ГРАНИЦА КАДЕНЦИИ. Письмо — для того, что приходит РЕДКО и читается целиком
    (недельная сводка). Срочное и ежедневное остаётся в telegram: если по письму
    пойдёт ещё и поток, владелец получит один предмет двумя голосами — ровно то,
    от чего нить избавляется.

    БЕЗОПАСНОСТЬ. Тема собирается из текста, который писала модель
    (night_investigator), поэтому CR/LF из неё вырезаются до сборки письма:
    перевод строки в заголовке — инъекция заголовков SMTP (WSTG-INPV-10), а не
    косметика. Тело в заголовки не попадает вовсе. Протокольных команд руками не
    собираем — stdlib (smtplib + EmailMessage) делает это сам, и он же отвергает
    управляющие символы в заголовках второй линией.

    Fail-closed и тихо: нет секретов — канал не настроен, возврат False. Владелец
    узнаёт об этом не отсюда, а от датчика на стороне того, кто письмо заказывал.
    Никогда не бросает: доставка отчёта не имеет права ронять вызывающего.

    ЧТО ЗНАЧИТ True, ЧЕСТНО. Сервер ПРИНЯЛ письмо — это квитанция протокола, а не
    доказательство, что владелец его увидел (ср. delivery_receipt_not_proxy у
    telegram_bot: там message_id, здесь — отсутствие отказа SMTP). Между «принято
    релеем» и «лежит во входящих» есть спам-фильтр, и машинного оракула на этот
    участок у нас нет. Первая доставка проверяется глазами владельца, дальше — его
    молчанием: не пришло две недели подряд — скажет.
    """
    import re
    import smtplib
    from email.message import EmailMessage

    cfg = _email_settings()
    if cfg is None:
        print("notify.email_owner: почтовый канал не настроен "
              "(нет smtp_host/smtp_user/smtp_password в секретах тенанта-владельца). "
              "Как включить: docs/how-to/email_channel.md",
              file=sys.stderr)
        return False
    host, port, user, pwd, to = cfg
    subj = re.sub(r"[\r\n]+", " ", str(subject or "Health OS")).strip()[:200] or "Health OS"
    try:
        m = EmailMessage()
        m["Subject"] = subj
        m["From"] = user
        m["To"] = to
        m.set_content(str(body or ""))
        with smtplib.SMTP_SSL(host, port, timeout=30) as srv:
            srv.login(user, pwd)
            srv.send_message(m)
        return True
    except Exception as e:
        # Не молчим: «отчёт не пришёл» и «отчёт не отправляли» различаются здесь.
        # Текст исключения печатаем, ЗНАЧЕНИЕ пароля в него не попадает (§19:
        # smtplib не кладёт credentials в сообщение об ошибке).
        print(f"notify.email_owner: письмо не ушло: {type(e).__name__}: {e}", file=sys.stderr)
        return False


def notify(msg: str, fallback: bool = True, *, reply_markup=None) -> str:
    """Шлёт msg в Telegram; при провале — резервный healthchecks/fail (email).

    Возврат: 'telegram' | 'fallback' | 'none' — куда фактически ушло.
    Никогда не бросает: доставка алерта не должна ронять вызывающего.
    """
    return _deliver(msg, fallback, None, reply_markup=reply_markup)


def notify_operator(msg: str, fallback: bool = True) -> str:
    """СЛУЖЕБНОЕ сообщение — всегда ОПЕРАТОРУ (владельцу), чей бы процесс ни звал.

    Решение владельца 2026-08-01: служебное — оператору, содержательное — тому, о ком
    оно. `notify()` честно-per-tenant и для содержательного это верно; для служебного
    оно означало «жалоба системы адресована тому, на кого она жалуется». Инцидент:
    вотчер партнёра три дня слал ЕМУ запросы на ревью, роль ревьюера — владельцана.

    Мед-данные тенанта сюда НЕЛЬЗЯ (канал ведёт к владельцу) — см. `owner_secrets_dir`.
    """
    return _deliver(msg, fallback, owner_secrets_dir())


def fault(tech: str, person_key: "str | None" = "common.error.our_side", **kw) -> "str | None":
    """Технический сбой — в журнал, человеку — одна человеческая строка.

    Решение владельца 28.09 (холодное чтение сообщений): «Error code: 529», «проверь
    логи», «disk space на Studio» приходили самому человеку, в том числе партнёру, —
    пугали, а сделать с ними он ничего не мог. Вызывающий шлёт человеку ВОЗВРАТ
    (или ничего, если person_key=None: сбой человека не касается).

    Решение владельца 2026-09-28 (batch 4b): владелец не почтальон. Журнал читает
    integrity, его артефакт — ночной цикл. Мед-данных и секретов в tech не класть.
    """
    import fcntl
    import json
    import logging
    from collections import deque
    from _time_inject import get_now

    try:
        parts = tech.split(":", 2)
        where = parts[0]
        if len(parts) > 1 and ("/" in where or where.endswith((".py", ".sh"))):
            where += ":" + parts[1]
        record = {"ts": get_now().astimezone().isoformat(),
                  "tenant": Path(secrets_dir()).name, "where": where, "text": tech[:500]}
        path = Path(os.environ.get("HEALTH_FAULTS_JOURNAL") or
                    Path(__file__).resolve().parent / "logs" / "faults.jsonl")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+", encoding="utf-8") as journal:
            # Бот и службы могут писать одновременно, в том числе во время обрезки.
            fcntl.flock(journal, fcntl.LOCK_EX)
            journal.write(json.dumps(record, ensure_ascii=False) + "\n")
            journal.flush()
            if os.fstat(journal.fileno()).st_size > 1024 * 1024:
                journal.seek(0)
                tail = deque(journal, maxlen=2000)
                journal.seek(0)
                journal.truncate()
                journal.writelines(tail)
    except Exception as exc:
        logging.getLogger(__name__).warning("Журнал сбоев не записан: %s", exc)
    if person_key is None:
        return None
    import i18n
    return i18n.t(person_key, **kw)


def weekly_path() -> Path:
    """Общий дом журнала: писатели и понедельничный сборщик используют один путь."""
    return Path(os.environ.get("HEALTH_OWNER_WEEKLY") or
                Path(__file__).resolve().parent / "logs" / "owner_weekly.jsonl")


def weekly(line: str) -> None:
    """Одна строка к сведению, БЕЗ медицинских данных. Доставка — owner_weekly.run.

    Журнал не обрезаем: ещё не доставленные события должны пережить отказ канала.
    Ошибка записи остаётся в журнале сбоев, исходный текст туда не копируется.
    """
    import fcntl
    import json
    from _time_inject import get_now

    try:
        text = " ".join(line.split())[:280]
        if not text:
            return
        path = weekly_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+", encoding="utf-8") as journal:
            fcntl.flock(journal, fcntl.LOCK_EX)
            journal.write(json.dumps({"ts": get_now().isoformat(), "text": text},
                                     ensure_ascii=False) + "\n")
            journal.flush()
            os.fsync(journal.fileno())
    except Exception as exc:
        fault(f"notify.weekly: journal write failed ({type(exc).__name__})", person_key=None)


def _deliver(msg: str, fallback: bool, secrets, reply_markup=None) -> str:
    try:
        kw = {"reply_markup": reply_markup} if reply_markup is not None else {}
        if _telegram(msg, secrets, **kw):
            return "telegram"
        if fallback and _healthcheck_fail(msg, secrets):
            return "fallback"
    except Exception as e:
        # Уронить вызывающего доставка права не имеет — но и молчать не имеет.
        # До 2026-08-01 здесь стоял голый `pass`, и он глотал ЛЮБУЮ ошибку, включая
        # неверную сигнатуру вызова: при рефакторинге два теста из пяти зеленели по
        # НЕВЕРНОЙ причине («none» — законный исход и при мёртвой сети, и при дефекте).
        print(f"notify: доставка упала внутри: {e!r}", file=sys.stderr)
    return "none"


if __name__ == "__main__":
    print(notify(" ".join(sys.argv[1:]) or "notify.py test"))
