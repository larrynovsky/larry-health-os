import llm_client
#!/usr/bin/env python3.11
"""treatment_extractor.py — извлечение онкологических режимов лечения из текста
медицинских документов (encounter.assessment/plan/notes) в таблицу medications.

Назначение: лечение перестаёт быть ручной строкой профиля и становится
производным из документов. Извлекает режим/модальность/intent/циклы; пишет
через health_db.upsert_medication с confirmation='proposed' (человек-гейт).
Счёт циклов — фактический из документа (не сумма упоминаний): накопление = MAX
по режиму делает upsert_medication.

Public API:
    process_treatment(event_id, text, effective_date) -> int   # сколько режимов записано
    extract_all_encounters() -> dict                            # ретро-бэкофилл

Зависимости: health_db, hai_core, anthropic.
"""
import json
import logging
import re
from datetime import date
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))
import hai_core
import health_db as db

log = logging.getLogger(__name__)


# Дешёвый предфильтр: не дёргаем LLM на документах без системной терапии.
# Классы препаратов — по суффиксам МНН (-platin, -tabine, -taxel, -mab, -nib) и русским аналогам;
# плюс имена схем тенанта из его словаря синонимов (health_db._REGIMEN_SYNONYMS, приватные данные).
_THERAPY_RE = re.compile(
    r"хими|chemo|иммунотерап|immunotherap|таргетн|targeted|лучев|radiat|цикл|cycle|курс|"
    r"platin|tabine|taxel|mab\b|nib\b|fluorouracil|5-?fu|платин|табин|таксел|маб\b|ниб\b"
    + "".join("|" + re.escape(k) for k in db._REGIMEN_SYNONYMS),
    re.IGNORECASE,
)

EXTRACTION_PROMPT = """Ты — онкологический аналитик. Из текста медицинского документа извлеки
СИСТЕМНУЮ ПРОТИВООПУХОЛЕВУЮ ТЕРАПИЮ (химио-, иммуно-, таргетную, химиолучевую).

Правила:
- Только реально проведённое/назначенное лечение, упомянутое в тексте. НЕ выдумывай.
- modality: chemo | chemoradiation | immunotherapy | targeted | radiation
  (схема с лучевой терапией = chemoradiation; ингибиторы контрольных точек, моноклональные
  антитела против PD-1/PD-L1 = immunotherapy; цитостатики = chemo)
- intent: neoadjuvant | adjuvant | palliative | maintenance | definitive (если ясно, иначе null)
- cycles_completed: число ТОЛЬКО если в тексте явно сказано «N циклов/курсов/cycles».
  Если указан график (напр. «N мг каждые 3 недели полгода») — cycles_completed=null,
  а график положи в "dose". НЕ оценивай число сам.
- agents: список действующих веществ (cisplatin, fluorouracil, pembrolizumab, ...)
- start_date/end_date: YYYY-MM или YYYY-MM-DD если есть, иначе null
- status: completed | ongoing | stopped

Верни ТОЛЬКО JSON-массив (или [] если терапии нет):
[{"regimen":"GC","agents":["gemcitabine","cisplatin"],"modality":"chemo",
  "intent":"adjuvant","cycles_completed":12,"dose":null,"start_date":"2024-03",
  "end_date":"2024-09","status":"completed"}]"""


def _get_client():
    from anthropic import Anthropic
    return llm_client.guarded_client()


def _mentions_therapy(text: str) -> bool:
    return bool(text and _THERAPY_RE.search(text))


def extract_regimens(text: str, event_id: int) -> list:
    """LLM-извлечение режимов из текста. [] при ошибке/отсутствии."""
    if not _mentions_therapy(text):
        return []
    try:
        client = _get_client()
        resp = client.messages.create(task="treatment_extractor.extract_regimens",
            model=hai_core.model_for("treatment_extraction"),
            max_tokens=700,
            temperature=0,
            system=EXTRACTION_PROMPT,
            messages=[{"role": "user", "content": f"ДОКУМЕНТ:\n{text[:4000]}"}],
        )
        raw = llm_client.answer_text(resp).strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-z]*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw)
        # Робастно: вычленяем JSON-массив, даже если LLM добавил текст вокруг
        # (иначе «Extra data» рушит парс — теряли название схемы на бэкофилле).
        m = re.search(r"\[.*\]", raw, re.DOTALL)
        if m:
            raw = m.group(0)
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except Exception as e:
        log.warning(f"extract_regimens(event_id={event_id}): {e}")
        return []


def _resolve_problem_for_date(effective_date: str) -> str | None:
    """Находит primary_problem_id эпизода, покрывающего дату документа."""
    if not effective_date:
        return None
    try:
        episodes = db.get_episodes()
    except Exception:
        return None
    best = None
    for ep in episodes:
        start = ep.get("start_date") or ""
        end = ep.get("end_date") or "9999-12-31"
        if start <= effective_date <= end:
            best = ep
            break
    if best is None and episodes:
        # ближайший по началу (документ может быть чуть вне границ)
        best = min(episodes, key=lambda e: abs(
            (date.fromisoformat((e.get("start_date") or "1900-01-01")[:10])
             - date.fromisoformat(effective_date[:10])).days))
    return best.get("primary_problem_id") if best else None


def process_treatment(event_id: int, text: str, effective_date: str) -> int:
    """Извлечь режимы из текста документа и upsert-нуть в medications.
    Возвращает число записанных режимов. confirmation='proposed' (человек-гейт)."""
    regimens = extract_regimens(text, event_id)
    if not regimens:
        return 0
    problem_id = _resolve_problem_for_date(effective_date)
    n = 0
    for r in regimens:
        name = (r.get("regimen") or "").strip()
        if not name:
            continue
        cyc = r.get("cycles_completed")
        try:
            cyc = int(cyc) if cyc is not None else None
        except (ValueError, TypeError):
            cyc = None
        try:
            db.upsert_medication(
                name=name,
                modality=r.get("modality"),
                intent=r.get("intent"),
                cycles_completed=cyc,
                agents=r.get("agents") if isinstance(r.get("agents"), list) else None,
                indication_problem_id=problem_id,
                prescribing_event_id=event_id,
                # Дата документа ≠ дата начала лечения: подстановка превращала дату визита в
                # начало курса (замер 04.10). Документ и так связан prescribing_event_id.
                start_date=r.get("start_date"),
                end_date=r.get("end_date"),
                status=r.get("status") or "completed",
                source=f"extractor:{event_id}",
                confirmation="proposed",
                notes=r.get("dose"),
            )
        except ValueError as e:
            # Статус вне словаря (treatment_db.MED_STATUSES): режим не пишем и говорим громко,
            # остальные режимы документа пишутся.
            log.error(f"  treatment: «{name}» пропущен — {e} (encounter #{event_id})")
            continue
        n += 1
        log.info(f"  treatment: {name} ({r.get('modality')}) cyc={cyc} ← encounter #{event_id}")
    return n


def extract_all_encounters() -> dict:
    """Ретроспективный прогон по всем encounters (для бэкофилла)."""
    with db.get_conn() as conn:
        rows = conn.execute("""
            SELECT e.id, e.effective_date,
                   COALESCE(enc.assessment,'') || ' | ' || COALESCE(enc.plan,'') AS txt
            FROM events e JOIN encounters enc ON enc.event_id = e.id
            ORDER BY e.effective_date ASC
        """).fetchall()
    total = 0
    for row in rows:
        n = process_treatment(row["id"], row["txt"], row["effective_date"])
        total += n
        if n:
            print(f"  encounter #{row['id']} ({row['effective_date']}): {n} режим(ов)")
    return {"encounters": len(rows), "regimens_written": total}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    db.init_db()
    print("Ретроспективная экстракция режимов лечения из encounters...")
    result = extract_all_encounters()
    print(f"\nИтог: {result}")
