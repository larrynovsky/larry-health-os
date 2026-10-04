import llm_client
#!/usr/bin/env python3.11
"""
GP Agent — Семейный врач (General Practitioner).

Роль: синтезирующий агент, держит problem_list, знает полную историю болезни.
Получает на вход: мнения специалистов (MDT) + lifestyle данные + лаборатория.
Выдаёт: еженедельный и ежемесячный отчёты в формате интервальной истории болезни.

Методология: GP/Family Medicine
- Problem list как единица учёта (активные, watchful waiting, resolved)
- Interval history: что изменилось с прошлого визита
- SOAP-структура мышления (не обязательно в тексте)
- Safety net: явные триггеры для экстренного обращения
- Dual process: быстрые паттерны + медленный анализ трендов
- Watchful waiting: явные критерии для перехода из наблюдения в действие
"""
# INTENT: proactivity — проактивность: тренд, а не всплеск (серия подряд, молчание на скудных данных).
#          Замысел и инварианты — subsystem_intent.yaml, раздел proactivity.

import asyncio
import json
import logging
from datetime import date, timedelta
from pathlib import Path

from _time_inject import get_today
from _fmt_helpers import fmt_min, fmt_or
import anthropic
import hai_core
import i18n

log = logging.getLogger(__name__)

# Окна валидности лабораторных данных (дней).
# F-094 fix (2026-05-22): дубликаты ключей удалены (Python silently
#   использовал последнюю запись, что путало читателей).
#   Для ALT/AST/CA125 итоговая seed-приоритет = "high" (продуктовое решение
#   2026-05-22 / architecture_audit Р-1).
#
# Sprint 2 fix (Р-1): этот dict — это **seed-default**. Канонические
# значения лежат в БД (lab_monitoring_schedule), и _check_lab_freshness
# уже использует `db.get_effective_lab_schedule()` как primary источник
# с fallback на этот dict. При первом init_db() данные сюда seed-ятся
# через _seed_data_freshness.
#
# Если данные устарели — агент обязан это видеть и не делать уверенных выводов.




# ── Динамический контекст истории болезни (W3-3) ─────────────────────────────









# ── Запрет приписывать глубину базы (решение владельца 2026-08-10) ───────────
#
# Повод, замеренный, а не предположенный. GP-отчёт 01.08 напечатал:
#   «корреляция sleep_deep ↔ hrv r=<r> подтверждена НА 10 ГОДАХ ДАННЫХ»
# Замер: `hrv` и `sleep_deep` непусты ОДНОВРЕМЕННО с <дата> — заметно меньше
# десяти лет. Вся `daily_metrics` идёт с начала ряда, но пара живёт
# короче. «Десять лет» — не округление, а приписанная ГЛУБИНА ДОКАЗАТЕЛЬСТВА:
# число могло быть настоящим, его основание — нет.
#
# Почему запрет, а не подстановка настоящей глубины: глубина у КАЖДОЙ пары своя
# (пересечение непустых полей), и посчитать её в промпте нельзя — её надо
# спросить у базы под конкретную пару. Пока такого канала нет, единственная
# честная позиция агента — молчать о сроке. Молчание проверяемо, догадка нет.
#
# Датчик UC-B-09 (`check_correlations_grounded`) ловит СЛЕДСТВИЕ — коэффициент
# без принятой веры. Этот запрет закрывает КЛАСС. Оба нужны: датчик увидит,
# если запрет обойдут.
_DEPTH_RULE = (
    "ГЛУБИНА ДАННЫХ: никогда не называй, за сколько лет/дней накоплены данные, "
    "и не пиши «подтверждено на N годах», если это число не пришло тебе в контексте "
    "явно. У каждой пары метрик своя глубина — она равна пересечению непустых "
    "значений, а не возрасту базы. Не знаешь срока — говори о связи без срока.\n\n"
)


@hai_core.with_answer_language
def _build_gp_system_prompt() -> str:
    history = _build_clinical_history()
    next_appt = _next_appointment()
    pctx = _get_patient_routine()
    routine = pctx["routine"]
    med_ctx = pctx["medical"]
    smoking = routine.get("smoking")
    diet = routine.get("diet")
    port = med_ctx.get("port_catheter")
    chronic = "ХРОНИЧЕСКИЕ КОНТЕКСТЫ:\n"
    _hrv_ctx   = med_ctx.get("hrv_context")
    _sleep_ctx = med_ctx.get("sleep_context")
    if _hrv_ctx:   chronic += f"- {_hrv_ctx}\n"
    if _sleep_ctx: chronic += f"- {_sleep_ctx}\n"
    if smoking or diet:
        parts = [p for p in [smoking, diet] if p]
        chronic += "- " + ". ".join(p.capitalize() for p in parts) + ".\n"
    if port:
        chronic += f"- Порт-а-катетер: {port}\n"
    chronic += "\n"
    return (
        f"Ты — семейный врач (GP) пациента: {_patient_header()}.\n\n"
        + history + "\n"
        "Следующий контроль: " + next_appt + "\n\n"
        "АКТИВНЫЙ СПИСОК ПРОБЛЕМ: загружается из базы данных (см. контекст запроса).\n\n"
        + chronic
        + "ТВОЯ РОЛЬ КАК GP:\n"
        "Ты ведёшь интервальную историю — что изменилось с прошлого отчёта.\n"
        "Ты интегрируешь мнения специалистов, но принимаешь итоговое решение сам.\n"
        "Ты знаешь когда что-то нормально для этого конкретного пациента (а не для популяции).\n"
        "Ты говоришь на русском, прямо, без менторства, как врач который знает пациента годами.\n\n"
        "ФОРМАТ ОТВЕТА (сплошной текст, без markdown заголовков):\n"
        "1. Интервальная история: что изменилось с прошлой недели — конкретно, в числах\n"
        "2. Активные проблемы: статус каждой из P001–P005 по текущим данным\n"
        "3. Паттерны и связи: что одно объясняет другое\n"
        "4. Приоритеты на неделю: 1–3 конкретных действия или наблюдения\n"
        "5. Safety net: есть ли что-то требующее внимания быстрее чем через неделю\n"
        "6. Data gaps: явно укажи какие данные устарели или отсутствуют, и какие анализы нужно сдать.\n"
        "   ВАЖНО: поле 'клин.приоритет' — это клиническая важность маркера (онкомаркер важнее рутинного),\n"
        "   НЕ срочность сдачи. Срочность определяется полем 'осталось: Nд' — чем меньше, тем срочнее.\n"
        "   Сортируй data gaps по числу оставшихся дней (меньше = срочнее).\n"
        "   НЕ называй анализ просроченным ('overdue', 'просрочен'), если его окно ещё не истекло.\n\n"
        "ВАЖНО: Если данные старше окна валидности — не делай уверенных выводов по ним.\n"
        "Раздел data gaps должен быть конкретным: что сдать, когда, зачем.\n\n"
        + _DEPTH_RULE +
        "Длина: 12–18 предложений. Тон: коллегиальный, точный, не тревожный."
    )


@hai_core.with_answer_language
def _build_gp_monthly_prompt() -> str:
    history = _build_clinical_history()
    next_appt = _next_appointment()
    return (
        f"Ты — семейный врач пациента: {_patient_header()}.\n"
        "(" + history + ")\n"
        "Следующий контроль: " + next_appt + "\n\n"
        "Ежемесячный отчёт — это стратегический взгляд, не тактика.\n\n"
        "ФОРМАТ (сплошной текст):\n"
        "1. Тренды за 30 и 90 дней — что системно меняется\n"
        "2. Статус problem_list: что сдвинулось, что стоит пересмотреть\n"
        "3. Пробелы в данных: чего не хватает, что пора сдать или проверить\n"
        "4. Задачи на месяц: конкретные, с дедлайнами где возможно\n"
        "5. Один нетривиальный вопрос — что стоит исследовать глубже\n\n"
        + _DEPTH_RULE +
        "Длина: 15–20 предложений. Без bullet points."
    )


def _gate_weave_instruction() -> str:
    """Инструкция LLM вплетать гейт-блоки брифа. Пустая строка, если гейт OFF.

    Инвариант (Bug 1, 2026-07-14): КАЖДЫЙ контентный блок, который render кладёт в
    user_content под гейтом (СРЕДА / ПЛАН / ЕДА), обязан иметь здесь инструкцию
    вплетания. Иначе Sonnet роняет блок в финальном тексте непоследовательно —
    тот же класс бага, что был со СРЕДА. Сторож: test_brief_prompt_weave.
    Чистая функция (без БД) — намеренно тестируема в изоляции.
    """
    if __import__("os").environ.get("MORNING_BRIEF_GATE") != "1":
        return ""
    return (
        "\n\nЖЁСТКО (анти-повтор): называй геномные варианты (по названию гена/rs-варианта) и "
        "конкретные факты ТОЛЬКО если они ЯВНО присутствуют в блоке ГЕНОМНЫЙ КОНТЕКСТ этого "
        "запроса — ничего не добавляй по памяти и НЕ бери названия генов из формулировки самой "
        "этой инструкции как факт о пациенте. Если блока ГЕНОМНЫЙ КОНТЕКСТ в запросе НЕТ — не "
        "упоминай НИ ОДНОГО гена и НИ ОДНОГО rs-номера, даже «известных» генов сна/стресса/обмена; "
        "максимум — нейтрально «возможно, это структурная особенность, а не разовая», без названия. "
        "Придуманный ген в медицинском тексте — грубая ошибка. Не квалифицируй показатель как "
        "«хронический», «устойчивый», «недельный/многодневный паттерн», если соответствующей "
        "многодневной находки НЕТ в контексте — сообщай только сегодняшнюю ночь как есть."
        " ВАЖНО про блоки СРЕДА/ПЛАН/ЕДА ниже: гейт уже отобрал в них немного пунктов — вплети "
        "КАЖДЫЙ пункт каждого блока отдельной конкретной фразой. НЕ выбирай один и не роняй "
        "остальные, даже если пункт кажется второстепенным (море, тропа, продукт — это часть "
        "брифа, а не опционал)."
        " Блок «СРЕДА» — сегодняшние внешние условия (UV, жара, воздух, пыль, море): вплети каждый "
        "его пункт одной фразой (что именно и что практически с этим сегодня — например «море "
        "27°C, хороший день поплавать»), без общих слов о погоде и без прогноза на неделю."
        " Блок «ПЛАН» — событие дня (поездка «за день до» И/ИЛИ тропа на выходной): назови КАЖДЫЙ "
        "пункт конкретной фразой (и поездку, и тропу, если оба есть), не сливай их в один и не "
        "превращай в общий совет."
        " Блок «ЕДА» — сезонные продукты, выгодные этому пациенту по его геному/ограничениям: "
        "обязательно назови каждый одной фразой (что и чем полезен сегодня), без общих слов о "
        "правильном питании. Не пропускай блок ЕДА — он чаще всего теряется."
    )


_TONE_INSTRUCTION = (
    "\n\nТОН (важно): ровный, на равных, как знающий собеседник рядом, а не врач-инструктор "
    "сверху. НЕ командуй в императиве («сделай», «ляг не позже», «только SPF», «минимизируй») — "
    "говори через констатацию и следствие, оставляя решение за человеком: не «ляг раньше», а "
    "«после такой ночи ранний отбой заметно помогает»; не «только SPF 50 и тень», а «UV высокий — "
    "тень и SPF 50 снимают почти весь риск». Без морализаторства, мотивационного шума и общих "
    "советов о ЗОЖ. Тёплая ирония и точная метафора допустимы. Коротко, максимум смысла."
    "\n\nФОРМАТ: НЕ пиши всё одним абзацем-кирпичом. Разбивай на короткие абзацы по смыслу, "
    "каждая новая тема — с НОВОЙ СТРОКИ через ПУСТУЮ строку между абзацами. Ориентир: "
    "тело/сон/восстановление — отдельный абзац; среда (UV/жара/воздух/море) — отдельный; "
    "план и активность (поездка, тропа) — отдельный; еда — отдельный. Пустые блоки пропускай, "
    "заголовков не делай — просто абзацы через пустую строку. Без bullet-списков."
)


@hai_core.with_answer_language
def _build_gp_daily_prompt(genome_in_scope: bool = True) -> str:
    history = _build_clinical_history()
    next_appt = _next_appointment()
    return (
        f"Ты — семейный врач пациента: {_patient_header()}.\n\n"
        "(" + history + ". Следующий визит: " + next_appt + ")\n\n"
        "АКТИВНЫЙ СПИСОК ПРОБЛЕМ: загружается из базы данных (см. контекст запроса).\n\n"
        "УТРЕННИЙ БРИФИНГ — это короткий обход, не клинический разбор.\n\n"
        "Ты получишь данные от lifestyle-агентов (те, у кого были данные за вчера),\n"
        "а также геномный контекст по доменам (сон, стресс, энергия, движение).\n"
        "В контексте будет блок ПРОФИЛЬ ПАЦИЕНТА — используй его, чтобы не спрашивать\n"
        "о факторах, которые явно не релевантны.\n\n"
        # Геном-рамка — только когда геном реально в контексте дня (genome_in_scope).
        # Имена генов НЕ называем как пример: сама формулировка праймила Sonnet назвать
        # ген даже при подавленной карточке (риск Ф0). Под гейтом, если геном подавлен,
        # абзац убирается целиком — не открываем дверь, которую гейт закрыл.
        + (
            "Геном — модификатор интерпретации, не диагноз. Если агент сообщает о проблеме\n"
            "(например, мало глубокого сна), а в геноме есть релевантный патогенный вариант —\n"
            "это меняет рекомендацию: проблема может быть хронической генетической, а не ситуативной.\n\n"
            if genome_in_scope else ""
        )
        + "Твоя задача — собрать короткий бриф ОТДЕЛЬНЫМИ АБЗАЦАМИ (между абзацами ПУСТАЯ "
        "строка; без заголовков и списков). Порядок и правила абзацев:\n"
        "0) СОБЫТИЕ: если в контексте есть строка, начинающаяся с «СОБЫТИЕ (…)» — НАЧНИ бриф "
        "с неё отдельной короткой фразой (смена города / вернулся домой / кольцо снято), это "
        "важнее рутины. Нет такой строки — ничего подобного не выдумывай.\n"
        "1) ТЕЛО: 1–3 фразы — общий фон дня по данным (сон, HRV, активность), только по тому, "
        "что есть в контексте. Если в контексте ЕСТЬ блок SLEEP — сон обязателен КАЖДЫЙ день "
        "отдельной развёрнутой фразой: назови ключевые числа (часы, глубокий, REM, эффективность) "
        "и что они значат для восстановления сегодня; никогда не отписывайся «сон непримечателен» "
        "и не роняй его как несущественный.\n"
        "2) СРЕДА: если в запросе есть блок «СРЕДА» — ОТДЕЛЬНЫМ абзацем вплети КАЖДЫЙ его пункт "
        "(UV, жара, воздух, море) конкретной фразой.\n"
        "3) ПЛАН: если есть блок «ПЛАН» — ОТДЕЛЬНЫМ абзацем назови КАЖДЫЙ пункт (поездка, тропа).\n"
        "4) ЕДА: если есть блок «ЕДА» — ОТДЕЛЬНЫМ абзацем назови КАЖДЫЙ продукт и чем он полезен.\n"
        "ЖЁСТКОЕ ПРАВИЛО: блок присутствует в контексте → его абзац ОБЯЗАТЕЛЕН, не роняй "
        "(еда теряется чаще всего — проверь, что она есть). Блока нет в контексте → абзац "
        "пропусти, ничего не выдумывай."
        + _gate_weave_instruction()
        + _TONE_INSTRUCTION
    )


@hai_core.with_answer_language
def _build_problem_list_reviewer_prompt() -> str:
    import health_db as _db; _db.init_db()
    _n = _db.get_profile_context().get("identity", {}).get("name") or "(имя не указано)"
    return """Ты — клинический редактор списка проблем GP пациента __PATIENT_NAME__.

Ты получишь:
1. Текущий problem list (из базы данных)
2. GP отчёт за этот период
3. Свежие лабораторные данные и lifestyle тренды

Твоя задача: предложить изменения если они обоснованы данными.

Возможные действия:
- "update_status": изменить статус проблемы (active_monitoring/watchful_waiting/resolved)
- "update_field": обновить поле (watch_trigger, watch_deadline, notes, description)
- "add": добавить новую проблему
- "resolve": перевести в resolved

ПРАВИЛА:
- Предлагай изменение только если есть конкретное клиническое обоснование в данных
- Не предлагай косметические правки без медицинской причины
- Статус "resolved" — только если проблема действительно закрыта, не просто неактивна
- Новая проблема — только если паттерн повторяется 2+ раза или имеет критическую значимость
- Если изменений нет — верни пустой список changes
- В поле "old_value" пиши не более 60 символов (обрезай длинные строки многоточием)

Ответь ТОЛЬКО JSON без markdown, без пояснений:
{
  "changes": [
    {
      "action": "update_status|update_field|add|resolve",
      "problem_id": "P001",
      "field": "status",
      "old_value": "текущее значение",
      "new_value": "новое значение",
      "reason": "конкретное клиническое обоснование из данных"
    }
  ]
}""".replace("__PATIENT_NAME__", _n)





def _review_problem_list(gp_report: str, context: str, source: str) -> list:
    """
    Запускает отдельный Claude-вызов для review problem list.
    Сохраняет пропозал в БД если есть изменения.
    Возвращает список изменений (может быть пустым).
    """
    import json as _j
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    import health_db as db

    problem_block = _format_problem_list_for_prompt()

    user_content = f"""{problem_block}

GP ОТЧЁТ:
{gp_report}

КОНТЕКСТ ДАННЫХ (краткий):
{context[:3000]}"""

    try:
        client = _get_client()
        response = client.messages.create(task="gp_agent._review_problem_list",
            model=hai_core.get_model("sonnet"),
            max_tokens=2500,
            system=_build_problem_list_reviewer_prompt(),
            messages=[{"role": "user", "content": user_content}]
        )
        raw = llm_client.answer_text(response).strip()
        # Убираем markdown если модель всё же добавила
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        # Многоуровневый парсинг: прямой → очистка trailing commas → regex-извлечение
        import re as _re
        def _try_parse(s):
            try:
                return _j.loads(s)
            except _j.JSONDecodeError:
                # Trailing commas перед ] или }
                cleaned = _re.sub(r',\s*([\]}])', r'\1', s)
                try:
                    return _j.loads(cleaned)
                except _j.JSONDecodeError:
                    # Извлекаем первый {...} блок
                    m = _re.search(r'\{[\s\S]*\}', cleaned)
                    if m:
                        return _j.loads(m.group(0))
                    raise
        try:
            parsed = _try_parse(raw)
        except _j.JSONDecodeError as e:
            log.warning(f"Problem list raw response (first 300): {raw[:300]!r}")
            raise
        changes = parsed.get("changes", [])

        if changes:
            proposal_id = db.save_problem_proposal(source, changes)
            log.info(f"Problem list proposal #{proposal_id}: {len(changes)} changes from {source}")
        else:
            log.info(f"Problem list review ({source}): no changes proposed")

        return changes

    except Exception as e:
        log.warning(f"Problem list review failed: {e}")
        return []


def _get_client() -> anthropic.Anthropic:
    return llm_client.guarded_client()




































def _save_gp_report(report_type: str, end_date: date, content: str, period_days: int):
    """Сохраняет GP отчёт в agent_reports."""
    try:
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        import health_db as db
        db.save_agent_report(
            agent_type="gp",
            agent_name=f"gp_{report_type}",
            date_str=str(end_date),
            has_findings=True,
            data_queried=[],
            pubmed_ids=[],
            peers_reviewed=[],
            changes_summary="",
            findings=content,
            recommendations=None,
            raw_output=None,
            period_days=period_days,
        )
    except Exception as e:
        log.warning(f"Не удалось сохранить GP отчёт: {e}")


# ── Публичный API ─────────────────────────────────────────────────────────────

def run_specialists_and_save(end_date: date = None, period_days: int = 7) -> dict:
    """
    Запускает MDT специалистов и сохраняет результат в agent_reports.
    Вызывается по расписанию (воскресенье 23:00).
    Возвращает dict с synthesis и opinions.
    """
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    import wellally_consult as mdt
    import health_db as db

    if end_date is None:
        end_date = get_today() - timedelta(days=1)

    log.info(f"GP: запуск MDT специалистов за период до {end_date}...")
    result = mdt.run_mdt_consultation(end_date=end_date, period_days=period_days)

    synthesis  = result.get("synthesis", "")
    opinions   = result.get("opinions", [])
    has_findings = bool(synthesis and len(synthesis) > 50)

    # Собираем резюме по специалистам
    specialist_summaries = "\n\n".join([
        f"{r['specialist'].upper()}:\n{r['opinion']}"
        for r in opinions if r.get("ok")
    ])

    # Сохраняем полный MDT результат
    db.save_agent_report(
        agent_type="mdt",
        agent_name="mdt_weekly",
        date_str=str(end_date),
        has_findings=has_findings,
        data_queried=[r["specialist"] for r in opinions if r.get("ok")],
        pubmed_ids=[],
        peers_reviewed=[],
        changes_summary="",
        findings=synthesis,
        recommendations=None,
        raw_output=None,
        period_days=period_days,
    )

    log.info(f"GP: MDT сохранён в agent_reports ({len(opinions)} специалистов)")
    return result


def _guard_absent_claims(report: str, context: str, system_prompt: str, client, model: str,
                         max_tokens: int, kind: str, retries: int = 2) -> str:
    """Сторож «не сдавался» (2026-08-30): текст отчёта против канона lab_results.

    Нашёл противоречие → повтор с поправкой; факты НАКАПЛИВАЮТСЯ между попытками
    (замер 13.09.2026: первая попытка возражала про один аналит, вторая — про два других,
    и вторая ничего не знала про первую; отказ пришёл на НОВОМ наборе аналитов, а не на
    том же). Попытки кончились → отказ (решение владельца 13.09: fail-closed остаётся —
    клинически лживый отчёт хуже отсутствующего). Отчёт не сохраняется, прошлый остаётся.

    В ERROR-лог едут САМИ КЛАУЗЫ: отклонённый текст не сохраняется нигде, и без них
    следующий разбор идёт вслепую. Человек получает объяснение —
    что утверждалось и чем это опровергнуто. Имена в code-span: выдуманный
    `Example_J` содержит подчёркивание, которое может нарушить Markdown-разбор Telegram.
    """
    import gp_context as _gc
    import health_db as db
    import labs_db as _ldb
    last = {r["test_name"]: r["date"] for r in db.get_recent_labs(730 * 5)}
    win = _ldb.PROMPT_WINDOW_DAYS       # граница показа — та же, что объявляет лаб-блок
    known: dict[str, str] = {}          # аналит → дата последней строки, накопительно
    hits: list = []
    for attempt in range(retries + 1):
        hits = _gc.absent_claims_contradicted(report, last, window_days=win)
        if not hits:
            return report
        for h in hits:
            known[h["test"]] = h["last_date"]
        clauses = " || ".join(h["clause"][:160] for h in hits)
        if attempt == retries:
            break
        facts = "; ".join(f"{t} — последняя строка {d}" for t, d in sorted(known.items()))
        log.warning(f"gp {kind}: «не сдавался» против канона ({len(hits)}), поправка "
                    f"{attempt + 1}/{retries}: {facts} | клаузы: {clauses}")
        fix = (f"\n\n=== ПОПРАВКА ВАЛИДАТОРА ===\nСледующие аналиты СДАНЫ и есть в базе: {facts}. "
               "Не пиши про них «не сдавался», «ни разу», «не проверен», «отсутствует» — "
               "ссылайся на дату последней строки («нет данных с 2023») либо не упоминай вовсе. "
               f"Если дата новее чем {win} дн. назад, строка показана в лаб-блоке выше: про "
               "такой аналит нельзя писать и «нет данных» — бери значение из блока.")
        resp = client.messages.create(task="gp_agent._guard_absent_claims", model=model, max_tokens=max_tokens, system=system_prompt,
                                      messages=[{"role": "user", "content": context + fix}])
        report = llm_client.answer_text(resp)
    log.error(f"gp {kind}: отчёт ОТКЛОНЁН после {retries} поправок — «не сдавался» против "
              f"канона: {'; '.join(f'{t} ({d})' for t, d in sorted(known.items()))} "
              f"| клаузы: {clauses}")
    detail = "\n".join(f"• `{h['test']}` — последняя строка {h['last_date']}; "
                       f"в отчёте: «{h['clause'][:120]}»" for h in hits)
    raise _AbsentClaimRejected(
        f"отчёт {kind} не отправлен: валидатор поймал утверждение об отсутствии анализа, "
        f"который в базе ЕСТЬ.\n{detail}\nОтчёт не сохранён, прошлый остаётся. Частая "
        f"причина — аналит вне окна лаб-блока или он не показан специалистам.")


class _AbsentClaimRejected(RuntimeError):
    """Отказ сторожа «не сдавался»: отчёт не публикуется, прошлый остаётся."""


def generate_weekly_report(end_date: date = None, run_mdt: bool = True) -> str:
    """
    Еженедельный GP отчёт.
    
    Если run_mdt=True — сначала запускает специалистов (для on-demand /weekly).
    Если run_mdt=False — читает последний MDT из agent_reports (для scheduled GP).
    """
    if end_date is None:
        end_date = get_today() - timedelta(days=1)

    # 1. Получаем MDT мнения
    mdt_synthesis = ""
    if run_mdt:
        try:
            mdt_result = run_specialists_and_save(end_date, period_days=7)
            mdt_synthesis = mdt_result.get("synthesis", "")
        except Exception as e:
            log.warning(f"MDT ошибка в weekly: {e}")
    else:
        # Читаем из базы
        try:
            import sys
            sys.path.insert(0, str(Path(__file__).parent))
            import health_db as db
            reports = db.get_reports_with_findings(str(end_date - timedelta(days=3)), agent_type="mdt")
            if reports:
                mdt_synthesis = reports[0].get("findings", "")
        except Exception:
            pass

    # 2. Контекст данных для GP
    # Блок «сдано один раз» выключен в еженедельном отчёте (решение владельца 21.09):
    # реплей 0/5 против 0/5 при +12 % токенов. Флаг — у вызывающего, а не по period_days:
    # решение принято про ОТЧЁТ, а не про длину периода.
    context = _build_gp_context(end_date, period_days=7, unrepeated=False)

    if mdt_synthesis:
        context += f"\n\n=== МНЕНИЯ СПЕЦИАЛИСТОВ (MDT) ===\n{mdt_synthesis}"

    # 3. Promethease геномный контекст (только если есть actionable варианты)
    try:
        import promethease_context as pc
        GP_DOMAINS = ["sleep", "stress", "metabolism", "cardio", "inflammation", "nutrition"]
        prom_block = pc.build_weekly_monthly_section(GP_DOMAINS, magnitude_min=2.0)
        if prom_block:
            context += f"\n\n=== ГЕНОМНЫЙ КОНТЕКСТ (Promethease) ===\n{prom_block}"
    except Exception as _pe:
        log.debug(f"promethease_context weekly: {_pe}")

    # 4. GP синтез через Sonnet
    client = _get_client()
    response = client.messages.create(task="gp_agent.generate_weekly_report",
        model=hai_core.get_model("sonnet"),
        max_tokens=4096,
        system=_build_gp_system_prompt(),
        messages=[{"role": "user", "content": context}],
    )
    report = llm_client.answer_text(response)
    try:
        report = _guard_absent_claims(report, context, _build_gp_system_prompt(), client,
                                      hai_core.get_model("sonnet"), 4096, "weekly")
    except _AbsentClaimRejected as _rej:
        return f"⛔ {_rej}"

    # 5. Сохраняем
    _save_gp_report("weekly", end_date, report, period_days=7)
    log.info(f"GP weekly отчёт сгенерирован за {end_date}")

    # 5. Review problem list (фоново, не блокирует отчёт)
    try:
        _review_problem_list(report, context, source="gp_weekly")
    except Exception as e:
        log.warning(f"Problem list review failed: {e}")

    return report


def generate_monthly_report(end_date: date = None) -> str:
    """
    Ежемесячный GP отчёт — стратегический взгляд, 30/90 дней.
    """
    if end_date is None:
        end_date = get_today() - timedelta(days=1)

    context = _build_gp_context(end_date, period_days=30)

    # Promethease геномный контекст для месячного отчёта
    try:
        import promethease_context as pc
        GP_DOMAINS = ["sleep", "stress", "metabolism", "cardio", "inflammation", "nutrition"]
        prom_block = pc.build_weekly_monthly_section(GP_DOMAINS, magnitude_min=2.0)
        if prom_block:
            context += f"\n\n=== ГЕНОМНЫЙ КОНТЕКСТ (Promethease) ===\n{prom_block}"
    except Exception as _pe:
        log.debug(f"promethease_context monthly: {_pe}")

    # Добавляем последние MDT отчёты за месяц
    try:
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        import health_db as db
        reports = db.get_reports_with_findings(str(end_date - timedelta(days=35)), agent_type="mdt")
        if reports:
            summaries = "\n\n---\n".join([
                f"MDT {r['date']}:\n{r.get('findings','')}"
                for r in reports[:4]
            ])
            context += f"\n\n=== MDT ОТЧЁТЫ ЗА МЕСЯЦ ===\n{summaries}"
    except Exception:
        pass

    client = _get_client()
    response = client.messages.create(task="gp_agent.generate_monthly_report",
        model=hai_core.get_model("sonnet"),
        max_tokens=6000,
        system=_build_gp_monthly_prompt(),
        messages=[{"role": "user", "content": context}],
    )
    report = llm_client.answer_text(response)
    try:
        report = _guard_absent_claims(report, context, _build_gp_monthly_prompt(), client,
                                      hai_core.get_model("sonnet"), 6000, "monthly")
    except _AbsentClaimRejected as _rej:
        return f"⛔ {_rej}"

    # data-in-code-9 инкремент 4: месячная сводка-diff сгенерированного рулбука (обзор, НЕ гейт).
    try:
        import food_rule_review
        _summary = food_rule_review.render_summary(food_rule_review.build_diff())
        if _summary:
            report += "\n\n" + _summary
    except Exception as e:
        log.warning(f"food-rule summary append failed: {e}")

    _save_gp_report("monthly", end_date, report, period_days=30)
    log.info(f"GP monthly отчёт сгенерирован за {end_date}")

    try:
        _review_problem_list(report, context, source="gp_monthly")
    except Exception as e:
        log.warning(f"Problem list review failed: {e}")

    return report


# _triage_metric и _auto_create_metric_task сняты: отдельная задача о тренде
# дублирует еженедельный разбор (gp_context._build_trends_block).
# Выдуманная задача «разобрать изменение Example_K» может остаться без результата
# даже после отметки о закрытии. Кроме того, модуль наклона не различает рост и спад,
# поэтому сам по себе не обосновывает слово «ухудшается».


def compute_step_target(activity_date: date) -> str:
    """
    Детерминированная рекомендация по шагам на день.
    Основана на readiness, HRV vs 30d avg, стрессовом профиле последних дней.
    Возвращает строку для вставки в контекст GP.
    """
    try:
        import health_db as db
        day    = db.get_day(str(activity_date))
        s30    = db.get_stats(30, activity_date)

        readiness  = day.get("readiness_score") or day.get("readiness")
        _apple_day = day.get("apple_health") or {}
        hrv_today  = (day.get("hrv") or {}).get("avg") or _apple_day.get("hrv")
        hrv_30avg = s30.get("avg_hrv") or 0

        # Стрессовые дни подряд (последние 2 дня)
        conn = db.get_conn()
        stress_rows = conn.execute("""
            SELECT stress_summary FROM daily_metrics
            WHERE date BETWEEN ? AND ?
            ORDER BY date DESC LIMIT 2
        """, (str(activity_date - timedelta(days=1)), str(activity_date))).fetchall()
        conn.close()
        consecutive_stress = sum(
            1 for r in stress_rows if (r["stress_summary"] or "") == "stressful"
        )

        # Базовый уровень по readiness
        if readiness is None:
            return "ЦЕЛЬ ШАГОВ: данных readiness нет — ориентируйся на самочувствие"

        if readiness < 60:
            base_min, base_max, label = 3000, 5000, "Восстановительный день"
        elif readiness < 75:
            base_min, base_max, label = 6000, 8000, "Умеренный день"
        elif readiness < 85:
            base_min, base_max, label = 8000, 10000, "Активный день"
        else:
            base_min, base_max, label = 10000, 12000, "Пиковый день"

        modifiers = []

        # HRV ниже 85% нормы — снижаем
        if hrv_today and hrv_30avg and hrv_today < hrv_30avg * 0.85:
            base_min = int(base_min * 0.75)
            base_max = int(base_max * 0.75)
            modifiers.append(f"HRV {hrv_today:.0f}мс < норма → −25%")

        # HRV критически низкий (< 15мс) — форс-мажор восстановления
        if hrv_today and hrv_today < 15:
            base_min, base_max = 2000, 4000
            label = "⚠ Критический отдых"
            modifiers = [f"HRV {hrv_today:.0f}мс — ВНС истощена"]

        # 2 стрессовых дня подряд — снижаем
        if consecutive_stress >= 2 and "Критический" not in label:
            base_min = int(base_min * 0.70)
            base_max = int(base_max * 0.70)
            modifiers.append("2 стрессовых дня подряд → −30%")

        result = f"ЦЕЛЬ ШАГОВ: {base_min:,}–{base_max:,} ({label})"
        if modifiers:
            result += f"  [{', '.join(modifiers)}]"
        result += f"  |  readiness={readiness}"
        return result

    except Exception as e:
        log.debug(f"compute_step_target: {e}")
        return ""


def _observation_window_note(n_days: int) -> str:
    """Предупреждение для GP-синтеза, когда данных наблюдения мало.

    Короткий ряд нельзя выдавать за полное окно агрегата: подпись «7d/30d avg»
    сама по себе не доказывает длительность наблюдения и не обосновывает тренд.
    Пустая строка при >=7 днях: 7 — минимальное окно для недельного тренда.
    Это граница доступных данных, а не доказательство устойчивости тренда.
    """
    if n_days >= 7:
        return ""
    return (
        f"⚠ НОВЫЙ ПРОФИЛЬ: всего {n_days} дн. данных наблюдения. "
        "Значения «7d/30d avg» посчитаны по этим немногим дням, а не по полной "
        "неделе/месяцу. НЕ описывай многодневные тренды и паттерны («N-е сутки», "
        "«снова», «уже не случайность», «хронический») — их нельзя обосновать на "
        "таком объёме. Комментируй только сегодняшний день и явные однодневные факты."
    )


def _recent_chat_notes(ref_date, days: int = 2) -> str:
    """Тонкий делегат к ЕДИНОМУ источнику patient_context.recent_notes (C-1, 2026-07-05).
    Оставлен для обратной совместимости call-site; логика — в patient_context."""
    import patient_context as _pc
    return _pc.recent_notes(days)


def _validate_brief(report: str, cards: list, shown: set) -> dict:
    """Пост-рендер сторож (N12/риск Ф0): подавленное (ген/verbatim-forbidden) не должно
    просочиться в финальный текст. Разбивает cards на shown/suppressed по shown-set гейта
    и зовёт brief_validator.validate. Вынесен из generate_daily_report, чтобы РАЗБИЕНИЕ
    самого хука было тестируемо (иначе зелёный юнит-тест чистой функции ≠ прод сторожит)."""
    import brief_validator as _bv
    shown_cards = [c for c in cards if c.semantic_key in shown]
    suppressed = [c for c in cards if c.semantic_key not in shown]
    return _bv.validate(report, shown_cards, suppressed)


def _env_context_lines(cards: list, shown: set, probe: dict | None) -> list:
    """Строки блока СРЕДА для промпта (C1, 2026-07-15). ЧИСТАЯ функция — тестируема
    отдельно от generate_daily_report. probe = env_context.env_probe().

    Три случая, различаемые ЯВНО (иначе LLM сочиняет «данных по среде не поступало»,
    хотя данные пришли — просто ниже порогов):
      1) есть показанные env-карты → блок с находками (метка места, travel-режим);
      2) находки БЫЛИ, но подавлены анти-повтором → молчим (это НЕ «чисто»);
      3) карт нет вовсе → «проверено, чисто» ТОЛЬКО в поездке и только если источник
         ответил; дома чистая среда (не плодим ежедневный повтор) / нет локации /
         фетч упал → молчим.
    """
    env_shown = [c for c in cards if getattr(c, "provider", "") == "env"
                 and c.semantic_key in shown]
    env_raw = [c for c in cards if getattr(c, "provider", "") == "env"]
    p = probe or {}
    away = bool(p.get("away"))
    # Без имени места — нейтральная подпись, не город владельца (до 2026-09-23 здесь стоял
    # литерал его города и уходил в бриф любого тенанта; pii-scrub).
    place = p.get("place") or ("место не определено" if away else "дом")
    if env_shown:
        header = f"СРЕДА ({place}, поездка):" if away else f"СРЕДА ({place}):"
        return [header] + ["  " + c.evidence_summary for c in env_shown] + [""]
    if env_raw:
        return []  # находки были, но подавлены — анти-повтор владеет, «чисто» не пишем
    if away and p.get("reachable"):
        return [f"СРЕДА ({place}, поездка):",
                "  проверено — жара, UV и воздух в норме", ""]
    return []


# Гены, которые Sonnet чаще всего ДОСОЧИНЯЕТ по знанию (не из контекста), обсуждая
# сон/стресс/обмен. Плюс реальные гены пациента из БД (см. _gene_universe).
_CONFAB_GENES = {
    "PER1", "PER2", "PER3", "CRY1", "CRY2", "CLOCK", "ARNTL", "BMAL1", "MTNR1A",
    "MTNR1B", "MTHFR", "MAOA", "MAOB", "COMT", "BDNF", "APOE", "FTO", "ACTN3",
    "VDR", "CYP1A2", "ALDH2", "SLC6A4", "HTR2A", "TPH2", "FKBP5", "NR3C1",
    "ADRB2", "ADORA2A", "TCF7L2", "PPARG", "LEPR", "LEP", "MC4R", "ADIPOQ",
    "GCK", "CACNA1C", "DRD2", "DRD4", "GABRA6",
}


def _gene_universe() -> set:
    """Множество имён генов, за которыми следим в тексте: curated-конфаб + реальные гены пациента."""
    genes = set(_CONFAB_GENES)
    try:
        import health_db as _db
        with _db.get_conn() as c:
            for r in c.execute("SELECT DISTINCT gene FROM genetic_variants WHERE gene IS NOT NULL"):
                g = (r[0] or "").strip().upper()
                if g.isascii() and g.isalnum() and 2 < len(g) <= 8:
                    genes.add(g)
    except Exception:  # silent-ok: БД недоступна → только curated-набор, скраб мягче
        pass
    return genes


def _scrub_unapproved_genes(report: str, shown: set) -> tuple:
    """Детерминированно удаляет ПРЕДЛОЖЕНИЯ, называющие ген/rs, НЕ одобренный гейтом.

    Промпт-запрет не держит: Sonnet досочиняет «знаменитые» гены (PER3, MTNR1B) по
    находке, даже когда их нет в контексте. Гарантия — пост-обработка: любой ген из
    _gene_universe (или rs-номер) не из одобренного гейтом набора → предложение вон.
    Выдуманное геном-утверждение в мед-тексте недопустимо; лучше убрать фразу целиком.
    Возвращает (очищенный_текст, [список удалённых токенов по предложениям])."""
    import re
    approved = {k.split(":", 2)[2].upper() for k in shown
                if k.startswith("genome:") and k.count(":") >= 2}
    universe = _gene_universe()
    allowed_rsids: set = set()
    if approved:
        try:
            import health_db as _db
            ph = ",".join("?" * len(approved))
            with _db.get_conn() as c:
                for r in c.execute(
                        f"SELECT rsid FROM genetic_variants WHERE UPPER(gene) IN ({ph})",
                        tuple(approved)):
                    if r[0]:
                        allowed_rsids.add(str(r[0]).lower())
        except Exception:  # silent-ok: нет rsid одобренных генов → скраб чуть строже, безопасно
            pass

    def _bad(s: str) -> set:
        toks = {t.upper() for t in re.findall(r'\b[A-Za-z][A-Za-z0-9]{1,7}\b', s)}
        return ((toks & universe) - approved) | (
            {m.lower() for m in re.findall(r'\brs\d+\b', s)} - allowed_rsids)

    text, removed = _cut_sentences(report, _bad)
    return text, removed


def _cut_sentences(report: str, bad) -> tuple:
    """Вырезает предложения, для которых bad(s) непусто; общий дом правила выреза для
    _scrub_unapproved_genes и _strip_leaked_genes. Абзацы (\\n\\n) сохраняем — бриф не
    схлопывается в кирпич.

    Сирота — предложение СРАЗУ после вырезанного, которое на него ссылается
    («это», «такой», «поэтому»…). Оно уходит вместе с опорой, цепочкой.
    Выдуманный пример: «Сигнал Example_L требует проверки. Поэтому уточните источник.»
    После выреза первого предложения второе теряет опору и тоже удаляется.
    Независимое соседнее «Файл сохранён.» остаётся. Признак ссылки — слово,
    а не смысл: предложение со своим «это» после выреза может уйти зря.
    Список слов — представление языка, не клиническая величина (§9 класс 3)."""
    import re
    removed: list = []
    out_paras = []
    for para in report.split("\n\n"):
        kept = []
        prev_cut = False
        for s in re.split(r'(?<=[.!?])\s+', para):
            h = bad(s)
            if h:
                removed.append(sorted(h))
                prev_cut = True
            elif prev_cut and _BACKREF.search(s):
                removed.append(["(опора вырезана)"])
            else:
                kept.append(s)
                prev_cut = False
        if kept:
            out_paras.append(" ".join(kept).strip())
    return "\n\n".join(out_paras).strip(), removed


import re as _re_backref  # noqa: E402 — рядом с единственным читателем, _cut_sentences
# Слова, которыми предложение опирается на предыдущее. Граница слова (\b юникодная):
# «эталон», «этаж», «таксист» не ловятся. Английские it/its/that/so сюда НЕ входят (ревью X4,
# 28.09): они стоят почти в каждом предложении, и срез после вырезанного съел бы весь абзац —
# потеря содержания хуже осиротевшего «it».
_BACKREF = _re_backref.compile(
    r"\b(это|этот|эта|эти|этого|этой|этим|этих|этому|такой|такая|такое|такие|такого|"
    r"поэтому|отсюда|при этом|this|these|those|such|therefore|thus|hence|consequently|accordingly|"
    r"as a result|because of this|in this case)\b", _re_backref.IGNORECASE)


def _strip_leaked_genes(report: str, genes: set) -> tuple:
    """Backstop к _scrub_unapproved_genes: удаляет предложения, называющие ЛЮБОЙ ген из
    `genes` (явный список утечки от валидатора brief_validator). Ключевое отличие от скраба
    выше — работает по ПЕРЕДАННОМУ списку, не по _gene_universe(), поэтому НЕ зависит от его
    полноты/готовности БД. Введён C3 (2026-07-15): инцидент, где скраб промолчал (ген вне одобренных), а
    валидатор поймал — но было log-only и утекло. Теперь находка валидатора → детерминированный
    вырез. Абзацы (\n\n) сохраняем."""
    import re
    up = {str(g).upper() for g in genes if g}
    if not up:
        return report, []

    def _hit(s: str) -> set:
        return {t.upper() for t in re.findall(r'\b[A-Za-z][A-Za-z0-9]{1,7}\b', s)} & up

    return _cut_sentences(report, _hit)


# N подряд ночей без Oura → «надень кольцо» (B). Порог N — решение владельца, §9 config.
SLEEP_ABSENT_EVENT_N_DEFAULT = 2


def _absent_streak(present_today_first: list) -> int:
    """Сколько ПОДРЯД ведущих ночей без данных (present=False), от сегодня назад. ЧИСТАЯ."""
    n = 0
    for has in present_today_first:
        if has:
            break
        n += 1
    return n


def _sleep_absent_streak(conn, target, lookback: int = 14) -> int:
    """Сколько подряд последних ночей (включая target) БЕЗ записи сна Oura. Пустая ночь =
    нет строки в daily_metrics ИЛИ sleep_total пуст/0. Для события «надень кольцо» (N ночей)."""
    from datetime import timedelta
    flags = []
    d = target
    for _ in range(lookback):
        row = conn.execute("SELECT sleep_total FROM daily_metrics WHERE date=?",
                            (str(d),)).fetchone()
        flags.append(bool(row and row[0]))
        d = d - timedelta(days=1)
    return _absent_streak(flags)


def _scrub_fabricated_sleep(report: str, sleep_present: bool) -> tuple:
    """Анти-конфаб (B2, 2026-07-15): когда за сегодня НЕТ записи сна (Oura не прислал),
    LLM не должен называть числа сна. Тот же класс, что выдуманный ген — придуманное
    измерение в мед-тексте недопустимо. Детерминированно удаляет ПРЕДЛОЖЕНИЯ, где вместе
    слово про сон И число-измерение (часы/мин/%/score/время отбоя). Честную фразу без
    чисел («кольцо не синхронизировало ночь») НЕ трогает. Работает ТОЛЬКО при
    sleep_present=False (есть данные → числа легитимны). Абзацы (\\n\\n) сохраняем."""
    if sleep_present or not report:
        return report, []
    import re
    term = re.compile(r"(сон|сна|сне|сном|спал|спать|спит|сплю|спали|снул|глубок|REM|"
                      r"фаз[аыуе]|засып|просып|выспал|высып|недосып|бодрствова|пробужд"
                      r"|\b(?:sleep|sleeping|slept|asleep|deep|bedtime|wake|wakes|waking|woke|"
                      r"awake|awakening|awakenings)\b)", re.I)
    num = re.compile(
        r"\d+([.,]\d+)?\s*(час(а|ов)?|ч\b|h\b|мин(ут[аыу]?)?|м\b|%|процент(а|ов)?|"
        r"балл(а|ов)?|/\s*100|\b(?:hours?|hrs?|minutes?|mins?|points?|percent)\b)"
        r"|\b\d{1,2}:\d{2}\b", re.I)
    removed: list = []
    out_paras = []
    for para in report.split("\n\n"):
        kept = []
        for s in re.split(r'(?<=[.!?])\s+', para):
            if term.search(s) and num.search(s):
                removed.append(s.strip()[:80])
            else:
                kept.append(s)
        if kept:
            out_paras.append(" ".join(kept).strip())
    return "\n\n".join(out_paras).strip(), removed


def _data_header(sleep_date: date, activity_date: date) -> str:
    """Первая строка контекста GP. Дни недели названы ЯВНО: модель, получая только ISO-дату,
    считает день недели сама и ошибается (2026-09-06, воскресенье → «ночь с воскресенья на
    понедельник»). Метка ночи — из _fmt_helpers.fmt_night_ru, единственный дом."""
    from _fmt_helpers import fmt_night_ru, fmt_weekday_ru
    return (f"ДАННЫЕ: сон за {sleep_date} ({fmt_weekday_ru(sleep_date)}; ночь "
            f"{fmt_night_ru(sleep_date)}), активность/HRV/восстановление за {activity_date} "
            f"({fmt_weekday_ru(activity_date)})\n")


# Какие полосы говорят «прошло». Только состояние тела: изменение показателя и
# полоса безопасности. Гипотеза «закрывается» по другой причине (это не выздоровление),
# предложения (еда/тропа/календарь) гасит brief_state (Э3), погода — событие, а не
# состояние человека. Структура канала, не клиническая величина (§9 класс 3).
_RESOLVED_PROVIDERS = ("drift", "safety_net")
# И только ежедневный замер (origin internal): лаб-карточка исчезает, когда меняется норма
# или тренд, а не когда человек выздоровел — новый анализ не сдан (brief_cards.from_safety).
_RESOLVED_ORIGINS = ("internal",)


def _resolved_context_lines(decisions: list, verdict_of=None) -> list[str]:
    """Блок «прошло» для модели — ЧИСТАЯ функция от решений гейта. Берёт только
    показанные отбои (status shown, state resolved) полос _RESOLVED_PROVIDERS с
    ежедневного замера; текст — напоминание из brief_state.resolved_reminder (человек не
    обязан помнить начало).

    Гипотезы (решение владельца 28.09 «надо»): отдельной строкой «гипотеза закрыта» — ТОЛЬКО
    при настоящем вердикте. verdict_of(memory_id) → 'confirmed' | 'rejected' | None
    (hai_hypotheses.hypothesis_verdict); None = гипотезу вытеснило окно «последние N», это
    не новость, строки нет. Без verdict_of гипотез в блоке нет вовсе (fail-closed)."""
    shown = [d for d in decisions or []
             if d.get("status") == "shown" and d.get("state") == "resolved"]
    items = [d["evidence"] for d in shown
             if d.get("provider") in _RESOLVED_PROVIDERS
             and d.get("origin") in _RESOLVED_ORIGINS and d.get("evidence")]
    hyps = []
    for d in shown:
        if d.get("provider") != "hypotheses" or verdict_of is None or not d.get("was"):
            continue
        try:
            v = verdict_of(int(str(d["semantic_key"]).split(":", 1)[1]))
        except (ValueError, IndexError):
            continue
        if v in _VERDICT_RU:
            when = f", человеку её показывали {d['shown_on'][8:10]}.{d['shown_on'][5:7]}" \
                if d.get("shown_on") else ""
            hyps.append(f"«{d['was']}» — {_VERDICT_RU[v]}{when}")
    out = []
    if items:
        out += (["ПРОШЛО (об этом человеку говорили раньше, он может не помнить — одной фразой "
                 "напомни, что было и с какого числа, и скажи, что вернулось к обычному):"]
                + [f"  - {x}" for x in items] + [""])
    if hyps:
        out += (["ГИПОТЕЗА ЗАКРЫТА (человек может не помнить её — одной фразой напомни, о чём "
                 "она была, и скажи итог):"] + [f"  - {x}" for x in hyps] + [""])
    return out


_VERDICT_RU = {"confirmed": "подтвердилась данными", "rejected": "не подтвердилась данными"}


def _ensure_shown_extras(report: str, cards: list, shown: set) -> str:
    """Гарантия доставки «мягких» карт (еда/тропа/море): LLM их стабильно роняет из
    многострочного блока даже под жёсткой инструкцией. Если гейт показал карту, а её
    субъекта нет в тексте — добавляем ЕСТЕСТВЕННЫЙ абзац. Единый стиль (владелец 2026-07-14):
    «сейчас», без пришитого «Сегодня:». Безвредно, когда LLM всё же вплёл (уже в тексте)."""
    import re
    low = report.lower()
    adds: list[str] = []
    for c in cards:
        if c.semantic_key not in shown:
            continue
        prov = getattr(c, "provider", "")
        ev = (c.evidence_summary or "").strip()
        if prov == "food":
            m = re.search(r"полезно:\s*(.+?)\s*[—-]\s*(.+)", ev)
            if (m and m.group(1).strip().lower() not in low
                    and not re.search(r"\b" + re.escape(i18n.t("gp.brief.seasonal", lang="en")
                                      .split("{food}", 1)[0].strip(" —:")) + r"\b", low, re.I)):
                adds.append(i18n.t("gp.brief.seasonal", food=m.group(1).strip(), detail=m.group(2).strip()))
        elif prov == "trail":
            m = re.search(r"тропа:\s*(.+)", ev)
            if m:
                name = m.group(1).strip()
                key = re.split(r"[\s(—-]", name, 1)[0].lower()
                # Модель транслитерирует название («<тропа>») — латинский
                # ключ не находится, тропа едет второй раз. Тропа в контексте одна, поэтому любое
                # упоминание тропы/трейла в тексте = субъект доставлен.
                mentioned = ((key and key in low) or "троп" in low or "трейл" in low
                             or re.search(r"\b" + re.escape(i18n.t("gp.brief.trail", lang="en")
                                          .split("{name}", 1)[1].strip(" .")) + r"\b", low, re.I))
                if not mentioned:
                    adds.append(i18n.t("gp.brief.trail", name=name))
        elif str(c.semantic_key).startswith("sea:"):
            if "море" not in low and not re.search(r"\b(?:sea|seaside|ocean)\b", low, re.I) and ev:
                adds.append(ev[:1].upper() + ev[1:] + ("" if ev.endswith(".") else "."))
    if not adds:
        return report
    return report + "\n\n" + "\n\n".join(adds)


# ── Реестр каналов утреннего брифа (нить brief-repeat, 2026-08-04) ─────────────
# КАЖДЫЙ источник, дописывающий в `context_lines`, обязан быть назван здесь и назвать
# свой подавитель повтора. Ключи сверяются с кодом ОБХОДОМ AST в обе стороны
# (tests/consistency/test_brief_channel_registry.py) — незаявленный канал краснеет,
# осиротевшая запись тоже.
#
# Зачем: анти-повтор строился ад-хок, по одному каналу за раз, когда конкретный начинал
# болеть. Локация получила watermark, «кольцо снято» — streak, гены — скраб и валидатор,
# заметки из чата не получили ничего и повторялись трое суток. Реестр не мешает добавить
# канал без подавителя — он мешает добавить его НЕ ЗАМЕТИВ, что подавителя нет.
#
# Замер 2026-08-04: каналов 19. Под карточным гейтом 7, со своим подавителем 3,
# без подавителя 9 — и это названо, а не спрятано.
#
# Маршрут добавления канала и выбор подавителя: docs/how-to/add_brief_channel.md
# Почему подавитель стоит ПОСЛЕ модели, а не до: docs/explanation/brief_repeat_boundary.md
BRIEF_CHANNELS = {
    "location":           "свой: watermark в location_signal, двигается после доставки (RYW)",
    "chat_notes":         "свой: провенанс в patient_context.brief_notes — сказанное владельцем "
                          "не пересказывается никогда; квитанция arbiter_unverified ровно одна",
    "observation_window": "нет: инструкция модели о разрежённости данных, в текст брифа не идёт",
    "safety_net":         "карточный гейт: фильтр по _shown, полоса safety",
    "lifestyle_agents":   "частичный: под гейтом только сон (gate_sleep_brief); остальные агенты "
                          "идут как есть — известная дыра, самый объёмный канал брифа",
    "missing_agents":     "нет: метаинформация для модели («агенты молчат»), не содержание",
    "sleep_absent":       "свой: streak подряд идущих ночей без Oura + порог N из конфига",
    "workouts":           "нет: факты вчерашнего дня, по построению меняются ежедневно",
    "step_target":        "нет: цель дня, пересчитывается от активности",
    "evening_checkin":    "нет: привязан к дате чекина, повториться не может",
    "genome":             "карточный гейт: only_genes из _shown + скраб + brief_validator",
    "beliefs_header":     "нет: авторитетная текущая вера (локация/travel); пересекается с "
                          "каналом location — оба могут назвать город в одно утро",
    "patient_profile":    "нет: стоячие факты образа жизни; держится инструкцией «не задавай "
                          "вопросы об этом», механизма нет",
    "recovery_index":     "нет: число пересчитывается за скользящее окно",
    "drift":              "карточный гейт: фильтр по _shown",
    "hypotheses":         "карточный гейт: фильтр по _shown",
    "resolved":           "карточный гейт: только показанный в эпизоде отбой "
                          "(brief_state.episode_shown); ключ уходит в resolved — второй раз не едет",
    "env":                "карточный гейт: _env_context_lines по _cards/_shown",
    "plan":               "карточный гейт: слот plan, фильтр по _shown",
    "food":               "карточный гейт: слот food, фильтр по _shown",
}


# Поля — из реестра methodology/profile_fields.yaml (раздел routine). До 26.09 здесь
# стояли ключи памяти чата верхнего уровня (alcohol_consumption…), которых в профиле не
# бывает: блок «известные факты — не задавай вопросы» в отчёте GP всегда был пуст.
_LIFESTYLE_FIELDS = [
    ("alcohol",        "Алкоголь"),
    ("smoking",        "Курение"),
    ("caffeine",       "Кофеин"),
    ("melatonin",      "Мелатонин"),
    ("bedroom_temp_c", "Температура в спальне, °C"),
]


def _known_lifestyle_facts(profile: dict) -> list[str]:
    routine = (profile or {}).get("routine", {}) or {}
    return [f"  {label}: {routine[key]}" for key, label in _LIFESTYLE_FIELDS
            if routine.get(key) not in (None, "", [], {})]


def generate_daily_report(target: date = None, gate_sink: dict = None) -> str:
    """
    Ежедневный утренний отчёт: lifestyle-агенты → GP синтез.
    Заменяет health_ai.generate_morning_report().
    """
    import health_db as db  # нужен для профиля и триажа

    if target is None:
        target = get_today()

    # Сон — за сегодня (Oura пишет на дату пробуждения)
    # Активность/HRV/readiness — за вчера (данные за текущий день ещё не полные)
    sleep_date    = target
    activity_date = target - timedelta(days=1)

    import lifestyle_agents as la
    briefs = la.run_lifestyle_agents(sleep_date, activity_date)

    if not briefs:
        log.info(f"Daily report: нет данных от lifestyle-агентов за {target}")
        return i18n.t("gp.report.no_sensor_data", date=target), None

    # ── Анти-повтор гейт (флаг MORNING_BRIEF_GATE) ───────────────────────
    # Флаг OFF → _shown=None → все гейты ниже no-op (бриф ровно как раньше).
    _shown = None
    _decs = []
    import os as _osg
    if _osg.environ.get("MORNING_BRIEF_GATE") == "1":
        try:
            import brief_pipeline as _bp, brief_state as _bs
            _cards = _bp.assemble_cards(target)
            with db.get_conn() as _cc:
                _decs, _specs = _bs.plan(_cards, target, _cc)
            _shown = {d["semantic_key"] for d in _decs if d["status"] == "shown"}
            if gate_sink is not None:
                gate_sink["specs"] = _specs  # commit ПОСЛЕ доставки (send_morning_report)
            if "lifestyle_sleep" in briefs:
                briefs = dict(briefs)
                briefs["lifestyle_sleep"] = _bp.gate_sleep_brief(
                    briefs["lifestyle_sleep"], "sleep:deep:below_band" in _shown)
        except Exception as _ge:
            log.warning(f"MORNING_BRIEF_GATE упал → ungated fallback: {_ge}")
            _shown = None

    # Собираем контекст для GP
    import lifestyle_agents as _la
    all_agent_types = {a.agent_type: a.agent_name for a in _la.AGENTS}
    missing = [name for atype, name in all_agent_types.items() if atype not in briefs]

    # ── Safety net (детерминированный, до LLM) ───────────────────────────
    import safety_net as sn
    sn_result = sn.run_safety_net(sleep_date)

    context_lines = [_data_header(sleep_date, activity_date)]

    # Событие смены города (A, 2026-07-15) — отдельно, ВЫШЕ рутины (решение владельца).
    # Геометрия дома первична; событие раз на переход через watermark; watermark двигаем
    # ТОЛЬКО после доставки (RYW) — складываем в gate_sink, коммит в send_morning_report.
    # channel: location
    try:
        import location_signal as _lsig
        _lev = _lsig.location_event(_lsig.location_state(_lsig.resolve_place()),
                                    _lsig.get_location_watermark())
        if _lev:
            context_lines.append(f"СОБЫТИЕ (локация): {_lev['text']}")
            context_lines.append("")
            if gate_sink is not None:
                gate_sink["location_watermark"] = _lev["new_watermark"]
    except Exception as _le:
        log.warning("location event провайдер упал (не блокируем): %r", _le)

    # Свежие заметки из чата (2026-07-05): отчёт обязан учитывать поправки пользователя
    # о качестве данных («Oura ошиблась, ночь не посчиталась»), а не брать датчики вслепую.
    # 2026-08-04 (нить brief-repeat): заметки едут как ФОН, а не как материал отчёта.
    # Раньше здесь стоял recent_notes() с заголовком «Текущий контекст … УЧИТЫВАЙ» — и
    # новость, которую владелец сообщил САМ, трое суток подряд открывала бриф. Разделение
    # по провенансу — в patient_context.brief_notes; квитанция отмечается ПОСЛЕ доставки.
    import patient_context as _pc
    _chat_notes, _note_receipts = _pc.brief_notes()
    # channel: chat_notes
    if _chat_notes:
        context_lines.append(_chat_notes)
        context_lines.append("")
    if _note_receipts and gate_sink is not None:
        gate_sink["note_receipts"] = _note_receipts

    # Гейт разрежённого профиля (2026-07-03): не давать LLM выдумывать многодневные
    # тренды, когда данных наблюдения мало. n_days = дни с непустым сном за 30д.
    _n_obs = (db.get_stats(30, sleep_date) or {}).get("n_days", 0) or 0
    _obs_note = _observation_window_note(_n_obs)
    # channel: observation_window
    if _obs_note:
        context_lines.append(_obs_note)
        context_lines.append("")

    _ws = sn_result["warn_summary"]
    if _shown is not None and _ws:
        _al = [a for a in (sn_result.get("alerts") or [])
               if f"safety:{str(a.get('metric','?')).lower()}:{str(a.get('direction','?')).lower()}" in _shown]
        _ws = ("SAFETY NET:\n" + "\n".join(f"  🔶 {a['metric']}: {a.get('note','')}" for a in _al)) if _al else ""
    # channel: safety_net
    if _ws:
        context_lines.append(_ws)
        context_lines.append("")
    # channel: lifestyle_agents
    for agent_type, brief in briefs.items():
        context_lines.append(brief)
        context_lines.append("")

    # channel: missing_agents
    if missing:
        context_lines.append(f"НЕТ ДАННЫХ (агенты молчат): {', '.join(missing)}")
        context_lines.append("")

    # Сон отсутствует (Oura не синхронизировал ночь) — честный нарратив без выдумки чисел
    # (B, 2026-07-15). Явная инструкция + детерминированный backstop _scrub_fabricated_sleep.
    # channel: sleep_absent
    if "lifestyle_sleep" not in briefs:
        _absent_n = SLEEP_ABSENT_EVENT_N_DEFAULT
        _streak = 0
        try:
            import config_db as _cfg
            _absent_n = int(_cfg.get_config("brief.sleep_absent_event_nights", _absent_n))
        except Exception:  # silent-ok: конфиг недоступен → дефолт N=2
            pass
        try:
            with db.get_conn() as _sc:
                _streak = _sleep_absent_streak(_sc, sleep_date)
        except Exception as _stk:
            log.warning("sleep-absent streak расчёт упал: %r", _stk)
        if _streak >= _absent_n:
            # N ночей подряд без кольца → эскалация в СОБЫТИЕ «надень кольцо» (data-freshness).
            context_lines.append(
                f"СОБЫТИЕ (кольцо): Oura не присылает сон уже {_streak} ночей подряд — похоже, "
                "кольцо снято или разряжено. Скажи мягко и по-человечески, что стоит надеть/"
                "зарядить кольцо. Числа сна НЕ выдумывай.")
        else:
            # Причина отсутствия данных здесь неизвестна: задержка доставки,
            # снятый прибор и сбой выгрузки неразличимы по одной пропущенной ночи.
            # Называть причину как факт нельзя. Ветка выше использует серию пропусков
            # как основание для осторожной гипотезы («похоже»), а не установленного факта.
            context_lines.append(
                "СОН СЕГОДНЯ: данных о ночи нет. ПРИЧИНУ НЕ НАЗЫВАЙ — она неизвестна. НЕ называй "
                "никакие числа сна (часы/глубокий/REM/score/время отбоя) — их нет. Скажи честно "
                "одной фразой, что ночь не посчиталась; если есть readiness/восстановление — "
                "можно коротко опереться на них, не выдумывая сам сон.")
        context_lines.append("")

    # ── Тренировки за день (activity_date) ───────────────────────────────
    # F-093 fix (2026-05-22): db.get_conn() вместо inline sqlite3.connect (только SELECT).
    # channel: workouts
    try:
        with db.get_conn() as _wcon:
            _wkts = _wcon.execute(
                "SELECT activity_type, duration_min, distance_km, calories, avg_hr "
                "FROM workouts WHERE date=? ORDER BY start_time",
                (str(activity_date),)
            ).fetchall()
        if _wkts:
            context_lines.append("ТРЕНИРОВКИ:")
            for _w in _wkts:
                _dur = _w["duration_min"] or 0
                _wl = f"  {_w['activity_type'] or '?'}: {_dur:.0f}м"
                if _w["distance_km"]:
                    _wl += f"  {_w['distance_km']:.1f}км"
                if _w["calories"]:
                    _wl += f"  {_w['calories']:.0f}ккал"
                if _w["avg_hr"]:
                    _wl += f"  HR {_w['avg_hr']:.0f}bpm"
                context_lines.append(_wl)
            context_lines.append("")
    except Exception as _we:
        log.debug(f"Workouts block daily: {_we}")

    # ── Цель шагов на день (детерминированная) ───────────────────────────
    step_target = compute_step_target(activity_date)
    # channel: step_target
    if step_target:
        context_lines.append(step_target)
        context_lines.append("")

    # Добавляем вечерний чекин из context_events
    # Чекин записан на дату вечера = target - 1 день (вечер предыдущего дня)
    checkin_date = target - timedelta(days=1)
    # channel: evening_checkin
    try:
        events = db.get_context_events(str(checkin_date), source='checkin')
        raw    = db.get_checkin_by_date(str(checkin_date), time_of_day='evening')
        if events or raw:
            context_lines.append("ВЕЧЕРНИЙ ЧЕКИН:")
            for evt in events:
                v = evt.get('value_text') or (
                    str(int(evt['value_num'])) if evt.get('value_num') is not None else None
                )
                if v:
                    context_lines.append(f"  {evt['key']}: {v}")
            if raw and raw.get('answer'):
                context_lines.append(f"  raw: {raw['answer'][:200]}")
            context_lines.append("")
    except Exception as e:
        log.warning(f"Checkin fetch failed: {e}")

    # Lifestyle-геном: под гейтом кормим ТОЛЬКО гены, одобренные гейтом ПЕРСОНАЛЬНО
    # (анти-повтор ≤1/мес на ген). НЕ весь домен — иначе подавленные и PINNED-гены
    # протекают в текст (класс BRIEF_LEAK). НЕ
    # привязываем к находке sleep:deep — ген называем, только если его карта прошла гейт.
    # channel: genome
    try:
        import genome_context as gc
        if _shown is None:
            for domain in ["sleep", "energy", "stress", "movement"]:
                block = gc.build_lifestyle_genome_block(domain, max_variants=6)
                if block:
                    context_lines.append(block)
                    context_lines.append("")
        else:
            _approved: dict = {}
            for k in _shown:
                if k.startswith("genome:"):
                    _p = k.split(":", 2)
                    if len(_p) == 3:
                        _approved.setdefault(_p[1], set()).add(_p[2])
            for _domain, _genes in _approved.items():
                block = gc.build_lifestyle_genome_block(
                    _domain, max_variants=6, only_genes=_genes)
                if block:
                    context_lines.append(block)
                    context_lines.append("")
    except Exception as e:
        log.warning(f"Геном для daily report: {e}")

    # ── Авторитетная ТЕКУЩАЯ ВЕРА (локация/travel) — важнее сырого профиля/фактов ──
    # channel: beliefs_header
    try:
        import beliefs as _bel
        _bh = _bel.render_header()
        if _bh:
            context_lines.append(_bh)
            context_lines.append("")
    except Exception:  # silent-ok: вера недоступна → отчёт без заголовка, не падаем
        pass

    # ── Инжект профиля пациента (образ жизни, известные факты) ──────────
    # channel: patient_profile
    try:
        profile_facts = _known_lifestyle_facts(db.get_profile_context())
        if profile_facts:
            context_lines.append("ПРОФИЛЬ ПАЦИЕНТА (известные факты — не задавай вопросы об этом):")
            context_lines.extend(profile_facts)
            context_lines.append("")
    except Exception as e:
        log.warning(f"Профиль пациента для daily report: {e}")

    # ── Индекс восстановления (vs baseline до болезни) ────────
    # channel: recovery_index
    try:
        import health_ai as hai
        ri = hai.compute_recovery_index(target=sleep_date, window_days=7)
        ri_line = hai.format_recovery_index(ri)
        if ri_line:
            context_lines.append(ri_line)
            context_lines.append("")
    except Exception as e:
        log.warning(f"Recovery index: {e}")

    # ── Drift detection + авто-гипотезы ──────────────────────────────────
    # channel: drift
    try:
        import health_ai as hai

        drifts = hai.detect_metric_drift(target=sleep_date)
        if _shown is not None:
            drifts = [d for d in (drifts or [])
                      if f"drift:{str(d.get('metric','?')).lower()}:{str(d.get('direction','?')).lower()}" in _shown]
        drift_text = hai.format_drift_report(drifts)
        if drift_text:
            context_lines.append(drift_text)
            context_lines.append("")

        # Авто-генерация гипотез из дрейфа (фоново, не блокирует отчёт)
        if drifts:
            # Ищем linked experiment
            try:
                _exps = db.get_active_experiments()
                _exp_id = _exps[-1]["id"] if _exps else None
            except Exception:
                _exp_id = None
            hai.generate_hypothesis_from_drift(drifts, linked_experiment_id=_exp_id)
    except Exception as e:
        log.warning(f"Drift detection/hypotheses: {e}")

    # ── Активные гипотезы → контекст GP ──────────────────────────────────
    # channel: hypotheses
    try:
        import health_ai as hai
        hyps = hai.get_open_hypotheses(n=5)
        if _shown is not None:
            hyps = [h for h in (hyps or []) if f"hypothesis:{h.get('memory_id')}" in _shown]
        hyp_text = hai.format_hypotheses_for_gp(hyps)
        if hyp_text:
            context_lines.append(hyp_text)
            context_lines.append("")
    except Exception as e:
        log.warning(f"Hypotheses context: {e}")

    # «Прошло» (нить brief-resolved-shown, решение владельца 28.09): до этой правки отбой
    # тревоги не доходил до модели НИКОГДА — блоки выше собираются из сегодняшних находок,
    # а разрешившейся сегодня нет. Теперь — только полосы состояния тела и только отбой
    # того, что человеку показали в этом эпизоде (brief_state.episode_shown), с напоминанием.
    try:
        import hai_hypotheses as _hh
        _verdict_of = _hh.hypothesis_verdict
    except Exception as _he:  # silent-ok-ish: без вердиктов гипотез в блоке нет (fail-closed)
        log.warning("hypothesis_verdict недоступен — закрытые гипотезы в бриф не едут: %r", _he)
        _verdict_of = None
    # channel: resolved
    context_lines.extend(_resolved_context_lines(_decs if _shown is not None else [],
                                                 verdict_of=_verdict_of))

    # Среда (гейт, C1 2026-07-15): показанные находки → блок; в поездке при чистой среде
    # → «проверено, в норме»; дома чистая среда / нет локации / фетч упал → молчим.
    # Логика вынесена в чистую _env_context_lines (тестируема отдельно от рендера).
    # channel: env
    if _shown is not None:
        try:
            import env_context as _ec
            _env_probe = _ec.env_probe()
        except Exception:  # silent-ok: зонд опционален; при сбое ведём себя как «нет данных»
            _env_probe = None
        context_lines.extend(_env_context_lines(_cards, _shown, _env_probe))

    # Календарь + трейлы (гейт, слот plan): поездка «за день до» ИЛИ тропа на выходной
    # channel: plan
    if _shown is not None:
        _plan_shown = [c for c in _cards if getattr(c, "provider", "") in ("calendar", "trail")
                       and c.semantic_key in _shown]
        if _plan_shown:
            context_lines.append("ПЛАН:")
            for _c in _plan_shown:
                context_lines.append("  " + _c.evidence_summary)
            context_lines.append("")

    # Сезон×геном (гейт): полезный сезонный продукт — второй этап
    # channel: food
    if _shown is not None:
        _food_shown = [c for c in _cards if getattr(c, "provider", "") == "food"
                       and c.semantic_key in _shown]
        if _food_shown:
            context_lines.append("ЕДА:")
            for _c in _food_shown:
                context_lines.append("  " + _c.evidence_summary)
            context_lines.append("")

    user_content = "\n".join(context_lines)

    # Геном-рамка в промпте только когда реально одобрен геном-ген (карта genome:*).
    # sleep:deep:below_band (находка) больше НЕ открывает геном-рамку — ген называем
    # лишь при прошедшей гейт геном-карте, иначе ген сна ехал каждую плохую ночь.
    _genome_in_scope = (_shown is None) or any(
        k.startswith("genome:") for k in _shown)

    client = _get_client()
    response = client.messages.create(task="gp_agent.generate_daily_report",
        model=hai_core.get_model("sonnet"),
        max_tokens=1500,   # было 700 — обрезало при насыщенном контексте
        system=_build_gp_daily_prompt(genome_in_scope=_genome_in_scope),
        messages=[{"role": "user", "content": user_content}]
    )
    report = llm_client.answer_text(response).strip()
    log.info(f"GP daily отчёт сгенерирован за {target} ({len(briefs)} агентов)")

    # ── Хирургический скраб генов (детерминированная гарантия) ──────────────
    # Промпт-запрет не держит против досочинения → удаляем предложения с генами/rs,
    # не одобренными гейтом. Только под гейтом (без него ничего не подавлено).
    # C3 (2026-07-15): скраб генов теперь БЕЗУСЛОВНЫЙ. Раньше `if _shown is not None`
    # значило «нет гейта → ничего не подавлено» — но fallback-ветка (_shown=None) кормит LLM
    # ВЕСЬ доменный геном (вкл. подавленный) и при этом пропускала скраб → утечка. None →
    # пустой approved → подавляем все гены (безопасный дефолт).
    _scrubbed: list = []
    try:
        report, _scrubbed = _scrub_unapproved_genes(report, _shown if _shown is not None else set())
        if _scrubbed:
            log.warning("GENE_SCRUB за %s: удалены предложения с неодобренными "
                        "генами/rs: %s", target, _scrubbed)
    except Exception as _se:
        log.warning("gene scrub упал (не блокируем доставку): %r", _se)

    # ── Анти-конфаб сна (B2, 2026-07-15): нет записи Oura → не выдумывай числа сна ──
    _sleep_present = "lifestyle_sleep" in briefs
    try:
        report, _sleep_conf = _scrub_fabricated_sleep(report, _sleep_present)
        if _sleep_conf:
            log.warning("SLEEP_CONFAB за %s: удалены выдуманные числа сна при пустом Oura: %s",
                        target, _sleep_conf)
    except Exception as _sce:
        log.warning("sleep-confab scrub упал (не блокируем): %r", _sce)

    if _shown is not None:
        # Гарантия доставки мягких карт (еда/тропа/море) — LLM их роняет.
        try:
            report = _ensure_shown_extras(report, _cards, _shown)
        except Exception as _fe:
            log.warning("ensure_shown_extras упал (не блокируем): %r", _fe)

    # Нормализация абзацев: схлопываем 3+ переводов строки в ровно один пустой абзац
    # (убирает лишние пустые строки; блоки уже разделены \n\n промптом+нетками).
    import re as _re_norm
    report = _re_norm.sub(r"\n{3,}", "\n\n", report).strip()

    # ── Пост-рендер сторож: ТЕПЕРЬ ДЕЙСТВУЕТ, а не только логирует (C3, 2026-07-15) ──
    # Инцидент 2026-07-15: скраб выше промолчал (ген вне одобренных у второго тенанта), валидатор поймал, но было
    # log-only → утекло. Валидатор ловит по КАРТАМ (_cards), не по _gene_universe(), поэтому
    # его находка надёжна независимо от того, почему universe-скраб промолчал. Находка →
    # ДЕТЕРМИНИРОВАННЫЙ вырез предложения (backstop). Diag-лог shown-геном+скраб — чтобы на
    # след. повторе пробить, почему первичный скраб молчал (статически не разрешилось).
    if _shown is not None:
        try:
            log.info("BRIEF_GATE diag за %s: shown_genome=%s scrubbed=%s", target,
                     sorted(k for k in _shown if k.startswith("genome:")), _scrubbed)
            _v = _validate_brief(report, _cards, _shown)
            if not _v["ok"]:
                _leaked = {str(l.get("gene", "")).upper() for l in _v["leaks"] if l.get("gene")}
                if _leaked:
                    report, _restr = _strip_leaked_genes(report, _leaked)
                    report = _re_norm.sub(r"\n{3,}", "\n\n", report).strip()
                    log.error("BRIEF_LEAK за %s пойман сторожем и ВЫРЕЗАН: leaks=%s "
                              "forbidden=%s restripped=%s", target, _v["leaks"],
                              _v["forbidden_hits"], _restr)
                else:
                    log.error("BRIEF_LEAK за %s: forbidden=%s (не гены — авто-вырез не делаю)",
                              target, _v["forbidden_hits"])
                _v2 = _validate_brief(report, _cards, _shown)
                if not _v2["ok"]:
                    log.error("BRIEF_LEAK за %s ОСТАЛСЯ после выреза (dev-баг): %s",
                              target, _v2["leaks"])
        except Exception as _ve:
            log.warning("brief_validator упал (не блокируем доставку): %r", _ve)

    # Автотриаж метрик → задачи человеку снят 28.09 (см. комментарий у compute_step_target).

    return report, sn_result


# ── Experiment check runner ───────────────────────────────────────────────────

def run_experiment_checks(target: date = None) -> list[str]:
    """
    Проверяет активные эксперименты: если сегодня = start_date + check_day[N],
    вычисляет текущие метрики vs baseline, сохраняет в check_results,
    возвращает список текстовых отчётов для Telegram.

    Вызывается ежедневно из send_morning_report.
    """
    if target is None:
        target = get_today()

    messages = []

    try:
        import json as _json
        import health_ai as hai
        import health_db as db
        from datetime import date as _date

        exps = db.get_active_experiments()

        for exp in exps:
            exp_id             = exp["id"]
            name               = exp["name"]
            start_str          = exp["start_date"]
            baseline_json      = exp.get("baseline_metrics")
            check_days_json    = exp.get("check_days")
            check_results_json = exp.get("check_results")
            if not start_str or not check_days_json:
                continue

            start   = _date.fromisoformat(start_str)
            days_elapsed = (target - start).days
            check_days   = _json.loads(check_days_json) if check_days_json else []
            check_results = _json.loads(check_results_json) if check_results_json else {}
            baseline = _json.loads(baseline_json) if baseline_json else {}

            # Ищем: сегодня == start + check_day?
            for cd in check_days:
                key = str(cd)
                if days_elapsed != cd:
                    continue
                if key in check_results:
                    # Уже посчитано (например, ретроактивно)
                    log.info(f"Experiment {name}: Day-{cd} уже записан, пропускаю")
                    continue

                log.info(f"Experiment {name}: Day-{cd} check (elapsed={days_elapsed}д)")

                # Метрики за последние 7 дней
                ri = hai.compute_recovery_index(target=target, window_days=7)
                curr = ri.get("metrics", {})

                # Считаем дельту к EXPERIMENT baseline (не к pre-illness baseline)
                metric_keys = {
                    "deep_min":  ("deep_min",  baseline.get("deep_min")),
                    "rem_min":   ("rem_min",   baseline.get("rem_min")),
                    "hrv_ms":    ("hrv_ms",    baseline.get("hrv_ms")),
                    "readiness": ("readiness", baseline.get("readiness")),
                    "steps":     ("steps",     baseline.get("steps")),
                }
                check_entry = {
                    "date":   str(target),
                    "n_days": ri.get("n_days", 0),
                    "period": f"{target - timedelta(days=7)} → {target}",
                    "metrics": {},
                }
                for mkey, (ri_key, bl_val) in metric_keys.items():
                    if not bl_val or ri_key not in curr:
                        continue
                    cur_val = curr[ri_key]["current"]
                    delta   = cur_val - bl_val
                    delta_pct = round(delta / bl_val * 100, 0) if bl_val else 0
                    check_entry["metrics"][mkey] = {
                        "current":    cur_val,
                        "baseline":   bl_val,
                        "delta":      round(delta, 1),
                        "delta_pct":  int(delta_pct),
                    }

                check_results[key] = check_entry

                # Сохраняем
                db.update_experiment_check_results(
                    exp_id, _json.dumps(check_results, ensure_ascii=False)
                )

                # Формируем Telegram-сообщение
                m = check_entry["metrics"]
                lines = [i18n.t("experiments.reply.check_heading", day=cd, name=name), ""]

                def fmt_row(label, mkey):
                    if mkey not in m:
                        return None
                    d = m[mkey]
                    sign = "+" if d["delta_pct"] >= 0 else ""
                    return i18n.t("experiments.reply.metric", label=label, current=d["current"],
                                 baseline=d["baseline"], sign=sign, delta_pct=d["delta_pct"])

                for mkey in ("deep_min", "rem_min", "hrv_ms", "readiness", "steps"):
                    row = fmt_row(i18n.t(f"experiments.metric.{mkey}"), mkey)
                    if row:
                        lines.append(row)

                # Краткий вывод GP через Claude
                try:
                    summary_prompt = (
                        f"Эксперимент '{name}', Day {cd}.\n"
                        f"Данные (7д avg vs baseline):\n"
                        + "\n".join(f"  {l}" for l in lines[2:]) +
                        "\n\nДай очень краткий (2-3 предложения) вывод: что изменилось, "
                        "стоит ли продолжать эксперимент, на что обратить внимание к следующему чеку. "
                        "Без markdown, без emoji, обычный текст."
                    )
                    client = _get_client()
                    resp = client.messages.create(task="gp_agent.run_experiment_checks",
                        model=hai_core.get_model("haiku_pinned"),
                        max_tokens=200,
                        messages=[{"role": "user", "content": summary_prompt}]
                    )
                    lines.append("")
                    lines.append(llm_client.answer_text(resp).strip())
                except Exception as e:
                    log.warning(f"GP summary для experiment check: {e}")

                messages.append("\n".join(lines))
                log.info(f"Experiment check Day-{cd} готов: {name}")

                # Если это последний check_day — генерируем attribution report
                if cd == max(check_days):
                    log.info(f"Experiment {name}: последний check (Day-{cd}), генерирую attribution report")
                    try:
                        attribution = generate_attribution_report(exp_id)
                        messages.append(attribution)
                    except Exception as e:
                        log.error(f"Attribution report для {name}: {e}")

    except Exception as e:
        log.error(f"run_experiment_checks: {e}", exc_info=True)

    return messages


def generate_attribution_report(experiment_id: int) -> str:
    """
    Финальный attribution report по завершении эксперимента.
    Почему таблицы protocols/experiments почти пусты и что с этим конвейером
    случилось: docs/explanation/old_hypothesis_pipeline.md.
    Вызывается автоматически из run_experiment_checks() в последний check_day,
    или вручную через /experiment_close <id>.

    Структура отчёта:
      - Что изменилось (метрики Day0 → Day7 → Day14 → Day30)
      - Что из гипотез подтвердилось / опровергнуто
      - Рекомендация: продолжить / модифицировать / остановить
      - Следующий шаг: новая гипотеза или новый эксперимент
    """
    import json as _json
    import health_ai as hai
    import health_db as db  # BL-GP-1 fix: без импорта был NameError → тихий отказ

    exp_data = db.get_experiment_stats(experiment_id)
    if not exp_data:
        return i18n.t("experiments.error.not_found", experiment_id=experiment_id)

    exp              = exp_data["experiment"]
    name             = exp["name"]
    hypothesis       = exp["hypothesis"]
    intervention     = exp["intervention"]
    start_str        = exp["start_date"]
    baseline_json    = exp["baseline_metrics"]
    target_json      = exp["target_metrics"]
    check_days_json  = exp["check_days"]
    check_results_json = exp["check_results"]

    baseline     = _json.loads(baseline_json)     if baseline_json     else {}
    check_results = _json.loads(check_results_json) if check_results_json else {}
    check_days   = _json.loads(check_days_json)   if check_days_json   else []

    # Строим сводную таблицу метрик по чекам
    METRIC_LABELS = {
        "deep_min": "Глубокий сон (м)",
        "rem_min":  "REM (м)",
        "hrv_ms":   "ВСР (мс)",
        "readiness":"Восстановление",
        "steps":    "Шаги",
    }
    rows_table = []
    for mkey, mlabel in METRIC_LABELS.items():
        bl_val = baseline.get(mkey)
        if not bl_val:
            continue
        row_parts = [f"baseline={bl_val:.1f}"]
        for cd in sorted(check_days):
            cd_data = check_results.get(str(cd), {}).get("metrics", {}).get(mkey)
            if cd_data:
                sign = "+" if cd_data["delta_pct"] >= 0 else ""
                row_parts.append(f"Day{cd}={cd_data['current']:.1f}({sign}{cd_data['delta_pct']}%)")
            else:
                row_parts.append(f"Day{cd}=нет данных")
        rows_table.append(f"  {mlabel}: {' | '.join(row_parts)}")

    # Гипотезы по эксперименту
    hyps = hai.get_open_hypotheses(n=20)
    linked_hyps = [h for h in hyps if h.get("linked_experiment_id") == experiment_id]

    hyp_context = ""
    if linked_hyps:
        hyp_context = "\n".join(
            f"  Гипотеза: {h['observation']}\n"
            f"  Прогноз: {h['prediction']}\n"
            f"  Тест: {h['test']}"
            for h in linked_hyps
        )

    # GP синтез через Claude Sonnet
    prompt = (
        f"Эксперимент: {name}\n"
        f"Вмешательство: {intervention}\n"
        f"Исходная гипотеза: {hypothesis}\n\n"
        f"Динамика метрик:\n" + "\n".join(rows_table) + "\n\n"
        + (f"Проверявшиеся гипотезы:\n{hyp_context}\n\n" if hyp_context else "")
        + "Напиши attribution report (3-4 абзаца):\n"
        "1. Что изменилось в цифрах — честно, без приукрашивания.\n"
        "2. Подтвердилась ли исходная гипотеза (и почему).\n"
        "3. Рекомендация: продолжить / модифицировать / остановить + обоснование.\n"
        "4. Следующий вопрос или гипотеза для следующего n=1 эксперимента.\n"
        "Тон: GP говорит с пациентом — прямо, без markdown, без emoji."
    )

    client = _get_client()
    resp = client.messages.create(task="gp_agent.generate_attribution_report",
        model=hai_core.get_model("sonnet"),
        max_tokens=800,
        messages=[{"role": "user", "content": prompt}]
    )
    report_text = llm_client.answer_text(resp).strip()

    # Закрываем эксперимент в БД
    try:
        db.complete_experiment(experiment_id, result=report_text[:500])
        log.info(f"Эксперимент #{experiment_id} закрыт, attribution report сохранён")
    except Exception as e:
        log.warning(f"Не удалось закрыть эксперимент: {e}")

    return i18n.t("experiments.reply.report", name=name, report=report_text)


# ── Ре-экспорт вынесенного gp_context (поток C) — импортёры/тесты не затронуты ──
from gp_context import (  # noqa: E402,F401
    _check_lab_freshness, _build_clinical_history, _next_appointment, _get_patient_routine, _patient_header, _build_lifestyle_days_rows, _build_trends_block, _build_labs_block, _build_freshness_block, _build_med_events_block, _build_consultations_block, _build_location_header, _build_mdt_block, _build_genome_block, _build_active_periods_block, _build_lifestyle_patterns_block, _build_context_events_block, _build_longitudinal_correlations_block, _build_specialist_review_block, _build_stress_workouts_section, _format_problem_list_for_prompt, _build_gp_context,
    build_gp_context,
)

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    cmd = sys.argv[1] if len(sys.argv) > 1 else "weekly"
    if cmd == "specialists":
        run_specialists_and_save()
    elif cmd == "weekly":
        print(generate_weekly_report())
    elif cmd == "monthly":
        print(generate_monthly_report())
    else:
        print(f"Unknown command: {cmd}")
