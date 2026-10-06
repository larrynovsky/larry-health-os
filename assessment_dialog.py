#!/usr/bin/env python3.11
"""assessment_dialog — state machine опросниковых диалогов через Telegram.

Standalone модуль, не зависит от telegram-lib. Контракт для бота:

  start(chat_id, task_id) -> (text, inline_keyboard_layout, session_id)
  answer(session_id, item_id, value) -> (text, inline_keyboard_layout | None, done: bool)
  get_active(chat_id) -> dict | None  — для роутинга свободного текста
  finalize(session_id) -> JSON-путь сохранённого файла

Знакомство (опросник проекта с sink="profile") — куда ложится каждый ответ и почему:
docs/explanation/onboarding_answers.md; пройти заново — docs/how-to/redo_onboarding.md.

Inline_keyboard_layout: list[list[tuple[label, callback_data]]].
Callback data:
  cb_aa:<sid>:<iid>:<v>   — answer
  cb_as:<action>:<task_id> — assessment task action (handled в bot routing)
"""
from __future__ import annotations
from _time_inject import get_today, get_utcnow  # seam

import json

import i18n
from _fmt_helpers import fmt_count, fmt_label, fmt_instrument_name
import notify
import logging
import re
from datetime import date, datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))
import health_db as db

log = logging.getLogger(__name__)

from assessment_scheduler import tenant_data_path
INSTRUMENTS_DIR = tenant_data_path("instruments")  # каталог опросников ЭТОГО человека (28.09, этап Б)
# Опросники проекта (не лицензионные, не данные человека) — в репозитории. Планировщик и
# датчик свежести читают только INSTRUMENTS_DIR: у опросника проекта нет ритма повторения,
# и попади он туда — ночной датчик звал бы «знакомство просрочено» каждую ночь (2026-09-23).
REPO_INSTRUMENTS_DIR = Path(__file__).resolve().parent / "methodology" / "instruments"


def _load_instrument_by_source_id(source_id):
    """source_id: короткий id опросника (isi, mfsi_sf, …) — найти JSON-файл.
    Сначала опросники проекта (репозиторий), затем каталог установки."""
    for base in (REPO_INSTRUMENTS_DIR, INSTRUMENTS_DIR):
        p = base / f"{source_id}.json"
        if p.exists():
            return json.loads(p.read_text())
    # Поиск по id внутри файлов
    for f in [*REPO_INSTRUMENTS_DIR.glob("*.json"), *INSTRUMENTS_DIR.glob("*.json")]:
        try:
            d = json.loads(f.read_text())
            if d.get("id") == source_id:
                return d
        except Exception:  # silent-ok: broken JSON
            continue
    return None


def _instrument_from_fingerprint(fingerprint):
    """assessment:<source_id> → source_id → load instrument."""
    if not fingerprint or not fingerprint.startswith("assessment:"):
        return None, None
    source_id = fingerprint.split(":", 1)[1]
    ins = _load_instrument_by_source_id(source_id)
    return source_id, ins


LOCATION_BUTTON = "__location__"   # маркер: бот рисует кнопку «отправить место» (reply-клавиатура)


def _item(instrument, item_id):
    return next((i for i in instrument.get("items") or [] if i.get("id") == item_id), None)


def _build_keyboard(instrument, item_id):
    """Клавиатура одного вопроса: list[list[(label, callback_data)]].

    Вид вопроса — `kind` пункта (по умолчанию scale, как у лицензионных опросников):
      scale  — кнопки шкалы response_scale;
      choice — кнопка на вариант (значение — индекс варианта);
      text/date/number — ответ текстом, кнопка только «пропустить»;
      location — кнопка «отправить место» (Telegram присылает координаты).
    У пункта с optional=true есть «пропустить» (значение -1)."""
    item = _item(instrument, item_id) or {}
    kind = item.get("kind", "scale")
    lang = i18n.lang_of()
    rows = []
    if kind == "scale":
        scale = instrument.get("response_scale", {})
        smin = int(scale.get("min", 1))
        smax = int(scale.get("max", 4))
        labels = scale.get("labels") or {}
        for v in range(smin, smax + 1):
            text = f"{v} · {labels.get(str(v), '')}".strip(" ·")
            rows.append([(text[:30], f"cb_aa:{item_id}:{v}")])
    elif kind == "choice":
        for n, opt in enumerate(item.get("options") or []):
            rows.append([(i18n.pick(opt.get("label", opt.get("value")), lang)[:40], f"cb_aa:{item_id}:{n}")])
    elif kind == "location":
        rows.append([(i18n.t("onboarding.button.send_location", lang), LOCATION_BUTTON)])
    known = _known_value(instrument, item)
    if known is not None:
        rows.insert(0, [(i18n.t("onboarding.button.confirm_known", lang, known=known)[:40], f"cb_aa:{item_id}:-2")])
    if item.get("optional"):
        rows.append([(i18n.t("common.button.skip", lang), f"cb_aa:{item_id}:-1")])
    return rows


def _known_value(instrument: dict, item: dict):
    """Что уже записано по полю этого пункта (знакомство: «верно?» вместо вопроса заново)."""
    if instrument.get("sink") != "profile" or not item.get("field"):
        return None
    try:
        import profile_db
        v = profile_db.get_patient_profile().get(item["field"])
    except Exception:  # silent-ok: профиль недоступен — спрашиваем как новое
        return None
    if v in (None, ""):
        return None
    for opt in item.get("options") or []:  # показываем подпись варианта, а не его код
        if str(opt.get("value")) == str(v):
            return i18n.pick(opt.get("label"))
    return v


def current_item(session: dict) -> dict | None:
    """Текущий неотвеченный пункт активной сессии (для маршрутизации текста и геопозиции)."""
    instrument = _load_instrument_by_source_id(session["instrument_id"])
    if not instrument:
        return None
    iid, _ = _next_unanswered(instrument, json.loads(session.get("answers_json") or "{}"))
    return _item(instrument, iid) if iid else None


def _resolve(item: dict, value):
    """Сырой ответ → (значение, текст для «записал: …») или (None, причина отказа).

    Проверка — на границе доверия: ответ пришёл от человека в чат. Пункт с полем профиля
    судится той же справкой, что и разборщик чата (methodology/profile_fields.yaml), — второго
    правила разбора нет."""
    kind = item.get("kind", "scale")
    if value == -1 and item.get("optional"):
        return "__skipped__", i18n.t("assessment.reply.skipped_value")
    if value == -2 and item.get("field"):
        return "__kept__", i18n.t("assessment.reply.kept_value")
    if kind == "scale":
        return value, str(value)
    if kind == "choice":
        opts = item.get("options") or []
        if isinstance(value, int) and 0 <= value < len(opts):
            return opts[value].get("value"), i18n.pick(opts[value].get("label", opts[value].get("value")))
        return None, i18n.t("assessment.error.choose_option")
    if kind == "location":
        if isinstance(value, (list, tuple)) and len(value) == 2:
            lat, lon = float(value[0]), float(value[1])
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                return [lat, lon], f"{lat:.3f}, {lon:.3f}"
        return None, i18n.t("assessment.error.send_location")
    raw = str(value).strip()
    field = item.get("field")
    if field:
        import profile_db
        spec = profile_db.profile_fields().get(field)
        if spec:
            v = profile_db._normalize(spec, raw)
            return (v, v) if v is not None else (None, i18n.pick(item.get("hint")) or i18n.t("assessment.error.unrecognized_answer"))
    limit = int(item.get("max_len", 500))
    if kind == "text" and 0 < len(raw) <= limit:
        return raw, raw if len(raw) <= 80 else raw[:79] + "…"
    return None, i18n.pick(item.get("hint")) or i18n.t("assessment.error.unrecognized_answer")


def _apply_sink(instrument: dict, item: dict, value) -> None:
    """Запись ответа туда, откуда его читают, — СРАЗУ, а не в конце: прерванное знакомство
    не теряет уже сказанного. Только для опросников с sink="profile" (знакомство); у
    лицензионных опросников ответы — баллы и едут в lab_results через finalize, как раньше."""
    if instrument.get("sink") != "profile" or value in ("__skipped__", "__kept__"):
        return
    field = item.get("field")
    if field:
        import profile_db
        # Источник — опросник, из которого пришёл ответ (до 04.10 любой опросник подписывался
        # «onboarding», и ответ medical_history 27.09 выглядел записью знакомства).
        profile_db.apply_stated(field, value, source=instrument.get("id") or "onboarding")
        return
    sink = _TARGETS.get(item.get("target"))
    if sink:
        sink(value)


def _lines(text) -> list[str]:
    """Свободный текст человека → пункты его же словами (без модели: гейт владельца не нужен
    для его собственных слов, и поверхности внедрения через текст нет). «нет» — ничего."""
    items = [x.strip(" -•\t") for x in str(text).replace(";", "\n").splitlines()]
    return [x for x in items if x and x.lower() not in ("нет", "no", "ничего", "готово", "-")]


def _sink_home(value) -> None:
    import config_db
    lat, lon = value
    config_db.upsert_config("location.home_lat", value_num=float(lat), category="location", source="onboarding")
    config_db.upsert_config("location.home_lon", value_num=float(lon), category="location", source="onboarding")


def _sink_brief_time(value) -> None:
    import config_db
    h, m = (int(x) for x in str(value).split(":"))
    config_db.upsert_config("schedule.morning_brief", value_json={"hour": h, "minute": m},
                            category="schedule", source="onboarding")


def _sink_problems(value) -> None:
    """Каждая проблема — ПРЕДЛОЖЕНИЕ в список (owner_gate_kept: в медкарту пишет только
    /approve человека). Карточки доставит outbox бота (proposal-delivery)."""
    import problems_db
    for line in _lines(value):
        problems_db.save_problem_proposal("onboarding", [{
            "action": "add", "problem_id": None,
            "new_value": {"title": line[:200], "status": "active", "description": line},
            "reason": "со слов при знакомстве"}])


def _sink_medications(value) -> None:
    """Лекарства словами человека: confirmation='manual' — это его слова, не вывод модели."""
    import treatment_db
    for line in _lines(value):
        treatment_db.upsert_medication(name=line[:200], status="active", source="onboarding",
                                       confirmation="manual", notes="со слов при знакомстве")


# Задача человеку говорит только «настраивает тот, кто устанавливал бота»: пути к секретам и
# эндпоинтам ему ни к чему (партия 8, 28.09). Установщику инструкции — здесь:
# docs/how-to/connect_oura.md, docs/how-to/connect_apple_health.md.
_SOURCE_TASKS = {
    "oura": "onboarding.task.connect_oura",
    "apple": "onboarding.task.connect_apple",
}


def _sink_connections(value) -> None:
    """Что подключить — задачей-действием с инструкцией (токены через чат не принимаем, §19)."""
    import health_db
    from secrets_paths import secrets_dir
    have = {"oura": "oura_token", "apple": "hae_ingest_token"}
    for key in {"oura": ["oura"], "apple": ["apple"], "both": ["oura", "apple"]}.get(value, []):
        try:
            if (secrets_dir() / have[key]).exists():  # уже подключено — задача была бы шумом
                continue
        except Exception:  # silent-ok: каталог секретов не определён — ставим задачу, лишняя дешевле пропущенной
            pass
        health_db.save_task(source="onboarding", type_="action", content=i18n.t(_SOURCE_TASKS[key]),
                            priority="medium")


_TARGETS = {"home": _sink_home, "brief_time": _sink_brief_time, "problems": _sink_problems,
            "medications": _sink_medications, "connections": _sink_connections}


def _next_unanswered(instrument, answers):
    """Возвращает (item_id, item_text) или (None, None) если всё заполнено."""
    for item in instrument.get("items") or []:
        iid = item.get("id")
        if iid not in answers:
            return iid, i18n.pick(item.get("text", ""))
    return None, None


def start(chat_id, task_id):
    """Запуск диалога. Возвращает (text, keyboard, session_id) или (error_text, None, None)."""
    # Найти task и его fingerprint
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT id, fingerprint, content FROM tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
    if not row:
        return (notify.fault(f"assessment_dialog.start: task missing task_id={task_id}"), None, None)
    fingerprint = row["fingerprint"]
    source_id, instrument = _instrument_from_fingerprint(fingerprint)
    if not instrument:
        return (notify.fault(f"assessment_dialog.start: instrument missing task_id={task_id} fingerprint={fingerprint}"), None, None)

    # Создать сессию (или resume если уже была)
    existing = db.get_active_assessment_session(chat_id, instrument_id=instrument.get("id"))
    if existing:
        sid = existing["id"]
        answers = json.loads(existing.get("answers_json") or "{}")
        log.info(f"resume session #{sid} with {len(answers)} answers")
    else:
        sid = db.save_assessment_session(
            instrument_id=instrument.get("id"),
            wording_version_hash=instrument.get("wording_version_hash", ""),
            chat_id=chat_id,
            task_id=task_id,
            answers_json="{}",
        )
        answers = {}
        log.info(f"started new session #{sid} for {instrument.get('id')}")

    iid, text = _next_unanswered(instrument, answers)
    if iid is None:
        # все ответы уже есть — finalize
        return finalize(sid)

    total = len(instrument.get("items") or [])
    n_done = len(answers)
    if instrument.get("sink") == "profile":
        # Урок, а не анкета (Diátaxis: показать, куда идём): вступление — на первом входе,
        # «продолжаем» — при возврате в прерванное.
        # Вступление — своё у опросника (intro) или общее знакомства; число вопросов считается
        # из items, а не пишется словами: «13 вопросов» в тексте устарело через день (27.09).
        intro = i18n.pick(instrument["intro"]) if instrument.get("intro") else i18n.t("onboarding.intro", n=total)
        lead = intro if n_done == 0 else i18n.t("onboarding.reply.resumed")
        header = f"{lead}\n\n📋 ({n_done + 1}/{total})\n\n" if lead else f"📋 ({n_done + 1}/{total})\n\n"
    else:
        header = (
            f"📋 {fmt_instrument_name(instrument)} ({n_done + 1}/{total})\n"
            + i18n.t("assessment.reply.period", period=fmt_label(
                instrument.get('recall_period', 'past_week'), "assessment.period")) + "\n\n"
        )
    keyboard = _build_keyboard(instrument, iid)
    return (header + text + i18n.t("assessment.help.silence"), keyboard, sid)


def answer(session_id, item_id, value):
    """Сохраняет ответ и возвращает следующий вопрос или финал.

    Returns: (text, keyboard, done) где done=True если опрос завершён.
    """
    sess = db.get_assessment_session(session_id)
    if not sess:
        return (notify.fault(f"assessment_dialog: session missing session_id={session_id}"), None, True)
    if sess.get("status") != "in_progress":
        return (i18n.t("cards.assessment.session_completed"), None, True)

    instrument = _load_instrument_by_source_id(sess["instrument_id"])
    if not instrument:
        return (notify.fault(f"assessment_dialog.answer: instrument missing session_id={session_id} instrument_id={sess['instrument_id']}"), None, True)

    answers = json.loads(sess.get("answers_json") or "{}")
    item = _item(instrument, item_id) or {"id": item_id}
    resolved, shown = _resolve(item, value)
    if resolved is None:
        # Неразобранный ответ — не записываем и переспрашиваем тот же пункт, с причиной.
        return (i18n.t("assessment.error.invalid_answer", shown=shown, question=i18n.pick(item.get("text", ""))),
                _build_keyboard(instrument, item_id), False)
    answers[item_id] = resolved
    db.update_assessment_session(session_id, answers_json=json.dumps(answers, ensure_ascii=False))
    _apply_sink(instrument, item, resolved)
    ack = ""
    if instrument.get("sink") == "profile":  # видимый результат шага (Diátaxis) — он же проверка
        marks = {"__skipped__": "onboarding.reply.skipped", "__kept__": "onboarding.reply.kept"}
        key = marks.get(resolved) if isinstance(resolved, str) else None
        ack = i18n.t(key) if key else i18n.t("onboarding.reply.recorded", shown=shown)

    iid, text = _next_unanswered(instrument, answers)
    if iid is None:
        text_done, _, _ = finalize(session_id)
        return (ack + text_done, None, True)

    total = len(instrument.get("items") or [])
    n_done = len(answers)
    header = f"📋 ({n_done + 1}/{total})\n\n"
    keyboard = _build_keyboard(instrument, iid)
    return (ack + header + text, keyboard, False)


def get_active(chat_id):
    """Для bot routing: есть ли активная assessment-сессия у этого chat_id."""
    return db.get_active_assessment_session(chat_id)


def finalize(session_id):
    """Сохраняет JSON в data/assessments/, импортирует в lab_results, закрывает task."""
    sess = db.get_assessment_session(session_id)
    if not sess:
        return (notify.fault(f"assessment_dialog: session missing session_id={session_id}"), None, True)
    instrument_id = sess["instrument_id"]
    from assessment_importer import assessments_dir, instrument_source_id
    source_id = instrument_source_id(instrument_id)
    adir = assessments_dir()

    answers = json.loads(sess.get("answers_json") or "{}")
    ins = _load_instrument_by_source_id(instrument_id) or {}
    if ins.get("sink") == "profile":
        return _finalize_profile_sink(sess, ins, answers)
    today = get_today().isoformat()
    fname = f"{source_id}_{today}_session{session_id}.json"
    out_path = adir / fname
    payload = {
        "instrument": instrument_id,
        "instrument_id": instrument_id,
        "wording_version_hash": sess.get("wording_version_hash", ""),
        "assessment_session_id": session_id,
        "task_id": sess.get("task_id"),
        "chat_id": sess.get("chat_id"),
        "assessment_date": today,
        "started_at": sess.get("started_at"),
        "completed_at": get_utcnow().isoformat(timespec="seconds"),
        "raw_responses": answers,
    }
    try:
        adir.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        log.info(f"saved {out_path.name}")
    except Exception as e:
        log.error(f"save JSON failed: {e}")
        return (notify.fault(f"assessment_dialog.finalize: save session_id={session_id}: {type(e).__name__}: {e}",
                             person_key="cards.assessment.answers_not_saved"), None, True)

    # Импорт в lab_results. Не посчиталось — сессия помечается import_failed, задача остаётся
    # открытой, чинит инженерный датчик (check_assessments_freshness), а не человек (§13):
    # его ответы целы в файле, повторно заполнять ему нечего.
    import_error = "import returned false"
    try:
        from assessment_importer import import_file
        imported = import_file(out_path)
    except Exception as e:
        log.error(f"import failed: {e}")
        import_error = f"{type(e).__name__}: {e}"
        imported = False
    if not imported:
        try:
            db.update_assessment_session(session_id, status="import_failed",
                                         completed_at=get_utcnow().isoformat(timespec="seconds"))
        except Exception as e:
            log.warning(f"mark import_failed failed: {e}")
        return (notify.fault(f"assessment_dialog.finalize: scoring session_id={session_id} source_id={source_id}: {import_error}",
                             person_key="cards.assessment.scoring_failed", count=len(answers),
                             name=fmt_instrument_name(ins)),
                None, True)

    # Закрытие сессии
    try:
        db.update_assessment_session(
            session_id,
            status="completed",
            completed_at=get_utcnow().isoformat(timespec="seconds"),
        )
    except Exception as e:
        log.warning(f"close session failed: {e}")

    # Закрытие связанной task
    if sess.get("task_id"):
        try:
            db.resolve_task(
                int(sess["task_id"]),
                resolved_text=f"Заполнен опросник {source_id}",
                status="completed",
            )
        except Exception as e:
            log.warning(f"resolve task failed: {e}")

    n = len(answers)
    return (
        i18n.t("cards.assessment.completed", count=n,
               name=fmt_instrument_name(ins),
               days=fmt_count(int(ins.get('cadence_days', 90)), "days")),
        None,
        True,
    )


def _finalize_profile_sink(sess: dict, instrument: dict, answers: dict):
    """Знакомство: ответы уже записаны по местам (_apply_sink) — здесь только закрытие.
    НЕ пишет JSON в assessments и НЕ импортирует в lab_results: «рост 175» не балл опросника,
    строкой анализа он поехал бы в тренды и в контекст врача (ключевой момент плана)."""
    try:
        db.update_assessment_session(sess["id"], status="completed",
                                     completed_at=get_utcnow().isoformat(timespec="seconds"))
    except Exception as e:
        log.warning(f"close session failed: {e}")
    if sess.get("task_id"):
        try:
            db.resolve_task(int(sess["task_id"]), resolved_text=f"Пройдено: {i18n.pick(instrument.get('name', ''), 'ru')}",
                            status="completed")
        except Exception as e:
            log.warning(f"resolve task failed: {e}")
    return (summary_text(instrument, answers), None, True)


def summary_text(instrument: dict, answers: dict) -> str:
    """Карточка «что я теперь знаю» — читается ИЗ МЕСТ, куда легли ответы, а не из сессии:
    если запись не дошла, человек увидит пропуск здесь, а не через месяц в отчёте."""
    import config_db
    import profile_db
    prof = profile_db.get_patient_profile()
    lang = i18n.lang_of(prof)
    labels = {i.get("field"): i for i in instrument.get("items") or []
              if i.get("field") and i.get("field") != i18n.FIELD}
    out = [i18n.t("onboarding.summary.heading", lang)]
    for field, item in labels.items():
        v = prof.get(field)
        for opt in item.get("options") or []:
            if str(opt.get("value")) == str(v):
                v = i18n.pick(opt.get("label"), lang)
        out.append(i18n.t("onboarding.summary.field", lang,
                          label=i18n.pick(item["text"], lang).split("?")[0],
                          value=v if v not in (None, "") else "—"))
    home = config_db.get_config("location.home_lat", None) is not None
    out.append(i18n.t("onboarding.summary.home", lang, status=i18n.t(
        "onboarding.summary.home_set" if home else "onboarding.summary.home_not_set", lang)))
    bt = config_db.get_config("schedule.morning_brief", None)
    if isinstance(bt, dict):
        out.append(i18n.t("onboarding.summary.brief_time", lang,
                          hour=f"{int(bt.get('hour', 0)):02d}", minute=f"{int(bt.get('minute', 0)):02d}"))
    n_prob = len(_lines(answers.get("health", ""))) if answers.get("health") not in (None, "__skipped__") else 0
    if n_prob:
        out.append(i18n.t("onboarding.summary.problems", lang, n=n_prob))
    # Пропуск вопроса не равен отрицательному ответу: неизвестное состояние
    # нельзя показывать как отсутствие. История и ответ анкеты — разные источники.
    if answers.get("meds") == "__skipped__":
        out.append(i18n.t("onboarding.summary.medications_skipped", lang))
    else:
        n_med = len(_lines(answers.get("meds", "")))
        out.append(i18n.t("onboarding.summary.medications", lang,
                          n=n_med or i18n.t("onboarding.summary.none", lang)))
    if answers.get("health") == "__skipped__":
        out.append(i18n.t("onboarding.summary.problems_skipped", lang))
    # Просьба о документах (решение владельца 24.09: «просить геном, анализы и любые заключения
    # врачей за последние 3 года»). Отдельной строкой в итоге, а не правкой вопроса о здоровье —
    # его формулировка принадлежит владельцу. Перечень форматов — из дома разборщика генома.
    import genome_intake
    out.append("")
    out.append(i18n.t("onboarding.summary.documents_heading", lang))
    out.append(i18n.t("onboarding.summary.medical_documents", lang))
    providers = genome_intake.PROVIDERS
    formats = i18n.t("onboarding.summary.genome_formats", lang,
                     head=", ".join(providers[:-1]), last=providers[-1])
    out.append(i18n.t("onboarding.summary.genome_file", lang, formats=formats))
    out.append("")
    out.append(i18n.t("onboarding.summary.edit", lang))
    return "\n".join(out)


def abandon(session_id, reason=""):
    """Отменить активную сессию (например, по /skip)."""
    try:
        db.update_assessment_session(
            session_id,
            status="abandoned",
            completed_at=get_utcnow().isoformat(timespec="seconds"),
        )
        log.info(f"abandoned session #{session_id}: {reason}")
        return True
    except Exception as e:
        log.warning(f"abandon failed: {e}")
        return False


# ── CLI smoke ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--task-id", type=int, help="task_id для start")
    ap.add_argument("--chat-id", type=int, default=999999)
    args = ap.parse_args()
    if args.task_id:
        text, kb, sid = start(args.chat_id, args.task_id)
        print("text:")
        print(text)
        print("\nkeyboard:")
        for row in (kb or []):
            for label, cb in row:
                print(f"  [{label}] -> {cb}")
        print(f"\nsession_id: {sid}")
