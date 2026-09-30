#!/opt/homebrew/bin/python3.11
"""
generate_constitutions.py — генератор нарративных конституций здоровья.

Читает генетические данные из promethease_variants + исторические метрики,
передаёт в Claude и сохраняет как Markdown в constitutions/.

Использование:
    python3 generate_constitutions.py          # все пять
    python3 generate_constitutions.py sleep    # только сон
    python3 generate_constitutions.py --dry-run  # вывод промпта без запроса к API
"""
# INTENT: constitutions — конституции: правила эксплуатации именно твоего организма.
#          Замысел и инварианты — subsystem_intent.yaml, раздел constitutions.
from __future__ import annotations

import llm_client
from _time_inject import get_now, get_today  # seam
import hai_core
import i18n

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).parent
CONSTITUTIONS_DIR = ROOT / "constitutions"
CONSTITUTIONS_DIR.mkdir(exist_ok=True)



def _export_dir() -> Path:
    """Каталог производного .md-экспорта конституций, tenant-aware (2026-07-04).

    Основной пользователь → зеркало из настройки установки (у владельца iCloud), без настройки —
    каталог данных; тенант (HEALTH_DATA_DIR с именем ≠ health) → локальный каталог тенанта. Раньше _save писал жёстко в ICLOUD (владельца) → экспорт
    конституций партнёра затирал бы .md владельца (одинаковые имена файлов
    sleep.md и т.д.) и утекал бы в его iCloud. DB-источник это не задевало, но
    производный экспорт мешался."""
    dd = os.environ.get("HEALTH_DATA_DIR", "")
    if dd and Path(dd).name != "health":
        d = Path(dd) / "constitutions"
    else:
        # Основной пользователь установки: зеркало — НАСТРОЙКА установки (private/infra.yaml,
        # ключ constitutions_mirror; у владельца — iCloud). Нет настройки → каталог данных.
        # До 2026-09-24 путь iCloud стоял константой и создавался ПРИ ИМПОРТЕ модуля: у
        # постороннего один импорт (хоть тестом) заводил папку health/ в его iCloud Drive
        # (приёмка урока установки свежим агентом).
        import infra_config
        d = infra_config.CONSTITUTIONS_MIRROR or (Path(dd) if dd else Path.home() / "health") / "constitutions"
    d.mkdir(parents=True, exist_ok=True)
    return d

# ── Домены и конфигурация ─────────────────────────────────────────────────────

DOMAINS = {
    "sleep": {
        "title": "Сон",
        "file": "sleep.md",
        "genes": ["PER3", "CLOCK", "MTNR1B", "CRY1", "CRY2", "ARNTL", "PER1", "PER2"],
        "topics": ["sleep", "circadian", "melatonin"],
        "prom_domain": "sleep",
        "metric_keys": ["sleep_duration", "deep_sleep", "rem_sleep", "sleep_score"],
    },
    "nutrition": {
        "title": "Питание",
        "file": "nutrition.md",
        "genes": ["MTHFR", "FUT2", "VDR", "BCMO1", "APOE", "TCF7L2", "SLC23A1", "FADS1", "FADS2"],
        "topics": ["folate", "vitamin", "methylation", "absorption", "detox", "nutrient"],
        "prom_domain": "nutrition",
        "metric_keys": [],
    },
    "stress": {
        "title": "Стресс",
        "file": "stress.md",
        "genes": ["COMT", "MAOA", "SLC6A4", "FKBP5", "BDNF", "NR3C1", "CRHR1", "HTR2A"],
        "topics": ["stress", "dopamine", "serotonin", "cortisol", "anxiety", "mood"],
        "prom_domain": "stress",
        "metric_keys": ["hrv", "rhr", "readiness_score"],
    },
    "nervous_system": {
        "title": "Нервная система",
        "file": "nervous_system.md",
        "genes": ["APOE", "BDNF", "COMT", "DTNBP1", "NRG1", "SLC6A4", "KIBRA", "CACNA1C"],
        "topics": ["alzheimer", "cognition", "neuroprotection", "brain", "neurodegeneration"],
        "prom_domain": "stress",
        "metric_keys": ["hrv"],
    },
    "movement": {
        "title": "Движение",
        "file": "movement.md",
        "genes": ["ACTN3", "ACE", "PPARGC1A", "IL6", "ADRB2", "MCT1", "AMPD1", "COL5A1"],
        "topics": ["muscle", "endurance", "recovery", "injury", "cardio", "athletic"],
        "prom_domain": "recovery",
        "metric_keys": ["steps", "active_calories", "rhr", "vo2max"],
        "correlation_agents": ["movement_coach", "energy_coach", "resilience_coach", "cardiology"],
    },
}

# Маппинг домен → агенты из корреляционного анализа
# Конституции строятся ТОЛЬКО на геноме и longitudinal анализе (10 лет).
# Репорты lifestyle-агентов (correlation_analysis) — оперативный слой,
# в конституцию не включаются: они содержат 30-дневные паттерны с конкретными датами.

# ── ГОРИЗОНТ КОНСТИТУЦИИ (решение владельца 2026-09-25) ──────────────────────
# «Конституция — текст о моём устройстве, инструкция по эксплуатации. Всё, что имеет тренд
# на год и дольше, там может быть; локальные события исключены. Речь о значимости
# последствий и сроке, а не о длительности события.» Операция — событие одного дня, но её
# след может держаться годами → входит как ПРИЧИНА. Неделя плохого сна, поездка, протокол
# двухмесячной давности → не входят: у них нет следствия длиной в горизонт, это месячный отчёт.
# Следствие: упоминание даты МОЛОЖЕ горизонта не может быть причиной годового следствия
# (оно ещё не успело длиться год) — поэтому проверка вывода судит просто по возрасту даты.
# Свежие заметки чата, короткие агрегаты восстановления и текущее значение
# анализа описывают оперативный срез. Их включение подменяет долгий горизонт
# документа состоянием на момент генерации.
HORIZON_KEY = "constitutions.horizon_days"
HORIZON_SEED_DAYS = 365   # seed-дефолт (§9 п.4): пишется в system_config, рантайм читает оттуда


def horizon_days() -> int:
    """Горизонт конституции в днях — из system_config; нет ключа → seed пишется и читается."""
    import config_db
    v = config_db.get_config(HORIZON_KEY)
    if v in (None, ""):
        config_db.upsert_config(HORIZON_KEY, value_text=str(HORIZON_SEED_DAYS), category="constitutions",
                                source="generate_constitutions")
        v = config_db.get_config(HORIZON_KEY)
    return int(str(v))


def phase_in_horizon(ph: dict, today: date, horizon: int) -> bool:
    """Подаётся ли фаза longitudinal в конституцию. Поездка — нет (локальное событие).
    Фаза, закончившаяся раньше горизонта, — да: это история, в т.ч. ПРИЧИНА долгого следствия
    (месячный курс лечения много лет назад). Фаза, задевающая последний год и сама короче
    горизонта, — нет: её среднее описывает короткое окно;
    сам курс лечения остаётся в клинической истории (periods)."""
    if ph.get("type") in ("trip", "travel"):
        return False
    try:
        start, end = date.fromisoformat(str(ph["start"])[:10]), date.fromisoformat(str(ph["end"])[:10])
    except Exception:
        return False   # silent-ok: фаза без разбираемых дат не доказывает горизонт
    if end <= today - timedelta(days=horizon):
        return True
    return (end - start).days >= horizon


_RU_MONTHS = (r"(?:январ|january\b)", r"(?:феврал|february\b)", r"(?:март|march\b)",
              r"(?:апрел|april\b)", r"(?:ма[йя]|may\b)", r"(?:июн|june\b)",
              r"(?:июл|july\b)", r"(?:август|august\b)", r"(?:сентябр|september\b)",
              r"(?:октябр|october\b)", r"(?:ноябр|november\b)", r"(?:декабр|december\b)")
# Фразы оперативного окна. «За последние 5 лет» — горизонт, не окно: судится только
# «за последние N дней/недель/месяцев» и «за последнюю неделю/месяц».
_RECENT_RE = (r"за\s+последн\w*\s+(?:\d+\s+)?(?:дн|сут|недел|месяц)\w*",
              r"на\s+(?:прошлой|этой)\s+неделе", r"на\s+неделе\b", r"в\s+(?:этом|прошлом)\s+месяце",
              r"\bвчера\b", r"\bпозавчера\b", r"ближайш\w+\s+(?:месяц|недел)\w*",
              r"\b(?:last|past)\s+(?:\d+\s+)?(?:days?|weeks?|months?)\b",
              r"\b(?:this|next|coming|upcoming)\s+(?:week|month)\b",
              r"\b(?:yesterday|the day before yesterday|recently|lately)\b")


def recent_mentions(text: str, today: date, horizon: int) -> list[str]:
    """Фрагменты текста, привязанные ко времени МОЛОЖЕ горизонта (оракул вывода конституции).

    Судит: ISO-даты, дд.мм.гггг, дд.мм (без года — текущий или прошлый год, ближайший к
    today в прошлом), «<месяц> гггг», и фразы оперативного окна («за последние», «на прошлой
    неделе»…). Строка «**Обновлено:**» — метаданные документа, не судится.
    ЧЕГО НЕ ВИДИТ (вслух): месяц без года («в сентябре»), относительное время другими
    словами («недавно», «с конца лета»), будущие даты (срок протокола «до 31.12.2026» — ловится
    только если он моложе горизонта В ПРОШЛОМ, будущее ловит отдельная ветка ниже)."""
    import re
    out: list[str] = []
    lo = today - timedelta(days=horizon)

    def _judge(frag: str, d: date | None):
        if d is not None and d > lo:
            out.append(frag)

    for line in text.splitlines():
        if line.lstrip().startswith("**Обновлено"):
            continue
        for m in re.finditer(r"\b(20\d\d)-(\d\d)-(\d\d)\b", line):
            try:
                _judge(m.group(0), date(int(m[1]), int(m[2]), int(m[3])))
            except ValueError:
                pass
        # Без года — только дд.мм ровно по две цифры и не часть числа: «6.8 ч», «84.2%»,
        # «20.8 мс» — значения, а не даты (одна цифра после точки). Граница: «12.10» как
        # число будет прочитано датой — ложная тревога вместо пропуска, сторона безопасная.
        for m in re.finditer(r"(?<![\d.,])(\d{1,2})\.(\d{2})(?:\.(20\d\d))?(?![.,]?\d)", line):
            dd, mm, yy = int(m[1]), int(m[2]), m[3]
            if not (1 <= dd <= 31 and 1 <= mm <= 12) or (not yy and len(m[1]) < 2):
                continue
            try:
                if yy:
                    _judge(m.group(0), date(int(yy), mm, dd))
                else:
                    cand = date(today.year, mm, dd)
                    if cand > today:
                        cand = date(today.year - 1, mm, dd)
                    _judge(m.group(0), cand)
            except ValueError:
                pass
        for i, stem in enumerate(_RU_MONTHS):
            for m in re.finditer(rf"\b{stem}\w*\s+(20\d\d)\b", line, flags=re.I):
                _judge(m.group(0), date(int(m[1]), i + 1, 1))
        for pat in _RECENT_RE:
            out += [m.group(0) for m in re.finditer(pat, line, flags=re.I)]
    # Будущее внутри горизонта — срок оперативного протокола («держать до 31.12.2026»):
    # у устройства организма нет календарных сроков вперёд.
    for m in re.finditer(r"\b(\d{1,2})\.(\d{1,2})\.(20\d\d)\b|\b(20\d\d)-(\d\d)-(\d\d)\b", text):
        try:
            d = (date(int(m[3]), int(m[2]), int(m[1])) if m[1]
                 else date(int(m[4]), int(m[5]), int(m[6])))
        except ValueError:
            continue
        if today < d <= today + timedelta(days=horizon):
            out.append(m.group(0))
    return out


# ── Получение данных ──────────────────────────────────────────────────────────

# ── Carrier-status фильтрация SNP ─────────────────────────────────────────────
# resolved / palindromic_het_resolved → проверяем носительство по genotype
# palindromic / multiallelic_ambiguous → сигнал неопределён → unknown
# no_call / no_data / source_conflict  → генотип недоступен  → пропускаем
_SNP_CARRIER_STATUSES   = frozenset({"resolved", "palindromic_het_resolved"})
_SNP_AMBIGUOUS_STATUSES = frozenset({"palindromic", "multiallelic_ambiguous"})
_SNP_SKIP_STATUSES      = frozenset({"no_call", "no_data", "source_conflict"})


def _get_snp_data(domain_cfg: dict) -> dict:
    """Возвращает клинически значимые варианты по генам домена из genetic_variants.

    Фильтрует по статусу носительства (carrier status, 2026-06-26):
    - resolved/palindromic_het_resolved: включается только если effect_allele
      присутствует в genotype (человек является носителем аллеля).
    - palindromic/multiallelic_ambiguous: носительство неопределимо → unknown.
    - no_call/no_data/source_conflict: пропускаются.
    """
    from health_db import get_conn
    genes = domain_cfg["genes"]
    gene_ph = ",".join("?" * len(genes))
    sql = f"""
        SELECT rsid, gene, genotype, significance, conditions,
               domain_tags, clinical_summary,
               effect_allele, effect_allele_status
        FROM genetic_variants
        WHERE gene IN ({gene_ph})
          AND significance NOT IN (
              'Uncertain significance', 'not provided', 'Benign',
              'Likely benign', 'Benign/Likely benign'
          )
          AND significance IS NOT NULL
        ORDER BY CASE significance
            WHEN 'Pathogenic'                          THEN 1
            WHEN 'Pathogenic/Likely pathogenic'        THEN 2
            WHEN 'Likely pathogenic'                   THEN 3
            WHEN 'Pathogenic; risk factor'             THEN 4
            WHEN 'Conflicting interpretations of pathogenicity' THEN 5
            WHEN 'risk factor'                         THEN 6
            WHEN 'association'                         THEN 7
            WHEN 'drug response'                       THEN 8
            WHEN 'functional_variant'                  THEN 9
            WHEN 'protective'                          THEN 10
            ELSE 11
        END
        LIMIT 30
    """
    with get_conn() as conn:
        rows = conn.execute(sql, genes).fetchall()
    result = {"bad": [], "good": [], "unknown": []}
    for r in rows:
        d = dict(r)
        status = d.get("effect_allele_status") or ""

        # Пропускаем: генотип недоступен или конфликт источников
        if status in _SNP_SKIP_STATUSES:
            continue

        # Неопределённое носительство → всегда в unknown
        if status in _SNP_AMBIGUOUS_STATUSES or status not in _SNP_CARRIER_STATUSES:
            result["unknown"].append(d)
            continue

        # Известный статус: проверяем наличие effect_allele в genotype
        effect_allele = (d.get("effect_allele") or "").upper()
        genotype      = (d.get("genotype") or "").upper()
        if not effect_allele:
            # Contract violation: carrier-статус, но effect_allele NULL. НЕ «не носитель»
            # (тихая чистота) — сюрфейсить как «неизвестно» (null_is_unknown_not_clean).
            result["unknown"].append(d)
            continue
        if effect_allele not in genotype:
            continue  # проверенный не-носитель

        sig = (d.get("significance") or "").lower()
        if any(s in sig for s in ("pathogenic", "risk factor", "conflicting")):
            bucket = "bad"
        elif any(s in sig for s in ("protective", "benign")):
            bucket = "good"
        else:
            bucket = "unknown"
        result[bucket].append(d)
    return result


_SLEEP_TIME_COLS = {"sleep_start_hour", "sleep_end_hour"}


def _fmt_sleep_hour(h: float) -> str:
    """Нормализованное время (часы после 20:00) → HH:MM."""
    total_min = int(round(((h + 20) % 24) * 60))
    return f"{total_min // 60:02d}:{total_min % 60:02d}"


def _get_longitudinal_context() -> str:
    """W5K-#175 (2026-05-14): переписано под новую схему raw_output.

    Новая схема longitudinal_analysis: data_range, phases (flat list),
    yearly_trend, top_correlations, lab_metric_correlations, recovery_vs_baseline.
    """
    # Приёмка веры — в belief_contract (единый источник правила для обоих ИИ-читателей).
    # До 2026-07-26 читатель брал последнюю строку как есть: при отказе гейта в конституцию
    # уходила НЕфильтрованная корреляция (аудит validation_gate, P1-01).
    from belief_contract import read_belief

    belief = read_belief()
    if not belief["accepted"]:
        # Веры нет вовсе (не запускался / findings не читаются) — молчим: это не «фильтр не
        # сработал», а отсутствие анализа, и у него свой датчик (check_longitudinal_freshness).
        if belief["reason"] in ("no_report", "unreadable"):
            return ""
        # Отказ обязан быть ВИДИМЫМ: молчаливо пустой раздел неотличим от «связей не нашлось».
        return ("  Продольные корреляции НЕ ПОДАЮТСЯ: последний анализ не прошёл статистический "
                f"фильтр ({belief['reason']}). Сырая связь — ещё не правда о теле.")
    data = belief["data"]

    _age = f", {belief['age_days']} дн. назад" if belief["age_days"] is not None else ""
    lines = [f"  Анализ от {belief['generated_at']}{_age}"]
    if belief["run_failed"]:
        lines.append(f"  ⚠️ последний прогон гейта УПАЛ ({belief['failed_at']}) — вера НЕ обновлялась; "
                     "всё ниже относится к дате выше, а не к сегодняшнему дню.")

    # 1. Диапазон данных
    dr = data.get("data_range", {})
    if dr:
        lines.append(f"  Период данных: {dr.get('start')}–{dr.get('end')} ({dr.get('total_years')} лет)")

    # 2. Клинические фазы (median по фазе)
    phases = data.get("phases", [])
    KEY_METRICS = ["hrv", "resting_hr", "readiness", "sleep_total", "sleep_deep",
                   "sleep_rem", "sleep_score", "sleep_efficiency",
                   "steps", "active_kcal", "spo2_avg", "weight"]
    if phases:
        lines.append("  Клинические фазы (среднее за фазу):")
        for ph in phases:
            n = ph.get("n", 0)
            if n < 10:
                continue
            name = ph.get("phase", "?")
            ptype = ph.get("type", "")
            if not phase_in_horizon(ph, get_today(), horizon_days()):
                continue
            parts = [f"  [{name} ({ptype})] ({ph.get('start')}–{ph.get('end')}, {n}д)"]
            for m in KEY_METRICS:
                if m in ph and ph[m] is not None:
                    parts.append(f"{m}={ph[m]:.1f}" if isinstance(ph[m], (int, float)) else f"{m}={ph[m]}")
            lines.append("  " + "  ".join(parts))

    # 3. recovery_vs_baseline НЕ подаётся (горизонт, 25.09): его «current» — среднее последних
    #    90 дней (longitudinal_analysis, cutoff_90), то есть оперативное окно. Долгий след лечения
    #    конституция видит через фазы и годовой тренд.

    # 4. Годовой тренд (последние 5 лет)
    # Текущий год неполон — его среднее это окно в несколько месяцев, не год (горизонт 25.09).
    yt = [y for y in data.get("yearly_trend", []) if y.get("year") != get_today().year]
    if yt:
        recent = yt[-5:]
        lines.append("  Годовой тренд (последние 5 лет):")
        for y in recent:
            year = y.get("year")
            metrics_str = ", ".join(
                f"{k}={v:.1f}" if isinstance(v, (int, float)) else f"{k}={v}"
                for k, v in y.items() if k != "year" and v is not None
            )
            lines.append(f"    {year}: {metrics_str}")

    # 5. Сильнейшие корреляции (top_correlations, уже отсортированы)
    top_corr = data.get("top_correlations", [])
    strong = [c for c in top_corr if abs(c.get("r", 0)) >= 0.4]
    if strong:
        from correlation_gate import family_label, causal_label, epoch_label   # единый источник формулировок
        lines.append("  Сильные корреляции метрик (|r|≥0.4):")
        for c in strong[:15]:
            # Карантин мерцающих (Ф4 предохранитель): пара впервые вошла в pass-set → НЕ подаём как
            # находку (рычаг/связь) до вердикта онлайн-контроллера. Видна, но помечена честно.
            if c.get("online_status") == "pending_adjudication":
                _suf = " — ⏳ состав менялся между прогонами, не подтверждён как находка (ждёт онлайн-контроллера)"
            else:
                _lab = family_label(c.get("verdict_family"))
                _cau = causal_label(c.get("verdict_causal"))            # рычаг|совпадение|не_знаю (owner-only, поверх A)
                _suf = f" — {_lab}" if _lab else ""
                if _cau:
                    _suf += f"; {_cau}"
            # Профиль по эпохам подаётся ВСЕГДА, когда посчитан — в том числе рядом с карантином:
            # это факт о данных, и он не отменяется вердиктом о составе pass-set.
            _ep = epoch_label(c)
            if _ep:
                _suf += f"; {_ep}"
            lines.append(f"    {c.get('a')} ↔ {c.get('b')}: r={c.get('r'):.3f} (p={c.get('p', '?'):.4f}){_suf}")

    # 6. Лабораторные ↔ ежедневные метрики
    lab_corr = data.get("lab_metric_correlations", [])
    if lab_corr:
        lines.append("  Лаб ↔ метрики (значимые корреляции):")
        for lc in lab_corr[:12]:
            lines.append(f"    {lc.get('lab')} ↔ {lc.get('metric')}: r={lc.get('r'):.3f} (p={lc.get('p', '?'):.4f})")

    # 7. Направленные связи во времени (лаг-семья q_lag, owner-only): предшествование, НЕ причинность
    lagged = data.get("top_lagged", [])
    if lagged:
        from correlation_gate import lag_label
        lines.append("  Направленные связи во времени (предшествование, НЕ доказанная причинность):")
        for lc in lagged[:10]:
            if lc.get("online_status") == "pending_adjudication":   # карантин мерцающей лаг-пары (Ф4)
                _ll_suf = " — ⏳ состав менялся, не подтверждён как находка (ждёт онлайн-контроллера)"
            else:
                _ll = lag_label(lc.get("verdict_lag"))
                _ll_suf = f" — {_ll}" if _ll else ""
            lines.append(f"    {lc.get('predictor')} сегодня → {lc.get('target')} через {lc.get('lag_days')} дн"
                         + _ll_suf)

    return "\n".join(lines)


def _get_metric_summary(metric_keys: list[str]) -> str:
    """Только 10-летний продольный контекст. Короткие агрегаты (30д/90д/365д),
    стресс-профиль и тренировки — оперативный слой, в конституцию не включаются."""
    lines = []
    longitudinal = _get_longitudinal_context()
    if longitudinal:
        lines.append("  === ИСТОРИЧЕСКИЙ КОНТЕКСТ (10 лет данных) ===")
        lines.append(longitudinal)
    # Лаборатория — ТОЛЬКО ряды длиной не меньше горизонта: многолетний тренд
    # описывает устройство, текущее значение и разовый забор — оперативный срез.
    # Их дом — консилиум и месячный отчёт. Поэтому здесь нет статусов
    # ТЕКУЩЕЕ/УСТАРЕЛО, блока однократных измерений и панелей без ряда.
    try:
        import labs_db
        lab_ctx = labs_db.build_lab_history_context(horizon_days=horizon_days())
        if lab_ctx:
            lines.append("  === ЛАБОРАТОРНЫЕ РЯДЫ ДЛИНОЙ ОТ ГОДА (многолетний тренд, не текущее значение) ===")
            lines.append(lab_ctx)
    except Exception:
        pass  # silent-ok: лаб-контекст опционален, конституция генерится и без него
    return "\n".join(lines) if lines else "нет данных"


def _get_medical_history() -> str:
    """
    Читает клиническую историю из таблицы periods.
    Возвращает форматированный текст для промпта конституции.
    Включает только медицинские периоды (не поездки).
    """
    from health_db import get_conn
    MEDICAL_TYPES = {
        "baseline", "diagnostic", "chemoradiation", "surgery",
        "treatment", "immunotherapy", "recovery", "relapse",
        "watchful_waiting", "remission",
    }
    try:
        with get_conn() as conn:
            # PERIODS-SEMANTICS (2026-06-19): + deleted_at IS NULL чтобы
            # soft-deleted (периоды #9/#17)
            # не попадали в constitution narrative.
            rows = conn.execute("""
                SELECT name, type, start_date, end_date, notes
                FROM periods
                WHERE type IN ({}) AND deleted_at IS NULL
                ORDER BY start_date
            """.format(",".join("?" * len(MEDICAL_TYPES))),
            list(MEDICAL_TYPES)).fetchall()
        if not rows:
            return ""
        lines = []
        for r in rows:
            end = r["end_date"] or "настоящее время"
            note = f" — {r['notes']}" if r["notes"] else ""
            lines.append(f"  {r['start_date']} – {end} | {r['name']} ({r['type']}){note}")
        return "\n".join(lines)
    except Exception as e:
        print(f"  ⚠ _get_medical_history ошибка: {e}")
        return ""


def _read_previous(domain_key: str) -> str:
    """Читает предыдущую конституцию из БД (DB-as-source, 2026-06-26).

    До 2026-06-26: читала из локального файла constitutions/<domain>.md,
    который после миграции Option 3 (2026-06-18) больше не обновлялся →
    stale intermediate layer, diff-секция не генерировалась.
    Исправлено: читаем из health_db.get_constitution(), единственного источника правды.
    """
    try:
        from health_db import get_constitution
        rec = get_constitution(domain_key)
        return rec["body_md"] if rec else ""
    except Exception:
        return ""


def _load_epistemic() -> str:
    """Текст эпистемической дисциплины нарратора, если включён флаг.

    EPISTEMIC_DISCIPLINE=on|1|true → возвращает текст skill; иначе "" (поведение
    генератора не меняется, default off). Fail-fast: при включённом флаге и
    сломанном загрузчике падаем громко, а не генерируем молча без дисциплины.
    """
    if os.environ.get("EPISTEMIC_DISCIPLINE", "on").strip().lower() in ("0", "off", "false", "no"):
        return ""
    from epistemic_skill.loader import load_skill
    return load_skill().text + "\n"


def _epistemic_version() -> str:
    """Версия дисциплины, под которой написан текст (штамп провенанса в constitutions.source_version).
    Без неё «конституция написана до правки дисциплины» было не отличить от «после»."""
    if not _load_epistemic():
        return "off"
    from epistemic_skill.loader import load_skill
    return load_skill().version

def _build_prompt(domain_key: str, snp: dict, metrics_txt: str, medical_txt: str,
                  horizon: int = HORIZON_SEED_DAYS) -> str:
    """Генерация конституции с нуля — только геном + longitudinal + клиническая история."""
    cfg = DOMAINS[domain_key]

    def fmt_variants(variants: list, limit: int = 8) -> str:
        if not variants:
            return "  —"
        lines = []
        for v in variants[:limit]:
            sig = v.get("significance", "")
            summary = v.get("clinical_summary") or v.get("conditions") or ""
            lines.append(f"  {v['rsid']} ({v['gene']}) [{sig}]: {summary[:100]}")
        return "\n".join(lines)

    bad_txt  = fmt_variants(snp["bad"])
    good_txt = fmt_variants(snp["good"])
    unk_txt  = fmt_variants(snp["unknown"], limit=4)

    rules = _rules_text()

    return f"""Ты пишешь персональную конституцию здоровья для домена «{cfg["title"]}».

{rules}

---

## Генетические данные (Promethease) — домен «{cfg["title"]}»

### Неблагоприятные варианты (Bad):
{bad_txt}

### Защитные варианты (Good):
{good_txt}

### Варианты неопределённой значимости (выборка):
{unk_txt}

## Клиническая история:
{medical_txt if medical_txt else "  нет данных"}

## Исторический контекст (10 лет данных — longitudinal analysis):
{metrics_txt if metrics_txt else "  нет данных"}

---

{_load_epistemic()}Напиши конституцию СТРОГО на основе данных выше.
Никакого наследования из прошлых версий — документ строится с нуля из генома и longitudinal.

ГОРИЗОНТ ({horizon} дней). Конституция — описание устройства организма, инструкция по эксплуатации,
а не сводка о текущем состоянии. В неё входит только то, у чего значимое следствие держится не меньше
{horizon} дней: генотип, многолетние тренды, устойчивые связи, клинические события как ПРИЧИНЫ долгих
следствий (операция — день, её след в сне — годы). НЕ входят: недели и месяцы, отдельные ночи, поездки,
текущие протоколы и добавки, ближайшие анализы и планы, сроки вперёд. Не называй дат моложе {horizon}
дней и не пиши «за последние N дней/недель», «на прошлой неделе», «ближайший месяц». Рекомендации —
только те, что следуют из закономерностей длиной от {horizon} дней. Даты событий старше горизонта —
годом или месяцем с годом.
ВАЖНО: секция «Базовые рекомендации» обязательна. Конкретные числовые пороги и триггеры.
Формат вывода — только Markdown, начиная с заголовка `{_heading(domain_key)}`.
Дата обновления: {get_today().isoformat()}.
Язык: русский. Стиль: живой нарратив, первое лицо, без таблиц данных в тексте.
"""


def _build_diff_prompt(domain_key: str, old_text: str, new_text: str) -> str:
    """Промпт для генерации diff-секции — отдельный вызов после генерации."""
    cfg = DOMAINS[domain_key]
    return f"""{_load_epistemic()}Ты сравниваешь две версии конституции здоровья для домена «{cfg["title"]}».

СТАРАЯ ВЕРСИЯ:
{old_text}

---

НОВАЯ ВЕРСИЯ (сгенерирована из свежих данных с нуля):
{new_text}

---

Напиши раздел «## Что изменилось» для вставки в начало новой конституции.

Правила:
- Только реальные изменения в выводах, рекомендациях, числовых порогах.
- Если старая версия содержала данные из недоступного источника (например,
  «longitudinal недоступен», «30-дневное окно вместо 10 лет») — отметь как исправление.
- Если раздел или вывод не изменился существенно — не упоминай.
- Bullet-список, конкретно, без воды. Максимум 10 пунктов.
- Только сам раздел «## Что изменилось», без заголовка конституции.
Язык: русский.
"""


# ── Генерация через Claude ────────────────────────────────────────────────────

def _rules_text() -> str:
    """CONSTITUTION_RULES.md для промпта. Строка-переключатель языка (docs-en-root, 29.09) —
    навигация для читателя, не правило для модели: снимается, промпт байт-в-байт прежний."""
    import doc_translation
    return doc_translation.strip_switch(
        (ROOT / "CONSTITUTION_RULES.md").read_text(encoding="utf-8")).lstrip("\n")


def _call_claude(prompt: str, max_tokens: int = 8000) -> str:
    client = llm_client.guarded_client()
    response = client.messages.create(
        model=hai_core.get_model("opus"),
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt + hai_core.answer_language()}],
    )
    return response.content[0].text


# ── Язык человека (нить model-lang, 28.09) ───────────────────────────────────
# Конституцию читает человек (дашборд), поэтому текст — на его языке, а заголовки,
# по которым её режет код (ревью алертов ищет раздел изменений), приводятся к i18n-форме
# детерминированно: модель могла бы перевести «## Что изменилось» и сломать извлечение.
# Промпт ревью алертов остаётся вне i18n; обёртка уведомления — на языке тенанта.
_CHANGED_HEADS = ("## Что изменилось", "## What changed")


def _heading(domain_key: str) -> str:
    import i18n
    lang = i18n.lang_of()
    title = DOMAINS[domain_key]["title"] if lang == i18n.DEFAULT else i18n.t(f"dashboard.constitution.title.{domain_key}", lang)
    return i18n.t("dashboard.constitution.heading", lang, title=title)


def _changed_head_first(section: str) -> str:
    """Первая строка раздела изменений — ровно i18n-заголовок языка человека."""
    import i18n
    body = section.strip()
    first, _, rest = body.partition("\n")
    if first.startswith("## "):
        body = rest.lstrip("\n")
    return i18n.t("dashboard.constitution.changed_heading") + "\n\n" + body


_SPARSE_METRICS_NOTE = (
    "НЕТ longitudinal-данных (новый профиль — носимое устройство ещё не накопило "
    "историю). Опирайся на геном и лабораторные данные. НЕ описывай многодневные "
    "тренды, динамику метрик или «паттерны» — их нельзя обосновать на текущем объёме. "
    "Числовые пороги давай из генетики и общих референсов, а не из личной истории."
)


def _metrics_or_sparse_note(metrics: str) -> str:
    """Возвращает метрики, либо явную пометку об их отсутствии (для genome-only
    тенанта). Пометка запрещает LLM выдумывать тренды по несуществующей истории."""
    if not metrics or metrics == "нет данных":
        return _SPARSE_METRICS_NOTE
    return metrics


def _generate_one(domain_key: str, dry_run: bool = False) -> str:
    cfg = DOMAINS[domain_key]
    print(f"\n→ Генерирую конституцию: {cfg['title']}...", flush=True)

    snp     = _get_snp_data(cfg)
    metrics = _get_metric_summary(cfg["metric_keys"])
    medical = _get_medical_history()
    prev    = _read_previous(domain_key)

    # ── Валидация источников ─────────────────────────────────────────────────
    snp_total = len(snp["bad"]) + len(snp["good"]) + len(snp["unknown"])
    print(f"  SNP: Bad={len(snp['bad'])}, Good={len(snp['good'])}, Unk={len(snp['unknown'])} (итого {snp_total})")
    print(f"  Клиническая история: {'✓ ' + str(medical.count(chr(10))+1) + ' периодов' if medical else '⛔ ПУСТО'}")
    print(f"  Longitudinal: {'✓ ' + str(len(metrics)) + ' символов' if metrics and metrics != 'нет данных' else '⛔ НЕТ ДАННЫХ'}")

    if snp_total == 0:
        print(f"  ⛔ Нет SNP-данных для домена — пропускаем генерацию {cfg['title']}")
        return ""
    if not medical:
        print(f"  ⚠ Клиническая история пуста — конституция будет неполной")
    if not metrics or metrics == "нет данных":
        # Профиль с геномом и без продольного ряда допускает генерацию на SNP
        # с явной пометкой о нехватке наблюдений, БЕЗ выдуманных трендов.
        # Проверяется наличие данных текущего профиля, а не принадлежность тенанту.
        print(f"  ⚠ Нет longitudinal — генерирую {cfg['title']} на геноме, без трендов")
    metrics = _metrics_or_sparse_note(metrics)

    # Чат в конституцию НЕ идёт: reasoning_block содержит свежие заметки и жалобы,
    # которые сами по себе не доказывают устойчивое свойство на длинном горизонте.
    # Отделить устойчивое по temporal_class нельзя:
    # перенесённая операция бывает размечена standing, durable — малая доля фактов, и среди
    # них встречаются открытые вопросы. Устойчивое, что важно для устройства (операции, лечение), приходит
    # через клиническую историю (periods). Самоописание из чата («утром я всегда бодрый») и
    # опросники конституция НЕ видит — это не потеря, а правило владельца 27.09: субъективное
    # в конституцию не идёт (опросники режет labs_db.build_lab_history_context по источнику).
    horizon = horizon_days()
    prompt  = _build_prompt(domain_key, snp, metrics, medical, horizon=horizon)

    if dry_run:
        print("--- ПРОМПТ (dry-run) ---")
        print(prompt[:2000])
        print("...")
        return ""

    # Шаг 1: генерация с нуля + оракул горизонта. Нарушение → ОДНА попытка исправить с
    # перечнем фрагментов; повторное нарушение → конституция НЕ сохраняется (None — сбой,
    # не законный пропуск): старая версия остаётся, отпечаток не пишется, прогон громкий.
    today = get_today()
    new_text = _call_claude(prompt, max_tokens=8000)
    print(f"  Готово: {len(new_text)} символов")
    bad = recent_mentions(new_text, today, horizon)
    if bad:
        print(f"  ⚠ горизонт нарушен ({len(bad)}): {bad[:8]} — одна попытка исправить", flush=True)
        new_text = _call_claude(
            prompt + "\n\nПРЕДЫДУЩИЙ ВАРИАНТ НАРУШИЛ ГОРИЗОНТ — в нём были фрагменты моложе "
            f"{horizon} дней или сроки вперёд: {bad}. Напиши заново без них и без того, что на них "
            "опиралось.", max_tokens=8000)
        bad = recent_mentions(new_text, today, horizon)
        if bad:
            print(f"  ⛔ {cfg['title']}: горизонт нарушен и после исправления: {bad[:8]} — НЕ сохраняю")
            return None

    # Шаг 2: diff с предыдущей версией (отдельный вызов). Прежняя версия, собранная ДО
    # горизонта (в ней есть свежие даты), — не база для сравнения: diff пересказал бы её
    # конъюнктуру. Такой diff не делается.
    if prev and recent_mentions(prev, today, horizon):
        print("  diff пропущен: прежняя версия собрана до правила горизонта")
        prev = ""
    if prev:
        print(f"  Генерирую diff с предыдущей версией...", flush=True)
        diff_prompt = _build_diff_prompt(domain_key, prev, new_text)
        diff_section = _changed_head_first(_call_claude(diff_prompt, max_tokens=1500))
        if recent_mentions(diff_section, today, horizon):
            print("  diff отброшен: в нём фрагменты моложе горизонта")
            diff_section = ""

    if prev and diff_section:
        # Вставляем diff после первой строки заголовка
        lines = new_text.split("\n")
        header_end = 0
        for i, line in enumerate(lines):
            if line.startswith("# "):
                header_end = i + 1
                break
        lines.insert(header_end, "\n" + diff_section + "\n")
        new_text = "\n".join(lines)
        print(f"  Diff добавлен ({len(diff_section)} символов)")

    return new_text


def _save(domain_key: str, text: str):
    cfg = DOMAINS[domain_key]
    # DB-as-source (Option 3, 2026-06-18): нарратив в БД = источник правды.
    try:
        import health_db
        health_db.upsert_constitution(domain_key, text, title=cfg["title"],
                                      source_version=f"generate_constitutions; epistemic={_epistemic_version()}")
        print(f"  БД: constitutions[{domain_key}] обновлена")
    except Exception as e:
        print(f"  ⚠ запись конституции в БД не удалась: {e}")
    # iCloud — производный человекочитаемый export, НЕ источник (constitutions/ вне git).
    # Маркер сверху, чтобы файл не приняли за источник и не правили вручную (anti-drift).
    export_path = _export_dir() / cfg["file"]
    _marker = (
        "<!-- ПРОИЗВОДНЫЙ ЭКСПОРТ из БД (таблица constitutions). Источник правды — БД; "
        "правки в этом файле игнорируются и перезаписываются при следующей генерации. -->\n\n"
    )
    export_path.write_text(_marker + text, encoding="utf-8")
    print(f"  derived export: {export_path}")


# ── Alert Review Agent ────────────────────────────────────────────────────────

def _tg_notify(msg: str) -> bool:
    """Прямая отправка в Telegram без импорта бота."""
    import os
    import urllib.request as _req
    from secrets_paths import secrets_dir
    _sec = secrets_dir()
    token_path = _sec / "telegram_token"
    chat_path  = _sec / "telegram_chat_id"
    if not token_path.exists() or not chat_path.exists():
        print("  ⚠ Telegram secrets не найдены, уведомление пропущено")
        return False
    token   = token_path.read_text().strip()
    chat_id = chat_path.read_text().strip()
    # Telegram ограничивает сообщение 4096 символами
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    for chunk_start in range(0, len(msg), 4000):
        chunk = msg[chunk_start:chunk_start + 4000]
        # Markdown, а при 400 — тот же кусок простым текстом. Текст ревью пишет модель, и
        # непарная «*» или «_» роняла Telegram-разметку: 25.09 ревью после пересборки
        # конституций не дошло до владельца (HTTP 400), осело только в agent_reports.
        for parse_mode in ("Markdown", None):
            body = {"chat_id": chat_id, "text": chunk}
            if parse_mode:
                body["parse_mode"] = parse_mode
            r = _req.Request(url, data=json.dumps(body).encode(),
                             headers={"Content-Type": "application/json"})
            try:
                with _req.urlopen(r, timeout=10):
                    pass
                break
            except Exception as e:
                if parse_mode and getattr(e, "code", None) == 400:
                    print("  ⚠ TG отверг разметку (400) — шлю этот кусок простым текстом")
                    continue
                print(f"  ⚠ Ошибка отправки TG: {e}")
                return False
    return True


def _build_alert_config_excerpt() -> str:
    """Текущая alert-конфигурация (DOMAIN_SIGNALS + абсолютные пороги) из БД,
    как markdown-врезка для prompt'а ревью алертов.

    Вынесено из _run_alert_review (audit 2026-06-17) ради тестируемости:
    раньше блок парсил telegram_bot.py текстом и после переезда конфига в БД
    всегда возвращал пусто (тихий обрыв). Пустая БД → пустая строка, без
    исключения. Ошибка чтения → диагностическая строка, не падение.
    """
    excerpt = ""
    try:
        import health_db as _dbcfg
        _DOMAINS = ["vagal_activation", "stress", "activity", "sleep", "nutrition"]
        _sig_lines = []
        for _dom in _DOMAINS:
            _sigs = _dbcfg.get_domain_signals(_dom)
            if _sigs:
                _parts = ", ".join(
                    f"{s.get('metric')} (r={s.get('r')}, p{s.get('threshold_pct')})"
                    for s in _sigs
                )
                _sig_lines.append(f"- {_dom}: {_parts}")
        if _sig_lines:
            excerpt += "**DOMAIN_SIGNALS (из БД):**\n" + "\n".join(_sig_lines) + "\n\n"
        _floors = _dbcfg.get_absolute_thresholds()
        if _floors:
            _fl_lines = [f"- {f.get('metric')} {f.get('direction')} {f.get('value')}" for f in _floors]
            excerpt += "**Абсолютные пороги (из БД):**\n" + "\n".join(_fl_lines) + "\n"
    except Exception as _e:
        excerpt = f"*(не удалось загрузить alert-config из БД: {_e})*"
    return excerpt


def _run_alert_review(targets: list[str]) -> None:
    """
    После регенерации конституций запускает ревью алерт-логики.

    Читает секции '## Что изменилось' из обновлённых конституций,
    текущие DOMAIN_SIGNALS и абсолютные пороги из БД (см.
    _build_alert_config_excerpt), вызывает Claude (sonnet) и отправляет
    предложения по изменениям в Telegram.

    Результат также сохраняется в agent_reports (type='alert_review').
    """
    print("\n🔍 Запуск ревью алерт-логики после обновления конституций...")

    # 1. Собираем дифф из обновлённых конституций
    diffs = []
    for domain_key in targets:
        cfg  = DOMAINS[domain_key]
        # DB-first; fallback на файл (Option 3, 2026-06-18).
        text = None
        try:
            import health_db as _dbc
            _rec = _dbc.get_constitution(domain_key)
            if _rec:
                text = _rec["body_md"]
        except Exception:
            text = None
        if text is None:
            path = CONSTITUTIONS_DIR / cfg["file"]
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8")
        # Извлекаем секцию "## Что изменилось"
        head = next((h for h in _CHANGED_HEADS if h in text), None)
        if head:
            start = text.index(head)
            # Берём до следующей секции ##
            rest  = text[start + len(head):]
            end   = rest.find("\n## ")
            diff_section = rest[:end].strip() if end > 0 else rest[:1500].strip()
            diffs.append(f"### {cfg['title']} ({domain_key})\n{diff_section}")
        else:
            diffs.append(f"### {cfg['title']} ({domain_key})\n*(секция 'Что изменилось' отсутствует)*")

    if not diffs:
        print("  Нет дифф-секций для анализа")
        return

    # 2. Текущие DOMAIN_SIGNALS + абсолютные пороги — из БД. Раньше парсились
    #    текстом из telegram_bot.py; после переезда обоих блоков в БД парсинг
    #    всегда находил пусто (тихий обрыв, исправлено audit 2026-06-17).
    #    Логика вынесена в _build_alert_config_excerpt() для тестируемости.
    alert_config_excerpt = _build_alert_config_excerpt()

    # 3. Читаем активные протоколы из БД
    protocols_text = ""
    try:
        import health_db as _db
        protos = _db.get_active_protocols()
        if protos:
            protocols_text = "**Активные протоколы:**\n"
            for p in protos:
                protocols_text += f"- [{p.get('domain','?')}] {p.get('title','?')}: {str(p.get('behavior',''))[:120]}\n"
    except Exception as e:
        protocols_text = f"*(не удалось загрузить протоколы: {e})*"

    # 4. Формируем промпт для Claude
    prompt = f"""{_load_epistemic()}Ты — аналитик системы Health OS. Конституции здоровья были только что обновлены.
Твоя задача — определить, нужно ли пересматривать логику алертов на основе изменений.

Конституции — это стратегические документы, определяющие норму для конкретного человека.
Они содержат: интерпретацию генетических данных, результаты анализов, клинический контекст,
правила поведения в разных фазах (baseline/remission/treatment). Когда они меняются —
меняется и то, что считать опасным отклонением.

## Что изменилось в конституциях

{chr(10).join(diffs)}

## Текущая алерт-конфигурация

{alert_config_excerpt}

{protocols_text}

## Твоя задача

Проанализируй изменения конституций и ответь:

1. **Изменились ли нормы** (пороги, контекст, интерпретация метрик) в обновлённых конституциях?
   Если нет явных изменений — напиши "Существенных изменений нет".

2. **Какие конкретные изменения в DOMAIN_SIGNALS или ABSOLUTE_FLOORS стоит рассмотреть?**
   Для каждого предложения укажи:
   - Что именно менять (домен, метрика, порог)
   - Почему (ссылка на конкретный текст из диффа)
   - Насколько срочно (требует немедленного внимания / при случае)

3. **Нужно ли обновить поведение протоколов в БД?**
   Укажи конкретные протоколы и что менять.

Будь конкретным и кратким. Не повторяй текст конституций дословно.
Если изменения незначительны — так и напиши, не раздувай ответ."""

    # 5. Вызываем Claude
    client  = llm_client.guarded_client()
    print("  Вызов Claude (alert review)...")
    response = client.messages.create(
        model=hai_core.get_model("sonnet"),
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )
    review_text = response.content[0].text
    print(f"  Ревью готово: {len(review_text)} символов")

    # 6. Сохраняем в agent_reports
    try:
        import health_db as _db
        import sqlite3
        with _db.get_conn() as conn:
            conn.execute(
                """INSERT INTO agent_reports (agent_type, agent_name, raw_output, date, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                ("alert_review", "alert_review", review_text,
                 get_today().isoformat(),
                 get_now().isoformat()),
            )
        print("  Сохранено в agent_reports (type=alert_review)")
    except Exception as e:
        print(f"  ⚠ Не удалось сохранить в agent_reports: {e}")

    # 7. Отправляем в Telegram
    domains_str = ", ".join(targets)
    tg_msg = i18n.t("constitutions.notice.alert_review", domains=domains_str, review=review_text)
    if _tg_notify(tg_msg):
        print("  ✅ Уведомление отправлено в Telegram")
    else:
        print("  Ревью (Telegram не доступен):")
        print(review_text)


# ── CLI ───────────────────────────────────────────────────────────────────────

# ── Триггер пересборки по новым вводным (решение владельца 2026-09-25) ────────────
# «Пересборка нужна, если есть основания; дальше — только по триггерам, когда появились новые
# вводные». Вводная — то, что меняет СОДЕРЖАНИЕ конституции, а не её числа недели к неделе:
# состав подтверждённых связей в вере, клинические фазы (в горизонте), состав лабораторных рядов
# длиной от горизонта, полные годы годового тренда, медицинские периоды, объём генома
# (до 25.09 вместо рядов стояли даты заборов — см. inputs_snapshot). Средние за год и «восстановление vs baseline» дрейфуют каждую неделю —
# их в отпечаток не берём, иначе «по триггеру» выродилось бы в «каждое воскресенье».
FP_KEY = "constitutions.inputs_fp"
FP_AT_KEY = "constitutions.inputs_fp_at"
CHECKED_KEY = "constitutions.trigger_checked_at"   # пульс расписания: пишется в конце КАЖДОГО успешного прогона
TRIGGER_STALE_DAYS = 8                              # расписание недельное; 8 = неделя + сутки люфта


def _cfg_get(conn):
    import config_db
    return config_db.get_config(HORIZON_KEY, conn=conn)


def inputs_snapshot(conn, belief: dict) -> dict:
    """Вводные конституции как данные (без хэша) — для отпечатка и для объяснения «что изменилось»."""
    snap = {"links": [], "lab_links": [], "lagged": [], "phases": []}
    if belief.get("accepted"):
        d = belief.get("data") or {}
        snap["links"] = sorted(f"{c.get('a')}×{c.get('b')}" for c in d.get("top_correlations") or [])
        snap["lab_links"] = sorted(f"{c.get('lab')}×{c.get('metric')}" for c in d.get("lab_metric_correlations") or [])
        snap["lagged"] = sorted(f"{c.get('predictor')}→{c.get('target')}+{c.get('lag_days')}" for c in d.get("top_lagged") or [])
        # Тот же отбор фаз, что у промпта (phase_in_horizon), и БЕЗ даты конца: у идущей фазы
        # конец = последний день данных, он сдвигался бы каждую неделю и будил пересборку зря.
        _t = get_today()
        _hz = int(str(_cfg_get(conn) or HORIZON_SEED_DAYS))
        snap["phases"] = [f"{p.get('phase')}|{p.get('start')}" for p in d.get("phases") or []
                          if phase_in_horizon(p, _t, _hz)]
        snap["years"] = [y.get("year") for y in d.get("yearly_trend") or [] if y.get("year") != _t.year]
    # До 25.09 здесь были ДАТЫ заборов: каждый новый анализ будил пересборку всех пяти — то есть
    # конъюнктура запускала «инструкцию по эксплуатации». Теперь вводная — только состав
    # лабораторных РЯДОВ длиной от горизонта (тот же критерий, что у промпта: ≥3 точки).
    _h = int(str(_cfg_get(conn) or HORIZON_SEED_DAYS))
    snap["lab_series"] = [r[0] for r in conn.execute(
        "SELECT test_name FROM lab_results WHERE value IS NOT NULL GROUP BY test_name "
        "HAVING COUNT(DISTINCT substr(date,1,10)) >= 3 "
        "AND julianday(MAX(substr(date,1,10))) - julianday(MIN(substr(date,1,10))) >= ? "
        "ORDER BY 1", (_h,)).fetchall()]
    snap["periods"] = [f"{r[0]}|{r[1]}|{r[2]}|{r[3]}" for r in conn.execute(
        "SELECT name, type, start_date, end_date FROM periods WHERE deleted_at IS NULL "
        "AND type NOT IN ('trip', 'travel') ORDER BY start_date, name").fetchall()]
    snap["genome_variants"] = conn.execute("SELECT COUNT(*) FROM genetic_variants").fetchone()[0]
    return snap


def inputs_fingerprint(snap: dict) -> str:
    import hashlib
    return hashlib.sha256(json.dumps(snap, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def _current_fingerprint() -> tuple[str, dict]:
    from belief_contract import read_belief
    from health_db import get_conn
    with get_conn() as conn:
        snap = inputs_snapshot(conn, read_belief())
    return inputs_fingerprint(snap), snap


def _store_fingerprint(fp: str, snap: dict) -> None:
    import config_db
    config_db.upsert_config(FP_KEY, value_text=fp, value_json={"fp": fp, "snap": snap},
                            category="constitutions", source="generate_constitutions")
    config_db.upsert_config(FP_AT_KEY, value_text=str(get_today()), category="constitutions",
                            source="generate_constitutions")


def _mark_checked() -> None:
    import config_db
    config_db.upsert_config(CHECKED_KEY, value_text=str(get_today()), category="constitutions",
                            source="generate_constitutions")


def trigger_silence_days(conn, today) -> int | None:
    """Сколько дней триггер пересборки не отчитывался (None — не отчитывался ни разу).
    Читатель — датчик в integrity_tests: молчание дольше недели = расписание мертво или прогон
    падает, и новые вводные копятся без пересборки."""
    import config_db
    at = config_db.get_config(CHECKED_KEY, conn=conn)
    if not at:
        return None
    return (today - date.fromisoformat(str(at)[:10])).days


def changed_inputs(old: dict | None, new: dict) -> list[str]:
    """Какие вводные сменились (для лога и сообщения). old=None — отпечатка ещё не было."""
    if not old:
        return ["отпечатка ещё не было"]
    return [k for k in new if old.get(k) != new[k]]


def main():
    parser = argparse.ArgumentParser(description="Генератор конституций здоровья")
    parser.add_argument(
        "domain", nargs="?", default="all",
        choices=list(DOMAINS.keys()) + ["all"],
        help="Домен или 'all' для всех пяти"
    )
    parser.add_argument("--dry-run", action="store_true", help="Вывод промпта без API-запроса")
    parser.add_argument("--if-changed", action="store_true",
                        help="Пересобрать все домены, только если сменились вводные (расписание)")
    args = parser.parse_args()

    fp, snap = _current_fingerprint()
    if args.if_changed:
        import config_db
        stored = config_db.get_config(FP_KEY)
        old = stored if isinstance(stored, dict) else None
        if old and old.get("fp") == fp:
            print(f"конституции: новых вводных нет (отпечаток {fp}) — пересборка не нужна")
            _mark_checked()
            return
        print(f"конституции: сменились вводные — {', '.join(changed_inputs((old or {}).get('snap'), snap))}")
        args.domain = "all"

    targets = list(DOMAINS.keys()) if args.domain == "all" else [args.domain]

    # Синхронизируем event-based поля patient_profile перед генерацией
    if not args.dry_run:
        try:
            from profile_reconciler import reconcile as _reconcile
            _changes = _reconcile()
            if _changes:
                print(f"  profile_reconciler: обновлено {len(_changes)} полей")
                for _, k, v in _changes:
                    print(f"    {k} = {v}")
        except Exception as _e:
            print(f"  ⚠ profile_reconciler пропущен: {_e}")

    failed = []
    for domain_key in targets:
        text = _generate_one(domain_key, dry_run=args.dry_run)
        if text is None:          # сбой оракула горизонта (не законный пропуск без генома)
            failed.append(domain_key)
        elif text:
            _save(domain_key, text)

    if failed:
        # Без отпечатка и без пульса: триггер попробует снова в следующее воскресенье, а если
        # сбой стойкий — пульс перестаёт обновляться и check_constitutions_trigger_alive краснеет
        # через TRIGGER_STALE_DAYS (доставка — ночная целостность). Код выхода 1 — для лога.
        print(f"\n⛔ конституции НЕ пересобраны (горизонт): {', '.join(failed)} — прежние версии оставлены")
        sys.exit(1)

    # Отпечаток пишется только после ПОЛНОЙ пересборки (все домены прошли цикл без исключения;
    # законный пропуск домена без генома — не сбой). Один домен или сбой API — отпечаток старый,
    # триггер сработает снова, а не сочтёт вводные учтёнными.
    if not args.dry_run and args.domain == "all":
        _store_fingerprint(fp, snap)
        _mark_checked()

    if not args.dry_run:
        print(f"\n✅ Конституции обновлены: {', '.join(targets)}")
        print(f"   Папка: {CONSTITUTIONS_DIR}")
        # Ревью алерт-логики: конституции определяют норму → норма меняется → алерты должны знать
        try:
            _run_alert_review(targets)
        except Exception as e:
            print(f"  ⚠ Alert review не удался (не критично): {e}")


if __name__ == "__main__":
    main()
