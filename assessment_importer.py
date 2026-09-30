#!/usr/bin/env python3.11
"""assessment_importer — парсит JSON-заполнения опросников в lab_results.

Один контракт: на вход путь к JSON (или директория для bulk), на выход —
записи в lab_results с source='instrument:<id>' и закрытие соответствующей
assessment_session + tasks.

Триггер: запускается через fswatch на ~/health/data/assessments/ (production)
или вручную (smoke). Также вызывается из assessment_dialog.finalize().

CLI:
    python3.11 assessment_importer.py <path-to.json>
    python3.11 assessment_importer.py --all   # пройти по всем JSON в assessments/
"""
from __future__ import annotations
from _time_inject import get_utcnow  # seam

import i18n
import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import health_db as db

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

from assessment_scheduler import tenant_data_path
INSTRUMENTS_DIR = tenant_data_path("instruments")  # каталог опросников ЭТОГО человека (28.09, этап Б)
# Каталог опросников — данные установки; доступность проверяется для текущего тенанта.
# Отсутствующий каталог не означает отсутствие заполненных опросников.


def assessments_dir() -> Path:
    """Ответы опросников — данные ЧЕЛОВЕКА, поэтому каталог тенанта (health_db._HEALTH_DIR).
    До 2026-09-23 — константа ~/health/data/assessments: ответы любого тенанта ложились
    владельцу (нить profile-home). Один дом для диалога и импортёра."""
    return Path(db._HEALTH_DIR) / "data" / "assessments"


def instrument_source_id(canonical_id: str) -> str:
    """Короткий id опросника для source/test_name (`instrument:<id>`): модуль семейства EORTC QLQ
    пишется без префикса семейства (eortc_qlq_xx → xx), остальные — как есть. Один дом конвенции
    для импортёра, диалога, планировщика и ночного датчика (до 2026-09-23 — четыре копии
    с литералом конкретного модуля)."""
    return canonical_id.removeprefix("eortc_qlq_")


def _load_instrument(instrument_id):
    p = INSTRUMENTS_DIR / f"{instrument_id}.json"
    if not p.exists():
        raise FileNotFoundError(f"instrument {instrument_id} not found in {INSTRUMENTS_DIR}")
    return json.loads(p.read_text())


_RECALL_RU = {"past_week": "за неделю", "past_2_weeks": "за 2 недели", "past_month": "за месяц"}


def describe_scores(source_id: str, scores: dict) -> str | None:
    """Понятное описание результатов опросника вместо необъяснённого набора чисел.
    Шкалы 0–100 и их направление (больше — хуже / больше — лучше) берутся из каталога.
    Для одной итоговой шкалы показываем сумму баллов и полосу по порогам каталога.
    Каталога нет → None, экран покажет исходное представление."""
    inst = None
    for cand in dict.fromkeys([source_id, f"eortc_qlq_{source_id}"]):
        try:
            inst = _load_instrument(cand)
            break
        except (FileNotFoundError, ValueError):
            continue
    if not inst or not scores:
        return None
    lang = i18n.lang_of()
    # Подписи — ДАННЫЕ каталога установки (title_ru, label_ru шкал, threshold_labels_ru),
    # не литералы кода: набор опросников у человека свой, и по нему читается его диагноз.
    subs = {s.get("id"): s for s in inst.get("subscales") or []}
    label = lambda k: (subs.get(k) or {}).get("label_ru") or k  # noqa: E731
    head = f"{inst.get('title_ru') or inst.get('name') or source_id}, {_RECALL_RU.get(inst.get('recall_period'), '')}".rstrip(", ")
    th = inst.get("thresholds") or {}
    th_ru = inst.get("threshold_labels_ru") or {}
    if set(scores) == {"total"} and th:
        rs = inst.get("response_scale") or {}
        lo, hi = rs.get("min", 0), rs.get("max", 4)
        n = len(subs.get("total", {}).get("items") or [])
        if n:
            raw = round(scores["total"] / 100 * (hi - lo) * n + lo * n)
            passed = [k for k, v in sorted(th.items(), key=lambda kv: kv[1]) if raw >= v]
            band = (th_ru.get(passed[-1], passed[-1]) if passed else
                    th_ru.get("below", i18n.t("assessment.scores.below_thresholds", lang)))
            return i18n.t("assessment.scores.total", lang, head=head, raw=raw,
                          maximum=(hi - lo) * n + lo * n, band=band)
    worse = {k: v for k, v in scores.items()
             if subs.get(k, {}).get("direction", "symptom_higher_worse") == "symptom_higher_worse"}
    better = {k: v for k, v in scores.items() if k not in worse}
    parts = []
    felt = {k: v for k, v in worse.items() if v > 0}
    if felt:
        parts.append(i18n.t("assessment.scores.symptoms", lang,
                            symptoms=", ".join(f"{label(k)} {round(v)}" for k, v in felt.items())))
    if worse and not felt:
        parts.append(i18n.t("assessment.scores.no_symptoms", lang, count=len(worse)))
    elif len(felt) < len(worse):
        parts.append(i18n.t("assessment.scores.other_zero", lang, count=len(worse) - len(felt)))
    for k, v in better.items():
        parts.append(i18n.t("assessment.scores.function", lang, label=label(k), value=round(v)))
    return f"{head}: " + "; ".join(parts)


def _compute_subscale_scores(instrument, raw_responses):
    """По правилам из каталога: вычисляет {subscale_id: score_0_100}."""
    scale_min = instrument.get("response_scale", {}).get("min", 1)
    scale_max = instrument.get("response_scale", {}).get("max", 4)
    rng = max(1, scale_max - scale_min)
    out = {}
    for sub in instrument.get("subscales") or []:
        sid = sub.get("id")
        items = sub.get("items") or []
        direction = sub.get("direction", "symptom_higher_worse")
        vals = []
        for item_id in items:
            v = raw_responses.get(item_id)
            if v is None:
                continue
            try:
                vals.append(float(v))
            except (TypeError, ValueError):  # silent-ok: невалидное значение
                continue
        if not vals:
            continue
        mean = sum(vals) / len(vals)
        # Linear transformation 0-100
        score = (mean - scale_min) / rng * 100.0
        if direction == "vigor_reverse_higher_better":
            # Для vigor: высокие значения — хорошо, но в lab_results всё-таки сохраняем raw 0-100
            # без инверсии (агент решит как интерпретировать через direction в каталоге).
            pass
        out[sid] = round(score, 2)
    return out


def import_file(path):
    p = Path(path)
    if not p.exists():
        log.error(f"file not found: {p}")
        return False
    try:
        data = json.loads(p.read_text())
    except Exception as e:
        log.error(f"parse failed for {p}: {e}")
        return False

    instrument_id = data.get("instrument") or data.get("instrument_id")
    if not instrument_id:
        log.error(f"no instrument field in {p}")
        return False
    # Каталог может лежать под полным (eortc_qlq_xx) или коротким (xx) именем — пробуем оба.
    short = instrument_source_id(instrument_id)
    instrument = None
    for cand in dict.fromkeys([instrument_id, short, f"eortc_qlq_{short}"]):
        try:
            instrument = _load_instrument(cand)
            break
        except FileNotFoundError:
            continue
        except Exception as e:
            log.error(f"instrument {cand} catalog unreadable: {e}")
            return False
    if instrument is None:
        log.error(f"instrument {instrument_id} catalog missing in {INSTRUMENTS_DIR}")
        return False

    canonical_id = instrument.get("id", instrument_id)
    source_id = instrument_source_id(canonical_id)

    # Сверка wording_hash
    catalog_hash = instrument.get("wording_version_hash", "")
    file_hash = data.get("wording_version_hash", "")
    if catalog_hash and file_hash and catalog_hash != file_hash:
        log.warning(f"wording_version_hash mismatch: file={file_hash[:30]} catalog={catalog_hash[:30]}")

    raw_responses = data.get("raw_responses") or data.get("answers_json") or data.get("answers") or {}
    if not raw_responses:
        log.error(f"no raw_responses in {p}")
        return False

    # Дата заполнения
    assessment_date = data.get("assessment_date") or get_utcnow().date().isoformat()

    subscale_scores = _compute_subscale_scores(instrument, raw_responses)
    log.info(f"  computed {len(subscale_scores)} subscales for {source_id}")

    # Сохраняем каждый subscale как отдельную lab_result запись
    inserted = 0
    lab_result_ids: list[int] = []
    with db.get_conn() as conn:
        for sid, score in subscale_scores.items():
            test_name = f"{source_id}_{sid}"
            try:
                cur = conn.execute(
                    "INSERT OR REPLACE INTO lab_results "
                    "(date, source, test_name, value, unit, status, notes) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (assessment_date, f"instrument:{source_id}", test_name, float(score),
                     "score_0_100", None,
                     f"wording_hash={file_hash[:20]}; n_items={len([i for i in (raw_responses or {})])}"),
                )
                inserted += 1
                if cur.lastrowid:
                    lab_result_ids.append(int(cur.lastrowid))
            except Exception as e:
                log.warning(f"INSERT failed for {test_name}: {e}")
    log.info(f"  inserted {inserted} lab_results rows")
    if inserted == 0:
        # BL-SILENT-0ROWS-1: ответы есть, а ни одна субшкала не посчиталась (дрейф ключей
        # ответов против items каталога — майский случай с суффиксом `qN_suffix`). Раньше здесь шло
        # «успех»: сессия и задача закрывались как заполненные, а баллов не было.
        log.error(f"0 строк из {len(raw_responses)} ответов {source_id}: ключи ответов не совпали "
                  f"с items каталога — файл {p.name} цел, задача НЕ закрыта")
        return False

    # SX-15: интеграция в FHIR-like events layer.
    # PRO-опросник — это self_observation event с прицепленным diagnostic_event
    # (type=functional_test, modality=<instrument_id>). raw_values_ref связывает
    # с lab_results.id для traversal "событие → числа".
    try:
        scores_summary = ", ".join(
            f"{sid}={score}" for sid, score in subscale_scores.items()
        )
        evt_id = db.save_event(
            event_type="self_observation",
            effective_date=assessment_date,
            performer="self",
            performer_role="self",
            recorded_by="patient",
            notes=f"PRO: {source_id}; subscales: {scores_summary}",
            diagnostic={
                "type": "functional_test",
                "modality": source_id,
                "raw_values_ref": lab_result_ids,
                "interpreted_report": scores_summary[:1000],
                "abnormal_flags": [],
            },
        )
        log.info(f"  created event #{evt_id} (self_observation) + diagnostic_event")
    except Exception as e:
        log.warning(f"save_event for PRO failed: {e}")

    # Закрыть assessment_session если есть в файле
    session_id = data.get("assessment_session_id")
    if session_id:
        try:
            db.update_assessment_session(
                int(session_id),
                status="completed",
                completed_at=get_utcnow().isoformat(timespec="seconds"),
            )
            log.info(f"  closed assessment_session #{session_id}")
        except Exception as e:
            log.warning(f"close session failed: {e}")

    # Закрыть связанную задачу
    task_id = data.get("task_id")
    if task_id:
        try:
            db.resolve_task(int(task_id), resolved_text=f"Заполнен опросник {source_id}", status="completed")
            log.info(f"  resolved task #{task_id}")
        except Exception as e:
            log.warning(f"resolve task failed: {e}")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?", help="path to assessment JSON")
    ap.add_argument("--all", action="store_true", help="re-import all from assessments/")
    args = ap.parse_args()

    if args.all:
        adir = assessments_dir()
        if not adir.exists():
            log.error(f"dir not found: {adir}")
            return 1
        n_ok = 0
        for p in sorted(adir.glob("*.json")):
            log.info(f"importing {p.name}")
            if import_file(p):
                n_ok += 1
        log.info(f"done: {n_ok} files imported")
        return 0
    if not args.path:
        ap.print_help()
        return 1
    return 0 if import_file(args.path) else 1


if __name__ == "__main__":
    sys.exit(main())
