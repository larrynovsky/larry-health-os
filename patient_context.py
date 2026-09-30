#!/usr/bin/env python3.11
"""
patient_context.py — единый рантайм-источник профиля пациента для промптов.

Инвариант штампа времени на каждом канале памяти (и карта всех каналов с их
оракулами): docs/reference/memory_prompt_channels.md. Каналы отсюда —
pending_chat, recent_notes, _chat_context_line.

Публичные функции: build_patient_brief() -> str; medical_frame_lines(med) -> list[str] и
is_unset(value) -> bool — один дом «медицинской рамки» и «поле не заполнено» для всех врачебных
контекстов (wellally_consult, consult_prep, gp_context; нить empty-profile 24.09).

Принцип (2026-06-17): в промптах НЕТ зашитых данных/клинических величин.
Билдер собирает компактный профиль ТОЛЬКО из БД (patient_profile +
daily_metrics-перцентили). НЕ содержит:
  - генома — он инжектится отдельно через genome_context в data package;
  - клинических порогов — они принадлежат коду / system_config.
БД недоступна → нейтральный маркер, без raise и без литералов пациента.
"""
from __future__ import annotations
from _time_inject import get_today, get_utcnow  # seam

import logging
from datetime import date as _date

log = logging.getLogger(__name__)

# Окно снимка анализов в профиле. Не клинический порог (§9), а бюджет показа: сколько
# строк влезает в системный промпт рядом со всем остальным профилем. Названо именем,
# потому что его объявляет граница блока — число в двух местах разъехалось бы молча.
SNAPSHOT_WINDOW_DAYS = 180


# Как выглядит «поле не заполнено» в профиле и в старых дефолтах читателей. ОДИН дом (нить
# empty-profile, 24.09): до этого четыре модуля держали свои списки заглушек, и они разошлись —
# consult_prep печатал модели «Диагноз: [диагноз не задан]», wellally не знал «[онколог не задан]».
_UNSET = {"", "—", "-", "none", "null", "нет данных"}


def is_unset(value) -> bool:
    """Значение поля профиля отсутствует: None, пусто, прочерк или заглушка вида «[… не задан…]»."""
    if value is None:
        return True
    s = str(value).strip()
    return s.lower() in _UNSET or (s.startswith("[") and s.endswith("]") and "не задан" in s)


def medical_frame_lines(med: dict | None = None) -> list[str]:
    """Медицинская рамка человека для ЛЮБОГО врачебного контекста — одинаково у всех тенантов.

    Источники: диагноз из профиля (его вписывают руками) И активный список проблем (туда пишут
    знакомство, документы и /approve). Замер 24.09 на снимках: у второго тенанта были активные проблемы и
    не было диагноза — консилиум, подготовка к визиту и этот бриф не видели ни одной, видел только GP.
    Пусто в обоих → отсутствие ОБЪЯВЛЕНО («не сообщены»), а не спрятано: пропущенная строка
    читается моделью как «здоров» (report_absence_claims — граница объявляется в блоке)."""
    if med is None:
        try:
            import health_db as db
            med = (db.get_profile_context() or {}).get("medical", {}) or {}
        except Exception:  # silent-ok: без профиля рамка строится по списку проблем
            med = {}
    try:
        import problems_db
        probs = [p["title"] for p in problems_db.get_problem_list() or []
                 if p.get("title") and p.get("status") != "resolved"]
    except Exception as e:
        log.warning(f"medical_frame_lines: список проблем недоступен: {e}")
        probs = None
    dx = med.get("diagnosis")
    out = []
    if not is_unset(dx):
        out.append(f"Диагноз (профиль): {dx}")
    if probs:
        out.append("Проблемы со здоровьем (список проблем, активные): " + "; ".join(probs))
    if probs is None:
        out.append("Список проблем со здоровьем НЕ ПРОЧИТАН — считай эту рамку неполной")
    elif not out:
        out.append("Диагноз и проблемы со здоровьем: не сообщены (в профиле и в списке проблем пусто)")
    # Аллергии (решение владельца 27.09: спросить у человека один раз). Не спрошено — сказано,
    # а не спрятано: «аллергий нет» и «не спрашивали» врач читает по-разному.
    allergies = med.get("allergies")
    out.append(f"Аллергии и непереносимости (со слов): {allergies}" if not is_unset(allergies)
               else "Аллергии и непереносимости: не сообщены")
    return out


def _age_suffix(birth_date) -> str:
    if not birth_date:
        return ""
    try:
        b = _date.fromisoformat(str(birth_date)[:10])
        return f", {(get_today() - b).days // 365} лет"
    except Exception:
        return ""


def _hrv_typical_range(db) -> str:
    """ВСР p25–p75 за 90д из daily_metrics. '' если данных мало."""
    try:
        h = (db.get_metric_percentiles(90) or {}).get("hrv") or {}
        lo, hi = h.get("p25"), h.get("p75")
        if lo is not None and hi is not None:
            return f"{round(lo)}–{round(hi)} мс (p25–p75, 90д)"
        return ""
    except Exception:
        return ""  # нет данных/ошибка БД → диапазон опускаем, не падаем


# Имена анализов, которые инжектируются в промпт через build_patient_brief().
# Ключи должны соответствовать каноническим test_name, а не синонимам глоссария.
_LABS_SNAPSHOT_TESTS = [
    "CEA", "CA19-9", "LDH",                          # онкомаркеры
    "WBC", "MCV", "Ferritin", "Iron",                 # кровь
    "Glucose", "HbA1c",                               # метаболизм
    "CRP",                                            # воспаление
    # Несовпадение ключа с каноническим именем может молча скрыть результат.
    # Согласованность имён проверяется отдельно от наличия результатов у тенанта.
    "Vitamin_D", "Vitamin_B12", "Folate",             # нутриенты
    "Holotranscobalamin", "Methylmalonic_acid",       # метилирование (B12-функция)
    "IgG3",                                           # иммунология
    "Amylase_pancreatic",                             # поджелудочная
    "NT_proBNP",                                      # сердце
]


# Статус может быть буквой бланка (H/L/N) или словом из словаря FLAG_WORDS.
# Читатель, распознающий только длинные формы, пропустит буквенную пометку;
# отсутствие распознанного флага само по себе не означает «в норме».


def _lab_flag(r: dict) -> str:
    """Пометка анализа для контекста модели: флаг лаборатории, если он напечатан; иначе
    сравнение с референсом бланка — подписанное как НАШЕ сравнение, не как слово лаборатории
    (статус NULL = «лаборатория пометку не печатала», это не «норма»)."""
    from labs_db import FLAG_WORDS  # один дом словаря — labs_db (26.09)
    stat = (r.get("status") or "").strip().upper()
    if stat in FLAG_WORDS:
        return f" ⚠ {FLAG_WORDS[stat]}"
    if stat == "FLAGGED":
        return " ~ отклонение"
    v, lo, hi = r.get("value"), r.get("ref_low"), r.get("ref_high")
    try:
        if v is not None and lo is not None and float(v) < float(lo):
            return f" ⚠ ниже референса бланка ({lo:g}–{hi:g})" if hi is not None else f" ⚠ ниже референса бланка (≥{lo:g})"
        if v is not None and hi is not None and float(v) > float(hi):
            return f" ⚠ выше референса бланка ({lo:g}–{hi:g})" if lo is not None else f" ⚠ выше референса бланка (≤{hi:g})"
    except (TypeError, ValueError):
        return ""
    return ""


def _labs_snapshot(db) -> str:
    """Последние значения ключевых анализов из lab_results + ОБЪЯВЛЕННАЯ граница блока.

    Два фильтра стоят между базой и этим текстом: окно SNAPSHOT_WINDOW_DAYS и список
    из 20 имён. Замер 14.09: в каноне 535 аналитов, со строкой за окно — 107. Молчаливый
    срез читается моделью как полнота (класс report_absence_claims), поэтому границу
    объявляет labs_db.canon_window_note — ЕДИНЫЙ дом объявления, не свой текст рядом.

    Отказ БД больше не отдаёт пустую строку молча: исчезнувший раздел неотличим от
    «анализов нет вовсе», а это худшее из возможных чтений (§7, тихий fallback →
    громкий датчик).
    """
    try:
        rows = db.get_recent_labs(n_days=SNAPSHOT_WINDOW_DAYS, key_tests=_LABS_SNAPSHOT_TESTS)
        if not rows:
            return ("Актуальные анализы: за последние "
                    f"{SNAPSHOT_WINDOW_DAYS} дн. по отслеживаемым в этом блоке именам "
                    "строк нет. Это НЕ значит «анализы не сдавались» — блок отбирает по "
                    "короткому списку имён и по окну; полная картина в лаб-контексте.")
        lines = []
        for r in rows:
            name = r["test_name"]
            val  = r["value"]
            unit = (r.get("unit") or "").strip()
            date = (r.get("date") or r.get("last_date") or "")[:10]
            flag = _lab_flag(r)
            val_str = f"{val}{' ' + unit if unit else ''}{' (' + date + ')' if date else ''}"
            lines.append(f"  {name}: {val_str}{flag}")
        block = (f"Актуальные анализы (последние {SNAPSHOT_WINDOW_DAYS} дней):\n"
                 + "\n".join(lines))
        # Отказ сборки объявления говорит о себе в тексте — это делает единый дом
        # declared_boundary (до 21.09 здесь жила его копия).
        import labs_db as _ldb
        note = _ldb.declared_boundary(SNAPSHOT_WINDOW_DAYS, shown_tests=_LABS_SNAPSHOT_TESTS)
        return block + ("\n" + note if note else "")
    except Exception as e:
        # Раньше здесь была пустая строка: раздел исчезал, и его отсутствие читалось как
        # «анализов нет». Теперь отказ виден и модели, и в логе (§7 тихий fallback).
        log.warning("_labs_snapshot: срез анализов не собран: %s", e)
        return ("Актуальные анализы: раздел недоступен (сбой чтения базы). Отсутствие "
                "данных здесь НИЧЕГО не говорит о том, сдавались анализы или нет.")


def build_patient_brief() -> str:
    """Компактный профиль пациента из БД для инъекции в промпты. См. docstring модуля."""
    try:
        import health_db as db
        profile = db.get_profile_context()
    except Exception:
        profile = None

    # Пустой профиль ({}) — не ошибка БД: у нового человека до знакомства строк профиля нет.
    # До 24.09 `not profile` ловил и его, и модель читала «ошибка БД» про здоровую базу.
    if profile is None:
        return ("Профиль пациента временно недоступен (ошибка БД). "
                "Используй общие рекомендации без персонализации.")

    ident   = profile.get("identity", {})
    med     = profile.get("medical",  {})
    routine = profile.get("routine",  {})

    name = ident.get("name", "Пациент")
    age  = _age_suffix(ident.get("birth_date"))
    loc  = ident.get("location") or (profile.get("current_location") or {}).get("city") or ""
    loc_s = f", {loc}" if loc else ""

    diagnosis  = med.get("diagnosis", "—")
    try:
        import treatment_summary as _ts
        treatment = _ts.treatment_text(fallback=med.get("treatment", "—"))
    except Exception:
        treatment = med.get("treatment", "—")
    status     = med.get("treatment_status", "—")
    next_appt  = med.get("next_appointment", "[не задан]")
    oncologist = med.get("oncologist") or "[онколог не задан]"
    pet_res    = med.get("last_pet_ct_result", "—")
    pet_date   = med.get("last_pet_ct", "—")

    import health_db as db
    hrv_range = _hrv_typical_range(db)
    hrv_line  = f"Типичная ВСР: {hrv_range}.\n" if hrv_range else ""

    labs_snap = _labs_snapshot(db)
    labs_line = f"{labs_snap}\n" if labs_snap else ""

    chat_line = _chat_context_line()
    _pending = pending_chat()                       # Фаза 3: read-your-writes
    pending_block = f"{_pending}\n" if _pending else ""

    try:
        import beliefs as _bel
        _bhdr = _bel.render_header()
    except Exception:  # silent-ok: вера недоступна → бриф без заголовка, не падаем
        _bhdr = ""
    _bhdr = (_bhdr + "\n\n") if _bhdr else ""

    # NEUTRAL (brief-neutralization Фаза 0): рамка диагноза/онко-статуса рендерится ТОЛЬКО при
    # наличии данных о диагнозе у тенанта. Раньше строки «Диагноз…», «Онкостатус: … PET-CT …»,
    # «Онколог: …» печатались ВСЕМ безусловно → не-онко тенант получал навязанную диагноз-рамку
    # с дефолтами («—», «[онколог не задан]»). Гейт нейтрален — по факту данных, не по имени
    # болезни в коде. «Следующий визит» вынесен отдельно: он общий, у не-онко теряться не должен.
    # empty-profile (24.09): рамка диагноза/проблем — из ОДНОГО дома для всех врачебных контекстов.
    dx_line = "".join(f"{l}.\n" for l in medical_frame_lines(med))
    if not is_unset(treatment):
        dx_line += f"Лечение: {treatment}." + ("" if is_unset(status) else f" Статус: {status}.") + "\n"
    _has_onco = (not is_unset(pet_res)) or (not is_unset(pet_date)) or not is_unset(med.get("oncologist"))
    onco_line = (f"Онкостатус: {pet_res} (PET-CT {pet_date}).\n"
                 f"Онколог: {oncologist}.\n") if _has_onco else ""
    appt_line = f"Следующий визит: {next_appt}.\n" if not is_unset(next_appt) else ""

    return (
        _bhdr +
        f"Пациент: {name}{age}{loc_s}.\n"
        f"{dx_line}"
        f"{onco_line}"
        f"{appt_line}"
        f"{hrv_line}"
        f"{labs_line}"
        f"{chat_line}"
        f"{pending_block}"
    ).rstrip()


def _chat_context_line(max_questions: int = 8, max_facts: int = 40) -> str:
    """Safe subset типизированной памяти для брифа консилиума (Фаза 2, 2026-07-04):
    keyed-рекомендации + свежие открытые вопросы из чата. Разросшиеся facts/states
    НЕ выводим до консолидации (R2 — иначе пачка однотипных ключей утечёт напрямую).
    subject='self' → чужие факты исключены (R11, дефолт get_facts). Изолировано:
    сбой чтения памяти не рушит бриф."""
    try:
        import memory_facts_db as _mf
        facts = _mf.get_facts("fact")
        recs = _mf.get_facts("recommendation")
        # только свежие вопросы (≤45 дней): открытые вопросы никогда не закрываются,
        # без окна консилиум видел бы прошлогоднее («до 12 апреля», «стресс 20 марта»)
        qs = _mf.get_facts("question", since_days=45)
    except Exception:
        return ""
    parts = []
    if facts:
        rendered = "; ".join(_fmt_fact(f["key"], f["value"]) for f in facts[:max_facts])
        parts.append(f"Профиль из чата (консолидировано R3): {rendered}.")
    if qs:
        # M4 (staleness): датируем каждый вопрос и убираем ложное «свежие» — окно 45д,
        # вопрос 40-дневной давности не должен подаваться как сегодняшний.
        top = "; ".join(f"[{(q.get('valid_from') or '')[:10]}] {q['value']}" for q in qs[:max_questions])
        parts.append(f"Открытые вопросы из чата (датированы — относятся к своей дате, не к сегодня): {top}.")
    if recs:
        parts.append("Рекомендации ассистента из чата: "
                     + "; ".join(r["value"] for r in recs) + ".")
    return ("\n".join(parts) + "\n") if parts else ""


def _fmt_fact(key: str, value: str) -> str:
    """Рендер факта для брифа. Канонические (value=JSON-фасеты) → 'key: f1=v1, f2=v2';
    простые → 'key: value'."""
    import json as _json
    v = (value or "").strip()
    # Ответ пациента несёт вопрос внутри себя («…? → Да»), а ключом у него служит
    # ОТПЕЧАТОК вопроса (`question:<отпечаток_вопроса>`) — технический
    # идентификатор, нужный механизму повтора, а не читателю. Печатать его перед
    # текстом значит показывать врачу и пациенту внутренности сцепления задач с
    # памятью. Сам факт при этом остаётся полным: вопрос в тексте уже есть.
    if (key or "").startswith("question:"):
        return v
    if v.startswith("{"):
        try:
            d = _json.loads(v)
            facets = []
            for k, val in d.items():
                # вложенное (history/детали) → компактно-усечённо (не пустой факт, но
                # без блоба); многословные текстовые заметки — в БД, не в бриф
                if isinstance(val, (dict, list)):
                    sval = _json.dumps(val, ensure_ascii=False)
                    if len(sval) > 45:
                        sval = sval[:45] + "…"
                else:
                    sval = str(val)
                    if len(sval) > 60:
                        continue
                facets.append(f"{k}={sval}")
            return f"{key}: " + ", ".join(facets)
        except (ValueError, TypeError):
            return f"{key}: {v[:80]}"
    return f"{key}: {v}"


def _age_label(valid_from) -> str:
    """Ф2: штамп возраста заметки из valid_from — «[2026-07-05, 2 дн. назад]». Пусто если не распарсить."""
    try:
        from datetime import date
        d = date.fromisoformat(str(valid_from)[:10])
        n = (get_today() - d).days
        ago = "сегодня" if n <= 0 else ("вчера" if n == 1 else f"{n} дн. назад")
        return f"[{d.isoformat()}, {ago}] "
    except Exception:
        return ""


def _age_days(valid_from) -> int | None:
    try:
        from datetime import date
        return (get_today() - date.fromisoformat(str(valid_from)[:10])).days
    except Exception:
        return None


def _effective_class(s: dict) -> str:
    """Временной класс заметки: из поля, иначе деривация на лету (немигрированные)."""
    import memory_facts_db as _mf
    return s.get("temporal_class") or _mf._derive_temporal_class(
        s.get("value") or "", s.get("critical_flag") or 0)


def _ttl_keep(s: dict, transient_ttl_days: int | None) -> bool:
    """ЕДИНЫЙ дом правила TTL: transient старше N дней уходит из подачи (в БД цел).
    durable/standing по возрасту не отсеиваются (never-invariant). Второй копии
    этого правила быть не должно — оно уже разъезжалось в этом файле."""
    if _effective_class(s) != "transient" or transient_ttl_days is None:
        return True
    age = _age_days(s.get("valid_from"))
    return not (age is not None and age > transient_ttl_days)


def _route_notes(states: list, transient_ttl_days: int | None = 7):
    """Ф2/Ф3 — ЧИСТАЯ функция маршрутизации (тестируется без БД, зовётся датчиком).
    Делит заметки на (current, history) по temporal_class: transient → история, старше TTL →
    отброшен; durable/standing → current, по возрасту НЕ отсеиваются (never-invariant).
    Инвариант, который проверяет датчик: ни один transient НЕ попадает в current."""
    current, history = [], []
    for s in states:
        eff = _effective_class(s)
        if not _ttl_keep(s, transient_ttl_days):
            continue  # Ф3: transient старше TTL уходит из подачи (в БД цел для трендов).
        line = f"- {_age_label(s.get('valid_from'))}{s.get('value')}"
        (history if eff == "transient" else current).append((eff, line))
    return current, history


def recent_notes(days: int = 2, transient_ttl_days: int | None = 7) -> str:
    """ЕДИНЫЙ ИСТОЧНИК свежих заметок из чата (memory_facts states, ≤N дней) для
    потребителей, которым нужны поправки о качестве данных И жалобы/симптомы из
    диалога (дневной/недельный отчёт, подсистема гипотез). subject='self' (R11).
    Изолировано: сбой чтения памяти не роняет вызывающего.

    Ф2 (2026-07-07, инцидент «сон сегодня»): каждая заметка со ШТАМПОМ ДАТЫ (pull-свежесть
    из valid_from при чтении) и разделена по temporal_class — transient как ИСТОРИЯ
    (датировано, НЕ «сейчас»), durable/standing как текущий контекст. Для немигрированных
    (temporal_class IS NULL) класс деривится на лету — работает до Ф4-миграции."""
    try:
        import memory_facts_db as _mf
        states = _mf.get_facts("state", since_days=days)
    except Exception:
        return ""
    if not states:
        return ""
    current, history = _route_notes(states[:8], transient_ttl_days)
    out = ["ЗАМЕТКИ ИЗ ЧАТА. Каждая ДАТИРОВАНА — НЕ считай старую заметку сегодняшней; для "
           "«сейчас» бери самые свежие. Поправка о качестве данных относится к СВОЕЙ дате, не к сегодня."]
    if current:
        out.append("Текущий контекст (статусы/жалобы, УЧИТЫВАЙ):\n" + "\n".join(l for _, l in current))
    if history:
        out.append("Разовые события (история, НЕ сегодняшнее):\n" + "\n".join(l for _, l in history))
    return "\n".join(out)


# ── Провенанс заметок для УТРЕННЕГО БРИФА (2026-08-04, нить brief-repeat) ──────
# Решение владельца: «зачем мне повторять то, что я знаю». Сказанное ИМ САМИМ система
# не пересказывает НИКОГДА — ни назавтра, ни через месяц. Поэтому дискриминатор здесь
# `source`, а НЕ давность: кулдаун был бы неверной моделью (повтор через 30 дней так же
# бессмыслен, как назавтра, — он просто реже раздражает).
# Инцидент, породивший правило: 02–04.08 бриф трое суток подряд открывался пересказом
# новости, которую владелец сам сообщил 02.08. Гейт анти-повтора этого не видел и не мог:
# он живёт на слое карточек, а заметки едут в промпт мимо него.
# Список ЗАКРЫТ по строгому: всё, что не названо здесь, идёт в фон. Незнание источника
# не даёт права рассказать — ошибаться безопаснее в сторону молчания (его слова он знает).
_NEEDS_RECEIPT = ("arbiter_unverified",)   # система ВЫВЕЛА из его слов → одна квитанция

_NOTES_BACKDROP_HEADER = (
    "ЗАМЕТКИ ИЗ ЧАТА — ФОН ДЛЯ ТВОИХ ВЫВОДОВ, НЕ МАТЕРИАЛ ДЛЯ ОТЧЁТА.\n"
    "Ниже то, что владелец сообщил ТЕБЕ САМ. Он это знает. ЗАПРЕЩЕНО пересказывать это, "
    "подтверждать, поздравлять с этим, открывать этим отчёт и вообще упоминать как новость. "
    "Нужны они ровно для двух вещей: не соврать в числах и не советовать отменённое.")
_NOTES_RECEIPT_HEADER = (
    "ЗАПИСАНО С ЕГО СЛОВ, НО ВЫВЕДЕНО СИСТЕМОЙ И НЕ ПРОВЕРЕНО. Скажи об этом ОДНОЙ фразой "
    "в самом конце отчёта, в форме «записал так: …; поправь, если не так». "
    "Это единственная квитанция — больше она не повторится, поэтому не потеряй её.")


def brief_notes(days: int = 2, transient_ttl_days: int | None = 7) -> tuple[str, list[int]]:
    """Заметки из чата ДЛЯ УТРЕННЕГО БРИФА, разделённые по провенансу.

    Возвращает (блок_для_промпта, id_фактов_под_квитанцию). Второй элемент отмечать
    через memory_facts_db.mark_receipts_shown ТОЛЬКО после доставки (RYW).

    Почему отдельная функция, а не флаг в recent_notes: у брифа и у чата разные вопросы
    к одним данным. В чате пересказ уместен — человек спросил; в брифе это шум. Один
    источник (memory_facts), два читателя, фильтр объявлен явно (§16).
    Изолировано: сбой чтения памяти не роняет бриф."""
    try:
        import memory_facts_db as _mf
        states = _mf.get_facts("state", since_days=days)
    except Exception:
        return "", []
    backdrop, receipts, receipt_ids = [], [], []
    for s in states[:8]:
        if not _ttl_keep(s, transient_ttl_days):
            continue
        line = f"- {_age_label(s.get('valid_from'))}{s.get('value')}"
        # Квитанция положена ровно один раз и только выведенному системой. Всё
        # остальное — фон: и его собственные слова (_OWNER_SAID), и уже отквитанченное,
        # и машинные источники (device_gps/backfill), про которые у брифа свои каналы.
        if (s.get("source") or "") in _NEEDS_RECEIPT and not s.get("receipt_shown_at"):
            receipts.append(line)
            receipt_ids.append(s.get("id"))
        else:
            backdrop.append(line)
    out = []
    if backdrop:
        out.append(_NOTES_BACKDROP_HEADER + "\n" + "\n".join(backdrop))
    if receipts:
        out.append(_NOTES_RECEIPT_HEADER + "\n" + "\n".join(receipts))
    return "\n\n".join(out), [i for i in receipt_ids if i is not None]


def chat_facts() -> str:
    """Консолидированные факты из чата (образ жизни, привычки) — для рассуждателей,
    которым нужны устойчивые факты, а не только свежие states. subject='self' (R11)."""
    try:
        import memory_facts_db as _mf
        facts = _mf.get_facts("fact")
    except Exception:
        return ""
    if not facts:
        return ""
    return "\n".join(f"- {_fmt_fact(f['key'], f['value'])}" for f in facts[:40])


def _utc_floor_str(hours: int) -> str:
    """Порог свежести «не старше N часов» от get_utcnow() как '%Y-%m-%d %H:%M:%S'.
    Вынесено из pending_chat для тестируемости time-contract (boundary под клоком)."""
    from datetime import timedelta
    return (get_utcnow() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")


def pending_chat(max_msgs: int = 6, max_hours: int = 12) -> str:
    """Read-your-writes (Фаза 3, 2026-07-05): сырые user-реплики НОВЕЕ последнего
    прогона арбитра — «сказано, но ещё не структурировано» в memory_facts. Закрывает
    окно, где явное сообщение («отменил <процедуру>») не видно структурным потребителям
    (consult/отчёт/гипотезы) до ~90с-арбитра или при его misclassification.

    Источник — уже СИНХРОННО пишущийся conversation_history + heartbeat
    _arbiter_last_run. БЕЗ второго хранилища → нет риска R6-расхождения (сырой чат
    эфемерен, memory_facts остаётся авторитетным; как только арбитр структурирует
    сообщение, оно уходит за cutoff и всплывает уже через recent_notes/chat_facts).
    Cap по числу и времени: мёртвый арбитр не вывалит сутки чата (его смерть ловит
    integrity.check_arbiter_liveness). Изолировано: сбой не рушит вызывающего."""
    try:
        import health_db as db
        from datetime import datetime, timedelta
        floor = _utc_floor_str(max_hours)
        with db.get_conn() as c:
            hb = c.execute(
                "SELECT updated_at FROM system_config WHERE key='_arbiter_last_run'"
            ).fetchone()
            cutoff = hb[0] if hb and hb[0] else floor
            if cutoff < floor:        # арбитр давно молчит → ограничим окном max_hours
                cutoff = floor
            rows = c.execute(
                "SELECT content, created_at FROM conversation_history "
                "WHERE role='user' AND created_at > ? "
                "ORDER BY created_at DESC LIMIT ?",
                (cutoff, max_msgs),
            ).fetchall()
    except Exception:
        return ""
    if not rows:
        return ""
    rows_chrono = list(reversed(rows))            # хронологический порядок
    # M5 (staleness): датируем сырые строки [ГГГГ-ММ-ДД] из created_at.
    lines = "\n".join(f"- [{(r[1] or '')[:10]}] {(r[0] or '')[:200]}" for r in rows_chrono)
    return ("ТОЛЬКО ЧТО В ЧАТЕ (ещё не структурировано — самое свежее; при конфликте "
            "со старыми фактами приоритет ЗДЕСЬ):\n" + lines)


_STATED_RECURRENCE = ("опять", "снова", "again", "recurr", "третий раз", "каждый", "постоянно")


def symptom_chronology(recent_days: int | None = None, baseline_days: int | None = None,
                       min_baseline_n: int | None = None) -> str:
    """v2 (2026-07-07) — замена recurring_symptoms. Квалификатор хронологии вместо сырого
    счётчика (тот встраивал availability-bias: частота-в-памяти ≠ значимость).

    Иерархия салиентности: (1) red-flag — severity бьёт frequency, сурфейсим ВСЕГДА;
    (2) явная хронология из слов («опять/снова»); (3) novelty (с проверкой, что база-окно
    имело данные); (4) rate-ratio — СЛАБЫЙ тайбрейкер (у N=1 недо-мощен). Хронически-
    стабильное ПОДАВЛЯЕМ. Счёт по РАЗЛИЧНЫМ ДАТАМ (valid_from), baseline БЕЗ recent-окна,
    отсев отрицаний. Кормит консилиум (reasoning_block). Изолировано."""
    try:
        import memory_facts_db as _mf
        import memory_salience as _ms
        import memory_config as _mc
        from datetime import date, timedelta
        from collections import defaultdict
        recent_days = recent_days if recent_days is not None else _mc.get_param("recent_days", 30)
        baseline_days = baseline_days if baseline_days is not None else _mc.get_param("baseline_days", 180)
        min_baseline_n = min_baseline_n if min_baseline_n is not None else _mc.get_param("min_baseline_n", 2)
        sym_terms, red_terms = _ms.symptom_terms(), _ms.red_flag_terms()
        red_set = set(red_terms)
        all_terms = tuple(sym_terms) + tuple(red_terms)
        states = _mf.get_facts("state", since_days=baseline_days)
    except Exception:
        return ""
    today = get_today()
    recent_floor = today - timedelta(days=recent_days)
    agg = defaultdict(lambda: {"recent": set(), "baseline": set(), "red": False, "stated": False})
    baseline_has_data = False
    for s in states:
        val = s.get("value") or ""
        low = val.lower()
        try:
            d = date.fromisoformat(str(s.get("valid_from"))[:10])
        except Exception:
            continue
        in_recent = d > recent_floor
        if not in_recent:
            baseline_has_data = True
        for term in all_terms:
            if term in low and not _ms._negated(low, term):
                a = agg[term]
                (a["recent"] if in_recent else a["baseline"]).add(d.isoformat())
                if term in red_set or _ms.is_red_flag(val):
                    a["red"] = True
                if any(w in low for w in _STATED_RECURRENCE):
                    a["stated"] = True
    lines = []
    span = max(1, baseline_days - recent_days)
    for term, a in agg.items():
        rn, bn = len(a["recent"]), len(a["baseline"])
        if rn == 0 and not a["red"]:
            continue
        base_rate = bn * (recent_days / span)  # база, нормированная на recent-окно
        if a["red"]:
            label = "🚩 red-flag (severity)"
        elif a["stated"]:
            label = "рецидив (заявлен)"
        elif rn > 0 and bn == 0 and baseline_has_data:
            label = "novel"
        elif bn >= min_baseline_n and rn >= max(3, base_rate * 2 + 1):
            label = "чаще обычного"
        else:
            continue  # хронически-стабильное / недостаточно базы → подавляем (анти-bias)
        lines.append(f"- {term}: {label} ({rn} дн. за {recent_days}; база ~{bn})")
    if not lines:
        return ""
    return ("СИМПТОМЫ — хронология (severity > частота; хронически-стабильное подавлено, "
            "чтобы не переоценивать привычное):\n" + "\n".join(lines))


# Обратная совместимость: старое имя → новый механизм (внешние вызовы не ломаются).
def recurring_symptoms(min_count: int = 3, days: int = 30) -> str:
    return symptom_chronology(recent_days=days)


def reasoning_block() -> str:
    """ЕДИНЫЙ блок памяти из чата для рассуждателей (конституции, недельный/месячный
    отчёт, оценка гипотез, подготовка разбора, чекин): консолидированные факты +
    свежие заметки/жалобы + только что сказанное (read-your-writes). Компактно
    (context rot: больше входа = хуже). Единый источник — потребители вставляют ЭТО."""
    facts = chat_facts()
    notes = recent_notes()
    recurring = recurring_symptoms()
    pending = pending_chat()
    parts = []
    if facts:
        parts.append("Факты из диалогов (образ жизни, привычки):\n" + facts)
    if notes:
        parts.append(notes)
    if recurring:
        parts.append(recurring)
    if pending:
        parts.append(pending)
    return "\n\n".join(parts)
