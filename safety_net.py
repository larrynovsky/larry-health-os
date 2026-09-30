#!/usr/bin/env python3.11
"""
safety_net.py — детерминированный safety net.

НЕ использует LLM. Только пороговые значения и арифметика.
GP-агент получает результаты как контекст, но не является гейткипером.

Уровни:
  WARN     — обратить внимание, упомянуть в утреннем отчёте
  URGENT   — отдельное срочное сообщение в Telegram до отчёта
  CRITICAL — немедленное уведомление, независимо от расписания
"""

import logging
import i18n
from datetime import date, timedelta
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))
from _time_inject import get_today
import health_db as db

log = logging.getLogger(__name__)

WARN     = "warn"
URGENT   = "urgent"
CRITICAL = "critical"

# «Судить нечем»: порог есть, но системе недостаёт данных для суждения —
# например, аналит не отображён в lab_name_loinc или у кратного порога нет референса.
# Это инженерный долг, а не тревога о здоровье. Такие записи не идут в alerts
# (бриф, контекст GP, срочные сообщения), а сохраняются отдельно у тенанта;
# их читает ночной разбор (integrity_tests.check_safety_net_can_judge).
CANNOT_JUDGE = "cannot_judge"
CANNOT_JUDGE_KEY = "safety_net.cannot_judge"
# Запись старше — не «сейчас судить нечем», а «safety_net давно не бегал» (это другой
# датчик). Системная механика (§9 класс 2), не клиническая величина.
CANNOT_JUDGE_FRESH_DAYS = 3

# ── РЕЗЕРВ порогов (safety-net-thresholds E1, 2026-07-18) ─────────────────────
# _FALLBACK_* — снимок значений на случай, когда absolute_thresholds/lab_trend_thresholds
# недоступны (пустой сид / БД offline). Рантайм-источник — БД (rules_db); loader'ы ниже
# читают её и при провале деградируют к этим резервам + ГРОМКИЙ алерт (не тихий литерал,
# а fail-static с сигналом, §9). Резерв ОБЯЗАН быть равен сиду — держит coherence-тест
# (test_safety_net_thresholds). Числа-нормы отсюда уходят в БД; unit/note/label — метки.
# _FALLBACK_LAB — снимок ТОГО ЖЕ документа, что и сид (data/norm_docs/ctcae_lab_v5.0.json,
# нить norm-from-documents 2026-09-02): metric → список строк {direction, band, kind, baseline,
# value, unit, note}. Числа не набраны — выведены norm_documents.import_ctcae из NCI CTCAE v5.0.
# Прежний 16-строчный литерал был пересказом CTCAE по памяти модели (HGB 12/10/8 = грейды
# анемии; PLT 100/50/20 вместо 75/50/25; ALT «3x/7x» вместо 3x/5x/20x) — ровно тот шаг
# «между таблицей и базой», который убран. Amylase (личный порог владельца) в резерве нет:
# при недоступной БД он не сторожится — сказано вслух, не спрятано.

# Emergency reserve: dictionary/formatting failure must not silence an urgent alert.
# Russian text is an exact copy; English keeps the same urgency and deadline.
_SAFETY_TEXT_FALLBACKS = {
    'safety.note.different_labs': {
        'ru': '{first_val} → {last_val} (сырые {pct:+.0f} %; по доле от ULN {first_val}/{r1:g} → {last_val}/{r2:g} = {pct_share:+.0f} %) — РАЗНЫЕ лаборатории (референс {r1:g} → {r2:g}), RCV неприменим',
        'en': '{first_val} → {last_val} (raw {pct:+.0f} %; as a fraction of ULN {first_val}/{r1:g} → {last_val}/{r2:g} = {pct_share:+.0f} %) — DIFFERENT laboratories (reference {r1:g} → {r2:g}), RCV does not apply',
    },
    'safety.note.unknown_lab': {
        'ru': '{first_val} → {last_val} — лаборатория не определена (нет референса), RCV неприменим',
        'en': '{first_val} → {last_val} — laboratory unknown (no reference range), RCV does not apply',
    },
    'safety.note.confirm_sample': {
        'ru': ' — по одному забору: подтвердить повторным (EGTM 2014: any increase must be confirmed with a second sample)',
        'en': ' — based on one sample: confirm with a repeat (EGTM 2014: any increase must be confirmed with a second sample)',
    },
    'safety.note.trend': {
        'ru': 'тренд за {count} измерения: {basis}',
        'en': 'trend over {count} readings: {basis}',
    },
    'safety.note.spo2': {
        'ru': 'SpO2 {spo2_avg:.1f}% ниже порога {limit}%',
        'en': 'SpO2 {spo2_avg:.1f}% below the threshold of {limit}%',
    },
    'safety.note.readiness': {
        'ru': 'Readiness {readiness} — тело требует восстановления',
        'en': 'Readiness {readiness} — your body needs to recover',
    },
    'safety.note.hrv': {
        'ru': 'HRV {hrv_today:.0f}ms — падение {drop_pct:.0f}% vs 30d avg {hrv_30d:.0f}ms',
        'en': 'HRV {hrv_today:.0f}ms — a drop of {drop_pct:.0f}% vs 30d avg {hrv_30d:.0f}ms',
    },
    'safety.note.resting_hr': {
        'ru': 'ЧСС покоя {rhr_today} — рост +{rise:.0f} bpm vs 30d avg {rhr_30d}',
        'en': 'Resting heart rate {rhr_today} — a rise of +{rise:.0f} bpm vs 30d avg {rhr_30d}',
    },
    'safety.label.resting_hr': {
        'ru': 'ЧСС покоя',
        'en': 'Resting heart rate',
    },
    'safety.head.critical': {
        'ru': '🚨 Срочно: пройдена опасная граница',
        'en': '🚨 Urgent: a danger line has been crossed',
    },
    'safety.head.urgent': {
        'ru': '⚠️ Важно: заметное ухудшение',
        'en': '⚠️ Important: a noticeable worsening',
    },
    'safety.data_doubt': {
        'ru': 'Сегодня я нашёл у себя сбой данных, поэтому эти цифры могут быть неточными. Если самочувствие обычное — перемерь, прежде чем тревожиться.',
        'en': 'Today I found a data fault on my side, so these numbers may be inaccurate. If you feel as usual, measure again before worrying.',
    },
    'safety.what_to_do': {
        'ru': 'Что делать:\n• Если чувствуешь себя плохо — сразу обратись к врачу или вызови скорую.\n• Если самочувствие обычное — перепроверь (кольцо могло сидеть неплотно, анализ можно пересдать) и покажи это сообщение врачу в ближайшие дни.\nЭто не диагноз: это сигнал, что показатель вышел за границу.',
        'en': 'What to do:\n• If you feel unwell, see a doctor right away or call an ambulance.\n• If you feel as usual, check again (the ring may have been loose; a lab test can be repeated) and show this message to your doctor in the next few days.\nThis is not a diagnosis: it is a signal that a reading crossed a line.',
    },
    'safety.what_to_do_critical': {
        'ru': 'Что делать:\n• Если чувствуешь себя плохо — вызови скорую.\n• Если самочувствие обычное — свяжись с врачом сегодня и покажи ему это сообщение. Перепроверить показатель можно, но не вместо звонка врачу.\nЭто не диагноз: это сигнал, что показатель пересёк границу, которую врачи считают опасной.',
        'en': 'What to do:\n• If you feel unwell, call an ambulance.\n• If you feel as usual, contact your doctor today and show them this message. You can recheck the reading, but not instead of contacting your doctor.\nThis is not a diagnosis: it is a signal that a reading crossed a line doctors consider dangerous.',
    },
    'safety.data_doubt_critical': {
        'ru': 'Сегодня я нашёл у себя сбой данных, поэтому эти цифры могут быть неточными. Но граница опасная — всё равно сначала свяжись с врачом, перемерить можно потом.',
        'en': 'Today I found a data fault on my side, so these numbers may be inaccurate. But this line is a dangerous one — contact your doctor first anyway; you can measure again afterwards.',
    },
}


def _safety_text(key: str, lang: str | None = None, **fmt) -> str:
    """Use the existing language resolver and keep an independent emergency text."""
    lang = lang or i18n.lang_of()
    try:
        text = i18n.t(key, lang, **fmt)
        if not isinstance(text, str):
            raise TypeError("safety translation is not text")
        return text
    except Exception as e:  # dictionary loading/formatting must not stop an alert
        log.warning("safety_net: translation %s failed (%s); emergency text used", key, e)
        reserve = _SAFETY_TEXT_FALLBACKS[key]
        # язык вне словаря резерва не должен уронить срочное сообщение: английский резерв
        return reserve.get(lang, reserve['en']).format(**fmt)


def _snapshot_lab_rows() -> dict:
    import norm_documents
    if not norm_documents.CTCAE_SNAPSHOT.exists():
        return {}   # чистая установка без снимка: резерва нет, loader кричит при пустой БД
    out: dict = {}
    for r in norm_documents.load_ctcae_rows()["rows"]:
        out.setdefault(r["metric"], []).append(
            {"direction": r["direction"], "band": r["band"], "kind": r["kind"],
             "baseline": r["baseline"], "value": r["value"], "unit": r["unit"], "note": r["note"]})
    return out


_FALLBACK_LAB = _snapshot_lab_rows()

# daily-метрики того же variant='safety_net' (spo2/readiness/sleep_score) — читатель
# _load_lifestyle_abs; лаб-loader их исключает по этому же словарю, не по второму списку
_LIFESTYLE_M2K = {"spo2": "spo2_avg", "readiness": "readiness_score", "sleep_score": "sleep_score"}

# Пороги для лабораторных трендов (РЕЗЕРВ; рантайм — lab_trend_thresholds в БД)
# Если показатель изменился на X% за последние N измерений — это сигнал
def _snapshot_trend_rows() -> dict:
    """Резерв трендов = RCV из снимка EFLM (тот же вывод, что в сиде health_db._seed_lab_trend_thresholds)."""
    import norm_documents
    if not norm_documents.EFLM_SNAPSHOT.exists():
        return {}   # чистая установка без снимка: резерва нет, loader кричит при пустой БД
    snap = norm_documents.load_eflm()
    conv = {"CEA": ("up", URGENT), "CA19-9": ("up", URGENT), "HGB": ("down", WARN),
            "MCV": ("up", WARN), "Albumin": ("down", WARN)}
    return {m: {"pct_change": norm_documents.rcv_pct(m, snapshot=snap), "n_readings": 2,
                "direction": d, "level": lv,
                "near_boundary_share": db.LAB_TREND_NEAR_BOUNDARY_SHARE} for m, (d, lv) in conv.items()}


_FALLBACK_LAB_TREND = _snapshot_trend_rows()

# ── Lifestyle пороги (РЕЗЕРВ; рантайм — absolute_thresholds variant='safety_net'/_rel) ──
# Абсолютные пороги
_FALLBACK_LIFESTYLE_ABS = {
    "spo2_avg":       {"low_critical": 90.0, "low_urgent": 92.0, "low_warn": 94.0,
                       "label": "SpO2", "unit": "%"},
    "readiness_score":{"low_urgent": 30,    "low_warn": 45,
                       "label": "Readiness", "unit": "/100"},
    "sleep_score":    {"low_urgent": 40,    "low_warn": 55,
                       "label": "Sleep score", "unit": "/100"},
}

# Относительные пороги — отклонение от личной 30-дневней базовой линии (РЕЗЕРВ)
_FALLBACK_LIFESTYLE_REL = {
    "hrv":            {"drop_warn": 0.20, "drop_urgent": 0.35, "drop_critical": 0.50,
                       "label": "HRV", "unit": "ms"},
    "resting_hr":     {"rise_warn": 8,   "rise_urgent": 15,
                       "label": "ЧСС покоя", "unit": "bpm"},
    "readiness_score":{"drop_warn": 20,  "drop_urgent": 35,
                       "label": "Readiness", "unit": "pts"},
}


# ── Загрузчики порогов из БД (safety-net-thresholds E1) ───────────────────────
# Рантайм-источник — БД (rules_db). Числа-нормы из БД, метки (unit/note/label) из _FALLBACK.
# При пустоте/ошибке источника → _FALLBACK целиком + ГРОМКИЙ алерт (fail-static, §9).
# Полноту БД≡_FALLBACK держит coherence-тест; loader деградирует лишь при недоступности
# источника (пустой сид / БД offline), доверяя полноте засеянного.

_FALLBACK_ALERTED: set = set()


def _emit_fallback_alert(scope: str, err) -> None:
    """Громкий сигнал: safety_net на резерве. log.error ВСЕГДА; Telegram оператору —
    раз per scope per процесс (дедуп, чтобы не спамить каждый прогон)."""
    log.error(
        f"🚨 SAFETY_NET FALLBACK [{scope}]: пороги из БД недоступны ({err}) — работаю на "
        f"резервных литералах _FALLBACK. Почини сид absolute_thresholds/lab_trend_thresholds (§9)."
    )
    if scope in _FALLBACK_ALERTED:
        return
    _FALLBACK_ALERTED.add(scope)
    try:
        import notify
        notify.fault(f"safety_net: thresholds unavailable ({scope}); fallback active", person_key=None)
    except Exception as e:  # доставка best-effort; лог уже громкий
        log.warning(f"_emit_fallback_alert notify failed: {e}")


def _load_lab_thresholds() -> dict:
    """LAB-пороги из absolute_thresholds (variant='safety_net', active): metric → список строк
    {direction, band, kind, baseline, value, unit, note}. kind='relative' + baseline ULN/LLN —
    кратное референса бланка (CTCAE); разрешается в число в _resolve_thresholds по строке
    строки канона. Пусто/ошибка → _FALLBACK_LAB (снимок документа) + громкий алерт."""
    try:
        import rules_db
        import lab_canon
        rows = [t for t in rules_db.get_absolute_thresholds()
                if t.get("variant") == "safety_net" and t["metric"] not in _LIFESTYLE_M2K]
        if not rows:
            raise ValueError("нет safety_net-строк в absolute_thresholds")
        out: dict = {}
        for t in rows:
            m = lab_canon.normalize(t["metric"])
            tmpl = t.get("reason_template") or ""
            unit = tmpl.split("}")[1].split("—")[0].strip() if "}" in tmpl and "—" in tmpl else ""
            note = tmpl.split("—", 1)[1].strip() if "—" in tmpl else ""
            out.setdefault(m, []).append(
                {"direction": t["direction"], "band": t.get("band_label") or "",
                 "kind": t.get("kind") or "absolute", "baseline": t.get("baseline"),
                 "value": t["value"], "unit": unit, "note": note})
        return out
    except Exception as e:
        _emit_fallback_alert("lab_abs", e)
        return _FALLBACK_LAB


def _resolve_thresholds(rows: list[dict], ref_low, ref_high) -> tuple | None:
    """Строки нормы + референс бланка → 8-кортеж _level_from_value
    (low_crit, low_urg, low_warn, high_warn, high_urg, high_crit, unit, note).
    Относительная строка без нужного референса → None (не догадка): вызывающий кричит
    'порог не разрешён'. Сложение с референсом — то, ради чего CTCAE выражен в ×ULN/LLN."""
    slots = {("floor", "critical"): 0, ("floor", "urgent"): 1, ("floor", "warn"): 2,
             ("ceiling", "warn"): 3, ("ceiling", "urgent"): 4, ("ceiling", "critical"): 5}
    vals = [None] * 6
    unit, note = "", ""
    for r in rows:
        i = slots.get((r["direction"], r["band"]))
        if i is None:
            continue
        v = r["value"]
        if r["kind"] == "relative":
            ref = ref_high if r["baseline"] == "ULN" else ref_low
            if ref is None:
                return None
            v = float(ref) * float(v)
        vals[i] = float(v)
        unit = unit or r.get("unit") or ""
        note = note or r.get("note") or ""
    return (*vals, unit, note)


def _load_lab_trend_thresholds() -> dict:
    """Пороги лаб-трендов из lab_trend_thresholds. Пусто/ошибка → _FALLBACK_LAB_TREND + алерт."""
    try:
        import rules_db
        rows = rules_db.get_lab_trend_thresholds()
        if not rows:
            raise ValueError("нет lab_trend_thresholds")
        import lab_canon
        return {lab_canon.normalize(t["metric"]): {
                    "pct_change": t["pct_change"], "n_readings": t["n_readings"],
                    "direction": t["direction"], "level": t["level"],
                    "near_boundary_share": t.get("near_boundary_share")} for t in rows}
    except Exception as e:
        _emit_fallback_alert("lab_trend", e)
        return _FALLBACK_LAB_TREND


def _load_lifestyle_abs() -> dict:
    """spo2/readiness/sleep_score из absolute_thresholds variant='safety_net'. Формат
    _FALLBACK_LIFESTYLE_ABS ({key:{low_critical/urgent/warn, label, unit}})."""
    try:
        import rules_db
        m2k = _LIFESTYLE_M2K
        band2key = {"critical": "low_critical", "urgent": "low_urgent", "warn": "low_warn"}
        rows = [t for t in rules_db.get_absolute_thresholds()
                if t.get("variant") == "safety_net" and t.get("kind") == "absolute"
                and t["metric"] in m2k]
        if not rows:
            raise ValueError("нет lifestyle safety_net-строк")
        out = {}
        for t in rows:
            k = m2k[t["metric"]]
            if k not in out:
                fb = _FALLBACK_LIFESTYLE_ABS[k]
                out[k] = {"label": fb["label"], "unit": fb["unit"]}
            bk = band2key.get(t.get("band_label") or "")
            if bk:
                out[k][bk] = t["value"]
        return out
    except Exception as e:
        _emit_fallback_alert("lifestyle_abs", e)
        return _FALLBACK_LIFESTYLE_ABS


def _load_lifestyle_rel() -> dict:
    """hrv/resting_hr из absolute_thresholds variant='safety_net_rel'. Формат
    _FALLBACK_LIFESTYLE_REL. floor→drop_{band}, ceiling→rise_{band}; числа из БД, label/unit резерв."""
    try:
        import rules_db
        m2k = {"hrv": "hrv", "resting_hr": "resting_hr", "readiness": "readiness_score"}
        rows = [t for t in rules_db.get_absolute_thresholds() if t.get("variant") == "safety_net_rel"]
        if not rows:
            raise ValueError("нет safety_net_rel-строк")
        out = {}
        for t in rows:
            k = m2k.get(t["metric"])
            if k is None:
                continue
            if k not in out:
                fb = _FALLBACK_LIFESTYLE_REL[k]
                out[k] = {"label": fb["label"], "unit": fb["unit"]}
            prefix = "drop" if t["direction"] == "floor" else "rise"
            out[k][f"{prefix}_{t.get('band_label') or ''}"] = t["value"]
        return out
    except Exception as e:
        _emit_fallback_alert("lifestyle_rel", e)
        return _FALLBACK_LIFESTYLE_REL


# ── Проверки ──────────────────────────────────────────────────────────────────

def _level_from_value(value, thresholds) -> tuple[str | None, str]:
    """
    Возвращает (уровень, направление) для числового значения по пороговой таблице.
    thresholds = (low_crit, low_urg, low_warn, high_warn, high_urg, high_crit, unit, note)
    """
    lc, lu, lw, hw, hu, hc, unit, note = thresholds
    if hc is not None and value >= hc: return CRITICAL, "high"
    if hu is not None and value >= hu: return URGENT,   "high"
    if hw is not None and value >= hw: return WARN,     "high"
    if lc is not None and value <= lc: return CRITICAL, "low"
    if lu is not None and value <= lu: return URGENT,   "low"
    if lw is not None and value <= lw: return WARN,     "low"
    return None, "ok"


def _get_scheduled_lab_metrics(reference_date: date) -> dict:
    """
    Возвращает {metric_name: deadline_str} для лаб. метрик,
    покрытых открытыми lab_test-задачами с дедлайном >= reference_date.
    Используется для подавления повторяющихся WARN-алертов в ежедневном отчёте,
    когда анализ уже запланирован.
    """
    try:
        conn = db.get_conn()
        rows = conn.execute(
            "SELECT fingerprint, deadline FROM tasks "
            "WHERE type='lab_test' AND status='open' AND deadline >= ? "
            "ORDER BY deadline",
            (str(reference_date),)
        ).fetchall()
        result = {}
        for row in rows:
            # поддерживаем и tuple, и Row
            fp = row[0] if isinstance(row, (list, tuple)) else row["fingerprint"]
            dl = row[1] if isinstance(row, (list, tuple)) else row["deadline"]
            if not fp or not fp.startswith("lab:"):
                continue
            for m in fp[4:].split(","):
                m = m.strip()
                if m:
                    result[m] = dl
        return result
    except Exception as e:
        log.warning(f"_get_scheduled_lab_metrics: {e}")
        return {}


def check_lab_alerts(reference_date: date = None) -> list[dict]:
    """Проверяет последние лабораторные данные против порогов."""
    if reference_date is None:
        reference_date = get_today()

    db.init_db()
    recent_labs = db.get_recent_labs(730)  # все за 2 года

    # Берём последнее значение каждого теста. Ключ — канон-имя (lab_canon.normalize),
    # как и у порогов/фингерпринтов: иначе порог и данные под двумя написаниями одного
    # аналита не встречаются, и предохранитель молчит неотличимо от «в норме».
    import lab_canon
    latest = {}
    for lab in recent_labs:
        name = lab_canon.normalize(lab["test_name"])
        if name not in latest:
            latest[name] = lab

    scheduled = {lab_canon.normalize(m): dl
                 for m, dl in _get_scheduled_lab_metrics(reference_date).items()}
    _LAB = _load_lab_thresholds()
    alerts = []
    try:
        import config_db
        min_docs = int(config_db.get_config("norm.witness_min_docs", 3))
    except Exception as e:  # noqa: BLE001 — конфиг недоступен: резерв, но с сигналом (§14)
        log.warning(f"norm.witness_min_docs недоступен ({e}) — резерв 3")
        min_docs = 3
    for name, lab in latest.items():
        value = lab.get("value")
        if value is None:
            continue
        lab_date = lab.get("date", "?")
        days_ago = (reference_date - date.fromisoformat(lab_date)).days if lab_date != "?" else None
        ref_low, ref_high = lab.get("ref_low"), lab.get("ref_high")
        rows = _LAB.get(name)
        if rows:
            thresholds = _resolve_thresholds(rows, ref_low, ref_high)
            if thresholds is None:
                # относительный порог, а референса на бланке нет → модальный по документам
                try:
                    import labs_db
                    modal = labs_db.get_modal_reference(name, min_docs)
                except Exception as e:  # noqa: BLE001 — сигнал, не тишина
                    log.warning(f"модальный референс {name} недоступен: {e}")
                    modal = None
                if modal:
                    thresholds = _resolve_thresholds(rows, modal[0], modal[1])
            if thresholds is None:
                alerts.append({
                    "source": "norm_unresolved", "kind": CANNOT_JUDGE,
                    "level": WARN, "metric": name, "value": value,
                    "unit": lab.get("unit") or "", "direction": "unknown", "date": lab_date,
                    "days_ago": days_ago,
                    "note": "порог задан кратным референса (CTCAE), а референса на бланке нет и "
                            "модального по документам нет — судить нечем",
                    "scheduled_date": scheduled.get(name),
                })
                continue
            level, direction = _level_from_value(float(value), thresholds)
            unit, note = thresholds[6], thresholds[7]
        else:
            # Аналит без порога решения: вид 1 — референс, напечатанный лабораторией на бланке.
            # Это единственный корректный источник референса (CLSI EP28); выход за него — WARN.
            level, direction, unit, note = None, "ok", lab.get("unit") or "", ""
            if ref_high is not None and float(value) > float(ref_high):
                level, direction, note = WARN, "high", f"выше референса бланка ({ref_low}–{ref_high})"
            elif ref_low is not None and float(value) < float(ref_low):
                level, direction, note = WARN, "low", f"ниже референса бланка ({ref_low}–{ref_high})"
        if level:
            alerts.append({
                "source":         "lab",
                "level":          level,
                "metric":         name,
                "value":          value,
                "unit":           unit,
                "direction":      direction,
                "date":           lab_date,
                "days_ago":       days_ago,
                "note":           note,
                "scheduled_date": scheduled.get(name),
            })

    return alerts


def _lab_fingerprint(row: dict):
    """Идентичность лаборатории/метода без колонки «лаборатория»: (ref_low, ref_high, единица).
    None — референс не напечатан, лаборатория неизвестна (консервативно = «другая»)."""
    lo, hi = row.get("ref_low"), row.get("ref_high")
    if lo is None and hi is None:
        return None
    import lab_canon
    return (lo, hi, lab_canon.norm_unit(row.get("unit") or "").lower())


def check_lab_trends(reference_date: date = None) -> list[dict]:
    """Проверяет направленные тренды в лабораторных данных."""
    if reference_date is None:
        reference_date = get_today()

    db.init_db()
    _LAB_TREND = _load_lab_trend_thresholds()
    # правило подтверждения — данные документа (EGTM 2014); нечитаемо → судим по одной
    # паре, как до правила, но громко: тихое исчезновение правила = §7
    try:
        import norm_documents
        _CONFIRM = norm_documents.confirmation_metrics()
    except Exception as e:  # noqa: BLE001
        _emit_fallback_alert("lab_trend_confirm_rule", e)
        _CONFIRM = set()
    alerts = []

    for test_name, cfg in _LAB_TREND.items():
        # серия, не «последнее по тесту»: get_recent_labs отдаёт одну строку на тест (GROUP BY),
        # и до 2026-09-02 этот цикл не набирал n_readings никогда (norm-from-documents)
        # По ВЕЩЕСТВУ (lab_name_loinc → get_lab_trend_by_component): единицы сводятся в одну шкалу
        # по отображению, а не «как есть». CEA/CA19-9/MCV отображены 2026-09-02 (single-candidate);
        # аналит без отображения → серия по канон-имени + громкий сигнал: тренд не должен
        # замолчать из-за пустой карты (замер до отображения давал 0 точек).
        import labs_db
        comp = labs_db.get_lab_trend_by_component(test_name, n=50)
        if not comp.get("points"):
            # Отсутствие строк для аналита не равнозначно отсутствию отображения.
            # Без измерений нет предмета для алерта о тренде.
            # Моки без n_rows считаются «строки есть» — громко.
            if comp.get("n_rows", 1) == 0:
                continue
            # Без отображения тренд НЕ строится по имени (ратчет читателей по имени): вместо
            # тихого нуля — алерт вида CANNOT_JUDGE. Он громкий, но ОПЕРАТОРУ (ночной
            # разбор), не человеку: пустая карта — долг системы, не новость о здоровье.
            alerts.append({
                "source": "lab_trend", "kind": CANNOT_JUDGE,
                "level": WARN, "metric": test_name, "value": None,
                "pct_change": None, "direction": cfg["direction"], "from_date": None, "to_date": None,
                "same_lab": None,
                "note": "тренд не построен: аналит не отображён в lab_name_loinc "
                        "(loinc_match.py --apply-auto или вердикт владельца)",
            })
            continue
        labs = [{"test_name": test_name, "value": p["value"], "unit": p["unit"], "date": p["date"],
                 "ref_low": p.get("ref_low"), "ref_high": p.get("ref_high")} for p in comp["points"]]
        if len(labs) < cfg["n_readings"]:
            continue

        recent = labs[-cfg["n_readings"]:]
        first, last = recent[0], recent[-1]
        first_val, last_val = first.get("value"), last.get("value")
        if first_val is None or last_val is None or first_val == 0:
            continue

        # Пол по величине (слово владельца 2026-09-03, данные lab_trend_thresholds.near_boundary_share):
        # «колебания у границы нормы в пределах 20 % от неё должны приходить; если нижняя
        # граница 0 — имеет значение только верхняя». Прочтение (а): правило действует лишь
        # для интервалов с нулевой/отсутствующей нижней границей (онкомаркеры); двусторонние
        # (HGB, Albumin) судятся как прежде. Без референса на последней точке пола нет —
        # громко, как и было (лаборатория не определена). Прецедент (условные числа): онкомаркер
        # удвоился у аналитического пола, оставаясь в единицах процентов от ULN, — шум измерения.
        share = cfg.get("near_boundary_share")
        r_hi, r_lo = last.get("ref_high"), last.get("ref_low")
        if share and r_hi and not r_lo and cfg["direction"] == "up":
            if last_val < r_hi * (1 - share):
                continue

        # ЛАБОРАТОРИЯ — из данных (колонки нет): отпечаток напечатанного референса + единица.
        # RCV — статистика ОДНОЙ лаборатории и метода; пара точек через смену лаборатории несёт
        # ещё межлабораторное смещение, которого в RCV нет. Тогда сравниваем долю
        # от ULN каждой лаборатории и не поднимаемся выше warn:
        # неопределённость сравнения должна быть видна читателю.
        fp_first, fp_last = _lab_fingerprint(first), _lab_fingerprint(last)
        same_lab = fp_first is not None and fp_first == fp_last
        pct = (last_val - first_val) / abs(first_val) * 100
        pcts = [pct]
        if same_lab:
            basis = f"{first_val} → {last_val}"
        else:
            r1, r2 = first.get("ref_high"), last.get("ref_high")
            if r1 and r2:
                # Нормализация по ULN не гарантирует сопоставимость лабораторий.
                # Считаем и показываем оба процента; срабатывает любой из них,
                # а уровень остаётся не выше warn.
                pct_share = (last_val / r2 - first_val / r1) / abs(first_val / r1) * 100
                pcts.append(pct_share)
                basis = (_safety_text('safety.note.different_labs', first_val=first_val, last_val=last_val, pct=pct, r1=r1, r2=r2, pct_share=pct_share))
            else:
                basis = (_safety_text('safety.note.unknown_lab', first_val=first_val, last_val=last_val))

        def _hit(p):
            return (cfg["direction"] == "up" and p >= cfg["pct_change"]) or \
                   (cfg["direction"] == "down" and p <= -cfg["pct_change"])
        triggered = any(_hit(p) for p in pcts)

        if triggered:
            level = cfg["level"] if same_lab else WARN
            # Подтверждение вторым забором (EGTM 2014, schedules.json::rules — данные, не число):
            # рост маркера между двумя точками — повод для повторного забора, не действие.
            # Уровень порога — только когда И предыдущая точка уже превышала порог
            # относительно того же исходного значения (рост держится), иначе warn.
            # Закрывает шум на аналитическом полу без пола по величине (условно: маркер
            # 1→2 = +100 % у аналитического пола). Метрики вне правила судятся по одной паре.
            if test_name in _CONFIRM and level != WARN:
                base = labs[-3] if len(labs) >= 3 else None
                prev = labs[-2]
                confirmed = False
                if base and base.get("value") and prev.get("value") is not None:
                    p_prev = (prev["value"] - base["value"]) / abs(base["value"]) * 100
                    p_last = (last_val - base["value"]) / abs(base["value"]) * 100
                    confirmed = _hit(p_prev) and _hit(p_last)
                if not confirmed:
                    level = WARN
                    basis += (_safety_text('safety.note.confirm_sample'))
            alerts.append({
                "source":    "lab_trend",
                "level":     level,
                "metric":    test_name,
                "value":     last_val,
                "pct_change": round(pct, 1),
                "direction": cfg["direction"],
                "from_date": first["date"],
                "to_date":   last["date"],
                "same_lab":  same_lab,
                "note":      _safety_text('safety.note.trend', count=len(recent), basis=basis),
            })

    return alerts


def _personal_floor(metric: str, pct: int = 5, min_n: int = 30):
    """Личный p{pct}-пол метрики из данных ЭТОГО тенанта (brief-neutralization B2,
    директива 16.07 «никакие числа не константы — вычисляются per-tenant»). Возвращает
    p{pct} или None при <min_n днях / недоступной БД → union тихо деградирует к
    абсолютному полу. Метрика — из белого списка _derive_metric_percentile (spo2
    намеренно ВНЕ списка, не user-input). Живой вывод per-tenant, БЕЗ материализации:
    safety_net уже читает БД тенанта на каждом прогоне, перцентиль дёшев → не заводим
    класс «устаревшей реплики» (в отличие от seeded rules_db p10 Фазы 3a)."""
    try:
        return db._derive_metric_percentile(db.get_conn(), metric, pct=pct, min_n=min_n)
    except Exception as e:  # silent-ok: БД/колонки нет → union деградирует к абс.полу
        log.warning(f"_personal_floor({metric}): {e}")
        return None


def check_lifestyle_alerts(target: date = None) -> list[dict]:
    """Проверяет lifestyle-метрики против абсолютных и относительных порогов."""
    if target is None:
        target = get_today()

    db.init_db()
    day    = db.get_day(str(target))
    stats30 = db.get_stats(30, target)
    _ABS = _load_lifestyle_abs()
    _REL = _load_lifestyle_rel()
    alerts = []

    # ── Абсолютные пороги ────────────────────────────────────────────────
    spo2_avg = (day.get("spo2") or {}).get("avg")
    if spo2_avg:
        cfg = _ABS["spo2_avg"]
        for level, key in [(CRITICAL, "low_critical"), (URGENT, "low_urgent"), (WARN, "low_warn")]:
            if key in cfg and spo2_avg <= cfg[key]:
                alerts.append({
                    "source": "lifestyle", "level": level,
                    "metric": "SpO2", "value": spo2_avg, "unit": "%", "limit": cfg[key],
                    "note": _safety_text('safety.note.spo2', spo2_avg=spo2_avg, limit=cfg[key])
                })
                break

    readiness = day.get("readiness_score")
    if readiness is not None:
        cfg = _ABS["readiness_score"]
        for level, key in [(URGENT, "low_urgent"), (WARN, "low_warn")]:
            if key in cfg and readiness <= cfg[key]:
                alerts.append({
                    "source": "lifestyle", "level": level,
                    "metric": "Readiness", "value": readiness, "unit": "/100", "limit": cfg[key],
                    "note": _safety_text('safety.note.readiness', readiness=readiness)
                })
                break

    sleep_score = (day.get("sleep") or {}).get("sleep_score")
    if sleep_score:
        cfg = _ABS["sleep_score"]
        for level, key in [(URGENT, "low_urgent"), (WARN, "low_warn")]:
            if key in cfg and sleep_score <= cfg[key]:
                alerts.append({
                    "source": "lifestyle", "level": level,
                    "metric": "Sleep score", "value": sleep_score, "unit": "/100", "limit": cfg[key],
                    "note": f"Sleep score {sleep_score}"
                })
                break

    # ── Относительные пороги (vs 30d baseline) ───────────────────────────
    hrv_today = (day.get("hrv") or {}).get("avg")
    hrv_30d   = stats30.get("avg_hrv")
    if hrv_today and hrv_30d and hrv_30d > 0:
        drop = (hrv_30d - hrv_today) / hrv_30d
        cfg  = _REL["hrv"]
        for level, key in [(CRITICAL, "drop_critical"), (URGENT, "drop_urgent"), (WARN, "drop_warn")]:
            if key in cfg and drop >= cfg[key]:
                alerts.append({
                    "source": "lifestyle", "level": level,
                    "metric": "HRV", "value": hrv_today, "unit": "ms",
                    "baseline": hrv_30d, "change_pct": round(drop * 100),
                    "note":   _safety_text('safety.note.hrv', hrv_today=hrv_today, drop_pct=drop * 100, hrv_30d=hrv_30d)
                })
                break

    rhr_raw = day.get("resting_heart_rate")
    rhr_today = rhr_raw.get("value") if isinstance(rhr_raw, dict) else rhr_raw
    rhr_30d   = stats30.get("avg_rhr")
    if rhr_today and rhr_30d:
        rise = rhr_today - float(rhr_30d)
        cfg  = _REL["resting_hr"]
        for level, key in [(URGENT, "rise_urgent"), (WARN, "rise_warn")]:
            if key in cfg and rise >= cfg[key]:
                alerts.append({
                    "source": "lifestyle", "level": level,
                    "metric": "ЧСС покоя", "value": rhr_today, "unit": "bpm",
                    "baseline": float(rhr_30d), "rise": round(rise),
                    "note":   _safety_text('safety.note.resting_hr', rhr_today=rhr_today, rise=rise, rhr_30d=rhr_30d)
                })
                break

    # ── Личный p5-пол (brief-neutralization B2, директива 16.07) ─────────
    # UNION поверх абсолютного пола: абс.пол выше ОСТАЁТСЯ fail-safe (страж
    # test_safety_floor_guard держит его для data-бедного тенанта). Здесь ДОБАВЛЯЕМ
    # WARN, если сегодня ниже ЛИЧНОГО p5 (≥30 своих дней) — «необычно низко для тебя»,
    # даже когда абс.пол не пробит. Union только пере-алертит, никогда не подавляет и
    # не понижает уровень → safe-by-construction. SpO2 сюда НЕ входит: низкая сатурация
    # опасна абсолютно, личный перцентиль (напр. p5≈96%) дал бы клинический шум.
    for metric_col, today_val, label, unit in (
        ("readiness",   readiness,   "Readiness",   "/100"),
        ("sleep_score", sleep_score, "Sleep score", "/100"),
    ):
        if today_val is None:
            continue
        p5 = _personal_floor(metric_col)          # None → <30 дней или БД недоступна
        if p5 is None or today_val >= p5:
            continue
        if any(a["metric"] == label for a in alerts):
            continue                               # абс.пол уже сработал — не дублируем
        alerts.append({
            "source": "lifestyle", "level": WARN,
            "metric": label, "value": today_val, "unit": unit,
            "note": f"{label} {today_val:g} — ниже личного p5 ({p5:.0f}) за 30+ дней",
        })

    return alerts


# ── Текст человеку ────────────────────────────────────────────────────────
# Техническая заметка с уровнем тревоги не объясняет, что делать дальше.
# Сообщение называет показатель простыми словами, его значение и границу,
# затем действие. Заметки note остаются для контекста врача-модели (warn_summary).

_PERSON_LABEL = {
    "SpO2": "lifestyle.spo2", "Readiness": "lifestyle.readiness",
    "Sleep score": "lifestyle.sleep", "HRV": "lifestyle.hrv", "ЧСС покоя": "lifestyle.rhr",
}


def _person_line(a: dict) -> str:
    src, m = a.get("source"), a.get("metric")
    try:
        if src == "lifestyle" and m in _PERSON_LABEL:
            return i18n.t("safety.line." + _PERSON_LABEL[m], value=a["value"],
                          limit=a.get("limit"), baseline=a.get("baseline"),
                          change_pct=a.get("change_pct"), rise=a.get("rise"))
        if src == "lab":
            key = "safety.line.lab_high" if a.get("direction") == "high" else "safety.line.lab_low"
            if a.get("level") == CRITICAL:
                key += "_critical"
            return i18n.t(key, metric=m, value=a.get("value"), unit=a.get("unit") or "",
                          date=a.get("date") or "?")
        if src == "lab_trend":
            key = "safety.line.trend_up" if (a.get("pct_change") or 0) >= 0 else "safety.line.trend_down"
            return i18n.t(key, metric=m, pct=abs(a.get("pct_change") or 0),
                          from_date=a.get("from_date") or "?", to_date=a.get("to_date") or "?")
    except Exception as e:   # dictionary or formatting failure: keep the raw-note fallback
        log.warning(f"safety_net: человеческая строка для {m} не собрана: {e}")
    if m == "ЧСС покоя":  # internal metric identifier stays stable
        m = _safety_text("safety.label.resting_hr")
    return f"{m}: {a.get('note', '')}"


def person_urgent_message(urgent: list, max_level: str, data_doubt: bool = False) -> str:
    """Срочное сообщение человеку: заголовок, строки показателей, что делать."""
    head = _safety_text("safety.head.critical" if max_level == CRITICAL else "safety.head.urgent")
    lines = [head, ""] + [f"• {_person_line(a)}" for a in urgent]
    # CRITICAL = 3-я степень CTCAE v5.0 (data/norm_docs/ctcae_lab_terms.json: 3 → critical):
    # «тяжёлое, медицински значимое». Совет «к врачу в ближайшие дни» для неё мягок —
    # свой блок «связаться с врачом сегодня» (28.09, холодное чтение текстов тревоги).
    crit = "_critical" if max_level == CRITICAL else ""
    if data_doubt:
        lines += ["", _safety_text("safety.data_doubt" + crit)]
    lines += ["", _safety_text("safety.what_to_do" + crit)]
    return "\n".join(lines)


def run_safety_net(target: date = None) -> dict:
    """
    Запускает все проверки.
    Возвращает {
        "alerts": [...],          # все алерты
        "max_level": str|None,    # максимальный уровень
        "urgent_message": str,    # форматированное сообщение для URGENT+
        "warn_summary": str,      # краткое резюме для контекста GP
        "cannot_judge": [...],    # «судить нечем» — НЕ в alerts; оператору (CANNOT_JUDGE)
    }
    """
    if target is None:
        target = get_today()

    all_alerts = []
    all_alerts += check_lab_alerts(target)
    all_alerts += check_lab_trends(target)
    all_alerts += check_lifestyle_alerts(target)

    # «Судить нечем» — оператору, не человеку (см. CANNOT_JUDGE). Отделяем ДО любого
    # потребителя: alerts читают карточки брифа, warn_summary — контекст GP, urgent — Telegram.
    cannot_judge = [a for a in all_alerts if a.get("kind") == CANNOT_JUDGE]
    all_alerts = [a for a in all_alerts if a.get("kind") != CANNOT_JUDGE]
    _record_cannot_judge(target, cannot_judge)

    if not all_alerts:
        return {
            "alerts": [],
            "max_level": None,
            "urgent_message": "",
            # «в норме» — только если судили всё: непроверенное не равно нормальному
            # (report_absence_claims), а детали долга — оператору, не в контекст врача.
            "warn_summary": ("Safety net: тревог нет." if cannot_judge
                             else "Safety net: все показатели в норме."),
            "cannot_judge": cannot_judge,
        }

    LEVEL_ORDER = {CRITICAL: 3, URGENT: 2, WARN: 1}
    all_alerts.sort(key=lambda a: LEVEL_ORDER.get(a["level"], 0), reverse=True)
    max_level = all_alerts[0]["level"]

    ICON = {CRITICAL: "🚨", URGENT: "⚠️", WARN: "🔶"}

    # Срочное сообщение (URGENT + CRITICAL)
    urgent = [a for a in all_alerts if a["level"] in (URGENT, CRITICAL)]
    urgent_lines = [person_urgent_message(urgent, max_level)] if urgent else []

    # Краткое резюме для GP
    # WARN-алерты с запланированным анализом уходят в нижнюю секцию,
    # чтобы не засорять ежедневный контекст повторяющимися напоминаниями.
    warn_lines = [f"SAFETY NET ({target}):"]
    scheduled_notes = []
    for a in all_alerts:
        icon = ICON[a["level"]]
        if a.get("scheduled_date") and a["level"] == WARN:
            scheduled_notes.append(
                f"  📅 {a['metric']} ({a['note']}) — анализ запланирован на {a['scheduled_date']}"
            )
        else:
            warn_lines.append(f"  {icon} [{a['level'].upper()}] {a['metric']}: {a['note']}")
    if scheduled_notes:
        warn_lines.append("  [плановые проверки — действие не требуется до даты]")
        warn_lines.extend(scheduled_notes)

    log.info(
        f"Safety net {target}: {len(all_alerts)} alerts "
        f"({sum(1 for a in all_alerts if a['level']==CRITICAL)} critical, "
        f"{sum(1 for a in all_alerts if a['level']==URGENT)} urgent, "
        f"{sum(1 for a in all_alerts if a['level']==WARN)} warn)"
    )

    return {
        "alerts":         all_alerts,
        "max_level":      max_level,
        "urgent_message": "\n".join(urgent_lines),
        "warn_summary":   "\n".join(warn_lines),
        "cannot_judge":   cannot_judge,
    }


def _record_cannot_judge(target: date, items: list) -> None:
    """Пишет последний прогон «судить нечем» в system_config ТЕКУЩЕГО тенанта — всегда,
    пустой тоже: свежая пустая запись = «проверил, долгов нет», а не «не проверял».
    Читатель — cannot_judge_open (ночной разбор). Отказ записи не роняет safety net
    (он страхует здоровье, запись — учёт), но громко."""
    try:
        import config_db
        config_db.upsert_config(
            CANNOT_JUDGE_KEY, category="safety_net", source="safety_net",
            value_json={"date": str(target),
                        "items": [{"metric": a.get("metric"), "source": a.get("source"),
                                   "note": a.get("note")} for a in items]})
    except Exception as e:  # noqa: BLE001 — учёт долга не смеет ронять предохранитель
        log.warning(f"safety_net: запись {CANNOT_JUDGE_KEY} не удалась: {e!r}")
    if items:
        log.warning("safety_net: судить нечем по %d порогам (%s) — долг системы, не тревога",
                    len(items), ", ".join(str(a.get("metric")) for a in items))


def cannot_judge_open(conn, today: date) -> list | None:
    """Открытые «судить нечем» тенанта по его соединению (read-only годится).
    Список пунктов, если запись свежее CANNOT_JUDGE_FRESH_DAYS; [] — свежая и пустая;
    None — записи нет или она старая (safety net не бегал — вопрос другого датчика)."""
    import config_db
    rec = config_db.get_config(CANNOT_JUDGE_KEY, None, conn=conn)
    if not isinstance(rec, dict) or not rec.get("date"):
        return None
    try:
        age = (today - date.fromisoformat(rec["date"])).days
    except ValueError:
        return None
    if age > CANNOT_JUDGE_FRESH_DAYS:
        return None
    return list(rec.get("items") or [])
