#!/usr/bin/env python3.11
"""
hai_context — контекст-блоки, tool execution, smart context.
Зависимости: health_db, genome_context (lazy), lifestyle_agents (lazy),
             calendar_client (опционально).
Не импортирует другие hai_* модули.
"""

import json
import logging
from datetime import date, timedelta
from _time_inject import get_today  # seam
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent))
from _fmt_helpers import fmt_min
import health_db as db
import metrics_db as _metrics_db

log = logging.getLogger(__name__)

# Лаб-блок чата: окно и потолок показа. Не клинические пороги (§9), а бюджет показа.
# Имена нужны потому, что оба числа ОБЪЯВЛЯЮТСЯ в границе блока: литерал в двух местах
# (срез и объявление) разъехался бы молча — ровно класс §18.
_LABS_WINDOW_DAYS = 730
_LABS_SHOWN_MAX = 25

try:
    import calendar_client as cal
    _CAL_AVAILABLE = True
except Exception:
    _CAL_AVAILABLE = False


# ── Компактный контекст-блок (~500 токенов) ──────────────────────────────

def build_context_block_compact(target: date = None) -> str:
    """Компактный блок данных (~500 токенов) для подстановки в чат-запрос."""
    if target is None:
        target = get_today() - timedelta(days=1)

    stats7    = db.get_stats(7, target)
    stats30   = db.get_stats(30, target)
    yesterday = db.get_day(str(target))
    checkins  = db.get_recent_checkins(5)
    exps      = db.get_active_experiments()

    def fmt_sleep(d):
        s = d.get("sleep") or {}
        if not s.get("totalSleep"): return "нет данных"
        deep = int((s.get("deep") or 0) * 60)
        rem  = int((s.get("rem")  or 0) * 60)
        sc   = s.get("sleep_score")
        t    = s.get("totalSleep", 0)
        sc_s = f" score={sc}" if sc else ""
        return f"{t:.1f}ч deep={deep}м rem={rem}м{sc_s}"

    lines = [f"=== Данные на {target} ==="]

    sleep   = yesterday.get("sleep") or {}
    hrv_val = (yesterday.get("hrv") or {}).get("avg")
    rhr_val = (yesterday.get("resting_heart_rate") or {}).get("value")
    rd_val  = yesterday.get("readiness_score")
    steps   = yesterday.get("steps")
    spo2    = (yesterday.get("spo2") or {}).get("avg")
    weight  = yesterday.get("weight_kg")

    lines.append(f"Вчера:")
    lines.append(f"  Сон: {fmt_sleep(yesterday)}")
    if sleep.get("sleepStart"):
        lines.append(f"  Лёг: {sleep['sleepStart'][11:16]}  Встал: {(sleep.get('sleepEnd') or '')[ 11:16]}")
    if hrv_val:  lines.append(f"  ВСР: {hrv_val:.1f} мс  (7д avg: {stats7.get('avg_hrv','?')})")
    if rhr_val:  lines.append(f"  ЧСС покоя: {rhr_val:.0f}")
    if rd_val:   lines.append(f"  Readiness: {rd_val}")
    if steps:    lines.append(f"  Шаги: {int(steps)}")
    if spo2:     lines.append(f"  SpO2: {spo2:.1f}%")
    if weight:   lines.append(f"  Вес: {weight} кг")

    lines.append(f"\nПоследние 7 дней (дата | сон | deep | ВСР | score | шаги):")
    for i in range(1, 8):
        d   = target - timedelta(days=i)
        row = db.get_day(str(d))
        s   = row.get("sleep") or {}
        hrv = (row.get("hrv") or {}).get("avg")
        sc  = s.get("sleep_score")
        st  = s.get("totalSleep")
        dp  = int((s.get("deep") or 0) * 60)
        sp  = row.get("steps")
        lines.append(
            f"  {d.strftime('%d.%m')} | {f'{st:.1f}ч' if st else '—':6} | "
            f"{f'{dp}м' if dp else '—':5} | {f'{hrv:.0f}' if hrv else '—':5} | "
            f"{f'{sc:.0f}' if sc else '—':5} | {int(sp) if sp else '—'}"
        )

    lines.append(f"\n30д avg: сон {stats30.get('avg_sleep','?')}ч  "
                 f"deep {fmt_min(stats30.get('avg_deep'))}м  "
                 f"ВСР {stats30.get('avg_hrv','?')}мс  "
                 f"readiness {stats30.get('avg_readiness','?')}  "
                 f"score {stats30.get('avg_sleep_score','?')}")

    # BL-DATA-PARITY-1: всё собранное, а не только ручной список выше.
    _all_metrics = db.render_all_metrics(30, target)
    if _all_metrics:
        lines.append("\n" + _all_metrics)

    if checkins:
        lines.append(f"\nПоследние чекины:")
        for c in checkins[-4:]:
            if c.get("question") and c.get("answer") and c["question"] != "recommendation":
                lines.append(f"  [{c['date']}] {c['question'][:60]}")
                lines.append(f"    → {c['answer'][:100]}")

    if exps:
        lines.append(f"\nАктивные эксперименты:")
        for e in exps:
            stats_e = db.get_experiment_stats(e["id"])
            n      = (stats_e.get("adherence") or {}).get("total", 0) or 0
            before = stats_e.get("before_14d", {})
            after  = stats_e.get("after", {})
            lines.append(f"  [{e['id']}] {e['name']} (день {n}): {e['intervention']}")
            if before.get("deep") and after.get("deep") and n >= 3:
                delta = (after["deep"] - before["deep"]) * 60
                lines.append(f"    Результат: deep sleep {'+' if delta>=0 else ''}{delta:.0f}м к исходному")


    return "\n".join(lines)


# ── Tool definitions ──────────────────────────────────────────────────────

HEALTH_TOOLS = [
    {
        "name": "query_metrics",
        "description": (
            "Получить данные о здоровье (сон, ВСР, шаги и др.) за произвольный период. "
            "Используй для любого вопроса о конкретных датах, периодах, трендах."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "date_from": {"type": "string", "description": "YYYY-MM-DD"},
                "date_to":   {"type": "string", "description": "YYYY-MM-DD"},
                "metrics": {
                    "type": "array",
                    "items": {"type": "string"},
                    # BL-DATA-PARITY-1: перечень из единого словаря подписей, не литерал.
                    "description": "Колонки: " + ", ".join(
                        f"{k} ({v[0]})" for k, v in _metrics_db.METRIC_LABELS.items()
                    ) + ". Пусто = все."
                }
            },
            "required": ["date_from", "date_to"]
        }
    },
    {
        "name": "query_stats",
        "description": "Агрегированная статистика (среднее, мин, макс) за период. Для сравнения периодов.",
        "input_schema": {
            "type": "object",
            "properties": {
                "date_from": {"type": "string", "description": "YYYY-MM-DD"},
                "date_to":   {"type": "string", "description": "YYYY-MM-DD"}
            },
            "required": ["date_from", "date_to"]
        }
    },
    {
        "name": "query_labs",
        "description": "Лабораторные анализы. Последние значения или за период.",
        "input_schema": {
            "type": "object",
            "properties": {
                "tests":     {"type": "array", "items": {"type": "string"}, "description": "Пусто = все ключевые."},
                "date_from": {"type": "string", "description": "YYYY-MM-DD (опционально)"}
            },
            "required": []
        }
    },
    {
        "name": "query_calendar",
        "description": (
            "Получить события из Google Calendar: поездки, перелёты, встречи. "
            "Используй когда нужно учесть поездки в анализе сна/ВСР, или найти даты путешествий."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "days_ahead":  {"type": "integer", "description": "Сколько дней вперёд смотреть (default 30, max 90)"},
                "travel_only": {"type": "boolean", "description": "true = только поездки и перелёты"}
            },
            "required": []
        }
    },
    {
        "name": "query_genome",
        "description": (
            "Получить данные из генома пользователя (сырые данные генотипирования). "
            "Используй ВСЕГДА когда вопрос касается: генетики, предрасположенностей, "
            "генов, геномных рекомендаций, наследственности, рисков по ДНК, "
            "влияния генов на сон/стресс/энергию/движение. "
            "Возвращает реальные патогенные варианты и генетический контекст."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "domain": {
                    "type": "string",
                    "description": "Область: sleep | stress | energy | movement | all. Default: all",
                    "enum": ["sleep", "stress", "energy", "movement", "all"]
                },
                "question": {
                    "type": "string",
                    "description": "Конкретный вопрос или трейт — например 'облысение', 'кофеин', 'витамин D'"
                }
            },
            "required": []
        }
    }
]


def _execute_tool(tool_name: str, tool_input: dict) -> str:
    import json as _j

    if tool_name == "query_metrics":
        date_from = tool_input["date_from"]
        date_to   = tool_input["date_to"]
        # BL-DATA-PARITY-1: белый список = все колонки-показатели таблицы (защита от
        # SQL-инъекции имени колонки та же — имя обязано быть реальной колонкой).
        # Пусто = все: раньше описание обещало «все», а отдавало 8.
        all_cols = db.metric_columns()
        valid = set(all_cols)
        wanted = tool_input.get("metrics") or all_cols
        cols = ["date"] + [m for m in wanted if m in valid]
        try:
            with db.get_conn() as conn:
                rows = conn.execute(
                    f"SELECT {','.join(cols)} FROM daily_metrics "
                    "WHERE date BETWEEN ? AND ? ORDER BY date",
                    (date_from, date_to)
                ).fetchall()
            if not rows:
                return f"Нет данных за {date_from}–{date_to}."
            if not tool_input.get("metrics"):   # «все» = все, где за период есть данные
                keep = [i for i, c in enumerate(cols)
                        if i == 0 or any(r[i] is not None for r in rows)]
                cols = [cols[i] for i in keep]
                rows = [[r[i] for i in keep] for r in rows]
            lines = [" | ".join(str(v) if v is not None else "—" for v in r) for r in rows]
            header = " | ".join(cols)
            return f"{len(rows)} записей ({date_from}–{date_to}):\n{header}\n" + "\n".join(lines)
        except Exception as e:
            return f"Ошибка: {e}"

    elif tool_name == "query_stats":
        date_from = tool_input["date_from"]
        date_to   = tool_input["date_to"]
        try:
            with db.get_conn() as conn:
                row = conn.execute("""
                    SELECT ROUND(AVG(sleep_total),2), ROUND(AVG(sleep_deep),2),
                           ROUND(AVG(hrv),1), ROUND(MIN(hrv),1), ROUND(MAX(hrv),1),
                           ROUND(AVG(resting_hr),1), ROUND(AVG(readiness),1),
                           ROUND(AVG(sleep_score),1), ROUND(AVG(steps)), COUNT(*)
                    FROM daily_metrics WHERE date BETWEEN ? AND ? AND sleep_total IS NOT NULL
                """, (date_from, date_to)).fetchone()
            keys = ["avg_sleep","avg_deep","avg_hrv","min_hrv","max_hrv",
                    "avg_rhr","avg_readiness","avg_score","avg_steps","n_days"]
            return _j.dumps(dict(zip(keys, row)), ensure_ascii=False, indent=2)
        except Exception as e:
            return f"Ошибка: {e}"

    elif tool_name == "query_labs":
        tests     = tool_input.get("tests") or []
        # Умолчание было "2020-01-01" — третий, нигде не названный фильтр поверх окна в
        # 10 лет. Теперь без date_from граница одна, и её объявляет declared_boundary.
        date_from = tool_input.get("date_from") or ""
        try:
            import lab_canon
            import labs_db as _ldb
            # Три границы полноты ответа:
            #   · псевдоним и каноническое имя должны совпасть после нормализации;
            #     выдуманные «Показатель-G» и «Example_G» иллюстрируют разные написания,
            #     а не отсутствие лабораторных данных (report_absence_claims);
            #   · качественный результат нельзя печатать как None из колонки value —
            #     текст результата отдаёт единый дом labs_db.result_text;
            #   · окно запроса и date_from должны быть объявлены.
            wanted = [lab_canon.normalize(t) for t in tests]
            labs = db.get_recent_labs(3650, wanted if wanted else None)
            labs = [l for l in labs if l.get("date","") >= date_from]
            found = {l["test_name"] for l in labs}
            missing = [t for t, w in zip(tests, wanted) if w not in found]
            out = [f"{l['test_name']}: {_ldb.result_text(l)} ({l['date']})" for l in labs]
            if missing:
                since = f" (с {date_from})" if date_from else ""
                out.append("По именам строк НЕ НАЙДЕНО: " + ", ".join(missing) + since + ". "
                           "Это НЕ значит «не сдавался»: имя могло не свестись к каноническому "
                           "или строка старше границы. Повтори запрос без tests и без date_from, "
                           "прежде чем говорить об отсутствии.")
            if not tests:
                note = _ldb.declared_boundary(3650)
                if note:
                    out.append(note)
            return "\n".join(out) if out else (
                "Строк за запрошенный период нет. Это НЕ значит, что анализы не сдавались раньше.")
        except Exception as e:
            return f"Ошибка: {e}"

    elif tool_name == "query_calendar":
        if not _CAL_AVAILABLE:
            return "Календарь недоступен."
        try:
            days        = min(int(tool_input.get("days_ahead", 30)), 90)
            travel_only = tool_input.get("travel_only", False)
            if travel_only:
                events = cal.get_travel_events(days)
                if not events:
                    return "Поездок в календаре не найдено."
                lines = []
                for e in events:
                    loc = f" [{e['location']}]" if e.get("location") else ""
                    lines.append(f"{e['date_raw']}: {e['title']}{loc}")
                return "Поездки:\n" + "\n".join(lines)
            else:
                return cal.format_calendar_context(days) or "Событий нет."
        except Exception as e:
            return f"Ошибка календаря: {e}"

    elif tool_name == "query_genome":
        try:
            import sys as _sys
            _sys.path.insert(0, str(Path(__file__).parent))
            import genome_context as _gc
            domain   = tool_input.get("domain", "all")
            question = tool_input.get("question", "")
            if question:
                result = _gc.answer_trait_question(question)
                return result if result else "По этому вопросу данных в геноме не найдено."
            elif domain == "all":
                result = _gc.build_genetic_context_block(max_variants=20)
                return result if result else "Геномных данных нет или они не загружены."
            else:
                result = _gc.build_lifestyle_genome_block(domain, max_variants=8)
                return result if result else f"Нет значимых вариантов для домена '{domain}'."
        except Exception as e:
            return f"Ошибка при чтении генома: {e}"

    return f"Неизвестный инструмент: {tool_name}"


# ── Smart context ─────────────────────────────────────────────────────────

_DOMAIN_KEYWORDS = {
    "sleep":    ["сон","сплю","спал","спать","просыпаюсь","засыпаю","sleep","rem","deep",
                 "циркадн","ночь","будильник","бессониц","нарушени сна","архитектура сна"],
    "genome":   ["геном","ген","генетик","днк","dna","предрасполож","наследств",
                 "23andme","мутаци","вариант","per3","mthfr","snp","полиморфизм","генетическ"],
    "stress":   ["стресс","тревог","паник","нерв","hrv","вср","кортизол",
                 "выгора","burnout","напряж","восстановлени","comt","серотонин"],
    "energy":   ["энерги","усталост","устал","бодрост","вялост","упадок",
                 "folat","фолат","витамин b","mthfr","митохондри","метаболизм","железо","ферритин"],
    "movement": ["движени","спорт","тренировк","шаги","exercise","бег","мышц","активност"],
    "labs":     ["анализ","кровь","биохими","маркер","cea","wbc","гемоглобин","hgb",
                 "ферритин","глюкоз","витамин d","b12","онкомаркер","ldh","alt","ast"],
    "profile":  ["обо мне","мой профиль","что ты знаешь","моя ситуация","контекст"],
    "memory":   ["помнишь","я говорил","ранее","прошлый раз","мы обсуждали"],
    "problems": ["проблема","диагноз","заболевани","болезн","онкологи","лечени","ремисси","рак"],
}

_FULL_CONTEXT_KEYWORDS = [
    "рекомендаци","что мне делать","как мне","план","посоветуй",
    "в целом","overall","стратегия","итог","что важного","приоритет",
]


def _detect_domains(question: str) -> set:
    """Определяет релевантные домены по ключевым словам.

    Sprint 2 / Р-1 step 4 (2026-05-22, closes F-122): keywords читаются
    из БД (routing_keywords table), fallback на module-level dicts если
    БД недоступна или пустая.
    """
    q        = question.lower()
    detected = set()

    # Primary: keywords из БД (можно edit без code change)
    try:
        kw_map = db.get_routing_keywords()
        if not kw_map:
            raise ValueError("empty routing_keywords")
        domain_kws = {d: kws for d, kws in kw_map.items() if d != "__full__"}
        full_kws   = kw_map.get("__full__", [])
    except Exception:
        # Fallback: hardcoded dicts (cold-start или БД недоступна)
        domain_kws = _DOMAIN_KEYWORDS
        full_kws   = _FULL_CONTEXT_KEYWORDS

    for domain, keywords in domain_kws.items():
        for kw in keywords:
            if kw in q:
                detected.add(domain)
                break
    for kw in full_kws:
        if kw in q:
            detected.update(["sleep","stress","energy","movement","genome"])
            break
    if not detected:
        detected.update(["sleep","stress","energy"])
    return detected


def build_smart_context(question: str, target: date = None) -> str:
    """Проактивный контекст: анализирует вопрос, загружает нужные данные."""
    if target is None:
        target = get_today()
    NL      = chr(10)
    domains = _detect_domains(question)
    sections = []

    # 1. Базовые метрики (всегда)
    sections.append(build_context_block_compact(target))

    # 2. Медицинский профиль
    try:
        profile = db.get_profile_context()
        med = profile.get('medical', {})
        loc = profile.get('current_location', {})
        lines = ['=== Медицинский профиль ===']
        if med.get('diagnoses'):
            lines.append('  Диагнозы: ' + str(med['diagnoses']))
        if med.get('treatment_status'):
            lines.append('  Статус: ' + med['treatment_status'])
        if med.get('chemo_ended'):
            lines.append('  Химия завершена: ' + med['chemo_ended'])
        if med.get('next_appointment'):
            lines.append('  Следующий приём: ' + med['next_appointment'])
        if loc.get('city'):
            lines.append('  Локация: ' + loc['city'] + ', ' + loc.get('country',''))
        if len(lines) > 1:
            sections.append(NL.join(lines))
    except Exception:
        pass

    # 3. Свежие заметки из чата (единый источник patient_context, C — 2026-07-05).
    #    Было легаси get_memory(n=8) по старой таблице memory вперемешку. Устойчивые
    #    факты профиля бот уже видит в системном промпте (build_patient_brief) —
    #    здесь только свежие states/жалобы/поправки, без дубля. reasoning-safe внутри.
    if 'memory' in domains or 'profile' in domains or len(domains) >= 3:
        import patient_context as _pc
        _notes = _pc.recent_notes()
        if _notes:
            sections.append(_notes)

    # 4. Список проблем
    if 'problems' in domains or len(domains) >= 3:
        try:
            problems = db.get_problem_list()
            active   = [p for p in problems if p.get('status') != 'resolved']
            if active:
                lines = ['=== Активные проблемы ===']
                for p in active:
                    lines.append('  [' + p.get('status','') + '] ' + p['problem_id'] + ': ' + p['title'])
                sections.append(NL.join(lines))
        except Exception:
            pass

    # 5. Геномный контекст
    try:
        import genome_context as _gc
        if 'genome' in domains:
            block = _gc.build_genetic_context_block(max_variants=20)
            if block:
                sections.append('=== Геном (все значимые варианты) ===' + NL + block)
        else:
            genome_domains = {'sleep','stress','energy','movement'} & domains
            for gd in genome_domains:
                block = _gc.build_lifestyle_genome_block(gd, max_variants=6)
                if block:
                    sections.append(block)
    except Exception:
        pass

    # 6. Lifestyle Agent Briefs
    lifestyle_needed = {'sleep','stress','energy','movement'} & domains
    if lifestyle_needed:
        try:
            import lifestyle_agents as _la
            agent_map = {
                'sleep':    _la.SleepAgent(),
                'stress':   _la.StressAgent(),
                'energy':   _la.EnergyAgent(),
                'movement': _la.MovementAgent(),
            }
            brief_lines = ['=== Lifestyle Agent Briefs ===']
            any_brief   = False
            for d in sorted(lifestyle_needed):
                if d in agent_map:
                    try:
                        brief = agent_map[d].run(target, target)
                        if brief:
                            brief_lines.append(NL + '[' + d.upper() + ']' + NL + brief[:600])
                            any_brief = True
                    except Exception:
                        pass
            if any_brief:
                sections.append(NL.join(brief_lines))
        except Exception:
            pass

    # 7. Анализы
    # Между базой и промптом два фильтра: окно времени И ограничение числа строк.
    # Если заголовок обещает весь период, необъявленное усечение выглядит как полнота
    # (report_absence_claims). Границу объявляет единый дом labs_db.canon_window_note,
    # своего текста здесь нет.
    if 'labs' in domains:
        try:
            labs = db.get_recent_labs(_LABS_WINDOW_DAYS)
            if labs:
                head = labs[:_LABS_SHOWN_MAX]
                lines = ['=== Лабораторные данные (последние 2 года) ===']
                import labs_db as _ldb
                # Результат — через result_text: качественный печатался как `None`
                # (тот же дефект, что у query_labs, замер 21.09).
                for lab in head:
                    lines.append('  ' + lab['test_name'].ljust(18) + _ldb.result_text(lab) + ' (' + lab['date'] + ')')
                # Отказ сборки объявления говорит о себе в тексте — единый дом
                # declared_boundary (до 21.09 здесь жила его копия).
                note = _ldb.declared_boundary(
                    _LABS_WINDOW_DAYS, shown_tests=[l['test_name'] for l in head])
                if note:
                    lines.append(note)
                # Свежесть по графику контроля каждого аналита — тот же блок, что у врача
                # (gp_context._check_lab_freshness, расписание — lab_monitoring_schedule).
                # До 27.09 правило «старше 180 дней» жило числом в системном промпте —
                # второй дом того, что расписание знает по аналиту.
                import gp_context as _gpc
                from _time_inject import get_today as _today
                fresh, _ = _gpc._check_lab_freshness(labs, _today())
                lines.append('--- Свежесть по графику контроля ---\n' + fresh)
                sections.append(NL.join(lines))
        except Exception as e:
            # Тихий пропуск раздела читался как «анализов нет» (§7): теперь отказ виден.
            log.warning('build_smart_context: лаб-блок не собран: %s', e)
            sections.append('=== Лабораторные данные: раздел недоступен (сбой чтения). '
                            'Это НИЧЕГО не говорит о том, сдавались анализы или нет. ===')

    return (NL + NL).join(s for s in sections if s.strip())
