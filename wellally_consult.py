import llm_client
#!/usr/bin/env python3.11
"""
WellAlly MDT консультация — Deliberative Council (v2).

Архитектура:
  - 13 участников: 9 медицинских специалистов + 4 lifestyle-коуча
  - 2 deliberation раунда за каждый цикл:
      Раунд A: все 13 независимо (параллельно)
      Раунд B: все 13 видят мнения коллег из Раунда A (параллельно)
  - Координатор синтезирует Раунд B → задаёт один вопрос пациенту
  - ConsultationSession хранит историю диалога и раундов
  - При каждом ответе пациента — новый цикл с обогащённым контекстом

Релевантные специалисты (состав — consilium_roster; пример для онко-тенанта):
  - oncology     — маркеры, ремиссия, динамика
  - gastroenterology — ЖКТ, альбумин, питание
  - hematology   — CBC пост-химия
  - cardiology   — кардиоонкология, ВСР
  - nephrology   — нефротоксичность химиотерапии
  - neurology    — CIPN периферическая нейропатия
"""

import asyncio
import consilium_roster
import hai_core
import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import sys as _sys
_sys.path.insert(0, str(Path(__file__).parent))
from _time_inject import get_today
from _fmt_helpers import fmt_min, fmt_or

import anthropic

log = logging.getLogger(__name__)


def _calc_age(birth_date_str: str) -> int:
    """Вычисляет полных лет на сегодня."""
    bd = date.fromisoformat(birth_date_str)
    today = get_today()
    return today.year - bd.year - ((today.month, today.day) < (bd.month, bd.day))


def _fmt_bday(birth_date_str: str) -> str:
    """ISO date → 'DD.MM.YYYY'."""
    bd = date.fromisoformat(birth_date_str)
    return bd.strftime("%d.%m.%Y")

SPEC_DIR  = Path(__file__).parent / "specialists"  # промпты в git (Ф0b), не в iCloud

def _medical_specialists() -> list[str]:
    """Единый ростер консилиума (consilium_roster): все мед. специалисты минус
    демография тенанта. Свёл split-brain с monthly_consilium."""
    import consilium_roster
    return consilium_roster.medical_roster()

LIFESTYLE_AGENT_NAMES = ["Sleep", "Movement", "Stress/HRV", "Energy/Recovery"]

# Координаторский промпт — диалоговый режим
COORDINATOR_DIALOGUE_PROMPT = """
Ты — координатор мультидисциплинарного консилиума (МДТ) в режиме живого диалога с пациентом.

Твоя задача — синтезировать мнения всех участников консилиума (врачи по профилю пациента + lifestyle-коучи) и вести
осмысленный диалог с пациентом, помогая разобраться в его ситуации.

Структура ответа:
1. СНАЧАЛА — прямой ответ на вопрос пациента из блока ❗ ИСХОДНЫЙ ВОПРОС или ❓ ВОПРОС,
   используя мнения специалистов по этой теме
2. Дополнительный контекст — что ещё важного видят специалисты, ключевые связи между доменами
3. Один конкретный уточняющий вопрос пациенту

Правила:
- Если консенсус специалистов подозрительно единодушен — скажи об этом
- Если lifestyle-коучи видят иначе, чем врачи — отметь это явно
- Не заканчивай диалог сам — всегда задавай вопрос, если нет явной завершённости
- Тон: коллегиальный, не менторский. На русском языке.
- Длина: достаточная чтобы полностью ответить на вопрос. Не обрывай мысль.
"""


# ── Session State ─────────────────────────────────────────────────────────────

@dataclass
class ConsultationRound:
    opinions_a: dict = field(default_factory=dict)   # {participant_name: opinion_text}
    opinions_b: dict = field(default_factory=dict)   # {participant_name: opinion_text}
    coordinator_response: str = ""
    coordinator_question: str = ""

    def to_dict(self) -> dict:
        return {
            "opinions_a": self.opinions_a,
            "opinions_b": self.opinions_b,
            "coordinator_response": self.coordinator_response,
            "coordinator_question": self.coordinator_question,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ConsultationRound":
        return cls(
            opinions_a=d.get("opinions_a", {}),
            opinions_b=d.get("opinions_b", {}),
            coordinator_response=d.get("coordinator_response", ""),
            coordinator_question=d.get("coordinator_question", ""),
        )


@dataclass
class ConsultationSession:
    """Полное состояние диалоговой консультации."""
    started_at: datetime = field(default_factory=datetime.now)
    user_qa: list = field(default_factory=list)         # [(question, answer), ...]
    rounds: list = field(default_factory=list)          # [ConsultationRound, ...]
    sleep_date: Optional[date] = None
    activity_date: Optional[date] = None

    def to_dict(self) -> dict:
        return {
            "started_at": self.started_at.isoformat(),
            "user_qa": [list(qa) for qa in self.user_qa],  # tuple→list для JSON
            "rounds": [r.to_dict() for r in self.rounds],
            "sleep_date": self.sleep_date.isoformat() if self.sleep_date else None,
            "activity_date": self.activity_date.isoformat() if self.activity_date else None,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ConsultationSession":
        obj = cls.__new__(cls)
        obj.started_at = datetime.fromisoformat(d["started_at"])
        obj.user_qa = [tuple(qa) for qa in d.get("user_qa", [])]
        obj.rounds = [ConsultationRound.from_dict(r) for r in d.get("rounds", [])]
        sd = d.get("sleep_date")
        obj.sleep_date = date.fromisoformat(sd) if sd else None
        ad = d.get("activity_date")
        obj.activity_date = date.fromisoformat(ad) if ad else None
        return obj


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_async_client() -> anthropic.AsyncAnthropic:
    return llm_client.guarded_client(async_=True)


def _read_specialist_prompt(name: str) -> str:
    path = SPEC_DIR / f"{name}.md"
    if path.exists():
        text = path.read_text(encoding="utf-8")
        # Плейсхолдер профиля → рантайм-инжект из БД (Ф4). Нет плейсхолдера → no-op.
        if "%%PATIENT_PROFILE%%" in text:
            import patient_context as _pc
            text = text.replace("%%PATIENT_PROFILE%%", _pc.build_patient_brief())
        return text
    return f"Ты — специалист в области {name}. Анализируй медицинские данные строго по своей специальности."


def _build_session_history(session: ConsultationSession) -> str:
    """Строит текстовый блок из истории диалога и предыдущих раундов."""
    parts = []

    if session.user_qa:
        qa_lines = []
        for q, a in session.user_qa:
            qa_lines.append(f"Координатор спросил: {q}")
            qa_lines.append(f"Пациент ответил: {a}")
        parts.append("═══ ИСТОРИЯ ДИАЛОГА ═══\n" + "\n".join(qa_lines))

    if session.rounds:
        prev_rounds = []
        for i, r in enumerate(session.rounds, 1):
            summary = f"[Раунд {i}] Координатор: {r.coordinator_response[:500]}"
            if r.coordinator_question:
                summary += f"\n  → Вопрос раунда {i}: {r.coordinator_question}"
            prev_rounds.append(summary)
        parts.append("═══ ПРЕДЫДУЩИЕ РАУНДЫ ═══\n" + "\n".join(prev_rounds))

    return "\n\n".join(parts)


# ── Data Package ──────────────────────────────────────────────────────────────

def _profile_lens_lines(med: dict, treatment: str, sources: list[str]) -> list[str]:
    """Строки диагноза/лечения/ПЭТ-КТ — ТОЛЬКО при данных у тенанта (правило, как в брифе:
    patient_context.build_patient_brief, нейтрализация 07.2026). До 24.09 они печатались всем:
    человек без диагноза получал «Последний ПЭТ-КТ: [результат не задан]» — онко-рамку от шаблона.
    Устройства — по фактически пришедшим данным (metrics_db.data_sources), не по полю профиля;
    ручное поле профиля показывается рядом, если заполнено (там бывают приборы без данных)."""
    # empty-profile (24.09): диагноз И список проблем — из одного дома всех врачебных контекстов;
    # до этого консилиум видел только поле диагноза, а проблемы второго тенанта — ни одной.
    import patient_context as _pc
    unset = _pc.is_unset
    out = [f"  {l}" for l in _pc.medical_frame_lines(med)]
    if not unset(treatment):
        out.append(f"  Лечение: {treatment}")
    pet_d, pet_r = med.get("last_pet_ct"), med.get("last_pet_ct_result")
    if not unset(pet_d) or not unset(pet_r):
        out.append(f"  Последний ПЭТ-КТ ({pet_d or 'дата не указана'}): {pet_r or 'результат не указан'}")
    out.append("  Данные за 30 дней приходят от: "
               + (", ".join(sources) if sources else "ни одного источника"))
    dev = med.get("devices")
    if not unset(dev):
        out.append(f"  Устройства (со слов, профиль): {dev}")
    return out


def _build_data_package(
    end_date: date = None,
    period_days: int = 7,
    session: ConsultationSession = None,
    current_user_message: str = "",
) -> str:
    """Строит пакет данных для специалистов из SQLite + история сессии."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    import health_db as db
    db.init_db()

    if end_date is None:
        end_date = get_today() - timedelta(days=1)

    stats7  = db.get_stats(7,  end_date)
    stats30 = db.get_stats(30, end_date)
    stats90 = db.get_stats(90, end_date)
    _all_metrics = db.render_all_metrics(30, end_date)

    # Ручного списка аналитов здесь БОЛЬШЕ НЕТ (13.09.2026). Он был — 18 имён, и PSA в
    # нём не было: урология в MDT написала «PSA не измерялся» при свежей строке
    # в каноне, GP повторил это в недельном отчёте, и сторож отчёт отклонил. Тот же
    # класс, что снятый из gp_context 30.08 allowlist: специалист судит по тому, что ему
    # показали, и отсутствие строки читает как отсутствие анализа. Плюс сам список был
    # диагноз-линзой в коде — CEA/CA19-9 объявлены важными любому тенанту (§9).
    import labs_db as _ldb
    LAB_WINDOW_DAYS = _ldb.PROMPT_WINDOW_DAYS   # один дом окна на все промпт-срезы
    recent_labs = db.get_recent_labs(LAB_WINDOW_DAYS)
    lab_date = max((l["date"] for l in recent_labs), default="нет данных")

    profile = db.get_profile_context()
    med   = profile.get("medical", {})
    ident = profile.get("identity", {})
    routine = profile.get("routine", {})

    days_table = []
    for i in range(1, 8):
        d = end_date - timedelta(days=i)
        row = db.get_day(str(d))
        s = row.get("sleep") or {}
        hrv = (row.get("hrv") or {}).get("avg")
        stress = row.get("stress") or {}
        _sh = stress.get("stress_high")
        _rh = stress.get("recovery_high")
        stress_min = int(_sh / 60) if _sh is not None else None
        recovery_min = int(_rh / 60) if _rh is not None else None
        # Проверяем контекстные события для этого дня
        ctx_events = db.get_context_events(str(d), str(d))
        ctx_note = ""
        if ctx_events:
            tags = [e.get("key", e.get("category", "")) for e in ctx_events]
            ctx_note = f" ⚑ {', '.join(tags)}"
        days_table.append(
            f"  {d.strftime('%d.%m')} | сон {s.get('totalSleep', '—')}ч | "
            f"deep {fmt_min(s.get('deep'))}м | "
            f"ВСР {f'{hrv:.0f}' if hrv else '—'} мс | "
            f"score {s.get('sleep_score', '—')} | "
            f"шаги {row.get('steps', '—')} | "
            f"стресс {fmt_or(stress_min)}м/восст {fmt_or(recovery_min)}м{ctx_note}"
        )

    lines = []

    # Текущий вопрос пациента — самое первое
    if current_user_message and current_user_message.strip():
        lines += [
            "═══════════════════════════════════════",
            "❓ ВОПРОС/СООБЩЕНИЕ ПАЦИЕНТА (ответь на него в первую очередь):",
            current_user_message.strip(),
            "═══════════════════════════════════════",
            "",
        ]

    # История диалога
    if session and (session.user_qa or session.rounds):
        lines += [_build_session_history(session), ""]

    try:
        import treatment_summary as _ts
        _tx_line = _ts.treatment_text(fallback=med.get('treatment') or "")
    except Exception:
        _tx_line = med.get('treatment') or ""
    # empty-profile (24.09): без даты рождения пакет ПАДАЛ (fromisoformat('[дата не задана]')) —
    # консилиум у человека, пропустившего дату, не собирался вовсе.
    _bd = ident.get('birth_date')
    try:
        _age_s = f"{_calc_age(_bd)} лет (р. {_fmt_bday(_bd)})"
    except (TypeError, ValueError):
        _age_s = "возраст не указан"
    def _t(v, suf=""):   # «None» литералом в промпт не едет
        return "—" if v is None else f"{v}{suf}"
    lines += [
        "=== МЕДИЦИНСКИЕ ДАННЫЕ ПАЦИЕНТА ===",
        "",
        "ПРОФИЛЬ ПАЦИЕНТА:",
        f"  Имя: {ident.get('name') or 'не указано'}",
        # Пол — из профиля (identity.sex), не литерал: до 24.09 здесь стояло «мужской» для любого
        # тенанта (BL-SEX-LITERAL-1).
        f"  Пол: {consilium_roster.sex_label(ident.get('sex'))}, {_age_s}",
        f"  Рост: {_t(ident.get('height_cm'), ' см')}, Вес: {_t(ident.get('weight_kg'), ' кг')}",
        *_profile_lens_lines(med, _tx_line, db.data_sources(30, end_date)),
        "",
        f"ВИТАЛЬНЫЕ ПОКАЗАТЕЛИ (последние {period_days} дней):",
        "  Дата | Сон | Deep | ВСР | Score | Шаги | Стресс/Восст",
        *days_table,
        "",
        "ТРЕНДЫ (7д → 30д → 90д avg):",
        f"  Сон:       {_t(stats7.get('avg_sleep'), 'ч')} → {_t(stats30.get('avg_sleep'), 'ч')} → {_t(stats90.get('avg_sleep'), 'ч')}",
        f"  Deep сон:  {fmt_min(stats7.get('avg_deep'))}м → {fmt_min(stats30.get('avg_deep'))}м → {fmt_min(stats90.get('avg_deep'))}м",
        f"  ВСР:       {_t(stats7.get('avg_hrv'))} → {_t(stats30.get('avg_hrv'))} → {_t(stats90.get('avg_hrv'))} мс",
        f"  Readiness: {_t(stats7.get('avg_readiness'))} → {_t(stats30.get('avg_readiness'))} → {_t(stats90.get('avg_readiness'))}",
        "",
        # BL-DATA-PARITY-1: всё собранное, а не только ручная таблица выше.
        *([_all_metrics, ""] if _all_metrics else []),
        f"ЛАБОРАТОРНЫЕ ДАННЫЕ (последний забор: {lab_date}):",
    ]

    # Референсы лаб — единый источник из БД (как в gp_agent). Без локального дикта.
    import health_db as _db
    LAB_REFS = _db.get_lab_refs()         # кэш моды бланков (norm-from-documents), не литерал
    for lab in recent_labs:
        v = lab.get("value")
        lo, hi = lab.get("ref_low"), lab.get("ref_high")
        ref = ((lo, hi, lab.get("unit") or "") if (lo is not None or hi is not None)
               else LAB_REFS.get(lab["test_name"]) or LAB_REFS.get(lab["test_name"].replace("_", "-")))
        flag = ""
        if v is not None and ref and ((ref[0] is not None and v < ref[0]) or (ref[1] is not None and v > ref[1])):
            flag = " ⚠ ВНЕ НОРМЫ"
        unit = (ref[2] if ref else None) or lab.get("unit") or ""
        norm = f"(референс {ref[0]}–{ref[1]})" if ref else "(референс не установлен)"
        # result_text, а не value: со снятым allowlist сюда доезжают качественные строки
        # (value=None), и f"{None:>8}" уронил бы сборку контекста всего консилиума.
        # Плюс это единственная граница §19 — сырое написание с бланка не едет в промпт.
        shown = _ldb.result_text(lab)
        unit = "" if v is None else unit
        lines.append(f"  {lab['test_name']:18} {shown:>8} {unit:12} {norm}{flag}")
    _win_note = _ldb.declared_boundary(LAB_WINDOW_DAYS, unjudged=True)   # отказ сборки — сказан в тексте
    if _win_note:
        lines.append("  " + _win_note)

    # Профиль пациента — единый рантайм-источник из БД (Ф3). Никаких литералов
    # ВСР/генетики/режима: ВСР и вагус — в брифе; геном — отдельным блоком ниже.
    import patient_context as _pc
    lines += [
        "",
        "ПРОФИЛЬ ПАЦИЕНТА (из БД):",
        _pc.build_patient_brief(),
    ]
    # доп. нарративный контекст из med, только если задан в БД (без литералов)
    if med.get("hrv_context"):
        lines.append("  - " + med["hrv_context"])
    if med.get("sleep_context"):
        lines.append("  - " + med["sleep_context"])

    # Геномный контекст
    try:
        import genome_context as gc
        genome_block = gc.build_genetic_context_block(max_variants=60)
        if genome_block:
            lines += ["", genome_block]
    except Exception as e:
        log.warning(f"Геномный контекст для MDT: {e}")

    return "\n".join(lines)


# ── Round A: независимые мнения ───────────────────────────────────────────────

async def _call_medical_specialist_async(
    client: anthropic.AsyncAnthropic,
    name: str,
    data_package: str,
    semaphore: asyncio.Semaphore,
    round_a_opinions: dict | None = None,
) -> dict:
    """Один медицинский специалист — один async вызов."""
    async with semaphore:
        spec_prompt = _read_specialist_prompt(name)
        round_label = "B" if round_a_opinions else "A"

        system = (
            f"{spec_prompt}\n\n"
            "ВАЖНО: Отвечай ТОЛЬКО на русском языке. "
            "Анализируй только по своей специальности. "
            "Если в данных есть блок ❓ ВОПРОС ПАЦИЕНТА — ответь на него "
            "с точки зрения своей специальности в первую очередь. "
            "Это главное. Остальное — дополнение. "
            "Клинический статус пациента — в блоке профиля выше; опирайся на него, не додумывай. "
            "Будь конкретен: что нормально, что вызывает вопросы. "
            "4–8 предложений."
        )

        content = data_package
        if round_a_opinions:
            filtered = {k: v for k, v in round_a_opinions.items() if k != name}
            opinions_block = "\n\n".join(f"=== {k} ===\n{v}" for k, v in filtered.items())
            # Извлекаем исходный вопрос для явного напоминания в Round B
            _q_anchor = ""
            for _line in data_package.split("\n"):
                if "ВОПРОС/СООБЩЕНИЕ ПАЦИЕНТА" in _line:
                    _idx = data_package.index(_line)
                    _block = data_package[_idx:_idx+500].split("═══")[0]
                    _q_anchor = _block.replace(
                        "❓ ВОПРОС/СООБЩЕНИЕ ПАЦИЕНТА (ответь на него в первую очередь):", ""
                    ).strip()
                    break
            _q_reminder = (
                f"\n⚠ ИСХОДНЫЙ ВОПРОС ПАЦИЕНТА (главный фокус Round B): {_q_anchor}\n\n"
                if _q_anchor else ""
            )
            content = (
                data_package + "\n\n"
                + _q_reminder
                + "═══ МНЕНИЯ КОЛЛЕГ (Раунд A) ═══\n" + opinions_block + "\n\n"
                "Твоя задача (Раунд B): ответь на вопрос пациента с учётом мнений коллег "
                "по своей специальности. Укажи связи, которые они могли упустить. "
                "Если видишь конфликт — не молчи. Несогласие ценно."
            )

        try:
            response = await asyncio.wait_for(
                client.messages.create(task="wellally_consult._call_medical_specialist_async",
                    model=hai_core.model_for("consult_specialist"),
                    max_tokens=400,
                    system=system,
                    messages=[{"role": "user", "content": content}],
                ),
                timeout=300.0,   # Opus медленнее; live-consult не чувствителен к задержке
            )
            log.info(f"  {name} (Раунд {round_label}): OK")
            return {"name": name, "opinion": llm_client.answer_text(response), "ok": True}
        except asyncio.TimeoutError:
            log.error(f"  {name} (Раунд {round_label}): TIMEOUT")
            return {"name": name, "opinion": "Таймаут ответа.", "ok": False}
        except Exception as e:
            log.error(f"  {name} (Раунд {round_label}): {type(e).__name__}: {e}")
            return {"name": name, "opinion": f"Ошибка: {e}", "ok": False}


async def _run_deliberation_round(
    client: anthropic.AsyncAnthropic,
    data_package: str,
    session: ConsultationSession,
    round_a_opinions: dict | None = None,
) -> dict:
    """
    Запускает один раунд для всех 13 участников параллельно.
    round_a_opinions=None → Раунд A
    round_a_opinions=dict → Раунд B
    Возвращает {participant_name: opinion_text}
    """
    round_label = "B" if round_a_opinions else "A"
    log.info(f"Раунд {round_label}: запуск 13 участников...")

    # Общий семафор на ВСЕ 13 участников (9 medical + 4 lifestyle).
    # Ограничивает одновременные API-вызовы к Anthropic — защита от rate-limit.
    semaphore = asyncio.Semaphore(6)

    async def _guarded(coro):
        """Оборачивает любую корутину в общий semaphore."""
        async with semaphore:
            return await coro

    # Медицинские специалисты
    medical_tasks = [
        _call_medical_specialist_async(client, name, data_package, semaphore, round_a_opinions)
        for name in _medical_specialists()
    ]

    # Lifestyle-коучи
    import lifestyle_agents as la
    sleep_date    = session.sleep_date    or get_today()
    activity_date = session.activity_date or get_today() - timedelta(days=1)

    # Извлекаем первое сообщение пациента как вопрос для коучей
    patient_q = ""
    if session.user_qa:
        # последний вопрос координатора + последний ответ пациента
        last_q, last_a = session.user_qa[-1]
        patient_q = f"Вопрос координатора: {last_q}\nОтвет пациента: {last_a}"
    elif round_a_opinions is None:
        # Первый раунд — берём из data_package первое сообщение
        for line in data_package.split("\n"):
            if "ВОПРОС/СООБЩЕНИЕ ПАЦИЕНТА" in line:
                idx = data_package.index(line)
                patient_q = data_package[idx:idx+300].split("═══")[0].strip()
                break

    lifestyle_tasks = [
        _guarded(agent.generate_mdt_opinion(
            sleep_date=sleep_date,
            activity_date=activity_date,
            patient_question=patient_q,
            round_a_opinions=round_a_opinions,
            client=client,
        ))
        for agent in la.AGENTS
    ]

    # Запускаем всё параллельно (все 13 через общий semaphore=6)
    medical_results, *lifestyle_opinions = await asyncio.gather(
        asyncio.gather(*medical_tasks),
        *lifestyle_tasks,
        return_exceptions=True,
    )

    opinions = {}

    for r in medical_results:
        if r.get("ok"):
            opinions[r["name"]] = r["opinion"]
        else:
            opinions[r["name"]] = r.get("opinion", "Нет ответа")

    for agent, opinion in zip(la.AGENTS, lifestyle_opinions):
        if isinstance(opinion, Exception):
            log.error(f"  {agent.agent_name} (Раунд {round_label}): {opinion}")
            opinions[agent.agent_name] = f"Ошибка: {opinion}"
        else:
            opinions[agent.agent_name] = opinion
            log.info(f"  {agent.agent_name} (Раунд {round_label}): OK")

    log.info(f"Раунд {round_label} завершён: {len(opinions)}/13 ответов")
    return opinions


# ── Coordinator ───────────────────────────────────────────────────────────────

def _load_epistemic_coord() -> str:
    """Дисциплина синтеза координатора, если включён флаг EPISTEMIC_DISCIPLINE.

    Default off → "" (поведение координатора не меняется). Fail-fast при флаге on.
    """
    import os
    if os.environ.get("EPISTEMIC_DISCIPLINE", "on").strip().lower() in ("0", "off", "false", "no"):
        return ""
    from epistemic_skill.loader import load_part
    return load_part("coordinator.txt").text + "\n\n"

async def _call_coordinator_async(
    client: anthropic.AsyncAnthropic,
    opinions_b: dict,
    data_package: str,
    session: ConsultationSession,
) -> str:
    """Координатор синтезирует Раунд B и ведёт диалог."""
    coord_prompt = _read_specialist_prompt("consultation-coordinator")
    system = (
        f"{coord_prompt}\n\n"
        f"{COORDINATOR_DIALOGUE_PROMPT}\n\n"
        f"{_load_epistemic_coord()}"
        "Отвечай ТОЛЬКО на русском языке."
    ) + hai_core.answer_language()

    opinions_text = "\n\n".join(
        f"=== {name.upper()} ===\n{opinion}"
        for name, opinion in opinions_b.items()
    )

    # Добавляем краткий data summary (не весь пакет)
    data_summary_lines = []
    for line in data_package.split("\n"):
        if any(k in line for k in ["ВОПРОС", "ИСТОРИЯ", "ТРЕНДЫ", "Readiness", "ВСР", "Deep"]):
            data_summary_lines.append(line)
    data_summary = "\n".join(data_summary_lines[:20])

    # Извлекаем исходный вопрос пациента — явно в начало контекста координатора
    _orig_q = ""
    for _line in data_package.split("\n"):
        if "ВОПРОС/СООБЩЕНИЕ ПАЦИЕНТА" in _line:
            _idx = data_package.index(_line)
            _blk = data_package[_idx:_idx+500].split("═══")[0]
            _orig_q = _blk.replace(
                "❓ ВОПРОС/СООБЩЕНИЕ ПАЦИЕНТА (ответь на него в первую очередь):", ""
            ).strip()
            break
    if not _orig_q and session.user_qa:
        _orig_q = session.user_qa[0][0]  # первый вопрос сессии как fallback
    _q_header = (
        f"❗ ИСХОДНЫЙ ВОПРОС ПАЦИЕНТА (ответ обязателен как первый пункт):\n{_orig_q}\n\n"
        if _orig_q else ""
    )
    user_content = (
        _q_header
        + f"ДАННЫЕ (краткое):\n{data_summary}\n\n"
        + f"МНЕНИЯ УЧАСТНИКОВ КОНСИЛИУМА (Раунд B):\n{opinions_text}"
    )

    ok_count = sum(1 for v in opinions_b.values() if v and "Ошибка" not in v and "Таймаут" not in v)
    log.info(f"Координатор: синтез {ok_count}/13 мнений...")

    try:
        response = await asyncio.wait_for(
            client.messages.create(task="wellally_consult._call_coordinator_async",
                model=hai_core.model_for("consult_coordinator"),
                max_tokens=2048,
                system=system,
                messages=[{"role": "user", "content": user_content}],
            ),
            timeout=600.0,   # Opus-синтез 16k, задержка неважна
        )
        return llm_client.answer_text(response)
    except asyncio.TimeoutError:
        log.error("Координатор: TIMEOUT (90s)")
        raise RuntimeError("Координатор не ответил за 90 секунд")


# ── Main Entry Point ──────────────────────────────────────────────────────────

async def run_consultation_cycle_async(
    user_message: str,
    session: ConsultationSession,
    end_date: date = None,
    period_days: int = 7,
) -> tuple[str, ConsultationSession]:
    """
    Один цикл диалоговой консультации.

    Args:
        user_message: текущее сообщение пациента (вопрос или ответ на вопрос координатора)
        session: текущее состояние сессии
        end_date: конец периода данных (default: вчера)
        period_days: период для таблицы дней

    Returns:
        (coordinator_response, updated_session)
    """
    if end_date is None:
        end_date = get_today() - timedelta(days=1)

    # Обновляем даты в сессии
    session.sleep_date    = get_today()
    session.activity_date = end_date

    log.info(f"=== Цикл консультации: '{user_message[:60]}...' ===")

    # 1. Собираем данные (refresh происходит снаружи через asyncio.to_thread)
    data_package = _build_data_package(
        end_date=end_date,
        period_days=period_days,
        session=session,
        current_user_message=user_message,
    )

    client = _get_async_client()

    # 2. Фиксируем Q&A ДО раунда — специалисты должны видеть историю диалога
    if session.rounds:
        last_q = session.rounds[-1].coordinator_question
        session.user_qa.append((last_q, user_message))
        # Пересобираем data_package с обновлённой историей
        data_package = _build_data_package(
            end_date=end_date,
            period_days=period_days,
            session=session,
            current_user_message=user_message,
        )

    # 3. Раунд A — все 13 независимо
    opinions_a = await _run_deliberation_round(client, data_package, session, round_a_opinions=None)

    # 4. Раунд B — все 13 видят Раунд A
    opinions_b = await _run_deliberation_round(client, data_package, session, round_a_opinions=opinions_a)

    # 5. Координатор синтезирует
    coordinator_response = await _call_coordinator_async(client, opinions_b, data_package, session)

    new_round = ConsultationRound(
        opinions_a=opinions_a,
        opinions_b=opinions_b,
        coordinator_response=coordinator_response,
        # Последний вопрос извлекаем из ответа (последнее предложение с "?")
        coordinator_question=_extract_question(coordinator_response),
    )
    session.rounds.append(new_round)

    log.info(f"=== Цикл завершён. Раундов в сессии: {len(session.rounds)} ===")
    return coordinator_response, session


def _extract_question(text: str) -> str:
    """Извлекает последний вопрос из текста координатора."""
    sentences = [s.strip() for s in text.replace("\n", " ").split(".") if s.strip()]
    for sentence in reversed(sentences):
        if "?" in sentence:
            return sentence.strip()
    return ""


# ── Legacy API (backward compatibility) ──────────────────────────────────────

async def run_mdt_consultation_async(
    end_date: date = None,
    period_days: int = 7,
    specialists: list = None,
    user_question: str = "",
) -> dict:
    """
    Legacy single-shot consultation (для совместимости).
    Используй run_consultation_cycle_async() для диалогового режима.
    """
    session = ConsultationSession()
    response, session = await run_consultation_cycle_async(
        user_message=user_question or "Общая консультация по текущему состоянию.",
        session=session,
        end_date=end_date,
        period_days=period_days,
    )
    opinions_flat = [
        {"specialist": name, "opinion": op, "ok": "Ошибка" not in op}
        for name, op in session.rounds[-1].opinions_b.items()
    ]
    return {
        "opinions": opinions_flat,
        "synthesis": response,
        "data_date": str(end_date or get_today() - timedelta(days=1)),
    }


def run_mdt_consultation(
    end_date: date = None,
    period_days: int = 7,
    specialists: list = None,
) -> dict:
    """Синхронная обёртка для скриптов и cron."""
    return asyncio.run(run_mdt_consultation_async(end_date, period_days, specialists))
