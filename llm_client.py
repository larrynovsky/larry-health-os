#!/usr/bin/env python3.11
"""llm_client.py — единственный дом выхода во внешний LLM-API.

ЗАЧЕМ. Замер 2026-08-03: клиент Anthropic конструируется 27 раз в 21 модуле,
каждый сам читает ключ по захардкоженному пути, а `secret_guard` вызывается
ВРУЧНУЮ в трёх местах одного `doc_agent`. То есть защита держится не на
конструкции, а на том, что автор не забыл. Двадцать раз не забыть не бывает —
это ровно та форма, которой в своде посвящён §17: общее знание, живущее копиями
у равных писателей, расходится (константа BLOOD разошлась между двумя
писателями канона, словарь модулей — с деревом, путь бэкапа — с реальностью).

ЧТО ДЕЛАЕТ: отдаёт клиента, у которого `messages.create` перед отправкой
прогоняет ТЕКСТОВЫЕ блоки через `secret_guard`. Изображения не сканируются —
решение названо здесь, в одном месте, а не подразумевается в двадцати.

ЧЕГО НЕ ДЕЛАЕТ (домен узкий намеренно, D1): не выбирает модель, не задаёт
max_tokens, не ретраит, не логирует промпты. Всё это остаётся у вызывающих.
Иначе правка политики ретраев потребовала бы трогать код гарда — ложное
разделение (Таненбаум §7.2.1): независимые оси в одном доме синхронизируются
без нужды, и со временем никто не рискует править ни одну.

ГРАНИЦА ПОКРЫТИЯ, названная вслух. Ключ лежит по владельческому пути и НЕ
тенантный, а `secret_guard` сканирует каталог секретов ТЕКУЩЕГО тенанта.
Значит в процессе партнёра игольница не содержит ключа владельца, и утечка
этого ключа через партнёрский тракт гардом не будет обнаружена. Решение
владельца 2026-08-03: оставить так — расширять скан на чужой каталог значит
ослабить мультитенантную изоляцию (SEC-31), а она дороже. Слепота записана,
а не забыта; в терминах Таненбаума conit гарда уже conit клиента.
"""
from __future__ import annotations

from pathlib import Path

import secret_guard

# ЕДИНСТВЕННОЕ место, где читается ключ. Ключ общий для всех тенантов: это ключ
# проекта, не пациента, поэтому дом — home владельца, а не secrets_dir().
#
# УТВЕРЖДЕНИЕ БЫЛО ЛОЖНЫМ 2026-08-03 … 2026-09-02. Дом завели, а копии не мигрировали:
# замер 02.09 нашёл 19 модулей, читавших путь сами, и в одном из них (lab_extractor)
# путь вёл в НЕСУЩЕСТВУЮЩИЙ ~/health_scripts/.anthropic_key — LLM-тракт был
# сломан, и это не всплывало, потому что тесты мокают клиента. Мигрированы все 19;
# счёт держит tests/consistency/test_llm_key_single_home.py, а не эта строка.
_KEY_FILE = Path.home() / ".health_secrets" / "anthropic_key"


class SecretLeakBlocked(RuntimeError):
    """Отправка заблокирована: гард нашёл значение секрета в исходящем тексте."""


class GuardUnavailable(RuntimeError):
    """Гард НЕ СМОГ отработать — это не «чисто», это слепота.

    Отдельный тип, а не то же исключение, что находка (решение 2026-08-03).
    Причина: `secrets_dir()` бросает RuntimeError в процессе тенанта без
    HEALTH_SECRETS_DIR, и старый гард возвращал это тем же значением, что и
    настоящую находку. При одном потребителе разница косметическая; при
    двадцати «всё заблокировано, потому что переменная не задана» неотличимо
    от «всё заблокировано, потому что течёт секрет» — а лечится противоположно.
    """


def api_key() -> str:
    return _KEY_FILE.read_text().strip()


def _texts(messages, system=None) -> str:
    """Только текстовые части запроса. base64 картинок не сканируется:
    секрет в пикселях текстовым поиском не ищется, а тащить сотни килобайт
    в скан значит платить за иллюзию покрытия."""
    parts = []
    if isinstance(system, str):
        parts.append(system)
    elif isinstance(system, list):
        parts += [b.get("text", "") for b in system if isinstance(b, dict)]
    for m in messages or []:
        c = m.get("content") if isinstance(m, dict) else None
        if isinstance(c, str):
            parts.append(c)
        elif isinstance(c, list):
            parts += [b.get("text", "") for b in c
                      if isinstance(b, dict) and b.get("type") == "text"]
    return "\n".join(p for p in parts if p)


def _block_log_path() -> Path:
    """Путь security-журнала блокировок. Резолвится ПРИ ВЫЗОВЕ, env
    HEALTH_LLM_GUARD_LOG переопределяет (тесты).

    Модульная константа здесь уже отравила журнал: тесты `test_llm_client`
    зовут guard_outgoing с мокнутыми находками, и каждый прогон набора писал
    тройку anthropic_key/RuntimeError/oura_token в БОЕВОЙ журнал — 42 дня
    подряд датчик `check_llm_guard_blocks_reported` кричал «гард блокировал
    3 раза сегодня» про артефакт теста (замер 2026-08-31: ВСЕ 168 строк
    журнала с 04.08 — эта тройка, настоящих блокировок ноль). Тот же класс,
    что отравление квитанции триажа тестом (§20, triage_agent 2026-08-07).
    """
    import os
    p = os.environ.get("HEALTH_LLM_GUARD_LOG")
    return Path(p) if p else Path(__file__).parent / "logs" / "llm_guard_blocks.log"


def _journal(kind: str, detail: str) -> None:
    """След блокировки, чтобы она не была молчаливой.

    Без журнала блок уходит в лог вызывающего, и ни ложное срабатывание, ни
    настоящая находка не видны человеку — а ночной датчик
    `check_llm_guard_blocks_reported` был бы вечно-зелёным по построению (§20).
    Пишем ИМЕНА файлов и тип события; значения не пишем никогда (SEC-21).
    """
    from datetime import datetime
    try:
        _log = _block_log_path()
        _log.parent.mkdir(parents=True, exist_ok=True)
        with _log.open("a", encoding="utf-8") as fh:
            # Штамп РЕАЛЬНОГО момента блокировки. Через seam было бы хуже:
            # тестовые часы сдвигали бы записи security-журнала, который читает
            # человек при разборе инцидента. Маркер обязан стоять на СТРОКЕ вызова —
            # в комментарии выше страж его не видит (проверено прогоном на Studio).
            _ts = datetime.now().isoformat(timespec="seconds")  # time-inject: ok
            fh.write(f"{_ts}\t{kind}\t{detail}\n")
    except OSError as e:
        # Не молчим и не роняем отправку из-за журнала: блок всё равно произойдёт,
        # но человек должен знать, что след не записан.
        print(f"llm_client: журнал блокировок недоступен ({type(e).__name__})", flush=True)


# Читатель этих исключений приходит по СОБЫТИЮ, а не листингом docs/: адрес
# инструкции обязан ехать в самом сообщении.
_HOWTO = " → что делать: docs/how-to/llm_guard_blocked.md"


def guard_outgoing(messages, system=None) -> None:
    """Поднимает GuardUnavailable при слепоте и SecretLeakBlocked при находке."""
    hits = secret_guard.find_secret_values(_texts(messages, system))
    blind = [h for h in hits if h.startswith("!")]
    if blind:
        _journal("GUARD_BLIND", "; ".join(blind))
        raise GuardUnavailable("; ".join(blind) + _HOWTO)
    if hits:
        # Имена файлов, не значения — сам гард этого и держится (SEC-21).
        _journal("SECRET_FOUND", ", ".join(hits))
        raise SecretLeakBlocked("значения секретов в исходящем тексте: "
                                + ", ".join(hits) + _HOWTO)


class _GuardedMessages:
    def __init__(self, inner):
        self._inner = inner

    def create(self, **kw):
        guard_outgoing(kw.get("messages"), kw.get("system"))
        return self._inner.create(**kw)

    def __getattr__(self, name):        # stream/count_tokens и прочее — как есть
        return getattr(self._inner, name)


class _GuardedClient:
    def __init__(self, inner):
        self._inner = inner
        self.messages = _GuardedMessages(inner.messages)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def guarded_client(async_=False):
    """Клиент с гардом на исходящем тексте.

    Имя не `get`: оно занято `parked_decisions.get`, и дубль-гейт справедливо
    заблокировал коммит. Переименование честнее allowlist — тот же выбор, что
    `resolve` → `resolve_outcome` 2026-08-02.

    async_=True обязателен там, где вызовы идут из event loop: sync-клиент под
    run_in_executor даёт [Errno 11] EDEADLK на macOS (ложный путь C-30).
    """
    import anthropic
    ctor = anthropic.AsyncAnthropic if async_ else anthropic.Anthropic
    return _GuardedClient(ctor(api_key=api_key()))
