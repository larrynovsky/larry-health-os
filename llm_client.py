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
# INTENT: llm_exit — один гард, один переводчик, смена модели только на проверенную: subsystem_intent.yaml
from __future__ import annotations

from collections.abc import Mapping
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


class ModelNotServed(RuntimeError):
    """Провайдер не отдаёт модель под этим именем: ответила другая модель или имя отвергнуто.
    DeepSeek молча подменяет имя (замер 02.10: «claude-opus-5» → deepseek-v4-pro,
    «deepseek-chat» → deepseek-v4-flash), а незнакомое отвергает кодом 400, не 404:
    без этой проверки допуск записал бы вердикт чужой модели, а датчик отзыва не увидел бы ухода."""


class NoTextInAnswer(RuntimeError):
    """В ответе модели нет ни одного текстового блока (например, рассуждение съело лимит)."""


def answer_text(resp) -> str:
    """Текст ответа модели — ЕДИНСТВЕННЫЙ читатель для рабочего кода и допуска моделей.

    До 03.10 рабочий код брал `content[0].text`, а допуск склеивал текстовые блоки: допуск
    пропустил claude-sonnet-5-5, у которой первым идёт блок рассуждения, и утренний отчёт,
    ночной разбор и консолидация памяти упали в первое же утро. Один способ чтения на всех —
    чтобы допуск судил то, что потом читает работа (урок C-139, тот же класс).
    Текста нет — громко, с причиной остановки: тихая пустая строка ушла бы человеку."""
    def _is_text(b) -> bool:
        kind = getattr(b, "type", None)
        if isinstance(kind, str):           # блок SDK: thinking/tool_use/text — решает тип
            return kind == "text"
        return isinstance(getattr(b, "text", None), str)   # подставной блок без типа (тесты)
    parts = [b.text for b in (getattr(resp, "content", None) or []) if _is_text(b)]
    if not parts:
        raise NoTextInAnswer(f"в ответе нет текста: stop_reason={getattr(resp, 'stop_reason', None)!r}, "
                             f"блоки={[getattr(b, 'type', '?') for b in getattr(resp, 'content', None) or []]}")
    return "".join(parts)


def is_model_not_found(exc: BaseException) -> bool:
    """«Модели нет у провайдера» у любого из SDK — для датчика отзыва моделей."""
    if isinstance(exc, ModelNotServed):
        return True
    code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    return code == 404 or type(exc).__name__ == "NotFoundError"


def _strings(x, out: list, *, media_source=False) -> None:
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
    elif isinstance(x, Mapping):
        # httpx.Headers/QueryParams допустимы у SDK; multi_items сохраняет
        # все значения повторяющихся параметров, которые items() схлопывает.
        for k, v in x.multi_items() if hasattr(x, "multi_items") else x.items():
            _strings(k, out)   # extra_body/headers могут нести секрет и в имени поля
            if k == "data" and media_source and x.get("type") == "base64":
                continue
            _strings(v, out, media_source=k == "source")
    elif isinstance(x, (list, tuple)):
        for v in x:
            _strings(v, out)
    elif hasattr(x, "model_dump"):
        _strings(x.model_dump(), out, media_source=media_source)


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


def _guard_kwargs(kw) -> None:
    # Расширения SDK (extra_body/headers/query, metadata, …) тоже выходят наружу.
    # Исключение медиа остаётся в общем _strings, второй сканер не нужен.
    guard_outgoing({k: v for k, v in kw.items() if k not in ("model", "max_tokens")})


# ── Думание по задачам (нить thinking-modes, 04.10) ─────────────────────────────
# Лимит max_tokens в вызове значит ДЛИНУ ОТВЕТА. Думает ли задача — данные
# (methodology/llm_task_modes.json), как модель включает/выключает думание — данные
# (llm_providers.json, anthropic.thinking), запас под думание — замер допуска
# (llm_admission_table.json, reasoning_reserve_tokens). Без замеренного запаса задача
# не думает: 03–04.10 рассуждение съедало весь лимит ответа (бриф, недельный отчёт).
# Поля и состояние моделей — docs/reference/llm_thinking.md.
_TASK_MODES_FILE = Path(__file__).parent / "methodology" / "llm_task_modes.json"


def task_mode(task: "str | None") -> str:
    """think | read | disputed для ключа задачи; неизвестный ключ — read (как до 04.10)."""
    import json
    tasks = json.loads(_TASK_MODES_FILE.read_text(encoding="utf-8")).get("tasks", {})
    return (tasks.get(task) or {}).get("mode", "read")


def thinking_profile(model: "str | None") -> dict:
    """Запись модели из anthropic.thinking.models: точное имя или самый длинный префикс
    (датированный снимок claude-haiku-4-5-20251001 — та же модель). Нет записи — {}."""
    models = ((profiles().get("anthropic") or {}).get("thinking") or {}).get("models") or {}
    m = str(model or "")
    best = max((k for k in models if m == k or m.startswith(k + "-")), key=len, default=None)
    return models.get(best, {}) if best else {}


def reasoning_reserve(model: "str | None") -> int:
    """Запас под думание, замеренный допуском (любая роль, где у модели есть запись). 0 — не замерен."""
    import hai_core   # поздний импорт: hai_core импортирует этот модуль
    vals = [int(v.get("reasoning_reserve_tokens") or 0)
            for role in (hai_core._admission_table().get("anthropic") or {}).values()
            for m, v in role.items() if m == model]
    return max(vals, default=0)


def _fill(param, reserve: int):
    if isinstance(param, dict):
        return {k: _fill(v, reserve) for k, v in param.items()}
    return reserve if param == "{reserve}" else param


def _thinking_plan(kw: dict, task) -> "tuple[dict, dict | None]":
    """→ (kwargs первого вызова, kwargs повтора при голодном ответе или None)."""
    if "thinking" in kw:          # вызывающий решил сам — не трогаем
        return kw, None
    prof = thinking_profile(kw.get("model"))
    reserve = reasoning_reserve(kw.get("model"))
    if task_mode(task) == "think" and prof.get("think") and reserve > 0:
        def think(r):
            # С думанием API не принимает temperature ≠ 1: убираем, а не падаем.
            base = {k: v for k, v in kw.items() if k != "temperature"}
            return {**base, "thinking": _fill(prof["think"], r),
                    "max_tokens": int(kw["max_tokens"]) + r}
        return think(reserve), think(2 * reserve)
    if prof.get("no_think"):
        return {**kw, "thinking": prof["no_think"]}, None
    return kw, None


def _starved(resp) -> bool:
    """Рассуждение съело весь лимит: остановка по лимиту и ни одного текстового блока."""
    return getattr(resp, "stop_reason", None) == "max_tokens" and not any(
        getattr(b, "type", "") == "text" for b in (getattr(resp, "content", None) or []))


def _is_async(create) -> bool:
    """Асинхронен ли вызов. У SDK `AsyncMessages.create` обёрнут СИНХРОННЫМ декоратором
    (required_args), и iscoroutinefunction на нём даёт False — смотреть надо сквозь обёртку
    (замер 04.10: консилиум упал бы на `with` вместо `async with`)."""
    import inspect
    return inspect.iscoroutinefunction(inspect.unwrap(create))


def _streamed(create):
    """Думающий вызов идёт ПОТОКОМ (замер 04.10): с запасом под думание лимит велик, и SDK
    отказывает обычному вызову («Streaming is required for operations that may take longer
    than 10 minutes»). Поток возвращает то же сообщение целиком — читатели не меняются."""
    import inspect
    inner = getattr(create, "__self__", None)
    if inner is None or not hasattr(inner, "stream"):
        return create
    if _is_async(create):
        async def _a(**k):
            async with inner.stream(**k) as s:
                return await s.get_final_message()
        return _a

    def _s(**k):
        with inner.stream(**k) as s:
            return s.get_final_message()
    return _s


def _log_thinking(create, task, kw, resp, attempts):
    """Сколько ушло на думание — в общий журнал расходов (api_spend_log), чтобы запас под
    думание пересчитывался по рабочим вызовам, а не по разовому замеру. Сбой учёта не роняет
    вызов; текст ответа в журнал не пишется — только числа."""
    try:
        import inspect
        import api_spend_log
        text = "".join(getattr(b, "text", "") for b in (resp.content or []) if getattr(b, "type", "") == "text")
        inner = getattr(create, "__self__", None)
        text_tok = 0
        if text and inner is not None and not _is_async(create):
            _guard_kwargs({"messages": [{"role": "user", "content": text}]})   # исходящий текст — через гард
            text_tok = inner.count_tokens(model=kw["model"],
                                          messages=[{"role": "user", "content": text}]).input_tokens
        out = int(resp.usage.output_tokens)
        api_spend_log.log_call(agent=str(task), model=getattr(resp, "model", kw["model"]),
                               tokens_in=resp.usage.input_tokens, tokens_out=out,
                               extra={"mode": "think", "answer_limit": int(kw["max_tokens"]),
                                      "text_tokens": text_tok or None,
                                      "think_tokens": (out - text_tok) if text_tok else None,
                                      "stop": resp.stop_reason, "attempts": attempts})
    except Exception as e:  # noqa: BLE001 — учёт не роняет вызов, но и не молчит
        import logging
        logging.getLogger(__name__).warning("учёт думания не записан: %r", e)


def _create_with_mode(create, kw: dict, task):
    first, retry = _thinking_plan(kw, task)
    if retry is None:
        return create(**first)
    send = _streamed(create)
    resp = send(**first)
    import inspect
    if inspect.isawaitable(resp):
        async def _async():
            r = await resp
            n = 1
            if _starved(r):
                r, n = await send(**retry), 2
            _log_thinking(create, task, first, r, n)
            return r
        return _async()
    n = 1
    if _starved(resp):
        resp, n = send(**retry), 2
    _log_thinking(create, task, first, resp, n)
    return resp


class _GuardedMessages:
    def __init__(self, inner, prov: str = "anthropic"):
        self._inner = inner
        self._prov = prov

    def create(self, **kw):
        task = kw.pop("task", None)
        _guard_kwargs(kw)
        if self._prov == "anthropic":
            return _create_with_mode(self._inner.create, kw, task)
        return self._inner.create(**kw)

    def stream(self, **kw):
        # До 01.10 stream уходил через __getattr__ без гарда; вызывающих нет,
        # но «нет вызывающих» — не защита: первый же новый вызов прошёл бы мимо.
        task = kw.pop("task", None)
        _guard_kwargs(kw)
        if self._prov == "anthropic":   # без повтора: поток не перезапускают
            kw = _thinking_plan(kw, task)[0]
        return self._inner.stream(**kw)

    def count_tokens(self, **kw):
        kw.pop("task", None)
        _guard_kwargs(kw)
        return self._inner.count_tokens(**kw)

    def __getattr__(self, name):
        raise AttributeError(f"messages.{name}: нет защищённого выхода через гард; "
                             "разрешены create, stream, count_tokens")


class _GuardedModels:
    """Сохраняет родной Page/AsyncPage; расширения запросов проходят тот же гард."""
    def __init__(self, inner):
        self._inner = inner

    def list(self, *args, **kw):
        guard_outgoing(args, kw)
        return self._inner.list(*args, **kw)

    def retrieve(self, *args, **kw):
        guard_outgoing(args, kw)
        return self._inner.retrieve(*args, **kw)

    def __getattr__(self, name):
        raise AttributeError(f"models.{name}: нет защищённого выхода через гард; "
                             "разрешены list, retrieve")


class _GuardedClient:
    def __init__(self, inner, prov: "str | None" = None):
        self._inner = inner
        # Чужой и совместимый клиенты знают своего поставщика; родной SDK — это Anthropic.
        self._prov = prov or getattr(inner, "_prov", "anthropic")
        self.messages = _GuardedMessages(inner.messages, self._prov)

    @property
    def models(self):
        return _GuardedModels(self._inner.models)

    def with_options(self, **kw):
        # Ключи аутентификации — назначение клиента; пользовательские заголовки
        # и query — ещё один путь исходящего текста, проверяем до клонирования.
        _guard_kwargs({k: v for k, v in kw.items() if k not in ("api_key", "auth_token")})
        return _GuardedClient(self._inner.with_options(**kw), self._prov)

    def close(self):
        return self._inner.close()

    def __getattr__(self, name):
        raise AttributeError(f"client.{name}: нет защищённого выхода через гард; "
                             "разрешены messages, models, with_options, close")


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
        self._prov, self._async, self._listing = prov, async_, listing
        self.messages = _ForeignMessages(prov, sdk, async_)
        self.models = _ForeignModels(prov, listing)

    def close(self):
        if self._async:
            async def _a():
                if self._prov == "openai":
                    await self._sdk.close()
                    self._listing.close()
                else:
                    await self._sdk.aio.aclose()
                    self._sdk.close()
            return _a()
        return self._sdk.close()

    def with_options(self, **kw):
        raise AttributeError(f"with_options: для {self._prov} нет защищённого перевода опций через гард")


def _raise_if_not_served(model, e: Exception) -> None:
    """400 «supported API model names are …» — имя отвергнуто, это «модели нет», а не сбой запроса."""
    if getattr(e, "status_code", None) == 400 and "model name" in str(e):
        raise ModelNotServed(f"{model!r}: {str(e)[:160]}") from e


def _same_model(model, r):
    got = getattr(r, "model", None)
    if got is not None and got != model:
        raise ModelNotServed(f"{model!r}: ответила {got!r}")
    return r


class _CompatMessages:
    """Точка провайдера, совместимая с Anthropic Messages (DeepSeek, замер 02.10): перевод не
    нужен. thinking — из профиля, если вызывающий не задал свой: без него DeepSeek открывает
    ответ блоком рассуждения, и тот съедает max_tokens (замер 02.10)."""
    def __init__(self, inner, prof):
        self._inner, self._prof = inner, prof

    def _kw(self, kw):
        if "thinking" in self._prof and "thinking" not in kw:
            return {**kw, "thinking": self._prof["thinking"]}
        return kw

    def create(self, **kw):
        kw = self._kw(kw)
        model = kw.get("model")
        try:
            r = self._inner.create(**kw)
        except Exception as e:
            _raise_if_not_served(model, e)
            raise
        if kw.get("stream"):
            return r
        if hasattr(r, "__await__"):
            async def _a():
                try:
                    res = await r
                except Exception as e:
                    _raise_if_not_served(model, e)
                    raise
                return _same_model(model, res)
            return _a()
        return _same_model(model, r)

    def stream(self, **kw):
        return self._inner.stream(**self._kw(kw))

    def count_tokens(self, **kw):
        return self._inner.count_tokens(**kw)

    def __getattr__(self, name):
        raise AttributeError(f"messages.{name}: нет защищённого выхода через гард; "
                             "разрешены create, stream, count_tokens")


class _CompatModels:
    """Список моделей — по models_url профиля: у совместимой точки /v1/models нет (404, замер 02.10)."""
    def __init__(self, prov, async_=False):
        self._prov, self._async = prov, async_

    def _page(self, r, limit):
        from datetime import datetime, timezone
        from types import SimpleNamespace as NS
        r.raise_for_status()
        out = [NS(id=m["id"], created_at=datetime.fromtimestamp(m.get("created") or 0, timezone.utc).isoformat())
               for m in r.json().get("data", [])]
        return out[:limit] if limit else out

    def list(self, limit=100):
        import httpx
        url = profiles()[self._prov]["models_url"]
        headers = {"Authorization": f"Bearer {api_key(self._prov)}"}
        if self._async:
            async def _a():
                async with httpx.AsyncClient() as client:
                    return self._page(await client.get(url, timeout=30, headers=headers), limit)
            return _a()
        return self._page(httpx.get(url, timeout=30, headers=headers), limit)


class _CompatClient:
    def __init__(self, prov, async_, sdk=None):
        import anthropic
        prof = profiles()[prov]
        ctor = anthropic.AsyncAnthropic if async_ else anthropic.Anthropic
        self._prov, self._async = prov, async_
        self._sdk = sdk if sdk is not None else ctor(api_key=api_key(prov), base_url=prof["base_url"])
        self.messages = _CompatMessages(self._sdk.messages, prof)
        self.models = _CompatModels(prov, async_)

    def with_options(self, **kw):
        return _CompatClient(self._prov, self._async, self._sdk.with_options(**kw))

    def close(self):
        return self._sdk.close()


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
    if profiles()[prov].get("transport") == "anthropic_compatible":
        return _GuardedClient(_CompatClient(prov, async_))
    if prov != "anthropic":
        # Тот же гард по ВСЕМУ исходящему тексту стоит до перевода и транспорта.
        return _GuardedClient(_ForeignClient(prov, async_))
    import anthropic
    ctor = anthropic.AsyncAnthropic if async_ else anthropic.Anthropic
    return _GuardedClient(ctor(api_key=api_key()))
