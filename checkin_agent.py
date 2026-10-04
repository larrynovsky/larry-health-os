import llm_client
#!/usr/bin/env python3.11
"""
Evening Check-in Agent.

Не форма, а разговор. Бот задаёт один тёплый контекстный вопрос,
слышит ответ, при необходимости спрашивает дальше (1-2 уточнения),
потом сохраняет структурированное резюме.

Принципы:
- Вопросы варьируются — AI генерирует их из контекста, не из шаблонов
- Тон: заинтересованный, не клинический
- 2-3 обмена максимум, потом финализация
- Арбитр извлекает: оценка дня, энергия, события, физика, стресс
"""

import json
import hai_core
import logging
import sys as _sys
from datetime import date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

_sys.path.insert(0, str(Path(__file__).parent))
from _time_inject import get_today, get_now

import anthropic

import region_pack

log = logging.getLogger(__name__)


TZ = ZoneInfo(region_pack.value("timezone", "UTC"))   # таймзона дома — пакет региона (pub-prep)

MAX_TURNS = 3  # максимум обменов до финализации

# ── Промпты ───────────────────────────────────────────────────────────────────

CHECKIN_SYSTEM = """Ты — внимательный врач и собеседник пациента (__PATIENT_DESC__).
Сейчас вечер, и ты хочешь узнать как прошёл его день.

Твои задачи:
1. Задать один тёплый, конкретный вопрос — не "как дела?", а что-то осмысленное
   исходя из контекста: что Oura показывает, что было в календаре, какой период жизни.
2. Если ответ неполный или что-то заслуживает уточнения — задай один уточняющий вопрос.
3. Не задавай больше одного вопроса за раз.
4. Когда ты чувствуешь что картина дня сложилась — скажи короткую тёплую фразу
   (2-3 предложения) и заверши разговор фразой: [CHECKIN_COMPLETE]

Тон: заинтересованный, не тревожный. Ты рад что у него всё под контролем.
Не перечисляй данные обратно. Не давай медицинских рекомендаций в чекине.
Говори как человек, а не как система. На русском."""

CHECKIN_OPENER_SYSTEM = """Ты генерируешь один вечерний вопрос для пациента (__PATIENT_DESC__).

Правило: вопрос должен быть конкретным и тёплым. Варьируй каждый раз.
Смотри на данные дня — если что-то необычное (низкий ВСР, много шагов,
событие в календаре, поездка) — зацепись за это.
Не начинай с "Как прошёл день?" — это слишком общо.

Важно про календарь и поездки:
- Если видишь событие с тегом [поездка идёт] — человек уже в путешествии, спрашивай про впечатления, а не про предстоящий перелёт.
- Если видишь [заезд в отель, не вылет] — это дата заезда в отель, не дата вылета. Не говори что он летит.
- Если видишь перелёт завтра/сегодня — можно спросить про подготовку.

Примеры хороших вопросов:
- "Сегодня у тебя было {событие} — как это прошло, и как ты себя чувствовал к вечеру?"
- "Oura показывает что ты сегодня прошёл больше обычного — ты куда-то выбирался?"
- "Как ощущается тело после вчерашней ночи? Oura отметила сон чуть короче."
- "Что из сегодняшнего дня стоит запомнить — что-то хорошее или что-то важное?"
- "Ты сегодня ел что-то новое или интересное? Мне любопытно как питание влияет на твою энергию."

Выдай ТОЛЬКО текст вопроса, без объяснений."""

CHECKIN_EXTRACTOR_SYSTEM = """Ты извлекаешь структурированные данные из вечернего чекина.
Верни ТОЛЬКО валидный JSON, без markdown, без объяснений.

Структура:
{
  "day_score": 7,          // оценка дня 1-10, null если не упомянута
  "energy_level": "high",  // high/medium/low/null
  "mood": "calm",          // positive/neutral/negative/mixed/null  
  "physical_symptoms": [], // список симптомов если упоминались
  "notable_events": [],    // ключевые события дня
  "stress_notes": null,    // если упоминался стресс — кратко
  "wins": [],              // что прошло хорошо
  "food_notes": null,      // питание если упоминалось
  "sleep_comment": null,   // если пациент прокомментировал сон/усталость
  "free_text": ""          // краткое резюме своими словами (1-2 предложения)
}

Если информации нет — ставь null или пустой массив. Не выдумывай."""


def _get_checkin_patient_desc() -> str:
    import health_db as _db; from datetime import date as _d
    _db.init_db()
    ident = _db.get_profile_context().get("identity", {})
    birth = ident.get("birth_date", ""); loc = ident.get("location", "")
    age_s = ""
    if birth:
        try: age_s = f"{(_d.today() - _d.fromisoformat(birth)).days // 365} лет"
        except Exception: pass  # silent-ok: birth_date parse → skip age
    return ", ".join(p for p in [age_s, loc] if p) or "пациент"

def _last_user_checkin(checkins: list[dict]) -> dict | None:
    """Первый чекин, который является ОТВЕТОМ пользователя, а не неотвеченным
    опенером бота (строки с префиксом 'ASSISTANT:'). Возвращает None если таких
    нет. Список ожидается отсортированным свежие→старые."""
    for c in checkins or []:
        ans = (c.get("answer") or "").strip()
        if ans and not ans.upper().startswith("ASSISTANT:"):
            return c
    return None


def _data_truth_note() -> str:
    """Заземляющий факт для чекин-беседы: сенсорные данные Oura присутствуют.

    Баг 2026-07-03: беседа чекина не заземлена данными (continue_checkin шлёт
    только system+историю), и модель выдумала «данных из Оры нет с декабря».
    Пустая строка, если данных действительно нет (тогда не утверждаем обратное).
    """
    import health_db as db
    try:
        with db.get_conn() as c:
            row = c.execute(
                "SELECT MAX(date) d FROM daily_metrics WHERE sleep_total IS NOT NULL"
            ).fetchone()
        latest = row["d"] if row else None
    except Exception:
        latest = None
    if not latest:
        return ""
    return (
        f"\n\nФАКТ О ДАННЫХ (не оспаривай и не выдумывай иного): сенсорные данные "
        f"Oura поступают автоматически; последний день с данными — {latest}. "
        "Пропуск РУЧНЫХ вечерних чекинов ≠ отсутствие сенсорных данных. Никогда не "
        "говори, что данных нет, что они «пропали» или «не поступают с какого-то "
        "месяца», и не упрекай пациента за то, что он «не делился данными»."
    )


@hai_core.with_answer_language
def _build_checkin_system() -> str:
    return (CHECKIN_SYSTEM.replace("__PATIENT_DESC__", _get_checkin_patient_desc())
            + _data_truth_note())

@hai_core.with_answer_language
def _build_checkin_opener_system() -> str:
    return (CHECKIN_OPENER_SYSTEM.replace("__PATIENT_DESC__",
                                          _get_checkin_patient_desc())
            + _data_truth_note())

def _get_client():
    return llm_client.guarded_client()


def _build_day_context() -> str:
    """Собирает контекст текущего дня для генерации вопроса."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    import health_db as db
    db.init_db()

    today = get_today()
    row = db.get_day(str(today))
    yesterday = db.get_day(str(today - timedelta(days=1)))

    # Сегодняшние данные (Oura пишет сон в дату пробуждения = сегодня)
    sleep = row.get("sleep") or {}
    hrv = (row.get("hrv") or {}).get("avg")
    readiness = row.get("readiness_score")
    steps = row.get("steps")
    # BP — из Withings, данные могут быть не каждый день
    bp_sys = (row.get("bp_systolic") or yesterday.get("bp_systolic"))
    bp_dia = (row.get("bp_diastolic") or yesterday.get("bp_diastolic"))

    # Прошлонедельный baseline
    stats7 = db.get_stats(7, today)

    # Периоды (поездки, клинические фазы)
    # PERIODS-SEMANTICS (2026-06-19): мигрировано на helpers — current_periods()
    # для активных-сейчас + future_periods() с фильтром 14 дней для upcoming.
    periods_ctx = ""
    try:
        import health_db as _db
        _today_s = str(today)
        _horizon = str(today + timedelta(days=14))
        active = _db.current_periods(_today_s)
        upcoming = [p for p in _db.future_periods() if p['start_date'] <= _horizon]

        lines = []
        if active:
            lines.append("Активные периоды:")
            for p in active:
                end = f" — {p['end_date']}" if p['end_date'] else ""
                note = f" ({p['notes']})" if p['notes'] else ""
                lines.append(f"  [идёт] {p['name']}{end}{note}")
        if upcoming:
            lines.append("Ближайшие:")
            for p in upcoming:
                end = f" — {p['end_date']}" if p['end_date'] else ""
                note = f" ({p['notes']})" if p['notes'] else ""
                lines.append(f"  [скоро] {p['name']} с {p['start_date']}{end}{note}")
        periods_ctx = "\n".join(lines)
    except Exception as _e:  # silent-ok: periods opt-in context
        import logging as _log
        _log.getLogger(__name__).warning(f"checkin periods_ctx error: {_e!r}")
        periods_ctx = ""

    # Последний чекин — только НАСТОЯЩИЙ ответ пользователя.
    # Баг 2026-07-03: строки checkins с префиксом "ASSISTANT:" — это неотвеченные
    # опенеры самого бота. Скармливая их обратно как «последний чекин», бот
    # эскалировал собственный нэг («ты не записывал данные») → в беседе выдумал
    # «данных из Оры нет с декабря». Показываем только реплики пользователя.
    last_checkins = db.get_recent_checkins(8)
    lc = _last_user_checkin(last_checkins)
    last_checkin_note = ""
    if lc and lc["date"] != str(today):
        days_ago = (today - date.fromisoformat(lc["date"])).days
        last_checkin_note = (
            f"Последний ОТВЕТ пользователя в ручном чекине: {lc['date']} "
            f"({days_ago} дн. назад): {lc['answer'][:100]}... "
            "(это про ручные чекины; сенсорные данные Oura при этом поступают.)")

    parts = [
        f"Дата: {today.strftime('%A, %d %B %Y')} (вечер)",
        "",
        "Данные Oura сегодня:",
        f"  Сон: {sleep.get('totalSleep', '—')}ч | deep {int((sleep.get('deep') or 0)*60)}м | score {sleep.get('sleep_score', '—')}",
        f"  ВСР: {f'{hrv:.0f} мс' if hrv else '—'} (7д avg: {stats7.get('avg_hrv', '—')} мс)",
        f"  Readiness: {readiness or '—'} (7д avg: {stats7.get('avg_readiness', '—')})",
        f"  Шаги: {int(steps) if steps else '—'} (7д avg: {int(stats7.get('avg_steps') or 0) or '—'})",
    ]
    if bp_sys and bp_dia:
        bp_flag = " ⚠ повышенное" if bp_sys >= 140 else ""
        parts.append(f"  АД: {bp_sys:.0f}/{bp_dia:.0f} мм рт.ст.{bp_flag}")

    # Все собранные показатели за неделю — тот же общий блок, что у врачебных контекстов
    # (BL-DATA-PARITY-1). Строки Oura выше — у человека без кольца там прочерки, а часы,
    # весы или Apple Health дают свои показатели; вопросу дня есть за что зацепиться у любого.
    # Промпт по-прежнему запрещает перечислять данные обратно: блок — материал, не текст ответа.
    _all = db.render_all_metrics(8, today)
    if _all:
        parts += ["", "Все собранные показатели (8 дней):", _all]

    if periods_ctx:
        parts += ["", periods_ctx]

    if last_checkin_note:
        parts += ["", last_checkin_note]

    # Единый контекст памяти из чата (C-3): свежие жалобы/поправки для генерации вопроса.
    # reasoning_block безопасен (внутри ловит) — обёртка не нужна.
    import patient_context as _pc
    _rb = _pc.reasoning_block()
    if _rb:
        parts += ["", _rb]

    return "\n".join(parts)


# ── Публичный API ─────────────────────────────────────────────────────────────

def _guarded_reply(text: str) -> str:
    """Судит реплику чекина против канона и ПОПРАВЛЯЕТ (решение владельца 14.09).

    Чекин — диалог: человек читает реплику немедленно, второго пути доставки нет,
    поэтому политика та же, что в чате, а не блок. Судья общий — gp_context.
    """
    try:
        import gp_context as _gc
        hits, note = _gc.judge_absence_claims(text)
    except Exception as e:
        log.warning("checkin: судья отсутствия не отработал: %s", e)
        return text
    if not hits:
        return text
    log.warning("checkin: утверждение об отсутствии против канона (%d): %s", len(hits),
                "; ".join(f"{h['test']} ({h['last_date']})" for h in hits))
    return text + chr(10) + chr(10) + note


def generate_opening_question() -> str:
    """Генерирует первый вечерний вопрос с учётом контекста дня."""
    context = _build_day_context()
    client = _get_client()

    response = client.messages.create(task="checkin_agent.generate_opening_question",
        model=hai_core.get_model("haiku"),
        max_tokens=200,
        system=_build_checkin_opener_system(),
        messages=[{"role": "user", "content": context}],
    )
    return _guarded_reply(llm_client.answer_text(response).strip())


def continue_checkin(conversation: list[dict]) -> tuple[str, bool]:
    """
    Продолжает разговор чекина.
    Возвращает (ответ, is_complete).
    """
    client = _get_client()
    response = client.messages.create(task="checkin_agent.continue_checkin",
        model=hai_core.get_model("haiku"),
        max_tokens=300,
        system=_build_checkin_system(),
        messages=conversation,
    )
    reply = llm_client.answer_text(response).strip()
    is_complete = "[CHECKIN_COMPLETE]" in reply
    reply = reply.replace("[CHECKIN_COMPLETE]", "").strip()
    return _guarded_reply(reply), is_complete


def finalize_checkin(conversation: list[dict]) -> dict:
    """Извлекает структурированные данные из завершённого разговора."""
    import health_db as db
    db.init_db()

    client = _get_client()
    full_text = "\n".join(
        f"{m['role'].upper()}: {m['content']}" for m in conversation
    )
    response = client.messages.create(task="checkin_agent.finalize_checkin",
        model=hai_core.get_model("haiku"),
        max_tokens=500,
        system=CHECKIN_EXTRACTOR_SYSTEM,
        messages=[{"role": "user", "content": full_text}],
    )
    raw = llm_client.answer_text(response).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = {"free_text": raw}

    # BUG-CHECKIN-SIG fix (2026-05-12): save_checkin требует (day, question,
    # answer, context, time_of_day). Раньше передавался только dict — ломало
    # запись (dict попадал в поле question как строка, answer оставался NULL).
    # Распределение:
    #   question  = первое assistant-сообщение (opening question)
    #   answer    = full_text — полный конкатенированный разговор
    #   context   = data — structured JSON-извлечение от Claude
    #   time_of_day = "evening" — этот pipeline используется только для вечернего
    #                 чекина (morning checkin — отдельная цепочка).
    data["answer"] = full_text  # сохраняем в data для совместимости с callers
    opening_q = ""
    for msg in conversation:
        if msg.get("role") == "assistant":
            opening_q = (msg.get("content") or "").strip()
            break
    day_str = str(get_today())
    db.save_checkin(
        day=day_str,
        question=opening_q,
        answer=full_text,
        context=data,
        time_of_day="evening",
    )

    # Sprint 3 / Р-2 (2026-05-22): сохраняем извлечённые scores в плоские колонки.
    # day_score: 1-10 как есть (LLM возвращает int или null).
    # mood: positive=3, neutral/mixed=2, negative=1, null → не пишем.
    # energy_level: high=3, medium=2, low=1, null → не пишем.
    # После этого lifestyle_agents.StressAgent может делать SQL-агрегации
    # без парсинга JSON (F-117 / Sprint 0 fix #2 имел TODO про это).
    MOOD_MAP   = {"positive": 3, "neutral": 2, "mixed": 2, "negative": 1}
    ENERGY_MAP = {"high": 3, "medium": 2, "low": 1}
    try:
        ds = data.get("day_score")
        stress_score = int(ds) if isinstance(ds, (int, float)) and 0 <= ds <= 10 else None
        mood_score   = MOOD_MAP.get(data.get("mood"))
        energy_score = ENERGY_MAP.get(data.get("energy_level"))
        db.update_checkin_scores(
            day=day_str, time_of_day="evening",
            stress_score=stress_score,
            mood_score=mood_score,
            energy_score=energy_score,
        )
    except Exception as _e:
        log.debug(f"update_checkin_scores failed: {_e}")

    return data


class CheckinState:
    """
    Держит состояние текущего чекин-разговора.
    Один экземпляр на весь бот-процесс.
    TTL: если чекин не завершён за 4 часа — считается зависшим.
    """
    def __init__(self):
        self.active = False
        self.conversation: list[dict] = []
        self.turns = 0
        self.started_at = None

    def start(self, opening_question: str):
        self.active = True
        self.conversation = [{"role": "assistant", "content": opening_question}]
        self.turns = 0
        from datetime import datetime
        self.started_at = get_now(TZ)

    def add_user(self, text: str):
        self.conversation.append({"role": "user", "content": text})
        self.turns += 1

    def add_assistant(self, text: str):
        self.conversation.append({"role": "assistant", "content": text})

    def should_force_end(self) -> bool:
        return self.turns >= MAX_TURNS

    def is_stale(self, ttl_hours: int = 4) -> bool:
        """True если чекин завис без завершения дольше ttl_hours часов."""
        if not self.active or self.started_at is None:
            return False
        from datetime import datetime
        age = get_now(TZ) - self.started_at
        return age.total_seconds() > ttl_hours * 3600

    def reset(self):
        self.active = False
        self.conversation = []
        self.turns = 0
        self.started_at = None


# Глобальный синглтон для бота
checkin_state = CheckinState()
