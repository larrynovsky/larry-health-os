"""mcp_tools — инструменты чтения Health OS для облачных помощников (шаг 1 нити mcp-gateway).

Зачем (решения владельца 06.10): Claude и ChatGPT — окна в одну память, Health OS. Помощник
отвечает о здоровье по данным, а не по догадке. Главная тревога владельца — выдумка поверх
данных. Поэтому каждый ответ несёт источник и дату, а пустота приходит явной строкой
«нет данных», а не пустым списком: пустоту модель заполняет догадкой.

Правила:
- Читаем ТОЛЬКО через существующие функции *_db: второй читатель со своим SQL видел бы
  не то, что дашборд и бот (другие фильтры, другая свежесть).
- Имени человека и его идентификаторов в ответе нет (инвариант llm_exit.identity_not_in_model_context):
  путь бланка не отдаём вовсе, итоговый текст проходит через словарь личных слов (§23).
- Тенант — данные процесса (HEALTH_DATA_DIR), ни один инструмент не принимает тенанта аргументом.
- Аргументы — граница доверия (их пишет модель): имя теста — строка ≤100, дни — целое в пределах,
  дата — строго ГГГГ-ММ-ДД.

Граница: модуль не пишет ничего. Запись (шаг 2) — только через «да» владельца, отдельным кодом.
"""
from __future__ import annotations

import datetime as _dt
import json
import re

# Классы словаря личных слов, которые не уходят наружу: имя и всё, что идентифицирует человека.
# Медицинские классы (clinical, treatment) не вымарываются: их владелец отдаёт сознательно.
REDACT_CLASSES = ["surname", "identity", "birth_date", "chat_id", "partner", "outside_users"]
MAX_DAYS = 7300
MAX_POINTS = 500
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

TOOLS = [
    {"name": "lab_tests",
     "description": ("Список анализов, которые есть в Health OS: имя теста, дата последнего результата "
                     "и число результатов. Начинай с него, чтобы знать точные имена для lab_results."),
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "lab_results",
     "description": ("Все числовые результаты одного анализа за период: дата забора, значение, единица, "
                     "референс бланка. Имя теста — точно как в lab_tests."),
     "inputSchema": {"type": "object", "properties": {
         "test_name": {"type": "string", "description": "имя теста из lab_tests"},
         "days": {"type": "integer", "description": "за сколько дней назад, по умолчанию 1825"}},
         "required": ["test_name"]}},
    {"name": "day_metrics",
     "description": ("Показатели одного дня с приборов (сон, пульс покоя, ВСР, шаги, давление и др.) "
                     "в том виде, как их хранит Health OS, с источником каждого показателя, где он записан."),
     "inputSchema": {"type": "object", "properties": {
         "date": {"type": "string", "description": "ГГГГ-ММ-ДД"}}, "required": ["date"]}},
    {"name": "problems",
     "description": "Список проблем со здоровьем из медкарты Health OS: название, статус, даты, краткое описание.",
     "inputSchema": {"type": "object", "properties": {
         "status": {"type": "string", "enum": ["active", "monitoring", "resolved"],
                    "description": "фильтр по статусу; без него — все"}}}},
    {"name": "facts",
     "description": ("Долгие факты о владельце из памяти Health OS (без разовых событий): "
                     "что известно, с какой даты."),
     "inputSchema": {"type": "object", "properties": {}}},
]
NAMES = {t["name"] for t in TOOLS}


class BadArgs(ValueError):
    """Аргумент от модели не прошёл проверку — ответ модели, не падение сервера."""


_redactor_cache: list = []


def _redactor():
    if not _redactor_cache:
        import pii_census
        _redactor_cache.append(pii_census.pattern(REDACT_CLASSES))
    return _redactor_cache[0]


def redact(text: str) -> str:
    rx = _redactor()
    return rx.sub("[скрыто]", text) if rx else text


def _days(args: dict, default: int) -> int:
    v = args.get("days", default)
    if isinstance(v, bool) or not isinstance(v, int):
        raise BadArgs("days — целое число дней")
    return max(1, min(v, MAX_DAYS))


def _envelope(source: str, body) -> str:
    today = _dt.date.today().isoformat()
    head = f"Источник: Health OS — {source}. Выдано {today}."
    return head + "\n" + (body if isinstance(body, str) else json.dumps(body, ensure_ascii=False, default=str))


def _none(what: str) -> str:
    return f"нет данных: {what}. Не достраивай значения — скажи, что в Health OS этого нет."


def _tool_lab_tests(args: dict) -> str:
    import labs_db
    rows = labs_db.get_lab_history(days=MAX_DAYS)
    if not rows:
        return _none(f"анализов за {MAX_DAYS} дней нет")
    agg: dict = {}
    for r in rows:
        a = agg.setdefault(r["test_name"], {"test_name": r["test_name"], "last_date": r["date"], "results": 0})
        a["results"] += 1
        a["last_date"] = max(a["last_date"], r["date"])
    return _envelope("лабораторные результаты (канон)", sorted(agg.values(), key=lambda a: a["test_name"]))


def _tool_lab_results(args: dict) -> str:
    name = args.get("test_name")
    if not isinstance(name, str) or not name.strip() or len(name) > 100:
        raise BadArgs("test_name — непустая строка до 100 символов")
    name, days = name.strip(), _days(args, 1825)
    cutoff = (_dt.date.today() - _dt.timedelta(days=days)).isoformat()
    import labs_db
    # Основной путь — по ВЕЩЕСТВУ через отображение LOINC (решение владельца 06.10): разные
    # лаборатории пишут один анализ по-разному («LDL calculated» и «ЛПНП»), и ряд по одному
    # написанию показал бы помощнику половину истории — по ней легко сделать ложный вывод о тренде.
    why_not = None
    try:
        tr = labs_db.get_lab_trend_by_component(name, n=MAX_POINTS)
    except RuntimeError:          # справочник веществ недоступен — честно говорим, чем отвечаем
        tr, why_not = None, "справочник веществ недоступен"
    if tr is not None and tr.get("points"):
        pts = [p for p in tr["points"] if p["date"] >= cutoff]
        if not pts:
            return _none(f"по веществу «{name}» за {days} дней результатов нет")
        out = {"by": "вещество (LOINC): разные написания и шкалы сведены",
               "names_merged": sorted({p["test_name"] for p in pts}),
               "points": [{"date": p["date"], "value": p["value"], "unit": p["unit"],
                           "ref_low": p.get("ref_low"), "ref_high": p.get("ref_high"),
                           **({"converted_from": p["raw_unit"]} if p.get("converted") else {})}
                          for p in pts]}
        unres = [u for u in tr.get("unresolved") or [] if u["date"] >= cutoff]
        if unres:
            out["not_merged"] = [{"date": u["date"], "value": u["value"], "unit": u["unit"],
                                  "why": u["reason"]} for u in unres]
        return _envelope(f"анализ «{name}», дата — дата забора", out)
    if tr is not None and why_not is None:
        why_not = {"unmapped": "для этого названия сведения веществ ещё нет",
                   "ambiguous": "название соответствует нескольким веществам",
                   "no_mapping_table": "таблицы сведения веществ нет"}.get(
            (tr.get("issues") or ["unmapped"])[0], "сведение веществ не построено")
    # Запасной путь — по точному названию, с пометкой: помощник должен знать, что ряд может быть неполным.
    rows = [r for r in labs_db.get_lab_series(name, n_days=days)]
    if not rows:
        return _none(f"по анализу «{name}» за {days} дней результатов нет (точные имена — lab_tests)")
    # Путь бланка (source) не отдаём: в нём бывает имя человека и номер документа.
    out = {"by": f"точное название, БЕЗ сведения веществ ({why_not}): ряд может быть неполным — "
                 "тот же анализ под другим названием сюда не попал",
           "points": [{"date": r["date"], "value": r["value"], "unit": r.get("unit"),
                       "ref_low": r.get("ref_low"), "ref_high": r.get("ref_high")} for r in rows]}
    return _envelope(f"анализ «{name}», дата — дата забора", out)


def _tool_day_metrics(args: dict) -> str:
    d = args.get("date")
    if not isinstance(d, str) or not _DATE.match(d):
        raise BadArgs("date — строка ГГГГ-ММ-ДД")
    try:
        _dt.date.fromisoformat(d)
    except ValueError:
        raise BadArgs("такой даты нет")
    import metrics_db
    day = metrics_db.get_day(d)
    if not day:
        return _none(f"показателей приборов за {d} нет")
    return _envelope(f"дневные показатели приборов за {d}", day)


def _tool_problems(args: dict) -> str:
    st = args.get("status")
    if st is not None and st not in ("active", "monitoring", "resolved"):
        raise BadArgs("status — active | monitoring | resolved")
    import problems_db
    rows = problems_db.get_problem_list(st)
    if not rows:
        return _none("проблем в медкарте" + (f" со статусом {st}" if st else "") + " нет")
    keep = ("title", "status", "domain", "onset_date", "first_seen", "last_updated", "resolved_date")
    out = [{**{k: r.get(k) for k in keep}, "summary": r.get("plain_summary") or r.get("description")}
           for r in rows]
    return _envelope("медкарта (список проблем)", out)


def _tool_facts(args: dict) -> str:
    import memory_facts_db
    rows = [r for r in memory_facts_db.get_facts(mem_class="fact")
            if (r.get("temporal_class") or "standing") != "transient"]
    if not rows:
        return _none("долгих фактов в памяти нет")
    out = [{"key": r.get("key"), "value": r.get("value"), "since": r.get("valid_from")} for r in rows]
    return _envelope("память о владельце (долгие факты, разовые события исключены)", out)


_DISPATCH = {"lab_tests": _tool_lab_tests, "lab_results": _tool_lab_results, "day_metrics": _tool_day_metrics,
             "problems": _tool_problems, "facts": _tool_facts}


def call(name: str, args: dict | None) -> tuple[str, bool]:
    """→ (текст, is_error). Ошибка аргумента — ответ модели; любая другая — короткий отказ без трассы."""
    if name not in _DISPATCH:
        return "нет такого инструмента", True
    try:
        return redact(_DISPATCH[name](args or {})), False
    except BadArgs as e:
        return f"неверный аргумент: {e}", True
    except Exception as e:      # silent-ok: отказ уходит модели строкой, причина — в журнал сервера
        import sys
        sys.stderr.write(f"mcp_tools {name}: {type(e).__name__}\n")
        return "Health OS не смог прочитать данные — внутренняя ошибка, данных в ответе нет", True
