#!/usr/bin/env python3.11
"""
triage_agent.py — автономный разбор integrity-предупреждений.

Вызывается из ночного цикла (run_triage_guarded); до 13.07 — из morning_report.py, модуль удалён 30.09.
Логика:
  - Читает logs/integrity_latest.json
  - Авто-фиксит то, что можно (GP отчёт, genome_update_agent)
  - Возвращает вопросы, требующие решения пользователя
  - Технические находки остаются в артефакте для ночного цикла

Вывод:
  {"auto_fixed": ["...", ...], "needs_user": ["...", ...]}
"""
import json
import i18n
import notify
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

import sys as _sys
_sys.path.insert(0, str(Path(__file__).parent))
from _time_inject import get_today

SCRIPT_DIR = Path(__file__).parent
# Путь лога — модульная КОНСТАНТА, чтобы тест мог увести запись в tmp. Инцидент 2026-07-26:
# юнит-тест `test_run_triage_guarded_alerts_on_crash` реально дёргает guard, тот пишет FATAL в
# ПРОДОВЫЙ triage.log — и лог показывал свежие записи каждый день, пока настоящая доставка была
# мертва 13 дней. Диагност (я) едва не принял следы теста за жизнь канала.
LOG_FILE = SCRIPT_DIR / "logs" / "triage.log"
# Квитанция доставки — КАНАЛ, а не факт вызова. До 2026-07-29 `_send_telegram` выбрасывал
# возврат `notify.notify()`, и строка «USER QUESTIONS sent» писалась одинаково при удачной
# отправке и при легших обоих каналах. То есть главный носитель warn отчитывался об успехе,
# не спросив, случился ли он, — ровно тот класс, против которого построен датчик рельсы.
# Маркер `triage_done_*.flag` тут не помощник: он доказывает, что триаж ОТРАБОТАЛ.
#
# Путь разрешается ПРИ ВЫЗОВЕ, а не при импорте (2026-08-07). Модульная константа была
# посчитана из настоящего SCRIPT_DIR в момент import, поэтому подмена `SCRIPT_DIR` в
# фикстуре её НЕ уводила: `tests/integration/test_uc_d_04_triage.py` патчил SCRIPT_DIR и
# LOG_FILE, а квитанцию писал в БОЕВУЮ `logs/` — каждый ночной прогон набора (00:03)
# затирал её значением `{"via": null, "questions": 1}`. Цена измерена: (1) датчик рельсы
# каждое утро ловил «квитанция отстала от маркера» — артефакт теста, не отказ канала,
# и warn ехал владельцу 3+ дня подряд; (2) ХУЖЕ — ветка FAIL `via != "none"` стала
# недостижимой: настоящее `"none"` от легших каналов затиралось тестовым `null` в ту же
# ночь. Сторож последнего метра доставки был обезврежен своим же тестом (§20: тест,
# дотянувшийся до боевого артефакта, — не «реалистичный», а сломанный).
def _logs_dir() -> Path:
    """Каталог артефактов триажа. Владелец — logs/ репозитория (как было). Человек-тенант
    (28.09, решение владельца «партнёру — свой утренний разбор»): run_checks.sh задаёт
    HEALTH_TRIAGE_LOGS=<данные тенанта>/logs — там его вердикт ночной проверки, его маркер
    дня и его квитанция. Иначе партнёрский триаж затёр бы маркер и квитанцию владельца,
    а датчик рельсы владельца судил бы чужой прогон."""
    import os
    d = os.environ.get("HEALTH_TRIAGE_LOGS")
    return Path(d) if d else SCRIPT_DIR / "logs"


def _receipt_path() -> Path:
    """Квитанция доставки. Резолвится при вызове — та же форма, что
    `owner_nag._receipt_path` (ступень 4: дом для этого класса уже был построен)."""
    return _logs_dir() / "triage_delivery_latest.json"


def _log(msg: str):
    import os
    log_file = Path(os.environ["HEALTH_TRIAGE_LOGS"]) / "triage.log" \
        if os.environ.get("HEALTH_TRIAGE_LOGS") else LOG_FILE
    log_file.parent.mkdir(exist_ok=True)
    with open(log_file, "a") as f:
        f.write(f"{get_today()} {msg}\n")


# `_send_telegram` снят 28.09 (нить triage-questions): вопросы человеку уходят в дом
# вопросов (tasks) и доставляются outbox'ом с обратным адресом; прямой отправки из
# триажа больше нет. Старый путь заглушён в коде, а не просто перестал зваться.


# Каналы, считающиеся ДОКАЗАННОЙ доставкой. 'none' — оба легли; None/'' — канал не
# назван (квитанцию писал не триаж). Ни то, ни другое доставкой не является.
PROVEN_CHANNELS = ("telegram", "fallback")


def last_proven_of(rec: dict) -> str | None:
    """День последней ДОКАЗАННОЙ доставки по квитанции — или None, если такого дня нет.

    ЕДИНСТВЕННЫЙ дом правила: его зовёт и писатель (перенос значения вперёд), и датчик
    (бюджет молчания канала). Иначе у одного факта завелось бы два толкования — ровно
    та болезнь, которую эта правка и лечит (§15, §18).

    Обратная совместимость: у квитанций до 2026-09-07 поля `last_proven` нет, но сама
    квитанция писалась ТОЛЬКО при отправке — значит её `date` и есть день доказанной
    доставки, если канал в ней назван. Без этой ветки первое же утро после деплоя дало
    бы ложный FAIL «канал не подтверждён НИКОГДА»."""
    if not isinstance(rec, dict):
        return None
    if rec.get("last_proven"):
        return rec["last_proven"]
    if rec.get("via") in PROVEN_CHANNELS or rec.get("via_digest") in PROVEN_CHANNELS:
        return rec.get("date")
    return None


def _write_run_receipt(n_questions: int, via, n_digest: int, via_digest, sent: bool,
                       to_outbox: int = 0) -> None:
    """Квитанция ПРОГОНА (не факта отправки). Пишется КАЖДЫЙ прогон — в том числе когда
    доставлять было нечего, и непосредственно ПЕРЕД `done_marker.touch()`.

    Дефект, который это чинит (2026-09-07). Квитанция писалась только внутри
    `if needs_user`, а маркер `triage_done_*.flag` ставился всегда: два дома одного
    факта «триаж отработал в день D» с РАЗНЫМИ условиями записи. В тихий день (всё
    уехало в понедельничный дайджест или в mute) они расходились, и наутро датчик
    рельсы рапортовал «квитанция отстала от маркера». Замер по logs/triage.log: 2
    тихих дня из 2 с момента ввода квитанции 29.07 дали ложный варн владельцу.
    Латентен с 29.07, разбужен разделением каденции 31.08.

    Хуже шума: в датчике стояло `assert stale_receipt or via not in (...)` — пока
    квитанция «отставала», ветка FAIL «ДОСТАВКА НЕ ДОКАЗАНА» не исполнялась вовсе.
    То есть в день после КАЖДОГО тихого дня сторож последнего метра был выключен.
    Воспроизведено 2026-09-07: то же состояние с via='none' FAIL не дало, датчик
    вернул delivered_via='none' и промолчал.

    Форма взята у `owner_nag._write_receipt`: колокол решил ту же задачу верно —
    «всегда пишет квитанцию пульса, даже когда молчит, иначе „не звонил“ и „сдох“ не
    различить» (§14). Здесь тот же принцип: «нечего было доставлять» и «канал не
    отчитался» обязаны быть РАЗНЫМИ состояниями артефакта, а не одним.

    Производных полей нет сознательно: «было что доставлять» = questions+digest > 0,
    «доставка доказана» = канал в PROVEN_CHANNELS — читатель выводит их сам. Хранить
    их значило бы завести второй дом тому же факту. Невыводимо из одного прогона
    ровно одно — `last_proven`, поэтому оно и хранится.

    `sent=False` (прогон без --send: ручной осмотр, тест) честно помечается: такой
    прогон доказывает, что рельса жива, и НЕ доказывает ничего про канал —
    `last_proven` он не двигает. Оба боевых входа (launchd 08:00 и цепочка
    run_checks.sh) идут с --send; если однажды заведут боевой путь без него, канал
    перестанет подтверждаться и после первого пропущенного понедельничного дайджеста это
    станет громким FAIL, а не тишиной (integrity_tests.check_triage_delivery_liveness, 23.09)."""
    path = _receipt_path()
    prev = {}
    try:
        if path.exists():
            prev = json.loads(path.read_text(encoding="utf-8")) or {}
    except (ValueError, OSError) as exc:
        # Не тихо: потеря prev обнуляет счёт бюджета молчания канала.
        _log(f"WARN: прежняя квитанция не прочитана ({exc!r}) — last_proven начнётся заново")
    today = str(get_today())
    proven_now = sent and (via in PROVEN_CHANNELS or via_digest in PROVEN_CHANNELS)
    rec = {"date": today, "sent": bool(sent),
           "questions": n_questions, "via": via,
           "digest": n_digest, "via_digest": via_digest,
           # вопросы в дом вопросов: доставку доказывает outbox (tg_message_id), не триаж
           "to_outbox": to_outbox,
           "last_proven": today if proven_now else last_proven_of(prev)}
    try:
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        _log(f"WARN: квитанция прогона не записана: {exc!r}")


# Явный mute-список: каждый пункт — с обоснованием, покрыт тестом
# test_triage_delivery. Остальное доставляется человеку или оператору по назначению.
MUTE_WARN_SUBSTRINGS = (
    "нет steps",                # данные шага уже пропущены прошлым — постфактум не actionable
    "активных протокол",        # «нет активных протоколов» — ожидаемое состояние (решение владельца 2026-06-29)
    "без активных эксперимент",  # «N гипотез без активных экспериментов» — эксперименты свёрнуты намеренно (BL-EXP-1); гипотезы разрешаются консилиумом, не экспериментами (2026-06-30)
)


# ── Класс предупреждения: ЧТО оно просит у читателя (2026-08-31) ─────────────
# Решение владельца «починить корзину по смыслу»: до этого дня всё, что не в mute,
# ехало одним списком под заголовком «нужно твоё решение» — уже решённое, инженерная
# механика и месяцами неактонируемое рядом с настоящим медицинским выбором. Одна
# корзина на четыре смысла тренирует пропускать все четыре (§13, banner-blindness):
# замер 2026-08-31 — список из 10 строк не читался 30+ дней, а к моменту чтения
# одна строка была уже неправдой (proposals закрыты накануне).
#
#   decide   — единственный оракул человек (медицинское, методологическое, канон):
#              ежедневно, как раньше. УМОЛЧАНИЕ: неизвестное едет сюда (mute-list,
#              не whitelist — detect_with_delivery).
#   fix      — инженерный worklist, оракул не владелец: недельный дайджест.
#   standing — датированное состояние без действия сейчас: недельный дайджест.
#
# ДОМ КЛАССА ПЕРЕЕХАЛ (2026-09-13) в `finding_identity`: класс и ИМЯ находки — один
# вопрос («что это за находка»), и держать их в разных модулях значит завести два ответа
# на один вопрос. Здесь копии таблицы больше НЕТ — старый дом заглушён, а не просто
# перестал зваться (иначе через месяц появится третья редакция того же списка).
import finding_identity

DIGEST_WEEKDAY = 0   # понедельник: день, когда fix/standing доезжают до человека


def warn_class(name: str, msg: str = "") -> str:
    """Класс находки. Делегирует единственному дому; своей логики здесь не осталось.
    msg — деталь: «не судимо в контейнере» делает её standing (30.09, desk-guard)."""
    return finding_identity.class_of_warning(name, msg)


def split_by_cadence(warnings, today):
    """Чистая: [[name, msg], ...] → (daily, digest). daily — decide-класс всегда;
    digest — fix/standing, и только в DIGEST_WEEKDAY они уезжают человеку
    (в прочие дни возвращаются пустым списком; вызывающий логирует отложенные)."""
    daily, deferred = [], []
    for name, msg in warnings:
        (daily if warn_class(name, msg) == "decide" else deferred).append([name, msg])
    digest = deferred if today.weekday() == DIGEST_WEEKDAY else []
    return daily, digest, deferred


def classify_warnings(warnings, log=None, *, person_only=False):
    """Чистая функция: warnings [[name, msg], ...] → строки, без доставки.

    person_only оставляет только вопросы человеку; технические пункты
    остаются в integrity_latest.json для ночного цикла.
    """
    out = []
    for name, msg in warnings:
        low = name.lower()
        if any(m in low for m in MUTE_WARN_SUBSTRINGS):
            if log:
                log(f"MUTED warn: {name}")
            continue
        if re.search(r"\bанализы(?:\s+(?:устарели|требуют обновления)|\s+\d+д\b)", low):
            # Тег тенанта обязан пережить переформулировку. Сборка только из
            # подстроки name может потерять тег и оставить пустой msg.
            # Тогда чужой алерт выглядит как свой: потеря субъекта меняет
            # смысл сообщения, а не только его оформление.
            tag = re.match(r"\s*\[([^\]]+)\]", name)
            who = f" [{tag.group(1)}]" if tag else ""
            m = re.search(r"(\d+)д", name)
            from _fmt_helpers import fmt_count
            days = i18n.t("triage.question.age", days=fmt_count(int(m.group(1)), "days")) if m else ""
            tail = f" — {msg}" if msg else ""
            out.append(i18n.t("triage.question.labs", who=who, days=days, tail=tail))
        elif "период" in low and ("клинич" in low or "истёк" in (msg or "").lower()):
            out.append(i18n.t("triage.question.period", message=msg))
        elif not person_only:
            detail = f": {msg}" if msg else ""
            out.append(f"⚠️ {name}{detail}")

    return out


def person_questions(warnings, own_tag: str, log=None) -> list[dict]:
    """Чистая: находки, на которые может ответить ТОЛЬКО человек, → вопросы в дом вопросов.

    До 28.09 они уезжали строкой в утреннем сообщении («Анализы (3 дня). Когда
    планируешь?») — без обратного адреса: ответ человека никуда не попадал
    (patient_answer_channel.single_home_of_questions — дом вопросов один, таблица tasks).
    Теперь каждый — вопрос-задача: outbox шлёт его с ForceReply, ответ возвращается
    врачу и в память.

    own_tag — тенант этой базы. Находка с чужим тегом ([health_partner] у владельца)
    человеку этой базы НЕ задаётся: артефакт ночной проверки один на двоих, и чужая
    лаб-каденция приходила владельцу как его собственный вопрос. Ночной цикл читает её
    из того же артефакта. Находка без тега — своя (одно-тенантная установка).

    fingerprint держит один открытый вопрос на эпизод: для анализов — по дате последней
    сдачи (новая сдача → новый эпизод), для периода — по именам периодов."""
    out = []
    for name, msg in warnings:
        low = name.lower()
        tag = re.match(r"\s*\[([^\]]+)\]", name)
        if tag and tag.group(1) != own_tag:
            if log:
                log(f"SKIP чужой тенант для вопроса человеку: {name}")
            continue
        m_lab = re.search(r"\bанализы(?:\s+(?:устарели|требуют обновления)|\s+\d+д\b)", low)
        if m_lab:
            from _fmt_helpers import fmt_count
            m = re.search(r"(\d+)д", name)
            last = re.search(r"\d{4}-\d{2}-\d{2}", msg or "")
            date_s = i18n.t("triage.ask.labs_date", date=last.group(0)) if last else ""
            out.append({
                "kind": "labs",
                "fingerprint": f"question:triage:labs:{last.group(0) if last else 'due'}",
                "content": (i18n.t("triage.ask.labs", days=fmt_count(int(m.group(1)), "days"), last=date_s)
                            if m else i18n.t("triage.ask.labs_due", last=date_s)),
            })
        elif "период" in low and ("клинич" in low or "истёк" in (msg or "").lower()):
            names = (msg or "").split(":", 1)[-1].strip()
            out.append({
                "kind": "period",
                "fingerprint": f"question:triage:period:{names}",
                "content": i18n.t("triage.ask.period", names=names),
            })
    return out


def _own_tag() -> str:
    """Тенант этой базы — тот же тег, которым integrity метит находки (_iter_tenant_ro:
    имя каталога над data/)."""
    import health_db as _db
    return Path(_db.DB_PATH).parent.parent.name


def _ask(questions, log=None) -> int:
    """Кладёт вопросы в дом вопросов (tasks, type=question). Вопрос об анализах не
    задаётся, если уже открыта задача сдать анализ: она несёт ту же просьбу, и третье
    сообщение про одно дело человек читает как три дела (холодное чтение 28.09:
    ферритин приходил задачей, вопросом и строкой утреннего разбора)."""
    import health_db as db
    if not questions:
        return 0
    with db.get_conn() as conn:
        lab_task_open = conn.execute(
            "SELECT 1 FROM tasks WHERE type='lab_test' AND status IN ('open','snoozed') LIMIT 1"
        ).fetchone() is not None
        # Открытый или отложенный вопрос эпизода не дублируем (save_task гасит только
        # 'open'). Отвеченный — перезадаём ТОЛЬКО по оси времени памяти
        # (task_agent.should_ask_again, patient_answer_channel.reask_uses_memory_axis):
        # ответ «сдам в ноябре» — transient и стареет, «не буду сдавать» — standing.
        # Своей оси срока здесь не заводим — иначе отвеченный вчера вопрос либо
        # рождался бы каждое утро, либо не вернулся бы никогда.
        rows = conn.execute("SELECT fingerprint, status FROM tasks "
                            "WHERE fingerprint LIKE 'question:triage:%'").fetchall()
    pending = {r[0] for r in rows if r[1] in ("open", "snoozed")}
    answered = {r[0] for r in rows} - pending
    n = 0
    for q in questions:
        if q["fingerprint"] in pending:
            continue
        if q["fingerprint"] in answered:
            import task_agent
            again, why = task_agent.should_ask_again(q["fingerprint"])
            if not again:
                if log:
                    log(f"SKIP {q['fingerprint']}: {why}")
                continue
        if q["kind"] == "labs" and lab_task_open:
            if log:
                log(f"SKIP вопрос об анализах: открыта задача сдать анализ ({q['fingerprint']})")
            continue
        tid = db.save_task(source="triage", type_="question", content=q["content"],
                           priority="medium", source_date=str(get_today()),
                           reason=i18n.t("triage.ask.reason"), fingerprint=q["fingerprint"])
        if tid:
            n += 1
            if log:
                log(f"QUESTION #{tid} в дом вопросов: {q['fingerprint']}")
    return n


def latest_verdict(require_today: bool = False) -> tuple[dict, str]:
    """Вердикт монитора из logs/integrity_latest.json → (данные, причина_отказа).

    Причина непустая ⟹ данным верить нельзя, и она ГОДИТСЯ ДЛЯ ПОКАЗА человеку:
    потребитель, который решает блокировать или нет, обязан уметь сказать, почему
    он НЕ заблокировал, — иначе «не заблокировал» неотличимо от «не проверял».

    require_today=True — граница устаревания (Tanenbaum 7.2.1): реплика годна
    только внутри объявленного окна. Монитор пишет артефакт в 07:50; при его
    отказе на месте остаётся ВЧЕРАШНИЙ файл (так решено в run_checks.sh: свежий
    мусор хуже читаемого вчерашнего). Читатель без границы принял бы вчерашнее
    «чисто» за сегодняшнее — то есть молчал бы ровно тогда, когда монитор умер.
    """
    path = _logs_dir() / "integrity_latest.json"
    if not path.exists():
        return {}, "артефакт монитора не найден"
    try:
        data = json.loads(path.read_text())
    except Exception as e:                      # noqa: BLE001
        return {}, f"артефакт монитора не разбирается: {type(e).__name__}"
    # C-19 (2026-08-12): артефакт один на репозиторий, читателей — два тенанта.
    # БД = тенант; вердикт годен только читателю ТОЙ ЖЕ базы, которую судил прогон.
    # Ключ записи и ключ чтения — один и тот же DB_PATH (писатель:
    # integrity_tests --json). Отсутствие поля = до-C-19 формат: провенанс
    # неизвестен, и тихо считать его «своим» — ровно тот тихий дефолт, который
    # multitenancy запрещает (tenant_data_loud_default).
    import health_db as _db
    art_db = str(data.get("db_path") or "")
    if art_db != str(_db.DB_PATH):
        return {}, (f"вердикт монитора о другой базе "
                    f"({art_db or 'без провенанса, до-C-19 формат'}) — данные этого "
                    f"тенанта ({_db.DB_PATH}) он не судил")
    if require_today and str(data.get("date")) != str(get_today()):
        return {}, f"вердикт от {data.get('date') or 'без даты'}, а не за сегодня"
    return data, ""


def run_triage(send_user_questions: bool = True, require_today: bool = False) -> dict:
    """
    Запускает авто-фиксы, возвращает {"auto_fixed": [...], "needs_user": [...]}.
    send_user_questions=True — кладёт вопросы человеку в дом вопросов (tasks); False — только считает.
    Идемпотентен: повторный вызов в тот же день — no-op.

    require_today (2026-09-01): боевой вход требует СЕГОДНЯШНИЙ артефакт и при
    вчерашнем выходит БЕЗ done-маркера. Замер 01.09 по logs/run_checks.log: монитор
    стартует 07:50 и пишет артефакт в 08:25–08:27 (полный pytest внутри), триаж
    стартовал в 08:00 — и каждый день доставлял владельцу ВЧЕРАШНИЕ находки под
    сегодняшней датой (01.09 в 08:00 ушёл список с «[сигнал от 2026-08-31]», в
    котором шесть из семи пунктов были закрыты накануне). Прежний довод «триаж
    сознательно берёт и вчерашний» держался, пока артефакт успевал к 08:00.
    Теперь доставка идёт цепочкой из run_checks сразу после записи артефакта;
    ранний запуск по плисту не ставит маркер, чтобы не отобрать день у цепочки.
    """
    done_marker = _logs_dir() / f"triage_done_{get_today()}.flag"
    if done_marker.exists():
        _log("INFO: triage уже выполнен сегодня — пропуск")
        return {"auto_fixed": [], "needs_user": []}

    data, why = latest_verdict(require_today=require_today)
    if why:
        _log(f"INFO: {why} — пропуск (маркер не ставится)")
        return {"auto_fixed": [], "needs_user": []}

    failures = data.get("failures", [])   # [[name, msg], ...]
    warnings = data.get("warnings", [])   # [[name, msg], ...]

    auto_fixed = []
    needs_user = []

    failure_names = {f[0] for f in failures}

    # ── Авто-фикс: GP отчёт отсутствует или слишком короткий ────────────────
    gp_broken = any("GP-отчёт" in n for n in failure_names)
    if gp_broken:
        log_path = SCRIPT_DIR / "logs" / "gp_agent_triage.log"
        _log("AUTO: gp_agent.py weekly — запуск")
        try:
            subprocess.Popen(
                [sys.executable, str(SCRIPT_DIR / "gp_agent.py"), "weekly"],
                stdout=open(log_path, "a"), stderr=subprocess.STDOUT,
                cwd=str(SCRIPT_DIR)
            )
            auto_fixed.append("GP отчёт: генерация запущена (готово ~2 мин)")
            _log("AUTO: gp_agent.py weekly — запущен")
        except Exception as e:
            _log(f"AUTO ERROR: gp_agent: {e}")

    # ── genome_update: НЕ авто-фиксим (CLAUDE.md §13: Diagnose-don't-Repair) ─
    # До 2026-06-29 здесь был subprocess.Popen genome_update_agent с пометкой
    # auto_fixed. Дефект: morning_report короткоживущий → launchd реапил группу →
    # агент SIGKILL на ClinVar-fetch до записи в genome_update_log (таблица пуста
    # с апреля). 16+ дней фейк-зелёного «вылечено». Теперь staleness-варн просто
    # читается ночным циклом из integrity_latest.json. Запуск самого
    # обновления — собственным джобом/вручную, не из секундного триажа.

    # «детект без доставки» недопустим: доставка технических failures/warnings —
    # ночной цикл читает тот же integrity_latest.json (решение владельца 28.09.2026).
    # Триаж оставляет человеку только его вопросы, включая личную часть дайджеста.
    # Вопросы человеку (28.09, нить triage-questions): не строкой в утреннем сообщении, а
    # в ДОМ ВОПРОСОВ — там у них обратный адрес (outbox + ForceReply), и ответ доходит до
    # врача. Каденция им не нужна: повтор гасит fingerprint эпизода, поэтому берём находки
    # и дневные, и отложенные. Прямой отправки вопросов в Telegram больше нет — квитанция
    # несёт questions=0 и число вопросов, положенных в дом (to_outbox).
    own_tag = _own_tag()
    daily_w, digest_w, deferred_w = split_by_cadence(warnings, get_today())
    asked = person_questions(daily_w + deferred_w, own_tag, log=_log)
    needs_user.extend(q["content"] for q in asked)
    to_outbox = _ask(asked, log=_log) if send_user_questions else 0
    digest = []

    via = via_d = None

    # Квитанция ПЕРЕД маркером и по тому же условию, что маркер (2026-09-07). Порядок
    # выбран так, чтобы отказ был громким: упадёт запись квитанции — маркера не будет,
    # и датчик рельсы закричит. Обратный порядок дал бы маркер без квитанции, то есть
    # ровно то расхождение, которое чиним. Дайджест теперь тоже отчитывается каналом:
    # до сегодня `via_d` никуда не записывался, и понедельник, в который уехал ТОЛЬКО
    # дайджест, оставлял квитанцию позади при состоявшейся доставке.
    _write_run_receipt(0, via, len(digest), via_d, send_user_questions, to_outbox=to_outbox)
    done_marker.touch()
    _log(f"DONE: auto_fixed={auto_fixed}, needs_user={needs_user}, digest={digest}")
    return {"auto_fixed": auto_fixed, "needs_user": needs_user, "digest": digest}


def run_triage_guarded(send_user_questions: bool = True, require_today: bool = False) -> dict:
    """run_triage в обёртке: собственное падение попадает в журнал сбоев.

    Вызывается из morning_report вместо голого run_triage. При исключении —
    лог + notify.fault для ночного цикла, возвращает пустой результат.
    Вынесено из morning_report.__main__ ради тестируемости (2026-06-29).
    """
    try:
        return run_triage(send_user_questions=send_user_questions, require_today=require_today)
    except Exception as e:
        import traceback
        _log(f"FATAL: triage упал: {e!r}\n{traceback.format_exc()}")
        try:
            import notify
            notify.fault(f"triage.run_triage: {type(e).__name__}: {e}", person_key=None)
        except Exception:  # silent-ok: notify недоступен — основной отказ уже в triage.log
            pass
        return {"auto_fixed": [], "needs_user": []}


if __name__ == "__main__":
    # guarded, а не голый run_triage: это ЕДИНСТВЕННЫЙ канал warn-уровня, его падение
    # не должно быть тихим. Раньше __main__ звал голую версию, а guard был только в
    # morning_report — и когда morning_report вывели из расписания (2026-07-13), вместе
    # с ним молча уехала вся доставка. См. docs/explanation/warn_delivery_rail.md.
    # Боевой вход: только сегодняшний артефакт (см. run_triage.require_today).
    result = run_triage_guarded(send_user_questions="--send" in sys.argv, require_today=True)
    print(json.dumps(result, ensure_ascii=False, indent=2))
