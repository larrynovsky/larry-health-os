#!/usr/bin/env python3.11
"""
hai_hypotheses — гипотезы, протоколы, форматирование.
Зависимости: health_db, hai_core (get_client).
"""

import logging
import hai_core
from datetime import date
from _time_inject import get_today  # seam
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent))
import health_db as db
from hai_core import get_client

log = logging.getLogger(__name__)


# ── Гипотезы ─────────────────────────────────────────────────────────────

def save_hypothesis(observation: str, mechanism: str, prediction: str,
                    test: str, trigger: str = "manual",
                    linked_experiment_id: int = None,
                    status: str = "open",
                    resolution_type: str = "self_managed",
                    trigger_subtype: str | None = None) -> int:
    """
    Сохраняет гипотезу в memory (category='hypothesis').
    trigger_subtype (W5E-4): для proactive specialist review — 'cardiology'/'sleep_coach'/...
    Возвращает id записи в memory.
    """
    import json as _json
    if not test:
        import logging as _log
        _log.getLogger(__name__).warning(
            f"save_hypothesis: test пустой (trigger={trigger}, obs={observation[:40]!r})"
        )
    payload = {
        "observation":          observation,
        "mechanism":            mechanism,
        "prediction":           prediction,
        "test":                 test,
        "status":               status,
        "trigger":              trigger,
        "trigger_subtype":      trigger_subtype,
        "linked_experiment_id": linked_experiment_id,
        "created_date":         str(get_today()),
        "resolution_type":      resolution_type,
    }
    key_suffix = f"_{trigger_subtype}" if trigger_subtype else ""
    key = f"hyp_{get_today()}_{trigger}{key_suffix}_{observation[:30].replace(' ','_')}"
    hyp_id = db.save_memory(
        category   = "hypothesis",
        key        = key,
        value      = _json.dumps(payload, ensure_ascii=False),
        confidence = 0.5,
        source     = {
            "drift":              "auto_drift",
            "correlation_drift":  "auto_correlation",
            "specialist_review":  "auto_specialist_review",
            "survivorship":       "auto_survivorship",
            "literature":         "auto_literature",
            "monthly_consilium":  "auto_consilium",
        }.get(trigger, "manual"),
    )
    # Notify перенесён в generate_hypothesis_from_* (W5F-2): теперь использует
    # patient_view из cbcr_dict через _notify_patient_view. save_hypothesis оставляем
    # backward-compatible с legacy callers — без notification отсюда.
    return hyp_id


def get_open_hypotheses(n: int = 10) -> list[dict]:
    """Возвращает активные (status != confirmed/rejected) гипотезы из memory."""
    import json as _json
    rows   = db.get_memory(category="hypothesis", n=n)
    result = []
    for row in rows:
        try:
            payload = _json.loads(row["value"])
            if payload.get("status") in ("confirmed", "rejected"):
                continue
            payload["memory_id"]  = row["id"]
            payload["updated_at"] = row.get("updated_at", "")
            result.append(payload)
        except Exception:
            pass
    return result


def hypothesis_verdict(memory_id: int) -> str | None:
    """Вердикт гипотезы: 'confirmed' | 'rejected' | None (открыта/не найдена).

    Бриф берёт гипотезы окном «последние N» (get_open_hypotheses). Исчезновение
    из окна может означать как явный вердикт, так и вытеснение новыми записями.
    Выдуманный пример: запись A получила rejected, запись B осталась open,
    но обе вышли из окна. Только A можно назвать закрытой.
    Сказать «закрыта» можно только по вердикту."""
    import json as _json
    for row in db.get_memory(category="hypothesis", n=1000, active_only=False):
        if row["id"] != memory_id:
            continue
        try:
            st = _json.loads(row["value"]).get("status")
        except Exception:  # silent-ok: битая запись — вердикта нет, «закрыта» не скажем
            return None
        return st if st in ("confirmed", "rejected") else None
    return None


def update_hypothesis_status(memory_id: int, status: str, note: str = None):
    """Обновляет статус гипотезы (open → testing → confirmed | rejected)."""
    import json as _json
    rows = db.get_memory(category="hypothesis", n=100, active_only=False)
    for row in rows:
        if row["id"] != memory_id:
            continue
        try:
            payload = _json.loads(row["value"])
            payload["status"] = status
            if note:
                payload["resolution_note"] = note
            db.save_memory(
                category   = "hypothesis",
                key        = row.get("key"),
                value      = _json.dumps(payload, ensure_ascii=False),
                confidence = row.get("confidence", 0.5),
                source     = row.get("source", "manual"),
            )
        except Exception:
            pass
        break


def append_dynamics_to_hypothesis(memory_id: int, note: str) -> bool:
    """этап2: добавляет запись динамики серии в гипотезу (поле dynamics[]) — врач видит
    эволюцию во времени. Возвращает True если гипотеза найдена и обновлена."""
    import json as _json
    rows = db.get_memory(category="hypothesis", n=200, active_only=False)
    for row in rows:
        if row["id"] != memory_id:
            continue
        try:
            payload = _json.loads(row["value"])
        except Exception:
            return False
        payload.setdefault("dynamics", []).append({"date": str(get_today()), "note": note})
        db.save_memory(
            category="hypothesis", key=row.get("key"),
            value=_json.dumps(payload, ensure_ascii=False),
            confidence=row.get("confidence", 0.5), source=row.get("source", "manual"),
        )
        return True
    return False


def generate_hypothesis_from_drift(drifts: list[dict],
                                   linked_experiment_id: int = None) -> list[int]:
    """W5H-B (2026-05-14): defunct.

    Гипотезы теперь генерируются ТОЛЬКО через monthly_consilium (1-го числа).
    Эта функция оставлена для backward-compat (gp_agent, triage_agent её вызывают)
    но возвращает []. Drift signals — это шум на суточных колебаниях, а не повод
    для гипотез. Реальные паттерны накапливаются и интерпретируются раз в месяц
    через консилиум 11 специалистов.

    Pipeline:
    """
    if drifts:
        log.info(
            f"generate_hypothesis_from_drift: {len(drifts)} drift signals "
            f"received, ignored (gen disabled, see monthly_consilium)"
        )
    return []
    # === LEGACY CODE BELOW (unreachable, kept for reference until cleanup) ===
    if not drifts:
        return []

    import json as _json
    import cbcr_hypothesis as cbcr

    existing     = get_open_hypotheses(n=50)
    existing_obs = {h.get("observation", "")[:60] for h in existing}
    saved_ids    = []

    for drift in drifts:
        try:
            observation = _build_cbcr_observation_from_drift(drift)
            cbcr_dict = cbcr.generate_hypothesis_with_critique(observation)
        except Exception as e:
            log.warning(f"CBCR generation failed for drift={drift.get('metric')}: {e}")
            continue

        flat = cbcr.flatten_cbcr_payload(cbcr_dict)
        if not flat["observation"] or not flat["mechanism"]:
            log.warning(f"CBCR flatten вернул пустые поля для {drift.get('metric')}")
            continue

        # Дедуп по CBCR one_line_statement (первые 50 chars)
        if any(flat["observation"][:50] in ex for ex in existing_obs):
            log.info(f"Дубль drift гипотезы пропущен: {flat['observation'][:60]}")
            continue

        hyp_id = save_hypothesis(
            observation          = flat["observation"],
            mechanism            = flat["mechanism"],
            prediction           = flat["prediction"],
            test                 = flat["test"],
            trigger              = "drift",
            linked_experiment_id = linked_experiment_id,
            status               = "testing" if linked_experiment_id else "open",
            resolution_type      = flat["resolution_type"],
        )
        saved_ids.append(hyp_id)

        # CBCR-payload в hypotheses_cbcr (Q1=B)
        sc = cbcr_dict.get("structural_confidence") or {}
        prov = cbcr_dict.get("provenance") or {}
        try:
            db.save_cbcr_payload(
                memory_id        = hyp_id,
                payload_json     = _json.dumps(cbcr_dict, ensure_ascii=False),
                structural_score = int(sc.get("score") or 0),
                confidence_level = sc.get("confidence") or "unknown",
                generated_by     = prov.get("generator") or "cbcr_hypothesis",
                model            = prov.get("model") or hai_core.get_model("sonnet"),
            )
        except Exception as e:
            log.warning(f"save_cbcr_payload failed for memory_id={hyp_id}: {e}")

        log.info(
            f"CBCR гипотеза (drift): memory_id={hyp_id}, "
            f"score={sc.get('score')}, conf={sc.get('confidence')}, "
            f"obs={flat['observation'][:80]}"
        )

        # W5F-3: patient-friendly Telegram уведомление при needs_specialist
        if flat["resolution_type"] == "needs_specialist":
            try:
                _notify_patient_view(hyp_id, cbcr_dict, trigger_label="drift")
            except Exception as e:
                log.warning(f"_notify_patient_view failed for {hyp_id}: {e}")

    return saved_ids


def confirm_hypothesis(memory_id: int) -> dict | None:
    """Подтверждает гипотезу (status → confirmed), возвращает payload."""
    import json as _json
    rows = db.get_memory(category="hypothesis", n=200, active_only=False)
    for row in rows:
        if row["id"] != memory_id:
            continue
        try:
            payload          = _json.loads(row["value"])
            if payload.get("status") == "confirmed":
                # Повторное подтверждение — не новое событие: второй протокол не заводим (01.10, #1118).
                payload["memory_id"] = memory_id
                payload["_already_confirmed"] = True
                return payload
            payload["status"]    = "confirmed"
            payload["memory_id"] = memory_id
            db.save_memory(
                category   = "hypothesis",
                key        = row.get("key"),
                value      = _json.dumps(payload, ensure_ascii=False),
                confidence = 0.9,
                source     = row.get("source", "manual"),
            )
            log.info(f"Гипотеза #{memory_id} подтверждена")
            return payload
        except Exception as e:
            log.warning(f"confirm_hypothesis: {e}")
            return None
    return None


def reject_hypothesis(memory_id: int, reason: str = "") -> bool:
    """Отклоняет гипотезу (status → rejected)."""
    import json as _json
    rows = db.get_memory(category="hypothesis", n=200, active_only=False)
    for row in rows:
        if row["id"] != memory_id:
            continue
        try:
            payload = _json.loads(row["value"])
            payload["status"] = "rejected"
            if reason:
                payload["rejection_reason"] = reason
            db.save_memory(
                category   = "hypothesis",
                key        = row.get("key"),
                value      = _json.dumps(payload, ensure_ascii=False),
                confidence = 0.2,
                source     = row.get("source", "manual"),
            )
            log.info(f"Гипотеза #{memory_id} отклонена")
            return True
        except Exception:
            return False
    return False




# ── W5A-INT-1: drift → CBCR observation ─────────────────────────────────────

_DRIFT_METRIC_LABELS = {
    "deep_min":   "глубокий сон",
    "rem_min":    "REM-сон",
    "hrv_ms":     "ВСР",
    "readiness":  "восстановление",
    "steps":      "шаги",
    "resting_hr": "ЧСС покоя",
    "sleep_total": "общий сон",
    "spo2_avg":    "SpO2",
}


def _drift_acuteness(streak_days: int) -> str:
    """Перевод streak в Bordage semantic qualifier."""
    if streak_days < 3:
        return "acute"
    if streak_days < 14:
        return "sub-acute"
    return "chronic"


def _drift_course(direction: str, streak_days: int) -> str:
    """Перевод в course qualifier."""
    if streak_days >= 7:
        return "monotonic"
    return "progressive"


def _build_cbcr_observation_from_drift(drift: dict) -> dict:
    """Конвертирует drift_dict → CBCR observation для generate_hypothesis_with_critique.

    Drift shape: {metric, direction, delta_pct, streak_days, current_7d, baseline_30d}.
    Patient context инжектируется отдельно (W5A-D6 _build_patient_context_block).
    """
    metric = drift.get("metric") or "?"
    metric_lbl = _DRIFT_METRIC_LABELS.get(metric, metric)
    direction = drift.get("direction") or "down"
    direction_word = "снижение" if direction == "down" else "рост"
    streak = int(drift.get("streak_days") or 0)
    delta_pct = float(drift.get("delta_pct") or 0)
    acuteness = _drift_acuteness(streak)
    course = _drift_course(direction, streak)

    summary = (
        f"{acuteness.capitalize()} {course} {direction_word} ({metric_lbl}) "
        f"на {delta_pct:+.1f}% за {streak}д подряд "
        f"(current_7d {drift.get('current_7d')}, baseline_30d {drift.get('baseline_30d')})"
    )

    return {
        "trigger": "drift",
        "summary": summary,
        "details": dict(drift),  # raw данные для evidence_for.fact whitelist (W5A-D2)
    }



# ── W5A-INT-2: correlation_drift → CBCR observation ─────────────────────────

_CORR_METRIC_LABELS = {
    "hrv": "ВСР", "resting_hr": "ЧСС покоя", "readiness": "готовность",
    "sleep_total": "общий сон", "sleep_deep": "глубокий сон",
    "sleep_rem": "REM-сон", "sleep_score": "качество сна",
    "sleep_efficiency": "эффективность сна",
    "steps": "шаги", "active_kcal": "активные ккал",
    "spo2_avg": "SpO2", "weight": "вес",
}


def _corr_change_desc(r_recent: float, r_baseline: float, delta_pct: float) -> str:
    """Семантический qualifier изменения корреляции."""
    if r_recent * r_baseline < 0:
        return f"СМЕНИЛА ЗНАК (было {r_baseline:+.2f}, стало {r_recent:+.2f})"
    if abs(r_recent) > abs(r_baseline):
        return f"УСИЛИЛАСЬ ({r_baseline:+.2f} → {r_recent:+.2f}, {delta_pct:+.0f}%)"
    return f"ОСЛАБЛА ({r_baseline:+.2f} → {r_recent:+.2f}, {delta_pct:+.0f}%)"


def _build_cbcr_observation_from_correlation(drift: dict) -> dict:
    """correlation_drift_dict → CBCR observation для generate_hypothesis_with_critique.

    correlation_drift shape: {metric_a, metric_b, r_recent, r_baseline,
                              delta_pct, severity, n_pairs_recent, n_pairs_baseline}.
    """
    m_a = drift.get("metric_a") or "?"
    m_b = drift.get("metric_b") or "?"
    m_a_lbl = _CORR_METRIC_LABELS.get(m_a, m_a)
    m_b_lbl = _CORR_METRIC_LABELS.get(m_b, m_b)
    r_recent = float(drift.get("r_recent") or 0)
    r_baseline = float(drift.get("r_baseline") or 0)
    delta_pct = float(drift.get("delta_pct") or 0)
    severity = drift.get("severity") or "mild"

    change = _corr_change_desc(r_recent, r_baseline, delta_pct)
    summary = (
        f"Корреляция {m_a_lbl}↔{m_b_lbl} {change}. Severity: {severity}. "
        f"n_pairs recent={drift.get('n_pairs_recent')} baseline={drift.get('n_pairs_baseline')}."
    )

    return {
        "trigger": "correlation_drift",
        "summary": summary,
        "details": dict(drift),  # raw для D2 whitelist
    }


def _notify_specialist(text: str, reply_markup=None):
    """Fire-and-forget Telegram уведомление (legacy простой текст).

    W5F-2: убрано [:200] truncation. Telegram держит до 4096 chars.
    Для богатого формата используй _notify_patient_view().
    """
    import os, urllib.request, urllib.parse
    from pathlib import Path
    # per-tenant: секреты из единого источника (иначе алёрт тенанта уйдёт не туда)
    from secrets_paths import secrets_dir
    secrets = secrets_dir()
    try:
        token   = (secrets / "telegram_token").read_text().strip()
        chat_id = (secrets / "telegram_chat_id").read_text().strip()
        url  = f"https://api.telegram.org/bot{token}/sendMessage"
        # Truncate только до Telegram-лимита (4096), не до 200.
        payload = {"chat_id": chat_id, "text": text[:4000]}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup.to_json()
        data = urllib.parse.urlencode(payload).encode()
        urllib.request.urlopen(url, data, timeout=8)
    except Exception as e:
        log.warning(f"_notify_specialist: {e}")


def _notify_patient_view(memory_id: int, cbcr_dict: dict, trigger_label: str = "drift"):
    """W5F-2: patient-friendly Telegram-уведомление о гипотезе.

    Формирует структурированный текст из patient_view + кнопка запроса для врача.
    Fallback: если patient_view нет — использует one_line_statement без обрезания.
    """
    import i18n
    pv = (cbcr_dict or {}).get("patient_view") or {}
    one_line = (cbcr_dict or {}).get("one_line_statement") or ""

    if pv and all(pv.get(k) for k in ("noticed", "might_mean", "do_now", "consult_when")):
        text = (
            i18n.t("hypotheses.notice.heading", memory_id=memory_id)
            + i18n.t("hypotheses.notice.body", **{k: pv[k] for k in ("noticed", "might_mean", "do_now", "consult_when")})
        )
    else:
        # Fallback: гипотеза без patient_view (старая или regenerate failed)
        text = (
            i18n.t("hypotheses.notice.specialist", memory_id=memory_id)
            + f"{one_line}\n\n"
        )

    _notify_specialist(text + i18n.t("hypotheses.notice.silence"), reply_markup=notice_keyboard(memory_id))


def notice_keyboard(memory_id: int, lang=None):
    """Кнопки уведомления о новой гипотезе — один дом для обоих путей (генератор и консилиум).
    01.10 (слово владельца): решить по гипотезе можно прямо в уведомлении; до этого
    «Подтвердить/Отклонить» были только в списке /hypotheses, в уведомлении — один «Запрос»."""
    import i18n
    from bot import actions
    return actions.keyboard(
        [actions.button(i18n.t("actions.confirm", lang), "hc", memory_id),
         actions.button(i18n.t("actions.reject", lang), "hr", memory_id)],
        [actions.button(i18n.t("actions.hypothesis.query", lang), "hq", memory_id)])


def get_specialist_hypotheses(n: int = 20) -> list[dict]:
    """Открытые гипотезы с resolution_type=needs_specialist."""
    import json as _json
    rows = db.get_memory(category="hypothesis", n=100, active_only=True)
    result = []
    for row in rows:
        try:
            payload = _json.loads(row["value"])
            if payload.get("resolution_type") == "needs_specialist":
                if payload.get("status") not in ("confirmed", "rejected"):
                    payload["memory_id"] = row["id"]
                    result.append(payload)
        except Exception:
            pass
    return result[:n]


# ── Протоколы ─────────────────────────────────────────────────────────────

def _judge_protocol(raw: str) -> None:
    """Судит текст протокола против канона и КРИЧИТ, но текста не меняет.

    Почему не поправка, как в чате и брифе (14.09): выход здесь СТРУКТУРНЫЙ — JSON,
    который тут же разбирается; приписка «уточнение по данным» сломала бы разбор, а
    вписывать её внутрь поля значит редактировать данные, а не текст. Поэтому здесь
    политика третья: громкий лог, а доставку человеку берёт на себя ночной датчик по
    СОХРАНЁННЫМ гипотезам (integrity_tests.check_reco_repeats_fresh_lab читает
    hypotheses_cbcr) — тот же приём, что для консилиума.

    Если профиль показывает только часть канона, модель может принять отсутствие
    выдуманного Example_H в этом срезе за отсутствие данных вообще. Судья сверяет
    такое утверждение с каноном и учитывает явно названную границу окна.
    """
    try:
        import gp_context as _gc
        hits, _ = _gc.judge_absence_claims(raw)
    except Exception as e:
        log.warning("протокол: судья отсутствия не отработал: %s", e)
        return
    if hits:
        log.warning("протокол: утверждение об отсутствии против канона (%d): %s",
                    len(hits), "; ".join(f"{h['test']} ({h['last_date']})" for h in hits))


def generate_protocol_from_hypothesis(hypothesis: dict) -> dict:
    """LLM генерирует поведенческий протокол из подтверждённой гипотезы."""
    import json as _json, re as _re

    # brief-neutralization A2: контекст пациента — из данных ЗАПУЩЕННОГО тенанта,
    # не вшитый литерал тенанта. Диагноз рендерится только если
    # он в данных тенанта; тенант без состояний → нейтрально.
    try:
        import patient_context
        _pctx = patient_context.build_patient_brief()
    except Exception:  # silent-ok: БД недоступна → нейтральный контекст, без досье
        _pctx = "Профиль пациента временно недоступен."

    client = get_client()
    prompt = (
        f"Гипотеза подтверждена:\n"
        f"  Наблюдение: {hypothesis.get('observation', '')}\n"
        f"  Механизм: {hypothesis.get('mechanism', '')}\n"
        f"  Прогноз: {hypothesis.get('prediction', '')}\n\n"
        "На основе этой гипотезы сформулируй практический протокол поведения.\n"
        "Ответь строго JSON с ключами:\n"
        '  "title": краткое название протокола (5-8 слов),\n'
        '  "behavior": конкретное действие — что делать, когда, как долго (2-3 предложения),\n'
        '  "rationale": почему это работает — одно предложение,\n'
        '  "frequency": как часто выполнять ("ежедневно", "3 раза в неделю", и т.д.),\n'
        '  "reminder_days": через сколько дней напомнить о проверке протокола (число, например 30),\n'
        '  "retire_condition": когда протокол можно отменить или пересмотреть.\n'
        f"Контекст пациента (из его данных):\n{_pctx}\n"
        "Протокол должен быть конкретным, реалистичным, проверяемым, учитывая профиль пациента выше."
    ) + hai_core.answer_language()

    resp = client.messages.create(
        model=hai_core.get_model("haiku_pinned"),
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = resp.content[0].text.strip()
    _judge_protocol(raw)          # структурный выход: судим и кричим, текст не трогаем

    m = _re.search(r'\{', raw)
    if m:
        json_str = raw[m.start():]
        open_b   = json_str.count('{') - json_str.count('}')
        if open_b > 0:
            json_str += '}' * open_b
        try:
            parsed = _json.loads(json_str)
        except _json.JSONDecodeError:
            parsed = {}
            for key in ("title", "behavior", "rationale", "frequency"):
                km = _re.search(rf'"{key}"\s*:\s*"([^"]+)"', raw)
                if km:
                    parsed[key] = km.group(1)
    else:
        parsed = {}

    rd_raw = parsed.get("reminder_days", 30)
    try:
        rd = int(rd_raw) if not isinstance(rd_raw, list) else rd_raw
        if isinstance(rd, int):
            rd = [rd, rd * 2]
    except Exception:
        rd = [30, 60]

    return {
        "title":                 parsed.get("title", hypothesis.get("observation", "")[:50]),
        "behavior":              parsed.get("behavior", ""),
        "rationale":             parsed.get("rationale", hypothesis.get("mechanism", "")),
        "frequency":             parsed.get("frequency", ""),
        "reminder_days":         _json.dumps(rd),
        "linked_hypothesis_id":  hypothesis.get("memory_id"),
        "linked_experiment_id":  hypothesis.get("linked_experiment_id"),
    }


def save_protocol(protocol: dict) -> int:
    """Сохраняет протокол в таблицу protocols."""
    protocol_id = db.save_protocol(protocol)
    log.info(f"Протокол #{protocol_id} сохранён: {protocol['title']}")
    return protocol_id


def get_active_protocols() -> list[dict]:
    """Возвращает все активные протоколы."""
    return db.get_active_protocols()


def retire_protocol(protocol_id: int, note: str = "") -> bool:
    """Снимает протокол (status → retired)."""
    return db.retire_protocol(protocol_id, note)


# ── Форматирование ────────────────────────────────────────────────────────

def format_hypotheses_for_gp(hypotheses: list[dict]) -> str:
    """Краткий блок для инжекта в контекст GP — активные гипотезы."""
    if not hypotheses:
        return ""
    lines = ["АКТИВНЫЕ ГИПОТЕЗЫ (требуют отслеживания):"]
    for h in hypotheses[:3]:
        status_label = {"open": "открыта", "testing": "проверяется"}.get(
            h.get("status"), h.get("status", "")
        )
        lines.append(f"  [{status_label}] {h.get('observation', '')}")
        if h.get("prediction"):
            lines.append(f"    Прогноз: {h['prediction']}")
    return "\n".join(lines)


# ── Wave 4-CORRELATIONS C-3 ─────────────────────────────────────────────────

def generate_hypothesis_from_correlation(
    correlation_drifts: list[dict],
    linked_experiment_id: int = None,
) -> list[int]:
    """W5H-B (2026-05-14): defunct.

    То же что generate_hypothesis_from_drift — гипотезы только через monthly_consilium.
    Возвращает []. Сигналы накапливаются как сырьё в БД (daily_metrics, etc).
    """
    if correlation_drifts:
        log.info(
            f"generate_hypothesis_from_correlation: {len(correlation_drifts)} "
            f"correlation drifts received, ignored (gen disabled)"
        )
    return []
    # === LEGACY CODE BELOW (unreachable, kept for reference) ===
    if not correlation_drifts:
        return []

    import json as _json
    import cbcr_hypothesis as cbcr

    saved_ids: list[int] = []

    for drift in correlation_drifts[:3]:  # top-3 по |delta_pct|
        try:
            observation = _build_cbcr_observation_from_correlation(drift)
            cbcr_dict = cbcr.generate_hypothesis_with_critique(observation)
        except Exception as e:
            log.warning(
                f"CBCR generation failed for correlation "
                f"{drift.get('metric_a')}↔{drift.get('metric_b')}: {e}"
            )
            continue

        flat = cbcr.flatten_cbcr_payload(cbcr_dict)
        if not flat["observation"] or not flat["mechanism"]:
            log.warning(
                f"CBCR flatten вернул пустые поля для "
                f"correlation {drift.get('metric_a')}↔{drift.get('metric_b')}"
            )
            continue

        hyp_id = save_hypothesis(
            observation=flat["observation"],
            mechanism=flat["mechanism"],
            prediction=flat["prediction"],
            test=flat["test"],
            trigger="correlation_drift",
            linked_experiment_id=linked_experiment_id,
            resolution_type=flat["resolution_type"],
        )
        saved_ids.append(hyp_id)

        sc = cbcr_dict.get("structural_confidence") or {}
        prov = cbcr_dict.get("provenance") or {}
        try:
            db.save_cbcr_payload(
                memory_id        = hyp_id,
                payload_json     = _json.dumps(cbcr_dict, ensure_ascii=False),
                structural_score = int(sc.get("score") or 0),
                confidence_level = sc.get("confidence") or "unknown",
                generated_by     = prov.get("generator") or "cbcr_hypothesis",
                model            = prov.get("model") or hai_core.get_model("sonnet"),
            )
        except Exception as e:
            log.warning(f"save_cbcr_payload failed for memory_id={hyp_id}: {e}")

        log.info(
            f"CBCR гипотеза (correlation): memory_id={hyp_id}, "
            f"score={sc.get('score')}, conf={sc.get('confidence')}, "
            f"obs={flat['observation'][:80]}"
        )

        # W5F-3: patient-friendly Telegram уведомление при needs_specialist
        if flat["resolution_type"] == "needs_specialist":
            try:
                _notify_patient_view(hyp_id, cbcr_dict, trigger_label="correlation_drift")
            except Exception as e:
                log.warning(f"_notify_patient_view failed for {hyp_id}: {e}")

    return saved_ids
