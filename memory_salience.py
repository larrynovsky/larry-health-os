"""
memory_salience.py — сигналы салиентности для сетки decay (Ф4.5).

Общие правила классификации для decay; рабочие списки настраиваются в БД.
Две независимые линии:

  (A) is_durable_fact() — устойчивые факты, ошибочно попавшие в эпизодический
      class `state`. Классификация должна учитывать срок действия факта:
      возраст записи сам по себе не означает, что факт перестал быть верным.
      Устойчивый факт получает never-decay (critical_flag).

  (B) is_symptom() — лексические признаки симптоматики, без утверждения,
      что перечисленные симптомы встречались у конкретного человека.
      Салиентность через РЕЦИДИВ (повторился N раз → важно), НЕ never-decay:
      одиночное старое упоминание может истечь, паттерн ловится повторами.
      Watch-словарь — фундамент для recurrence-линии.

RST: у (A) риск в ОБЕ стороны — ложный негатив (устойчивый факт истёк = потеря данных,
плохо) И ложный позитив (транзиентная метрика помечена never-decay = мусор навсегда,
defeats decay). Данные важнее мусора → лёгкий крен в recall, но БЕЗ широких терминов,
что ловят язык метрик (напр. «стадия» → стадии сна). Проверка должна сочетать
recall на независимо выдуманных примерах и precision-контроль на метриках.

Данные двуязычны (арбитр пишет и RU, и EN) → термины на обоих языках.

⚠️ Настройка списков требует ревью их назначения и границ классификации.
"""
from __future__ import annotations

import memory_config as _mc
import re

# Ниже — КОД-ДЕФОЛТЫ (fallback). Боевой источник — БД (system_config, memory_lexicon.*),
# редактируется владельцем без деплоя. Читатели зовут *_terms()/markers(), не константы.

# ── (A) Устойчивые медфакты → never-decay ─────────────────────────────────────
# Специфичные маркеры; избегаем широких слов, совпадающих с языком метрик.
DURABLE_FACT_MARKERS: tuple[str, ...] = (
    # генетика
    # имени гена здесь нет (25.09): код-дефолт не держит гены владельца; ген в ключе
    # факта (genotype_<ГЕН>) ловится словом genotype.
    "pathogenic", "патогенн", "мутаци", "генотип", "genotype",
    "genetic variant", "генетическ",
    # онкостатус / ремиссия
    "ремисси", "remission", "pet-ct", "pet ct", "metabolic response",
    "метаболическ ответ", "онкомаркер", "tumor marker", "рецидив опухол",
    # решения врача / план наблюдения (конкретные процедуры/решения, не контекст)
    "онколог", "oncologist", "гастроскоп", "gastroscopy", "колоноскоп",
    "colonoscopy", "биопси", "biopsy",
    # NB: «химиотерапия/chemotherapy» НАМЕРЕННО убрано — слишком широко, ловит
    # временной контекст в наблюдениях сна и вопросах («deep sleep during
    # chemotherapy period») как ложный durable. Устойчивый пост-химио статус
    # редок; точность важнее (ложный never-decay = мусор, defeats decay).
)

# ── (B) Частые симптомы → recurrence-watch (НЕ never-decay) ─────────────────────
SYMPTOM_TERMS: tuple[str, ...] = (
    "изжог", "бессонниц", "слабость", "диаре", "запор", "вздути",
    "аппетит", "простуд", "грипп",
    "heartburn", "insomnia", "weakness", "diarrhea", "diarrhoea", "constipation",
    "bloating", "appetite", "common cold", "caught a cold", "have a cold", "had a cold",
    "flu", "influenza",
)

# ── Red-flag: severity бьёт frequency — сурфейсятся ВСЕГДА, даже 1 раз ─────────
# ⚠️ REVIEW: состав — медицинское решение владельца. Стартовый консервативный минимум.
RED_FLAG_TERMS: tuple[str, ...] = (
    "боль в груд", "chest pain", "обморок", "syncope", "потеря сознан",
    "кровотеч", "рвота кровь", "bleeding", "hemoptys", "melena", "мелена",
    "одышк", "shortness of breath", "dyspnea", "не могу дышать",
    "судорог", "seizure", "внезап", "sudden onset",
)

_NEG_MARKERS = ("нет", "без", "отсутств", "no ", "without", "absence", "denies", "отриц", "isn't", "not ")


def _negated(text_low: str, term: str) -> bool:
    """Грубый отсев отрицания: «изжоги нет», «без изжоги», «отсутствие ... одышки»,
    «no heartburn» → не случай. Окно ~32 симв. до (ловит негатор в начале списка симптомов,
    как «отсутствие кашля, одышки») + короткое после. Для red-flag ложный ПРОПУСК (не флагать
    отсутствующее) важнее ложного флага — эрозия доверия к тревоге хуже."""
    idx = text_low.find(term)
    if idx < 0:
        return False
    before = text_low[max(0, idx - 32):idx]
    after = text_low[idx + len(term):idx + len(term) + 8]
    return any(n in before for n in _NEG_MARKERS) or "нет" in after or " no" in after


# ── Аксессоры: БД (system_config) > код-дефолт. Читатели зовут ЭТИ, не константы ──
def durable_markers() -> tuple[str, ...]:
    return _mc.get_lexicon("durable_fact_markers", DURABLE_FACT_MARKERS)


def symptom_terms() -> tuple[str, ...]:
    return _mc.get_lexicon("symptom_terms", SYMPTOM_TERMS)


def red_flag_terms() -> tuple[str, ...]:
    return _mc.get_lexicon("red_flag_terms", RED_FLAG_TERMS)


def is_red_flag(text: str | None) -> bool:
    """True если текст содержит тревожный симптом (severity-override, не гасится частотой)."""
    if not text:
        return False
    low = text.lower()
    return any(r in low for r in red_flag_terms())


def is_durable_fact(text: str | None) -> bool:
    """(A) True если текст — устойчивый медфакт (генетика/онкостатус/план лечения),
    который НЕ должен истекать по времени. Используется для critical_flag при записи."""
    if not text:
        return False
    low = text.lower()
    return any(m in low for m in durable_markers())


def is_symptom(text: str | None) -> bool:
    """(B) True если текст описывает симптом из watch-словаря владельца. Для recurrence-линии
    (счёт повторов), НЕ для never-decay."""
    if not text:
        return False
    low = text.lower()
    return any(re.search(r"\b" + re.escape(s) + r"\b", low) if s.isascii() else s in low
               for s in symptom_terms())
