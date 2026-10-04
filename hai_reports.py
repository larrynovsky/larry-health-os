#!/usr/bin/env python3.11
"""
hai_reports — утренний, еженедельный, ежемесячный отчёты + расширенный контекст-блок.
Зависимости: health_db, hai_core.
Внешние (lazy): wellally_consult, pubmed_client.
"""

import llm_client
import json
import hai_core
import logging
from datetime import date, timedelta
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent))
from _time_inject import get_today
from _fmt_helpers import fmt_min
import health_db as db
from hai_core import get_client, get_system_prompt, save_message

log = logging.getLogger(__name__)


# ── Расширенный контекст для отчётов (7д/30д/90д + тренды + анализы) ────

def build_context_block(target: date = None) -> str:
    """
    Полный блок данных для генерации утреннего отчёта.
    Включает: вчера, 7д/30д/90д тренды, стрелки динамики, последние анализы.
    Для чат-запросов используй build_context_block_compact (~500 токенов).
    """
    if target is None:
        target = get_today() - timedelta(days=1)

    stats7    = db.get_stats(7,  target)
    stats30   = db.get_stats(30, target)
    stats90   = db.get_stats(90, target)
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

    def trend_arrow(v7, v90):
        if not v7 or not v90 or v90 == 0: return ""
        pct = (v7 - v90) / v90 * 100
        if pct > 8:  return " ↑"
        if pct < -8: return " ↓"
        return " →"

    lines = [f"=== Данные на {target} ==="]

    sleep   = yesterday.get("sleep") or {}
    hrv_val = (yesterday.get("hrv") or {}).get("avg")
    rhr_val = (yesterday.get("resting_heart_rate") or {}).get("value")
    rd_val  = yesterday.get("readiness_score")
    steps   = yesterday.get("steps")
    spo2    = (yesterday.get("spo2") or {}).get("avg")
    weight  = yesterday.get("weight_kg")

    lines.append("Вчера:")
    lines.append(f"  Сон: {fmt_sleep(yesterday)}")
    if sleep.get("sleepStart"):
        lines.append(f"  Лёг: {sleep['sleepStart'][11:16]}  Встал: {(sleep.get('sleepEnd') or '')[11:16]}")
    if hrv_val:  lines.append(f"  ВСР: {hrv_val:.1f} мс  (7д avg: {stats7.get('avg_hrv','?')})")
    if rhr_val:  lines.append(f"  ЧСС покоя: {rhr_val:.0f}")
    if rd_val:   lines.append(f"  Readiness: {rd_val}")
    if steps:    lines.append(f"  Шаги: {int(steps)}")
    if spo2:     lines.append(f"  SpO2: {spo2:.1f}%")
    if weight:   lines.append(f"  Вес: {weight} кг")

    lines.append("\nПоследние 7 дней (дата | сон | deep | rem | ВСР | score):")
    for i in range(1, 8):
        d   = target - timedelta(days=i)
        row = db.get_day(str(d))
        s   = row.get("sleep") or {}
        hrv = (row.get("hrv") or {}).get("avg")
        sc  = s.get("sleep_score")
        st  = s.get("totalSleep")
        dp  = int((s.get("deep") or 0) * 60)
        rm  = int((s.get("rem")  or 0) * 60)
        lines.append(
            f"  {d.strftime('%d.%m')} | {f'{st:.1f}ч' if st else '—':6} | "
            f"{f'{dp}м' if dp else '—':5} | {f'{rm}м' if rm else '—':5} | "
            f"{f'{hrv:.0f}' if hrv else '—':5} | {f'{sc:.0f}' if sc else '—'}"
        )

    lines.append("\nТренды (7д → 30д → 90д avg):")

    def _s(d, k, mul=1, fmt=".1f"):
        v = d.get(k)
        if v is None: return "—"
        return format(v * mul, fmt)

    lines.append(
        f"  Сон:       {_s(stats7,'avg_sleep')}ч → {_s(stats30,'avg_sleep')}ч → {_s(stats90,'avg_sleep')}ч"
        + trend_arrow(stats7.get('avg_sleep'), stats90.get('avg_sleep'))
    )
    d7  = fmt_min(stats7.get('avg_deep'))
    d30 = fmt_min(stats30.get('avg_deep'))
    d90 = fmt_min(stats90.get('avg_deep'))
    lines.append(
        f"  Deep:      {d7}м → {d30}м → {d90}м"
        + trend_arrow(stats7.get('avg_deep'), stats90.get('avg_deep'))
    )
    lines.append(
        f"  ВСР:       {_s(stats7,'avg_hrv')} → {_s(stats30,'avg_hrv')} → {_s(stats90,'avg_hrv')} мс"
        + trend_arrow(stats7.get('avg_hrv'), stats90.get('avg_hrv'))
    )
    lines.append(
        f"  Readiness: {_s(stats7,'avg_readiness',fmt='.0f')} → {_s(stats30,'avg_readiness',fmt='.0f')} → {_s(stats90,'avg_readiness',fmt='.0f')}"
        + trend_arrow(stats7.get('avg_readiness'), stats90.get('avg_readiness'))
    )
    lines.append(
        f"  Score:     {_s(stats7,'avg_sleep_score',fmt='.0f')} → {_s(stats30,'avg_sleep_score',fmt='.0f')} → {_s(stats90,'avg_sleep_score',fmt='.0f')}"
    )

    # BL-DATA-PARITY-1: всё собранное, а не только ручной список выше.
    _all_metrics = db.render_all_metrics(30, target)
    if _all_metrics:
        lines.append("\n" + _all_metrics)

    # Референс — как в /labs (handlers/reports): бланк строки → мода бланков тенанта → без флага.
    # Прежний литерал-словарь нёс мужской HGB 13.5–17.5 любому тенанту (аудит B26) — число без
    # источника хуже пустоты (labs_db.get_lab_refs).
    KEY_LABS = ['CEA', 'CA19-9', 'WBC', 'HGB', 'PLT', 'NEUTRO_pct', 'CRP', 'Creatinine',
                'ALT', 'AST', 'Albumin', 'Ferritin', 'Glucose', 'Calcium', 'Potassium', 'LDH']
    try:
        recent_labs = db.get_recent_labs(600, KEY_LABS)
        if recent_labs:
            bank = db.get_lab_refs()
            lines.append("\nПоследние ключевые анализы:")
            for lab in recent_labs:
                v    = lab.get('value')
                name = lab['test_name']
                lo, hi = lab.get('ref_low'), lab.get('ref_high')
                ref  = (lo, hi, lab.get('unit') or '') if lo is not None and hi is not None else bank.get(name)
                flag = ""
                if v is not None and ref:
                    if v < ref[0] or v > ref[1]:
                        flag = " ⚠"
                unit = (lab.get('unit') or (ref[2] if ref else '')) or ''
                lines.append(f"  {name:18} {v:>8} {unit:10} ({lab['date']}){flag}")
            last_lab_date = max(l['date'] for l in recent_labs)
            days_since    = (get_today() - date.fromisoformat(last_lab_date)).days
            lines.append(f"  (последний забор: {last_lab_date}, {days_since} дней назад)")
    except Exception as e:
        lines.append(f"\n[лабораторные данные недоступны: {e}]")

    if checkins:
        lines.append("\nПоследние чекины:")
        for c in checkins[-4:]:
            if c.get("question") and c.get("answer") and c["question"] != "recommendation":
                lines.append(f"  [{c['date']}] {c['question'][:60]}")
                lines.append(f"    → {c['answer'][:100]}")

    if exps:
        lines.append("\nАктивные эксперименты:")
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


# ── Утренний отчёт ────────────────────────────────────────────────────────

MORNING_PROMPT = """
Сгенерируй утренний отчёт о здоровье за вчера.

Структура (без заголовков, сплошным текстом):
1. Одна фраза — общая оценка ночи (без имени, без возраста)
2. Ключевые цифры сна: сколько спал, глубокий, REM — с кратким комментарием
3. ВСР и восстановление — в сравнении с его личной нормой
4. Тренд недели — 1–2 предложения что происходит
5. Одна конкретная гипотеза или наблюдение — почему так и что попробовать
6. Финальная строка: один вопрос для чекина — что тебя интересует про вчера

Тон: как будто умный друг прочитал его данные и пишет сообщение.
Длина: 8–12 предложений. Без заголовков. Без списков.
Telegram markdown: *жирный* для ключевых цифр.
"""


def _guarded_report(report: str, kind: str) -> str:
    """Судит текст отчёта против канона и ПРИПИСЫВАЕТ уточнение (не блокирует).

    Почему не блок, как у GP (14.09): утренний бриф и недельный отчёт доставляются по
    расписанию и второй попытки в тот же день не имеют — отклонённый отчёт превращается
    в молчание, а молчание здесь неотличимо от «система умерла». Врач эти тексты не
    читает; читает владелец, и ему уточнение полезнее тишины. Судья общий
    (gp_context.judge_absence_claims), политика — эта строка.
    """
    try:
        import gp_context as _gc
        hits, note = _gc.judge_absence_claims(report)
    except Exception as e:
        log.warning("%s: судья отсутствия не отработал: %s", kind, e)
        return report
    if not hits:
        return report
    log.warning("%s: утверждение об отсутствии против канона (%d): %s", kind, len(hits),
                "; ".join(f"{h['test']} ({h['last_date']})" for h in hits))
    return report + chr(10) + chr(10) + note


def generate_morning_report(target: date = None) -> str:
    """Генерирует утренний отчёт через Claude Sonnet."""
    if target is None:
        target = get_today() - timedelta(days=1)

    client        = get_client()
    system_prompt = get_system_prompt()
    db.init_db()

    context  = build_context_block(target)
    response = client.messages.create(task="hai_reports.generate_morning_report",
        model=hai_core.get_model("sonnet"),
        max_tokens=4096,
        system=system_prompt,
        messages=[{"role": "user", "content": f"{context}\n\n---\n{MORNING_PROMPT}"}]
    )
    report = _guarded_report(llm_client.answer_text(response), "morning")
    save_message("assistant", f"[утренний отчёт {target}]\n{report}")
    return report


# ── Еженедельный отчёт ────────────────────────────────────────────────────

WEEKLY_PROMPT = """
Сгенерируй еженедельный аналитический отчёт о здоровье.

Структура (связный текст, без списков и заголовков):
1. Краткая оценка недели одной фразой — тон разговорный
2. Главные паттерны сна за 7 дней: что стабильно, что менялось, в сравнении с предыдущими 7 днями
3. ВСР и восстановление — недельный тренд и что он означает
4. Активность, шаги, энергия — что можно сказать о физическом состоянии
5. Один вывод: что работает, что нет, и что стоит попробовать на следующей неделе
6. Вопрос: что в этой неделе, с твоей точки зрения, было важным и повлияло на самочувствие?

Тон: разговорный, живой. Не пересказывай цифры механически — интерпретируй.
Длина: 10–15 предложений. Без заголовков. Telegram markdown: *жирный* для выводов.
"""


def generate_weekly_report(end_date: date = None) -> str:
    """Еженедельный отчёт с MDT WellAlly + PubMed доказательной базой."""
    import wellally_consult as mdt
    import pubmed_client as pm

    if end_date is None:
        end_date = get_today() - timedelta(days=1)

    db.init_db()
    stats_this  = db.get_stats(7,  end_date)
    stats_prev  = db.get_stats(7,  end_date - timedelta(days=7))
    stats_30    = db.get_stats(30, end_date)
    stats_90    = db.get_stats(90, end_date)
    recent_labs = db.get_recent_labs(730)

    def pct(a, b):
        if a and b and b != 0:
            return round((a - b) / b * 100)
        return None

    lines = [f"=== Еженедельный отчёт за 7 дней до {end_date} ===\n"]
    lines.append("Последние 7 дней (дата | сон | deep | ВСР | score | шаги):")
    for i in range(1, 8):
        d   = end_date - timedelta(days=i)
        row = db.get_day(str(d))
        s   = row.get("sleep") or {}
        hrv = (row.get("hrv") or {}).get("avg")
        sc  = s.get("sleep_score")
        st  = s.get("totalSleep")
        dp  = int((s.get("deep") or 0) * 60)
        sp  = row.get("steps")
        lines.append(
            f"  {d.strftime('%a %d.%m')} | {f'{st:.1f}ч' if st else '—':6} | "
            f"{f'{dp}м' if dp else '—':5} | {f'{hrv:.0f}' if hrv else '—':5} | "
            f"{f'{sc:.0f}' if sc else '—':5} | {int(sp) if sp else '—'}"
        )
    lines.append("\nЭта неделя vs прошлая:")
    for key, label in [
        ('avg_sleep','Сон'), ('avg_deep','Deep'), ('avg_hrv','ВСР'),
        ('avg_readiness','Readiness'), ('avg_sleep_score','Score'),
    ]:
        v_this = stats_this.get(key)
        v_prev = stats_prev.get(key)
        if v_this is not None and v_prev is not None:
            diff  = pct(v_this, v_prev)
            sign  = "+" if (diff or 0) > 0 else ""
            arrow = "↑" if (diff or 0) > 5 else "↓" if (diff or 0) < -5 else "→"
            lines.append(f"  {label}: {v_this:.1f} vs {v_prev:.1f}  {arrow} {sign}{diff}%")

    base_context = "\n".join(lines)

    log.info("Weekly: запуск MDT...")
    try:
        mdt_result    = mdt.run_mdt_consultation(end_date, period_days=7)
        mdt_synthesis = mdt_result.get("synthesis", "")
    except Exception as e:
        log.warning(f"MDT ошибка: {e}")
        mdt_synthesis = ""

    log.info("Weekly: поиск PubMed...")
    try:
        import clinical_kb as _ckb  # класс состояния тенанта → нейтральный контекст (нить diagnosis-hardcode B)
        _active = _ckb.active_conditions()
        patterns       = pm.detect_patterns_from_stats(stats_this, stats_30, stats_90, recent_labs, _active)
        evidence       = pm.get_evidence_for_patterns(patterns[:3], _active)
        evidence_block = pm.format_evidence_block(evidence)
    except Exception as e:
        log.warning(f"PubMed ошибка: {e}")
        evidence_block = ""

    client        = get_client()
    system_prompt = get_system_prompt()
    full_context  = base_context
    if mdt_synthesis:
        full_context += f"\n\n=== МДТ КОНСУЛЬТАЦИЯ (WellAlly) ===\n{mdt_synthesis}"
    if evidence_block:
        full_context += evidence_block

    weekly_prompt_enhanced = WEEKLY_PROMPT + """

Дополнительно:
- Если в МДТ консультации есть значимые наблюдения — включи их
- Если PubMed показал релевантные исследования — упомяни ключевую находку в 1 предложении
- Не пересказывай все источники — только то что меняет вывод или гипотезу
"""
    response = client.messages.create(task="hai_reports.generate_weekly_report",
        model=hai_core.get_model("sonnet"),
        max_tokens=4096,
        system=system_prompt,
        messages=[{"role": "user", "content": f"{full_context}\n\n---\n{weekly_prompt_enhanced}"}]
    )
    report = _guarded_report(llm_client.answer_text(response), "weekly")
    save_message("assistant", f"[еженедельный отчёт {end_date}]\n{report}")
    return report


# ── Ежемесячный чекап ─────────────────────────────────────────────────────

MONTHLY_PROMPT = """
Это ежемесячный анализ состояния здоровья.

Твои задачи:
1. Оцени тренды за последние 30 и 90 дней — что значимо изменилось, что стабильно
2. Посмотри на список витальных метрик и последние даты сдачи — что пора сдать, что просрочено
3. Сформируй конкретный список задач на ближайший месяц (анализы, обследования, эксперименты)
4. Дай один нестандартный вопрос на размышление о здоровье — не очевидный

Тон: конкретный, практический. Больше выводов, меньше пересказа цифр.
Длина: 12–18 предложений. *Задачи* выдели жирным в тексте. Без списков — сплошной текст.
"""


def generate_monthly_check(target: date = None) -> str:
    """Ежемесячный отчёт с проверкой витальных метрик и задачами."""
    if target is None:
        target = get_today() - timedelta(days=1)

    stats30 = db.get_stats(30, target)
    stats90 = db.get_stats(90, target)

    import infra_config
    config_path = infra_config.cloud_dir("data", "vital_metrics_config.json")
    config = json.loads(config_path.read_text()) if config_path.exists() else {}

    recent_labs = db.get_recent_labs(730)
    lab_last    = {r['test_name']: r['date'] for r in recent_labs}

    lines = [f"=== Ежемесячная проверка: {target} ===\n"]
    lines.append("Тренды 30д vs 90д:")
    for key, label in [
        ('avg_sleep','Сон'), ('avg_deep','Deep'), ('avg_hrv','ВСР'),
        ('avg_readiness','Readiness'), ('avg_sleep_score','Score')
    ]:
        v30 = stats30.get(key)
        v90 = stats90.get(key)
        if v30 and v90:
            if key == 'avg_deep':
                v30, v90, unit = round(v30 * 60), round(v90 * 60), 'м'
            elif key == 'avg_sleep':
                unit = 'ч'
            elif key == 'avg_hrv':
                unit = 'мс'
            else:
                unit = ''
            diff_pct = round((v30 - v90) / v90 * 100) if v90 else 0
            arrow    = "↑" if diff_pct > 5 else "↓" if diff_pct < -5 else "→"
            lines.append(f"  {label}: {v30}{unit} (30д) vs {v90}{unit} (90д) {arrow}")

    lines.append("\nСтатус витальных метрик:")
    overdue  = []
    upcoming = []
    for section_key, section in config.items():
        if section_key.startswith('_') or not isinstance(section, dict):
            continue
        for item in section.get('items', []):
            test_id       = item.get('id', '')
            freq          = item.get('freq_days', 90)
            label         = item.get('label', test_id)
            last_known    = item.get('last_known')
            last_date_str = lab_last.get(test_id) or last_known
            if last_date_str and last_date_str != 'unknown':
                try:
                    last_dt = (
                        date.fromisoformat(last_date_str[:7] + '-01')
                        if len(last_date_str) == 7
                        else date.fromisoformat(last_date_str)
                    )
                    days_since = (target - last_dt).days
                    if days_since > freq:
                        overdue.append(f"{label} (просрочено {days_since - freq}д, последний {last_date_str})")
                    elif days_since > freq * 0.75:
                        upcoming.append(f"{label} (через {freq - days_since}д)")
                except Exception:
                    pass
            else:
                overdue.append(f"{label} (нет данных — никогда не сдавал)")

    if overdue:
        lines.append("  ⚠ Просрочено:")
        for item in overdue[:12]:
            lines.append(f"    — {item}")
    if upcoming:
        lines.append("  ⏰ Скоро (в ближайшие 3–4 недели):")
        for item in upcoming[:8]:
            lines.append(f"    — {item}")

    base_context = "\n".join(lines)

    import wellally_consult as mdt
    import pubmed_client    as pm

    log.info("Monthly: запуск MDT (30д)...")
    try:
        mdt_result    = mdt.run_mdt_consultation(target, period_days=30)
        mdt_synthesis = mdt_result.get("synthesis", "")
    except Exception as e:
        log.warning(f"MDT monthly ошибка: {e}")
        mdt_synthesis = ""

    log.info("Monthly: поиск PubMed (все паттерны)...")
    try:
        recent_labs_2  = db.get_recent_labs(730)
        import clinical_kb as _ckb  # класс состояния тенанта → нейтральный контекст (нить diagnosis-hardcode B)
        _active = _ckb.active_conditions()
        patterns       = pm.detect_patterns_from_stats(stats30, stats90, db.get_stats(180, target), recent_labs_2, _active)
        evidence       = pm.get_evidence_for_patterns(patterns[:5], _active)
        evidence_block = pm.format_evidence_block(evidence, max_per_pattern=2)
    except Exception as e:
        log.warning(f"PubMed monthly ошибка: {e}")
        evidence_block = ""

    full_context = base_context
    if mdt_synthesis:
        full_context += f"\n\n=== МДТ КОНСУЛЬТАЦИЯ (WellAlly, 30д) ===\n{mdt_synthesis}"
    if evidence_block:
        full_context += evidence_block

    client        = get_client()
    system_prompt = get_system_prompt()
    db.init_db()

    response = client.messages.create(task="hai_reports.generate_monthly_check",
        model=hai_core.get_model("sonnet"),
        max_tokens=8192,
        system=system_prompt,
        messages=[{"role": "user", "content": f"{full_context}\n\n---\n{MONTHLY_PROMPT}"}]
    )
    report = _guarded_report(llm_client.answer_text(response), "monthly")
    save_message("assistant", f"[ежемесячный чекап {target}]\n{report}")
    return report
