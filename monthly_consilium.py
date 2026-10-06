#!/usr/bin/env python3.11
"""monthly_consilium.py — Deliberative Council pipeline для генерации гипотез.

Wave 5H (2026-05-14).

Архитектурный принцип:
- Гипотезы — редкое событие (раз в 30 дней), не daily-шум.
- 11 specialists работают коллективно через Round A + Round B + Coordinator.
- Дубликаты устраняются В МОМЕНТЕ через Round B (specialist видит чужие findings).
- Output: 3-7 уникальных гипотез со structured patient_view.

Запуск: python3 monthly_consilium.py [period_days=30]
Расписание: launchd com.larry.health.consilium (1-го числа 04:00).

ГЕНОМНЫЙ КОНТЕКСТ (genome_context.build_genetic_context_block) включается
в _build_consilium_input: без этой зависимости гипотезам недоступен генетический
контекст текущего тенанта.
"""
from __future__ import annotations

import llm_client
import hai_core
import consilium_roster

import asyncio
import json
import logging
import sys
from datetime import date, timedelta
from _time_inject import get_today  # seam
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import health_db as db
import anthropic

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


# Промпты в git (Ф0b 2026-06-18), как в wellally_consult. Старый iCloud-путь
# (.claude/specialists) заархивирован 2026-06-18 — этот читатель был пропущен
# миграцией: с 2026-06-18 по 2026-07-02 консилиум тихо работал на fallback-
# заглушках («Ты — специалист в области X»). Датчик: test_consilium_prompts_present.
SPEC_DIR  = Path(__file__).parent / "specialists"

# Модели ролей — через hai_core.model_for("consilium_specialist"/"consilium_coordinator").

# ── 11 specialists ───────────────────────────────────────────────────────────

def _medical_specialists() -> list[tuple[str, str]]:
    """Единый ростер (consilium_roster) → (имя, промпт-файл). Свёл split-brain
    двух захардкоженных списков; состав = все минус демография тенанта."""
    import consilium_roster
    return [(n, n) for n in consilium_roster.medical_roster()]

def _patient_brief() -> str:
    try:
        import patient_context
        return patient_context.build_patient_brief()
    except Exception:  # silent-ok: профиль недоступен → lifestyle-промпт получит fallback
        return ""


def _lifestyle_participants(brief: str) -> list[tuple[str, str]]:
    """(display, role-промпт) из ЕДИНОГО источника: consilium_roster.LIFESTYLE +
    specialists/lifestyle_<key>.md. Свёл инлайн-промпты monthly к каноническим файлам
    (+ подстановка профиля, как в wellally). Ключ stress_hrv → stress ушёл с инлайном."""
    import consilium_roster
    return [(display, consilium_roster.lifestyle_prompt(key, brief))
            for display, key in consilium_roster.LIFESTYLE]


def _read_medical_prompt(name: str) -> str:
    path = SPEC_DIR / f"{name}.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    # Fallback оставлен для робастности (новое имя в ростере до создания файла),
    # но теперь ГРОМКИЙ: инцидент 2026-06-18..07-02 — консилиум месяц работал
    # на заглушках молча. Fallback без датчика = баг.
    logging.error("ПРОМПТ СПЕЦИАЛИСТА НЕ НАЙДЕН: %s — консилиум деградирует "
                  "на заглушку. Проверь specialists/ и consilium_roster.", path)
    return f"Ты — специалист в области {name}."


# ── Mapping метрик на человеческие имена (W5H-E) ──────────────────────────────

METRIC_HUMAN = {
    "hrv":               "ВСР (вариабельность сердечного ритма)",
    "resting_hr":        "пульс покоя",
    "readiness":         "готовность по Oura",
    "sleep_total":       "общее время сна",
    "sleep_deep":        "глубокий сон",
    "sleep_rem":         "REM-сон",
    "sleep_score":       "качество сна",
    "spo2_avg":          "насыщение крови кислородом ночью (SpO2)",
    "steps":             "шаги",
    "active_kcal":       "активные ккал",
    "weight":            "вес",
    "stress_high_min":   "высокий стресс (минут в день)",
    "recovery_high_min": "высокое восстановление (минут в день)",
    "resilience_level":  "уровень резиленса",
}


def _humanize_metric(key: str) -> str:
    return METRIC_HUMAN.get(key, key)


# ── Сбор structured input (на лету, без pending_signals) ────────────────────

def _self_report_lines(end_date: date, period_days: int) -> list[str]:
    """Самоотчёт за период: баллы опросников и заметки куратора о расхождениях с кольцом.

    28.09.2026 (решение владельца «конфликты разбирает система»): опросники по правилу 27.09
    не идут в конституцию, а из лаб-истории их режет фильтр источника — и до сегодня их не
    видел НИ ОДИН читатель; заметки куратора (survivorship_note) тоже писались в пустоту.
    Месячный консилиум — дом конъюнктуры; здесь они и читаются, с явной пометкой «субъективное»."""
    import json as _json
    start = str(end_date - timedelta(days=period_days))
    out: list[str] = []
    for r in db.get_recent_labs(n_days=period_days, exclude_pro=False):
        if str(r.get("source") or "").startswith("instrument:") and start <= str(r.get("date")) <= str(end_date):
            out.append(f"  - {r['test_name']}: {r.get('value')} {r.get('unit') or ''} ({r.get('date')})".rstrip())
    for m in db.get_memory(category="survivorship_note", n=50):
        if str(m.get("date") or "") >= start:
            try:
                note = _json.loads(m.get("value") or "{}").get("curator_summary") or ""
            except ValueError:
                note = ""
            if note:
                out.append(f"  - заметка куратора ({m.get('date')}): {note[:200]}")
    return out or ["  (за период опросников не заполнялось и заметок куратора нет)"]


def _build_consilium_input(end_date: date, period_days: int) -> str:
    """Готовит structured input для Round A.

    Включает: patient context, clinical history, daily aggregates (прямой SQL),
    drifts (detect_metric_drift), корреляции ТОЛЬКО из принятой веры (belief_contract
    через gp_context._build_longitudinal_correlations_block; дрейф корреляций убран 2026-09-03),
    последние лабораторные, тренировки, геномный контекст (носители значимых
    вариантов, carrier-aware через genome_context). Без хардкода пациента (Rule #9).
    """
    db.init_db()

    # Patient context из БД per-call
    profile_block = "[Профиль пациента: недоступен]"
    history_block = "[История болезни: недоступна]"
    try:
        import hai_core
        profile_block = hai_core._build_patient_profile()
    except Exception as e:
        log.warning(f"profile_block: {e}")
    try:
        import gp_agent
        history_block = gp_agent._build_clinical_history()
    except Exception as e:
        log.warning(f"history_block: {e}")

    # Daily aggregates — прямой SQL на daily_metrics (надёжно, без зависимости от get_stats schema)
    daily_summary_lines: list[str] = []
    try:
        with db.get_conn() as conn:
            row = conn.execute(
                """SELECT
                    COUNT(*) AS n_days,
                    ROUND(AVG(hrv), 1)         AS avg_hrv,
                    ROUND(MIN(hrv), 1)         AS min_hrv,
                    ROUND(MAX(hrv), 1)         AS max_hrv,
                    ROUND(AVG(resting_hr), 1)  AS avg_resting_hr,
                    ROUND(AVG(readiness), 1)   AS avg_readiness,
                    ROUND(AVG(sleep_total), 2) AS avg_sleep_total_h,
                    ROUND(AVG(sleep_deep), 2)  AS avg_sleep_deep_h,
                    ROUND(MIN(sleep_deep), 2)  AS min_sleep_deep_h,
                    ROUND(AVG(sleep_rem), 2)   AS avg_sleep_rem_h,
                    ROUND(AVG(sleep_score), 1) AS avg_sleep_score,
                    ROUND(AVG(steps))          AS avg_steps,
                    ROUND(AVG(spo2_avg), 1)    AS avg_spo2,
                    ROUND(AVG(weight), 1)      AS avg_weight,
                    ROUND(AVG(stress_high_min)) AS avg_stress_high_min,
                    ROUND(AVG(recovery_high_min)) AS avg_recovery_high_min
                FROM daily_metrics
                WHERE date BETWEEN ? AND ?""",
                (str(end_date - timedelta(days=period_days)), str(end_date))
            ).fetchone()
        if row and row["n_days"]:
            d = dict(row)
            n_days = d.get("n_days", 0)
            daily_summary_lines.append(f"  Дней с данными: {n_days} из {period_days}")
            for sql_key, human_label, unit in [
                ("avg_hrv", "ВСР (среднее)", "мс"),
                ("min_hrv", "ВСР (минимум)", "мс"),
                ("max_hrv", "ВСР (максимум)", "мс"),
                ("avg_resting_hr", "Пульс покоя (среднее)", "уд/мин"),
                ("avg_readiness", "Готовность Oura (среднее)", ""),
                ("avg_sleep_total_h", "Общий сон (среднее)", "ч"),
                ("avg_sleep_deep_h", "Глубокий сон (среднее)", "ч"),
                ("min_sleep_deep_h", "Глубокий сон (минимум за период)", "ч"),
                ("avg_sleep_rem_h", "REM-сон (среднее)", "ч"),
                ("avg_sleep_score", "Качество сна (среднее)", ""),
                ("avg_steps", "Шаги (среднее/день)", ""),
                ("avg_spo2", "SpO2 ночное (среднее)", "%"),
                ("avg_weight", "Вес (среднее)", "кг"),
                ("avg_stress_high_min", "Высокий стресс (среднее)", "мин/день"),
                ("avg_recovery_high_min", "Высокое восстановление (среднее)", "мин/день"),
            ]:
                v = d.get(sql_key)
                if v is not None:
                    daily_summary_lines.append(f"  - {human_label}: {v} {unit}".rstrip())
    except Exception as e:
        log.warning(f"daily stats SQL: {e}")
    # BL-DATA-PARITY-1: всё собранное, а не только список выше.
    _all_metrics = db.render_all_metrics(period_days, end_date)
    if _all_metrics:
        daily_summary_lines += ["", *_all_metrics.split("\n")]

    # Drifts через detect_metric_drift (правильное имя функции)
    drift_lines: list[str] = []
    try:
        import hai_analysis
        drifts = hai_analysis.detect_metric_drift(target=end_date, window=7, streak_threshold=3) or []
        for d in drifts:
            metric_human = _humanize_metric(d.get("metric", "?"))
            drift_lines.append(
                f"  - {metric_human}: {d.get('direction', '?')} "
                f"current_7d={d.get('current_7d')}, baseline_30d={d.get('baseline_30d')}, "
                f"delta={d.get('delta_pct', 0):+.1f}%, streak={d.get('streak_days')}д, "
                f"severity={d.get('severity', 'mild')}"
            )
    except Exception as e:
        log.warning(f"drifts compute: {e}")

    # Корреляции — ТОЛЬКО из принятой веры (validation_gate), тем же читателем, что у GP.
    # До 2026-09-03 здесь печатался hai_analysis.detect_correlation_drift — Spearman по
    # 90/90 дней без перестановок и FDR, включая производные пары (шаги↔активные ккал,
    # коэффициент попадал в консилиум): второй производитель `r` мимо гейта, из которого
    # Movement Coach вывел «связь с HbA1c». Замысел hypothesis_experiment.drift_birth_defunct
    # уже назвал суточный дрейф шумом; UC-B-09 ловил ровно это число. Пустая вера подаётся
    # ЯВНО: модель обязана видеть «принятых связей нет», а не тишину, из которой додумает.
    corr_lines: list[str] = []
    try:
        from gp_context import _build_longitudinal_correlations_block
        corr_lines = [ln for ln in _build_longitudinal_correlations_block() if ln]
        if not corr_lines:
            corr_lines = ["  Принятых статистическим гейтом корреляций нет — не называй "
                          "коэффициентов связи между метриками, их система не считала."]
    except Exception as e:
        log.warning(f"belief correlations: {e}")

    # Labs: ПОЛНАЯ многолетняя история + пометка давности по effective-сроку
    # валидности (2026-07-01 перепроводка). ТЕКУЩЕЕ = решение, УСТАРЕЛО = статистика.
    lab_lines: list[str] = []
    try:
        ctx = db.build_lab_history_context()
        if ctx:
            lab_lines = ["  " + ln for ln in ctx.split("\n")]
    except Exception as e:
        log.warning(f"labs: {e}")
    # Фильтр min_points=3 не пропускает нетекущий аналит с единственной точкой.
    # Отдельный контекст разовых измерений сохраняет видимость такой точки;
    # её положение относительно референса не отменяет ограничения фильтра.
    try:
        unrep = db.build_unrepeated_draw_context()
        if unrep:
            lab_lines += ["  " + ln for ln in unrep.split("\n")]
    except Exception as e:  # noqa: BLE001 — не роняем консилиум
        log.warning(f"unrepeated: {e}")  # silent-ok
    # Спец-панели вне биохимии крови (BL-LAB-CANON-2 T4): сводка с ЯВНЫМ тегом
    # давности, чтобы консилиум не принял старые результаты за текущие.
    try:
        spec = db.build_specialized_context()
        if spec:
            lab_lines += ["  " + ln for ln in spec.split("\n")]
    except Exception as e:  # noqa: BLE001 — не роняем консилиум
        log.warning(f"specialized: {e}")  # silent-ok
    # co-draw срез (plan Фаза 1): ОСТРЫЕ отклонения, сошедшиеся в ОДНОМ заборе →
    # «единое событие» (фикс 30 мая: 3 болезни из одного забора). Хронический фон
    # исключён (в). Additive, не роняет консилиум.
    try:
        codraw = db.build_codraw_context()
        if codraw:
            lab_lines += ["  " + ln for ln in codraw.split("\n")]
    except Exception as e:  # noqa: BLE001
        log.warning(f"codraw: {e}")  # silent-ok
    # Cold-start (2026-07-01): трендовой истории нет (новый тенант, 1-2 забора —
    # build_lab_history_context требует ≥3 точки) → подаём ТЕКУЩУЮ панель
    # (последнее значение на аналит), чтобы консилиум видел лабы. Путь владельца (богатая
    # история) не затрагивается — фолбэк только когда lab_lines пуст.
    if not lab_lines:
        try:
            with db.get_conn() as conn:
                rows = conn.execute(
                    "SELECT test_name, value, unit, date, COALESCE(specimen,'blood') sp "
                    "FROM lab_results r WHERE value IS NOT NULL AND date = "
                    "(SELECT MAX(date) FROM lab_results r2 WHERE r2.test_name=r.test_name) "
                    "ORDER BY sp, test_name"
                ).fetchall()
            if rows:
                lab_lines = ["  Текущая панель анализов (последняя дата на аналит; cold-start, трендов пока нет):"]
                for r in rows:
                    u = f" {r['unit']}" if r["unit"] else ""
                    tag = "" if r["sp"] == "blood" else f" [{r['sp']}]"
                    lab_lines.append(f"    {r['test_name']}{tag}: {r['value']}{u} ({r['date']})")
        except Exception as e:
            log.warning(f"labs current-snapshot: {e}")

    # Workouts summary (30д) с None-guard
    workout_summary = "Нет данных"
    try:
        with db.get_conn() as conn:
            wkts = conn.execute(
                "SELECT activity_type, COUNT(*) cnt, AVG(duration_min) avg_min "
                "FROM workouts WHERE date >= ? AND date <= ? "
                "GROUP BY activity_type ORDER BY cnt DESC",
                (str(end_date - timedelta(days=period_days)), str(end_date))
            ).fetchall()
        if wkts:
            parts = []
            for w in wkts:
                avg_min = w["avg_min"] if w["avg_min"] is not None else 0
                act = w["activity_type"] or "?"
                cnt = w["cnt"] or 0
                parts.append(f"{act}: {cnt} раз, в среднем {avg_min:.0f}мин")
            workout_summary = "; ".join(parts)
    except Exception as e:
        log.warning(f"workouts: {e}")

    # Genome context (carrier-aware, 2026-06-26)
    # Только реальные носители значимых вариантов — _zygosity() проверяет
    # effect_allele ∈ genotype, что исключает ложноположительные ClinVar-метки.
    genome_block = ""
    try:
        import genome_context
        genome_block = genome_context.build_genetic_context_block(domain=None, max_variants=50)
    except Exception as e:
        log.warning(f"genome_block: {e}")

    # Фармакогеномика — star-allele calling (Wave 1, CPIC). Если данные устарели
    # или отсутствуют — pharmaco_context возвращает sentinel-строку, консилиум
    # видит явное предупреждение вместо тихой потери данных.
    pharmaco_block = ""
    try:
        import pharmaco_context
        pharmaco_block = pharmaco_context.build_pharmacogenomic_block()
    except Exception as e:
        log.warning(f"pharmaco_block: {e}")

    # Детерминированные черты — Wave 2 (Phase F)
    traits_block = ""
    try:
        import traits_context
        traits_block = traits_context.build_traits_block()
    except Exception as e:
        log.warning(f"traits_block: {e}")

    # Wellness-геномика — Wave 3 (Phase G)
    wellness_block = ""
    try:
        import wellness_context
        wellness_block = wellness_context.build_wellness_block()
    except Exception as e:
        log.warning(f"wellness_block: {e}")

    # Полигенные индексы риска — Wave 4 (Phase H)
    prs_block = ""
    try:
        import prs_context
        prs_block = prs_context.build_prs_block()
    except Exception as e:
        log.warning(f"prs_block: {e}")

    # Сборка input_pkg
    lines = [
        f"=== ВХОДНЫЕ ДАННЫЕ КОНСИЛИУМА (период {period_days}д, {end_date - timedelta(days=period_days)} → {end_date}) ===",
        "",
        "## ПРОФИЛЬ ПАЦИЕНТА",
        profile_block,
        "",
        "## КЛИНИЧЕСКАЯ ИСТОРИЯ",
        history_block,
        "",
        "## СРЕДНИЕ ПОКАЗАТЕЛИ ЗА ПЕРИОД",
        *(daily_summary_lines or ["  (данные недоступны)"]),
        "",
        "## ВЫЯВЛЕННЫЕ ДРЕЙФЫ ПО ОДИНОЧНЫМ МЕТРИКАМ (delta ≥ 10% от baseline_30d)",
        *(drift_lines or ["  (значимых дрейфов не обнаружено)"]),
        "",
        "## ДОЛГОСРОЧНЫЕ КОРРЕЛЯЦИИ (только прошедшие статистический гейт)",
        *corr_lines,
        "",
        "## ЛАБОРАТОРИИ — ПОЛНАЯ ИСТОРИЯ (тренды за годы) + давность",
        "  ТЕКУЩЕЕ = актуально для решений; УСТАРЕВАЕТ/УСТАРЕЛО = только статистика/тренд,",
        "  НЕ трактуй как текущее состояние. Опирайся на тренды и на актуальные значения.",
        *(lab_lines or ["  (нет данных)"]),
        "",
        "## САМООТЧЁТ ЗА ПЕРИОД (субъективное: опросники и их расхождения с кольцом — не анализ и не норма)",
        *_self_report_lines(end_date, period_days),
        "",
        "## ТРЕНИРОВКИ ЗА ПЕРИОД",
        f"  {workout_summary}",
        "",
        "## ГЕНОМНЫЙ КОНТЕКСТ (клинически значимые варианты, только носители)",
        genome_block or "  (данные недоступны)",
        "",
        "## ФАРМАКОГЕНОМИКА (CPIC star-allele, Wave 1)",
        pharmaco_block or "  (данные недоступны)",
        "",
        "## ДЕТЕРМИНИРОВАННЫЕ ЧЕРТЫ (Wave 2, Phase F)",
        traits_block or "  (данные недоступны)",
        "",
        "## WELLNESS ГЕНОМИКА (Wave 3, Phase G)",
        wellness_block or "  (данные недоступны)",
        "",
        "## ПОЛИГЕННЫЕ ИНДЕКСЫ РИСКА (Wave 4, Phase H)",
        prs_block or "  (данные недоступны)",
        "",
        "=== КОНЕЦ ВХОДНЫХ ДАННЫХ ===",
    ]
    return "\n".join(lines)


# ── Round A: 11 specialists discovery ────────────────────────────────────────

ROUND_A_TASK = """
ЗАДАЧА: Ты — эксперт в своей области. Тебе показаны 30-дневные данные пациента
с уже размеченными дрейфами и корреляциями. Найди 1-3 значимых паттерна
в зоне СВОЕЙ специальности.

ПРАВИЛА:
1. Не выдумывай. Опирайся только на данные из payload.
2. Каждый паттерн — JSON-объект с полями:
   - summary: 1-2 предложения, что нашёл (≤30 слов)
   - metrics_involved: список конкретных метрик (как они названы в payload)
   - numbers_from_data: 2-3 числа из payload, подкрепляющие паттерн
   - clinical_meaning: 1-2 предложения, что это значит для пациента
   - what_to_check: 1-2 конкретных проверки (анализы или поведение)
   - confidence: high | medium | low
3. 0-3 паттерна. Если ничего значимого — patterns: [].

ФОРМАТ ОТВЕТА — СТРОГО валидный JSON:
{
  "specialist": "<имя>",
  "patterns": [
    {"summary": "...", "metrics_involved": [...], "numbers_from_data": [...],
     "clinical_meaning": "...", "what_to_check": "...", "confidence": "..."},
    ...
  ]
}
"""


def _roles() -> dict:
    """{участник: промпт} — медицина + lifestyle; совпавшее имя схлопнуло бы участника молча."""
    pairs = [(spec, _read_medical_prompt(key)) for spec, key in _medical_specialists()]
    pairs += list(_lifestyle_participants(_patient_brief()))
    roles = dict(pairs)
    if len(roles) != len(pairs):
        raise ValueError(f"monthly_consilium: duplicate participant name: {[n for n, _ in pairs]}")
    return roles


async def _call_round_a(client, name: str, role: str, input_pkg: str, sem) -> dict:
    """Один specialist в Round A."""
    async with sem:
        log.info(f"  Round A → {name}")
        try:
            loop = asyncio.get_event_loop()
            resp = await loop.run_in_executor(None, lambda: client.messages.create(task="monthly_consilium._call_round_a",
                model=hai_core.model_for("consilium_specialist"),
                max_tokens=3000,
                system=role + "\n\n" + ROUND_A_TASK,
                messages=[{"role": "user", "content": input_pkg}],
            ))
            text = llm_client.answer_text(resp).strip()
        except Exception as e:
            return {"name": name, "ok": False, "patterns": [], "raw": str(e)}

        try:
            from cbcr_hypothesis import _extract_json
            parsed = _extract_json(text)
        except Exception:
            parsed = None
        if not parsed:
            return {"name": name, "ok": False, "patterns": [], "raw": text[:500]}

        patterns = parsed.get("patterns") or []
        if not isinstance(patterns, list):
            patterns = []
        return {"name": name, "ok": True, "patterns": patterns, "raw": text}


async def _run_round_a(input_pkg: str) -> list[dict]:
    client = llm_client.guarded_client()
    sem = asyncio.Semaphore(4)

    roles = _roles()
    askers = {name: (lambda name=name, role=role: _call_round_a(client, name, role, input_pkg, sem))
              for name, role in roles.items()}
    # Все обязаны ответить (consilium_roster.ask_all): не ответивший — повтор, затем отказ.
    answers = await consilium_roster.ask_all(askers, lambda r: bool(r.get("ok")), "monthly_consilium round A")
    return [answers[n] for n in roles]


# ── Round B: каждый видит ПОЛНЫЕ findings остальных ───────────────────────────

ROUND_B_TASK = """
ЗАДАЧА: Ты уже выразил своё мнение в Round A. Теперь ты видишь ВСЕ findings
других 10 специалистов. Перечитай. Если кто-то нашёл то же что и ты — отметь
согласие, не дублируй. Если ты видишь то что они пропустили со своей перспективы —
оставь. Если их выводы заставляют тебя пересмотреть — обнови.

ПРАВИЛА:
1. Возвращай 0-2 финальных уникальных паттерна.
2. Если соглашаешься с чужим выводом — НЕ дублируй (verifies другого, не свой).
3. Если у тебя уникальная перспектива — оставь её.
4. Для каждого паттерна:
   - summary, metrics_involved, numbers_from_data — как в Round A
   - clinical_meaning, what_to_check, confidence
   - agrees_with: список имён специалистов которые нашли то же (если есть)
   - new_insight: bool — это новая перспектива не покрытая никем

ФОРМАТ — СТРОГО валидный JSON:
{
  "specialist": "<имя>",
  "final_patterns": [
    {"summary": "...", "metrics_involved": [...], "numbers_from_data": [...],
     "clinical_meaning": "...", "what_to_check": "...", "confidence": "...",
     "agrees_with": [...], "new_insight": true|false},
    ...
  ]
}
"""


async def _call_round_b(client, name: str, role: str, input_pkg: str,
                       all_findings: list[dict], sem) -> dict:
    """Specialist видит полные findings всех 10 остальных."""
    async with sem:
        log.info(f"  Round B → {name}")
        peer_findings = [f for f in all_findings if f["name"] != name]
        peer_text = "\n\n## FINDINGS ДРУГИХ СПЕЦИАЛИСТОВ (Round A):\n" + json.dumps(
            [{"specialist": f["name"], "patterns": f["patterns"]} for f in peer_findings],
            ensure_ascii=False, indent=2
        )
        own_finding = next((f for f in all_findings if f["name"] == name), {"patterns": []})
        own_text = "\n\n## ТВОИ FINDINGS (Round A) — пересмотри:\n" + json.dumps(
            own_finding.get("patterns", []), ensure_ascii=False, indent=2
        )

        full_input = input_pkg + own_text + peer_text

        try:
            loop = asyncio.get_event_loop()
            resp = await loop.run_in_executor(None, lambda: client.messages.create(task="monthly_consilium._call_round_b",
                model=hai_core.model_for("consilium_specialist"),
                max_tokens=3000,
                system=role + "\n\n" + ROUND_B_TASK,
                messages=[{"role": "user", "content": full_input}],
            ))
            text = llm_client.answer_text(resp).strip()
        except Exception as e:
            return {"name": name, "ok": False, "final_patterns": [], "raw": str(e)}

        try:
            from cbcr_hypothesis import _extract_json
            parsed = _extract_json(text)
        except Exception:
            parsed = None
        if not parsed:
            return {"name": name, "ok": False, "final_patterns": [], "raw": text[:500]}

        finals = parsed.get("final_patterns") or []
        if not isinstance(finals, list):
            finals = []
        return {"name": name, "ok": True, "final_patterns": finals, "raw": text}


async def _run_round_b(input_pkg: str, round_a_findings: list[dict]) -> list[dict]:
    client = llm_client.guarded_client()
    sem = asyncio.Semaphore(4)

    roles = _roles()
    askers = {name: (lambda name=name, role=role: _call_round_b(
                  client, name, role, input_pkg, round_a_findings, sem))
              for name, role in roles.items()}
    answers = await consilium_roster.ask_all(askers, lambda r: bool(r.get("ok")), "monthly_consilium round B")
    return [answers[n] for n in roles]


# ── Coordinator: Sonnet синтезирует N финальных гипотез ──────────────────────

COORDINATOR_TASK = """
ЗАДАЧА: Ты — координатор консилиума. Тебе показаны refined findings от 11
специалистов после двух раундов обсуждения. Твоя задача — синтезировать
3-7 УНИКАЛЬНЫХ гипотез о состоянии пациента.

ПРАВИЛА:
1. 3-7 гипотез — реши сам сколько unique тем. Если найдёшь только 2 — выдай 2.
2. Каждая гипотеза должна быть СТРУКТУРИРОВАНА в patient_view формате (для пациента,
   не врача).
3. Используй peer-attribution: если несколько специалистов согласны — это указывает
   на надёжность.

ФОРМАТ КАЖДОЙ ГИПОТЕЗЫ (СТРОГО):
{
  "theme": "<краткая тема, 2-4 слова>",
  "patient_view": {
    "noticed": "Что заметили. ОБЯЗАТЕЛЬНО назвать конкретные метрики ЧЕЛОВЕЧЕСКИМ
      языком из payload (например 'ВСР' и 'пульс покоя', не 'два показателя').
      ОБЯЗАТЕЛЬНО включить 1-2 числа из данных. 2-3 предложения.",
    "metrics_involved": ["ВСР", "пульс покоя"],
    "numbers_referenced": ["−35%", "за 90 дней"],
    "might_mean": "Возможное объяснение простым языком, без терминов 'sub-acute',
      'диссоциация', 'нейротоксичность'. Можно оставить 'TSH', 'SpO2' — это
      аббревиатуры. 2-3 предложения.",
    "possible_causes": [
      {"cause": "...", "how_to_check": "...", "supported_by": ["имя specialist"]}
    ],
    "do_now": "1-2 конкретных действия с timeline (например 'сдать TSH в 14 дней').",
    "consult_when": "Операциональный threshold для Healz.ai. Конкретные числа."
  },
  "consensus": {
    "specialists_supporting": ["имена specialists которые нашли эту тему"],
    "count": 3
  },
  "medical_view": {
    "qualifiers": ["semantic Bordage qualifiers: acuteness, course, severity"],
    "differential": [{"diagnosis": "...", "rule_out_by": "..."}, ...],
    "decision_threshold": "..."
  }
}

ФОРМАТ ОТВЕТА — СТРОГО валидный JSON:
{
  "hypotheses": [
    {... как выше ...},
    ...
  ],
  "no_hypotheses_reason": null | "пояснение если 0 гипотез"
}

ЗАПРЕЩЁННЫЕ ТЕРМИНЫ в patient_view (любое поле):
sub-acute, monotonic, диссоциация, дисфункция, нейротоксичность, патогенез,
ятрогенный, falsification, etiological, антикорреляция, decision threshold,
illness script, semantic qualifiers, два показателя, один из показателей.

ЗАПРЕЩЕНО в patient_view: абстрактные обороты типа "два показателя", "связь
между ними", "определённая метрика". Всегда называй метрику конкретно из payload.

ЗАПРЕЩЕНО переписывать СРЕДНИЕ значения как ЕЖЕДНЕВНЫЕ факты.
Все агрегаты в payload (`avg_*`, "среднее", "типичное") описывают усреднение за
период, не конкретные сутки. Конкретное значение в любые отдельные сутки может
отклоняться от среднего значительно.

✘ НЕЛЬЗЯ: "Каждый день стресс 95 минут" (если `avg_stress_high_min=95`)
✘ НЕЛЬЗЯ: "Шаги составляют 6200 в день" (если `avg_steps_7d=6200`)
✘ НЕЛЬЗЯ: "Глубокий сон длится 1.1 часа" без слова "в среднем"

✓ МОЖНО: "За последние 7 дней среднее число шагов — 6200"
✓ МОЖНО: "В среднем за месяц глубокий сон 1.1 ч; в худшую ночь — 0.4 ч"
✓ МОЖНО: "Высокий стресс в среднем 95 мин/день за период"

Для одиночных измерений (labs от конкретной даты, single event) — можно без
"в среднем", но указывай дату: "В анализе крови от 3 марта глюкоза 5.4 ммоль/л".

Для streak'ов из drift detection ("streak=7д") — можно: "снижение держится 7 дней
подряд" — это уже не среднее, а явление с длительностью.
"""


def _run_coordinator(input_pkg: str, round_b_findings: list[dict]) -> dict:
    client = llm_client.guarded_client()

    findings_text = "\n\n## REFINED FINDINGS ОТ 11 СПЕЦИАЛИСТОВ (после Round B):\n" + json.dumps(
        [{"specialist": f["name"], "final_patterns": f["final_patterns"]}
         for f in round_b_findings if f["ok"]],
        ensure_ascii=False, indent=2
    )

    log.info("  Coordinator синтезирует...")
    try:
        resp = client.messages.create(task="monthly_consilium._run_coordinator",
            model=hai_core.model_for("consilium_coordinator"),
            max_tokens=20000,  # 04.10 решение владельца: замер — текст 14.6k и 15.1k при 16k (94%); 06-26: 8000→16000
            system=COORDINATOR_TASK + hai_core.answer_language(),
            messages=[{"role": "user", "content": input_pkg + findings_text}],
        )
        text = llm_client.answer_text(resp).strip()
    except Exception as e:
        log.error(f"Coordinator failed: {e}")
        return {"hypotheses": [], "no_hypotheses_reason": f"API error: {e}"}

    try:
        from cbcr_hypothesis import _extract_json
        parsed = _extract_json(text)
    except Exception:
        parsed = None
    if not parsed:
        log.warning(f"Coordinator не вернул JSON. text[:300]: {text[:300]}")
        if getattr(resp, "stop_reason", None) == "max_tokens":
            # Обрыв на пределе ответа выглядел как «консилиум не нашёл тем» — месяц терялся молча
            # (замер 04.10: текст 15.1k при пределе 16k). В инженерную очередь, не человеку.
            import i18n
            import notify
            notify.fault(i18n.t("monthly_consilium.fault.answer_limit", "ru",
                                tokens=getattr(resp.usage, "output_tokens", "?")), person_key=None)
            return {"hypotheses": [], "no_hypotheses_reason": "answer limit reached"}
        return {"hypotheses": [], "no_hypotheses_reason": "JSON parse failed"}

    return parsed


# ── Save + Notify ────────────────────────────────────────────────────────────

def _save_consilium_hypothesis(hyp: dict) -> int | None:
    """Сохраняет одну hypothesis из consilium output.

    Возвращает memory_id, либо None если гипотеза — семантический дубль уже
    открытой (semantic dedup, зеркало literature_curator/survivorship_curator;
    путь консилиума раньше был единственным источником без этой проверки).
    """
    import hai_hypotheses as hh
    import hypothesis_semantic_check as semcheck
    pv = hyp.get("patient_view") or {}
    mv = hyp.get("medical_view") or {}
    consensus = hyp.get("consensus") or {}

    # save_hypothesis ожидает плоские поля mechanism/prediction/test
    mechanism = pv.get("might_mean") or "—"
    prediction = (pv.get("possible_causes") or [{}])[0].get("how_to_check") or "—"
    test = pv.get("do_now") or "—"
    observation = pv.get("noticed") or hyp.get("theme") or "—"

    # Semantic dedup — не плодим вторую открытую гипотезу об одном явлении.
    # На дубль пропускаем сохранение (аудит пишет сам semcheck в dedup_skipped).
    try:
        is_dup, existing_id, reason = semcheck.check(observation[:400])
        if is_dup:
            log.info(f"  dedup skip: existing #{existing_id} — {reason[:60]}")
            return None
    except Exception as e:
        log.warning(f"semcheck failed: {e} (proceeding anyway)")

    memory_id = hh.save_hypothesis(
        observation=observation,
        mechanism=mechanism,
        prediction=prediction,
        test=test,
        trigger="monthly_consilium",
        trigger_subtype=f"consensus_{consensus.get('count', 0)}",
        resolution_type="needs_specialist",
    )

    # Сохраняем полный payload в hypotheses_cbcr
    try:
        full_payload = {
            "theme": hyp.get("theme"),
            "patient_view": pv,
            "medical_view": mv,
            "consensus": consensus,
            "provenance": {
                "generator": "monthly_consilium",
                "model": f"{hai_core.model_for('consilium_specialist')} + {hai_core.model_for('consilium_coordinator')}",
            },
        }
        db.save_cbcr_payload(
            memory_id=memory_id,
            payload_json=json.dumps(full_payload, ensure_ascii=False),
            structural_score=consensus.get("count", 0),
            confidence_level="medium",
            generated_by="monthly_consilium",
            model=f"{hai_core.model_for('consilium_specialist')}+{hai_core.model_for('consilium_coordinator')}",
        )
    except Exception as e:
        log.warning(f"save_cbcr_payload failed for {memory_id}: {e}")

    return memory_id


def _notify_consilium_hypothesis(memory_id: int, hyp: dict):
    """Telegram уведомление со structured patient_view + peer-attribution."""
    import hai_hypotheses as hh
    import i18n
    lang = i18n.lang_of()
    pv = hyp.get("patient_view") or {}
    consensus = hyp.get("consensus") or {}
    theme = hyp.get("theme") or i18n.t("monthly_consilium.notice.new_hypothesis", lang)
    n = consensus.get("count", 0)
    supporters = consensus.get("specialists_supporting") or []

    causes_text = ""
    for c in (pv.get("possible_causes") or [])[:4]:
        cause = c.get("cause", "")
        check = c.get("how_to_check", "")
        sup = c.get("supported_by") or []
        sup_text = i18n.t("monthly_consilium.notice.supporters", lang, supporters=", ".join(sup)) if sup else ""
        causes_text += i18n.t("monthly_consilium.notice.cause", lang,
                              cause=cause, supporters=sup_text, check=check)

    text = i18n.t("monthly_consilium.notice.message", lang,
                  memory_id=memory_id, theme=theme, count=n,
                  supporters=", ".join(supporters[:5]), noticed=pv.get("noticed", "—"),
                  causes=causes_text, do_now=pv.get("do_now", "—"),
                  consult_when=pv.get("consult_when", "—"))
    hh._notify_specialist(text + i18n.t("hypotheses.notice.silence", lang),
                          reply_markup=hh.notice_keyboard(memory_id, lang))


def _alert_food_generation_gap(reason: str) -> None:
    """Громкий датчик (data-in-code-9 #3): месячная генерация диет-правил дала ПУСТО.
    Раньше парс-сбой/падение уходили в тихий log.warning под двумя `except` — склад молча
    оставался пуст, эндшпиль вставал невидимо. Отказ идёт в notify.fault;
    журнал читает integrity, разбор выполняет ночной цикл."""
    import notify
    log.error("food-rule generation GAP: %s", reason)
    try:
        notify.fault("monthly_consilium: food-rule generation returned no rules", person_key=None)
    except Exception as e:  # silent-ok: уже залогировано ERROR выше
        log.error("food-gen gap alert send failed: %s", e)


# ── Main pipeline ────────────────────────────────────────────────────────────

def _incomplete(e) -> list[int]:
    """Месячный консилиум идёт из launchd/cron: исключение осталось бы в лог-файле. Неполного
    итога нет (решение владельца 04.10) — сбой в инженерную очередь, гипотез месяца нет."""
    import notify
    notify.fault(f"monthly_consilium: {e.label}: {', '.join(e.missing)} / {e.total}", person_key=None)
    return []


def run(period_days: int = 30) -> list[int]:
    """Запуск monthly consilium. Возвращает список memory_id сохранённых гипотез."""
    end_date = get_today() - timedelta(days=1)
    log.info(f"Monthly consilium: {end_date - timedelta(days=period_days)} → {end_date}")

    log.info("Building consilium input package...")
    input_pkg = _build_consilium_input(end_date, period_days)
    log.info(f"  input_pkg: {len(input_pkg)} chars")

    log.info("Round A: 11 specialists discovery (Haiku, parallel sem=4)...")
    try:
        findings_a = asyncio.run(_run_round_a(input_pkg))
    except consilium_roster.ConsiliumIncomplete as e:
        return _incomplete(e)
    a_count = sum(len(f["patterns"]) for f in findings_a if f["ok"])
    log.info(f"  Round A: {a_count} findings от {sum(1 for f in findings_a if f['ok'])} specialists")

    log.info("Round B: peer review с полными findings (Haiku, parallel sem=4)...")
    try:
        findings_b = asyncio.run(_run_round_b(input_pkg, findings_a))
    except consilium_roster.ConsiliumIncomplete as e:
        return _incomplete(e)
    b_count = sum(len(f["final_patterns"]) for f in findings_b if f["ok"])
    log.info(f"  Round B: {b_count} refined findings")

    log.info("Coordinator (Sonnet): синтез финальных гипотез...")
    coord_result = _run_coordinator(input_pkg, findings_b)
    hypotheses = coord_result.get("hypotheses") or []
    log.info(f"  Coordinator: {len(hypotheses)} unique hypotheses")

    if not hypotheses:
        reason = coord_result.get("no_hypotheses_reason") or "(no reason given)"
        log.info(f"Консилиум не нашёл значимых тем за период: {reason}")
        return []

    saved_ids: list[int] = []
    for hyp in hypotheses:
        try:
            mid = _save_consilium_hypothesis(hyp)
            if mid is None:            # семантический дубль — пропущено, не уведомляем
                log.info(f"  ⊘ dedup skip: {hyp.get('theme', '')[:60]}")
                continue
            saved_ids.append(mid)
            try:
                _notify_consilium_hypothesis(mid, hyp)
            except Exception as e:
                log.warning(f"notify failed for {mid}: {e}")
            log.info(f"  ✓ saved hypothesis #{mid}: {hyp.get('theme', '')[:60]}")
        except Exception as e:
            log.error(f"  save failed: {e}")

    # Сохраняем raw consilium output в agent_reports для аудита
    try:
        db.save_agent_report(
            agent_type="monthly_consilium",
            agent_name="monthly_consilium",
            date_str=str(end_date),
            has_findings=1 if saved_ids else 0,
            data_queried=["drifts", "correlations", "labs", "workouts", "genome"],
            pubmed_ids=[],
            peers_reviewed=[],
            changes_summary=f"Saved {len(saved_ids)} hypotheses from 11 specialists × 2 rounds",
            findings=json.dumps({
                "input_pkg_chars": len(input_pkg),
                "round_a_findings_count": a_count,
                "round_b_findings_count": b_count,
                "coordinator_hypotheses_count": len(hypotheses),
                "saved_memory_ids": saved_ids,
            }, ensure_ascii=False),
            recommendations=None,
            raw_output={
                # v3 A0 (plan_consilium_under_manifest 2026-07-08): заморозка входа для
                # форвард-реплея. coord_result/findings_b могут содержать несериализуемое →
                # упадёт в объемлющий try/except (log.warning); сохранение гипотез не рушится.
                "input_pkg": input_pkg,
                "findings_b": findings_b,
                "coordinator_result": coord_result,
            },
            period_days=period_days,
        )
    except Exception as e:
        log.warning(f"agent_report save failed: {e}")

    # data-in-code-9 ФЛИП: промоутим ПРОШЛЫЙ теневой рулбук в active ПЕРЕД новой генерацией
    # (1-цикл лаг, DESIGN §6.1) — свежие правила месяц живут в shadow, владелец видит их в сводке
    # прежде чем они начнут применяться. Пол fail-closed в наложении — небезопасное не пройдёт.
    try:
        import generated_food_rules as _gfr
        _np = _gfr.promote_shadow_to_active()
        log.info(f"  food-rules promote: {_np} shadow→active")
    except Exception as e:  # silent-ok: промоушен не критичен для консилиума
        log.warning(f"food-rule promote failed: {e}")

    try:
        import food_rule_generator
        _fr = food_rule_generator.generate_shadow(input_pkg, period_days=period_days)
        log.info(f"  food-rules shadow: saved={_fr['n_saved']} rejected={_fr['n_rejected']} "
                 f"total={_fr['n_total']}")
        if _fr["n_saved"] == 0:  # склад не пополнился — громкий датчик (data-in-code-9 #3)
            reason = ("модель ответила, но 0 предложений распарсилось"
                      if _fr["n_total"] == 0
                      else f"все {_fr['n_rejected']} предложений отклонены полом/схемой")
            _alert_food_generation_gap(reason)
    except Exception as e:  # генерация не критична для консилиума, НО не молчим о дыре
        log.warning(f"food_rule_generator shadow pass failed: {e}")
        _alert_food_generation_gap(f"генератор упал: {e}")

    print(f"\n{'='*70}")
    print(f"MONTHLY CONSILIUM — {end_date} ({period_days} дней)")
    print(f"{'='*70}")
    print(f"Сохранено гипотез: {len(saved_ids)}")
    for mid, hyp in zip(saved_ids, hypotheses[:len(saved_ids)]):
        n = (hyp.get("consensus") or {}).get("count", 0)
        print(f"  #{mid} ({n}/11): {hyp.get('theme', '')}")
    print(f"{'='*70}\n")

    return saved_ids


if __name__ == "__main__":
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    run(days)
