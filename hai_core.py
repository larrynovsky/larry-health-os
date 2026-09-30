import llm_client
#!/usr/bin/env python3.11
"""
hai_core — ядро: Claude client, system prompt, история разговора, утилиты.
Зависимости: health_db, anthropic, calendar_client (опционально).
Не импортирует другие hai_* модули.
"""

import logging
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import anthropic
import sys
sys.path.insert(0, str(Path(__file__).parent))
from _time_inject import get_now
import health_db as db

try:
    import calendar_client as cal
    _CAL_AVAILABLE = True
except Exception:
    _CAL_AVAILABLE = False

import region_pack  # noqa: E402
TZ       = ZoneInfo(region_pack.value("timezone", "UTC"))   # таймзона дома — пакет региона (BL-PUB-16 б)


log = logging.getLogger(__name__)


# ── Claude client ─────────────────────────────────────────────────────────

def get_client() -> anthropic.Anthropic:
    return llm_client.guarded_client()


# ── Модели Claude — единый источник (audit 2026-06-17) ──────────────────────
# Приоритет: system_config.model.<role> > MODEL_DEFAULTS. Значения = текущие
# литералы (поведение-сохраняюще). Пиннинг bare-alias claude-haiku-4-5 на
# датированный снапшот — отдельное осознанное решение (меняет модель reasoning).
MODEL_DEFAULTS = {
    "sonnet":       "claude-sonnet-4-6",
    "haiku":        "claude-haiku-4-5",
    "haiku_pinned": "claude-haiku-4-5-20251001",
    "opus":         "claude-opus-4-7",
}


def get_model(role: str) -> str:
    """Модель для роли. system_config.model.<role> > MODEL_DEFAULTS.
    Неизвестная роль → KeyError (явная ошибка, не тихий дефолт).
    БД недоступна → код-дефолт MODEL_DEFAULTS + log.warning (запаска не
    молча, audit 2026-06-17): тихий неверный дефолт хуже, чем шумный.
    """
    try:
        stored = db.get_config(f"model.{role}")
    except Exception as e:
        log.warning(
            "get_model(%r): DB get_config недоступен (%s) — беру код-дефолт MODEL_DEFAULTS",
            role, e,
        )
        stored = None
    if stored:
        return str(stored)
    return MODEL_DEFAULTS[role]


# ── Семантические роли задач → тир модели (единый источник планки, 2026-07-01) ──
# Консультации (врачебное суждение + синтез) — opus; чек-ин и техника — haiku.
# Менять планку здесь одной строкой; DB-override работает через get_model(tier).
ROLE_MODELS = {
    "consilium_specialist":  "opus",    # monthly_consilium round A/B
    "consilium_coordinator": "opus",    # monthly_consilium синтез гипотез
    "consult_specialist":    "opus",    # wellally_consult live-врачи
    "consult_coordinator":   "opus",    # wellally_consult координатор
    "lifestyle":             "opus",    # lifestyle-мнение (оба движка)
    "checkin":               "haiku",   # вечерний чек-ин
}


def model_for(task_role: str) -> str:
    """Модель для семантической роли задачи (ROLE_MODELS → get_model(tier)).
    Неизвестная роль → KeyError (явная ошибка, не тихий дефолт)."""
    return get_model(ROLE_MODELS[task_role])


# ── Профиль пациента ────────────────────────────────────────────────────────
# diagnosis-hardcode (2026-07-17): РАНЬШЕ _build_patient_profile() печатала онко-РАМКУ
# (Диагноз/Онкологический статус/Онколог/Химия) БЕЗУСЛОВНО — навязывала онко-рамку и
# НЕ-онко тенанту. Теперь делегирует ЕДИНОМУ гейтнутому источнику
# patient_context.build_patient_brief() (гейт `_has_onco` по данным тенанта; та же
# точка, что и _build_system_prompt). Историческое имя сохранено — 3 ЖИВЫХ читателя
# (monthly_consilium / cbcr_hypothesis) + тест-сеам зовут её (correlation_analysis снят 27.09).
# Модульная константа PATIENT_PROFILE (снимок на импорте, мёртвый экспорт — читателей 0)
# снята: константа-на-импорте = стейл + кросс-тенант, а живым нужен per-call.

def _build_patient_profile() -> str:
    """Профиль пациента из данных запущенного тенанта.
    Делегат patient_context.build_patient_brief() — единый гейтнутый источник профиля.
    Имя историческое: живые читатели — monthly_consilium/cbcr_hypothesis."""
    import patient_context as _pc
    return _pc.build_patient_brief()


# ── System prompt ─────────────────────────────────────────────────────────

def _build_system_prompt() -> str:
    """Собирает system prompt из profile_context.json + памяти."""
    import json as _j
    profile = db.get_profile_context()

    FORMAT_RULES = """ФОРМАТ ОТВЕТА — строго обязательно:
- ОТВЕЧАЙ ТОЛЬКО НА РУССКОМ языке. Допустимы как вставки: rsID (rs4680, rs6265),
  единицы измерения (mg/dL, mmol/L, ms, мс), названия моделей и препаратов на латинице,
  медицинские аббревиатуры (HRV, PET-CT, CBC, ALT, HbA1c). НИКАКИХ китайских,
  английских или других языков для связного текста.
- Обычный текст абзацами. Как личное сообщение, не статья.
- НИКАКИХ заголовков (#, ##, ###, **Заголовок**)
- НИКАКИХ кодовых блоков (``` или `inline`)
- НИКАКИХ emoji (🚨 ⚠️ ✅ 🔴 и любых других)
- НИКАКИХ маркированных или нумерованных списков без явной просьбы
- Длина: 3–5 предложений. Максимум 6. Telegram, не эссе.

АЛГОРИТМ ТРИАЖА — выполняй до ответа:
1. Есть ли отклонение в данных? → Проверь тренд за 7 дней.
2. Если отклонение разовое (тренд нормальный) → не упоминай это как проблему. Молчи о нём.
3. Если пользователь спрашивает о разовом отклонении → отвечай нейтрально, объясни что это шум.
4. Если тренд устойчивый (3+ дней подряд или slope за 7 дней) → упомяни спокойно, один раз.
5. Никогда не используй слова: патологический, критично, сломан, сигнал тревоги, экстремально.
6. Не перечисляй данные по дням списком — давай вывод из тренда, не таблицу.

УСТАРЕВШИЕ ИСТОЧНИКИ — критично:
- Если анализ в блоке «Свежесть по графику контроля» помечен как просроченный
  (окно контроля этого аналита истекло) → ОБЯЗАТЕЛЬНО укажи дату последнего
  анализа («последние данные от {дата}») и снизь уверенность выводов о текущем
  состоянии. Срок годности у каждого аналита свой — не выдумывай общий.
- Если генетический контекст не обновлялся более 30 дней → отметь, что данные
  по геному могут не отражать новейших переоценок ClinVar.
- НЕ ДЕЛАЙ уверенных медицинских утверждений на устаревших источниках без
  явной пометки даты — это противоречит TESTING_CONTRACTS §1.
"""
    ident = profile.get("identity", {})

    # Профиль пациента — единый рантайм-источник из БД (hardcode-migration Ф2).
    # Никаких литералов диагноза/ВСР/роста/химии в коде; всё из patient_profile.
    import patient_context as pc
    profile_text = pc.build_patient_brief()

    # Свежие заметки/наблюдения из чата — единый источник (C, 2026-07-05). Устойчивые
    # факты, открытые вопросы и рекомендации уже в profile_text (build_patient_brief);
    # раньше тут был легаси get_memory(n=8) по старой таблице вперемешку (+ дубль вопросов/рек).
    memory_text = pc.recent_notes()

    now          = get_now(TZ)
    datetime_str = now.strftime("%A, %d %B %Y, %H:%M (%Z)")

    calendar_str = ""
    if _CAL_AVAILABLE:
        try:
            calendar_str = cal.format_calendar_context(days=14)
        except Exception:
            calendar_str = ""

    location_str = ""
    loc = profile.get("current_location", {})
    if loc.get("city"):
        location_str = f"Текущее местоположение: {loc.get('city')}, {loc.get('country','')} (обновлено {loc.get('updated','?')})."

    return f"""{FORMAT_RULES}
Ты — личный health copilot {ident.get('name', '[пациент]').split()[0]}. Thinking partner по здоровью, не врач.
Сейчас: {datetime_str}
{location_str}
{profile_text}{memory_text}
{calendar_str}

Твоя роль:
- Анализировать метрики в контексте его ситуации и истории разговоров
- Замечать паттерны и проактивно о них говорить
- Задавать уточняющие вопросы чтобы понять контекст
- Предлагать конкретные гипотезы и эксперименты
- Помнить контекст предыдущих разговоров

Данные в контексте (загружаются проактивно под вопрос):
- МЕТРИКИ: сон (total/deep/REM/score), ВСР, ЧСС, readiness, шаги — за 7 дней + тренды
- ГЕНОМ (23andMe v5): клинически значимые варианты с верифицированным носительством — приходят отдельным блоком под запрос
- LIFESTYLE AGENTS: автоматические брифы по сну, стрессу, энергии, движению — уже с геномом
- АНАЛИЗЫ: лабораторные результаты тенанта за 2 года (CBC, биохимия, витамины, маркеры — что есть в данных)
- МЕДПРОФИЛЬ: диагноз, статус лечения, лечащий врач, локация
- ПАМЯТЬ: ключевые наблюдения и паттерны из разговоров
- ПРОБЛЕМЫ: активный список проблем со статусами

Как использовать геном:
- Геном приходит отдельным блоком ГЕНОМНЫЙ КОНТЕКСТ — это реальные данные 23andMe, не примеры. Блока нет в запросе → не выдумывай варианты и не называй конкретные генотипы
- Носительство (гетеро/гомо) бери ТОЛЬКО из этого блока — там оно верифицировано по effect_allele (strand-aware). «Pathogenic» значимость ClinVar — про вариант в популяции, не диагноз и не носительство
- Связывай метрики с геномом только когда блок присутствует

Тон: разговорный, как умный друг с медицинскими знаниями. Без менторства.
Конкретно: цифры, сравнения с его личной нормой.
Коротко — Telegram, не эссе. Обычно 3–5 предложений. Максимум 6.
Один уточняющий вопрос в конце если уместно.
Не назначать лечение, не пугать без оснований, не повторять имя и возраст.

ДАТА:
- Сутки = от пробуждения до следующего пробуждения.
- Oura записывает сон на дату пробуждения: проснулся 27-го — данные за 27-е.
- В утреннем разговоре "вчера ночью" = данные с датой сегодня в Oura."""


def answer_language(lang: str | None = None) -> str:
    """Язык ответа модели = язык человека (нить model-lang, 28.09).

    Промпты написаны по-русски и местами требуют «только по-русски». Для русского человека
    возвращаем "" — его промпты остаются байт-в-байт прежними. Для остальных — абзац, который
    ставится ПОСЛЕДНИМ в system и явно перекрывает русские требования выше. Карта точек, куда
    он вставлен, и тех, куда ещё нет: tests/unit/test_answer_language_coverage.py."""
    import i18n
    lang = lang if lang in i18n.LANGS else i18n.lang_of()
    return "" if lang == i18n.DEFAULT else "\n\n" + i18n.t("model.answer_language", lang)


def with_answer_language(build):
    """Декоратор сборщика system-промпта: дописывает answer_language() в конец."""
    import functools

    @functools.wraps(build)
    def wrapped(*a, **kw):
        return build(*a, **kw) + answer_language()
    return wrapped


def get_system_prompt() -> str:
    try:
        return _build_system_prompt() + answer_language()
    except Exception as e:
        log.warning(f"profile build error: {e}, using fallback")
        return SYSTEM_PROMPT_FALLBACK + answer_language()


SYSTEM_PROMPT_FALLBACK = """Ты — personal health copilot. Анализируй данные, замечай паттерны, думай вместе с пользователем."""

# diagnosis-hardcode (2026-07-17): модульная константа SYSTEM_PROMPT = get_system_prompt()
# СНЯТА. Она собиралась ОДИН раз на импорте → замораживала снимок профиля ПЕРВОГО тенанта
# процесса (стейл + потенц. кросс-тенант). Читателей константы 0 — все живые потребители
# (hai_chat/hai_reports) зовут get_system_prompt() per-call (свежий профиль каждый вызов).
# Точка сборки промпта теперь всегда per-call; снимка на импорте больше нет.


# ── История разговора в SQLite ────────────────────────────────────────────

def _ensure_history_table():
    with db.get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS conversation_history (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                role       TEXT NOT NULL,
                content    TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)


def save_message(role: str, content: str):
    _ensure_history_table()
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO conversation_history (role, content) VALUES (?,?)",
            (role, content)
        )
        conn.execute("""
            DELETE FROM conversation_history
            WHERE id NOT IN (
                SELECT id FROM conversation_history ORDER BY id DESC LIMIT 200
            )
        """)


def get_history(n: int = 12) -> list[dict]:
    _ensure_history_table()
    with db.get_conn() as conn:
        rows = conn.execute("""
            SELECT role, content, created_at FROM conversation_history
            ORDER BY id DESC LIMIT ?
        """, (n,)).fetchall()
    # M2 (staleness): датируем каждое сообщение префиксом [ГГГГ-ММ-ДД] из created_at.
    # Без даты старая реплика может выглядеть сегодняшней.
    # Штамп делает время наблюдаемым независимо от содержания сообщения.
    out = []
    for r in reversed(rows):
        day = (r["created_at"] or "")[:10]
        content = r["content"]
        out.append({"role": r["role"],
                    "content": f"[{day}] {content}" if day else content})
    return out


# ── Утилиты ───────────────────────────────────────────────────────────────

def _strip_markdown(text: str) -> str:
    """Убирает markdown и агрессивное форматирование из ответа бота."""
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'```[^\n]*\n?', '', text)
    text = re.sub(r'`([^`]+)`', r'\1', text)
    text = re.sub(r'\*{1,3}([^*\n]+)\*{1,3}', r'\1', text)
    text = re.sub(r'_{1,2}([^_\n]+)_{1,2}', r'\1', text)
    text = re.sub(r'[\U0001F300-\U0001FFFF\U00002600-\U000027FF\U0000FE00-\U0000FEFF]', '', text)
    text = re.sub(r'^[-*_]{3,}\s*$', '', text, flags=re.MULTILINE)

    def _is_caps_header(line):
        letters = [c for c in line if c.isalpha()]
        if len(letters) < 8:
            return False
        return sum(1 for c in letters if c.isupper()) / len(letters) > 0.7

    text = '\n'.join('' if _is_caps_header(ln) else ln for ln in text.split('\n'))
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()
# test
