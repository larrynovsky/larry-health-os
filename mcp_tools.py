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
    {"name": "health_brief",
     "description": ("НАЧИНАЙ С НЕГО. Тот же бриф о человеке, что видит бот Health OS перед каждым ответом: "
                     "текущая вера (где он, что отменено), возраст, диагноз и его статус, лечение с датами, "
                     "АКТИВНЫЕ проблемы, свежие анализы с флагами, личная норма ВСР. Закрытые проблемы сюда "
                     "не входят — они в problems(status=resolved) и ограничением сегодня не являются."),
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "nutrition_frame",
     "description": ("Рамка питания, которую Health OS вывел КОДОМ (не моделью) из медкарты, генома и ИМТ по "
                     "курируемым правилам: энергия (gain — набрать/удержать вес важнее ограничений порций), "
                     "белок, микроэлементы, ограничения (reflux_aware, dumping_aware, low_GI, lactose_free…), "
                     "у каждого пункта — причина. Плюс правила, выведенные консилиумом системы и ещё НЕ "
                     "одобренные владельцем, — с этой пометкой. Для любого совета о еде бери ограничения "
                     "отсюда, а не выводи их из диагнозов сам."),
     "inputSchema": {"type": "object", "properties": {}}},
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
         "status": {"type": "string", "enum": ["current", "resolved", "all"],
                    "description": "current (по умолчанию) — действующие, в любом статусе наблюдения; "
                                   "resolved — закрытые; all — все"}}}},
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
    st = args.get("status") or "current"
    if st not in ("current", "resolved", "all"):
        raise BadArgs("status — current | resolved | all")
    import problems_db
    # Фильтр по смыслу «закрыта / нет», а не по имени статуса: в базе живут active_monitoring и
    # watchful_waiting (замер 07.10), и точный фильтр active/monitoring их терял.
    # Без аргумента — только действующие, как бриф бота: 07.10 помощник подал закрытую проблему
    # главным ограничением.
    rows = [r for r in problems_db.get_problem_list()
            if st == "all" or (r.get("status") == "resolved") == (st == "resolved")]
    what = {"current": "действующих", "resolved": "закрытых", "all": ""}[st]
    if not rows:
        return _none(f"{what} проблем в медкарте нет".strip())
    keep = ("title", "status", "domain", "onset_date", "first_seen", "last_updated", "resolved_date")
    out = [{**{k: r.get(k) for k in keep}, "summary": r.get("plain_summary") or r.get("description")}
           for r in rows]
    note = {"current": ", только действующие; закрытые — status=resolved", "resolved": ", закрытые", "all": ""}[st]
    return _envelope(f"медкарта (список проблем{note})", out)


def _tool_facts(args: dict) -> str:
    import memory_facts_db
    rows = [r for r in memory_facts_db.get_facts(mem_class="fact")
            if (r.get("temporal_class") or "standing") != "transient"]
    if not rows:
        return _none("долгих фактов в памяти нет")
    out = [{"key": r.get("key"), "value": r.get("value"), "since": r.get("valid_from")} for r in rows]
    return _envelope("память о владельце (долгие факты, разовые события исключены)", out)


def _tool_health_brief(args: dict) -> str:
    # Один читатель с ботом: ровно тот бриф, что get_system_prompt кладёт боту (без правил формата Telegram).
    import patient_context
    text = patient_context.build_patient_brief()
    if not text or not text.strip():
        return _none("бриф о человеке пуст")
    return _envelope("бриф пациента, тот же, что видит бот", text)


NOT_APPROVED = "НЕ одобрено владельцем: вывод консилиума системы, не врача и не курируемое правило"


def _tool_nutrition_frame(args: dict) -> str:
    import food_profile
    import generated_food_rules
    f = food_profile.medical_frame()
    frame = {k: (sorted(v) if isinstance(v, (set, frozenset)) else v) for k, v in f.items()}
    shadow = []
    for r in generated_food_rules.get_rules(status="shadow"):
        p = r.get("payload") or {}
        shadow.append({"status": NOT_APPROVED,
                       "condition": (p.get("condition") or {}).get("label"),
                       "frame": p.get("frame"),
                       "why": (p.get("evidence") or {}).get("why"),
                       "evidence": {k: (p.get("evidence") or {}).get(k) for k in ("source", "weight")},
                       "generated": r.get("created_at")})
    return _envelope("рамка питания (код по курируемым правилам) + неодобренные правила консилиума",
                     {"frame": frame, "not_approved_rules": shadow})


_DISPATCH = {"health_brief": _tool_health_brief, "nutrition_frame": _tool_nutrition_frame,
             "lab_tests": _tool_lab_tests, "lab_results": _tool_lab_results, "day_metrics": _tool_day_metrics,
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
