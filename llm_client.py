#!/usr/bin/env python3.11
"""llm_client.py — единственный дом выхода во внешний LLM-API.

ЗАЧЕМ. Замер 2026-08-03: клиент Anthropic конструируется 27 раз в 21 модуле,
каждый сам читает ключ по захардкоженному пути, а `secret_guard` вызывается
ВРУЧНУЮ в трёх местах одного `doc_agent`. То есть защита держится не на
конструкции, а на том, что автор не забыл. Двадцать раз не забыть не бывает —
это ровно та форма, которой в своде посвящён §17: общее знание, живущее копиями
у равных писателей, расходится (константа BLOOD разошлась между двумя
писателями канона, словарь модулей — с деревом, путь бэкапа — с реальностью).

ЧТО ДЕЛАЕТ: отдаёт клиента, у которого `messages.create`/`stream` перед
отправкой прогоняют ВЕСЬ исходящий текст (system, любые блоки сообщений, tools)
через `secret_guard`. Не сканируются только base64-данные медиа — решение
названо в `_strings`, в одном месте, а не подразумевается в двадцати.

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


# Профили провайдеров (ключ, хосты, запас вывода на рассуждение, судьба temperature,
# модели ролей по умолчанию) — ДАННЫЕ: methodology/llm_providers.json.
_PROFILES_FILE = Path(__file__).parent / "methodology" / "llm_providers.json"


def profiles() -> dict:
    import json
    return {k: v for k, v in json.loads(_PROFILES_FILE.read_text(encoding="utf-8")).items()
            if not k.startswith("_")}


def provider() -> str:
    """Провайдер УСТАНОВКИ (решение человека при установке, переменная HEALTH_LLM_PROVIDER
    из .env; нет — anthropic, как до 2026-10-02). Неизвестный — громкий отказ."""
    import os
    p = (os.environ.get("HEALTH_LLM_PROVIDER") or "anthropic").strip().lower()
    if p not in profiles():
        raise ValueError(f"HEALTH_LLM_PROVIDER={p!r}: такого провайдера нет в methodology/llm_providers.json")
    return p


def api_key(prov: str | None = None) -> str:
    """Ключ провайдера. Дом всех ключей — тот же каталог, что у anthropic_key;
    имя файла — из профиля провайдера (openai_key, gemini_key, …)."""
    prov = prov or provider()
    if prov == "anthropic":
        return _KEY_FILE.read_text().strip()
    return (_KEY_FILE.parent / profiles()[prov]["key_file"]).read_text().strip()


def is_model_not_found(exc: BaseException) -> bool:
    """«Модели нет у провайдера» у любого из SDK — для датчика отзыва моделей."""
    code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    return code == 404 or type(exc).__name__ == "NotFoundError"


def _strings(x, out: list) -> None:
    """Все строки исходящей структуры, кроме base64-данных медиа.

    До 2026-10-01 скан брал только system и блоки type=text: содержимое
    tool_result, аргументы tool_use и описания tools уходили провайдеру мимо
    гарда (проба Кодекса 01.10: секрет в tool_result → SENT, 1 вызов транспорта).
    Белый список типов блоков отстаёт от SDK по построению — поэтому обход
    ВСЕГО, а исключение одно и названо: `source.data` при `source.type=base64`
    (картинка/документ — секрет в пикселях текстовым поиском не ищется, а
    сотни килобайт base64 дали бы иллюзию покрытия). Блоки SDK (pydantic)
    приводятся к dict через model_dump — их передают обратно в историю
    (hai_chat, cbcr_hypothesis)."""
    if isinstance(x, str):
        out.append(x)
    elif isinstance(x, dict):
        for k, v in x.items():
            if k == "data" and x.get("type") == "base64":
                continue
            _strings(v, out)
    elif isinstance(x, (list, tuple)):
        for v in x:
            _strings(v, out)
    elif hasattr(x, "model_dump"):
        _strings(x.model_dump(), out)


def _texts(messages, system=None, tools=None) -> str:
    """Весь исходящий текст запроса: system, сообщения (любые блоки), tools."""
    parts: list = []
    _strings(system, parts)
    _strings(messages, parts)
    _strings(tools, parts)
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


def guard_outgoing(messages, system=None, tools=None) -> None:
    """Поднимает GuardUnavailable при слепоте и SecretLeakBlocked при находке."""
    hits = secret_guard.find_secret_values(_texts(messages, system, tools))
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
        guard_outgoing(kw.get("messages"), kw.get("system"), kw.get("tools"))
        return self._inner.create(**kw)

    def stream(self, **kw):
        # До 01.10 stream уходил через __getattr__ без гарда; вызывающих нет,
        # но «нет вызывающих» — не защита: первый же новый вызов прошёл бы мимо.
        guard_outgoing(kw.get("messages"), kw.get("system"), kw.get("tools"))
        return self._inner.stream(**kw)

    def __getattr__(self, name):        # count_tokens/batches — без генерации текста ответа
        return getattr(self._inner, name)


class _GuardedClient:
    def __init__(self, inner):
        self._inner = inner
        self.messages = _GuardedMessages(inner.messages)

    def __getattr__(self, name):
        return getattr(self._inner, name)


class _ForeignModels:
    """models.list() в форме, которую читают датчик отзыва и допуск: .id и .created_at."""
    def __init__(self, prov, sdk):
        self._prov, self._sdk = prov, sdk

    def list(self, limit=100):
        from datetime import datetime, timezone
        from types import SimpleNamespace as NS
        out = []
        if self._prov == "openai":
            for m in self._sdk.models.list():
                ts = datetime.fromtimestamp(getattr(m, "created", 0) or 0, timezone.utc).isoformat()
                out.append(NS(id=m.id, created_at=ts))
        else:
            for m in self._sdk.models.list():
                out.append(NS(id=m.name.removeprefix("models/"), created_at=""))
        return out[:limit] if limit else out


class _ForeignMessages:
    """messages.create в формате Anthropic → родная библиотека провайдера → ответ в форме,
    которую читают вызывающие (llm_translate). Решение владельца 2026-10-01 «вариант а»."""
    def __init__(self, prov, sdk, async_):
        self._prov, self._sdk, self._async = prov, sdk, async_

    def create(self, **kw):
        import llm_translate as T
        prof = profiles()[self._prov]
        if self._prov == "openai":
            req = T.to_openai(kw, prof)
            if self._async:
                async def _a():
                    return T.from_openai(await self._sdk.responses.create(**req))
                return _a()
            return T.from_openai(self._sdk.responses.create(**req))
        req = T.to_gemini(kw, prof)
        models = self._sdk.aio.models if self._async else self._sdk.models
        if self._async:
            async def _g():
                return T.from_gemini(await models.generate_content(
                    model=req["model"], contents=req["contents"], config=req["config"]))
            return _g()
        return T.from_gemini(models.generate_content(
            model=req["model"], contents=req["contents"], config=req["config"]))

    def stream(self, **kw):
        raise NotImplementedError(f"stream не переведён для {self._prov} — вызывающих нет (замер 01.10)")


class _ForeignClient:
    def __init__(self, prov, async_):
        if prov == "openai":
            import openai
            sdk = (openai.AsyncOpenAI if async_ else openai.OpenAI)(api_key=api_key(prov))
            listing = openai.OpenAI(api_key=api_key(prov)) if async_ else sdk
        elif prov == "gemini":
            from google import genai
            sdk = listing = genai.Client(api_key=api_key(prov))
        else:
            raise ValueError(f"провайдер {prov!r} не подключён к переводчику")
        self._sdk = sdk          # держим ссылку: клиент genai закрывается при сборке мусора
        self.messages = _ForeignMessages(prov, sdk, async_)
        self.models = _ForeignModels(prov, listing)


def guarded_client(async_=False, prov: str | None = None):
    """Клиент с гардом на исходящем тексте.

    Имя не `get`: оно занято `parked_decisions.get`, и дубль-гейт справедливо
    заблокировал коммит. Переименование честнее allowlist — тот же выбор, что
    `resolve` → `resolve_outcome` 2026-08-02.

    async_=True обязателен там, где вызовы идут из event loop: sync-клиент под
    run_in_executor даёт [Errno 11] EDEADLK на macOS (ложный путь C-30).

    prov — явный провайдер вместо провайдера установки: только допуск моделей
    (llm_admission --provider) прогоняет чужую модель с установки anthropic.
    """
    prov = prov or provider()
    if prov not in profiles():
        raise ValueError(f"провайдер {prov!r}: нет в methodology/llm_providers.json")
    if prov != "anthropic":
        # Тот же гард по ВСЕМУ исходящему тексту стоит до перевода и транспорта.
        return _GuardedClient(_ForeignClient(prov, async_))
    import anthropic
    ctor = anthropic.AsyncAnthropic if async_ else anthropic.Anthropic
    return _GuardedClient(ctor(api_key=api_key()))
