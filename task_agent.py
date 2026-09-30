import llm_client
#!/usr/bin/env python3.11
"""
Task Agent — извлекает задачи из GP отчётов и контролирует их выполнение.

Логика:
1. После GP weekly/monthly: парсит текст, извлекает задачи (вопросы, анализы, действия)
2. Сохраняет в таблицу tasks с приоритетами и дедлайнами
3. Форматирует для Telegram: отдельным сообщением после отчёта
4. Follow-up: в воскресенье показывает незакрытые задачи старше 7 дней

Типы задач:
- question:  GP задал вопрос, ждёт ответа от пациента
- lab_test:  нужно сдать анализ
- action:    действие пациента (записаться, купить, проверить)
- followup:  GP хочет знать результат через N дней
"""

# INTENT: patient_answer_channel — канал «вопрос → ответ»: система спрашивает человека
# и слышит ответ (замысел и инварианты: project_intent patient_answer_channel).

import json
import re
import hai_core
import i18n
from _fmt_helpers import fmt_count
import logging
import sys as _sys
from datetime import date, timedelta
from pathlib import Path

_sys.path.insert(0, str(Path(__file__).parent))
from _time_inject import get_today

import anthropic

log = logging.getLogger(__name__)

# ── Промпт для извлечения задач ───────────────────────────────────────────────

TASK_EXTRACTOR_PROMPT = """Ты читаешь медицинский отчёт GP и извлекаешь конкретные задачи.

Верни ТОЛЬКО валидный JSON-массив, без markdown, без комментариев.

Формат каждой задачи:
{
  "type": "question|lab_test|action|followup",
  "priority": "critical|high|medium|low",
  "content": "конкретная формулировка задачи (для пациента, не для врача)",
  "deadline": "YYYY-MM-DD или null",
  "reason": "клиническое обоснование (1 фраза): зачем это нужно именно сейчас",
  "fingerprint": "канонический ключ для дедупликации",
  "resolution_type": "self_managed|needs_specialist"
}

Правила fingerprint:
- Должен однозначно идентифицировать суть задачи, не её формулировку
- Формат: "тип:суть" — например:
    "lab:CEA,CA19.9"       — сдать онкомаркеры
    "lab:B12,Folate"       — сдать B12 и фолат
    "lab:CBC"              — общий анализ крови
    "lab:ALT,AST,Creatinine"
    "question:event_2025-03-20"  — вопрос о конкретном событии
    "action:BP_week"       — измерить давление в течение недели
    "action:doctor_visit"  — запись к врачу
- Если два отчёта требуют одно и то же — fingerprint должен совпасть
- НЕ включай дату в fingerprint если задача не привязана к конкретному событию

Правила:
- question: если GP задал вопрос пациенту, перефразируй как задачу ("Ответить: ...")
- lab_test: если упомянуто что нужно сдать анализ
- action: любое конкретное действие
- followup: если GP хочет обратную связь через N дней
- НЕ включай абстрактные советы ("продолжать режим", "следить за самочувствием")
- НЕ включай визиты, которые уже запланированы (проверяй таблицу consultations)
- deadline: если в тексте есть "к [дата]", "через N дней", "в апреле" — вычисли дату
- resolution_type: "needs_specialist" если задача требует рецепта, назначения специалиста,
  исключения рецидива, или действия лицензированного врача; иначе "self_managed"
- Если задач нет — верни пустой массив []

Отвечай ТОЛЬКО JSON-массивом."""


def _notify_specialist(text: str):
    """Fire-and-forget Telegram при задаче needs_specialist."""
    import os, urllib.request, urllib.parse
    from pathlib import Path
    # per-tenant: секреты из единого источника
    from secrets_paths import secrets_dir
    secrets = secrets_dir()
    try:
        token   = (secrets / "telegram_token").read_text().strip()
        chat_id = (secrets / "telegram_chat_id").read_text().strip()
        url  = f"https://api.telegram.org/bot{token}/sendMessage"
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
        urllib.request.urlopen(url, data, timeout=8)
    except Exception as e:
        log.warning(f"_notify_specialist: {e}")


def _get_client():
    return llm_client.guarded_client()


PARSE_STATS_KEY = "llm_parse.stats"


def _parse_source() -> str:
    """Кто позвал разбор. СЫРОЙ факт, без классификации — судит читатель.

    Замер 13.09, ради которого это появилось: логи обоих ботов за сутки дали НОЛЬ
    поломок разбора при 12 продовых вызовах судьи, а счётчик за те же сутки — 20
    частичных и 8 полных отказов. Расходились не два механизма, а два населения:
    счётчик считал вместе бота и мои пробы на живом каноне. Датчик на таком счётчике
    краснел бы после каждой сессии разработки и научил бы листать триаж мимо ровно к
    тому дню, когда сломается настоящий разбор.

    Почему ПУТЬ, а не имя файла: путь внутри репозитория даёт читателю право спросить
    периметр («оснастка или рабочий код»), имя — нет. Классификация здесь НЕ делается
    сознательно: у неё один дом (project_context.dispgate), и второй в виде списка
    имён разъехался бы с ним молча.
    """
    import sys
    raw = sys.argv[0] or "-c"
    try:
        return str(Path(raw).resolve().relative_to(Path(__file__).resolve().parent))
    except (ValueError, OSError):
        return Path(raw).name or "-c"


def _note_parse_outcome(ok: bool, failed: bool = False) -> None:
    """Счётчик исходов разбора ответа модели за СЕГОДНЯ. Тихо, но не молча.

    Зачем вообще. 13.09 пообъектный salvage перестал терять партию из-за одного
    сломанного объекта — и на этом тихая деградация не кончилась, а стала тише:
    частичный разбор пишет log.warning, а логи никто не читает. Правило проекта на
    этот счёт прямое: на каждый тихий fallback — громкий датчик, иначе «детект есть,
    доставки нет».

    ЧТО считается провалом, а что нет. После починки частичный разбор теряет максимум
    поля одного элемента — это шум модели, не поломка системы, и краснеть на нём
    значит учить человека листать триаж мимо. Краснеть обязан ПОЛНЫЙ отказ: ноль
    объектов, исключение наружу, fail-closed, партия молчит. Поэтому счётчики
    раздельные, а порога у частичных нет — они просто ВИДНЫ числом.

    Счёт за сутки, а не накопленный: вчерашний урок про запас и поток. Смена даты
    обнуляет — так у датчика по построению не может быть монотонно растущего числа,
    которое однажды перевалит порог и будет краснеть вечно.

    ПО ИСТОЧНИКАМ, а не одним ведром (13.09, второй заход): в одном ведре смешивались
    бот и пробы разработчика на живом каноне, и датчик судил бы смесь. Разбивку кладём
    сырую — кто именно звал; «оснастка это или прод» решает читатель, у которого есть
    единственный дом периметра.

    Сам счётчик — best-effort: БД может быть недоступна (тесты, чужой процесс), и
    падать из-за статистики разбор не должен.
    """
    try:
        import config_db as cfg
        today = str(get_today())
        st = cfg.get_config(PARSE_STATS_KEY, default=None)
        if not isinstance(st, dict) or st.get("date") != today:
            st = {"date": today, "by": {}}
        # Плоские ok/partial/failed больше не пишем: два дома одного числа (итог и
        # разбивка) разъезжаются молча, и первым это заметил бы датчик, а не человек.
        st.pop("ok", None)
        st.pop("partial", None)
        st.pop("failed", None)
        by = st.setdefault("by", {})
        if not isinstance(by, dict):
            by = st["by"] = {}
        bucket = by.setdefault(_parse_source(), {"ok": 0, "partial": 0, "failed": 0})
        if failed:
            bucket["failed"] += 1
        elif ok:
            bucket["ok"] += 1
        else:
            bucket["partial"] += 1
        cfg.upsert_config(PARSE_STATS_KEY, value_json=st, category="llm",
                          source="task_agent._note_parse_outcome")
    except Exception as e:  # noqa: BLE001 — статистика не важнее разбора
        log.warning(f"_note_parse_outcome: счётчик не записан ({e})")


def _parse_tasks_json(raw: str) -> list:
    """Парсит JSON-список задач от LLM, устойчиво к обрезке и ```-обёртке.

    2026-07-05: при max_tokens ответ обрывался mid-string → json.loads падал
    'unterminated string', ВСЕ задачи терялись.
    2026-09-13: замер показал ВТОРОЙ способ сломаться — объект бьётся в СЕРЕДИНЕ,
    а массив закрывается нормально; приём «до последней }» такое не лечил. Salvage
    стал пообъектным и покрывает оба случая одним механизмом (подробности в теле).
    """
    raw = (raw or "").strip()
    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1] if len(parts) > 1 else raw
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()
    try:
        out = json.loads(raw)
        _note_parse_outcome(ok=True)
        return out
    except json.JSONDecodeError:
        # Salvage ПООБЪЕКТНО, а не «до последнего }» (2026-09-13). Прежний приём
        # лечил ОБРЫВ на конце и был бессилен против сломанного объекта В СЕРЕДИНЕ:
        # замер 10 прогонов судьи дал 2 ответа, где модель закрывала объект до поля
        # "ru", а дальше шёл осиротевший «"ru": …}». Массив при этом кончался
        # нормальным «]», обрезка по последней «}» давала тот же невалидный текст,
        # и падал ВЕСЬ разбор — то есть 20% вызовов возвращали пустоту.
        #
        # Цена этой пустоты не равна нулю и не равна «повторим завтра»: в
        # extract_tasks_from_report пустой вердикт судьи понижает ВСЕ вопросы отчёта
        # в action, и это необратимо — задача создана, вопрос не задан никогда.
        # Ровно та тишина, против которой построена вся подсистема.
        #
        # Fail-closed сохранён, но переехал с ПАРТИИ на ЭЛЕМЕНТ: неразобранный
        # объект молчит сам за себя, разобранные соседи доезжают. Ноль объектов —
        # по-прежнему исключение наружу: это уже не «одна кривая строка», а
        # «ответ не о том», и молчать о таком нельзя.
        objs, buf, depth, in_str, esc = [], [], 0, False, False
        for ch in raw:
            if depth:
                buf.append(ch)
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                if not depth:
                    buf = ["{"]
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        objs.append(json.loads("".join(buf)))
                    except json.JSONDecodeError:
                        log.warning("_parse_tasks_json: объект не разобран, пропущен: "
                                    f"{''.join(buf)[:120]}")
                elif depth < 0:          # осиротевшая «}» от сломанного соседа
                    depth = 0
        if not objs:
            _note_parse_outcome(ok=False, failed=True)
            raise
        log.warning(f"_parse_tasks_json: ответ разобран частично — спасено {len(objs)} "
                    f"объект(ов); полный разбор не удался")
        _note_parse_outcome(ok=False)
        return objs


def extract_tasks_from_report(report_text: str, source: str,
                               source_date: str = None, report_date: date = None) -> list[dict]:
    """
    Парсит GP отчёт, извлекает задачи, сохраняет в DB.
    Returns: список сохранённых задач с id.

    Public API: см. также alias `extract_tasks` ниже (для UC-G-01 unit-тестов).
    """
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    import health_db as db
    db.init_db()

    if report_date is None:
        report_date = get_today()
    if source_date is None:
        source_date = str(report_date)

    client = _get_client()
    try:
        response = client.messages.create(
            model=hai_core.get_model("haiku"),
            # 2026-07-05: было 800 → JSON задач обрезался mid-string. 2026-09-28: 2000 тоже мало —
            # замер на 21 реальном отчёте GP × 2 (42 вызова): 3 ответа упёрлись в 2000 и обрезались
            # (месячный 30.06 — в ОБОИХ прогонах, повтор не лечит), ещё два дошли до 1943 и 1976.
            # Хвост обрезанного ответа — потерянные задачи. Прогон тех же отчётов с потолком 4000:
            # 6/6 разобраны, самый длинный ответ 2730 токенов (недельный 01.08). Потолок 6000 —
            # двукратный запас к замеренному максимуму; платится только за реально выданные токены.
            max_tokens=6000,
            system=TASK_EXTRACTOR_PROMPT + hai_core.answer_language(),
            messages=[{
                "role": "user",
                "content": f"Дата отчёта: {report_date}\n\nОТЧЁТ:\n{report_text}"
            }],
        )
        if getattr(response, "stop_reason", None) == "max_tokens":
            log.warning("Task extractor: ответ упёрся в max_tokens — усечение, "
                        "включаю salvage неполного JSON")
        tasks_raw = _parse_tasks_json(response.content[0].text)
    except Exception as e:
        log.warning(f"Task extractor parse error: {e}")
        return []

    # Кто из кандидатов в question ДЕЙСТВИТЕЛЬНО адресован человеку — решает тот же
    # судья, что и на подъёме из памяти. Экстрактор ставил тип сам и путал действие
    # с вопросом: «согласовать с онкологом письменно» и «наблюдать за симптомами»
    # пришли как question, хотя закрываются делом, а не текстом (замер 2026-09-12).
    # Не прошедшее судью становится action — уедет ремайндером, как и должно.
    _q_cands = [{"id": str(i), "text": t.get("content") or ""}
                for i, t in enumerate(tasks_raw)
                if t.get("type") == "question" and t.get("content")]
    _verdicts = addressed_to_patient(_q_cands) if _q_cands else {}
    for i, t in enumerate(tasks_raw):
        if t.get("type") != "question":
            continue
        v = _verdicts.get(str(i))
        # Суждение едет вместе с задачей до записи (15.09). Вердикты лежат по
        # ИНДЕКСУ в `tasks_raw`, а сохраняющий цикл ниже идёт без индекса —
        # складывать их во второй словарь значило бы завести второй дом для
        # того же; кладём на саму задачу и там же читаем.
        t["_judge"] = v or {"verdict": "unparsed",
                            "reason": "судья не ответил (fail-closed)"}
        if not v or v["verdict"] not in ASKING_VERDICTS:
            reason = v["reason"] if v else "судья не ответил (fail-closed)"
            log.info(f"Тип понижен question→action ({reason}): "
                     f"{(t.get('content') or '')[:60]}")
            t["type"] = "action"
        elif v["verdict"] == "unsure" and v.get("ru"):
            # Сомнение разрешается содержательным вопросом, а не действием:
            # берём переформулировку судьи («появились ли высыпания?») вместо
            # исходной формулировки-действия («наблюдать за симптомами»).
            log.info(f"Сомнение → вопрос ({v['reason']}): "
                     f"{(t.get('content') or '')[:40]} → {v['ru'][:60]}")
            t["content"] = v["ru"]

    saved = []
    for t in tasks_raw:
        if not t.get("content"):
            continue
        try:
            resolution_type = t.get("resolution_type", "self_managed")
            # Дедупликация save_task смотрит только на ОТКРЫТЫЕ задачи, поэтому
            # закрытый вопрос GP задаёт заново на следующей неделе. Срок годности
            # ответа решает ось памяти, а не календарь отчётов (нить
            # question-answer-channel, 2026-09-12).
            if t.get("type") == "question":
                ask, why = should_ask_again(t.get("fingerprint"))
                if not ask:
                    log.info(f"Вопрос не перезадаётся ({why}): {t['content'][:60]}")
                    continue
            task_id = db.save_task(
                source=source,
                type_=t.get("type", "action"),
                content=t["content"],
                priority=t.get("priority", "medium"),
                deadline=t.get("deadline"),
                source_date=source_date,
                reason=t.get("reason", ""),
                fingerprint=t.get("fingerprint"),
                resolution_type=resolution_type,
                # Понижённые question→action сохраняют суждение ТОЖЕ: иначе у
                # половины работы судьи (у той, где он сказал «нет») следа бы не
                # осталось, и «судья стал строже» опять было бы неотличимо от
                # «таких задач не приходило».
                judge_verdict=(t.get("_judge") or {}).get("verdict"),
                judge_reason=(t.get("_judge") or {}).get("reason"),
            )
            if task_id is None:
                log.info(f"Task skipped (duplicate): {t['content'][:60]}")
                continue
            if resolution_type == "needs_specialist":
                _notify_specialist(
                    i18n.t("tasks.specialist_required", content=t['content'][:200])
                )
            saved.append({
                "id": task_id,
                "type": t.get("type"),
                "priority": t.get("priority", "medium"),
                "content": t["content"],
                "deadline": t.get("deadline"),
                "reason": t.get("reason", ""),
                "fingerprint": t.get("fingerprint", ""),
                "resolution_type": resolution_type,
            })
            log.info(f"Task saved #{task_id}: [{t.get('type')}] {t['content'][:60]}")
        except Exception as e:
            log.warning(f"Task save error: {e}")

    return saved


# Public API alias — extract_tasks (для UC-G-01 unit-тестов и внешних callers)
extract_tasks = extract_tasks_from_report


def format_tasks_message(tasks: list[dict]) -> str:
    """Форматирует список задач для Telegram."""
    if not tasks:
        return ""

    ICONS = {
        "question":  "❓",
        "lab_test":  "🧪",
        "action":    "📋",
        "followup":  "🔔",
    }
    PRIORITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}

    sorted_tasks = sorted(tasks, key=lambda t: PRIORITY_ORDER.get(t.get("priority", "medium"), 2))

    lines = [i18n.t("tasks.report.heading")]
    for i, t in enumerate(sorted_tasks, 1):
        icon = ICONS.get(t.get("type", "action"), "📋")
        deadline = i18n.t("tasks.deadline", deadline=t['deadline']) if t.get("deadline") else ""
        reason = f"\n   _{t['reason']}_" if t.get("reason") else ""
        lines.append(f"{icon} *{i}.* {t['content']}{deadline}{reason}")

    return "\n".join(lines)


def _md_escape(s: str) -> str:
    """Экранирует спецсимволы Markdown v1 в динамическом тексте."""
    for ch in ['_', '*', '`', '[']:
        s = s.replace(ch, f'\\{ch}')
    return s


def format_open_tasks_message(tasks: list[dict]) -> str:
    """Форматирует список открытых задач (для weekly follow-up)."""
    if not tasks:
        return ""

    ICONS = {"question": "❓", "lab_test": "🧪", "action": "📋", "followup": "🔔"}
    lines = [i18n.t("tasks.open.heading")]
    for i, t in enumerate(tasks[:20], 1):
        icon = ICONS.get(t.get("type", "action"), "📋")
        age = (get_today() - date.fromisoformat(t["created_at"][:10])).days
        deadline = i18n.t("tasks.open.deadline", deadline=_md_escape(t['deadline'])) if t.get("deadline") else ""
        content = _md_escape(t.get("content") or "")
        lines.append(i18n.t("tasks.open.item", icon=icon, number=i, task_id=t['id'],
                            content=content, deadline=deadline, age=fmt_count(age, "days")))

    return "\n".join(lines)


def resolve_task_by_number(task_list: list[dict], number: int,
                           resolved_text: str = None) -> bool:
    """Закрывает задачу по порядковому номеру в списке."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    import health_db as db

    if not (1 <= number <= len(task_list)):
        return False
    task = task_list[number - 1]
    return db.resolve_task(task["id"], resolved_text, status="completed")


# ── Публичный API ─────────────────────────────────────────────────────────────

def process_gp_report(report_text: str, report_type: str = "gp_weekly",
                       report_date: date = None) -> tuple[list, str]:
    """
    Полный цикл: извлечь задачи → создать ремайндеры → форматировать краткое сообщение.
    Returns: (tasks_list, formatted_message)
    """
    tasks = extract_tasks_from_report(
        report_text=report_text,
        source=report_type,
        report_date=report_date or get_today(),
    )
    if not tasks:
        return tasks, ""

    # Создаём ремайндеры в macOS Reminders
    created = create_reminders_for_tasks(tasks)
    log.info(f"Created {created}/{len(tasks)} macOS reminders")

    # Краткое уведомление в Telegram (не дублируем весь список)
    message = _format_tasks_summary(tasks, created)
    return tasks, message


def summary_tasks(tasks):
    priority = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    return sorted(tasks, key=lambda task: priority.get(task.get("priority", "medium"), 2))[:19]


def _format_tasks_summary(tasks: list[dict], reminders_created: int) -> str:
    """Краткое сообщение о задачах: что куда уехало.

    Вопросы и опросники в Reminders не едут (REMINDER_TYPES_EXCLUDED) — они придут
    отдельными сообщениями бота, на которые можно ответить. Поэтому недостачу
    ремайндеров считаем только по тем задачам, которым ремайндер полагался:
    иначе каждое сообщение несло бы ложную тревогу «не удалось создать»."""
    if not tasks:
        return ""

    sorted_tasks = summary_tasks(tasks)

    lines = [i18n.t("tasks.summary.heading", count=len(tasks), reminders_created=reminders_created)]
    for number, t in enumerate(sorted_tasks, 1):
        icon = TYPE_EMOJI.get(t.get("type", "action"), "\U0001f4cb")
        deadline = i18n.t("tasks.deadline", deadline=t['deadline']) if t.get("deadline") else ""
        # Номер печатался только в подписи «/done <id>» — параметра, которого в
        # сообщении не было; чтобы им воспользоваться, приходилось звать /tasks.
        lines.append(f"{number}. {icon} [{t.get('id')}] {t['content'][:80]}{deadline}")

    expected = [t for t in tasks if t.get("type") not in REMINDER_TYPES_EXCLUDED]
    asked = [t for t in tasks if t.get("type") == "question"]
    if reminders_created < len(expected):
        lines.append(i18n.t("tasks.summary.missing_reminders", count=len(expected) - reminders_created))
    if asked:
        lines.append(i18n.t("tasks.summary.questions", count=len(asked)))
    return "\n".join(lines)


def get_weekly_followup() -> str:
    """
    Для weekly follow-up в воскресенье:
    возвращает сообщение с незакрытыми задачами старше 7 дней.
    """
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    import health_db as db

    overdue = db.get_overdue_tasks(days_old=7)
    if not overdue:
        return ""
    return format_open_tasks_message(overdue)


# ── macOS Reminders integration ───────────────────────────────────────────────

def _reminders_list() -> str:
    """Список Reminders текущего человека: «Health» у владельца, «Health (<имя>)» у других.
    Один дом имени — reminders_sync.reminders_list_name (читатель списка); писатель здесь
    зовёт его же, иначе задачи человека уходили бы в список владельца (28.09, этап Б).
    Импорт ленивый: reminders_sync при импорте настраивает свой лог (в боте — без эффекта,
    корневой логгер уже настроен)."""
    from reminders_sync import reminders_list_name
    return reminders_list_name()

PRIORITY_DAYS = {
    "critical": 1,
    "high":     3,
    "medium":   7,
    "low":      14,
}

TYPE_EMOJI = {
    "question":  "❓",
    "lab_test":  "🧪",
    "action":    "📋",
    "followup":  "🔔",
}


def create_macos_reminder(task: dict) -> bool:
    """
    Создаёт задачу в macOS Reminders (список Health).
    task: dict с полями type, priority, content, deadline, reason, id
    """
    import subprocess
    from datetime import datetime

    title_emoji = TYPE_EMOJI.get(task.get("type", "action"), "📋")
    title = f"{title_emoji} {task['content']}"

    # Дедлайн: из задачи или по умолчанию от приоритета
    if task.get("deadline"):
        try:
            dl = date.fromisoformat(task["deadline"])
        except Exception:
            dl = get_today() + timedelta(days=PRIORITY_DAYS.get(task.get("priority", "medium"), 7))
    else:
        dl = get_today() + timedelta(days=PRIORITY_DAYS.get(task.get("priority", "medium"), 7))

    notes = task.get("reason") or ""   # в базе поле бывает NULL: .get(key, "") вернул бы None
    if task.get("id"):
        notes += f"\n[task_id:{task['id']}]"

    # CalDAV (docker-install, этап 3): есть caldav.json у тенанта — туда, иначе AppleScript.
    import reminders_backend
    if reminders_backend.caldav_configured() and task.get("id"):
        from zoneinfo import ZoneInfo
        import location_signal
        try:
            reminders_backend.put_task(int(task["id"]), title, notes, dl, _reminders_list(),
                                     ZoneInfo(location_signal.tenant_timezone()))
            log.info(f"Reminder created (CalDAV): {title[:50]}")
            return True
        except Exception as e:  # noqa: BLE001 — сеть/сервер: громко, задача остаётся в боте
            log.warning(f"Reminder CalDAV error: {e}")
            return False

    # Экранируем для AppleScript
    def esc(s):
        return s.replace("\\", "\\\\").replace('"', '\\"') if s else ""

    # Дата через компоненты — не зависит от системной локали
    rl = esc(_reminders_list())
    script = f'''
tell application "Reminders"
    if not (exists list "{rl}") then
        make new list with properties {{name:"{rl}"}}
    end if
    set r to make new reminder at end of reminders of list "{rl}"
    set name of r to "{esc(title)}"
    set body of r to "{esc(notes)}"
    set d to current date
    set year of d to {dl.year}
    set month of d to {dl.month}
    set day of d to {dl.day}
    set time of d to 0
    set due date of r to d
    return "OK"
end tell'''

    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0 and "OK" in result.stdout:
            log.info(f"Reminder created: {title[:50]}")
            return True
        else:
            log.warning(f"Reminder error: {result.stderr}")
            return False
    except Exception as e:
        log.warning(f"Reminder exception: {e}")
        return False


# Носитель задачи выбирается по тому, ЧЕМ на неё отвечают. Физическое действие
# закрывается фактом делания. Вопрос требует текста ответа; галочка его не несёт.
# Вопрос уходит в бот (jobs.scheduled.deliver_unsent_question_tasks).
REMINDER_TYPES_EXCLUDED = {"question", "assessment"}


def create_reminders_for_tasks(tasks: list[dict]) -> int:
    """Создаёт ремайндеры для списка задач. Возвращает количество созданных.

    Задачи, у которых ответ — текст, а не факт делания, ремайндера не получают:
    два носителя одного предмета разъезжаются (закрыл галочкой — ответа нет)."""
    created = 0
    for task in tasks:
        if task.get("type") in REMINDER_TYPES_EXCLUDED:
            continue
        if create_macos_reminder(task):
            created += 1
    return created


# ── Ответ на вопрос: единственный писатель ────────────────────────────────────

def record_answer(task_id: int, answer_text: str,
                  source: str = "telegram_reply") -> bool:
    """Принять ответ на задачу-вопрос: закрыть задачу и доставить ответ потребителям.

    Как отвечает человек — docs/how-to/answer_a_question.md (вход для того, кто
    уткнулся в отказ закрытия: 409 дашборда и RAISE(ABORT) базы ведут сюда же).

    Единственная точка, через которую ответ входит в систему (doctor_in_loop:
    «ответ возвращается ВХОДОМ, а не оседает репликой в чате»). Primary ответа —
    tasks.resolved_text; в память едет ПРОИЗВОДНОЕ с провенансом task:<id>, не копия:
    сырой текст остаётся сырым (R3 плана — модель уже сочиняла даты химии, и
    переформулировать ответ пациента ей здесь нечего).

    Ключ факта = fingerprint вопроса, поэтому повторный ответ вытесняет прежний
    (supersede-by-key), а не ложится рядом. Временной класс проставляет
    memory_facts_db структурно (class_from_structure), дефолт standing.
    """
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    import health_db as db

    text = (answer_text or "").strip()
    if not text:
        return False

    task = None
    for t in db.get_open_tasks(limit=1000):
        if t["id"] == task_id:
            task = t
            break
    if task is None:
        log.warning(f"record_answer: задача #{task_id} не найдена среди открытых")
        return False

    db.resolve_task(task_id, text, status="completed")

    # Ремайндера у вопроса нет по построению, но задача другого типа могла прийти
    # тем же путём (/done с текстом) — тогда её ремайндер надо закрыть.
    if task.get("type") not in REMINDER_TYPES_EXCLUDED:
        try:
            complete_macos_reminder(task_id)
        except Exception as e:
            log.warning(f"record_answer: ремайндер #{task_id} не закрыт: {e}")

    try:
        import memory_facts_db as mf
        # Классифицируем ответ отдельно от вопроса. Независимо придуманный пример:
        # «Открыли учебный архив к сроку?» → «да, открыли». Срок в вопросе
        # не превращает подтверждённое событие в transient-факт.
        mf.save_fact(
            "fact",
            f"{task.get('content') or ''} → {text}",
            key=task.get("fingerprint") or f"task_{task_id}",
            confidence=1.0,                      # сказано человеком, не выведено
            source=f"task:{task_id}",
            temporal_class=mf.derive_temporal_class(text),
        )
    except Exception as e:
        # Громко: без записи в память ответ не доедет до консилиума, и это
        # ровно тот тихий обрыв, ради которого строился датчик сцепления.
        log.error(f"record_answer: ответ #{task_id} не попал в память: {e}",
                  exc_info=True)

    log.info(f"record_answer: #{task_id} закрыт ответом ({len(text)} симв., "
             f"канал {source})")
    return True


QUESTION_ADDRESSING_PROMPT = """Ты решаешь, что делать с формулировкой: спросить человека или нет.

СПРОСИТЬ (verdict "patient") — когда ответ знает ТОЛЬКО он и ответ этот слова:
что принимает и в какой дозе, что чувствует, что делал, о чём договорился с
врачом, когда что запланировано, состоялось ли назначенное.

НЕ СПРАШИВАТЬ (verdict "not_patient") — только когда это ТОЧНО не к человеку:
1. Вопрос к ДАННЫМ, которые система смотрит сама — значения анализов, метрики сна.
   Под формулировкой в квадратных скобках может стоять улика «в системе УЖЕ есть
   измерения: …». Она значит ровно одно: эти числа система прочитает сама, спрашивать
   их у человека не надо. Но улика НЕ делает вопрос автоматически ненужным: она про
   ЧИСЛА, а не про решения и самочувствие. «Какие значения ферритина были» при улике —
   not_patient. «Согласовали ли дозу железа с врачом», «стало ли легче после железа»
   при той же улике — patient: измерения есть, а договорённость и ощущение есть только
   у человека.
2. Вопрос к НАУКЕ или к самой системе — «как работает утилизация глюкозы при
   варианте PPARG», «будет ли дельта-сон устойчивым», «подтвердится ли гипотеза».
3. Чистое действие без неизвестного: «сдать липазу», «записаться к врачу» —
   человеку тут нечего сообщить, кроме факта делания.

СОМНЕВАЕШЬСЯ (verdict "unsure") — когда формулировка выглядит действием, но за ней
стоит неизвестное системе: актуально ли ещё, появились ли симптомы, состоялось ли,
изменилось ли решение. Тогда НАЧИНАЙ С ВОПРОСА: сформулируй содержательный первый
вопрос человеку и положи его в "ru". Пример: «Наблюдать за симптомами контакта с
герпесом» → «Появились ли высыпания после контакта?». Правило владельца
2026-09-12: при неуверенности сначала содержательный вопрос, а не молчание и не
слепое действие.

Верни ТОЛЬКО JSON-массив, по объекту на КАЖДЫЙ вход, в том же порядке:
[{"id": <id>, "verdict": "patient" | "unsure" | "not_patient",
  "reason": "<коротко, почему>",
  "ru": "<для patient и unsure: вопрос по-русски, одной фразой, человеку>"}]
Ничего, кроме JSON."""

# Вердикты, ведущие к вопросу человеку. «unsure» здесь по решению владельца
# (2026-09-12): неуверенность разрешается СОДЕРЖАТЕЛЬНЫМ вопросом — «актуально
# ли ещё», «появились ли симптомы», — а не молчанием и не слепым действием.
# Молчание остаётся только там, где судья не ответил ВООБЩЕ: сбой ≠ сомнение,
# и спрашивать наугад при отказе модели значит рисковать лавиной (01.08).
ASKING_VERDICTS = {"patient", "unsure"}
_KNOWN_VERDICTS = ASKING_VERDICTS | {"not_patient"}


# «Вся история» — не порог и не настройка, а отсутствие окна, выраженное числом:
# `get_lab_series` принимает только n_days, и сотня лет заведомо покрывает жизнь
# человека. Константой это быть имеет право ровно потому, что физически неизменно
# (правило владельца: константа = только неизменное), а любое «разумное» окно вроде
# пяти лет было бы тем самым фильтром, который здесь и снимается.
_FULL_HISTORY_DAYS = 36500


def _measurements_evidence(text: str) -> str:
    """Улика судье: какие из упомянутых аналитов система УЖЕ измеряла. Пусто — молчим.

    Правило «вопрос к данным — не человеку» требует сведений о доступных
    измерениях. Без этой улики судья может отправить человеку вопрос, на
    который система уже умеет ответить по сохранённой истории.

    Улика, а не решение. Вердикт выносит судья — у суждения об адресате один дом
    (инвариант single_judge_of_addressing), и второй в виде списка слов-триггеров
    завести нельзя: «согласовали ли дозу железа с врачом» упоминает железо, измерения
    железа есть, а ответ всё равно только у человека.

    FAIL-OPEN, и это зеркало правила doubt_resolves_into_a_question: любой сбой
    резолвера или чтения — пустая улика, судья решает как раньше, вопрос скорее
    задаётся, чем глохнет. Тишина в этом канале уже однажды была невидима.
    """
    try:
        import lab_canon
        import labs_db
        names = lab_canon.analytes_mentioned(text)
        if not names:
            return ""
        seen = []
        for canon in sorted(names):
            # БЕЗ ОКНА, и это правка от 13.09, а не недосмотр. Дефолт get_lab_series —
            # 730 дней, то есть фильтр между БД и промптом того же класса, что снятый
            # 30.08 ручной allowlist (запись реестра report_absence_claims). Замер на
            # живом каноне: аналиты со строкой старше окна давали ПУСТУЮ
            # улику — «система этого не измеряла», — а аналит с несколькими строками давал
            # урезанный счёт. Улика отвечает на вопрос «есть ли у системы это измерение
            # ВООБЩЕ», и у такого вопроса границы нет: объявлять окно здесь нечего,
            # его надо убрать. Давность скажет сама дата.
            rows = labs_db.get_lab_series(canon, n_days=_FULL_HISTORY_DAYS)
            if rows:
                seen.append(f"{canon} — {len(rows)} знач., последнее {rows[-1]['date']}")
        return ("в системе УЖЕ есть измерения: " + "; ".join(seen)) if seen else ""
    except Exception as e:  # noqa: BLE001 — улика необязательна, вопрос важнее
        log.warning(f"_measurements_evidence: не собрал улику ({e}) — судья решает без неё")
        return ""


def addressed_to_patient(items: list[dict]) -> dict:
    """Единый судья «это вопрос К ЧЕЛОВЕКУ»: {id: {verdict, reason, ru}}.

    Один дом суждения. До 2026-09-12 его выносили ДВА разных промпта — экстрактор
    задач (ставил type) и гейт подъёма из памяти, — и они уже разошлись: экстрактор
    пропускал «согласовать с онкологом» как вопрос к пациенту, гейт такое отсекал.
    Два дома одного суждения расходятся молча, и расходились.

    Fail-closed: сбой модели или неразобранный ответ → пустой вердикт, и вызывающий
    обязан трактовать это как «не адресовано пациенту». Молчание дешевле, чем вопрос,
    на который человек не может ответить: цена ошибки здесь — выключенный канал.

    Вход: [{"id": <любой>, "text": "<формулировка>"}]. Порядок сохраняется моделью,
    но опираемся на id, а не на позицию.

    ПОВТОР ПОТЕРЯННОГО (28.09, решение владельца «переспросить потерянное»). Замер на 7
    кейсах пробы: 6 ответов из 30 не разбирались целиком (все end_turn, 492–696 токенов —
    не обрезка; ломался объект в середине). Пообъектный salvage спасал соседей, а сломанный
    объект пропадал, и на пути экстрактора пропавший вердикт НЕОБРАТИМО делает вопрос
    действием. Поэтому id без вердикта судятся ещё ОДИН раз — только они, тем же промптом:
    суждение не меняется (строгий формат по схеме пробовали и отвергли — 2 из 12 ответов
    Haiku всё равно слал текстом, а корпус из 27 сдвинул поведение на двух). Второй промах —
    прежний fail-closed. Лишние id из ответа модели (бывает больше вердиктов, чем входов)
    в результат не попадают.
    """
    if not items:
        return {}
    wanted = {str(it["id"]) for it in items}
    out = {k: v for k, v in _judge_once(items).items() if k in wanted}
    missing = [it for it in items if str(it["id"]) not in out]
    if missing:
        log.warning(f"addressed_to_patient: без вердикта {len(missing)} из {len(items)} — "
                    f"переспрашиваю только их")
        again = _judge_once(missing)
        out.update({str(it["id"]): again[str(it["id"])] for it in missing
                    if str(it["id"]) in again})
    return out


def _judge_once(items: list[dict]) -> dict:
    """Один вызов судьи → {id: вердикт}; сбой → {} (fail-closed решает вызывающий)."""
    listing = "\n".join(
        f"{it['id']}: {it['text']}" + (f"\n    [{ev}]" if (ev := _measurements_evidence(it["text"])) else "")
        for it in items)
    try:
        client = _get_client()
        response = client.messages.create(
            model=hai_core.get_model("haiku"),
            max_tokens=2000,
            system=QUESTION_ADDRESSING_PROMPT + hai_core.answer_language(),
            messages=[{"role": "user", "content": listing}],
        )
        raw = _parse_tasks_json(response.content[0].text)
    except Exception as e:
        log.warning(f"addressed_to_patient: судья не отработал ({e}) — "
                    f"fail-closed, ни одна формулировка не считается вопросом")
        return {}

    return _verdicts_from_rows(raw)


def _verdicts_from_rows(raw: list[dict]) -> dict:
    """Разобранные объекты ответа судьи → {id: {verdict, reason, ru}}.

    Отдельной функцией, чтобы ДЕФОЛТ вердикта судился тестом без сети и без денег:
    вся остальная `addressed_to_patient` — вызов модели, и проверить через неё можно
    было бы только мокая клиент, то есть проверяя мок (§20).

    Дефолт "unparsed", а НЕ "not_patient" (нить question-discard-axis, 2026-09-14).
    Оба одинаково fail-closed для СПРАШИВАНИЯ: ни один не входит в ASKING_VERDICTS,
    вопрос не задаётся. Но у вызывающего появился второй акт — снять кандидата с
    активных, — и для него это РАЗНЫЕ события: «судья посмотрел и сказал нет» можно
    закрывать навсегда, «объект разобрался, а поля вердикта в нём не было» закрывать
    нельзя, иначе артефакт разбора хоронит кандидата под видом суждения.
    """
    out = {}
    for row in raw:
        rid = row.get("id")
        if rid is None:
            continue
        out[str(rid)] = {
            # Значение пришло от модели — вход недоверенной границы: вне словаря → "unparsed"
            # (не суждение: не спрашиваем и не хороним), а не пропуск как есть (28.09).
            "verdict": (row.get("verdict") if row.get("verdict") in _KNOWN_VERDICTS
                        else "unparsed"),
            "reason": (row.get("reason") or "").strip(),
            "ru": (row.get("ru") or "").strip(),
        }
    return out


def promote_memory_questions(limit: int | None = None) -> list[dict]:
    """Поднимает вопросы из памяти в задачи, которые реально задаются человеку.

    Вопросы в memory_facts(mem_class='question') — кандидаты, не очередь
    доставки. Общий дом задаваемых вопросов — tasks; накопление в памяти
    само по себе не доказывает, что вопрос был задан.

    Отбор двухступенчатый и fail-closed на каждой: структурный (источник
    'conversation' — не 'arbiter_unverified', где вопрос выдуман моделью по картинке
    без улик в словах человека; окно свежести; не поднимался раньше) и смысловой —
    может ли на вопрос ответить ТОЛЬКО пациент. Личное решение и общий вопрос
    к научным знаниям требуют разных адресатов. Поэтому модель предлагает,
    код решает, а человек отвечает на заданный вопрос или отклоняет его.

    Числа (окно, размер партии) — в system_config: без них не поднимаем ничего.
    """
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    import health_db as db
    import memory_facts_db as mf
    import config_db as cfg

    window = cfg.get_config("questions.memory_window_days", default=None)
    batch = limit if limit is not None else cfg.get_config("questions.promote_batch",
                                                           default=None)
    try:
        window = float(window)
        batch = int(float(batch))
    except (TypeError, ValueError):
        log.warning("promote_memory_questions: нет questions.memory_window_days / "
                    "questions.promote_batch в system_config — не поднимаем")
        return []
    if batch <= 0:
        return []

    # ПОТОЛОК ПРОИЗВОДСТВА (нить question-tails, 2026-09-12). Дроссель стоял только
    # на ДОСТАВКЕ, а рождение вопросов не ограничивалось ничем: job объявлен суточным
    # (interval=86400, first=600), но бот перезапускается на каждый коммит, значит
    # срабатывает раз в РЕСТАРТ. Замер 12.09: 16 вопросов за 2ч17м семью партиями при
    # сливе 2/сутки.
    #
    # Инвариант переезжает с РАСПИСАНИЯ на ДАННЫЕ: не поднимаем, пока в очереди уже
    # лежит недоставленных не меньше дневного бюджета. Как часто врёт таймер, после
    # этого неважно — тот же урок, что с предикатом `sent_at IS NULL` в этой же нити.
    # Своего числа не заводим: потолок это дневной бюджет доставки (§9).
    queued = len(db.get_questions_needing_delivery())
    ceiling = cfg.get_config("questions.max_per_day", default=None)
    try:
        ceiling = int(float(ceiling))
    except (TypeError, ValueError):
        log.warning("promote_memory_questions: questions.max_per_day не задан — "
                    "потолка нет, не поднимаем (fail-closed, как и доставка)")
        return []
    if queued >= ceiling:
        log.info(f"promote_memory_questions: в очереди {queued} ≥ потолка {ceiling} — "
                 f"новых не поднимаем, судью не зовём")
        return []

    candidates = [
        f for f in mf.get_facts("question", since_days=int(window))
        if (f.get("source") or "") == "conversation"
    ]
    if not candidates:
        return []

    # Уже поднятые — по fingerprint, независимо от статуса задачи: вопрос,
    # который человек отклонил, не должен воскресать следующей ночью.
    with db.get_conn() as conn:
        taken = {r[0] for r in conn.execute(
            "SELECT fingerprint FROM tasks WHERE fingerprint LIKE 'question:mem:%'"
        )}
    candidates = [f for f in candidates
                  if f"question:mem:{f['id']}" not in taken][:batch]
    if not candidates:
        return []

    # Судья — общий с экстрактором задач (один дом суждения), fail-closed:
    # пустой вердикт значит «не поднимаем», а не «поднимаем всё».
    verdicts = addressed_to_patient(
        [{"id": f["id"], "text": f["value"]} for f in candidates])

    # Дедуп ПОСЛЕ судьи (нить question-tails, 2026-09-12). Отбор выше сравнивает
    # отпечатки ИСХОДНЫХ фактов и дубли, рождённые судьёй, не видит по построению:
    # два факта о согласовании дозы одной и той же добавки с врачом —
    # разные факты разными словами (близость по Жаккару 0.33, ниже любого разумного
    # порога), а судья переписал оба в одну русскую фразу и родил задачи 197 и 200.
    #
    # Асимметрия цены названа: лишний вопрос стоит одного сообщения, потерянный —
    # молчания, которое не видно. Поэтому сравнение СТРОГОЕ (нормализованное
    # совпадение), а не пороговое: склеить «дозу добавки А» с «дозой добавки Б» хуже,
    # чем пропустить перефразированный дубль.
    def _dedup_key(s: str) -> str:
        return " ".join(re.findall(r"[а-яёa-z0-9]+", (s or "").lower()))

    open_keys = {_dedup_key(r["content"]) for r in db.get_open_questions()}

    created = []
    for mem_id, v in verdicts.items():
        if v["verdict"] not in ASKING_VERDICTS:
            log.info(f"promote_memory_questions: #{mem_id} не пациенту ({v['reason']})")
            # СЛЕД суждения (нить question-discard-axis, 2026-09-14). До этой строки
            # отклонённый судьёй кандидат оставался активным и неотличимым от того, кого
            # судья не видел НИ РАЗУ; у обоих одинаковый конец — выпасть за окно молча.
            # Датчик выброшенных кандидатов из-за этого считал работу фильтра потерей и
            # краснел тем громче, чем лучше фильтр работает. Дом ретайра уже существует
            # (retire_fact ниже по функции гасит дубли) — своего поля не заводим.
            #
            # Снимаем ТОЛЬКО по явному "not_patient". "unsure" сюда не доходит (он в
            # ASKING_VERDICTS — решение владельца «сомнение разрешается вопросом»), а
            # "unparsed" — не суждение, а артефакт разбора: хоронить по нему нельзя.
            # Цена ошибки асимметрична (потерянный вопрос — молчание, которого снаружи
            # не видно), поэтому сомнение и сбой остаются кандидатами.
            if v["verdict"] == "not_patient":
                mf.retire_fact(int(mem_id), f"судья: не пациенту — {v['reason']}")
            continue
        text = v["ru"]
        if not text:
            continue
        key = _dedup_key(text)
        if key in open_keys:
            log.info(f"promote_memory_questions: #{mem_id} дубль уже открытого вопроса "
                     f"(«{text[:60]}») — задачу не создаём")
            mf.retire_fact(int(mem_id), "дубль уже открытого вопроса")
            continue
        open_keys.add(key)
        task_id = db.save_task(
            source="memory_question",
            type_="question",
            content=text,
            priority="low",          # вопрос из разговора не срочнее клинического
            source_date=str(get_today()),
            reason="вопрос накоплен ассистентом в разговоре",
            fingerprint=f"question:mem:{mem_id}",
            # Суждение судьи — в СВОИ поля, не в `reason`: тот рендерится человеку
            # курсивом под вопросом (format_tasks_message). Строка выше намеренно
            # остаётся человеческой и одинаковой — она объясняет ПРОИСХОЖДЕНИЕ
            # вопроса, а не обоснование машины (15.09, BL-JUDGE-VERDICT-UNRECORDED-1).
            judge_verdict=v["verdict"],
            judge_reason=v.get("reason"),
        )
        if task_id:
            created.append({"id": task_id, "memory_id": mem_id, "content": text})
    log.info(f"promote_memory_questions: поднято {len(created)} из {len(candidates)}")
    return created


def should_ask_again(fingerprint: str | None) -> tuple[bool, str]:
    """Задавать ли вопрос заново, если на него уже отвечали.

    Ось срока годности — та же, что у памяти (memory_temporal_axis), второй не
    заводим. Класс ответа определяет поведение: durable — правда не меняется,
    спрашивать больше никогда; standing — держится до явной смены, по возрасту не
    тускнеет; transient — привязан к дате или относительному дню, стареет по
    memory_tuning.transient_ttl_days (дом срока уже существует, своё число не вводим).

    Этот читатель — первый, который ось УВАЖАЕТ на чтении: инвариант
    read_side_enforcement_open честно открыт, запись класс ставит, чтение до сих пор
    не спрашивало.
    """
    if not fingerprint:
        return True, "нет fingerprint — сравнить не с чем"
    try:
        import memory_facts_db as mf
        import config_db as cfg
    except Exception as e:
        return True, f"память недоступна ({e})"

    rows = [f for f in mf.get_facts("fact") if f.get("key") == fingerprint]
    if not rows:
        return True, "ответа на этот вопрос в памяти нет"

    answer = rows[0]
    tclass = answer.get("temporal_class") or "standing"
    if tclass == "durable":
        return False, "ответ durable — не меняется"
    if tclass == "standing":
        return False, "ответ standing — держится до явной смены, не по возрасту"

    ttl = cfg.get_config("memory_tuning.transient_ttl_days", default=None)
    try:
        ttl_days = float(ttl)
    except (TypeError, ValueError):
        # fail-closed: без срока из данных не перезадаём, а сообщаем
        return False, "срок transient не задан в system_config — вопрос не перезадаётся"

    stamp = (answer.get("valid_from") or answer.get("created_at") or "")[:10]
    try:
        age = (get_today() - date.fromisoformat(stamp)).days
    except Exception:
        return True, "дата ответа нечитаема"
    if age > ttl_days:
        return True, f"ответ transient и старше {ttl_days:.0f} дн. ({age} дн.)"
    return False, f"ответ transient, но свежий ({age} дн.)"


def complete_macos_reminder(task_id: int) -> bool:
    """Помечает ремайндер в macOS как выполненный по task_id в теле заметки."""
    import subprocess
    import reminders_backend
    if reminders_backend.caldav_configured():   # CalDAV (docker-install, этап 3)
        try:
            return reminders_backend.complete_task(task_id, _reminders_list())
        except Exception as e:  # noqa: BLE001 — сеть/сервер: громко
            log.warning(f"complete reminder CalDAV error: {e}")
            return False

    rl = _reminders_list().replace("\\", "\\\\").replace('"', '\\"')
    script = f'''
tell application "Reminders"
    if exists list "{rl}" then
        set matched to (reminders of list "{rl}" whose body contains "[task_id:{task_id}]")
        repeat with r in matched
            set completed of r to true
        end repeat
        return count of matched
    end if
    return 0
end tell'''

    try:
        result = subprocess.run(["osascript", "-e", script],
                                capture_output=True, text=True, timeout=10)
        count = int(result.stdout.strip()) if result.stdout.strip().isdigit() else 0
        log.info(f"Completed {count} reminders for task_id={task_id}")
        return count > 0
    except Exception as e:
        log.warning(f"complete_macos_reminder error: {e}")
        return False
