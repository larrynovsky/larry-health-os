#!/usr/bin/env python3.11
"""
integrity_tests.py — проверка целостности системы Larry Health OS.

Как датчик отсюда доходит до владельца (рельсы доставки, кто кого сторожит и где
рвётся): docs/explanation/sensor_delivery_spine.md — читать ДО того, как
добавлять новый check_*.

Отвечает на вопрос: "Система делает то, для чего создана?"
НЕ проверяет синтаксис или импорты — только поведение данных и пайплайна.

Oracle: см. TESTING_CONTRACTS.md — там декларированы пороги и критерии.

Тестирует:
  1. Свежесть данных Oura    — conit-контракты: oura/apple_health ≤26ч
  2. Полнота метрик           — ключевые поля заполнены (hrv, sleep, steps, bp)
  3. Агентские отчёты        — GP weekly ≤8д, GP monthly ≤35д
  4. Проблемный список       — problem_list заполнен; review_date не просрочен
  5. Задачи                  — task pipeline работает
  6. Checkin pipeline        — чекины фиксируются
  7. Сквозной тест           — данные → контекст для GP
  8. Свежесть источников     — labs, genome_update, GP-расписание
  9. Консистентность         — гипотезы→эксперименты, review_date, periods

Запуск:
  python3.11 integrity_tests.py
  python3.11 integrity_tests.py --warn-only   # exit 0 даже при падениях (для CI)
  python3.11 integrity_tests.py --json        # вывод в JSON для парсинга

Расписание: launchd ежедневно в 07:50 (до отправки утреннего отчёта в 08:00).
FAIL до 08:00 → алерт в Telegram вместо отчёта.

Exit codes: 0 = все PASS, 1 = есть FAIL, 2 = критическая ошибка окружения
"""
# INTENT: self_monitoring — само-мониторинг: система стережёт саму себя.
#          Замысел и инварианты — subsystem_intent.yaml, раздел self_monitoring.

import json
import infra_config  # основная машина — данные установки (private/infra.yaml)
import re
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _time_inject import get_today, get_now, get_utcnow

# ── Конфиг порогов (источник: TESTING_CONTRACTS.md) ──────────────────────────
DATA_FRESHNESS_HOURS      = 26   # oura/apple_health: conit C1
from metrics_db import SOURCE_STALE_DAYS as DATA_FRESHNESS_DAYS  # то же в днях; дом — metrics_db (02.10)
MIN_DAYS_WITH_DATA        = 5    # из последних 7 дней должно быть данных >= N
GP_WEEKLY_MAX_AGE_DAYS    = 8    # GP weekly report не старше N дней
GP_MONTHLY_MAX_AGE_DAYS   = 35   # GP monthly report не старше N дней
GP_REPORT_MAX_AGE_DAYS    = 8    # обратная совместимость → weekly
MIN_ACTIVE_PROBLEMS       = 1    # минимум активных проблем в problem_list
MIN_OPEN_TASKS            = 0    # 0 = только проверяем что таблица читается
LAB_REMINDER_DAYS         = 180  # 6 мес — напоминание пересдать анализы
LAB_ALERT_DAYS            = 270  # 9 мес — тревога: агент работает по stale labs
PROMOTION_BACKLOG_DAYS    = 7    # staging: строка «к авто-промоуту» ждёт > N дней = затор
RETIRED_LAB_WRITER_FENCE  = "2026-07-29"  # дата фенса coordinate_import→lab_results (F-01)
UNIT_REQUIRED_FENCE       = "2026-07-30"  # с этой даты строка канона обязана нести единицу (loinc-name-home, шаг A)
GENOME_UPDATE_ALERT_DAYS  = 30   # genome_update_agent не запускался > N дней
HYPOTHESIS_MAX_AGE_DAYS   = 14   # гипотеза без эксперимента/лога > N дней
REPORT_MIN_CHARS          = 500  # минимальная длина GP-отчёта в chars

# ── Счётчики ──────────────────────────────────────────────────────────────────
PASS = 0
FAIL = 0
WARN = 0
_failures: list[tuple[str, str]] = []
_warnings: list[tuple[str, str]] = []
# Падения из-за ОШИБКИ КОДА (NameError/typo/битый импорт/несовпадение сигнатуры), а НЕ
# провала данных (AssertionError). Код-ошибки валидны даже на устаревшем снапшоте → их можно
# ловить в pre-commit (test_on_studio.sh шаг 5), в отличие от data-FAIL. Закрывает класс:
# сломанный check() глотается check()-обёрткой, pytest зелёный, всплывает лишь в ночном --json.
_code_failures: list[tuple[str, str]] = []
# Падения датчиков, помеченных critical=True. Подмножество _failures. Раньше critical
# делал sys.exit(2) ПОСРЕДИ прогона — остальные 130 датчиков не выполнялись, а артефакт
# не писался вовсе, т.е. «критическое» падение делало монитор МЕНЕЕ информативным.
# Теперь critical помечает: прогон идёт до конца, список уезжает в JSON, а потребитель
# (утренний бриф) сам решает, что делать со свежим вердиктом.
_critical: list[tuple[str, str]] = []
# Метка находки → имя датчика, который её выдал (нить repair-order, 02.10). По имени датчика ночной
# ремонт берёт из project_context/integrity_sensors.json, кого задевает поломка (человек / данные /
# система), и решает, что чинить первым. Метка одна на находку — так её зовёт и ночной цикл.
_sensor_of: dict[str, str] = {}
_current_sensor = ""
WARN_ONLY   = "--warn-only" in sys.argv
JSON_OUTPUT = "--json"      in sys.argv
# МАШИННЫЙ СУДЬЯ (нить machine-judge, 03.10). `--scope machine` — прогон на хосте Studio, не
# привязанный ни к одному тенанту: судит ТОЛЬКО проверки самой машины (host_only / machine).
# До 03.10 их судил ночной прогон партнёра — случайно: уйди партнёр, и снимки MacBook, launchd
# и экспозиция хоста ослепли бы молча. Пишет scripts/machine_check.sh, читает контейнер владельца.
MACHINE_SCOPE = "--scope" in sys.argv and sys.argv[sys.argv.index("--scope") + 1:][:1] == ["machine"]
MACHINE_LABEL = "com.larry.health.machine-check"
MACHINE_RESULT = "machine_integrity_latest.json"
_not_judged_here: list[str] = []


def _host_judged_here() -> bool:
    """Судит ли ЭТОТ прогон проверки машины сам: да — только нативный прогон владельца на хосте
    (установка без контейнера). Контейнер их не видит, прогон тенанта — не хозяин машины; у них
    судья — машинный прогон, а здесь — проверка его квитанции (check_machine_judge_alive)."""
    import plist_env_liveness as _pl
    import secrets_paths as _sp
    return (not _pl.in_container() and _sp.is_owner_data()
            and not _sp.moved_to_container(_sp.owner_root()))


def check(label: str, fn, critical: bool = False, repo_only: bool = False,
          host_only: bool = False, machine: bool = False):
    """repo_only (docker-install, этап 6): предмет проверки — РЕПОЗИТОРИЙ (git, хуки, приватная
    зона), а не работающая система. В контейнере его нет по построению (.dockerignore): там
    проверка говорит «не судимо» вслух, а судит её прогон в мастерской (run_checks на MacBook).

    host_only (machine-judge, 03.10): предмет — сама машина Studio (launchd, снимки MacBook,
    счётчик тенантов). Судит машинный прогон (--scope machine) или нативный прогон владельца;
    остальные прогоны её пропускают молча — их громкость держит check_machine_judge_alive.
    machine: проверка общая — идёт и в обычном прогоне, и в машинном (SEC-датчики хоста)."""
    global PASS, FAIL, _current_sensor
    _current_sensor = getattr(fn, "__name__", "")
    if MACHINE_SCOPE and not (host_only or machine):
        return None
    if host_only and not MACHINE_SCOPE and not _host_judged_here():
        _not_judged_here.append(label)
        return None
    if repo_only:
        import plist_env_liveness
        if plist_env_liveness.in_container():
            import finding_identity as _fi
            warn(label, f"{_fi.NOT_JUDGED_HERE}: предмет — репозиторий (git, приватная зона); "
                        "судит run_checks в мастерской")
            return None
    try:
        result = fn()
        if not JSON_OUTPUT:
            print(f"  ✅ {label}")
        PASS += 1
        return result
    except AssertionError as e:
        if not JSON_OUTPUT:
            print(f"  ❌ {label}: {e}")
        FAIL += 1
        _failures.append((label, str(e)))
        _sensor_of[label] = _current_sensor
        if critical:
            _critical.append((label, str(e)))
        return None
    except Exception as e:
        if not JSON_OUTPUT:
            print(f"  ❌ {label}: {type(e).__name__}: {e}")
        FAIL += 1
        _failures.append((label, f"{type(e).__name__}: {e}"))
        _sensor_of[label] = _current_sensor
        # НЕ-AssertionError = ошибка КОДА (не данных) → отдельный список для pre-commit-стража.
        _code_failures.append((label, f"{type(e).__name__}: {e}"))
        if critical:
            _critical.append((label, f"{type(e).__name__}: {e}"))
        return None


def warn(label: str, detail: str = ""):
    global WARN
    WARN += 1
    _warnings.append((label, detail))
    if _current_sensor:
        _sensor_of.setdefault(label, _current_sensor)
    if not JSON_OUTPUT:
        print(f"  ⚠️  {label}: {detail}")


def fail_(label: str, detail: str = ""):
    """Прямой FAIL без обёртки в check()."""
    global FAIL
    FAIL += 1
    _failures.append((label, detail))
    if not JSON_OUTPUT:
        print(f"  ❌ {label}")
        if detail:
            print(f"     {detail}")


# ── Импорт БД (критический) ───────────────────────────────────────────────────
try:
    import health_db as db
except ImportError as e:
    print(f"❌ КРИТИЧНО: не удалось импортировать health_db: {e}")
    sys.exit(2)

start_time = time.time()
today      = get_today()
yesterday  = today - timedelta(days=1)
week_ago   = today - timedelta(days=7)


# ─────────────────────────────────────────────────────────────────────────────
if not JSON_OUTPUT:
    print(f"\n[1] Свежесть данных Oura (порог: не старше {DATA_FRESHNESS_DAYS} дней)")


def check_data_freshness():
    for delta in range(DATA_FRESHNESS_DAYS + 1):
        d = str(today - timedelta(days=delta))
        row = db.get_day(d)
        if row and (row.get("hrv_rmssd_night") or row.get("hrv")):
            return d
    raise AssertionError(
        f"нет данных HRV за последние {DATA_FRESHNESS_DAYS} дней"
    )


check("последние данные HRV не старше 48ч", check_data_freshness)


def check_db_integrity():
    """
    PRAGMA integrity_check per-tenant — B-tree corruption и out-of-order rowids.

    История: workouts-таблица накапливала corruption незаметно несколько недель
    (2026-06), пока не начала падать на каждом Oura-импорте с
    'database disk image is malformed'. Этот чек добавлен чтобы следующая
    деградация была поймана в тот же день, не через недели.

    partner-integrity (2026-07-17): расширен на ВСЕХ тенантов (_tenant_db_paths).
    Corruption СТРУКТУРНА — не зависит от объёма данных → НЕ ложно-срабатывает на
    data-бедном партнёре (в отличие от problem_list/lab-regression, которые остались
    owner-only осознанно). Connect-ошибка = ЯВНЫЙ fail, НЕ тихий skip как в _iter_tenant_ro:
    недостижимая БД тенанта — тоже деградация (закрывает RST-усечёнку для этого измерения).
    Сообщение структурное ([tenant]+счётчик проблем), без перс. данных → owner-доставка безопасна.
    Регистрация — в позднем блоке (forward-def: _tenant_db_paths определён ниже).
    """
    import sqlite3 as _sq
    bad: list[str] = []
    for _p in _tenant_db_paths(include_current=True):
        _tag = Path(_p).parent.parent.name
        try:
            _c = _sq.connect(f"file:{_p}?mode=ro", uri=True)
        except Exception as e:  # noqa: BLE001 — недостижимая БД тенанта = деградация (не тихий skip)
            bad.append(f"[{_tag}] БД не открылась: {str(e)[:80]}")
            continue
        try:
            rows = _c.execute("PRAGMA integrity_check").fetchall()
        finally:
            _c.close()
        if not (len(rows) == 1 and rows[0][0] == "ok"):
            probs = [r[0] for r in rows]
            bad.append(f"[{_tag}] {len(probs)} проблем: {str(probs[0])[:60]}")
    assert not bad, "DB integrity: " + " ; ".join(bad)
    return {"ok": True, "tenants": len(_tenant_db_paths(include_current=True))}


def check_data_window():
    with db.get_conn() as conn:
        rows = conn.execute("""
            SELECT date FROM daily_metrics
            WHERE date BETWEEN ? AND ?
              AND sleep_total IS NOT NULL AND hrv IS NOT NULL
        """, (str(today - timedelta(days=7)), str(today - timedelta(days=1)))).fetchall()
    days_rich = len(rows)
    assert days_rich >= MIN_DAYS_WITH_DATA, (
        f"данных с sleep+hrv только за {days_rich}/7 дней, ожидалось >= {MIN_DAYS_WITH_DATA}"
    )
    return days_rich


check(f"данные за >= {MIN_DAYS_WITH_DATA} из 7 последних дней", check_data_window)


def check_steps_visible_via_get_day():
    """
    Консистентность шагов: если steps есть в flat daily_metrics.steps —
    get_day() должен их вернуть (через flat-колонки fallback).

    История (2026-06): Oura писал steps в flat-колонку но не в raw JSON
    (steps не в OURA_RAW_KEYS). get_day() читал только raw → шаги пропадали
    из lifestyle agents и morning_report на дни без Apple Health HAE-файла.
    Фикс: get_day() мёрджит flat-колонки. Этот чек ловит регрессию.
    """
    for delta in range(1, 4):
        d = str(today - timedelta(days=delta))
        with db.get_conn() as conn:
            flat_steps = conn.execute(
                "SELECT steps FROM daily_metrics WHERE date=?", (d,)
            ).fetchone()
        if not flat_steps or flat_steps[0] is None:
            continue  # нет шагов за этот день — пропускаем
        get_day_steps = db.get_day(d).get("steps")
        if get_day_steps is None:
            raise AssertionError(
                f"{d}: steps={flat_steps[0]} в flat daily_metrics.steps, "
                f"но get_day() вернул None. "
                f"Lifestyle agents и morning_report не увидят шаги."
            )
    return True


check("шаги из flat daily_metrics видны через get_day()", check_steps_visible_via_get_day)


# ─────────────────────────────────────────────────────────────────────────────
if not JSON_OUTPUT:
    print(f"\n[2] Полнота метрик — ключевые поля биометрики")


def check_core_metrics():
    for delta in range(1, 4):
        d = str(today - timedelta(days=delta))
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT sleep_total, sleep_deep, hrv, steps FROM daily_metrics WHERE date=?",
                (d,)
            ).fetchone()
        if not row:
            continue
        r = dict(row)
        missing = [k for k in ("sleep_total", "hrv") if not r.get(k)]
        if not missing:
            if not r.get("steps"):
                warn("нет steps", f"дата {d}: шаги не записаны")
            return r
        warn("неполные метрики", f"{d}: нет {', '.join(missing)}")
    raise AssertionError("нет дня с hrv+sleep_total за последние 3 дня")


check("ключевые метрики (HRV + sleep) присутствуют", check_core_metrics)


def check_stats_non_empty():
    stats = db.get_stats(7)
    assert stats and stats.get("avg_hrv"), "get_stats(7) вернул пустые данные"
    return stats


check("недельная статистика не пуста", check_stats_non_empty)


# ─────────────────────────────────────────────────────────────────────────────
if not JSON_OUTPUT:
    print(f"\n[3] Агентские отчёты — GP weekly ≤{GP_WEEKLY_MAX_AGE_DAYS}д")


def check_gp_reports():
    # F-fix (2026-07-19): окно "существования" привязано к порогу свежести
    # GP_WEEKLY_MAX_AGE_DAYS. Раньше единственная живая ветка фильтровала
    # date>=week_ago (7д), а порог возраста — 8д: отчёт возрастом ровно 8 дней
    # проваливался в зазор → ложный FAIL "нет ни одного GP-отчёта".
    # Мёртвые exact-name ветки ("gp"/"GP_daily"/"morning") убраны: реальных
    # таких имён нет (реальные gp_weekly/gp_monthly), их ловит agent_type='gp'
    # — тот же дрейф, что чинил Q-1 у соседнего check_report_content.
    gp_window = today - timedelta(days=GP_WEEKLY_MAX_AGE_DAYS)
    reports = db.get_reports_with_findings(str(gp_window), agent_type="gp")
    assert reports, "нет ни одного GP-отчёта в agent_reports"
    # До 21.09 get_reports_with_findings сортировала по (agent_type, agent_name), не
    # по дате. Теперь — свежие первыми; явная сортировка оставлена: датчик не должен
    # зависеть от порядка чужой функции.
    reports.sort(key=lambda r: r.get("date", ""), reverse=True)
    last_date_str = reports[0].get("date", "")
    if last_date_str:
        age = (today - date.fromisoformat(last_date_str[:10])).days
        assert age <= GP_WEEKLY_MAX_AGE_DAYS, (
            f"последний GP-отчёт от {last_date_str[:10]} — {age} дней назад "
            f"(порог {GP_WEEKLY_MAX_AGE_DAYS}д)"
        )
    return reports[0]


check("GP-отчёт есть и не старше 8 дней", check_gp_reports)


def check_report_content():
    # Q-1 fix (2026-05-22, roadmap / F-126): db.get_agent_report("gp")
    # использовал exact match WHERE agent_name=? — реальные имена
    # gp_weekly/gp_monthly/gp_daily, "gp" не существует. Возвращал [],
    # срабатывал fallback get_reports_with_findings(week_ago) который
    # брал MDT-отчёт без "ВСР" → false-positive FAIL.
    # Теперь явный фильтр по agent_type='gp'.
    # F-fix (2026-07-19): окно = порогу свежести GP (как в check_gp_reports),
    # иначе отчёт возрастом ровно 8д даёт вечный WARN-skip по воскресеньям и
    # его содержание не валидируется. Сортировка по дате — берём свежий отчёт,
    # а не произвольный (до 21.09 get_reports_with_findings сортировала по type,name).
    gp_window = today - timedelta(days=GP_WEEKLY_MAX_AGE_DAYS)
    reports = db.get_reports_with_findings(str(gp_window), agent_type="gp")
    if not reports:
        warn("нет GP-отчётов за окно свежести", "пропускаем")
        return None
    reports.sort(key=lambda r: r.get("date", ""), reverse=True)
    text = (reports[0].get("findings") or "") + (reports[0].get("recommendations") or "")
    assert len(text) >= REPORT_MIN_CHARS, (
        f"GP-отчёт слишком короткий: {len(text)} chars < {REPORT_MIN_CHARS}"
    )
    # Q-1 fix: расширены keywords под русские словоформы. Раньше "сон"
    # не находил "сном/сну/сна/спал" — typical русская морфология.
    # Substring match (in text.lower()): "сон" не подматч "сном", потому
    # что подматчем оно стало бы только если "сон" — префикс. Wait,
    # "сон" in "сном" даёт True (substring). А вот "сон" in "сну" — False.
    # Добавляю основные корни.
    keywords = [
        "ВСР", "HRV", "hrv",
        "сон", "сна", "сну", "сном", "спал", "сне",
        "задач", "задани",
        "sleep", "deep",
        "лаборатор", "анализ",
    ]
    found = [kw for kw in keywords if kw.lower() in text.lower()]
    assert len(found) >= 2, f"отчёт не содержит ключевых разделов: {found}"
    return found


check("содержание GP-отчёта: есть ключевые разделы", check_report_content)


# ─────────────────────────────────────────────────────────────────────────────
if not JSON_OUTPUT:
    print(f"\n[4] Проблемный список и протоколы")


def check_problem_list():
    all_problems = db.get_problem_list()
    open_statuses = {"active", "active_monitoring", "watchful_waiting", "monitoring"}
    open_probs = [p for p in all_problems if p.get("status") in open_statuses]
    assert len(open_probs) >= MIN_ACTIVE_PROBLEMS, (
        f"открытых проблем: {len(open_probs)}, ожидалось >= {MIN_ACTIVE_PROBLEMS}"
    )
    return len(open_probs)


check(f"problem_list содержит >= {MIN_ACTIVE_PROBLEMS} активных проблем", check_problem_list)


def check_problem_plain_summary():
    """Открытая проблема несёт описание простыми словами (27.09, волна 4). Медицинский текст —
    врачам, человек на дашборде видит plain_summary; иначе он получает лишь код проблемы.
    Кто заводит проблему (знакомство, документы, /approve), простой текст пока не пишет —
    пропуск видит этот датчик, дописывает инженерная очередь. warn, не fail."""
    open_statuses = {"active", "active_monitoring", "watchful_waiting", "monitoring"}
    missing = [p.get("problem_id") for p in db.get_problem_list()
               if p.get("status") in open_statuses and not (p.get("plain_summary") or "").strip()]
    if missing:
        warn("открытые проблемы без описания простыми словами", f"{len(missing)}: " + ", ".join(missing[:5]))
    return len(missing)


check("открытые проблемы описаны простыми словами", check_problem_plain_summary)


def check_protocols():
    protos = db.get_active_protocols()
    if not protos:
        warn("нет активных протоколов", "возможно пока не созданы")
    return protos


check("протоколы читаются", check_protocols)


# ─────────────────────────────────────────────────────────────────────────────
if not JSON_OUTPUT:
    print(f"\n[5] Задачи — pipeline постановки задач")


def check_tasks_pipeline():
    open_tasks = db.get_open_tasks()
    overdue    = db.get_overdue_tasks(days_old=30)
    if len(open_tasks) == 0:
        warn("нет открытых задач", "GP не ставил задач или все выполнены")
    return {"open": len(open_tasks), "overdue": len(overdue)}


check("задачи читаются (open + overdue)", check_tasks_pipeline)


def check_recent_task_activity():
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM tasks WHERE created_at >= date('now', '-14 days')"
        ).fetchone()
    cnt = row["cnt"] if row else 0
    if cnt == 0:
        warn("нет новых задач за 14 дней", "GP-агент не создавал задач")
    return cnt


check("активность задач за 14 дней", check_recent_task_activity)


# ── Канал «вопрос → ответ» ────────────────────────────────────────────────
# Датчик проверяет доставку ответа получателю, а не только вызов функции.
# Счётчик заданных вопросов без проверки ответов может скрывать потерю доставки.

def check_question_answer_integrity():
    """Вопрос, закрытый без ответа; недоставленный вопрос; ответ без следа в памяти."""
    with db.get_conn() as conn:
        empty = conn.execute(
            "SELECT id FROM tasks WHERE type='question' AND status='completed' "
            "AND (resolved_text IS NULL OR TRIM(resolved_text)='' "
            "     OR resolved_text='completed via Reminders.app')"
        ).fetchall()
        # «Некуда ответить» = нет обратного адреса (tg_message_id), а не «не
        # отправлено»: вопросы старше канала помечены sent_at от ремайндера.
        stuck = conn.execute(
            "SELECT id FROM tasks WHERE type='question' AND status='open' "
            "AND tg_message_id IS NULL "
            "AND julianday('now') - julianday(created_at) > 0.25"
        ).fetchall()
        # Очередь сама по себе — норма: доставку ограничивает дроссель
        # questions.max_per_day, и вопросы ЖДУТ очереди законно. Молчание канала —
        # другое: за сутки не ушло ни одного при непустой очереди. Датчик,
        # красневший бы на штатной очереди, обучает игнорировать триаж (§13).
        moved = conn.execute(
            "SELECT COUNT(*) AS n FROM tasks WHERE type='question' "
            "AND sent_at >= datetime('now', '-1 day')"
        ).fetchone()
        answered = conn.execute(
            "SELECT id FROM tasks WHERE type='question' AND status='completed' "
            "AND resolved_text IS NOT NULL AND TRIM(resolved_text)!=''"
        ).fetchall()

    if empty:
        # Триггер БД это запрещает — строки здесь означают, что запрет обойдён
        # (правка мимо кода, восстановление из старого дампа, снятый триггер).
        fail_("вопрос закрыт без ответа",
              f"id: {[r['id'] for r in empty]} — гейт trg_question_answer_required обойдён")
    moved_n = moved["n"] if moved else 0
    if stuck and moved_n == 0:
        fail_("канал вопросов молчит",
              f"{len(stuck)} вопросов ждут (id: {[r['id'] for r in stuck][:5]}), "
              f"за сутки не доставлено ни одного — job deliver_unsent_question_tasks "
              f"мёртв или questions.max_per_day не задан в system_config")

    # Сцепление с памятью: ответ, не доехавший в memory_facts, не увидит консилиум.
    orphan = []
    if answered:
        with db.get_conn() as conn:
            for r in answered:
                hit = conn.execute(
                    "SELECT 1 FROM memory_facts WHERE source=? LIMIT 1",
                    (f"task:{r['id']}",)
                ).fetchone()
                if not hit:
                    orphan.append(r["id"])
    if orphan:
        warn("ответ не доехал в память",
             f"id: {orphan} — record_answer записал задачу, но не факт")

    # Судья «адресовано пациенту» (2026-09-12) может ошибаться в СТОРОНУ МОЛЧАНИЯ:
    # понизит настоящие вопросы в action, и канал онемеет тихо — ни одной красной
    # строки, просто вопросов больше нет. Ошибку в эту сторону видно только по
    # отсутствию: GP-отчёты идут, задачи рождаются, а вопросов ноль.
    with db.get_conn() as conn:
        recent_q = conn.execute(
            "SELECT COUNT(*) AS n FROM tasks WHERE type='question' "
            "AND created_at >= date('now','-30 days')"
        ).fetchone()
        recent_any = conn.execute(
            "SELECT COUNT(*) AS n FROM tasks "
            "WHERE created_at >= date('now','-30 days')"
        ).fetchone()
    q_n, any_n = (recent_q["n"] if recent_q else 0), (recent_any["n"] if recent_any else 0)
    if any_n >= 10 and q_n == 0:
        warn("канал вопросов онемел",
             f"за 30 дней задач {any_n}, из них вопросов 0 — судья "
             f"addressed_to_patient мог начать понижать всё подряд")

    return {"answered": len(answered), "empty": len(empty),
            "undelivered": len(stuck), "orphan_memory": len(orphan),
            "questions_30d": q_n, "tasks_30d": any_n}


check("канал вопрос→ответ целостен", check_question_answer_integrity)


def _questions_ceiling():
    """Дневной бюджет доставки — он же потолок производства. None, если не задан.

    Молча вернуть None нельзя (ратчет тихих обработчиков, §7): отсутствие ключа это
    не «нечего проверять», а ВЫКЛЮЧЕННЫЙ канал — и доставка, и производство
    fail-closed без него. Поэтому сигнал даёт вызывающий, а не эта функция.
    """
    import config_db as cfg
    raw = cfg.get_config("questions.max_per_day", default=None)
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        warn("questions.max_per_day не число",
             f"в system_config лежит {raw!r} — дроссель и потолок читают его как отсутствие")
        return None


def check_question_queue_drains():
    """Очередь вопросов обязана СЛИВАТЬСЯ за время, которое сама себе назначает.

    Соседний датчик намеренно считает очередь нормой: она законна, пока доставка
    ограничена дросселем. Но у «законно ждёт» есть срок, и он вычисляется, а не
    назначается: при глубине очереди Q и бюджете B последний вопрос уезжает через
    Q/B суток. Ждёт дольше — либо потолок производства обойдён (кто-то пишет задачи
    мимо `promote_memory_questions`), либо доставка идёт медленнее, чем считает
    дроссель. Оба случая невидимы снаружи: задачи открыты, ошибок нет.

    Почему не «очередь больше N» (§9 и урок R2 плана нити): порог на мгновенной
    глубине краснел бы на законном всплеске разговоров и учил бы листать триаж мимо.
    Здесь порог — ПРОИЗВОДНАЯ от собственного бюджета канала.

    Сутки запаса — не подобранное число, а гранулярность самого бюджета: он задан
    «в сутки», значит вопрос, попавший в очередь после дневной выборки, законно
    ждёт следующих суток.
    """
    ceiling = _questions_ceiling()
    if not ceiling or ceiling <= 0:
        warn("бюджет вопросов не задан",
             "questions.max_per_day отсутствует или ≤0 — канал вопросов выключен "
             "fail-closed, и срок слива очереди вычислить не из чего")
        return {"checked": 0, "note": "questions.max_per_day не задан"}
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, created_at, "
            "       julianday('now') - julianday(created_at) AS age_days "
            "FROM tasks WHERE status='open' AND type='question' AND tg_message_id IS NULL "
            "ORDER BY created_at"
        ).fetchall()
    if not rows:
        return {"queued": 0}
    budget_days = len(rows) / ceiling + 1        # +1 сутки = шаг самого бюджета
    overdue = [r for r in rows if (r["age_days"] or 0) > budget_days]
    if overdue:
        fail_("очередь вопросов не сливается",
              f"{len(overdue)} из {len(rows)} ждут дольше {budget_days:.1f} сут "
              f"(бюджет {ceiling}/сут); id: {[r['id'] for r in overdue][:5]} — "
              f"потолок производства обойдён или доставка медленнее дросселя")
    return {"queued": len(rows), "overdue": len(overdue),
            "budget_days": round(budget_days, 1)}


check("очередь вопросов сливается за свой срок", check_question_queue_drains)


def check_proposals_delivered():
    """Предложение GP по списку проблем ждёт решения человека, но до него не доехало.

    Если доставка вызывается только ручными /report и /weekly, предложения
    scheduled-GP могут остаться без получателя. Порог производный: ритм outbox
    (proposals_db.DELIVERY_EVERY_S) × 2 — один пропущенный запуск плюс запас
    на рестарт бота; собственного порога у датчика нет."""
    import proposals_db as _pdb
    total, stuck = _pdb.stuck_undelivered_proposals()
    if stuck:
        fail_("предложения по списку проблем не доставлены",
              f"{len(stuck)} ждут решения, но до человека не доехали дольше "
              f"{2 * _pdb.DELIVERY_EVERY_S // 60} мин; id: {stuck[:5]} — outbox бота не работает")
    return {"undelivered": total, "stuck": len(stuck)}


check("предложения по списку проблем доставлены", check_proposals_delivered)


def check_question_candidates_not_discarded():
    """Кандидат выпал за окно, НИ РАЗУ не побывав у судьи.

    Сравнение «умерло незаданными» с «задано за то же окно» смешивает
    разные механизмы и когорты:
      • кандидаты могли появиться до запуска promote_memory_questions;
        их старение не доказывает дефект нового механизма;
      • отклонённый судьёй кандидат — работа фильтра, а не потеря доставки.
        Например, общий вопрос «как устроены измерительные приборы»
        может быть исключён как вопрос к науке;
      • редкие всплески диалога не образуют устойчивый поток, поэтому
        отношение небольших счётчиков может отражать шум.

    Теперь судится ФАКТ, а не отношение, и порога у него нет. Кандидат
    (`mem_class='question'`, `source='conversation'`) заканчивает жизнь одним из трёх
    способов, и два из них оставляют след: поднят — отпечаток `question:mem:<id>` в
    `tasks`; отклонён судьёй или схлопнут дублем — снят с активных (`active=0`) с
    причиной. Остаётся активным и без отпечатка только тот, чью формулировку судья не
    видел ни разу. Это и есть потеря: механизм, который мог её причинить, ровно один —
    потолок производства блокирует ВЕСЬ прогон, включая вызов судьи, пока очередь
    недоставленных не меньше дневного бюджета.

    Верхняя граница возраста ограничивает накопленный долг окном наблюдения.
    Старые кандидаты, появившиеся до механизма, требуют отдельного решения
    с причиной ретайра в данных. Дата запуска механизма не зашивается
    в предикат (§9, §18).

    Граница вслух. (1) Кандидат с вердиктом «пациенту», но с пустой русской
    формулировкой тоже остаётся активным — его сюда считает, и это верно: вопрос не
    задан. Отличить его от неувиденного по этим данным нельзя, разбирать придётся
    глазами по логу. (2) Ретайр по другой причине (гигиена памяти) выглядит как
    суждение судьи — дом ретайра один, причина лежит только в логе. (3) warn, не fail:
    упущенный вопрос — упущение, а не поломка, и §13 держит человека на верхней
    ступени.
    """
    import config_db as cfg
    raw = cfg.get_config("questions.memory_window_days", default=None)
    try:
        window = int(float(raw))
    except (TypeError, ValueError):
        warn("окно памяти для вопросов не задано",
             f"questions.memory_window_days = {raw!r} — подъём кандидатов не идёт вовсе")
        return {"checked": 0, "note": "questions.memory_window_days не задан"}
    with db.get_conn() as conn:
        taken = {r[0] for r in conn.execute(
            "SELECT fingerprint FROM tasks WHERE fingerprint LIKE 'question:mem:%'")}
        # ПОТОК, а не запас. Первая версия считала ВСЕХ, кто когда-либо вышел за окно,
        # и такой счётчик растёт монотонно: однажды сработав, он краснел бы каждую ночь
        # независимо от того, как канал ведёт себя СЕЙЧАС — ровно тот датчик, который
        # учат пролистывать (риск R2 плана нити). Считаем тех, кто пересёк границу за
        # последнее окно: поведение, а не история.
        #
        # `active=1` — не косметика, а весь смысл новой оси: отклонённый судьёй и
        # схлопнутый дублем сняты с активных и сюда не попадают. Отбор тот же, что
        # видит сам подъёмник (get_facts фильтрует active=1), то есть «разобранным»
        # датчик считает ровно то же, что и механизм.
        cands = conn.execute(
            "SELECT id FROM memory_facts WHERE mem_class='question' "
            "AND source='conversation' AND active=1 "
            "AND julianday('now') - julianday(valid_from) > ? "
            "AND julianday('now') - julianday(valid_from) <= ?",
            (window, 2 * window)).fetchall()
    lost = [r["id"] for r in cands if f"question:mem:{r['id']}" not in taken]
    if lost:
        warn("кандидаты в вопросы умерли, не побывав у судьи",
             f"за последнее окно ({window} дн) {len(lost)} формулировок вышли из "
             f"подъёма активными и без отпечатка — их не поднимали и не отклоняли; "
             f"id памяти: {lost[:8]}{' …' if len(lost) > 8 else ''}. Единственная "
             f"известная причина — потолок производства блокировал прогон целиком")
    return {"died_unjudged": len(lost), "window_days": window}


check("кандидаты в вопросы не выбрасываются молча", check_question_candidates_not_discarded)


def _harness_classifier():
    """Вернуть предикат «источник вызова — оснастка, а не рабочий код» и текст отказа.

    Спрашиваем ЕДИНСТВЕННЫЙ дом периметра (§15): `dispgate.class_for` даёт класс для
    путей ВНЕ судимой зоны (`plans/` → not_executed_in_operation, `tests/` →
    judgement_harness) и None для кода, который система исполняет в штатной работе.
    Своего списка каталогов здесь нет сознательно: он был бы вторым домом критерия и
    разъехался бы с первым молча.

    FAIL-LOUD, направление выбрано осознанно: неизвестный источник (запуск вне
    репозитория, `-c`, недоступный периметр) считается ПРОДОВЫМ. Датчик, ошибающийся
    в сторону лишнего крика, чинится за минуту; ошибающийся в сторону тишины не
    чинится вовсе — о нём не узнают.

    Политика читается ОДИН раз на прогон: она про устройство репозитория, а не про
    конкретный источник, и спрашивать её на каждое ведро значило бы платить за одно
    и то же знание столько раз, сколько сегодня было разных вызывающих.
    """
    try:
        from project_context import dispgate as _dg
        pol = _dg.perimeter_policy(str(Path(__file__).resolve().parent))
        return (lambda src: _dg.class_for(src, pol) is not None), None
    except Exception as e:  # noqa: BLE001 — периметр недоступен → судим всё как прод
        return (lambda src: False), str(e)[:120]


def check_llm_answer_parsed():
    """Ответ модели, который не удалось разобрать ВООБЩЕ, обязан быть видимым.

    Что стережётся. `task_agent._parse_tasks_json` общий для судьи и экстрактора
    задач. Полный отказ разбора возвращает пустоту, а пустой вердикт судьи в
    `extract_tasks_from_report` понижает ВСЕ вопросы отчёта в action — необратимо:
    задача создана, вопрос не задан никогда. Замер 13.09 до починки: 2 отказа на 10
    вызовов, и ни один не был виден снаружи.

    Почему красное только на ПОЛНОМ отказе. После пообъектного salvage частичный
    разбор теряет максимум поля одного элемента — это шум модели, не поломка системы.
    Датчик, краснеющий на нём, учил бы листать триаж мимо (тот же урок, что с
    очередью вопросов). Частичные видны числом и не поднимают тревогу.

    Почему судим ТОЛЬКО продовые вёдра (13.09, второй заход). Первая редакция читала
    один общий счётчик — и он смешивал бота с пробами разработчика на живом каноне.
    Замер в тот же день: логи обоих ботов дали ноль поломок при 12 продовых вызовах,
    а счётчик — 20 частичных и 8 отказов. Датчик покраснел бы верно по форме и ложно
    по происхождению, а это тот же яд, что ложно-зелёный: после второго раза красное
    перестают читать.

    Граница честно: датчик судит СЕГОДНЯШНИЙ счётчик. Отказ в день, когда монитор не
    отработал, останется незамеченным — счётчик обнуляется сменой даты. Это плата за
    то, что у него не может быть монотонно растущего числа; альтернатива (копить
    вечно) краснела бы всегда.

    Вторая граница, названная вслух: счётчик пишется чтением-правкой-записью без
    compare-and-swap, и два бота, стартующие одновременно, теряют счёт друг друга.
    Цена — занижение на единицы; лекарство (строки-события вместо одного JSON) завело
    бы таблицу ради метрики. Риск принят зряче, а не пропущен.
    """
    import config_db as cfg
    st = cfg.get_config("llm_parse.stats", default=None)
    if not isinstance(st, dict):
        return {"checked": 0, "note": "счётчика разбора ещё нет — модель сегодня не звали"}
    by = st.get("by")
    if not isinstance(by, dict):
        return {"checked": 0, "date": st.get("date"),
                "note": "счётчик без разбивки по источникам — старая форма, судить нечего"}

    is_harness, perim_err = _harness_classifier()
    if perim_err:
        warn("периметр не спрошен — оснастка судится как прод",
             f"{perim_err}; счётчик разбора может закричать от пробы разработчика")
    prod, harness = {}, {}
    for src, c in by.items():
        if not isinstance(c, dict):
            continue
        (harness if is_harness(src) else prod)[src] = c

    def _sum(d, field):
        return sum(int(c.get(field) or 0) for c in d.values())

    failed, ok, partial = _sum(prod, "failed"), _sum(prod, "ok"), _sum(prod, "partial")
    if failed:
        worst = sorted(prod.items(), key=lambda kv: -int(kv[1].get("failed") or 0))[0][0]
        fail_("ответ модели не разобран вовсе",
              f"{failed} полн. отказ(ов) за {st.get('date')} при {ok} успешных "
              f"(громче всех «{worst}») — вердикт судьи пуст, вопросы отчёта уходят "
              f"в action необратимо")
    return {"date": st.get("date"), "ok": ok, "partial": partial, "failed": failed,
            "оснастка": {"источников": len(harness),
                         "отказов": _sum(harness, "failed"),
                         "частичных": _sum(harness, "partial")}}


check("ответ модели разбирается", check_llm_answer_parsed)


def check_reco_repeats_fresh_lab():
    """Текст советует сдать анализ, который уже сдан СВЕЖЕЕ окна мониторинга.

    Класс. Утверждение об отсутствии бывает двух форм. «Анализ не сдавался» ловит
    сторож `absent_claims_contradicted`. Вторая форма — то же самое, сказанное как
    ДЕЙСТВИЕ: «повторить анализ». Она не содержит ни одного слова из словаря
    отсутствия, поэтому сторож её не видит, а человек получает лишнее назначение.

    Если свежий результат уже лежит внутри окна мониторинга, рекомендация
    повторить анализ должна учитывать его дату. WARN делает возможный повтор
    видимым для проверки; датчик не устанавливает медицинскую необходимость
    повторного исследования.

    Границы вслух. (1) Периметр — тексты за сутки из трёх домов: `agent_reports`,
    `tasks` (то, что человек читает как задачу) и `hypotheses_cbcr` (консилиум).
    Ответы бота в диалоге сюда НЕ входят — их судит сторож в самом чате, до отправки.
    (2) Аналит без расписания мониторинга даёт класс
    `no_schedule` и считается ОТДЕЛЬНО — «судить нечем» не то же, что «чисто» (§18).
    (3) Предикат читает СЕГОДНЯШНИЙ канон: текст недельной давности судится тем, что
    известно сейчас; для суточного окна расхождение пренебрежимо, для исторического
    прогона — нет: состояние канона могло измениться после создания отчёта.
    """
    import gp_context as _gc
    import labs_db as _ldb
    since = str(get_today() - timedelta(days=1))
    # Три дома текстов, которые за сутки доехали до человека. Консилиум и задачи
    # добавлены сюда, а не обвешаны сторожем в коде: у их выхода нет одной точки
    # возврата (JSON координатора, строки задач), а у монитора она есть. Класс тот же.
    texts = []
    with db.get_conn() as conn:
        for r in conn.execute(
                "SELECT date, agent_name, COALESCE(recommendations,'') AS a, "
                "COALESCE(findings,'') AS b FROM agent_reports WHERE date >= ?",
                (since,)).fetchall():
            texts.append((f"отчёт {r['agent_name']}", (r["a"] + " " + r["b"]).strip()))
        for r in conn.execute(
                "SELECT source, content FROM tasks WHERE COALESCE(source_date, '') >= ?",
                (since,)).fetchall():
            texts.append((f"задача {r['source']}", r["content"] or ""))
        try:
            for r in conn.execute(
                    "SELECT generated_by, payload FROM hypotheses_cbcr "
                    "WHERE generated_at >= ?", (since,)).fetchall():
                texts.append((f"гипотеза {r['generated_by']}", r["payload"] or ""))
        except Exception as e:      # таблицы может не быть у нового тенанта
            warn("периметр судьи неполон", f"hypotheses_cbcr не прочитан: {e}")
    texts = [(src, t) for src, t in texts if t]
    if not texts:
        return {"текстов": 0}
    last = {r["test_name"]: r["date"] for r in _ldb.get_recent_labs(730 * 5)}
    sched = _ldb.get_effective_lab_schedule()
    fresh, no_sched, absent = [], 0, []
    for src, text in texts:
        for hit in _gc.recommendations_without_evidence(text, last, sched):
            if hit["kind"] == "fresh_row":
                fresh.append(f"{src}: {hit['test']} сдан {hit['last_date']} "
                             f"({hit['age_days']}д назад при окне {hit['interval_days']}д)")
            else:
                no_sched += 1
        for hit in _gc.absent_claims_contradicted(text, last, _ldb.PROMPT_WINDOW_DAYS):
            absent.append(f"{src}: {hit['test']} есть в базе ({hit['last_date']})")
    if fresh:
        warn("совет сдать уже сданное",
             "; ".join(fresh[:5]) + (f" и ещё {len(fresh) - 5}" if len(fresh) > 5 else "")
             + " — текст не назвал дату последней строки")
    if absent:
        # FAIL, а не WARN: это прямая ложь в тексте, дошедшем до человека. Сторожа
        # трактов ловят её ДО доставки; сюда она попадает, только если тракт не под
        # сторожем — то есть найдена дыра периметра, а не шум формулировки.
        fail_("текст утверждает отсутствие анализа, который есть",
              "; ".join(absent[:5]) + (f" и ещё {len(absent) - 5}" if len(absent) > 5 else ""))
    return {"текстов": len(texts), "повторов": len(fresh),
            "без_расписания": no_sched, "лживых_отсутствий": len(absent)}


check("доставленные тексты судятся против канона", check_reco_repeats_fresh_lab)


# Тракты, чей выход НЕ под сторожем осознанно. Причина обязательна: пустая строка
# здесь — это «забыли», и отличить её от решения нельзя (урок `legacy` в §15).
_ABSENCE_SURFACE_ACCEPTED = {
    "task_agent": "выход читает СУДЬЯ, а не человек напрямую; у формулировки вопроса "
                  "свой механизм — улика об измерениях (judge_gets_evidence_not_a_second_judge, "
                  "13.09), а доставленные задачи судит датчик выше",
    "hypothesis_consilium_eval": "тракт «модель→модель»: его текст читает следующая "
                                 "модель, а не человек (решение владельца 14.09, вариант C)",
    "monthly_consilium": "выход — JSON координатора без одной точки возврата; судится "
                         "датчиком выше по сохранённым гипотезам (hypotheses_cbcr)",
}
_ABSENCE_SURFACE_BASELINE = 0   # замер 14.09: открытых трактов не осталось


def check_absence_surface_guarded(root=None):
    """Тракт с ПОВЕРХНОСТЬЮ для утверждения об отсутствии либо под сторожем, либо назван.

    `root` — каталог сканирования; по умолчанию каталог проекта. Параметр существует
    ради оракула: доказать, что датчик КРАСНЕЕТ на новом тракте с поверхностью, можно
    только подсунув ему такой тракт, а заводить его в самом репозитории ради теста
    значило бы держать приманку в проде.

    Зачем датчик (14.09). Аудит контекстов — испытание с человеком-оракулом: он
    однократен по природе и вердикт «поверхности нет» после него живёт вечно, потому
    что у него нет гасителя (§18). Гаситель — этот датчик: он пересчитывает периметр и
    поверхность на КАЖДОМ прогоне, поэтому промпт, обзаведшийся приглашением говорить
    об отсутствии, краснеет сам, без чтения кода человеком.

    Что считается. Периметр — модуль, который И зовёт модель (импорт `anthropic`,
    `llm_client` или `hai_core`), И читает лаб/метрик-данные: замер 14.09 — 14 модулей.
    Поверхность — словарь отсутствия ИЛИ рекомендательный словарь в строковых
    константах модуля длиннее 200 символов; оба дома лексикона — `gp_context`, копий
    здесь нет. Под сторожем — модуль, в AST которого есть вызов судьи.

    Почему периметр ВЫЧИСЛЯЕТСЯ, а не перечисляется: список имён разъехался бы с
    деревом ровно так, как разошёлся `_LLM_TRACTS_KNOWN` до AST-редакции (§19, §18).

    Поверхность бывает УНАСЛЕДОВАННОЙ: тракт не имеет своих промпт-констант, но берёт
    чужой промпт (системный промпт бота, роль из `specialists/*.md`). Такой тракт
    считается имеющим поверхность по факту вызова — испытание 14.09 показало, что
    обратное правило давало ложное «чисто» сразу двум лгавшим трактам.

    Границы. (1) Промпт, собранный из кусков в рантайме и не через известный
    заимствователь, детектор не видит. (2) Детектор отвечает «приглашение есть», а не
    «модель соврала»: второе судит датчик доставленных текстов выше. (3) Список
    заимствователей — данные рядом с кодом; новый способ занять чужой промпт в него
    не попадёт сам, и это названная дыра, а не забытая.
    """
    import ast as _ast
    import gp_context as _gc
    root = Path(root) if root else Path(__file__).parent
    LLM = {"anthropic", "llm_client", "hai_core"}
    DATA = __import__("re").compile(
        r"labs_db|get_recent_labs|get_lab_|daily_metrics|metrics_db|lab_results")
    GUARD = {"judge_absence_claims", "_guard_absent_claims", "_guarded_reply",
             "_guarded_report", "_guarded_text", "_judge_protocol"}
    # УНАСЛЕДОВАННАЯ поверхность: тракт может не иметь своих промпт-констант и всё
    # равно приглашать модель говорить об отсутствии — потому что берёт ЧУЖОЙ промпт.
    # Найдено испытанием 14.09: детектор объявил `lifestyle_agents` и `hai_hypotheses`
    # чистыми (их роли живут в `specialists/*.md` и в системном промпте бота), а живые
    # вызовы показали ложь у обоих — «сдать <аналит>, нет данных за 180 дней» при строке
    # внутри этого окна и то же про другую панель при строках
    # недельной свежести. Вердикт «поверхности нет» был ложным ровно потому, что
    # детектор искал промпт там, где его нет.
    INHERITS = {"get_system_prompt", "lifestyle_prompt", "_read_medical_prompt",
                "_build_checkin_system", "_build_gp_system_prompt"}
    perim, guarded, surface, unreadable = [], [], [], []
    for p in sorted(root.glob("*.py")):
        if p.name.startswith(("test_", "_")) or p.name == Path(__file__).name:
            continue
        try:
            src = p.read_text(encoding="utf-8", errors="ignore")
            tree = _ast.parse(src)
        except (OSError, SyntaxError) as e:
            # Нечитаемый файл выпадает из периметра — то есть тракт внутри него
            # становится невидимым. Молчать нельзя, это тот же отказ (ср. §19-датчик).
            warn("периметр поверхности: файл не разобран", f"{p.name}: {type(e).__name__}")
            unreadable.append(p.name)
            continue
        mods = set()
        for n in _ast.walk(tree):
            if isinstance(n, _ast.Import):
                mods |= {a.name.split(".")[0] for a in n.names}
            elif isinstance(n, _ast.ImportFrom) and n.module:
                mods.add(n.module.split(".")[0])
        if not (mods & LLM) or not DATA.search(src):
            continue
        perim.append(p.stem)
        calls = {getattr(n.func, "attr", None) or getattr(n.func, "id", None)
                 for n in _ast.walk(tree) if isinstance(n, _ast.Call)}
        if calls & GUARD:
            guarded.append(p.stem)
            continue
        blob = "\n".join(n.value.value for n in _ast.walk(tree)
                         if isinstance(n, _ast.Assign) and isinstance(n.value, _ast.Constant)
                         and isinstance(n.value.value, str) and len(n.value.value) > 200)
        if _gc._ABSENCE_RE.search(blob) or _gc._RECO_RE.search(blob) or (calls & INHERITS):
            surface.append(p.stem)
    assert perim, "периметр пуст — детектор смотрит мимо дерева (позитивный контроль)"
    open_tracts = sorted(set(surface) - set(_ABSENCE_SURFACE_ACCEPTED))
    if len(open_tracts) > _ABSENCE_SURFACE_BASELINE:
        raise AssertionError(
            f"трактов с поверхностью и без сторожа стало {len(open_tracts)} "
            f"(baseline {_ABSENCE_SURFACE_BASELINE}): {open_tracts}. Либо проведи выход "
            f"через gp_context.judge_absence_claims, либо назови причину в "
            f"_ABSENCE_SURFACE_ACCEPTED — пустой причины не бывает.")
    return {"периметр": len(perim), "под сторожем": len(guarded),
            "с поверхностью": len(surface), "открытых": len(open_tracts),
            "не разобрано": len(unreadable)}


check("поверхность отсутствия под сторожем или названа", check_absence_surface_guarded)


def check_question_answers_reach_doctor():
    """Квитанция в точке потребления: свежий ответ ВИДЕН в собранном GP-контексте.

    Предыдущий датчик проверяет БД — этот проверяет то, что реально уедет в промпт.
    Между ответом и врачом стоит сборка контекста, и именно она молчала до 09-12:
    `resolved_text` не читал никто, вопрос задавался заново каждую неделю."""
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT id, resolved_text FROM tasks WHERE type='question' "
            "AND status='completed' AND resolved_text IS NOT NULL "
            "AND TRIM(resolved_text)!='' "
            "AND julianday('now') - julianday(resolved_at) <= 14 "
            "ORDER BY resolved_at DESC LIMIT 1"
        ).fetchone()
    if not row:
        return {"checked": 0, "note": "свежих ответов нет — проверять нечего"}

    import gp_context as _gc
    ctx = _gc.build_gp_context(today, 7)
    fragment = (row["resolved_text"] or "")[:40]

    # Ищем фрагмент ИМЕННО в своей секции, а не «где-нибудь в контексте».
    # Мутация 2026-09-12 (снял _build_patient_answers_block из сборки) оставила
    # датчик зелёным: тот же текст доезжает вторым путём — через память в блок
    # «ЗАМЕТКИ ИЗ ДИАЛОГОВ». Зелёный, причинённый не тем механизмом, который
    # проверяют, — это §20 в чистом виде, и поймала его только мутация.
    marker = "ОТВЕТЫ ПАЦИЕНТА НА ВОПРОСЫ"
    start = ctx.find(marker)
    section = ctx[start:start + 4000] if start >= 0 else ""
    if fragment and fragment not in section:
        fail_("ответ пациента не доехал до врача",
              f"задача #{row['id']}: ответ есть в БД, но секции ответов в "
              f"GP-контексте нет или он в неё не попал "
              f"(секция найдена: {start >= 0})")
        return {"checked": 1, "in_section": False}
    return {"checked": 1, "in_section": True}


check("ответы пациента доезжают до GP-контекста", check_question_answers_reach_doctor)


# Статусы наблюдения обещают человеку «учитываю в каждом недельном разборе» (решение
# владельца 29.09: подпись от системы + сторож). «Разбор идёт» стережёт check_gp_reports;
# этот датчик стережёт второе звено — проблема правда попадает в собранный контекст разбора.
WATCHED_STATUSES = ("active_monitoring", "watchful_waiting", "monitoring")
_PROBLEM_SECTION = "АКТИВНЫЙ СПИСОК ПРОБЛЕМ (из базы данных):"


def watched_missing_from_review(rows, ctx: str) -> list:
    """Проблемы со статусом наблюдения, которых нет строкой в СВОЕЙ секции контекста."""
    start = ctx.find(_PROBLEM_SECTION)
    end = ctx.find("\n\n", start) if start >= 0 else -1
    section = ctx[start:end if end > 0 else None] if start >= 0 else ""
    return [r["problem_id"] for r in rows if r["status"] in WATCHED_STATUSES
            and not re.search(rf"(?m)^{re.escape(str(r['problem_id']))} \[", section)]


def check_watched_problems_reach_review(rows=None, ctx=None):
    """Каждая проблема со статусом наблюдения видна в контексте недельного разбора GP."""
    if rows is None:
        with db.get_conn() as conn:
            rows = [dict(r) for r in conn.execute(
                "SELECT problem_id, status FROM problem_list WHERE status IN (?,?,?)",
                WATCHED_STATUSES).fetchall()]
    if not rows:
        return {"checked": 0, "note": "проблем со статусом наблюдения нет"}
    if ctx is None:
        import gp_context as _gc
        ctx = _gc.build_gp_context(today, 7)
    missing = watched_missing_from_review(rows, ctx)
    assert not missing, (f"статус «учитываю в каждом недельном разборе» не держится: проблемы "
                         f"{missing} не попали в контекст разбора")
    return {"checked": len(rows), "missing": 0}


check("проблемы под наблюдением доезжают до недельного разбора", check_watched_problems_reach_review)


# ─────────────────────────────────────────────────────────────────────────────
# [6] Checkin pipeline — датчик СНЯТ 2026-08-17 вместе с автозапуском чекина.
# Он краснел на «нет чекинов за 7 дней»; после снятия автозапуска пустота стала
# ОЖИДАЕМЫМ состоянием, и датчик warn'ил бы каждую ночь по норме. Датчик, который
# горит на штатном положении дел, тренирует игнорировать ночной триаж целиком
# (§13, banner-blindness). Вернётся вместе с новой механикой чекина — но уже
# с порогом, привязанным к её расписанию, а не к факту «хоть что-то было».


# ─────────────────────────────────────────────────────────────────────────────
if not JSON_OUTPUT:
    print(f"\n[7] Сквозной тест: данные → контекст для GP")


def check_build_context():
    ctx = db.build_context(yesterday)
    assert ctx, "build_context(yesterday) вернул пустой dict"
    keys = set(ctx.keys())
    expected_any = {"today", "metrics", "stats", "problems", "tasks",
                    "day", "daily", "date", "window"}
    assert keys & expected_any, (
        f"build_context вернул неожиданную структуру: {list(keys)[:8]}"
    )
    return list(keys & expected_any)


check("build_context(yesterday) содержит данные", check_build_context)


# ─────────────────────────────────────────────────────────────────────────────
if not JSON_OUTPUT:
    print(f"\n[8] Свежесть источников данных (TESTING_CONTRACTS.md §1)")


def check_lab_freshness():
    """Анализы не старше LAB_ALERT_DAYS (9 мес = тревога, 6 мес = предупреждение)."""
    # brief-neutralization step0/Tier-A: per-tenant. Дата последнего анализа = квази-PHI →
    # своему тенанту деталь, сиблингу age-only. Регистрация — ниже _tenant_db_paths.
    out: dict = {}
    for _tag, _conn, _cur in _iter_tenant_ro():
        try:
            row = _conn.execute("SELECT MAX(date) as last_date FROM lab_results").fetchone()
        except Exception as e:  # noqa: BLE001 — A5 22.09: законна только «нет таблицы у тенанта»; иная ошибка — находка
            if not _absent_table(e):
                warn(f"lab-freshness: [{_tag}] чтение упало не из-за отсутствия таблицы",
                     f"{type(e).__name__}: {str(e)[:80]}")
            continue
        last = row["last_date"] if row else None
        if not last:
            warn(f"[{_tag}] нет лабораторных данных", "import_medical_docs не запускался?")
            continue
        age_days = (today - date.fromisoformat(last)).days
        _det = f"последний: {last}" if _cur else ""  # дату — только своему тенанту
        if age_days > LAB_ALERT_DAYS:
            # свежесть = каденция данных, не баг системы: своему тенанту FAIL (его здоровье),
            # сиблингу WARN (не краснить owner-nightly чужой лаб-каденцией; §13 анти-banner-blindness)
            _m = f"[{_tag}] анализы устарели: {age_days}д (порог {LAB_ALERT_DAYS}д)"
            if _cur:
                fail_(_m, _det)
            else:
                warn(_m, _det)
        elif age_days > LAB_REMINDER_DAYS:
            warn(f"[{_tag}] анализы требуют обновления: {age_days}д", _det)
        out[_tag] = age_days
    return out or None
# check("свежесть lab_results") ПЕРЕНЕСЁН в блок под _tenant_db_paths.


def check_lab_canon_health():
    """Канон lab_results: нет физиологически невозможных значений.
    Ошибки распознавания проверяются по правилам правдоподобия; конфликтующие
    значения одного (date, аналит) — по согласованности после промоута.
    Датчик пайплайна; замысел — subsystem_intent.yaml::lab_recognizer."""
    # step0/Tier-A: per-tenant. PHI-guard — свой тенант: детали значений; сиблинг: ТОЛЬКО
    # счётчик (значения анализов партнёра НЕ в owner-доставку). Регистрация — в блоке.
    import lab_oracles
    import lab_canon
    bounds, pct = lab_oracles._HARD, lab_oracles._PCT
    out: dict = {}
    for _tag, _conn, _cur in _iter_tenant_ro():
        try:
            # specimen и method в ключе с 2026-07-31 (шаг 6 нити loinc-name-home):
            # два прибора под одним именем в один день — НЕ split-brain, а два
            # измерения, которые промоут развёл НАМЕРЕННО (например, один гормон
            # масс-спектрометрией и иммуноанализом). Без этих полей датчик
            # ругался бы ровно на то, ради чего шаг делался. Ключ дедупа канона и
            # ключ этого датчика обязаны совпадать, иначе они судят разное.
            cols_l = {r[1] for r in _conn.execute("PRAGMA table_info(lab_results)")}
            extra = ", ".join(c for c in ("specimen", "method") if c in cols_l)
            rows = _conn.execute(
                f"SELECT date, test_name, value, unit{', ' + extra if extra else ''} "
                "FROM lab_results WHERE value IS NOT NULL").fetchall()
        except Exception as e:  # noqa: BLE001 — A5 22.09: законна только «нет таблицы у тенанта»; иная ошибка — находка
            if not _absent_table(e):
                warn(f"lab-canon: [{_tag}] чтение упало не из-за отсутствия таблицы",
                     f"{type(e).__name__}: {str(e)[:80]}")
            continue
        bad, seen = [], {}
        for r in rows:
            cn = lab_canon.normalize(r["test_name"])
            v, _u = lab_canon.to_conventional(cn, r["value"], r["unit"])
            k = r.keys()
            # Границы физиологии в lab_oracles._HARD — КРОВЯНЫЕ. Применять их к
            # моче нельзя: концентрации калия и натрия в разовой моче
            # — норма, а по кровяной шкале это несовместимо с жизнью. Замер
            # 2026-07-31: ровно пять таких строк приехали с перепромоутом
            # (мочевая панель ИСП-МС, материал прочитан из
            # шапки как «Моча (разовая)»). Это пробел ДАТЧИКА, а не дефект данных.
            # Мочевые границы — клиническое знание, его вносит человек; до тех пор
            # честнее не судить, чем судить чужой линейкой.
            _blood = (r["specimen"] if "specimen" in k else None) in (None, "", "blood")
            if _blood and cn in bounds and not (bounds[cn][0] <= v <= bounds[cn][1]):
                bad.append(f"{r['date']} {r['test_name']}={v} (вне физиологии)")
            elif _blood and cn in pct and not (0 <= v <= 100):
                bad.append(f"{r['date']} {r['test_name']}={v}% (>100)")
            key = (r["date"], cn,
                   r["specimen"] if "specimen" in k else None,
                   r["method"] if "method" in k else None)
            if key in seen and abs(seen[key] - v) > 1e-6:
                bad.append(f"конфликт {r['date']} {cn}: {seen[key]} vs {v}")
            else:
                seen[key] = v
        if bad:
            _det = "; ".join(bad[:8]) if _cur else ""  # значения — только своему тенанту
            warn(f"[{_tag}] канон lab_results: {len(bad)} невозможных/конфликтов", _det)
        out[_tag] = len(rows)
    return out or None
# check("канон lab_results: нет невозможных значений и конфликтов") ПЕРЕНЕСЁН в блок.


def check_lab_single_writer():
    """Инвариант единственного писателя канона (BL-LAB-CANON-1, Primary-Based
    Protocol). lab_results пишет только vision-пайплайн (source=doc:/имя PDF).
    Строки от ЗАГЛУШЁННОГО biochemical-JSON writer (source *_chemistry.json) =
    нарушение: рецидив старого пути ИЛИ устаревшая копия после отката из бэкапа.
    Красный до реконсиляции Ш3; после — ловит любой новый прорыв фенса."""
    import collections
    with db.get_conn() as conn:
        rows = conn.execute(  # %chemistry%.json ловит и дубли _2/_594.json
            "SELECT source FROM lab_results "
            "WHERE source LIKE '%chemistry%.json' AND source NOT LIKE 'instrument:%'"
        ).fetchall()
    if rows:
        by = collections.Counter(r["source"] for r in rows)
        warn(f"канон lab_results: {len(rows)} строк от заглушённого biochemical-writer "
             f"(нарушение single-writer, source *_chemistry.json)",
             "; ".join(f"{s}×{n}" for s, n in by.most_common(6)))

    # Второй ретайрнутый писатель (фенс 2026-07-29): coordinate_import →
    # _write_known_to_db писал в канон НАПРЯМУЮ, минуя staging и ревью (F-01).
    # Его строки узнаются по source БЕЗ известного префикса: единственный живой
    # писатель ставит `doc:`, приборы — `instrument:`, ручной ввод — `manual:`.
    # Смотрим только на строки моложе фенса: более ранние относятся к легаси.
    # Граница отделяет рецидив от истории без ратчета по размеру коллекции.
    with db.get_conn() as conn:
        # Неспособность проверить — не пропуск. Нет колонки времени → датчик рецидива
        # СЛЕПОЙ, и молчание тут неотличимо от «всё чисто»; говорим об этом громко.
        cols = {r[1] for r in conn.execute("PRAGMA table_info(lab_results)")}
        if "created_at" not in cols:
            warn("датчик рецидива ретайрнутого писателя СЛЕП: в lab_results нет "
                 "created_at — отличить легаси от нового прорыва нечем")
            return len(rows)
        revived = conn.execute(
            "SELECT source, COUNT(*) n FROM lab_results "
            "WHERE created_at > ? AND source IS NOT NULL "
            "AND source NOT LIKE 'doc:%' AND source NOT LIKE 'instrument:%' "
            "AND source NOT LIKE 'manual:%' "
            # Строки biochemical-писателя тоже без префикса, но за них отвечает
            # запрос выше. Без этого исключения один и тот же прорыв считался бы
            # дважды — поймано существующим тестом фенса, не мной.
            "AND source NOT LIKE '%chemistry%.json' "
            "GROUP BY source ORDER BY n DESC",
            (RETIRED_LAB_WRITER_FENCE,)
        ).fetchall()
    if revived:
        total = sum(r["n"] for r in revived)
        warn(f"канон lab_results: {total} строк от РЕТАЙРНУТОГО писателя после фенса "
             f"{RETIRED_LAB_WRITER_FENCE} — старый путь воскрес, staging обойдён",
             "; ".join(f"{r['source']}×{r['n']}" for r in revived[:6]))
    return len(rows) + sum(r["n"] for r in revived)


check("канон lab_results: единственный писатель (нет biochemical-json)", check_lab_single_writer)


def promotion_backlog(canon_rows, staging_rows, today_):
    """Чистая логика датчика затора: сколько строк ждут промоута и сколько дней.

    Вход: canon_rows [(date, test_name)], staging_rows [(date, canonical_name,
    created_at)] со статусом к авто-промоуту, today_ — дата отсчёта (инъекция, а
    не now(): иначе тест непроверяем). Выход: (сколько_ждёт, самая_старая, возраст).
    Ждёт = пары (дата, нормализованное имя) НЕТ в каноне. Возраст None, если ждать
    нечего либо created_at не разбирается — битая дата не должна гасить датчик,
    но и не должна изображать нулевой возраст (это был бы тихий ложно-зелёный).
    """
    import lab_canon
    canon = {(d, lab_canon.normalize(t)) for d, t in canon_rows}
    waiting = [r for r in staging_rows
               if (r[0], lab_canon.normalize(r[1])) not in canon]
    if not waiting:
        return 0, None, None
    oldest = min((r[2] or "") for r in waiting)
    try:
        age = (today_ - date.fromisoformat(oldest[:10])).days
    except Exception:  # noqa: BLE001 — битый created_at: возраст неизвестен, счёт известен
        return len(waiting), oldest or None, None
    return len(waiting), oldest[:10], age


def check_promotion_backlog_stale():
    """Труба распознавания не должна забиваться на ВЫХОДЕ молча.

    Если готовые к промоуту строки копятся в staging, канон может не пополняться
    при живом распознавателе. Промоут, запускаемый человеком из дашборда
    (api_lab_review), не обязан иметь отдельный постоянно работающий процесс.
    Поэтому проверяем возраст очереди, а не только живость процессов.

    §13, почему эскалация, а не авто-ремонт: безопасного авто-действия здесь нет —
    авто-промоут в клинический канон и есть то самое опасное, от чего защищает
    staging; блокировать тоже нечего (никто не пишет). Остаётся человек, и он
    единственный оракул: решение «пустить эти значения в канон» медицинское.
    """
    out: dict = {}
    for _tag, _conn, _cur in _iter_tenant_ro():
        try:
            canon = _conn.execute("SELECT date, test_name FROM lab_results").fetchall()
            rows = _conn.execute(
                "SELECT date, canonical_name, created_at FROM lab_results_staging "
                "WHERE review_status IN ('auto','gold') AND canonical_name IS NOT NULL "
                "AND canonical_name != ''").fetchall()
        except Exception as e:  # noqa: BLE001 — A5 22.09: законна только «нет таблицы у тенанта»; иная ошибка — находка
            if not _absent_table(e):
                warn(f"promotion-backlog: [{_tag}] чтение упало не из-за отсутствия таблицы",
                     f"{type(e).__name__}: {str(e)[:80]}")
            continue
        n, oldest, age = promotion_backlog([tuple(r) for r in canon],
                                           [tuple(r) for r in rows], today)
        out[_tag] = n
        if age is not None and age > PROMOTION_BACKLOG_DAYS:
            warn(f"[{_tag}] промоут стоит: {n} строк ждут {age}д "
                 f"(порог {PROMOTION_BACKLOG_DAYS}д)",
                 f"самая старая с {oldest}" if _cur else "")
    return out or None


def disjoint_reference_ranges(rows) -> list:
    """Чистая: ключи (имя, единица, материал), под которыми лежат НЕПЕРЕСЕКАЮЩИЕСЯ
    референсные интервалы. Вход — [(name, unit, specimen, ref_low, ref_high)].

    Почему это признак СКЛЕЙКИ РАЗНЫХ АНАЛИТОВ, а не разброса норм: референсный
    интервал приходит из того же бланка, что и значение. Если под одним ключом
    лаборатория указала несовместимые интервалы для разных форм вещества,
    канонизатор мог ошибочно объединить их под одним именем. Это повод сверить
    идентичность анализов по документу, а не автоматически переименовать строки.
    """
    by_key: dict = {}
    for name, unit, spec, lo, hi in rows:
        if lo is None or hi is None:
            continue
        by_key.setdefault((name, unit, spec), set()).add((lo, hi))
    out = []
    for k, ivs in sorted(by_key.items()):
        ivs = sorted(ivs)
        if any(h1 < l2 or h2 < l1 for l1, h1 in ivs for l2, h2 in ivs):
            out.append((k, ivs))
    return out


def check_loinc_decisions_possible():
    """Записанное решение не смеет ссылаться на код, невозможный для этого человека.

    Отсев `loinc_match.impossible_for` работает на КАНДИДАТАХ — до вердикта.
    После вердикта то же ограничение должно проверяться повторно: отображение
    может устареть или противоречить допустимому материалу. Иначе две половины
    одного правила — выбор кандидата и проверка принятого решения — расходятся.

    Судья тот же самый (`impossible_for`), а не второй экземпляр правила: иначе
    два дома одного суждения разъехались бы молча.

    §13, почему WARN и эскалация, а не авто-ремонт: заменить код может только
    человек — «правильный» вариант тут клинический выбор, а не вычисление.

    ГРАНИЦА, названная вслух: факты о человеке читаются для ТЕКУЩЕГО тенанта
    (`loinc_match.clinical_facts` идёт в `profile_db`), поэтому датчик судит
    отображение этого тенанта. Читать чужой профиль своим SELECT значило бы
    завести второго читателя `patient_profile` — ровно тот дубль, который
    дубль-гейт поймал на этой же нити.
    """
    with db.get_conn(read_only=True) as conn:
        if not conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='lab_name_loinc'").fetchone():
            return None
        rows = conn.execute(
            "SELECT our_name, unit, loinc_num, decided_by FROM lab_name_loinc").fetchall()
        if not rows:
            return "0 решений"
        db.attach_reference(conn)
        if not conn.execute(
                "SELECT 1 FROM ref.sqlite_master WHERE type='table' "
                "AND name='loinc_terms'").fetchone():
            warn("справочник LOINC не присоединён — решения не судятся",
                 f"ожидался {db.LOINC_DB_PATH}")
            return None
        codes = sorted({r[2] for r in rows})
        ph = ",".join("?" * len(codes))
        meta = {r[0]: {"system": r[1] or "", "component": r[2] or "", "long": r[3] or ""}
                for r in conn.execute(
                    f"SELECT loinc_num, system, component, long_common_name "
                    f"FROM loinc_terms WHERE loinc_num IN ({ph})", codes)}
    import loinc_match
    facts = loinc_match.clinical_facts()
    bad = 0
    for our_name, unit, code, decided_by in rows:
        why = loinc_match.impossible_for(code, meta, facts)
        if why:
            bad += 1
            warn(f"решение противоречит отсеву: {our_name} [{unit}] → {code}",
                 f"{meta.get(code, {}).get('long', '?')} — {why} "
                 f"(записал: {decided_by}); нужен вердикт человека")
    return f"{len(rows)} решений, противоречат отсеву: {bad}"


def check_lab_names_not_glued():
    """Разные аналиты под одним именем — тихая порча тренда.

    Оракул — сама лаборатория: непересекающиеся референсные интервалы под одним
    ключом означают, что мерили разное. Класс ошибки тот же, что `ox-LDL` → `LDL`
    и `Кальций ионизированный` → `Calcium`: значение правдоподобно, имя знакомо,
    и в тренде это выглядит как «показатель упал вдвое».

    §13, почему эскалация: слить или развести имена — решение о том, ЧТО измеряли,
    и принять его может только человек с бланком в руках.
    """
    out: dict = {}
    for _tag, _conn, _cur in _iter_tenant_ro():
        # Наличие таблицы проверяется ВОПРОСОМ, а не глушителем: `except: continue`
        # молчал бы и при опечатке в SQL, то есть датчик выключился бы сам и
        # доложил «чисто». Ратчет тихих обработчиков поймал это на входе.
        if not _conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='lab_results_staging'").fetchone():
            continue
        rows = _conn.execute(
            "SELECT canonical_name, unit, panel, ref_low, ref_high, raw_name "
            "FROM lab_results_staging WHERE canonical_name IS NOT NULL "
            "AND canonical_name <> '' AND unit IS NOT NULL AND unit <> ''"
        ).fetchall()
        import lab_canon
        import lab_promote
        # Имя выводим ТЕМ ЖЕ правилом, что промоут и лист LOINC, а не читаем из
        # `canonical_name`: та колонка — снимок канонизатора на момент разбора.
        # Если канонизатор научился различать формы вещества, старый снимок
        # имени может всё ещё объединять их. Проверка снимка вместо canon_of
        # тогда создаёт ложную тревогу о склейке, уже устранённой правилом.
        prepared = []
        for r in rows:
            nm = lab_promote.canon_of({"raw_name": r[5], "canonical_name": r[0]})
            prepared.append((nm, lab_canon.norm_unit(r[1]),
                             lab_promote.specimen_of({"panel": r[2], "canonical_name": nm}),
                             r[3], r[4]))
        found = disjoint_reference_ranges(prepared)
        out[_tag] = len(found)
        for (name, unit, spec), ivs in found:
            warn(f"[{_tag}] под одним именем разные аналиты: {name} [{unit}, {spec}]",
                 f"лаборатория дала несовместимые нормы {ivs} — значит мерила "
                 f"разное, а канонизатор слил в одно имя" if _cur else "")
    return out or None


def unitless_offenders(rows, fence):
    """Чистая логика датчика: строки канона МОЛОЖЕ фенса, где число есть, а единицы нет.

    Вход: rows [(created_at, test_name, value, unit)], fence — граница ISO-строкой
    (инъекция, а не константа внутрь: иначе датчик непроверяем). Выход — список
    нарушителей. Пустой фенс = судить всё (нужен для контекстной цифры легаси).

    Три отсева, и каждый закрывает СВОЙ класс, а не «удобство»:
    · created_at <= fence — исторические строки без единицы требуют отдельного
      ремонта. Без границы датчик рецидива постоянно сообщал бы старый долг,
      и его научились бы игнорировать (§14).
    · `value is None` — строка без числа шкалу не теряла: измерять нечего.
    · безразмерный аналит — у отношения единицы нет и не будет (lab_canon.DIMENSIONLESS).

    Строку с пустым `created_at` НЕ пропускаем: она неотличима от новой, и молчание
    о ней было бы готовым способом обойти фенс — не ставить время.
    """
    import lab_canon
    out = []
    for created_at, name, value, unit in rows:
        if fence and created_at and created_at <= fence:
            continue
        if value is None:
            continue
        if (unit or "").strip():
            continue
        if lab_canon.is_dimensionless(name or ""):
            continue
        out.append((created_at, name, value))
    return out


def ref_scale_mismatch(rows):
    """Чистая логика: значение в одной шкале, а его референс — в другой.

    Признак машинный и узкий: значение выходит за границы более чем в 1.5 раза, НО
    после применения коэффициента конверсии этого же аналита границы охватывают
    значение. Совпадение двух условий сразу — не случайность: это подпись того, что
    пересчитали число и забыли диапазон.

    Проверка правдоподобия числа не ловит рассогласование шкал: значение и
    границы могут быть корректны по отдельности, но выражены в разных единицах.
    Совпадение с пересчитанным диапазоном — признак для проверки, не диагноз:
    настоящий выход за референс тоже может удовлетворять этому предикату.

    Защита от ложной тревоги — подтверждение границы другим документом
    с той же единицей. Подтверждённую границу датчик не считает ошибкой
    конверсии; клиническую интерпретацию результата он не определяет.

    Вход: rows [(id, test_name, value, ref_low, ref_high)] либо
    [(…, unit, source)] — расширенная форма включает подтверждение корпусом.
    Выход — список находок.
    """
    import lab_canon
    # Границы, подтверждённые независимыми документами:
    # {(канон, единица, 'lo'|'hi', значение): {источники}}. Сверяем ГРАНИЦЫ
    # порознь, а не полосу целиком: один бланк печатает «< 5,00» (только верх),
    # другой «0 - 5» (обе), и полосы как кортежи у них разные при одной и той же
    # верхней границе. Совпадения ОДНОЙ границы достаточно — забытая при
    # конверсии полоса числовым совпадением с чужим бланком не объясняется.
    seen: dict = {}
    for r in rows:
        if len(r) < 7:
            continue
        _rid, name, _v, lo, hi, unit, source = r[:7]
        u = lab_canon.norm_unit(unit or "")
        c = lab_canon.normalize(name or "")
        for side, val in (("lo", lo), ("hi", hi)):
            if val is not None:
                seen.setdefault((c, u, side, val), set()).add(source)

    out = []
    for r in rows:
        rid, name, value, lo, hi = r[:5]
        unit, source = (r[5], r[6]) if len(r) >= 7 else (None, None)
        if value is None or (lo is None and hi is None):
            continue
        canon = lab_canon.normalize(name or "")
        if unit is not None:
            u = lab_canon.norm_unit(unit or "")
            if any(len(seen.get((canon, u, side, val), ())) > 1
                   for side, val in (("lo", lo), ("hi", hi)) if val is not None):
                continue  # ту же границу в той же единице печатал и другой документ
        rules = lab_canon._TO_CONVENTIONAL.get(canon)
        if not rules:
            continue
        outside = ((lo is not None and value < lo / 1.5)
                   or (hi is not None and value > hi * 1.5))
        if not outside:
            continue
        for _si, (factor, _target) in rules.items():
            nlo = lo * factor if lo is not None else None
            nhi = hi * factor if hi is not None else None
            # Поля 10 % с каждой стороны: границы лабораторий не совпадают в точности,
            # и требовать попадания впритык значило бы пропускать половину находок.
            if ((nlo is None or value >= nlo * 0.9)
                    and (nhi is None or value <= nhi * 1.1)):
                out.append((rid, canon, value, lo, hi, factor))
                break
    return out


def check_lab_ref_scale():
    """Референс обязан быть в той же шкале, что значение.

    Класс, который не ловит ни один датчик значений: и число, и границы верны по
    отдельности, неверно их сочетание. Читают это safety-net и утренний бриф —
    то есть цена не «некрасиво в базе», а ложная тревога на нормальном анализе.

    §13: авто-ремонт не делаем. Пересчитать границы задним числом можно только
    зная, в какой они шкале, а это решает бланк.

    Почему assert, а не warn: промоут обязан конвертировать границы вместе
    со значением. Требование к канону — отсутствие рассогласованных шкал;
    исторические исключения по отсутствующим единицам судит отдельный датчик.
    Нулевой порог здесь выражает правило, а не размер измеренного хвоста.
    """
    import collections
    out: dict = {}
    offenders = []
    for _tag, _conn, _cur in _iter_tenant_ro():
        cols = {r[1] for r in _conn.execute("PRAGMA table_info(lab_results)")}
        if not cols:
            out[_tag] = "таблицы lab_results нет"
            continue
        # unit и source нужны для подтверждения корпусом (см. ref_scale_mismatch):
        # одна и та же граница в той же единице из ДРУГОГО документа означает, что
        # полоса нормальна, а расхождение объясняется состоянием пациента.
        rows = _conn.execute(
            "SELECT id, test_name, value, ref_low, ref_high, unit, source FROM lab_results "
            "WHERE value IS NOT NULL AND (ref_low IS NOT NULL OR ref_high IS NOT NULL)"
        ).fetchall()
        found = ref_scale_mismatch([tuple(r) for r in rows])
        out[_tag] = len(found)
        if found:
            # Имена аналитов соседа — персональные данные; ему только счётчик.
            by = collections.Counter(f[1] for f in found)
            detail = "; ".join(f"{n}×{k}" for n, k in by.most_common(6)) if _cur else ""
            offenders.append(f"[{_tag}] {len(found)} строк {detail}".rstrip())
    assert not offenders, (
        "значение и референс в РАЗНЫХ шкалах — нормальный анализ читается как "
        "патология: " + "; ".join(offenders))
    return out or None


# ── «Пусто по правде» — явное объявление, а не молчание (2026-08-09) ─────────
# Ратчет той же формы, что `producer_registry` и покрытие статусов staging:
# «покрыто либо ЯВНО ОБЪЯВЛЕНО». Без него датчик ниже вечно ругался бы на строку,
# которую невозможно починить, и превратился бы в шум — тот самый, что учит
# игнорировать звонок.
#
# Ключ — (тенант, дата, имя). Значение — ПРИЧИНА, проверяемая глазами по документу.
def _load_empty_result_declared() -> dict:
    """Объявленные «пустые по правде» строки канона — ДАННЫЕ тенантов (дата, анализ, бланк),
    поэтому живут в приватной зоне (private/empty_result_declared.yaml), не в коде
    (чтение не автором 2026-09-26: здесь лежали дата и имя бланка партнёра). Нет файла —
    объявлений нет: датчик честно покажет все строки без результата."""
    import yaml as _y
    p = Path(__file__).resolve().parent / "private" / "empty_result_declared.yaml"
    if not p.exists():
        return {}
    rows = _y.safe_load(p.read_text(encoding="utf-8")) or []
    return {(r["tenant"], str(r["date"]), r["test"]): r["why"] for r in rows}


_EMPTY_RESULT_DECLARED = _load_empty_result_declared()


def check_canon_rows_have_a_result():
    """В каноне нет строк без результата — ни числа, ни текста. Оба тенанта.

    Строка без числа и текста сообщает о наличии записи, но не о результате.
    Она может скрывать потерю при распознавании.

    ПОЧЕМУ БЕЗ BASELINE-РАТЧЕТА: результат должен быть восстановлен из источника,
    заменён с предъявлением замены либо явно объявлен отсутствующим.
    Ратчет «стало не больше, чем было» консервировал бы долг;
    требование «покрыто либо объявлено» сохраняет проверяемость каждой строки.

    C-19 (датчик на одного тенанта принят за покрытие всех) закрыт `_iter_tenant_ro`.
    Персональные значения СИБЛИНГА наружу не идут: чужому тенанту — только число,
    иначе монитор целостности сам становится кросс-тенантной утечкой.
    """
    out = {}
    for _tag, _conn, _is_current in _iter_tenant_ro():
        # СПРАШИВАЕМ, А НЕ ЛОВИМ. Широкий `except` здесь был бы тихим обработчиком:
        # он проглотил бы и «таблицы нет» (законно), и опечатку в SQL (дефект), и
        # ратчет тихих обработчиков покраснел бы справедливо. Вопрос задаётся прямо.
        if not _conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                             "AND name='lab_results'").fetchone():
            out[_tag] = "таблицы нет"
            continue
        rows = _conn.execute(
            "SELECT date, test_name FROM lab_results "
            "WHERE value IS NULL AND COALESCE(value_text,'')=''").fetchall()
        undeclared = [(d, t) for d, t in rows
                      if (_tag, d, t) not in _EMPTY_RESULT_DECLARED]
        out[_tag] = {"всего": len(rows), "необъявленных": len(undeclared)}
        if not undeclared:
            continue
        if _is_current:
            head = "; ".join(f"{d} {t}" for d, t in undeclared[:6])
            tail = f" (+ ещё {len(undeclared) - 6})" if len(undeclared) > 6 else ""
            warn(f"[{_tag}] канон: {len(undeclared)} строк без результата",
                 f"{head}{tail}. Ни числа, ни текста — строка утверждает «анализ был», "
                 f"не говоря каким. Либо вернуть перечитыванием, либо удалить с "
                 f"предъявленной заменой, либо объявить в _EMPTY_RESULT_DECLARED с "
                 f"ПРИЧИНОЙ по документу")
        else:
            # ТОЛЬКО ЧИСЛО: имена анализов сиблинга — персональные данные, и датчик,
            # печатающий их владельцу, сам является утечкой (контракт _iter_tenant_ro).
            warn(f"[{_tag}] канон: {len(undeclared)} строк без результата",
                 "детали — в ночном прогоне ТОГО тенанта; здесь только счётчик")
    return out or None


# ── Одна дата на (документ, аналит, материал) — ратчет П-9 (2026-08-09) ──────
# Ключ — (тенант, источник, имя, материал). Значение — ПРИЧИНА по документу.
#
# ПУСТ НАМЕРЕННО (09.08, вечер). Здесь стояло объявление про Insulin/blood: в одном
# документе гормон в мкМЕ/мл и ЭЛИ-Тест «АТ к инсулину» в процентах. Строка ЭЛИ-Теста
# удалена из канона — её дом `specialized_lab_results` (panel_type=autoantibodies)
# уже содержал её со своей единицей и референсом, так что переезда не было, была
# уборка дубля. Объявление снято ВМЕСТЕ со строкой: объявление живёт ровно столько,
# сколько живёт причина. Оставленное — тихое исключение, которое однажды прикроет
# уже НЕ ту строку.
_MULTIDATE_DECLARED: dict = {}


def multidate_offenders(rows) -> list:
    """Чистая часть: [(источник, имя, материал, дата)] → ключи с более чем одной датой.

    Ключ намеренно включает материал: без него моча и кровь одного аналита выглядят
    расхождением. Этот ключ без `specimen` уже дважды приводил меня к ложной тревоге.
    """
    seen: dict = {}
    for source, name, specimen, date in rows:
        seen.setdefault((source, name, specimen), set()).add(date)
    return sorted((k, sorted(v)) for k, v in seen.items() if len(v) > 1)


def check_canon_one_date_per_analyte_in_doc():
    """Внутри ОДНОГО документа один аналит в одном материале не может иметь две даты.

    Возможный сбой многостраничного бланка: продолжение таблицы не содержит
    шапки с датой забора, а в подвале напечатана дата выполнения исследования.
    Если распознавание принимает её за дату забора, строки одного измерения
    могут оказаться в каноне под разными датами.

    Класс шире одного документа: так ведёт себя любой многостраничный бланк,
    у которого продолжение не несёт шапки. Сколько таких уже в каноне — этот
    датчик и отвечает числом.

    ПОЧЕМУ НЕ УНИКАЛЬНЫЙ ИНДЕКС (ступень «нативная возможность платформы»):
    UNIQUE(source, test_name, specimen) запретил бы законный случай: разные
    методики могут дать отдельные результаты под одним именем в одном документе.
    CHECK-констрейнт межстрочный предикат не выражает вовсе.

    ГРАНИЦА: датчик видит расхождение дат, но не знает, какая верна. Он зовёт
    человека с бланком, а не выбирает сам (§13) — выбор из двух дат и есть та
    тихая потеря, от которой всё это затевалось.

    Почему warn, а не assert: расхождение может быть законным (сводный бланк за
    период), и тогда правильный ход — объявить его в `_MULTIDATE_DECLARED` с
    причиной, а не держать ночной прогон красным.
    """
    out = {}
    for _tag, _conn, _is_current in _iter_tenant_ro():
        if not _conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                             "AND name='lab_results'").fetchone():
            out[_tag] = "таблицы нет"
            continue
        rows = _conn.execute(
            "SELECT source, test_name, specimen, date FROM lab_results "
            "WHERE source LIKE 'doc:%' AND date IS NOT NULL").fetchall()
        found = multidate_offenders([tuple(r) for r in rows])
        undeclared = [(k, d) for k, d in found
                      if (_tag, k[0], k[1], k[2]) not in _MULTIDATE_DECLARED]
        out[_tag] = {"всего": len(found), "необъявленных": len(undeclared)}
        if not undeclared:
            continue
        if _is_current:
            head = "; ".join(f"{k[1]}/{k[2]} {d}" for k, d in undeclared[:6])
            tail = f" (+ ещё {len(undeclared) - 6})" if len(undeclared) > 6 else ""
            warn(f"[{_tag}] канон: {len(undeclared)} аналитов с двумя датами "
                 f"внутри одного документа",
                 f"{head}{tail}. Либо дата взята из подвала вместо шапки — тогда "
                 f"лишняя строка дубль, либо одно имя носят два разных теста. "
                 f"Решать по бланку; законный случай объявить в _MULTIDATE_DECLARED "
                 f"с ПРИЧИНОЙ")
        else:
            # ТОЛЬКО ЧИСЛО: имена анализов сиблинга — персональные данные.
            warn(f"[{_tag}] канон: {len(undeclared)} аналитов с двумя датами "
                 f"внутри одного документа",
                 "детали — в ночном прогоне ТОГО тенанта; здесь только счётчик")
    return out or None


def check_lab_unit_present():
    """Число без шкалы непригодно для клинического вывода — а канон его принимает.

    Отсутствующая единица делает числовой результат неоднозначным.
    Пример цены: условный `Magnesium` 1.0 — в мг/дл это дефицит, в ммоль/л (≈ 2.4 мг/дл)
    норма. Одно число, два противоположных вывода, и различает их только единица.

    Датчик судит ТОЛЬКО строки моложе фенса — легаси чинится отдельно (шаги B/C).
    Возврат несёт обе цифры: `после фенса` обязан быть нулём, `легаси` — убывать по
    мере ремонта. Ремонт без этого датчика чинил бы то, что продолжает натекать.

    §13, почему не авто-ремонт: подобрать единицу задним числом = решить, ЧТО измеряли.
    """
    import collections
    out: dict = {}
    for _tag, _conn, _cur in _iter_tenant_ro():   # C-19: датчик на одном тенанте — не покрытие
        # Пустой PRAGMA = таблицы НЕТ вовсе (тестовый тенант, урезанное окружение).
        # Это не слепота датчика и не повод шуметь: отсутствие канона громко ловят
        # соседи. Но и молчать нельзя — цифра уходит в ночной JSON как есть.
        cols = {r[1] for r in _conn.execute("PRAGMA table_info(lab_results)")}
        if not cols:
            out[_tag] = "таблицы lab_results нет"
            continue
        # Таблица есть, а колонки времени нет → отличить легаси от новой потери
        # нечем, и молчание было бы неотличимо от «чисто». Говорим вслух.
        if "created_at" not in cols:
            warn(f"[{_tag}] датчик пустой единицы СЛЕП: в lab_results нет created_at — "
                 "отличить легаси от новой потери нечем")
            out[_tag] = "слеп"
            continue
        rows = _conn.execute(
            "SELECT created_at, test_name, value, unit FROM lab_results"
        ).fetchall()
        fresh = unitless_offenders(rows, UNIT_REQUIRED_FENCE)
        legacy = len(unitless_offenders(rows, "")) - len(fresh)
        out[_tag] = {"после фенса": len(fresh), "легаси до фенса": legacy}
        if fresh:
            # Имена аналитов соседа — персональные данные (какие анализы сдаёт
            # партнёр). Ему — только счётчик, как в check_lab_names_not_glued.
            by = collections.Counter(name for _c, name, _v in fresh)
            warn(f"[{_tag}] канон lab_results: {len(fresh)} строк со значением, но БЕЗ "
                 f"единицы после фенса {UNIT_REQUIRED_FENCE} — число без шкалы нечитаемо",
                 "; ".join(f"{n}×{k}" for n, k in by.most_common(6)) if _cur else "")
    return out or None


_SPECIMEN_READ_STEPS = ("read_header", "read_footer", "continuation")
_SPECIMEN_STEPS = _SPECIMEN_READ_STEPS + ("unknown",)


def specimen_provenance_offenders(rows) -> dict:
    """Чистая часть датчика (как `unitless_offenders` и `ref_scale_mismatch`):
    вход — пары (specimen, specimen_source), выход — три счётчика нарушений сцепки.
    Отдельная функция, чтобы контроль не требовал ни тенанта, ни БД."""
    orphan = empty = 0
    alien = set()
    for specimen, source in rows:
        s = (specimen or "").strip()
        src = source or ""
        if s and src not in _SPECIMEN_READ_STEPS:
            orphan += 1
        if src in _SPECIMEN_READ_STEPS and not s:
            empty += 1
        if src and src not in _SPECIMEN_STEPS:
            alien.add(src)
    return {"материал без ступени": orphan, "ступень без материала": empty,
            "чужие ступени": sorted(alien)}


def check_staging_specimen_provenance():
    """Материал БЕЗ своей ступени неотличим от назначенного — а именно эта
    неразличимость и стоила 143 строк с чужим материалом.

    Замер 2026-07-31 (многостраничный бланк): шапка «Биоматериал:» стоит не на
    всех страницах; прежнее правило наследовало её безусловно и приписывало материал
    ПОСТОРОННЕЙ заявки. Отчёт по мазку
    целиком шёл как «Кровь с ЭДТА» от соседней кровяной заявки.

    Датчик стережёт СЦЕПКУ двух колонок, а не наличие материала:
      (1) есть `specimen` → обязана быть ступень чтения, не `unknown` и не NULL;
      (2) ступень чтения → обязан быть непустой `specimen`;
      (3) значение `specimen_source` — только из закрытого множества.
    Ноль здесь достижим по построению (писатель ставит обе колонки разом), поэтому
    режим assert, а не warn: датчик, у которого ноль недостижим, держат красным и
    перестают читать.

    ЧЕСТНАЯ ГРАНИЦА: сцепка не доказывает, что материал ВЕРЕН. Что «мазок из зева»
    прочитан правильно, доказывает не этот датчик, а негативный контроль
    tests/unit/test_lab_specimen.py::test_footer_wins_over_neighbour_header.
    """
    out: dict = {}
    offenders = []
    for _tag, _conn, _cur in _iter_tenant_ro():   # C-19
        cols = {r[1] for r in _conn.execute("PRAGMA table_info(lab_results_staging)")}
        if not cols:
            out[_tag] = "таблицы lab_results_staging нет"
            continue
        if "specimen_source" not in cols:
            # Миграция не прошла на этом тенанте — молчать нельзя: датчик тогда
            # зелен не потому, что чисто, а потому, что смотреть не на что.
            warn(f"[{_tag}] датчик провенанса материала СЛЕП: нет колонки specimen_source")
            out[_tag] = "слеп"
            continue
        found = specimen_provenance_offenders(_conn.execute(
            "SELECT specimen, specimen_source FROM lab_results_staging").fetchall())
        out[_tag] = found
        if any(found.values()):
            offenders.append(f"[{_tag}] " + "; ".join(f"{k}: {v}" for k, v in found.items()))
    assert not offenders, (
        "материал пробы и его провенанс разошлись — материал без ступени "
        "неотличим от назначенного по имени панели: " + "; ".join(offenders))
    return out or None


_VALUE_OPS = ("<", ">", "<=", ">=")


def censored_value_offenders(rows) -> list:
    """Чистая часть: (value, value_op) → нарушения. Оператор без числа бессмыслен,
    оператор вне закрытого множества — новый смысл, вошедший молча."""
    bad = []
    for value, op in rows:
        if not op:
            continue
        if op not in _VALUE_OPS:
            bad.append(("чужой оператор", op))
        elif value is None:
            bad.append(("оператор без значения", op))
    return bad


def check_censored_values_coherent():
    """Оператор сравнения и число живут ТОЛЬКО вместе.

    Если результат ограничен пределом обнаружения прибора, число без оператора
    ошибочно выглядит точным измерением. Абстрактная запись «< L» означает
    верхнюю границу, а не равенство L; потеря оператора искажает тренд.

    Датчик стережёт СЦЕПКУ, а не наличие оператора: строк без него большинство,
    и это нормально. Ноль достижим по построению → assert.

    ГРАНИЦА: датчик не знает, был ли оператор на бланке. Что он не потерян при
    разборе, доказывает не он, а тест распознавателя и сверка глазами.
    """
    out, offenders = {}, []
    for _tag, _conn, _cur in _iter_tenant_ro():   # C-19
        for table in ("lab_results", "lab_results_staging"):
            cols = {r[1] for r in _conn.execute(f"PRAGMA table_info({table})")}
            if not cols:
                continue
            if "value_op" not in cols:
                warn(f"[{_tag}] датчик оператора СЛЕП: в {table} нет value_op")
                out[f"{_tag}:{table}"] = "слеп"
                continue
            found = censored_value_offenders(
                _conn.execute(f"SELECT value, value_op FROM {table}").fetchall())
            out[f"{_tag}:{table}"] = len(found)
            if found:
                offenders.append(f"[{_tag}] {table}: {found[:5]}")
    assert not offenders, ("оператор сравнения и значение разошлись: " + "; ".join(offenders))
    return out or None


def check_reference_tables_not_in_canon():
    """У справочника LOINC ОДИН дом — общий `loinc.db`. Копия в каноне — ловушка.

    SQLite ищет неквалифицированное имя таблицы сначала в `main`, потом в
    присоединённых схемах. Значит стоит любой из таблиц справочника завестись
    обратно в health.db — пусть пустой, пусть от старой миграции, — и КАЖДЫЙ
    читатель молча возьмёт её вместо настоящего справочника. Сопоставление имён
    отработает на пустом словаре и вернёт «кандидатов нет» вместо кода: зелёный
    результат на неверных данных (§12), а не отказ.

    Датчик именно на СУЩЕСТВОВАНИЕ, а не на пустоту: непустая копия ещё хуже —
    она отработает правдоподобно и на устаревшей версии релиза.

    §13: авто-ремонт (DROP) не делаем — таблица могла завестись из-за отката,
    и молча удалить данные, происхождение которых неизвестно, опаснее, чем
    позвать человека.
    """
    out: dict = {}
    for _tag, _conn, _cur in _iter_tenant_ro():
        # Без try: sqlite_master есть в любой открытой БД, а нечитаемые БД
        # отсеивает сам _iter_tenant_ro. Глушитель здесь был бы не осторожностью,
        # а слепотой — датчик, который молчит при поломке, хуже отсутствующего.
        got = {r[0] for r in _conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
            "({})".format(",".join("?" * len(db.REFERENCE_TABLES))),
            list(db.REFERENCE_TABLES))}
        out[_tag] = len(got)
        if got:
            warn(f"[{_tag}] таблицы справочника завелись в КАНОНЕ: {sorted(got)}",
                 "они перехватят чтение у общего loinc.db — сопоставление "
                 "отработает на них молча. Убедись, откуда они, и убери из канона.")
    return out or None


# check("промоут не стоит (очередь staging движется)") ПЕРЕНЕСЁН в блок под _tenant_db_paths.


def check_specialized_lab_health():
    """specialized_lab_results (BL-LAB-CANON-2): целостность спец-панелей. Каждая
    строка обязана иметь panel_type и хоть какое-то значение (value ИЛИ value_text) —
    иначе пустой мусор. Единственный писатель — lab_specialized.promote_specialized.

    C-19 ЗАКРЫТ 2026-07-31: датчик шёл через `db.get_conn()`, то есть смотрел ТОЛЬКО
    базу владельца и молчал про тенант партнёра. Проверка одного из двух — это не
    покрытие, а его видимость: зелёный такого датчика ничего не говорит о второй базе.
    """
    out, total = {}, 0
    for _tag, _conn, _cur in _iter_tenant_ro():
        cols = {r[1] for r in _conn.execute("PRAGMA table_info(specialized_lab_results)")}
        if not cols:
            out[_tag] = "таблицы нет"      # не наша ось (напр. до миграции)
            continue
        bad = _conn.execute(
            "SELECT COUNT(*) FROM specialized_lab_results "
            "WHERE panel_type IS NULL OR (value IS NULL AND value_text IS NULL)").fetchone()[0]
        n = _conn.execute("SELECT COUNT(*) FROM specialized_lab_results").fetchone()[0]
        out[_tag], total = n, total + n
        if bad:
            warn(f"[{_tag}] specialized_lab_results: {bad} строк без panel_type или значения", "")
    return out or total


# check(specialized_lab_results) ПЕРЕНЕСЁН вниз: датчик пошёл через _iter_tenant_ro (C-19).


def specialized_canon_waiting(rows: list, canon_keys: set) -> dict:
    """Канон-ждущие строки спец-слоя по идентичности ИМЯ+МАТЕРИАЛ+РАЗМЕРНОСТЬ.

    rows: [(panel_type, analyte_raw, specimen, unit)]; canon_keys: множество
    (lab_canon.identity_name, specimen) строк, уже лежащих в каноне.

    Три бакета — разный смысл, разный оракул:
      • stuck — полная идентичность УЖЕ в каноне, а дубль висит → баг узнавания
        `lab_specialized._in_canon` (сравнивает имена буквально). Безопасно звать багом.
      • pending — имя сводимо, но идентичности в каноне нет: новый промоут ЛИБО
        мис-резолюция, где имя-only обмануло (моча-WBC, RDW %) → резолвер+человек, НЕ авто.
      • unresolvable — имени в каноне нет: назывной долг FU2, человека не будит.

    Идентичность и её гарды живут в ОДНОМ доме — `lab_canon.identity_name`
    (нормализация + суффикс размерности + гард голого `%` на не-безразмерном
    аналите: антитело к инсулину в % при гормоне Insulin в мкМЕ/мл на том же бланке в
    stuck не идёт). Датчик и guard промоута `_in_canon` зовут одну функцию:
    собственный ключ (normalize, spec, dimension_key) может не согласовать
    суффиксное имя с канонической формой и ошибочно назвать дубль pending.
    Общий судья идентичности устраняет расхождение датчика и промоута.
    """
    import lab_canon as LC
    import collections
    stuck = pending = unresolvable = 0
    by_panel = collections.Counter()
    for pt, raw, spec, unit in rows:
        by_panel[pt or "?"] += 1
        if LC.normalize(raw or "") not in LC.CANONICALS:
            unresolvable += 1
            continue
        ident = LC.identity_name(raw or "", unit or "")
        if ident is not None and (ident, (spec or "").strip()) in canon_keys:
            stuck += 1
        else:
            pending += 1
    return {"stuck": stuck, "pending": pending, "unresolvable": unresolvable,
            "by_panel": dict(by_panel), "total": len(rows)}


def check_specialized_canon_waiting():
    """Спец-слой — комната ожидания канона: строка с вердиктом дом=канон живёт здесь,
    пока имя не сведут, и УХОДИТ сама, когда промоут её примет
    (`lab_specialized.promote_specialized._in_canon`). Что комната реально пустеет —
    не стерёг никто.

    Красит на `stuck` — идентичность (имя+материал+размерность) УЖЕ в каноне, а дубль
    висит: узнавание _in_canon может быть слепо к эквивалентным именам.
    Сравнение только имён тоже недостаточно: материал и размерность могут
    различать самостоятельные измерения. Такие строки датчик не считает дублями.
    `pending`/`unresolvable` — работа резолвера и назывной долг FU2, человека не будят.
    §13: авто-действия нет; видимость + человек.
    """
    import lab_canon as LC
    out = {}
    for _tag, _conn, _cur in _iter_tenant_ro():
        if not _conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                             "AND name='specialized_lab_results'").fetchone():
            continue
        if not _conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                             "AND name='lab_domain_verdicts'").fetchone():
            continue
        canon_keys = set()
        for tn, sp, un in _conn.execute("SELECT test_name, specimen, unit FROM lab_results"):
            ident = LC.identity_name(tn or "", un or "")
            if ident is not None:
                canon_keys.add((ident, (sp or "").strip()))
        rows = _conn.execute(
            "SELECT s.panel_type, s.analyte_raw, s.specimen, s.unit "
            "FROM specialized_lab_results s "
            "JOIN lab_domain_verdicts v ON v.panel_type=s.panel_type "
            "WHERE v.home='canon' AND (s.analyte_canonical IS NULL OR s.analyte_canonical='')"
        ).fetchall()
        st = specialized_canon_waiting([tuple(r) for r in rows], canon_keys)
        out[_tag] = st["total"]
        if st["stuck"]:
            warn(f"[{_tag}] спец-слой: {st['stuck']} строк уже в каноне по идентичности, "
                 f"но дубль висит (узнавание _in_canon слепо к синонимам)",
                 (f"ещё {st['pending']} ждут резолвера, {st['unresolvable']} назывной долг; "
                  f"docs/how-to/lab_review_queue.md") if _cur else "")
    return out or None


# Ратчет назывного долга запрещает появление новых неизвестных имён.
# Ноль — действующее требование отсутствия долга, а не отключение датчика.
# Снижать ратчет следует после исправления словаря и проверки результата.
# Количественный/качественный тип и статус результата определяются по данным;
# их нельзя выводить из общего названия панели или пересказа её состава.
NAMING_DEBT_RATCHET = 0


def canon_naming_debt(rows, canonicals) -> list:
    """Имена строк канона из ДОКУМЕНТОВ, которых канон не знает. Чистая логика."""
    return sorted({(r[0] or "") for r in rows
                   if (r[1] or "").startswith("doc:") and (r[0] or "") not in canonicals})


def check_canon_naming_debt():
    """Назывной долг канона не растёт: строка из документа живёт под своим именем.

    Что стережётся и почему это не дубль соседнего датчика. `check_specialized_canon_waiting`
    смотрит КОМНАТУ ОЖИДАНИЯ — спец-слой, откуда строка уходит в канон, когда имя
    сведут. Он ничего не знает про строки, которые в канон УЖЕ въехали под сырым
    именем. А въехать под сырым именем можно: промоут не требует, чтобы имя было
    каноническим, и правильно делает — иначе документ с одним незнакомым аналитом
    не приехал бы целиком.

    Долг может переместиться из комнаты ожидания в канон под сырым именем.
    Тогда очередь уменьшится, но поиск по каноническому имени всё ещё
    пропустит результат. Отдельно проверяется доступность таких строк для
    трендов, консилиума, проверки свежести и семей валид-гейта.

    ПОЧЕМУ ТОЛЬКО `doc:`. Баллы опросников (`instrument:isi`, `mfsi_sf`, …) —
    законно не аналиты и канону не принадлежат (§16). Отсечка идёт по источнику,
    то есть по ДАННЫМ, а не списком имён в предикате: список разъехался бы с
    приёмом молча.

    ГРАНИЦА ЧЕСТНО. Датчик судит присутствие имени в словаре, а не правильность
    сведения: строка, сведённая к ЧУЖОМУ канону, для него зелёная. Этот класс
    стерегут снимок `normalize` и правило идентичности, не он.
    """
    out = {}
    import lab_canon as LC
    for _tag, _conn, _cur in _iter_tenant_ro():
        rows = _conn.execute("SELECT test_name, source FROM lab_results").fetchall()
        debt = canon_naming_debt([tuple(r) for r in rows], LC.CANONICALS)
        out[_tag] = len(debt)
        if len(debt) > NAMING_DEBT_RATCHET:
            fail_(f"[{_tag}] назывной долг канона вырос",
                  f"{len(debt)} имён из документов канон не знает при ратчете "
                  f"{NAMING_DEBT_RATCHET} (новые: {debt[:6]}) — строка под сырым именем "
                  f"невидима тренду, консилиуму и свежести")
    return out or None


# Регистрация — НЕ здесь: `check()` исполняет функцию немедленно, а `_iter_tenant_ro`
# определён ниже по файлу. Соседи с тем же обходом регистрируются группой после него.


def check_unrepeated_draw_not_swamping():
    """Исторический блок не перерастает текущую лабораторную картину.

    ЗАЧЕМ. labs_db.build_unrepeated_draw_context показывает аналиты, сданные
    один раз и не пересдававшиеся. Порога показа у блока намеренно нет:
    аналит покидает его при пересдаче. Однако большая панель может целиком
    выйти за окно свежести и резко увеличить историческую часть промпта.

    ВОПРОС ПОЛУЧАТЕЛЯ, НЕ СТРОИТЕЛЯ: «не заслонило ли мне прошлое настоящее». Поэтому
    мера — не байты блока, а отношение к тому, что у аналитов есть СЕГОДНЯ: сколько
    имён названо поимённо из истории против того, сколько имён вообще имеют свежий
    замер. Обе величины считаются по данным, порога-литерала нет (§ «константа =
    только физически неизменное»).

    Красное здесь не поломка кода: это приглашение решить, что делать с
    историей, если она поимённо занимает больше места, чем текущая картина.
    """
    import labs_db as _ldb
    out = {}
    cutoff = str(today - timedelta(days=_ldb.PROMPT_WINDOW_DAYS))
    for _tag, _conn, _cur in _iter_tenant_ro():
        rows = [dict(r) for r in _conn.execute(
            "SELECT test_name, date, value, value_text, unit, ref_low, ref_high "
            "FROM lab_results "
            "WHERE (source IS NULL OR source NOT LIKE 'instrument:%')")]
        if not rows:
            continue
        d = _ldb.unrepeated_draw(rows, cutoff)
        named = len(d["out_of_ref"]) + len(d["qual_positive"])
        fresh = len({r["test_name"] for r in rows if (r["date"] or "") >= cutoff})
        out[_tag] = f"{named}/{fresh}"
        if named > fresh:
            fail_(f"[{_tag}] история заслонила настоящее",
                  f"блок «сдано один раз» называет {named} аналитов поимённо при "
                  f"{fresh} имён со свежим замером — исторический срез стал больше "
                  f"текущего. Реши, что показывать: сузить отбор или признать, что "
                  f"регулярных измерений мало")
    return out or None


def literature_partner_gate(approved: int, partner_plist_exists: bool) -> str | None:
    """Чистая логика гасителя: пора ли возвращаться к партнёрскому крану литературы.

    Возвращает текст повода либо None. Оба условия обязательны и оба самогасящиеся.
    """
    if approved <= 0:
        return None
    if partner_plist_exists:
        return None
    return (f"у literature_curator появилось принятых предложений: {approved} "
            f"Условие возврата к решению владельца наступило: "
            f"партнёрский кран литературы завести можно — доставка "
            f"партнёру ПРЯМАЯ, без оператора. "
            f"Карточка: BACKLOG::BL-LIT-PARTNER-CRON-1")


def partner_tap_present(agents_dir, repo_launchd, in_container: bool,
                        name: str = "com.larry.health.literature-search.partner.plist") -> bool:
    """Заведён ли партнёрский кран. Установленный плист — где служба исполняется. В контейнере
    владельца партнёра нет (он живёт на хосте), его плисты не смонтированы: замер 01.10 —
    гаситель позвал владельца «включить?» через 4 дня после «включить» (27.09). Там судит копия
    в репо (launchd/, её кладёт тот же коммит, что заводит кран); живость крана — забота
    хостового прогона партнёра (check_literature_freshness_partner), не этого гасителя."""
    from pathlib import Path as _P
    if (_P(agents_dir) / name).exists():
        return True
    return bool(in_container) and (_P(repo_launchd) / name).exists()


def check_literature_partner_gate():
    """Отложенное решение владельца возвращается по СОБЫТИЮ, а не по сроку.

    ЧТО ОТЛОЖЕНО. Развёртывание доставки сиблингу зависит от появления
    принятых предложений literature_curator в текущей установке.
    Нулевой приём не доказывает причину отказов: её нельзя вывести из счётчика.
    Прямая доставка сиблингу требует отдельного решения владельца,
    поскольку получатель не управляет механизмом отбора.

    ПОЧЕМУ ДАТЧИК, А НЕ СТРОКА В BACKLOG. Ровно сегодня чинили класс «записка нити
    зовёт к живому фронту, которого нет»: обещание «вернёмся, когда…» без механизма
    живёт ложью столько, сколько его никто не перечитывает (рекорд в этом проекте —
    49 дней). Условие возврата машинно проверяемо, значит у него обязан быть носитель.

    САМОГАСИТСЯ ДВАЖДЫ: молчит, пока approved ноль, и молчит, если партнёрский кран
    уже заведён. Ни одно из двух состояний не требует помнить о нём человеку.
    """
    import os as _os

    import health_db as _db
    with _db.get_conn() as conn:
        approved = conn.execute(
            "SELECT count(*) FROM problem_list_proposals "
            "WHERE source='literature_curator' AND status='approved'").fetchone()[0]
    import plist_env_liveness as _pl
    present = partner_tap_present(_pl.agents_dir(), Path(__file__).parent / "launchd",
                                  _pl.in_container())
    reason = literature_partner_gate(approved, present)
    if reason:
        warn("партнёрский кран литературы: условие возврата наступило", reason)
    return {"approved": approved}


def electrophoresis_offenders(rows, tol: float = 1.5):
    """Чистая логика: электрофорез белка обязан сходиться сам с собой.

    Два инварианта, оба живут ВНУТРИ дома спец-панелей и не требуют ничего извне:
      (1) доли фракций в сумме дают 100% — иначе фракция потеряна или лишняя;
      (2) число фракций в процентах равно числу в массовых единицах — бланк печатает
          каждую фракцию дважды, и расхождение значит, что одна форма не доехала.

    Зачем именно арифметика: маршрутизация фракций держится на правиле по ИМЕНИ
    (`lab_specialized._NAME_OVERRIDE`), а имя — свойство бланка, не системы. Смени
    лаборатория написание, и правило молча перестанет узнавать фракцию: она уедет
    в биохимию крови и склеится с альбумином, измеренным другим методом. Тест
    покрывает поломку ПРАВИЛА, этот датчик — поломку БЛАНКА. Живой случай
    2026-07-30: пять фракций из шести лежали в спец-панелях, шестая — в тренде
    крови. Проценты не сходились к 100 до 2026-08-13: процентная строка альбумина
    СЪЕДАЛАСЬ слиянием реконсилятора по голому имени (обе модели
    читали строку всегда — см. lab_recognizer._key), после починки ключа и ручной
    доводки сумма = 100.0, датчик погас на живом каноне.

    Вход: rows [(date, source, analyte_raw, value, unit)] панели electrophoresis.
    Выход: [(date, source, вид, факт, ожидание)].
    """
    import collections
    pct = collections.defaultdict(list)
    mass = collections.defaultdict(list)
    for date, source, name, value, unit in rows:
        if value is None:
            continue
        key = (date, source)
        (pct if "%" in (unit or "") else mass)[key].append(float(value))
    out = []
    for key in sorted(set(pct) | set(mass)):
        p, m = pct.get(key, []), mass.get(key, [])
        if p:
            s = sum(p)
            if abs(s - 100.0) > tol:
                out.append((key[0], key[1], "сумма долей", round(s, 1), 100.0))
        # Пары проверяем, только когда обе формы вообще есть: бланк, печатающий
        # ТОЛЬКО проценты, — законный бланк, а не потеря.
        # 2026-08-13 (решение владельца, план C5 вариант 1): при СХОДЯЩЕЙСЯ сумме
        # процентов парность не судится — каждая массовая форма выводима
        # (m_i = p_i × общий белок / 100), недостающая строка не потеря информации.
        # Случай возник не теоретически: датчик границы двух домов постановил, что
        # Albumin (г/л) фракции — одна идентичность с биохимическим альбумином
        # канона (метод в идентичность не входит, ADR), строка ушла из спец-слоя,
        # и парность 6%/5г-л стала вечным WARN об исполненном вердикте соседнего
        # датчика. Потерянная доля так не маскируется: без неё сумма % ≠ 100.
        if p and m and len(p) != len(m) and abs(sum(p) - 100.0) > tol:
            out.append((key[0], key[1], "число фракций %/масса", len(p), len(m)))
    return out


def check_electrophoresis_sums():
    """Электрофорез сходится сам с собой (см. `electrophoresis_offenders`).

    Почему warn, а не assert (в отличие от датчика шкал): новая панель проходит
    staging → человек-гейт с задержкой; жёсткий отказ на этом промежутке
    приучал бы к красному. Датчик проверяет согласованность суммы фракций,
    не подменяя решение по исходному бланку.
    """
    out: dict = {}
    import lab_canon as LC
    # Периметр канон-имён фракций ВЫЧИСЛЯЕТСЯ из таблицы уточнений (§18: датчик,
    # объявляющий периметр, обязан его вычислять): базы с парой pct/conc.
    _fr_names = [f"{b}_{s}" for b, ent in LC._DIMENSION_BY_UNIT.items()
                 if set(ent[0]) == {"pct", "conc"} for s in ent[0]]
    for _tag, _conn, _cur in _iter_tenant_ro():
        cols = {r[1] for r in _conn.execute("PRAGMA table_info(specialized_lab_results)")}
        if not cols:
            continue
        rows = [tuple(r) for r in _conn.execute(
            "SELECT date, source, analyte_raw, value, unit FROM specialized_lab_results "
            "WHERE panel_type='electrophoresis'").fetchall()]
        # Дом фракций — КАНОН (решение владельца 2026-08-14); спец-слой читается
        # ДО полного переезда, чтобы у датчика не было слепого окна на миграции.
        if _fr_names:
            ph = ",".join("?" * len(_fr_names))
            rows += [tuple(r) for r in _conn.execute(
                f"SELECT date, source, test_name, value, unit FROM lab_results "
                f"WHERE test_name IN ({ph})", _fr_names).fetchall()]
        found = electrophoresis_offenders(rows)
        out[_tag] = len(found)
        for date, _src, kind, got, want in found:
            warn(f"[{_tag}] электрофорез не сходится: {kind} = {got}, ожидание {want}",
                 f"дата {date}" if _cur else "")
    return out or None


def check_promote_conflicts():
    """Измерения, которые промоут ОТКАЗЫВАЕТСЯ класть в канон, обязаны быть видны.

    Гейт расхождения делает верное: два значения под одним именем, разошедшиеся
    больше порога, он не сливает — выбирать победителя пришлось бы по порядку
    строк. Решение, напечатанное только в stdout ручной команды lab_promote.py,
    может остаться без получателя. Детект без доставки.

    Соседи этот класс не покрывают по своим предикатам:
      · check_lab_names_not_glued требует непересекающихся двусторонних
        референсов; при односторонней границе интервал не строится;
      · check_lab_canon_health смотрит канон, куда спорные строки не доехали;
      · check_promotion_backlog_stale считает только auto/gold,
        поэтому группы в статусе review требуют отдельного наблюдения.

    Почему warn, а не assert: ноль недостижим моими руками — разрешение конфликта
    требует бланка и человека. Датчик, который держат красным, перестают читать.

    §13, почему эскалация, а не авто-ремонт: безопасного авто-действия здесь нет.
    Автоматический выбор одного из конфликтующих значений мог бы потерять данные.
    Оракул — человек с исходным бланком.
    """
    import lab_promote
    import labs_db
    out: dict = {}
    for _tag, _conn, _cur in _iter_tenant_ro():
        # Наличие таблицы спрашиваем ВОПРОСОМ, а не глушителем: `except: continue`
        # молчал бы и при опечатке в SQL — датчик выключился бы сам и доложил «чисто».
        if not _conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='lab_results_staging'").fetchone():
            continue
        aliases = labs_db.get_confirmed_aliases_all(conn=_conn) or {}
        found = lab_promote.pending_conflicts(_conn, aliases)
        out[_tag] = len(found)
        if found:
            # PHI-guard: свой тенант — значения; сиблинг — только счётчик.
            det = "; ".join(
                f"{c['date']} {c['canonical']} {c['values']} стр={c['pages']}"
                for c in found[:6]) if _cur else ""
            warn(f"[{_tag}] промоут отказался слить {len(found)} групп "
                 f"(разные значения под одним именем) — нужен бланк и решение", det)
    return out or None


def check_lab_no_billing_rows():
    """Канон lab_results НЕ содержит биллинг-мусор из инвойсов (#67, 2026-07-02).
    Валютная единица = цена (не результат) → жёсткий FAIL: однозначная подпись
    утечки инвойса в лаб-пайплайн. Имена-процедуры/услуги → WARN. Датчик закрывает
    класс: строка счёта может пройти в vision через lab_reconcile как результат."""
    # step0/Tier-A: per-tenant. PHI-guard: свой — детали; сиблинг — счётчик.
    _CURRENCY = {"€", "eur", "nis", "₪", "$", "usd", "gbp", "£", "руб", "rub", "shekel"}
    import pii_census as _pc
    # Названия операций тенанта — класс treatment приватного словаря (решение владельца 30.09).
    _PROC = ("electrocardiogram", "ecg ", "pet-ct", "pet ct", "ct chest", "ct abdomen",
             "-histology", "price per", "concentrate for infu", "blood unit",
             *(t.lower() for t in _pc.literals(["treatment"])))
    out: dict = {}
    for _tag, _conn, _cur in _iter_tenant_ro():
        try:
            rows = _conn.execute("SELECT date, test_name, value, unit FROM lab_results").fetchall()
        except Exception as e:  # noqa: BLE001 — A5 22.09: законна только «нет таблицы у тенанта»; иная ошибка — находка
            if not _absent_table(e):
                warn(f"lab-billing: [{_tag}] чтение упало не из-за отсутствия таблицы",
                     f"{type(e).__name__}: {str(e)[:80]}")
            continue
        cur_bad, proc_bad = [], []
        for r in rows:
            if (r["unit"] or "").strip().lower() in _CURRENCY:
                cur_bad.append(f"{r['date']} {r['test_name']}={r['value']} {r['unit']}")
            nm = (r["test_name"] or "").lower()
            if any(p in nm for p in _PROC):
                proc_bad.append(f"{r['date']} {r['test_name']}")
        if cur_bad:
            fail_(f"[{_tag}] канон: {len(cur_bad)} строк с валютной единицей (цена из инвойса)",
                  "; ".join(cur_bad[:6]) if _cur else "")
        if proc_bad:
            warn(f"[{_tag}] канон: {len(proc_bad)} имён-процедур (инвойс-мусор?)",
                 "; ".join(proc_bad[:8]) if _cur else "")
        out[_tag] = len(rows)
    return out or None
# check("канон lab_results: нет биллинг-мусора (валюта/процедуры)") ПЕРЕНЕСЁН в блок.


def check_pgs_reference():
    """Reference-БД полигенных весов (pgs_catalog+pgs_weights) вынесена из канона
    2026-07-02. Если файл пропал/пуст — Phase H (PRS) молча посчитает 0 (attach
    не найдёт весов). Датчик закрывает тихий отказ на границе БД."""
    import pgs_reference
    p = pgs_reference.ref_db_path()
    if not p.exists():
        fail_("PGS reference-БД отсутствует", f"нет {p} — PRS сломается (no_weights)")
        return 0
    import sqlite3 as _sq
    try:
        rc = _sq.connect(f"file:{p}?mode=ro", uri=True)
        n = rc.execute("SELECT COUNT(*) FROM pgs_weights").fetchone()[0]
        cat = rc.execute("SELECT COUNT(*) FROM pgs_catalog").fetchone()[0]
        rc.close()
    except Exception as e:
        fail_("PGS reference-БД нечитаема", str(e))
        return 0
    if n == 0 or cat == 0:
        warn(f"PGS reference пуста (catalog={cat}, weights={n})", "Phase H даст no_weights")
    return n


check("PGS reference-БД на месте и населена", check_pgs_reference)


LONGITUDINAL_LABEL = "com.larry.health.longitudinal"


def _schedule_covers(label, artifact, now=None):
    """Покрывает ли след задачи launchd её последний плановый запуск по ЖИВОМУ плисту.

    Возвращает (covered, fire): covered True/False/None, None — «не судимо» (плиста нет
    или форма расписания не выводится), вызывающий обязан звенеть этим, а не молчать.

    Заменяет пороги-литералы «N дней молчания» (нить schedule-liveness, 23.09): ритм
    задачи живёт в плисте, а число рядом с кодом было вторым домом того же ритма и
    расходилось с ним молча — у mc_gap порог однажды стоял 3 дня при недельной задаче
    и кричал бы «замолк» каждую середину недели (баг Коммита3).

    `now` по умолчанию — дата модуля `today` + время часов: датчики этого файла судят
    «сегодня» через `today`, и тесты подменяют именно его."""
    import plist_env_liveness as _pl
    from datetime import datetime as _dt
    now = now or _dt.combine(today, get_now().time())
    return (_pl.artifact_covers_last_fire(label, artifact, now),
            _pl.last_scheduled_fire(label, now))


def _utc_trace(stamp):
    """Момент из SQLite `datetime('now')` (UTC без пояса) → ISO с явным +00:00 для
    `_schedule_covers`. Без пояса момент читался бы как местный и «опаздывал» на 3 ч."""
    if not stamp:
        return None
    s = str(stamp).replace(" ", "T")
    return s if ("+" in s[10:] or s.endswith("Z")) else s + "+00:00"


def _schedule_unjudged(what, label):
    """«Не знаю» звучит тем же каналом, что «не было» (§14)."""
    warn(f"{what}: живость не судима",
         f"расписание {label} не выводится из живого плиста (нет плиста или форма не "
         f"поддержана) — «не знаю» не равно «запуск был»")


def check_genome_update():
    """genome_update_agent не старше GENOME_UPDATE_ALERT_DAYS."""
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT MAX(run_date) as last_run FROM genome_update_log"
        ).fetchone()
    if not row or not row["last_run"]:
        warn("нет запусков genome_update_agent", "ни разу не запускался?")
        return None
    age_days = (today - date.fromisoformat(row["last_run"][:10])).days
    if age_days > GENOME_UPDATE_ALERT_DAYS:
        warn(f"genome_update_agent не запускался {age_days}д",
             f"последний: {row['last_run'][:10]}")
    return age_days


check("свежесть genome_update_agent", check_genome_update)


def check_daemons_alive():
    """KeepAlive-демоны должны иметь живой PID. Закрывает класс «сервис тихо умер»
    (health.api крутился в crash-loop 14 дней — виден только в launchctl list).
    Логика в daemon_liveness (Studio-only, 2-сэмпл confirm). warn, не fail.
    """
    import daemon_liveness
    try:
        down = daemon_liveness.find_down_daemons()
    except Exception as e:  # noqa: BLE001 — не роняем суит из-за launchctl
        warn("не смог проверить liveness демонов", str(e)[:120])
        return None
    if down:
        warn("демоны не запущены (KeepAlive без PID)", "; ".join(down))
    return None


check("liveness демонов (KeepAlive)", check_daemons_alive)


def check_stderr_watch_blindness():
    """Не ослеп ли сам датчик stderr. ОБЯЗАН стоять ДО check_stderr_errors.

    Мина в конструкции, названная при постройке датчика: он возвращает пусто и
    когда ошибок нет, и когда он ничего не смотрел (состояние стёрлось, плисты
    переехали, права пропали). Различить снаружи нельзя — «тихо» выглядит
    одинаково. Это буквально тот же класс, ради которого сам stderr_watch и
    построен: PID есть, работы нет (§14).

    Порядок обязателен: new_errors() ПЕРЕПИСЫВАЕТ состояние, и после него любой
    замер возраста показал бы «свежее» всегда — зелёный был бы причинён порядком
    строк, а не работой датчика (§20). Порядок стережёт
    tests/unit/test_stderr_watch.py::test_liveness_check_runs_before_error_check.
    """
    import stderr_watch
    try:
        blind = stderr_watch.blind_spots()
    except Exception as e:  # noqa: BLE001 — не роняем ночной суит из-за чтения плистов
        warn("не смог проверить слепоту stderr-датчика", str(e)[:120])
        return None
    if blind:
        warn("stderr-датчик ослеп", "; ".join(blind))
    return {"blind_spots": len(blind)}


check("слепота stderr-датчика", check_stderr_watch_blindness)


def check_stderr_errors():
    """Ошибки, ДОПИСАННЫЕ в stderr-логи джоб с прошлой проверки.

    Закрывает класс, на котором §14 честно оговаривается: PID есть, а работа мимо.
    Замер 13.09 — вотчер авто-тестов девять суток писал «Read-only file system» и
    обходил всю файловую систему вместо репозитория; у него был живой PID (KeepAlive
    перезапускал), поэтому check_daemons_alive молчал верно. Первый скан 38 джоб нашёл
    ошибки в семи логах, ни у одного из них не было читателя.

    warn, не fail: датчик судит ТЕКСТ, и похожая на ошибку строка может быть безобидной.
    Класс находки — fix (инженерная очередь), будить владельца строкой из лога нельзя.
    """
    import stderr_watch
    try:
        found = stderr_watch.new_errors()
    except Exception as e:  # noqa: BLE001 — не роняем ночной суит из-за чтения логов
        warn("не смог прочитать stderr-логи джоб", str(e)[:120])
        return None
    if found:
        warn(f"stderr джоб: новые ошибки у {len(found)} задач",
             "; ".join(f"{f['label']} (+{f['hits']}): {f['sample']}" for f in found[:5]))
    return {"jobs_with_new_errors": len(found)}


check("новые ошибки в stderr джоб", check_stderr_errors)


def check_deploy_restart_completeness():
    """Ратчет полноты рестарта: множество долгоживущих джоб == множество, которое
    перезапускает деплой-хук. Расхождение = «код доехал до диска, но не до процесса».

    §12 покрывает «код не доехал до Studio». Дыра была на шаг дальше: файл свежий,
    процесс держит старый в памяти, и все датчики зелёные. Список в хуке ведётся
    руками, поэтому стареет молча — 15.07 так укусило бота партнёра (закрыли
    вписыванием имени), 01.08 то же самое повторилось на вотчере: 16 суток старого
    кода, 19278 сообщений тенанту. Второе срабатывание одной причины за три недели —
    по §15 это вопрос об архитектуре, а не о внимательности.

    Логика в daemon_liveness (Studio-only, чистые ядра вынесены). FAIL, не warn (решение
    владельца 06.10, «делать»): warn этой проверки шесть суток никто не прочёл, пока вотчер
    партнёра жил на коде 30.09 — хук перезапускал прежнее имя службы (урок C-166). FAIL красит
    ночной монитор и идёт в ночной цикл как сбой, а не как предупреждение.
    """
    import daemon_liveness
    try:
        missing = daemon_liveness.find_unrestarted_daemons()
    except Exception as e:  # noqa: BLE001 — не роняем суит из-за launchd/плистов
        warn("не смог сверить полноту рестарта", str(e)[:120])
        return None
    if missing:
        fail_("деплой оставит джобы на СТАРОМ коде (хук их не перезапускает)",
              "; ".join(missing))
    return {"unrestarted": missing}


check("полнота рестарта после деплоя (§12)", check_deploy_restart_completeness)


# Замер 2026-08-02: launchd обслуживает ДВА каталога данных — ~/health
# (владелец) и ~/health_partner. Не-владельческий тенант ровно один.
_PARTNER_TENANTS_AT_DECISION = 1


def check_tenant_count_vs_silence_decision():
    """Счётчик под решением «молчание человека признаком не считать» (§18).

    Решение владельца 2026-08-02 сняло из подсистемы `service_trouble` целую ветку —
    периодический проход, который ловил бы замолчавшего человека. Основание: тенант
    один и живёт под одной крышей, скажет вживую. Это утверждение об ОКРУЖЕНИИ:
    верно сейчас, гасителя не имеет, станет ложным без единой правки кода.

    По §18 такое утверждение обязано нести счётчик, а не дату в комментарии. Вот он.
    Появится второй не-владельческий тенант — красное, и решение пересматривается
    ЧЕЛОВЕКОМ: автоматически включать удалённую ветку нельзя, это его выбор.

    warn, не fail: расхождение не портит данные, оно делает решение устаревшим.
    """
    import socket
    if not infra_config.is_primary():
        return None
    import plist_env_liveness
    from secrets_paths import is_owner_dir
    if plist_env_liveness.in_container():
        # Предмет — launchd ХОСТА; в контейнере его нет, и «было 1, стало 0» 30.09 ушло
        # владельцу вопросом «людей стало значительно больше». Не судим — вслух.
        import finding_identity as _fi
        warn("счётчик тенантов под решением о молчании (§18)",
             f"{_fi.NOT_JUDGED_HERE}: предмет — launchd хоста (контейнер его не видит)")
        return None
    try:
        dirs = plist_env_liveness.tenants_served()
    except Exception as e:  # noqa: BLE001 — не роняем суит из-за плистов
        warn("не смог сосчитать тенантов", str(e)[:120])
        return None
    partners = sorted(d for d in dirs if not is_owner_dir(d))
    if len(partners) != _PARTNER_TENANTS_AT_DECISION:
        warn("тенантов стало больше — решение «молчание не признак» устарело",
             f"было {_PARTNER_TENANTS_AT_DECISION}, стало {len(partners)}: {partners}. "
             f"Молчащий человек системе невидим — см. docs/explanation/service_trouble.md")
    return {"partner_tenants": len(partners)}


check("счётчик тенантов под решением о молчании (§18)", check_tenant_count_vs_silence_decision, host_only=True)


def check_plist_env_consistency():
    """WARN: owner-джоба с HEALTH_DATA_DIR в плисте, но БЕЗ него в загруженном
    окружении = стейл-плист (правили файл, не перезагрузили bootout+bootstrap).
    Под HEALTH_MULTITENANT=1 такая джоба падает на импорте (R1 fail-closed) —
    инцидент literature-search/assessment-scheduler 2026-07-05..10 (BL-MULTITENANT-1).
    Логика в plist_env_liveness (Studio-only, multitenant-gate). warn, не fail.
    """
    import plist_env_liveness
    try:
        stale = plist_env_liveness.find_stale_plist_env()
    except Exception as e:  # noqa: BLE001 — не роняем суит из-за launchctl
        warn("не смог проверить env-консистентность плистов", str(e)[:120])
        return None
    if stale:
        warn("плист новее загруженной джобы (нужен bootout+bootstrap)",
             "; ".join(stale))
    return None


check("env-консистентность launchd-плистов (мультитенант)", check_plist_env_consistency, host_only=True)


# Замер 2026-08-04 (Studio): живых health-джоб 38, копий в `launchd/` — 14, значит без
# копии 24. Ратчет только ВНИЗ: число снижается вместе с заведением копий, само вверх
# не ходит. Иначе «покрытие улучшается» тем, что джобу удалили.
# 28.09: копии двух партнёрских плистов крана литературы заведены вместе с кранами → 25 − 2 = 23.
_LAUNCHD_UNCOVERED_BASELINE = 23


def check_launchd_inventory():
    """WARN: копия плиста в репозитории разошлась с живой, либо джоб без копии стало больше.

    Почему копия, а не источник истины: launchd читает ~/Library/LaunchAgents, git её не
    инвалидирует — у копии нет гасителя, и она врёт молча (§18). Замер это подтвердил на
    первом же прогоне: из 14 копий одна уже врала (партнёрский бот без MORNING_BRIEF_GATE,
    который в живом плисте стоит с 14.07). Симлинк был бы хуже: `git push` менял бы
    расписание на диске, не перезагружая джобу — класс §12.

    Поэтому репозиторий получает не копию, а СЧЁТЧИК: датчик вычисляет живое множество
    и сверяет с тем, что копии всё-таки утверждают. Граница вслух: он не защищает 24
    непокрытые джобы — он лишает молчания двадцать пятую и ловит расхождение в тех 14,
    что есть. Studio-only: у MacBook свой набор джоб.
    """
    import plist_env_liveness as _pl
    try:
        inv = _pl.repo_plist_drift()
    except Exception as e:  # noqa: BLE001 — не роняем суит из-за плистов
        warn("не смог сверить инвентарь launchd", str(e)[:120])
        return None
    for label, keys in inv["drifted"]:
        warn(f"launchd: копия в репозитории разошлась с живой ({label})",
             f"ключи: {', '.join(keys)} — правь копию под живой плист либо удали её, "
             f"но не оставляй врать")
    n = len(inv["live_only"])
    if n > _LAUNCHD_UNCOVERED_BASELINE:
        warn(f"launchd: джоб без копии в репозитории стало больше ({n} > "
             f"{_LAUNCHD_UNCOVERED_BASELINE})",
             "новая: " + ", ".join(inv["live_only"][-3:]))
    if inv["repo_only"]:
        warn("launchd: копия есть, живой джобы нет",
             ", ".join(inv["repo_only"]) + " — надгробие или незагруженная джоба")
    return f"{n} без копии, {len(inv['drifted'])} разошлись"


check("launchd: инвентарь джоб против репозитория", check_launchd_inventory, host_only=True)


def _stamp_unreadable(what, where, value):
    """Текст находки «штамп нечитаем» (label, detail) — для `warn(*...)` в обработчике (23.09).

    До 23.09 пять датчиков живости (арбитр, reschedule, консолидация, гейт брифа) на
    нечитаемой дате молча выходили: «штамп битый» было неотличимо от «задача жива».
    Значение печатается как есть — вызывающий подставляет «…» для чужого тенанта."""
    return f"{what}: штамп нечитаем", f"{where} = {str(value)[:40]!r} — живость не судима"


def _rhythm_missed(what, where, raw_json, now_utc):
    """Первый пропуск по ритму, объявленному самим джобом бота (решение владельца 23.09:
    «общее правило» — тревога после ПЕРВОГО пропущенного запуска, без своего допуска у
    каждого датчика).

    Квитанция несёт {"every_s": N, "started_at": ISO-UTC} (jobs/scheduled._rhythm): следующий
    запуск обязан начаться не позже started_at + every_s. Возврат: момент, к которому запуск
    был должен (запуск пропущен); False — не пропущен; None — ритм не объявлен (квитанция
    старше 23.09 или битая), сказано вслух, «не знаю» ≠ «жив»."""
    import json as _json
    from datetime import datetime, timedelta, timezone as _tzu
    try:
        d = _json.loads(raw_json or "")
        every = float(d["every_s"])
        start = datetime.fromisoformat(str(d["started_at"]))
    except (ValueError, TypeError, KeyError) as e:
        warn(f"{what}: ритм не объявлен",
             f"{where}: в квитанции нет every_s/started_at ({type(e).__name__}) — живость не "
             f"судима до первого прогона джоба с объявлением ритма")
        return None
    if start.tzinfo is not None:
        start = start.astimezone(_tzu.utc).replace(tzinfo=None)
    due = start + timedelta(seconds=every)
    return due if now_utc > due else False


def check_arbiter_liveness():
    """WARN: экстрактор памяти (арбитр) отстал от СВЕЖИХ сообщений. Класс «тихая
    смерть экстрактора» — бот отвечает, но ничего не запоминает (Фаза 0, 2026-07-04).
    Оцениваем только при свежей активности (посл. user-сообщение ≤3 дней): если
    разговоров нет — тишина арбитра ожидаема, не шумим (анти-кричавший-волк).
    Heartbeat `_arbiter_last_run` бампается на каждом run_arbiter (system_config
    .updated_at, UTC, сравним с conversation_history.created_at). warn, не fail."""
    import health_db as _db
    from datetime import datetime, timedelta
    with _db.get_conn() as conn:
        row = conn.execute(
            "SELECT MAX(created_at) FROM conversation_history WHERE role='user'"
        ).fetchone()
        last_user = row[0] if row else None
        hb = conn.execute(
            "SELECT updated_at FROM system_config WHERE key='_arbiter_last_run'"
        ).fetchone()
        last_run = hb[0] if hb else None
    if not last_user:
        return None  # нет user-сообщений — нечего проверять
    try:
        t_user = datetime.fromisoformat(last_user)
    except (ValueError, TypeError):
        warn(*_stamp_unreadable("арбитр памяти", "conversation_history.created_at", last_user))
        return None
    # нет свежей активности → нечего обрабатывать, не поднимаем ложную тревогу
    if get_utcnow() - t_user > timedelta(days=3):  # time-inject: seam
        return None
    if not last_run:
        warn("арбитр памяти молчит при свежей активности",
             f"есть свежее user-сообщение ({last_user}), но арбитр ни разу не отметился")
        return None
    try:
        t_run = datetime.fromisoformat(last_run)
    except (ValueError, TypeError):
        warn(*_stamp_unreadable("арбитр памяти", "system_config._arbiter_last_run", last_run))
        return None
    # арбитр стартует с задержкой ~90с; допуск 2ч покрывает лаг и батчинг
    if t_user - t_run > timedelta(hours=2):
        warn("арбитр памяти не обработал свежие сообщения",
             f"посл. user {last_user} новее посл. запуска арбитра {last_run} (>2ч)")
    return None


check("живость арбитра памяти", check_arbiter_liveness)


def check_reschedule_liveness():
    """WARN: 12ч-job пересчёта местного пояса брифа (reschedule_local) молчит у тенанта.
    Класс «тихая смерть джоба»: при поездке бриф застынет в старом поясе, пока бот не
    рестартанёт. Heartbeat `schedule.reschedule_last_run` пишется в начале каждого
    _reschedule_local и несёт ритм джоба ({every_s, started_at}).

    С 23.09 — «тревога после первого пропуска» (решение владельца): до этого стоял литерал
    26 ч («2 цикла по 12ч + запас») и грейс «свежий старт бота». Грейс не нужен: первый
    прогон идёт через _RESCHED_FIRST_S (2 мин) после старта, а ключ существует с июля.
    Per-tenant (_iter_tenant_ro); сиблингам — без личных значений. warn, не fail."""
    now = get_utcnow()
    for tag, conn, is_current in _iter_tenant_ro():
        try:
            hb = conn.execute(
                "SELECT updated_at, value_json FROM system_config "
                "WHERE key='schedule.reschedule_last_run'"
            ).fetchone()
        except Exception as e:  # noqa: BLE001 — «нет таблицы» законно, иное — вслух
            if not _absent_table(e):
                warn(f"reschedule брифа [{tag}]: чтение упало", f"{type(e).__name__}: {str(e)[:80]}")
            continue
        if not hb:
            warn(f"reschedule брифа: тенант {tag} ни разу не отметился",
                 "нет schedule.reschedule_last_run")
            continue
        due = _rhythm_missed(f"reschedule брифа [{tag}]", "schedule.reschedule_last_run",
                             hb[1], now)
        if due:
            detail = f"посл. отметка {hb[0]}, следующий запуск был должен к {due:%d.%m %H:%M} UTC" \
                if is_current else "запуск по ритму пропущен"
            warn(f"reschedule брифа пропустил запуск: тенант {tag}", detail)
    return None


# check("живость reschedule брифа (местный tz)") ПЕРЕНЕСЁН ниже _tenant_db_paths (forward-def:
# check() исполняет fn сразу, а check_reschedule_liveness использует _iter_tenant_ro,
# определённый ниже). Инлайн-регистрация здесь давала NameError в ночном мониторе (fix diagnosis
# сессии 2026-07-17: датчик caa1a0c/BL-BRIEF-TIMING-2 забыл перенести регистрацию).


def check_consolidation_delivery_liveness():
    """WARN: делверинг-джоб disagree/supersede (run_nightly_consolidation, daily) мог тихо
    умереть → расхождения фактов с Oura и SUPERSEDE-предложения не доедут до человека
    (детект-без-доставки на уровне джоба, хвост D1). Heartbeat _consolidation_delivery_last_run
    бампается в КОНЦЕ джоба и несёт ритм ({every_s, started_at}). С 23.09 — тревога после
    первого пропуска (решение владельца); до этого — литерал 48 ч (сутки + допуск)."""
    import health_db as _db
    with _db.get_conn() as conn:
        hb = conn.execute(
            "SELECT updated_at, value_json FROM system_config "
            "WHERE key='_consolidation_delivery_last_run'"
        ).fetchone()
    if not hb:
        warn("делверинг-джоб консолидации ни разу не отметился",
             "heartbeat _consolidation_delivery_last_run отсутствует — disagree/supersede могут не доставляться")
        return None
    due = _rhythm_missed("делверинг-джоб консолидации", "_consolidation_delivery_last_run",
                         hb[1], get_utcnow())  # time-inject: seam
    if due:
        warn("делверинг-джоб консолидации пропустил запуск",
             f"последняя отметка {hb[0]}, следующий запуск был должен к {due:%d.%m %H:%M} UTC — "
             f"disagree/supersede могут не доходить до человека")
    return None


check("живость доставки консолидации/disagree", check_consolidation_delivery_liveness)


def check_promote_questions_liveness():
    """WARN: подъёмник кандидатов в вопросы мог тихо умереть.

    Зачем отдельный датчик, когда есть датчик потерь. `check_question_candidates_
    not_discarded` увидит смерть подъёмника — но только когда первый неувиденный
    кандидат пересечёт окно свежести, то есть через 45 дней. Это тот самый случай,
    ради которого §14 различает живость и корректность: датчик исхода стоит слишком
    далеко вниз по потоку, чтобы быть ПЕРВЫМ сигналом. Здесь — вход: джоб отметился
    или нет.

    Судится факт запуска, а не урожай. При нерегулярных разговорах отсутствие
    новых кандидатов допустимо; датчик на их число краснел бы на исправном
    механизме. Проверять нужно доставку и обработку доступного входа.

    Порога здесь нет (23.09): ритм объявляет сам джоб в квитанции (_rhythm_missed), тревога —
    после первого пропущенного запуска. Бот перезапускается на каждый коммит, и первый
    прогон после старта идёт через 10 минут — фактически подъём идёт чаще суток.

    Граница вслух, и это ДЫРА, а не замысел: читается БД владельца (`get_conn`), как и
    у соседа выше. Джоб при этом зарегистрирован без тенант-гейта — проверено в
    `jobs/scheduled.py`, оба бота гоняют его одинаково, — значит мёртвый подъёмник у
    партнёра этот датчик не увидит. Обход тенантов (`_iter_tenant_ro`) существует и
    сюда не заведён: расширять покрытие в одном датчике из пары, где сосед остаётся
    однотенантным, значило бы развести их молча. Долг общий, записан на пару.
    """
    import health_db as _db
    from datetime import datetime, timedelta
    with _db.get_conn() as conn:
        hb = conn.execute(
            "SELECT updated_at, value_json FROM system_config "
            "WHERE key='_promote_questions_last_run'"
        ).fetchone()
    if not hb:
        warn("подъёмник вопросов ни разу не отметился",
             "квитанции _promote_questions_last_run нет — либо джоб не запускался с "
             "момента постройки отметки, либо падает до её записи; кандидаты копятся "
             "и умрут незаданными через окно свежести")
        return None
    # С 23.09 — тревога после первого пропуска по ритму, который объявил сам джоб (решение
    # владельца «общее правило»); до этого — литерал 48 ч. Битая квитанция по-прежнему
    # вслух: _rhythm_missed говорит «ритм не объявлен — живость не судима».
    due = _rhythm_missed("подъёмник вопросов", "_promote_questions_last_run", hb[1],
                         get_utcnow())  # time-inject: seam
    if due:
        warn("подъёмник вопросов пропустил запуск",
             f"последняя отметка {hb[0]}, следующий запуск был должен к {due:%d.%m %H:%M} UTC — "
             f"кандидаты из разговора не превращаются в вопросы, и человека перестали спрашивать")
    return None


check("живость подъёмника вопросов", check_promote_questions_liveness)


def check_watchdog_liveness():
    """WARN: §14 terminus — сам uncommitted_watchdog мог тихо умереть (launchd
    выгружен / скрипт падает на импорте). Тогда стойкая грязь Studio снова молчит
    сутками (инцидент 2026-07-05). Watchdog пишет HEARTBEAT_FILE на каждом hourly-
    запуске; проверяем свежесть отметки (<120м).

    Terminus регресса «кто сторожит сторожа»: эта проверка — часть nightly
    integrity_tests, чья собственная живость доказывается ПРИСУТСТВИЕМ ночного
    Telegram-дайджеста (run_checks → triage). Пропадёт дайджест — человек заметит
    отсутствие. Дальше не рекурсируем: внешний контур замкнут на регулярный сигнал,
    отсутствие которого видит человек. Studio-only; warn, не fail."""
    import socket
    if not infra_config.is_primary():
        return None  # heartbeat пишется только на Studio
    from pathlib import Path
    from datetime import datetime, timezone
    # Дом переехал в logs/ 2026-08-03 (ISO-timestamp в каталоге секретов
    # становился «иглой» secret_guard). Легаси-путь читается, пока watchdog
    # не отметился на новом месте — иначе датчик покраснел бы на деплое.
    import os as _os
    import plist_env_liveness as _pl
    if _pl.in_container():
        # Сторож живёт на хосте (launchd), его отметка — в logs/ репозитория хоста. До 03.10
        # контейнер искал её в своём /app/logs и каждое утро писал «отсутствует» (нить
        # host-container-split). Журналы хоста видны только у владельца (HEALTH_HOST_LOGS
        # ставит install.py --owner-override); у постороннего сторожа нет — судить нечего.
        host_logs = _os.environ.get("HEALTH_HOST_LOGS")
        if not host_logs:
            return "не судимо в контейнере: сторож незакоммиченного живёт на хосте, журналов хоста здесь нет"
        hb = Path(host_logs) / "uncommitted_watchdog.heartbeat"
    else:
        hb = Path(__file__).parent / "logs" / "uncommitted_watchdog.heartbeat"
        if not hb.exists():
            hb = Path.home() / ".health_secrets" / "uncommitted_watchdog.heartbeat"
    if not hb.exists():
        warn("watchdog heartbeat отсутствует",
             "uncommitted_watchdog ни разу не отметился — launchd выгружен?")
        return None
    try:
        ts = datetime.fromisoformat(hb.read_text().strip())
    except Exception as e:  # noqa: BLE001
        warn("watchdog heartbeat нечитаем", str(e)[:120])
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    age_min = (get_now(timezone.utc) - ts).total_seconds() / 60  # time-inject: seam
    if age_min > 120:
        warn("watchdog heartbeat устарел",
             f"последний запуск {int(age_min)}м назад (>120м; hourly launchd мёртв?)")
    return None


check("живость uncommitted_watchdog (§14)", check_watchdog_liveness)


MACBOOK_SNAPSHOT_STALE_D = 3   # снимок раз в 3ч; трое суток тишины уже не «ноут поспал»


def check_macbook_uncommitted():
    """НЕЗАКОММИЧЕННАЯ РАБОТА НА MacBook — судится отсюда, по снимку, а не по опросу ноутбука.

    Дыра, которую это закрывает (найдена 2026-09-07). `uncommitted_watchdog` построен
    17.05 под класс «работу написали и не закоммитили, обнаружили через сутки
    регрессиями». С 23.05 код пишет MacBook, а Studio стал деплой-таргетом — сторож
    остался на Studio и с тех пор сторожит ДРУГОЕ (грязное дерево деплоя, тоже нужное).
    Исходный класс не был покрыт ничем, кроме суточного снимка `backup.sh`.

    Почему датчик здесь, а не на ноутбуке. У ноутбука не может быть честного сигнала
    живости: он законно спит и уезжает, и «сторож умер» от «крышка закрыта» изнутри
    неотличимо — то есть §14 нарушался бы по построению. Зато Studio всегда включён и
    уже ВИДИТ рабочее дерево MacBook: `backup.sh` кладёт его снимок в
    `refs/backups/*` и пушит сюда. Судим снимок, ноутбук ни о чём не спрашиваем.

    Оракул: свежайший снимок против своего родителя; всё, что не
    `git_facts.MACHINE_REGENERATED_FILES`, — незакоммиченная работа человека. WARN,
    класс `decide` (умолчание триажа): это не отказ механизма, а напоминание, и
    коммитить или нет — решение владельца (§13, человек — верхняя ступень).

    ГРАНИЦЫ, названные вслух:
      · Возраст снимка — часть суждения (§18): «работы нет» верно на момент снимка,
        не сейчас. Возраст печатается всегда.
      · Устаревший снимок НЕ различает «ноут выключен» и «джоба снимка мертва» —
        отсюда это неразличимо в принципе, поэтому текст называет оба чтения, а не
        выбирает удобное. Порог тем и высок: трое суток, чтобы выходные не звенели.
      · Датчик про ПОТЕРЮ и НАПОМИНАНИЕ, не про качество: он ничего не знает о том,
        хороша ли незакоммиченная работа."""
    import git_facts as _gf
    refs = _gf._git(["for-each-ref", "--sort=-committerdate",
                     "--format=%(refname) %(committerdate:short)", "refs/backups/"])
    if refs is None:
        warn("снимки рабочего дерева MacBook не читаются",
             "git for-each-ref refs/backups/ не отработал — судить о незакоммиченном нечем")
        return None
    lines = [ln for ln in refs.splitlines() if ln.strip()]
    if not lines:
        warn("снимков рабочего дерева MacBook нет ни одного",
             "backup.sh не доехал ни разу: незакоммиченная работа не защищена И не видна")
        return None
    ref, _, day = lines[0].rpartition(" ")
    import re as _re
    snap_day = date.fromisoformat(day) if _re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) else None
    age = (today - snap_day).days if snap_day else None
    if age is None or age < 0 or age > MACBOOK_SNAPSHOT_STALE_D:
        warn(f"снимок дерева MacBook устарел: {day or 'дата не читается'}",
             f"{f'{age}д назад' if age is not None else 'дата не разобрана'} "
             f"(порог {MACBOOK_SNAPSHOT_STALE_D}д). Два чтения, и отсюда они НЕ различимы: "
             f"ноутбук выключен ИЛИ джоба снимка мертва (launchd com.larry.health.backup-wip, "
             f"каждые 3ч). Пока снимка нет, незакоммиченная работа не защищена.")
        return {"snapshot": day, "age_days": age, "verdict": "снимок устарел"}
    changed = _gf._git(["diff", "--name-only", f"{ref}^", ref])
    if changed is None:
        warn("снимок MacBook не сравнивается с родителем", f"git diff {ref}^ {ref} не отработал")
        return None
    work = [f for f in changed.splitlines()
            if f.strip() and f.strip() not in _gf.MACHINE_REGENERATED_FILES]
    # СКОЛЬКО РЕПЛИК СНИМОК НЕ ВИДИТ (14.09, внешнее ревью F5). Снимок берётся с
    # главной копии, а работа нити живёт в своём дереве — туда он не заглядывает.
    # Раньше датчик в этом случае молчал «всё хорошо»; честный ответ — «про N
    # деревьев не знаю». Факт едет в сообщении коммита снимка: отсюда, со
    # Studio, деревья MacBook не перечислить.
    _msg = _gf._git(["log", "-1", "--format=%B", ref]) or ""
    _m = _re.search(r"worktrees_uncovered=(\d+)", _msg)
    uncovered = int(_m.group(1)) if _m else None
    if uncovered:
        warn(f"снимок MacBook не видит {uncovered} дерев(о/а) нитей",
             f"снимок {day} взят с ГЛАВНОЙ копии; работа в деревьях нитей "
             f"($HOME/.worktrees) в него не попадает. Это НЕ «там чисто», это "
             f"«оттуда не знаю». Незакоммиченное в деревьях не копируется осознанно: "
             f"p90 интервала между коммитами внутри нити — 11 минут (замер 16.09). "
             f"Закоммиченная работа нитей копируется — check_thread_work_has_a_second_copy.")
    if work:
        shown = ", ".join(work[:6]) + (f" … ещё {len(work) - 6}" if len(work) > 6 else "")
        warn(f"на MacBook незакоммиченная работа: {len(work)} файлов",
             f"снимок {day} ({age}д назад): {shown}. Защищена снимком, но не в main: "
             f"гейты её не судили, деплой её не видит. Закоммить или объясни себе, почему нет.")
    return {"snapshot": day, "age_days": age, "uncommitted": work,
            "worktrees_uncovered": uncovered,
            "filtered_out": [f for f in changed.splitlines()
                             if f.strip() in _gf.MACHINE_REGENERATED_FILES]}


check("незакоммиченная работа на MacBook (по снимку)", check_macbook_uncommitted, host_only=True)


def check_macbook_head_deployed():
    """КОД С MacBook НЕ ДОЕХАЛ ДО STUDIO — и об этом никто не сказал (27.09, решение владельца).

    Замер нити agent-coordination: при коммите через мост фоновый push в post-commit иногда
    не доживал; деплоя нет, записи в deploy.log нет, тревоги нет — это не падение. Судим
    отсюда по снимку `refs/backups/wip`: его родитель — HEAD главной копии MacBook. Нет этого
    коммита в main на Studio дольше часа → Studio работает на старом коде. WARN: данные не
    портятся, но каждый следующий прогон и деплой судят не ту версию (§12).
    Граница: снимок раз в 3 часа — застрявший коммит виден с задержкой до 3 ч; ноутбук, который
    спит, снимка не делает — тогда датчик молчит честно («судить не на чем» не равно «чисто»)."""
    import git_facts as _gf
    r = _gf.undeployed_head()
    if r is None:
        warn("не сверил, доехал ли код MacBook до Studio",
             "нет снимка refs/backups/wip или его родителя — судить не на чем")
        return None
    if not r["deployed"]:
        warn("код MacBook не доехал до Studio",
             f"HEAD ноутбука {r['head']} ({r['age_s'] // 3600} ч назад) отсутствует в main на Studio — "
             "Studio работает на старом коде; повтори push с MacBook (git push studio main)")
    return r


check("код MacBook доехал до Studio (по снимку)", check_macbook_head_deployed, host_only=True)


THREAD_COPY_GRACE_H = 6      # два периода снимка (backup-wip каждые 3ч), решение владельца 16.09


def threads_in_snapshot(threads_field):
    """Разбор поля `threads=` снимка MacBook — ОДИН дом формата для двух датчиков
    (копии веток нитей и нити без строки в INDEX). Возврат: (entries, broken),
    entries = list[(slug, sha, unixts)]; None = поля нет, судить не на чем."""
    if not threads_field:
        return None
    if threads_field == "нет":
        return [], []
    entries, broken = [], []
    for entry in threads_field.split(","):
        parts = entry.split(":")
        # Проверяем ФОРМУ, а не ловим исключение: обработчик без собственного
        # сигнала здесь был бы тихим (ратчет тихих обработчиков поймал первую
        # редакцию и был прав), а сигнал у битой записи один на всех — список
        # broken, который печатает вызывающий. Без try/except обработчика нет
        # вовсе, и спорить не о чем.
        if len(parts) != 3 or not parts[2].isdigit():
            broken.append(entry)
            continue
        entries.append((parts[0], parts[1], int(parts[2])))
    return entries, broken


def thread_copies_missing(threads_field, have, now_ts, grace_h=THREAD_COPY_GRACE_H):
    """Чистая/тестируемая: какие ветки нитей старше grace_h НЕ доехали сюда.

    `threads_field` — поле `threads=` из сообщения снимка: `slug:sha:unixts,...`
    либо `нет`. `have(sha) -> bool` — есть ли объект ЗДЕСЬ.
    Возврат — пара (missing, broken): missing = list[(slug, sha, age_h)],
    broken = записи, которые не разобрались. None вместо пары = судить не на чем.

    ПОЧЕМУ BROKEN ВОЗВРАЩАЕТСЯ, А НЕ ПРОГЛАТЫВАЕТСЯ. Первая редакция делала
    `continue` на неразобранной записи — тихий обработчик, и ратчет тихих
    обработчиков это поймал. Он прав по сути, а не формально: битая запись
    означает, что список веток испорчен, то есть про какую-то нить датчик НЕ
    ЗНАЕТ. Молчаливое «не знаю» здесь неотличимо от «всё доехало» — ровно тот
    класс, против которого заведён blind_sensor_is_loud.
    """
    parsed = threads_in_snapshot(threads_field)
    if parsed is None:
        return None
    entries, broken = parsed
    out = [(slug, sha, (now_ts - ts) / 3600) for slug, sha, ts in entries
           if (now_ts - ts) / 3600 >= grace_h and not have(sha)]
    return out, broken


def check_thread_work_has_a_second_copy():
    """РАБОТА НИТИ В ОДНОМ ЭКЗЕМПЛЯРЕ — вопрос ПОСТРАДАВШЕГО, не строителя.

    Что закрывает (замер 16.09). `post-commit` пушит ТОЛЬКО main — «не main →
    выход ДО push», это написано в самом хуке. Значит закоммиченная работа нити
    от первого коммита до слияния лежала на ОДНОМ диске: в момент замера так
    лежали 992 строки двух нитей (22 ч и 62 ч), и `git cat-file -e` здесь
    отвечал «нет». Риск рос ровно от соблюдения §21.

    ПОЧЕМУ СУДИМ НАЛИЧИЕ, А НЕ РАПОРТ. `backup.sh` умеет написать в лог «push
    веток нитей: OK» — это утверждение отправителя. Предикат здесь другой:
    объект ЕСТЬ У МЕНЯ (`cat-file -e`), то есть квитанция в точке потребления
    (§17, receipt_not_proxy). Отправитель сообщает только СПИСОК того, что
    должно было доехать: перечислить ветки MacBook отсюда невозможно — тот же
    предел, что у `worktrees_uncovered`, и дом у факта тот же, сообщение снимка.

    ГРАНИЦЫ ВСЛУХ:
      · отсрочка {grace} ч = два периода снимка. Нить живёт медиану 12 минут
        (замер 16.09, 18 слияний), и порог меньше периода звенел бы на каждой
        здоровой нити — §13, датчик, который выучат пролистывать;
      · снимок без поля `threads=` — это «не знаю», и молчать об этом нельзя:
        так выглядит старый `backup.sh`, а не отсутствие риска;
      · датчик ничего не говорит о ЦЕННОСТИ работы в ветке и о том, доедет ли
        она до main.
    """
    import git_facts as _gf
    msg = _gf._git(["log", "-1", "--format=%B", "refs/backups/wip"])
    if msg is None:
        warn("снимок MacBook не читается — о копиях веток нитей судить нечем",
             "git log refs/backups/wip не отработал")
        return None
    import re as _re
    m = _re.search(r"threads=(\S+)", msg)
    if not m:
        warn("снимок MacBook не несёт списка веток нитей",
             "поле threads= в сообщении снимка отсутствует: снимок сделан старым "
             "backup.sh. Это «не знаю», а не «копии есть» — работа нитей может "
             "лежать в одном экземпляре, и отсюда это неотличимо.")
        return {"threads_field": None}

    def _have(sha):
        return _gf._git(["cat-file", "-e", sha]) is not None

    missing, broken = thread_copies_missing(m.group(1), _have, time.time())
    if missing:
        shown = ", ".join(f"{s} ({a:.0f}ч)" for s, _sha, a in missing)
        warn(f"работа {len(missing)} нит(и/ей) существует в одном экземпляре: {shown}",
             "ветки есть на MacBook, копии здесь нет: push веток нитей не доехал "
             "(backup.sh, режим --wip). Пока копии нет, отказ диска MacBook уносит "
             "эту работу целиком — в main она ещё не слита.")
    if broken:
        warn(f"в списке веток нитей {len(broken)} неразобранн(ая/ых) запис(ь/ей)",
             f"{', '.join(broken[:4])} — список испорчен, значит про эти нити датчик "
             f"НЕ ЗНАЕТ, и молчание о них неотличимо от «копия есть». Формат поля "
             f"threads= пишет backup.sh: slug:sha:unixts через запятую.")
    return {"threads_field": m.group(1),
            "missing": [(s, round(a, 1)) for s, _sha, a in missing],
            "broken": broken}


check("копия работы нитей (не один экземпляр)", check_thread_work_has_a_second_copy, host_only=True)


THREAD_ORPHAN_GRACE_H = 48   # решение владельца 22.09: свежую нить не шуметь двое суток


def threads_without_index_row(threads_field, index_text, now_ts,
                              grace_h=THREAD_ORPHAN_GRACE_H):
    """Чистая/тестируемая: нити, чья ветка жива дольше grace_h, а строки в
    docs/handoff/INDEX.md нет. Возврат (orphans, broken), orphans = [(slug, age_h)];
    None = поля снимка нет. Возраст — от последнего коммита ветки (так пишет снимок)."""
    parsed = threads_in_snapshot(threads_field)
    if parsed is None:
        return None
    entries, broken = parsed
    listed = set(re.findall(r"^\| `([^`]+)` \|", index_text, flags=re.M))
    orphans = [(slug, (now_ts - ts) / 3600) for slug, _sha, ts in entries
               if slug not in listed and (now_ts - ts) / 3600 >= grace_h]
    return orphans, broken


def check_threads_have_index_row():
    """НИТЬ-СИРОТА: ветка жива, а в реестре нитей её нет — её не видит ни одна сессия.

    Что закрывает (22.09, решение владельца «вариант Б»). Ветка `dead-root-loud`
    прожила 9 дней без строки в INDEX и без записки; за это время две другие нити,
    не видя её, независимо написали ту же функцию, и её же половину сделала третья.
    Реестр — единственное место, куда смотрит новая сессия; нить вне него невидима.
    Датчик копий (выше) её видел с 16.09 (62 ч), но спрашивал о другом — «есть ли
    копия», а не «знает ли о ней кто-нибудь».

    Данные — те же, что у датчика копий: поле `threads=` снимка MacBook (ветки
    нитей отсюда перечислить нельзя). Реестр — INDEX главной копии здесь.
    Порог 48 ч: нить живёт медиану 12 минут, строку в INDEX автор пишет на
    закрытии, и свежая нить без строки — норма, а не сирота.

    ДОСТАВКА: WARN уходит в ночной триаж — разбирает агент (закрыть нить или
    дописать строку), владельцу не эскалируется: решение устранимо без него."""
    import git_facts as _gf
    msg = _gf._git(["log", "-1", "--format=%B", "refs/backups/wip"])
    if msg is None:
        warn("снимок MacBook не читается — о нитях-сиротах судить нечем",
             "git log refs/backups/wip не отработал")
        return None
    m = re.search(r"threads=(\S+)", msg)
    index_path = Path(__file__).resolve().parent / "docs" / "handoff" / "INDEX.md"
    if not m or not index_path.exists():
        warn("нити-сироты: судить нечем",
             "нет поля threads= в снимке" if not m else f"нет реестра {index_path}")
        return None
    orphans, broken = threads_without_index_row(
        m.group(1), index_path.read_text(encoding="utf-8"), time.time())
    if orphans:
        shown = ", ".join(f"{s} ({a:.0f}ч)" for s, a in orphans)
        warn(f"нить без строки в реестре — её не видит ни одна сессия: {shown}",
             "ветка жива на MacBook, в docs/handoff/INDEX.md её нет. Следующая сессия "
             "не узнает о работе и сделает её заново (22.09: три копии одной функции). "
             "Дописать строку в INDEX и LATEST — или закрыть нить thread_finish.sh.")
    if broken:
        warn(f"нити-сироты: в поле threads= {len(broken)} неразобранн(ая/ых) запис(ь/ей)",
             f"{', '.join(broken[:4])} — про эти нити датчик НЕ ЗНАЕТ")
    return {"orphans": [(s, round(a, 1)) for s, a in orphans], "broken": broken}


check("нить без строки в реестре (сирота)", check_threads_have_index_row, host_only=True)


def check_captured_files():
    """ЗАХВАТ ЧУЖОГО ФАЙЛА В КОММИТ — вопрос человеку, не вердикт машины.

    Долг BL-THREAD-GATE-1 ждал «следующего зафиксированного случая захвата» и
    ждал бы вечно: в самом коммите носителя понятия «эта сессия» нет. Признак
    лежит СНАРУЖИ — в ленте снимков рабочего дерева (внешнее ревью 14.09):
    файл приехал в коммит, будучи незакоммиченным на последнем снимке, И его
    содержимое побайтно то же, что в снимке, — значит коммиттер его не трогал.

    ДОСТАВКА, А НЕ ТОЛЬКО ДЕТЕКТ: без этой функции признак был бы механизмом,
    которого никто не читает. Исход — WARN класса decide: отличить захват от
    «моя же старая работа» машине нечем, решает человек (§13).

    ГРАНИЦЫ — в git_facts.captured_files, они замерены и под оракулом.
    """
    ПОТОЛОК = 20
    import git_facts as _gf
    коммиты = _gf._git(["log", "--format=%h", "refs/backups/wip^..HEAD"])
    if коммиты is None:
        return None  # ленты снимков нет — судить не на чем, это не «чисто»
    все = [x for x in коммиты.split("\n") if x.strip()]
    список = все[:ПОТОЛОК]
    находки = {}
    несудимо = []          # captured_files вернул None — вердикта НЕТ
    for c in список:
        поймано = _gf.captured_files(c)
        if поймано is None:
            несудимо.append(c)
        elif поймано:
            находки[c] = поймано
    if находки:
        строки = "; ".join(f"{c}: {', '.join(f)}" for c, f in находки.items())
        warn(f"похоже на захват чужого файла в коммит: {len(находки)} коммит(ов)",
             f"{строки}. Файл был незакоммичен на последнем снимке и приехал в "
             f"коммит БЕЗ единой правки — то есть автор коммита его не трогал. "
             f"Отличить захват от собственной давней работы машине нечем: "
             f"посмотри сам, твоё ли это.")
    # ── «НЕ ЗНАЮ» ГОВОРИТСЯ ВСЛУХ, ИНАЧЕ ОНО ЧИТАЕТСЯ КАК «ЧИСТО» ───────────
    # `captured_files` различает [] («чисто») и None («судить не на чем»), а
    # `if поймано:` их склеивал: обе ветки ложны. Датчик молчал одинаково и
    # когда захвата нет, и когда он его не искал. Это ровно тот класс, против
    # которого сам датчик и заведён (§18: утверждение без гасителя).
    if несудимо:
        warn(f"детектор захвата не смог судить {len(несудимо)} из {len(список)} коммит(ов)",
             f"{', '.join(несудимо)}. У снимка нет причинного предка, либо он не "
             f"в прошлом коммита. Это НЕ «чисто»: по этим коммитам вердикта нет.")
    # Срез тоже перестаёт быть тихим: молчание про хвост неотличимо от чистоты.
    if len(все) > ПОТОЛОК:
        warn(f"детектор захвата посмотрел {ПОТОЛОК} коммит(ов) из {len(все)}",
             f"Хвост в {len(все) - ПОТОЛОК} коммит(ов) не судился вовсе. "
             f"Потолок стоит ради времени прогона, но его молчание — не вердикт.")
    return {"commits_checked": len(список), "commits_total": len(все),
            "suspects": находки, "unjudged": несудимо,
            "truncated": len(все) > ПОТОЛОК}


check("захват чужого файла в коммит (по ленте снимков)", check_captured_files, host_only=True)


def check_git_hooks_executable():
    """Активный хук без бита исполнения = ВСЕ гейты выключены разом, и молча.

    Инцидент 2026-08-20: запись в `scripts/git-hooks/pre-commit` через
    смонтированную ФС сняла бит исполнения. Git сказал об этом одной строкой
    в stderr и продолжил коммитить без единой проверки. Поймал побочный hint,
    а не сторож — то есть §14 в чистом виде: полагались на защиту, которой нет.

    Здесь стережётся ДИСК той машины, где бежит монитор. Режим в git (то, что
    доедет до второй машины) стережёт tests/consistency/test_git_hooks_executable.py.
    """
    import os
    hooks = Path(__file__).parent / ".git" / "hooks"
    if not hooks.is_dir():
        raise AssertionError(f"нет каталога хуков: {hooks}")
    dead = []
    for h in sorted(hooks.iterdir()):
        if h.name.endswith(".sample") or h.is_dir():
            continue
        target = h.resolve()
        if not target.exists():
            dead.append(f"{h.name} → битый симлинк ({os.readlink(h) if h.is_symlink() else '?'})")
        elif not os.access(target, os.X_OK):
            dead.append(f"{h.name} → не исполняемый ({target})")
    if dead:
        raise AssertionError(
            "хуки мертвы, гейты не работают: " + "; ".join(dead)
            + ". Fix: chmod 755 <цель>")
    return f"{len([h for h in hooks.iterdir() if not h.name.endswith('.sample') and not h.is_dir()])} хуков живы"


check("хуки исполняемы на диске (§14)", check_git_hooks_executable, repo_only=True)


def check_dispgate_liveness():
    """§14 для гейта одноразовости (§15): он мог перестать защищать двумя способами —
    РАСПАЙКА (из pre-commit убрали вызов, новые модули едут без контракта и вердикта) и
    МОЛЧАЛИВАЯ ПОЛОМКА (вызов на месте, но проверки больше не срабатывают — класс genome
    fake-green: «крутился жив», проверял мимо). Heartbeat здесь негоден: гейт стреляет только
    когда в коммите есть НОВЫЙ .py, а длинные паузы нормальны, и «давно не срабатывал» не
    отличить от «сломан». Поэтому liveness — ПОВЕДЕНЧЕСКАЯ проба, а не отметка времени:
    подсовываем заведомого нарушителя (модуль без сайдкара и без теста) и требуем, чтобы гейт
    его назвал. Terminus §14: проверка живёт в ночном integrity, чья живость доказывается
    присутствием ночного Telegram-дайджеста; глубже не рекурсируем."""
    from pathlib import Path
    _root = Path(__file__).parent
    hook = _root / "scripts" / "git-hooks" / "pre-commit"
    if not hook.exists():
        warn("dispgate: канонический хук не найден", str(hook))
    elif "project_context dispgate --block" not in hook.read_text(encoding="utf-8"):
        fail_("dispgate распаян из pre-commit",
              "новые модули проходят без сайдкар-контракта и вердикта (§15)")
    try:
        from project_context import indexer, dispgate
        blocks, _w = dispgate.evaluate_new_modules(
            indexer.build(str(_root)),
            {"zz_liveness_probe.py": "def do_thing(x):\n    return x\n"},
            {}, {}, indexer.disposability_policy(str(_root)))
        if not blocks:
            fail_("dispgate не кусает нарушителя",
                  "модуль без сайдкара и без теста прошёл — гейт зелёный, но слепой")
    except Exception as e:  # noqa: BLE001
        warn("dispgate: поведенческая проба не выполнилась", f"{type(e).__name__}: {e}")
    return None


check("живость гейта одноразовости (§14/§15)", check_dispgate_liveness)


def check_intentgate_liveness():
    """§14 для гейта квитанции замысла (нить intent-receipts): два способа перестать защищать —
    РАСПАЙКА (вызов убран из pre-commit: артефакты закрытия едут без квитанции) и МОЛЧАЛИВАЯ
    ПОЛОМКА (вызов на месте, но правила не кусают). Heartbeat негоден: гейт стреляет только
    когда в коммите есть новый артефакт закрытия, длинные паузы нормальны. Поэтому liveness —
    ПОВЕДЕНЧЕСКАЯ проба: подсовываем судье план без секции «## Замысел» и требуем блок.
    ГРАНИЦА (реестр: intent_receipts): проба доказывает «гейт жив», НЕ «живо каждое правило
    R1-R5» — нарушителя останавливает уже R1; смерть отдельного правила ловят только целевые
    tests/unit/test_intentgate.py (та же граница, что dispgate::liveness_not_rule_coverage).
    Terminus §14: живёт в ночном integrity, чья живость доказана ночным Telegram-дайджестом."""
    from pathlib import Path
    _root = Path(__file__).parent
    hook = _root / "scripts" / "git-hooks" / "pre-commit"
    if not hook.exists():
        warn("intentgate: канонический хук не найден", str(hook))
    elif "project_context intentgate --block" not in hook.read_text(encoding="utf-8"):
        fail_("intentgate распаян из pre-commit",
              "артефакты закрытия проходят без квитанции замысла (нить intent-receipts)")
    try:
        from project_context import intentgate
        blocks, _w = intentgate.evaluate_files(
            {"plans/PLAN_zz_liveness_probe.md": "# проба\n\nплан без секции замысла\n"},
            {"self_monitoring": {"daily_integrity_check": "holds"}})
        if not blocks:
            fail_("intentgate не кусает нарушителя",
                  "план без секции «## Замысел» прошёл — гейт зелёный, но слепой")
    except Exception as e:  # noqa: BLE001
        warn("intentgate: поведенческая проба не выполнилась", f"{type(e).__name__}: {e}")
    return None


check("живость гейта квитанции замысла (§14, intent-receipts)", check_intentgate_liveness)


def check_disposability_causes():
    """§15/§13: одна и та же причина отрицательного вердикта, всплывшая в N РАЗНЫХ календарных
    недель (порог в политике, сейчас 3), — уже не инженерная деталь, а вопрос об архитектуре, и
    оракул тут человек. Рутина до порога человеку НЕ доставляется: она печатается в preflight тому,
    кто работает в подсистеме (урок в руки, не в инбокс). Сигнал ПОВТОРЯЕТСЯ, пока причина не
    помечена разобранной в project_context.json → disposability.resolved_causes; выход стоит одну
    строку, поэтому это стойкий сигнал, а не banner-blindness (§13 «эскалируй СТОЙКОЕ» — транзиентная
    причина до трёх недель не доживёт). Флуд-гейт: не больше cause_knock_max_per_run за прогон."""
    from pathlib import Path
    _root = str(Path(__file__).parent)
    try:
        from project_context import indexer
        causes = indexer.disposability_causes(_root)
        cap = indexer.disposability_policy(_root)["limits"].get("cause_knock_max_per_run") or 1
    except Exception as e:  # noqa: BLE001
        warn("одноразовость: реестр причин не прочитан", f"{type(e).__name__}: {e}")
        return None
    hot = sorted((v for v in ((c, d) for c, d in causes.items()) if v[1]["over_threshold"]),
                 key=lambda cd: -cd[1]["count"])
    for cause, d in hot[:cap]:
        warn(f"одноразовость: причина «{cause}» повторяется {d['count']} нед.",
             f"недели {', '.join(d['weeks'])}; модули {', '.join(d['modules'][:5])} — "
             f"вопрос об архитектуре, оракул человек. Разобрал → disposability.resolved_causes "
             f"в project_context.json (иначе сигнал повторится)")
    if len(hot) > cap:
        warn("одноразовость: ещё причины за порогом", f"показана {cap} из {len(hot)} (флуд-гейт)")
    return None


check("повтор причин «не одноразовый» (§15/§13)", check_disposability_causes)


MEMORY_EPISODIC_GROWTH_WARN = 1500  # активных state+question; выше — пора строить Ф4.1

def check_memory_facts_growth():
    """WARN (Ф4.3 tripwire, план «ПЕРЕСБОРКА ФАЗЫ 4»): активные ЭПИЗОДИЧЕСКИЕ
    (state+question) в memory_facts растут без retirement — обратимое истечение
    (Ф4.1) ещё не построено (gated на Ф4.5 сетку + Ф4.0 гейт). Датчик делает решение
    «отложить decay» пересматриваемым по СИГНАЛУ, а не по ощущению: перевалил порог —
    эпизодическое реально раздувает контекст/скан консолидации, пора строить lifecycle.

    Считаем ТОЛЬКО эпизодические классы: fact/preference растут легитимно и не
    истекают по времени. warn, не fail (рост — не авария). Честно: этот WARN нельзя
    погасить без постройки Ф4.1 — это отложенное обязательство, не бесплатный датчик."""
    import health_db as _db
    try:
        with _db.get_conn() as conn:
            n = conn.execute(
                "SELECT COUNT(*) FROM memory_facts "
                "WHERE valid_to IS NULL AND active=1 AND mem_class IN ('state','question')"
            ).fetchone()[0]
    except Exception as e:  # noqa: BLE001 — таблицы может не быть в урезанном окружении
        warn("не смог посчитать рост memory_facts", str(e)[:120])
        return None
    if n > MEMORY_EPISODIC_GROWTH_WARN:
        warn("эпизодическая память растёт без retirement",
             f"{n} активных state+question > {MEMORY_EPISODIC_GROWTH_WARN} — пора строить "
             f"lifecycle-истечение (Ф4.1; сперва сетка Ф4.5 + гейт Ф4.0)")
    return None


check("рост эпизодической памяти (Ф4.3 tripwire)", check_memory_facts_growth)


def check_memory_facts_invariants():
    """FAIL: инварианты memory_facts (E, долг из аудита 5 июля).
    (1) no-double-active-key: upsert обязан superseder'ить прежнюю активную версию
        ключа — двух активных на (mem_class,key,subject) быть не должно (битый upsert/
        порча). (2) R11: third_party не протекает в self-профиль (get_profile self-only).
    medical-never-deleted — код-контракт (supersede-not-delete в save_fact + unit-тест),
    не рантайм-датчик на данных (удаление = отсутствие, не сенсится чисто)."""
    # step0/Tier-A: double-active-key проверяется per-tenant (raw SQL; PHI-guard: пример-ключ
    # только своему тенанту, сиблингу — счётчик). R11 (third_party→self) пока owner-env —
    # использует memory_facts_db.get_facts/get_profile, привязанные к текущему env (долг: sibling).
    import memory_facts_db as _mf
    fails: list[str] = []
    for _tag, _conn, _cur in _iter_tenant_ro():
        try:
            dbl = _conn.execute(
                "SELECT mem_class, key, subject, COUNT(*) n FROM memory_facts "
                "WHERE valid_to IS NULL AND active=1 AND key IS NOT NULL "
                "GROUP BY mem_class, key, subject HAVING n>1").fetchall()
        except Exception as e:  # noqa: BLE001 — A5 22.09: законна только «нет таблицы у тенанта»; иная ошибка — находка
            if not _absent_table(e):
                warn(f"memory_facts: [{_tag}] чтение упало не из-за отсутствия таблицы",
                     f"{type(e).__name__}: {str(e)[:80]}")
            continue
        if dbl:
            _det = f", напр. {tuple(dbl[0])[:3]}" if _cur else ""
            fails.append(f"[{_tag}] double-active-key ({len(dbl)} наруш.){_det}")
    assert not fails, "битый upsert/supersede: " + " ; ".join(fails)
    tp = {r["value"] for r in _mf.get_facts(subject="third_party", active_only=True)}
    if tp:
        prof = _mf.get_profile()
        prof_vals = set()
        for lst in prof.values():
            prof_vals |= {x["value"] for x in lst}
        leak = tp & prof_vals
        assert not leak, f"third_party протёк в self-профиль (R11): {list(leak)[:2]}"
    return {"ok": True}
# check("инварианты memory_facts (E: no-double-active-key + R11)") ПЕРЕНЕСЁН в блок под _tenant_db_paths.


def check_temporal_class_coverage():
    """WARN: active state с temporal_class IS NULL — путь записи обошёл деривацию
    (save_fact обязан ставить класс всем state/fact, Ф1 2026-07-07). Ловит регресс-класс
    «новый писатель забыл классифицировать» — ровно то, что the-end поймал руками (схема
    жила лишь в ALTER, _ensure создавал таблицу без колонки → save_fact падал/писал NULL).
    Доставляется через triage (не в MUTE). warn, не fail."""
    import health_db as _db
    with _db.get_conn() as conn:
        try:
            n = conn.execute("SELECT COUNT(*) FROM memory_facts WHERE mem_class='state' "
                             "AND active=1 AND temporal_class IS NULL").fetchone()[0]
        except Exception as e:  # noqa: BLE001 — нет колонки/таблицы → отдельный сигнал
            warn("temporal_class не проверить", str(e)[:100])
            return None
    if n:
        warn("temporal_class не проставлен",
             f"{n} active state с NULL temporal_class — писатель обошёл деривацию Ф1")
    return None


check("покрытие temporal_class (Ф1)", check_temporal_class_coverage)


def check_clinical_kb_populated():
    """FAIL: clinical_kb ПУСТ → medical_frame вырождается в standard-рамку И assert_floor молчит →
    пациент после резекции может получить «постное» (fail-open, введён brief-neutralization Ф2 B2.2:
    рамка/пол еды читаются из clinical_kb, хардкод CONDITION_RULES/food_floor-таблицы больше не
    страхуют детерминированный путь). Сид seed_clinical_kb в init_db обёрнут в try/except — его сбой
    БЕЗ этого датчика невидим. §14: liveness критичного хранилища, fail-closed. Проверяемый тенант —
    БД процесса (ночной прогон = owner-канон); партнёр под своим монитором (долг — см. PLAN_forward)."""
    # brief-neutralization step0 (2026-07-16): проверяем КАЖДЫЙ тенант (_tenant_db_paths),
    # не только owner-канон — партнёрская вырожденная рамка/пол еды была невидима (долг закрыт).
    # Сообщение СТРУКТУРНОЕ (счётчики ОБЩЕГО git-знания, БЕЗ перс. данных) — безопасно owner-доставке.
    import sqlite3 as _sq
    bad: list[str] = []
    ok_counts: dict = {}
    for _path in _tenant_db_paths(include_current=True):
        _tag = Path(_path).parent.parent.name
        try:
            _rc = _sq.connect(f"file:{_path}?mode=ro", uri=True)
        except Exception as e:  # noqa: BLE001 — БД тенанта не открылась (нет файла/лок)
            bad.append(f"[{_tag}] clinical_kb: БД не открылась: {str(e)[:80]}")
            continue
        try:
            try:
                nc = _rc.execute("SELECT COUNT(*) FROM clinical_kb_conditions").fetchone()[0]
                nf = _rc.execute("SELECT COUNT(*) FROM clinical_kb WHERE domain='food'").fetchone()[0]
                nfl = _rc.execute("SELECT COUNT(*) FROM clinical_kb "
                                  "WHERE domain='food' AND kind='floor'").fetchone()[0]
            except Exception as e:  # noqa: BLE001 — таблиц нет → сид не прошёл, критично
                bad.append(f"[{_tag}] clinical_kb недоступен (сид не прошёл?): {str(e)[:80]}")
                continue
        finally:
            _rc.close()
        if nc <= 0:
            bad.append(f"[{_tag}] clinical_kb_conditions ПУСТ — рамка/пол еды вырождены")
        elif nf <= 0:
            bad.append(f"[{_tag}] clinical_kb food ПУСТ — рамка/каталог еды вырождены")
        elif nfl < 2:
            bad.append(f"[{_tag}] clinical_kb floor < 2 — ПОЛ БЕЗОПАСНОСТИ еды пропал: floor={nfl}")
        else:
            ok_counts = {"conditions": nc, "food": nf, "floor": nfl}
    assert not bad, "clinical_kb вырожден у тенант(ов): " + " ; ".join(bad)
    return {"ok": True, **ok_counts}


# check("clinical_kb населён…") ПЕРЕНЕСЁН ниже _tenant_db_paths (forward-def: check() исполняет fn сразу).


def check_clinical_kb_replica_fresh(conn=None):
    """FAIL: clinical_kb в БД тенанта РАЗОШЁЛСЯ с git-источником (yaml сменился, init_db не
    ре-сижен на тенанте → СТОЙКАЯ устаревшая реплика МОЛЧА — движок брифа считает по старому
    клин-знанию). Дополняет населённость: та ловит «пусто», эта — «stale». Сравнивает контент-хеши
    (seed-стабильные поля; runtime-mutable status/review исключены). §12: зелёный на не-той версии =
    ложно-зелёный, но для ДАННЫХ."""
    # brief-neutralization step0/Tier-A: per-tenant. Хеш-сравнение — структурно, без PHI.
    import clinical_kb as _ckb
    src = _ckb.source_content_hash()
    bad: list[str] = []
    # Тестовый режим: явный conn → проверяем ОДНУ переданную реплику детерминированно, без
    # обхода живого канона (иначе юнит-тест ловит стаю канона до ре-сида при смене yaml).
    if conn is not None:
        if src != _ckb.table_content_hash(conn):
            bad.append("[passed-conn] реплика разошлась с git-источником")
        assert not bad, "clinical_kb stale-реплика: " + " ; ".join(bad)
        return {"ok": True}
    for _tag, _conn, _cur in _iter_tenant_ro():
        try:
            tbl = _ckb.table_content_hash(_conn)
        except Exception as e:  # noqa: BLE001 — нет clinical_kb у тенанта
            bad.append(f"[{_tag}] clinical_kb недоступен: {str(e)[:60]}")
            continue
        if src != tbl:
            bad.append(f"[{_tag}] реплика разошлась с git-источником (ре-сид не прогнан?)")
    assert not bad, "clinical_kb stale-реплика: " + " ; ".join(bad)
    return {"ok": True}
# check("clinical_kb реплика свежа ...") ПЕРЕНЕСЁН в блок под _tenant_db_paths.


def check_frozen_relative_not_current():
    """WARN: active state с «сегодня/вчера» в ТЕКСТЕ, но НЕ классифицированное transient →
    попадёт в current-блок брифинга = рецидив инцидента 07-07 (стухшее подано как сегодняшнее).
    Ф1 (время-биндинг) + деривация («сегодня»→transient) это исключают; WARN ловит писателя
    в обход. Доставка через triage. warn, не fail."""
    import health_db as _db
    import re as _re
    rel = _re.compile(r'\b(сегодня|вчера|today|yesterday)\b', _re.I)
    with _db.get_conn() as conn:
        rows = conn.execute("SELECT id, value, temporal_class FROM memory_facts "
                            "WHERE mem_class='state' AND active=1").fetchall()
    bad = [r[0] for r in rows if r[1] and rel.search(r[1]) and (r[2] or "") != "transient"]
    if bad:
        warn("замороженное «сегодня» не в transient",
             f"{len(bad)} active state с относит. временем, но не transient (рецидив 07-07): {bad[:5]}")
    return None


check("frozen-relative → transient (Ф1/Ф2)", check_frozen_relative_not_current)


def check_db_row_regression():
    """Таблица упала >50% против пика последних бэкапов → тихое стирание данных.
    Проверка «пуста» ловит только полный ноль и пропускает частичную потерю строк.
    Логика в db_regression (Studio-only). warn, не fail.
    """
    import db_regression
    try:
        drops = db_regression.check()
    except Exception as e:  # noqa: BLE001 — не роняем суит из-за бэкапов
        warn("не смог проверить регрессию строк", str(e)[:120])
        return None
    if drops:
        warn("таблицы потеряли строки (>50% vs бэкап)", "; ".join(drops[:8]))
    return None


check("регрессия числа строк vs бэкап", check_db_row_regression)


# ── visual symptom-intake сенсоры (WP5, §14: громкие через triage) ───────────

def check_visual_orphans():
    """Целостность visual-intake: фото без кейса / кейс без фото / handed_off без
    гипотезы. Порядок записи (файл→строка БД) их не должен порождать; появились →
    баг проводки. warn через triage."""
    try:
        import visual_db
        o = visual_db.get_visual_orphans()
    except Exception as e:  # noqa: BLE001
        warn("не смог проверить visual-сирот", str(e)[:120])
        return None
    bad = {k: v for k, v in o.items() if v}
    if bad:
        warn("visual-intake: осиротевшие данные", str(bad))
    return None


check("visual-intake сироты (фото/кейс/гипотеза)", check_visual_orphans)


def check_stale_visual_cases():
    """Сторож МЕХАНИЗМА срока жизни разбора (нить symptom-ttl, 03.10), а не человека.

    До 03.10 судился сам факт «open >72ч» — и два кейса тенанта висели в отчёте
    два месяца: молчание человека выглядело поломкой, а срока жизни у кейса не было. Теперь у
    незаконченного разбора есть исход (check_visual_followups: вопрос → автозакрытие), и
    красное значит одно — механизм не сработал: open дольше visual_open_idle_days + сутки
    (вопрос не задан) или awaiting_decision дольше visual_close_after_days + сутки (не закрыт).
    Сутки — ритм джоба (run_daily 10:00)."""
    try:
        import config_db as _cfg
        import visual_db
        stuck = visual_db.get_cases_stuck(int(_cfg.get_config("visual_open_idle_days", 3)),
                                          int(_cfg.get_config("visual_close_after_days", 1)))
    except Exception as e:  # noqa: BLE001
        warn("не смог проверить застрявшие visual-кейсы", str(e)[:120])
        return None
    if stuck:
        warn("visual-intake: срок жизни разбора не сработал",
             f"{len(stuck)} кейсов: {[(s['id'], s['status']) for s in stuck[:5]]} — "
             f"проверь джоб visual_followups (symptom_intake_enabled) и его журнал")
    return None


check("visual-intake: срок жизни разбора работает", check_stale_visual_cases)


def check_visual_verdict_rate():
    """ЗАПАЗДЫВАЮЩИЙ сигнал качества elicitation: доля symptom_intake-гипотез,
    отвергнутых на терминальном вердикте. Высокая доля при достаточном N = движок
    генерирует слабые версии. Сигнал появляется только после визитов/вердиктов —
    честный предел (real-time проверки медкачества нет). warn, не fail."""
    import json as _j
    try:
        rows = db.get_memory(category="hypothesis", n=500, active_only=False)
    except Exception as e:  # noqa: BLE001
        warn("не смог проверить verdict-rate symptom_intake", str(e)[:120])
        return None
    total = rej = 0
    for r in rows:
        try:
            p = _j.loads(r["value"])
        except Exception as e:  # noqa: BLE001 — A5 22.09: битые данные — находка, не пропуск
            warn("symptom_intake: строка гипотезы — битый JSON, пропущена", f"#{r['id'] if 'id' in r.keys() else '?'}: {type(e).__name__}: {str(e)[:80]}")
            continue
        if p.get("trigger") != "symptom_intake":
            continue
        if p.get("status") in ("confirmed", "rejected"):
            total += 1
            if p.get("status") == "rejected":
                rej += 1
    if total >= 5 and rej / total > 0.6:
        warn("symptom_intake: высокая доля отвергнутых гипотез",
             f"{rej}/{total} rejected (>60%) — elicitation может генерировать слабые версии")
    return None


check("symptom_intake verdict-rate (lagging)", check_visual_verdict_rate)


def check_symptom_prompt_discipline():
    """Guard безопасной позы: промпт elicitation не потерял запрет успокоения;
    diagnosis_guard чист по symptom-файлам (§9). Ловит тихое ослабление позы."""
    try:
        p = Path(__file__).resolve().parent / "specialists" / "symptom_intake_system.txt"
        txt = p.read_text(encoding="utf-8") if p.exists() else ""
    except Exception as e:  # noqa: BLE001
        warn("не смог прочитать symptom-промпт", str(e)[:120])
        return None
    if txt and "НИКОГДА НЕ УСПОКАИВАЙ" not in txt:
        warn("symptom-intake: промпт потерял запрет успокоения",
             "безопасная поза ослаблена — вернуть запрет успокаивать")
    try:
        import diagnosis_guard
        hits = [h for h in diagnosis_guard.scan() if "symptom_intake" in h]
        if hits:
            warn("symptom-intake: диагноз-литерал в движке/промпте (§9)", "; ".join(hits[:5]))
    except Exception as e:  # noqa: BLE001 — A5 22.09: сенсор не падает, но и не молчит
        warn("symptom-intake: проверка диагноз-литералов не выполнилась", f"diagnosis_guard: {type(e).__name__}: {str(e)[:100]}")
    return None


check("symptom-intake дисциплина промпта (поза+§9)", check_symptom_prompt_discipline)


def _own_backups(db_path=None) -> list:
    """Ежедневные бэкапы тенанта этой базы, старые → новые (раскладка backup_studio.sh:
    <каталог тенанта>/backups/<имя каталога>_<дата>.db)."""
    troot = Path(db_path or db.DB_PATH).resolve().parent.parent
    bdir = troot / "backups"
    if not bdir.is_dir():
        return []
    return sorted(bdir.glob(f"{troot.name}_*.db"), key=lambda p: p.stat().st_mtime)


def check_lab_history_regression():
    """FAIL (health-критично): lab_results резко усохла vs последний бэкап — класс
    тихой потери лабораторной истории. Общий датчик регрессии с более мягкой
    доставкой не заменяет эту проверку: здесь порог 20% и уровень FAIL
    (форвардится в Telegram). Оси: строки И охват дат."""
    import sqlite3 as _sq
    # Бэкап СВОЕГО тенанта: <каталог>/backups/<имя каталога>_*.db — раскладка backup_studio.sh.
    # Сравнение с бэкапом другого тенанта создало бы ложную тревогу:
    # размеры независимых коллекций не являются временным рядом одной базы.
    baks = _own_backups()
    if not baks:
        return None  # нет бэкапа (напр. off-Studio) — не наша ось
    latest = baks[-1]
    try:
        with db.get_conn() as conn:
            cur_rows = conn.execute("SELECT COUNT(*) c FROM lab_results").fetchone()["c"]
            cur_dates = conn.execute("SELECT COUNT(DISTINCT date) c FROM lab_results").fetchone()["c"]
        bc = _sq.connect(str(latest))
        b_rows = bc.execute("SELECT COUNT(*) FROM lab_results").fetchone()[0]
        b_dates = bc.execute("SELECT COUNT(DISTINCT date) FROM lab_results").fetchone()[0]
        bc.close()
    except Exception as e:  # noqa: BLE001 — не роняем суит из-за бэкапа
        warn("не смог сверить лаб-историю с бэкапом", str(e)[:120])
        return None
    name = latest.name
    if b_rows and cur_rows < b_rows * 0.80:
        fail_("lab_results усохла vs бэкап (строки)",
              f"сейчас {cur_rows} < 80% от {b_rows} ({name}) — тихая потеря лаб-истории?")
    if b_dates and cur_dates < b_dates * 0.80:
        fail_("lab_results усохла vs бэкап (охват дат)",
              f"сейчас {cur_dates} дат < 80% от {b_dates} ({name})")
    return {"cur_rows": cur_rows, "backup_rows": b_rows,
            "cur_dates": cur_dates, "backup_dates": b_dates}


check("усыхание лаб-истории vs бэкап (FAIL)", check_lab_history_regression)


def check_effect_allele_coverage():
    """Покрытие strand-резолюции effect_allele (genome strand-fix Ф7).

    Защита от ТИХОГО РЕГРЕССА — именно он был исходным багом: поле никто не
    заполнял, а защитный рендер маскировал это. Алертим если:
      - появились варианты без effect_allele_status (ре-аннотация без backfill);
      - доля resolved обвалилась (регресс resolve_effect_allele / источника ref/alt).
    """
    # brief-neutralization step0: per-tenant (_tenant_db_paths). Сообщение СТРУКТУРНОЕ —
    # счётчики/доля покрытия, БЕЗ rsid/генотипов → приватность партнёра не течёт.
    # Регистрация — ниже _tenant_db_paths (forward-def: check() исполняет fn сразу).
    import sqlite3 as _sq
    out: dict = {}
    for _path in _tenant_db_paths(include_current=True):
        _tag = Path(_path).parent.parent.name
        try:
            _rc = _sq.connect(f"file:{_path}?mode=ro", uri=True)
        except Exception as e:  # noqa: BLE001 — A5 22.09: «freshness-чек поймает» не проверялось
            warn(f"effect_allele: [{_tag}] БД тенанта не открылась — покрытие генома не судимо",
                 f"{type(e).__name__}: {str(e)[:80]}")
            continue
        try:
            try:
                total = _rc.execute("SELECT COUNT(*) FROM genetic_variants").fetchone()[0]
            except Exception as e:  # noqa: BLE001 — A5 22.09: законна только «нет таблицы у тенанта»; иная ошибка — находка
                if not _absent_table(e):
                    warn(f"effect_allele: [{_tag}] чтение генома упало не из-за отсутствия таблицы",
                         f"{type(e).__name__}: {str(e)[:80]}")
                continue
            if not total:
                continue
            null_status = _rc.execute(
                "SELECT COUNT(*) FROM genetic_variants WHERE effect_allele_status IS NULL").fetchone()[0]
            resolved = _rc.execute(
                "SELECT COUNT(*) FROM genetic_variants WHERE effect_allele_status='resolved'").fetchone()[0]
        finally:
            _rc.close()
        if null_status > 200:
            warn(f"[{_tag}] {null_status} вариантов без effect_allele_status",
                 "ре-аннотация без backfill? backfill_effect_alleles.py")
        share = resolved / total
        if share < 0.30:
            warn(f"[{_tag}] strand-резолюция обвалилась: resolved {share:.0%}",
                 f"{resolved}/{total} (ожидаем ~60%)")
        out[_tag] = {"total": total, "resolved": resolved, "share": round(share, 3)}
    return out or None


# check("покрытие effect_allele (strand)") ПЕРЕНЕСЁН ниже _tenant_db_paths (forward-def).


def check_carrier_status_allele_coupling():
    """Сцепление carrier-статус ⟹ effect_allele NOT NULL (инвариант
    null_is_unknown_not_clean, subsystem genome_effect_allele). Нарушение —
    carrier-статус с NULL/'' effect_allele: ниже по потоку вариант грозит тихо
    стать «не носитель» = ложная чистота. Для онкопациента ложная чистота дороже
    пустоты, поэтому FAIL, не WARN."""
    # step0/Tier-A: per-tenant. PHI-guard: rsid (генотип) — только своему тенанту; сиблинг — счётчик.
    import genome_db as _gdb
    fails: list[str] = []
    for _tag, _conn, _cur in _iter_tenant_ro():
        try:
            bad = _gdb.carrier_status_null_allele_violations(_conn)
        except Exception as e:  # noqa: BLE001 — A5 22.09: законна только «нет таблицы у тенанта»; иная ошибка — находка
            if not _absent_table(e):
                warn(f"carrier-status: [{_tag}] чтение упало не из-за отсутствия таблицы",
                     f"{type(e).__name__}: {str(e)[:80]}")
            continue
        if bad:
            _det = (f"{', '.join(r['rsid'] for r in bad[:10])} — " if _cur else "")
            fails.append(f"[{_tag}] {len(bad)} carrier-статус с NULL effect_allele: "
                         f"{_det}сцепление нарушено (ложная чистота ниже по потоку)")
    assert not fails, " ; ".join(fails)
    return {"violations": 0}
# check("сцепление carrier-статус⟹effect_allele (null≠чисто)") ПЕРЕНЕСЁН в блок.


def check_cbcr_methodology_present():
    """CBCR-методология load-bearing (manifest = system-prompt генерации гипотез,
    wiki = concept-tool read_cbcr_concept) — в git и непуста. СК-1: перенесена из
    iCloud 2026-07-11 (эвикт iCloud давал тихую деградацию качества гипотез).
    Падение = гипотезы деградируют молча → FAIL. Путь — из cbcr_lookup (единый источник)."""
    import cbcr_lookup as _cl
    wiki = _cl.WIKI_DIR
    manifest = wiki.parent / "cbcr_manifest.md"
    assert manifest.exists() and manifest.stat().st_size > 1000, \
        f"CBCR manifest отсутствует/пуст: {manifest}"
    n = len(list(wiki.glob("*.md"))) if wiki.exists() else 0
    assert n >= 50, f"CBCR wiki: {n} концептов (<50) — обвал/пропажа? {wiki}"
    return {"manifest_bytes": manifest.stat().st_size, "wiki_concepts": n}


check("CBCR-методология в git (наличие+непустота)", check_cbcr_methodology_present)


def check_validation_gate_methodology_present(root=None):
    """Методология валидационного гейта load-bearing (спека = замысел подсистемы,
    на неё ссылается subsystem_intent.validation_gate.spec; fdr_harness/fdr_sweep =
    числа вердикта статистиков) — в git и непуста. СК-1: перенесена из iCloud
    2026-07-12 (эвикт делал вердикт невоспроизводимым, spec-указатель реестра повисал).
    Падение = молчаливая потеря провенанса → FAIL. Проверяет НАЛИЧИЕ+НЕПУСТОТУ, не
    исполнение (harness требует numpy/scipy; воспроизводимость среды = отдельный Ф0/§10)."""
    base = Path(root) if root else Path(__file__).resolve().parent
    spec = base / "methodology" / "signal_validation_lifecycle_SPEC.md"
    harness = base / "methodology" / "validation_gate" / "fdr_harness.py"
    sweep = base / "methodology" / "validation_gate" / "fdr_sweep.py"
    assert spec.exists() and spec.stat().st_size > 2000, f"SPEC гейта отсутствует/пуст: {spec}"
    assert harness.exists() and harness.stat().st_size > 2000, f"fdr_harness отсутствует/пуст: {harness}"
    htext = harness.read_text(encoding="utf-8")
    missing = [fn for fn in ("def by_reject", "def p_shift", "def p_oracle", "def wall") if fn not in htext]
    assert not missing, f"fdr_harness потерял функции {missing}: {harness}"
    assert sweep.exists() and sweep.stat().st_size > 500, f"fdr_sweep отсутствует/пуст: {sweep}"
    family = base / "methodology" / "validation_gate" / "signal_family.yaml"
    assert family.exists() and family.stat().st_size > 300, f"signal_family.yaml отсутствует/пуст: {family}"
    import yaml as _y
    fam = _y.safe_load(family.read_text(encoding="utf-8"))
    assert fam.get("version") and fam.get("daily_metrics") and fam.get("gate_params"), \
        f"signal_family.yaml неполон (нет version/daily_metrics/gate_params): {family}"
    protocol = base / "methodology" / "validation_gate" / "qualification_protocol.yaml"
    qual = base / "fdr_qualification.py"
    assert protocol.exists() and protocol.stat().st_size > 300, f"qualification_protocol.yaml отсутствует/пуст: {protocol}"
    assert qual.exists() and qual.stat().st_size > 500, f"fdr_qualification.py отсутствует/пуст: {qual}"
    stage_a = base / "methodology" / "validation_gate" / "fdr_stage_a.py"
    assert stage_a.exists() and stage_a.stat().st_size > 500, f"fdr_stage_a.py отсутствует/пуст: {stage_a}"
    manifest = base / "methodology" / "validation_gate" / "data_manifest.yaml"
    assert manifest.exists() and manifest.stat().st_size > 500, f"data_manifest.yaml отсутствует/пуст: {manifest}"
    return {"spec_bytes": spec.stat().st_size, "harness_bytes": harness.stat().st_size,
            "harness_fns": 4, "family_version": fam["version"], "has_qual_scaffold": True}


check("Методология валидационного гейта в git (наличие+непустота)", check_validation_gate_methodology_present)


def check_lab_path_scope(db_path=None, base=None):
    """Механизм авто-возврата lab-пути при достаточном покрытии.
    Считает аналиты с ≥ LAB_N_MIN замеров; когда их ≥ min_viable_analytes
    (из data_manifest), lab-путь можно вернуть осознанным ре-объявлением
    семьи (§12.3). НЕ падает (descoped намеренно) — сигналит через warn."""
    import sqlite3
    import yaml as _y
    root = Path(base) if base else Path(__file__).resolve().parent
    man = _y.safe_load((root / "methodology" / "validation_gate" / "data_manifest.yaml").read_text(encoding="utf-8"))
    fam = _y.safe_load((root / "methodology" / "validation_gate" / "signal_family.yaml").read_text(encoding="utf-8"))
    labs = list(fam["lab_metrics"])
    lab_n_min = int(fam["gate_params"]["lab_n_min"])
    thr = int(man["labs"]["reactivation"]["min_viable_analytes"])
    dbp = db_path or db.DB_PATH
    con = sqlite3.connect(f"file:{dbp}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT test_name, COUNT(*) FROM lab_results WHERE specimen='blood' GROUP BY test_name"
        ).fetchall()
    finally:
        con.close()
    # Свёртка ПО КАНОНУ, а не строковое IN(имена семьи): семья объявляет "CA19.9"/"Cholesterol",
    # БД хранит "CA19-9"/"Cholesterol_Total" → IN давал ноль, неотличимый от «данных нет»
    # (аудит датчиков 2026-07-25). Варианты одного аналита складываются.
    import lab_canon
    counts = {}
    for _name, _n in rows:
        _c = lab_canon.normalize(_name)
        counts[_c] = counts.get(_c, 0) + _n
    viable = sum(1 for a in labs if counts.get(lab_canon.normalize(a), 0) >= lab_n_min)
    # Отсрочка возврата — ДАННЫЕ манифеста (решение владельца с датой), не флаг в коде:
    # датчик её читает и меняет каденцию, но не судит. Битая/отсутствующая дата = нет отсрочки.
    deferred = (man["labs"]["reactivation"] or {}).get("deferred") or {}
    until = str(deferred.get("until") or "")[:10]
    return {"viable": viable, "threshold": thr, "eligible": viable >= thr, "scope": man["labs"]["scope"],
            "deferred_until": until or None, "deferred_reason": deferred.get("reason")}


def _lab_path_scope_warn():
    try:
        r = check_lab_path_scope()
    except Exception as exc:  # noqa: BLE001
        # Находка внешнего ревью 2026-07-25 (P1): раньше ЛЮБОЕ исключение глоталось молча, и
        # «БД есть, но таблицы нет» было неотличимо от «мы не на Studio». Датчик реактивации
        # переставал считаться, владелец не узнавал — сломанный smoke detector. Теперь молчим
        # ТОЛЬКО когда БД физически нет (не-Studio); всё остальное — громко.
        if not Path(str(db.DB_PATH)).exists():
            return
        warn("лаб-датчик реактивации не смог посчитать",
             f"{type(exc).__name__}: {exc} — БД на месте, но чек упал; событие возврата лаб-пути "
             f"больше НЕ вычисляется")
        return
    if r["scope"] == "descoped_pending_data" and r["eligible"]:
        until = r.get("deferred_until")
        try:
            still_deferred = bool(until) and date.fromisoformat(until) >= today
        except ValueError:
            # Битая дата — отсрочки нет и это НАЗВАНО, а не проглочено (DG-05, §7):
            # ниже уйдёт обычный ежедневный вопрос, а здесь — почему отсрочка не сработала.
            warn("отсрочка lab-пути с неразбираемой датой",
                 f"deferred.until={until!r} в data_manifest.yaml — отсрочка не действует")
            still_deferred = False
        if still_deferred:
            # Решение уже принято и датировано → недельный дайджест (класс standing по
            # подстроке «возврат отложен»), а не ежедневный вопрос на уже решённое (§13).
            warn(f"Lab-путь годен, возврат отложен до {until}",
                 f"{r['viable']} аналитов ≥ порога (нужно {r['threshold']}); "
                 f"{r.get('deferred_reason') or 'причина в data_manifest.yaml'}")
            return
        warn("Lab-путь готов к возврату в release-scope",
             f"{r['viable']} аналитов ≥ порога (нужно {r['threshold']}) — верни lab-путь "
             f"ре-объявлением семьи/манифеста (§12.3) [сигнал от {today}]")


_lab_path_scope_warn()


# ── M1: датчик дрейфа квалификации FDR-гейта (numerical conit; plan §5, решение владельца «по дрейфу») ──
# Квалификация (qualification_protocol.release_result) относится к проверенному диапазону дрейфа.
# Выход независимого эпоха-дрейфа за этот диапазон требует повторной проверки применимости.
# Дрейф вырос (новые эпохи/сильнее сдвиги) → квалификация может устареть → переморозь+переквалифицируй.
FDR_QUAL_DRIFT_AMP_WARN = 2.0    # σ: порог предупреждения; выход за диапазон квалификации требует повторной проверки


def _compute_qual_drift(daily_df, baseline_acf, epochs):
    """Чистое ядро (тестируемо БЕЗ БД): per-metric независимый эпоха-дрейф =
    разброс эпоха-средних / внутриэпоховый дневной шум (σ). Возвращает сводку + breach."""
    import numpy as _np
    import pandas as _pd
    d = daily_df.set_index("date").sort_index()

    def epoch_of(ts):
        for e in epochs:
            s, en = e["range"]
            s = _pd.Timestamp(s); en = _pd.Timestamp(en) if en else _pd.Timestamp("2100-01-01")
            if s <= ts <= en:
                return e["name"]
        return None

    d = d.assign(_ep=[epoch_of(t) for t in d.index])
    drift = {}
    for m in baseline_acf:
        if m not in d.columns:
            continue
        sub = d[[m, "_ep"]].dropna()
        if len(sub) < 60:
            continue
        gm = sub.groupby("_ep")[m].mean(); cnt = sub.groupby("_ep")[m].count()
        em = gm[cnt >= 20]
        sw = (sub[m] - sub["_ep"].map(gm)).std()
        if sw > 0 and len(em) >= 2:
            drift[m] = float(em.std() / sw)
    if not drift:
        return {"max_drift_amp": 0.0, "breach": False, "drift_thr": FDR_QUAL_DRIFT_AMP_WARN}
    mx = max(drift.values())
    return {"max_drift_amp": round(mx, 2),
            "median_drift_amp": round(float(_np.median(list(drift.values()))), 2),
            "worst_metric": max(drift, key=drift.get),
            "breach": bool(mx > FDR_QUAL_DRIFT_AMP_WARN),
            "drift_thr": FDR_QUAL_DRIFT_AMP_WARN}


def check_fdr_qualification_drift(db_path=None, base=None):
    """M1-обёртка: читает канон (read-only) + манифест-ядро, считает дрейф. НЕ падает — warn."""
    import sqlite3
    import yaml as _y
    import pandas as _pd
    root = Path(base) if base else Path(__file__).resolve().parent
    man = _y.safe_load((root / "methodology" / "validation_gate" / "data_manifest.yaml").read_text(encoding="utf-8"))
    core = man.get("twin_baseline_core") or {}
    bacf = core.get("baseline_acf") or {}
    epochs = man.get("epochs") or []
    cols = [m for m in bacf]
    if not cols:
        return {"max_drift_amp": 0.0, "breach": False, "drift_thr": FDR_QUAL_DRIFT_AMP_WARN}
    dbp = db_path or db.DB_PATH
    con = sqlite3.connect(f"file:{dbp}?mode=ro", uri=True)
    try:
        df = _pd.read_sql_query(f"SELECT date,{','.join(cols)} FROM daily_metrics ORDER BY date",
                                con, parse_dates=["date"])
    finally:
        con.close()
    return _compute_qual_drift(df, bacf, epochs)


def _fdr_qual_drift_warn():
    try:
        r = check_fdr_qualification_drift()
    except Exception as exc:  # noqa: BLE001
        # Аудит validation_gate 2026-07-26 (P2-03): раньше ЛЮБОЕ исключение глоталось молча под
        # мотивом «не-Studio». На Studio та же ветка прятала бы падение схемы/конфига/кода —
        # датчик переставал считаться и рапортовал «всё хорошо». Тот же класс, что чинили
        # у `_lab_path_scope_warn` 07-25, и тот же фикс: молчим ТОЛЬКО когда БД физически нет.
        if not Path(str(db.DB_PATH)).exists():
            return
        warn("датчик дрейфа FDR-квалификации не смог посчитаться",
             f"{type(exc).__name__}: {exc} — БД на месте, но чек упал; устаревание квалификации "
             "больше НЕ отслеживается (см. how-to M2)")
        return
    if r.get("breach"):
        warn("FDR-квалификация может устареть (эпоха-дрейф вырос)",
             f"max независимый дрейф {r['max_drift_amp']}σ (метрика {r.get('worst_metric')}) > порога "
             f"{r['drift_thr']}σ — переморозь data_manifest twin-ядро + переквалифицируй "
             f"(qualification_protocol.release_result). См. how-to M2.")


_fdr_qual_drift_warn()


# Дом списка — secrets_paths, рядом с понятием «владелец» (2026-08-12). Здесь остаётся
# имя-псевдоним: три места в этом файле и два теста читают его, а разъехавшаяся копия
# правила «что считать клоном» и была причиной того, что is_owner() принимал клон
# владельца за чужого тенанта. Импорт дешёвый: secrets_paths тянет только os и pathlib.
from secrets_paths import DEV_CLONE_MARKERS as _DEV_CLONE_MARKERS  # noqa: E402


def _tenant_db_paths(include_current: bool = True) -> list:
    """Пути health.db реальных тенантов. Предикат живёт в secrets_paths.tenant_db_paths
    (перенесён 2026-09-05, BL-DIGEST-1: второй читатель — weekly_digest); здесь — обёртка,
    добавляющая БД текущего процесса. BL-TENANT-OURA-1: ~/health_staging — не пациент."""
    import secrets_paths as _sp
    return _sp.tenant_db_paths(current=Path(db.DB_PATH) if include_current else None)


def _owner_judged_in_container(what: str):
    """Причина пропуска для датчиков ОБЩИХ следов владельца (logs/ репозитория, его плисты),
    если этот прогон — чужого тенанта на хосте, а владелец переехал в контейнер; иначе None.

    Замер 02.10 (нить tenant-run-scope): после переезда владельца 30.09 единственным судьёй
    этих следов на хосте остался ночной прогон партнёра — и он каждое утро слал оператору
    «рельса доставки мертва», «проба красная», «ночной цикл/колокол/ночные тесты молчат»
    про копию, которую никто больше не пишет. Живые следы владельца судит монитор в
    контейнере. Предикат — secrets_paths.owner_runs_elsewhere (там же метка RUNTIME)."""
    import secrets_paths as _sp
    if _sp.owner_runs_elsewhere():
        return f"{what}: владелец в контейнере — судится там, здесь его следы заморожены"
    return None


def _absent_table(exc) -> bool:
    """Единственная законная тишина per-tenant чтения — у тенанта НЕТ этой таблицы (A5, 22.09).

    До A5 восемь медицинских датчиков ловили `except Exception → continue` с комментарием
    «нет таблицы у тенанта», и под этим комментарием прятались ЛЮБЫЕ ошибки: блокировка БД,
    порча, разъехавшаяся схема (класс C-10: «нет данных» вместо «схема разошлась»). Датчик
    тогда молча пропускал тенанта, и «у него всё хорошо» было неотличимо от «его не прочли»."""
    import sqlite3 as _sq
    return isinstance(exc, _sq.OperationalError) and "no such table" in str(exc)


def _iter_tenant_ro():
    """Итератор (tag, ro-conn, is_current) по БД ВСЕХ тенантов для per-tenant integrity.
    is_current=True — БД текущего процесса (owner в ночном прогоне): ей МОЖНО детальные
    сообщения. Сиблингам (партнёр) — ТОЛЬКО структурные счётчики без перс. значений
    (иначе монитор целостности сам = cross-tenant утечка). row_factory=Row (модули ждут
    именованный доступ). Connect-ошибка → пропуск (freshness-чек ловит отсутствие)."""
    import sqlite3 as _sq
    _cur = Path(db.DB_PATH).resolve()
    for _p in _tenant_db_paths(include_current=True):
        _tag = Path(_p).parent.parent.name
        try:
            _conn = _sq.connect(f"file:{_p}?mode=ro", uri=True)
            _conn.row_factory = _sq.Row
        except Exception:  # noqa: BLE001 — БД тенанта не открылась
            continue
        try:
            yield _tag, _conn, (Path(_p).resolve() == _cur)
        finally:
            _conn.close()


def check_weekly_digest_delivered():
    """Нить weekly-digest (2026-09-05). Понедельник: файл дайджеста ПРОШЛОЙ недели есть и
    несёт текст, и у КАЖДОГО тенанта system_config.weekly_digest.last_sent_week == та неделя.
    Иначе FAIL: генератор (launchd сб 22:00) или outbox-читатель бота умерли тихо, либо
    гейт заблокировал и алерт оператору не дошёл. Не-понедельник → пропуск (одна проверка
    в неделю, после окна доставки вс 09:00–24:00). Сиблингам — только структурный факт
    «метка есть/нет», без текста."""
    from datetime import timedelta as _td
    today = get_today()
    if today.isoweekday() != 1:
        return {"skipped": "не понедельник"}
    import weekly_digest as _wd
    from datetime import datetime as _dtm
    last_week = _wd.current_week(_dtm.combine(today - _td(days=1), _dtm.min.time()))
    d = _wd.read_digest(last_week)
    # Две разные причины одного «файла нет» (digest-container 05.10): папки нет у ЭТОГО читателя —
    # он не видит генератора вовсе (контейнер без тома); папка есть, файла нет — генератор не бежал.
    assert d is not None, (f"нет файла дайджеста {last_week}: " + (
        "генератор не бежал (launchd weekly-digest на хосте)" if _wd.OUT_DIR.is_dir() else
        f"у этого читателя нет папки {_wd.OUT_DIR} — генератор живёт на хосте, том не проброшен "
        "(контейнер владельца: scripts/install.py --owner-override)"))
    assert d.get("text"), (f"дайджест {last_week} без текста: gate={d.get('gate', {}).get('kind')} "
                          f"— заблокирован, тенанты ничего не получили")
    missing = []
    for tag, conn, _cur in _iter_tenant_ro():
        try:
            row = conn.execute("SELECT value_text FROM system_config WHERE key='weekly_digest.last_sent_week'").fetchone()
        except Exception as e:  # noqa: BLE001 — нет таблицы у тенанта = тоже «не доставлен»
            warn(f"weekly_digest: system_config тенанта {tag} не читается", type(e).__name__)
            row = None
        if not row or row[0] != last_week:
            missing.append(tag)
    assert not missing, (f"дайджест {last_week} не доставлен тенантам: {missing} "
                         f"(outbox-читатель бота / вердикт-файл; docs/how-to/weekly_digest.md)")
    return {"week": last_week, "delivered_to": "all"}


def check_tenant_dbs_reachable():
    """Мета-датчик против ТИХОЙ УСЕЧЁНКИ (RST, partner-integrity 2026-07-17). Ловушка:
    _iter_tenant_ro при connect-ошибке МОЛЧА пропускает тенанта → любой per-tenant чек
    пройдёт зелёным, осмотрев ТОЛЬКО owner = ложно-зелёный для партнёра. Здесь: КАЖДАЯ
    обнаруженная тенант-БД обязана открыться RO + иметь daily_metrics. Недостижимая/битая-
    на-открытии БД → ЯВНЫЙ fail поверх тихого skip во всех per-tenant чеках. Сообщение
    структурное ([tag]), owner-доставка безопасна. «БД удалена целиком» glob не вернёт —
    поэтому ожидаемые тенанты берутся ещё и из плистов launchd (27.09, BL-BRIEF-TENANT-REGISTRY-1):
    джоба, объявившая HEALTH_DATA_DIR, помнит тенанта, которого на диске уже нет."""
    import sqlite3 as _sq
    import plist_env_liveness as _pel
    bad: list[str] = [f"[{Path(d).name}] launchd обслуживает тенанта, а его health.db нет"
                      for d in _pel.missing_tenant_dbs(_pel.tenants_served())]
    for _p in _tenant_db_paths(include_current=True):
        _tag = Path(_p).parent.parent.name
        try:
            _c = _sq.connect(f"file:{_p}?mode=ro", uri=True)
            try:
                _c.execute("SELECT 1 FROM daily_metrics LIMIT 1").fetchone()
            finally:
                _c.close()
        except Exception as e:  # noqa: BLE001 — недостижимая/битая тенант-БД = явный fail
            bad.append(f"[{_tag}] тенант-БД недостижима/без daily_metrics: {str(e)[:80]}")
    assert not bad, "tenant-БД недостижимы: " + " ; ".join(bad)
    return {"ok": True, "tenants": len(_tenant_db_paths(include_current=True))}


def check_canon_normal_flag_matches_reference():
    """«Норма» не противоречит напечатанному референсу. Оба тенанта (волна 4, 27.09).

    Если распознаватель подставляет N вместо отсутствующего флага, «норма»
    может противоречить значению и референсу той же строки. Флаг машиной
    не вычисляется; датчик только обнаруживает это противоречие.
    Сиблингу наружу — только число (C-19)."""
    bad = {}
    for _tag, _conn, _is_current in _iter_tenant_ro():
        if not _conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                             "AND name='lab_results'").fetchone():
            continue
        n = _conn.execute(
            "SELECT COUNT(*) FROM lab_results WHERE UPPER(COALESCE(status,'')) IN ('N','NORMAL') "
            "AND value IS NOT NULL AND ((ref_low IS NOT NULL AND value < ref_low) "
            "OR (ref_high IS NOT NULL AND value > ref_high))").fetchone()[0]
        if n:
            bad[_tag] = n
    if bad:
        warn("канон: «норма» вне напечатанного референса (класс BL-LAB-FLAG-ABSENCE-1)",
             "; ".join(f"[{t}] {n}" for t, n in bad.items())
             + " — пометку N выдумал распознаватель? снять статус актом с журналом")
    return bad


check("канон: «норма» не противоречит референсу бланка", check_canon_normal_flag_matches_reference)


def check_partner_epochs_ready():
    """Готовность СИБЛИНГ-тенанта к пер-тенант стратификации A/D.
    Триггер возврата (data_manifest.partner_rollout): накоплено ≥ min_daily_days
    daily_metrics И ≥ min_periods определённых периода → можно оценить эпохи.
    WARN возвращает владельцу решение о запуске Группы 3; FAIL нет, поскольку
    этот путь намеренно исключён из действующего scope.

    C-19: петля по всем сиблингам. Текущий тенант (is_current) пропущен:
    этот триггер относится к развёртыванию у сиблингов. Сообщение структурное
    ([tag] + счётчики дней/периодов, без имён и дат периодов)."""
    import yaml as _y
    root = Path(__file__).resolve().parent
    pr = _y.safe_load(
        (root / "methodology" / "validation_gate" / "data_manifest.yaml").read_text(encoding="utf-8")
    ).get("partner_rollout")
    if not pr:
        return {"checked": 0}   # стенза не объявлена — стеречь нечего
    min_days = int(pr["min_daily_days"]); min_periods = int(pr["min_periods"])
    checked = ready = 0
    for tag, conn, is_current in _iter_tenant_ro():
        if is_current:
            continue   # текущий тенант не входит в периметр sibling-rollout
        checked += 1
        try:
            days = conn.execute("SELECT COUNT(DISTINCT date) FROM daily_metrics").fetchone()[0]
            nper = conn.execute("SELECT COUNT(*) FROM periods").fetchone()[0]
        except Exception as e:  # noqa: BLE001 — A5 22.09: законна только «нет таблицы у тенанта»; иная ошибка — находка
            if not _absent_table(e):   # битую БД ловит и check_tenant_dbs_reachable, но битую ТАБЛИЦУ — нет
                warn(f"partner-epochs: [{_tag}] чтение упало не из-за отсутствия таблицы",
                     f"{type(e).__name__}: {str(e)[:80]}")
            continue
        if days >= min_days and nper >= min_periods:
            ready += 1
            warn(f"Партнёр [{tag}] готов к A/D-стратификации (Группа 3)",
                 f"{days}д daily_metrics ≥ {min_days} И {nper} периодов ≥ {min_periods} — эпохи есть, "
                 f"стартуй letitbe пер-тенант стратификации [сигнал от {today}]")
    return {"checked": checked, "ready": ready, "min_days": min_days, "min_periods": min_periods}


# ── Датчик КЛАССА «объявленное имя не находится в данных» (аудит датчиков 2026-07-25) ──
def _raw_day_keys() -> frozenset:
    from metrics_db import APPLE_RAW_KEYS
    return APPLE_RAW_KEYS


_SYNTH_ENTRY = {"date": "2000-01-01 00:00:00 +0000", "qty": 1.0, "systolic": 1.0,
                "diastolic": 1.0, "Avg": 1.0, "Min": 1.0, "Max": 1.0, "totalSleep": 1.0}


def _hae_device_absent(con, col: str, rest_dir: Path) -> bool:
    """Пустая колонка = «прибора нет», а не потеря (решение владельца 26.09, вариант А).

    Истина — только если у колонки ЕСТЬ HAE-источник (метрика, для которой настоящий разборщик
    кладёт ключ с именем колонки) и ни один такой источник не приходил за глубину архива сырья
    (самый старый файл hae_rest). Приходил — значит, данные есть, а колонка пуста: потеря,
    кричим (как было с давлением). Источника нет вовсе (колонка Oura и т.п.) — судить нечем,
    прежняя строгость. Память правила = глубина архива сырья: почистят архив — правило
    ослабнет (станет молчать про давние потери); дата начала архива берётся из файлов каждый прогон.
    """
    import sqlite3
    from hae_checker import _produced_keys
    from hae_checker import raw_archive_files
    rest = raw_archive_files(rest_dir)          # и сжатые .json.gz — глубина архива та же
    if not rest:
        return False
    s = rest[0].name.split("-REST-")[1][:8]
    since = f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    try:
        reg = con.execute("SELECT metric_name, last_seen FROM hae_metric_registry").fetchall()
    except sqlite3.Error as e:
        if _absent_table(e):
            return False
        raise
    sources = [(n, seen) for n, seen in reg if col in _produced_keys(n, [_SYNTH_ENTRY])]
    return bool(sources) and all((seen or "") < since for _, seen in sources)


def check_family_names_resolve(db_path=None, base=None):
    """Каждое объявленное имя семьи валид-гейта должно разрешаться в ЖИВЫЕ данные.

    Класс ошибки: семья объявляет ИМЯ, БД хранит ВАРИАНТ → строгое совпадение
    даёт пустой ряд, неотличимый от отсутствия данных. Исправлять отдельные
    псевдонимы недостаточно: датчик должен проверять разрешение всех имён семьи.

    Три оракула: (1) имя лаб-семьи не разрешается в ИЗВЕСТНОЕ канон-имя (lab_canon.CANONICALS);
    (2) разрешается, но записей 0, а в БД есть ПОХОЖЕЕ имя (грубый ключ / префикс ≥5);
    (3) daily-метрика без колонки в daily_metrics ИЛИ колонка сплошь NULL («пусто ≠ отсутствие»).

    ГРАНИЦА (честно): случай «разные слова для одного аналита» (HGB↔Hemoglobin) эвристикой НЕ
    ловится — его знает только таблица канона. Датчик покрывает два класса из трёх.
    Область — валид-гейт (решение владельца 2026-07-25): 14 аналитов + 12 daily-метрик, не весь проект.
    Таблицы ПОРОГОВ (absolute_thresholds, lab_trend_thresholds) — вне этой области; их стережёт
    check_threshold_names_reach_data с ВЫЧИСЛЯЕМЫМ периметром (нить norm-provenance 2026-09-02).
    Не дублирует lab_name_aliases (тот про распознавание документов, format_id→raw_name) и
    check_lab_canon_health (тот про ЗНАЧЕНИЯ, этот про ИМЕНА) — сверено project_capabilities.

    C-19: петля по ВСЕМ тенантам, не только владелец. PHI-гард по образцу check_lab_canon_health:
    имена аналитов — только СВОЕМУ тенанту, сиблингу — ТОЛЬКО счётчик (какие анализы сдаёт
    партнёр = его медданные, в owner-доставку не идут). НЕ падает: сигналит warn."""
    import re as _re
    import sqlite3
    import yaml as _y
    import lab_canon
    root = Path(base) if base else Path(__file__).resolve().parent
    fam = _y.safe_load((root / "methodology" / "validation_gate" / "signal_family.yaml").read_text(encoding="utf-8"))
    labs = list(fam["lab_metrics"])
    dailies = [m for m in fam["daily_metrics"] if _re.fullmatch(r"[A-Za-z0-9_]+", str(m))]
    rest_dir = (Path(db_path).parent if db_path else Path(db.DB_PATH).parent) / "hae_rest"

    def _rough(s):
        return _re.sub(r"[^a-z0-9]", "", (s or "").lower())

    def _scan(con, owner=True):
        # Находка внешнего ревью 2026-07-25 (P2): здесь стояли два `except: → пустой набор`, и
        # БД БЕЗ СХЕМЫ давала `issues=[]` — «всё хорошо», неотличимое от корректного отсутствия
        # данных. Хуже: `check()` в этом модуле УЖЕ кричит на не-Assertion исключении (FAIL +
        # _code_failures), а я собственноручно заглушил громкий механизм. Теперь наличие таблиц
        # проверяется ЯВНО (отсутствие у владельца — issue), а реальные ошибки БД летят наверх.
        _tables = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "lab_results" not in _tables:
            rows = []
            if owner:
                return [f"нет таблицы lab_results — гейту нечего читать (таблиц в БД: {len(_tables)})"]
        else:
            rows = con.execute("SELECT test_name, COUNT(*) FROM lab_results "
                               "WHERE specimen='blood' GROUP BY test_name").fetchall()
        if "daily_metrics" not in _tables:
            dcols, filled = set(), {}
            if owner:
                return [f"нет таблицы daily_metrics — гейту нечего читать (таблиц в БД: {len(_tables)})"]
        else:
            dcols = {r[1] for r in con.execute("PRAGMA table_info(daily_metrics)").fetchall()}
            filled = {c: con.execute(f'SELECT COUNT("{c}") FROM daily_metrics').fetchone()[0]
                      for c in dailies if c in dcols}   # имена колонок отфильтрованы regex выше
        counts, raw = {}, {}
        for _nm, _n in rows:
            _c = lab_canon.normalize(_nm)
            counts[_c] = counts.get(_c, 0) + _n
            raw[_nm] = _n
        issues = []
        for a in labs:
            canon = lab_canon.normalize(a)
            if canon not in lab_canon.CANONICALS:
                issues.append(f"{a}: не разрешается в известное канон-имя → невидим гейту")
                continue
            if counts.get(canon, 0) == 0 and raw:
                ra = _rough(a)
                near = sorted(nm for nm in raw
                              if _rough(nm) == ra
                              or (len(ra) >= 5 and (_rough(nm).startswith(ra) or ra.startswith(_rough(nm)))))
                if near:
                    issues.append(f"{a}: 0 записей, но в БД есть похожее — {', '.join(near[:3])}")
        for m in dailies:
            # Оракул (3) — только текущая установка: семья объявлена по её метрикам.
            # У сиблинга может быть другой набор устройств, поэтому пустая
            # колонка сама по себе не означает дефект. Оракул (2) — всем.
            if not owner or not dcols:
                break
            if m not in dcols:
                issues.append(f"daily.{m}: нет колонки в daily_metrics")
            elif filled.get(m, 0) == 0 and not _hae_device_absent(con, m, rest_dir):
                issues.append(f"daily.{m}: колонка есть, но все значения NULL (пусто ≠ отсутствие)")
        return issues

    _hint = ("приведи signal_family.yaml к канону (lab_canon) и перезамерь counts в data_manifest")
    if db_path:                              # тестовый/точечный путь: одна БД, считаем «своей»
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            issues = _scan(con)
        finally:
            con.close()
        if issues:
            warn(f"имена семьи не разрешаются в данные: {len(issues)}", "; ".join(issues) + " — " + _hint)
        return {"lab_checked": len(labs), "daily_checked": len(dailies), "issues": issues}
    out = {}
    for _tag, _con, _cur in _iter_tenant_ro():
        issues = _scan(_con, owner=bool(_cur))
        out[_tag] = len(issues)
        if issues:
            _det = ("; ".join(issues) + " — " + _hint) if _cur else ""   # имена — только своему тенанту
            warn(f"[{_tag}] имена семьи не разрешаются в данные: {len(issues)}", _det)
    return out or None


# ── Датчик КЛАССА «порог не доезжает до данных» (нить norm-provenance, 2026-09-02) ──
def check_threshold_names_reach_data(db_path=None):
    """Каждый лабораторный порог обязан ВСТРЕЧАТЬ данные: имя = канон lab_canon.normalize,
    и под канон-именем в lab_results есть хоть одна строка.

    Если разрешение имён проверяется только у валид-гейта, другие читатели
    могут остаться слепыми к тем же псевдонимам. Для safety_net отсутствие
    разрешённого имени не должно выглядеть как отсутствие повода для тревоги.

    ПЕРИМЕТР ВЫЧИСЛЯЕТСЯ, не перечисляется (§18): все таблицы, у которых есть колонки
    metric И direction (= таблица порогов), по sqlite_master. Daily-метрики (hrv, steps…)
    исключаются по НАЛИЧИЮ КОЛОНКИ в daily_metrics (или её агрегата metric_*: spo2→spo2_avg),
    не списком. Честно: правило суффикса — эвристика по схеме; имя, которое читатель
    маппит на колонку без общего префикса, датчик посчитает лабораторным и покраснеет —
    это ложное красное, а не молчание, и оно чинится именем, не исключением. Предикат в обе стороны
    (§17): (1) metric не канон → красное даже при наличии данных (читатель со строгим
    матчем не найдёт); (2) канон без единой строки lab_results → красное: либо аналит
    ни разу не мерялся (порог объявлен впустую — находка, не умолчание), либо канон ещё
    не знает алиаса, под которым лежат данные.
    Граница честно: «строка есть» ≠ «читатель использует» — датчик стережёт ИМЕНА, а
    не путь кода; safety_net нормализует обе стороны сам (тест на него отдельный)."""
    import sqlite3
    import lab_canon

    def _scan(con, owner=True):
        daily = {r[1] for r in con.execute("PRAGMA table_info(daily_metrics)")}
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        perim = []
        for t in tables:
            cols = {r[1] for r in con.execute(f"PRAGMA table_info({t})")}
            if {"metric", "direction"} <= cols:
                perim.append(t)
        data = {lab_canon.normalize(r[0]) for r in con.execute(
            "SELECT DISTINCT test_name FROM lab_results WHERE test_name IS NOT NULL")}
        issues, soft, checked = [], [], 0
        for t in perim:
            active = "active" in {r[1] for r in con.execute(f"PRAGMA table_info({t})")}
            q = f"SELECT DISTINCT metric FROM {t}" + (" WHERE active=1" if active else "")
            for (m,) in con.execute(q):
                # daily-метрика: колонка daily_metrics либо её агрегат с суффиксом
                # (spo2 → spo2_avg, safety_net.m2k) — по СХЕМЕ, не списком
                if not m or m in daily or any(d.startswith(m + "_") for d in daily):
                    continue
                # ключ дня без колонки (пик давления bp_*_max, 26.09) — дом ключей raw
                # metrics_db.APPLE_RAW_KEYS, тот же, что у писателя; не лаб-имя
                if m in _raw_day_keys():
                    continue
                checked += 1
                canon = lab_canon.normalize(m)
                if canon != m:
                    issues.append(f"{t}.{m}: не канон (канон '{canon}') — строгий читатель не найдёт")
                elif canon not in data:
                    # владелец: аналит объявлен порогом, значит мерялся по замыслу → красное;
                    # партнёр: пороги сеются всем тенантам, а сдаёт он не всё → предупреждение,
                    # иначе датчик красен по построению и его научатся игнорировать (§13)
                    (issues if owner else soft).append(
                        f"{t}.{m}: ни одной строки lab_results под этим именем")
        return perim, checked, issues, soft

    _hint = "переименуй metric в lab_canon.normalize(metric) (health_db._canonize_threshold_metrics) либо заведи алиас в lab_canon"
    if db_path:                              # тестовый/точечный путь: одна БД, считаем «своей»
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            perim, checked, issues, _ = _scan(con)
        finally:
            con.close()
        assert not issues, f"порогов не доезжает до данных: {len(issues)} из {checked} ({perim}): " + "; ".join(issues) + " — " + _hint
        return {"tables": perim, "checked": checked}
    import secrets_paths as _sp
    out, red = {}, []
    for _tag, _con, _cur in _iter_tenant_ro():
        # строгость — по ПРОИСХОЖДЕНИЮ данных, не по «текущая ли база»: прогон по данным
        # партнёра (плист проверки у каждого человека, 28.09) иначе судил партнёра как владельца
        perim, checked, issues, soft = _scan(_con, owner=bool(_cur) and _sp.is_owner_data())
        out[_tag] = {"tables": perim, "checked": checked, "issues": len(issues), "soft": len(soft)}
        if soft:
            warn(f"[{_tag}] порогов без единой строки данных: {len(soft)} из {checked}",
                 "; ".join(soft) if _cur else "")
        if issues:
            red.append(f"[{_tag}] {len(issues)} из {checked}" + ((": " + "; ".join(issues)) if _cur else ""))
    assert not red, "порогов не доезжает до данных: " + " | ".join(red) + " — " + _hint
    return out or None


# ── Провенанс нормы: срок годности · внешний свидетель · покрытие (нить norm-provenance, 2026-09-02) ──
# Норма — реплика внешнего документа (гайдлайн, справочник лаборатории), и единственная
# применимая ось согласованности — устаревание: медицина обновляет норму не вычислением, а
# именованным документом с датой пересмотра (pull человеком, push из литературы отвергнут —
# извлечение числа моделью даёт уверенную чушь). next_review = граница устаревания.
_NORM_FALLBACK = {"norm.witness_max_dev": 0.25, "norm.witness_min_docs": 3.0, "norm.coverage_min_n": 5.0}


def _norm_cfg(key: str, conn=None) -> float:
    try:
        import config_db
        return float(config_db.get_config(key, _NORM_FALLBACK[key], conn=conn))
    except Exception:  # noqa: BLE001 — нет БД/таблицы: резерв, но ГРОМКО (§14)
        warn(f"{key} недоступен в БД", f"взят резерв {_NORM_FALLBACK[key]}")
        return _NORM_FALLBACK[key]


def _norm_scan_overdue(con, today: str) -> list[str]:
    cols = {r[1] for r in con.execute("PRAGMA table_info(absolute_thresholds)")}
    if "next_review" not in cols:
        return ["absolute_thresholds без колонки next_review — миграция не прошла"]
    return [f"{r[0]}/{r[1]}/{r[2] or '-'}/{r[3] or '-'}: next_review {r[4]}"
            for r in con.execute(
                "SELECT metric, direction, band_label, variant, next_review FROM absolute_thresholds "
                "WHERE active=1 AND next_review IS NOT NULL AND next_review < ?", (today,))]


def check_norm_review_overdue():
    """Активная норма с прошедшим next_review — красное у владельца. Это буквально
    «регулярно апдейтить» (слова владельца), переведённое в механизм: датчик не знает,
    что изменилось в медицине, он знает, что срок, назначенный человеком, прошёл.
    Партнёр: тот же счёт, но предупреждением — сроки ему не назначал никто.
    Граница: unclassified без даты сюда не попадает намеренно (§13: единый срок всем =
    60 красных в один день); их считает check_norm_kind_unclassified."""
    from datetime import date as _d
    today = str(_d.today())
    out, red = {}, []
    for _tag, _con, _cur in _iter_tenant_ro():
        issues = _norm_scan_overdue(_con, today)
        out[_tag] = len(issues)
        if issues and _cur:
            red.append(f"[{_tag}] {len(issues)}: " + "; ".join(issues))
        elif issues:
            warn(f"[{_tag}] норм просрочено: {len(issues)}", "")
    assert not red, "нормы просрочены (next_review < сегодня): " + " | ".join(red) + \
        " — перепроверь источник, обнови value/source/source_date и передвинь next_review"
    return out or None


def _norm_scan_witness(con, max_dev: float, min_docs: int):
    """Сверка порогов с интервалом, который ЛАБОРАТОРИЯ печатает в каждом отчёте
    (ref_low/ref_high уже в lab_results). Второй источник в смысле §17: он не проходил
    через наш сид. Модальный интервал по ≥min_docs РАЗНЫМ документам (source) — один
    бланк не свидетель. Возвращает (red, proposals): red — норма вида reference_interval
    расходится >max_dev; proposals — для unclassified печатается, совпал ли warn-порог
    с лабораторией (кандидат в reference_interval) или нет (кандидат в decision_threshold
    / personal) — ПРЕДЛОЖЕНИЕ человеку, машина norm_kind не пишет."""
    import lab_canon
    from collections import defaultdict
    cols = {r[1] for r in con.execute("PRAGMA table_info(absolute_thresholds)")}
    if "norm_kind" not in cols:
        return ["absolute_thresholds без колонки norm_kind — миграция не прошла"], []
    thr = con.execute(
        "SELECT metric, direction, value, norm_kind FROM absolute_thresholds "
        "WHERE active=1 AND variant='safety_net' AND band_label='warn' AND kind='absolute' "
        "AND norm_kind IN ('reference_interval','unclassified')").fetchall()
    if not thr:
        return [], []
    names = {r[0] for r in thr}
    docs = defaultdict(lambda: defaultdict(set))   # metric → (lo,hi) → {source}
    skipped_rows: list = []
    for tn, lo, hi, unit, src in con.execute(
            "SELECT test_name, ref_low, ref_high, unit, source FROM lab_results "
            "WHERE ref_low IS NOT NULL AND ref_high IS NOT NULL "
            "AND (source IS NULL OR source NOT LIKE 'instrument:%')"):
        m = lab_canon.normalize(tn)
        if m not in names:
            continue
        lo, hi = lab_canon.to_conventional_range(m, lo, hi, unit or "")
        try:
            docs[m][(float(lo), float(hi))].add(src or "?")
        except (TypeError, ValueError):
            if not skipped_rows:               # сигнал один раз, дальше — счётчик
                warn("свидетель: нечисловой референс в lab_results", f"{m}: {lo}–{hi} (первый из)")
            skipped_rows.append((m, lo, hi))
            continue
    if len(skipped_rows) > 1:
        warn(f"свидетель: строк с нечисловым референсом всего {len(skipped_rows)}", "")
    modal = {}
    for m, spans in docs.items():
        best = max(spans.items(), key=lambda kv: len(kv[1]))
        if len(best[1]) >= min_docs:
            modal[m] = best[0]
    red, proposals = [], []
    for m, direction, v, kind in thr:
        if m not in modal or v is None:
            continue
        lo, hi = modal[m]
        ref = lo if direction == "floor" else hi
        if not ref:
            continue
        dev = abs(float(v) - ref) / abs(ref)
        line = f"{m} {direction} warn {v:g} vs лаборатория {lo:g}–{hi:g} ({dev:.0%})"
        if kind == "reference_interval" and dev > max_dev:
            red.append(line)
        elif kind == "unclassified":
            proposals.append(line + (" → совпадает: кандидат reference_interval"
                                     if dev <= max_dev else
                                     " → расходится: порог решения или личная норма?"))
    return red, proposals


def check_norm_vs_lab_reference():
    """Внешний свидетель нормы. Красное — только для norm_kind='reference_interval':
    норма ОБЪЯВЛЕНА статистикой здоровых и расходится с тем, что печатает лаборатория,
    больше norm.witness_max_dev. Для unclassified — предложения человеку (WARN-строка),
    чтобы 60+ вердиктов были проверкой предложений, а не работой с нуля.
    Граница (§17, не потерять): ref_low/ref_high сами производные — из PDF-разбора; модальный
    интервал по ≥min_docs документам смягчает, не снимает. Ложное согласие свидетеля с
    ошибочной меткой человека машиной не ловится."""
    out, red = {}, []
    for _tag, _con, _cur in _iter_tenant_ro():
        max_dev = _norm_cfg("norm.witness_max_dev", conn=_con)
        min_docs = int(_norm_cfg("norm.witness_min_docs", conn=_con))
        r, props = _norm_scan_witness(_con, max_dev, min_docs)
        out[_tag] = {"red": len(r), "proposals": len(props)}
        if props and _cur:
            warn(f"[{_tag}] предложения вида нормы по свидетелю: {len(props)}", "; ".join(props))
        if r:
            red.append(f"[{_tag}] {len(r)}" + ((": " + "; ".join(r)) if _cur else ""))
    assert not red, "норма вида reference_interval расходится с интервалом лаборатории: " + \
        " | ".join(red) + " — либо норма устарела, либо это не референс (сменить norm_kind)"
    return out or None


def _norm_scan_coverage(con, min_n: int, refs: dict, verdicts: dict | None = None) -> list[str]:
    """Аналит мерялся ≥min_n раз и нормы нет НИГДЕ: ни порога в таблицах с metric+direction,
    ни интервала в lab_refs (пятый дом нормы, считаем, не переносим). Слепое пятно.
    `verdicts` — живые вердикты человека (`analyte_norm_verdicts`: deferred/no_norm с
    датой пересмотра): аналит с живым вердиктом — не слепое пятно, а принятое решение;
    истёкший вердикт читатель не отдаёт, и аналит возвращается сюда сам (2026-09-03)."""
    import lab_canon
    from collections import Counter
    cnt = Counter(lab_canon.normalize(r[0]) for r in con.execute(
        "SELECT test_name FROM lab_results WHERE test_name IS NOT NULL AND value IS NOT NULL "
        "AND (source IS NULL OR source NOT LIKE 'instrument:%')"))
    covered = set()
    for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'"):
        cols = {r[1] for r in con.execute(f"PRAGMA table_info({t})")}
        if {"metric", "direction"} <= cols:
            q = f"SELECT DISTINCT metric FROM {t}" + (" WHERE active=1" if "active" in cols else "")
            covered |= {lab_canon.normalize(r[0]) for r in con.execute(q) if r[0]}
    covered |= {lab_canon.normalize(k) for k in refs}
    # вид 1: аналит, у которого лаборатория печатает референс, судится бланком (safety_net) —
    # покрыт. Слепое пятно теперь — измеряемое, но без референса ни на одном бланке и без документа.
    covered |= {lab_canon.normalize(r[0]) for r in con.execute(
        "SELECT DISTINCT test_name FROM lab_results WHERE ref_low IS NOT NULL OR ref_high IS NOT NULL")}
    covered |= {lab_canon.normalize(k) for k in (verdicts or {})}
    return [f"{m}×{n}" for m, n in sorted(cnt.items(), key=lambda kv: -kv[1])
            if n >= min_n and m not in covered]


def check_norm_coverage():
    """Покрытие нормой: аналит с ≥norm.coverage_min_n измерений без нормы ни в одном доме —
    ни порога решения (документ), ни референса на бланке (вид 1), ни lab_refs.
    WARN, не красное: механизм не сломан, это очередь решений человека; красное, стоящее
    месяцами, научит игнорировать (§13). Счётчик печатается ночью и в preflight."""
    import labs_db
    out = {}
    for _tag, _con, _cur in _iter_tenant_ro():
        min_n = int(_norm_cfg("norm.coverage_min_n", conn=_con))
        refs = labs_db.get_lab_refs() if _cur else {}
        gaps = _norm_scan_coverage(_con, min_n, refs, labs_db.analyte_norm_verdicts(conn=_con))
        out[_tag] = len(gaps)
        if gaps:
            warn(f"[{_tag}] аналитов без нормы при ≥{min_n} измерений: {len(gaps)}",
                 ", ".join(gaps) if _cur else "")
    return out or None


def check_norm_kind_unclassified():
    """Остаток без вердикта о виде нормы — WARN-счётчик владельцу. Не красное: вердикт
    человеческий и его нельзя вынудить датчиком (§13); но и не тишина — иначе остаток
    станет умолчанием (§9: отсутствие вердикта = находка)."""
    out = {}
    for _tag, _con, _cur in _iter_tenant_ro():
        cols = {r[1] for r in _con.execute("PRAGMA table_info(absolute_thresholds)")}
        if "norm_kind" not in cols:
            out[_tag] = None
            continue
        # только лабораторные нормы: daily-метрики (spo2, readiness, sleep_*, hrv…) — конвенции
        # устройства/владельца, не медицинская норма четырёх видов; их считать здесь = шум
        daily = {r[1] for r in _con.execute("PRAGMA table_info(daily_metrics)")}
        n = sum(1 for (m,) in _con.execute("SELECT metric FROM absolute_thresholds WHERE active=1 "
                                            "AND norm_kind='unclassified'")
                if m and m not in daily and not any(d.startswith(m + "_") for d in daily))
        out[_tag] = n
        if n and _cur:
            warn(f"[{_tag}] норм без вида (norm_kind=unclassified): {n}",
                 "вердикт человека: reference_interval | decision_threshold | stratified_target | personal")
    return out


# ── Документы нормы: свежесть и кадансы (нить norm-from-documents, 2026-09-02) ──
def check_norm_documents_fresh():
    """Норма — реплика внешнего документа; единственная ось согласованности — устаревание,
    и она требует pull: раз в next_check датчик спрашивает удалённое состояние (sha256 файла
    CTCAE / max(updated_at) EFLM) и сравнивает с записанным. Изменилось → красное с обоими
    состояниями: человек (врач в контуре) видит, что документ сменился, машина норму не
    переписывает. next_check прошёл, а проверки не было → красное. Сети нет → WARN (§14),
    не тишина. Документы без URL-состояния (DOI гайдлайнов) — проверка версии руками,
    датчик только напоминает по next_check."""
    from datetime import date as _d, timedelta as _td
    import norm_documents
    today = str(_d.today())
    red, out = [], {}
    with db.get_conn() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(norm_documents)")}
        if "remote_state" not in cols:
            assert False, "norm_documents без remote_state — миграция не прошла"
        rows = conn.execute("SELECT id, url, sha256, next_check, last_check, remote_state, next_check_days "
                            "FROM norm_documents").fetchall()
        for r in rows:
            rid = r["id"]
            due = (r["next_check"] or today) <= today
            if not due:
                out[rid] = "ok"
                continue
            doc = next((d for d in norm_documents.DOCUMENTS if d["id"] == rid), None)
            if doc is None:
                red.append(f"{rid}: в БД есть, в реестре нет")
                continue
            try:
                state = norm_documents.remote_state(doc)
            except Exception as e:  # noqa: BLE001 — сеть: громко, но не красное
                warn(f"документ {rid}: удалённое состояние недоступно", str(e)[:120])
                out[rid] = "unreachable"
                continue
            if state is None:
                warn(f"документ {rid}: версия проверяется руками (DOI без тела)", f"next_check был {r['next_check']}")
                out[rid] = "manual"
                continue
            baseline = r["remote_state"]      # тот же вид состояния, что remote_state (local_state при регистрации)
            conn.execute("UPDATE norm_documents SET last_check=?, next_check=?, remote_state=COALESCE(remote_state, ?) WHERE id=?",
                         (today, str(_d.today() + _td(days=int(r["next_check_days"] or 30))), state, rid))
            if baseline and state != baseline:
                red.append(f"{rid}: удалённое состояние изменилось ({baseline[:16]}… → {state[:16]}…)")
                out[rid] = "changed"
            else:
                out[rid] = "checked"
    assert not red, "документы нормы изменились: " + "; ".join(red) + \
        " — скачай новую версию, snapshot, диф в коммите; норму машина не переписывает"
    return out


def check_schedule_vs_guideline():
    """Назначение врача (lab_monitoring_schedule, source encounter:*) — первично; гайдлайн по
    эпизоду (schedules.json: профильный гайдлайн наблюдения для онко-эпизода) — свидетель, что
    интервал внутри рекомендованного окна и окно наблюдения не истекло. Интервал шире
    max_days → красное; метрики нет в расписании → красное (слепое пятно наблюдения);
    интервал уже min_days — WARN (чаще, чем гайдлайн, — решение врача). Только владелец:
    эпизоды — его."""
    import norm_documents
    import labs_db
    with db.get_conn() as conn:
        eps = [dict(r) for r in conn.execute("SELECT id, end_date, status FROM episodes_of_care")]
    cads = norm_documents.guideline_cadences(eps)
    stmts = norm_documents.guideline_statements(eps)
    if not cads:
        return {"cadences": 0, "statements": len(stmts)}
    sched = labs_db.get_effective_lab_schedule()
    red, soft = [], []
    for c in cads:
        m = c["metric"]
        if m in ("CT_chest_abdomen", "Colonoscopy"):
            continue                       # не лабораторные — расписание их не ведёт (граница)
        s = sched.get(m)
        if not s:
            red.append(f"{m}: гайдлайн {c['doc']} требует наблюдения до {c['until']}, в расписании нет")
            continue
        iv = int(s["interval_days"] or 0)
        if iv > c["max_days"]:
            red.append(f"{m}: интервал {iv} д > {c['max_days']} д ({c['doc']}, до {c['until']}, source={s.get('source')})")
        elif iv < c["min_days"]:
            soft.append(f"{m}: интервал {iv} д < {c['min_days']} д — чаще гайдлайна ({s.get('source')})")
    if soft:
        warn(f"кадансы чаще гайдлайна: {len(soft)}", "; ".join(soft))
    assert not red, "расписание вне окна гайдлайна: " + "; ".join(red)
    return {"cadences": len(cads), "checked": len(cads) - len(soft), "statements": len(stmts)}


def _threshold_source_scan(con) -> tuple[list[str], int]:
    """Активная ЛАБ-строка safety_net обязана нести источник-документ (id из norm_documents),
    личный источник (*_owner_personal_*) или назначение врача (clinician:*). Иначе — набранное
    число, класс «пересказ» (апрель 2026). daily-метрики (spo2/readiness/sleep_*) — конвенции
    устройства, вне этого предиката."""
    daily = {r[1] for r in con.execute("PRAGMA table_info(daily_metrics)")}
    docs = {r[0] for r in con.execute("SELECT id FROM norm_documents")} \
        if con.execute("SELECT 1 FROM sqlite_master WHERE name='norm_documents'").fetchone() else set()
    bad, checked = [], 0
    for m, src, band, direction in con.execute(
            "SELECT metric, source, band_label, direction FROM absolute_thresholds "
            "WHERE active=1 AND variant='safety_net'"):
        if not m or m in daily or any(d.startswith(m + "_") for d in daily):
            continue
        checked += 1
        src = src or ""
        if src in docs or "owner_personal" in src or src.startswith("clinician:"):
            continue
        bad.append(f"{m}/{direction}/{band}: source={src!r}")
    # lab_refs (кэш референсов): каждый интервал — мода по ≥min_docs документам; иначе число
    # без свидетелей (та же категория, что набранный порог)
    if con.execute("SELECT 1 FROM sqlite_master WHERE name='system_config'").fetchone():
        import json as _json
        row = con.execute("SELECT value_json, source FROM system_config WHERE key='lab_refs'").fetchone()
        meta = con.execute("SELECT value_json FROM system_config WHERE key='lab_refs_meta'").fetchone()
        if row and row[0]:
            min_docs = int((_json.loads(meta[0]) if meta and meta[0] else {}).get("min_docs", 3))
            for k, v in _json.loads(row[0]).items():
                checked += 1
                n = v[3] if isinstance(v, list) and len(v) > 3 else None
                if row[1] != "bank_modal" or n is None or n < min_docs:
                    bad.append(f"lab_refs.{k}: source={row[1]!r}, документов={n} (< {min_docs})")
    return bad, checked


def check_threshold_source_is_document():
    """Инвариант threshold_derived_from_document (реестр norm_from_documents): набранных
    клинических чисел в safety_net нет. Красное у владельца И у партнёра — сид общий,
    число без документа одинаково ложно для обоих. Граница: датчик судит ИМЯ источника,
    не то, что документ действительно говорит это число, — это держит coherence снимка."""
    out, red = {}, []
    for _tag, _con, _cur in _iter_tenant_ro():
        bad, checked = _threshold_source_scan(_con)
        out[_tag] = {"checked": checked, "bad": len(bad)}
        if bad:
            red.append(f"[{_tag}] {len(bad)} из {checked}" + ((": " + "; ".join(bad)) if _cur else ""))
    assert not red, "пороги safety_net без документа/личного/врачебного источника: " + " | ".join(red) + \
        " — число без документа = пересказ; заведи документ (norm_documents) или source clinician:<id>"
    return out or None


# brief-neutralization step0/Tier-A: регистрации per-tenant чеков — ЗДЕСЬ, ПОСЛЕ
# _tenant_db_paths/_iter_tenant_ro (check() исполняет fn сразу → forward-def иначе).
check("SQLite integrity_check ok per-tenant (нет B-tree corruption)", check_db_integrity,
      critical=True)  # битая B-tree: любая цифра в брифе — из повреждённого файла
check("тенант-БД достижимы (анти-усечёнка per-tenant чеков)", check_tenant_dbs_reachable)
check("готовность партнёра к пер-тенант стратификации (Группа 3 триггер)", check_partner_epochs_ready)
check("имена семьи валид-гейта разрешаются в живые данные", check_family_names_resolve)
check("лаб-пороги доезжают до данных (канон-имя + строка в lab_results)", check_threshold_names_reach_data)
check("нормы не просрочены (next_review)", check_norm_review_overdue)
check("норма-референс согласна с интервалом лаборатории (внешний свидетель)", check_norm_vs_lab_reference)
check("покрытие нормой измеряемых аналитов", check_norm_coverage)
check("вид нормы вынесен (norm_kind ≠ unclassified)", check_norm_kind_unclassified)
check("документы нормы свежи (sha256 / updated_at по next_check)", check_norm_documents_fresh)
check("расписание наблюдения внутри окна гайдлайна по эпизоду", check_schedule_vs_guideline)
check("пороги safety_net выведены из документа (не набраны)", check_threshold_source_is_document)
check("живость reschedule брифа (местный tz)", check_reschedule_liveness)
def check_hae_arrivals_have_owner():
    """WARN: у метрики, которую прибор реально присылает, нет хозяина (решение владельца 26.09).

    Вердикт выносит hae_checker.judge_payload на приёме — по поведению разборщика, не по
    пометке реестра (пометка про давление врала с апреля). Здесь — доставка: реестр как
    СОСТОЯНИЕ, не событие — крик держится, пока не появится ветка разборщика или решение
    «не берём:», даже если метрика больше не приходит. Второе: живость судьи — реестр
    обязан видеть приход; с 06.07 по 26.09 он не видел ничего (iCloud-скан после миграции
    на REST) — эта слепота и есть класс, который датчик обязан ловить сам.
    Сиблингу — только счётчики (без имён метрик), как у соседних per-tenant датчиков.
    """
    import sqlite3 as _sq
    out = {}
    for _p in _tenant_db_paths(include_current=True):
        _tag = Path(_p).parent.parent.name
        _own = Path(_p).resolve() == Path(db.DB_PATH).resolve()
        try:
            con = _sq.connect(f"file:{_p}?mode=ro", uri=True)
            rows = con.execute("SELECT metric_name, status, notes, last_seen FROM hae_metric_registry"
                               ).fetchall()
            con.close()
        except _sq.Error as e:
            if _absent_table(e):
                continue
            raise
        loud = [(n, s, nt) for n, s, nt, _ in rows if s in ("unowned", "new")]
        out[_tag] = len(loud)
        if loud:
            det = ("; ".join(f"{n} [{s}]" + (f" — {nt}" if nt else "") for n, s, nt in loud[:12])
                   + " — нужна ветка в import_apple_health.aggregate_metric_by_day (+ ключ в "
                   "metrics_db.APPLE_RAW_KEYS) или решение в health_db._HAE_DECISIONS") if _own else ""
            warn(f"[{_tag}] метрики прибора без хозяина: {len(loud)}", det)
        from hae_checker import raw_archive_files
        rest = raw_archive_files(Path(_p).parent / "hae_rest")
        seen = max((r[3] for r in rows if r[3]), default=None)
        if rest:
            newest = rest[-1].name.split("-REST-")[1][:8]
            newest = f"{newest[:4]}-{newest[4:6]}-{newest[6:8]}"
            if seen is None or seen < str(date.fromisoformat(newest) - timedelta(days=3)):
                warn(f"[{_tag}] реестр HAE слеп к приходу",
                     f"последний файл {newest}, реестр видел данные по {seen} — судья на приёме "
                     "(api_hae_ingest → hae_checker.judge_payload) не работает")
    return out or None


check("метрики прибора: у каждой есть хозяин (HAE)", check_hae_arrivals_have_owner)


def check_bp_day_is_withings():
    """WARN: дневное давление разошлось с замерами Withings (нить bp-withings-owner, 06.10).

    Решение владельца 06.10: у кого Withings подключён, дневное bp_systolic/bp_diastolic — среднее
    всех замеров дня из bp_readings, и пишет его только import_withings. Судья пересчитывает
    среднее сам, не через код писателя (§17): иначе ошибка писателя прошла бы обоих. Ловит
    второго писателя (путь «Здоровья» снова пишет давление) и недописанный пересчёт: день с
    замерами без значения или с другим значением, и день без замеров (начиная с первого дня
    Withings) со значением. Нет таблицы или замеров — Withings не подключён, молчим.
    Сиблингу — только счётчик дней, без значений давления.
    """
    import sqlite3 as _sq
    from datetime import datetime as _dtm
    from zoneinfo import ZoneInfo
    import region_pack
    tz = ZoneInfo(region_pack.value("timezone", "UTC"))
    out = {}
    for _p in _tenant_db_paths(include_current=True):
        _tag = Path(_p).parent.parent.name
        try:
            con = _sq.connect(f"file:{_p}?mode=ro", uri=True)
            sums: dict = {}
            for t, s, d in con.execute("SELECT measured_at, systolic, diastolic FROM bp_readings"):
                acc = sums.setdefault(_dtm.fromtimestamp(t, tz).date().isoformat(), [0.0, 0.0, 0])
                acc[0] += s
                acc[1] += d
                acc[2] += 1
            if not sums:
                con.close()
                continue
            have = {r[0]: (r[1], r[2]) for r in con.execute(
                "SELECT date, bp_systolic, bp_diastolic FROM daily_metrics WHERE date >= ?",
                (min(sums),))}
            con.close()
        except _sq.Error as e:
            if _absent_table(e):
                continue
            raise
        bad = []
        for day in sorted(set(sums) | {d for d, v in have.items() if v[0] is not None or v[1] is not None}):
            got = have.get(day, (None, None))
            if day in sums:
                s, d, n = sums[day]
                want = (round(s / n, 1), round(d / n, 1))
                if got[0] is None or got[1] is None or abs(got[0] - want[0]) > 0.05 or abs(got[1] - want[1]) > 0.05:
                    bad.append(day)
            else:
                bad.append(day)
        out[_tag] = len(bad)
        if bad:
            warn(f"[{_tag}] дневное давление разошлось с замерами Withings: {len(bad)} дн.",
                 f"последний такой день {bad[-1]} — пересчитайте import_withings.py; если повторится, "
                 "давление пишет кто-то ещё (путь «Здоровья» в metrics_db.upsert_metrics_from_json)")
    return out or None


check("давление: дневное значение — среднее замеров Withings, второго писателя нет", check_bp_day_is_withings)


RAW_ARCHIVE_STALE_DAYS = 15   # сжатие — после 14 дней (hae_checker.compress_raw_archive) + сутки запаса


def check_hae_raw_archive_compressed():
    """FAIL: в архиве сырья HAE лежат несжатые выгрузки старше 15 дней (решение владельца 26.09).

    Сжатие — обязанность ротатора (log_rotate.__main__ → hae_checker.compress_raw_archive).
    check_logrotate_liveness видит смерть агента и провал самопроверки copytruncate, но слеп к
    случаю «агент жив, а шаг сжатия сломан или не дошёл до тенанта» — архив тогда снова растёт
    без предела (2 ГБ за 2,5 месяца до 26.09). Судим по ДАННЫМ — именам файлов на диске, а не
    по квитанции: квитанция говорит, что шаг звался, диск — что он сработал.
    FAIL, а не WARN, — по той же причине, что у ротатора: падения доходят до владельца.
    """
    stale_before = (get_today() - timedelta(days=RAW_ARCHIVE_STALE_DAYS)).strftime("%Y%m%d")
    bad = {}
    for _p in _tenant_db_paths(include_current=True):
        rest = Path(_p).parent / "hae_rest"
        n = sum(1 for f in rest.glob("HealthAutoExport-REST-*.json")
                if f.name.split("-REST-")[1][:8] < stale_before)
        if n:
            bad[Path(_p).parent.parent.name] = n
    if bad:
        raise AssertionError(
            "несжатые выгрузки HAE старше %d дней: %s — шаг сжатия ротатора не сработал "
            "(log_rotate.py → hae_checker.compress_raw_archive)"
            % (RAW_ARCHIVE_STALE_DAYS, ", ".join(f"[{t}] {n}" for t, n in sorted(bad.items()))))
    return None


check("архив сырья HAE сжат (ротатор, старше 15 дней)", check_hae_raw_archive_compressed)
check("weekly_digest: доставлен всем тенантам (понедельник)", check_weekly_digest_delivered)  # перенесён из ~748 (forward-def _iter_tenant_ro)
check("clinical_kb населён (пол/рамка еды не вырождены, Ф2)", check_clinical_kb_populated)
check("покрытие effect_allele (strand)", check_effect_allele_coverage)
check("clinical_kb реплика свежа (source==table, Ф2)", check_clinical_kb_replica_fresh)
check("свежесть lab_results", check_lab_freshness)
check("канон lab_results: нет невозможных значений и конфликтов", check_lab_canon_health)
check("промоут не стоит (очередь staging движется)", check_promotion_backlog_stale)
check("справочник LOINC: один дом, канон его не перехватывает", check_reference_tables_not_in_canon)
check("под одним именем не склеены разные аналиты (нормы не расходятся)", check_lab_names_not_glued)
check("канон lab_results: у каждой строки есть результат", check_canon_rows_have_a_result)  # там же
check("канон lab_results: один аналит в документе — одна дата",
      check_canon_one_date_per_analyte_in_doc)  # там же: forward-def _iter_tenant_ro
check("канон lab_results: у нового значения есть единица", check_lab_unit_present)  # регистрация здесь: forward-def _iter_tenant_ro
check("канон lab_results: референс в той же шкале, что значение", check_lab_ref_scale,
      critical=True)  # шкала разошлась → «норма/не норма» в брифе перевёрнута молча
check("staging: материал пробы и его ступень не разошлись", check_staging_specimen_provenance)  # там же и по той же причине
check("значение и оператор сравнения не разошлись", check_censored_values_coherent,
      critical=True)  # «<0,01» прочитанное как 0,01 — цифра в брифе неверна по знаку
check("specialized_lab_results: целостность (panel_type + значение)", check_specialized_lab_health)  # там же и по той же причине (C-19)
check("спец-слой пустеет: сводимое имя не застревает мимо канона", check_specialized_canon_waiting)
check("назывной долг канона не растёт", check_canon_naming_debt)  # там же: forward-def _iter_tenant_ro
check("история не заслоняет настоящее", check_unrepeated_draw_not_swamping)  # там же: forward-def _iter_tenant_ro
check("партнёрский кран литературы: гаситель", check_literature_partner_gate)
check("специальные панели: электрофорез сходится сам с собой", check_electrophoresis_sums)  # там же и по той же причине
check("промоут: отказ слить два значения виден человеку", check_promote_conflicts)  # там же и по той же причине
check("решения LOINC не противоречат отсеву по фактам о человеке", check_loinc_decisions_possible)
check("канон lab_results: нет биллинг-мусора (валюта/процедуры)", check_lab_no_billing_rows)
check("сцепление carrier-статус⟹effect_allele (null≠чисто)", check_carrier_status_allele_coupling)
check("инварианты memory_facts (E: no-double-active-key + R11)", check_memory_facts_invariants)


def _is_dev_clone(path) -> bool:
    name = Path(path).parent.parent.name
    return any(m in name for m in _DEV_CLONE_MARKERS)


def scan_hollow_constitutions(db_paths) -> list[str]:
    """Чистая функция: для каждой БД возвращает пометку, если у неё есть
    конституции + геном, но 0 resolved effect_allele (полые). Тестируема в отрыве
    от glob/DB_PATH."""
    import sqlite3 as _sq
    hits = []
    for p in db_paths:
        rc = _sq.connect(f"file:{p}?mode=ro", uri=True)
        try:
            def _c(sql):
                try:
                    return rc.execute(sql).fetchone()[0]
                except _sq.OperationalError as e:  # A5 22.09: ноль законен только без таблицы
                    if not _absent_table(e):
                        raise
                    return 0
            gv = _c("SELECT COUNT(*) FROM genetic_variants")
            cons = _c("SELECT COUNT(*) FROM constitutions")
            resolved = _c("SELECT COUNT(*) FROM genetic_variants WHERE effect_allele_status='resolved'")
            if cons > 0 and gv > 0 and resolved == 0:
                hits.append(f"{Path(p).parent.parent.name}(cons={cons}, gv={gv}, resolved=0)")
        finally:
            rc.close()
    return hits


def check_constitutions_not_hollow():
    """FAIL, если конституции сгенерированы на ПУСТОМ effect_allele (полые).

    Возможный сбой порядка: конституции собираются до backfill_effect_alleles.
    Наличие genetic_variants ещё не означает, что effect_allele_status заполнен;
    интерпретация может стать пустой. В отличие от check_effect_allele_coverage
    (WARN про backfill), доставленный пустой продукт получает FAIL.
    genome_pipeline S6 стережёт штатный путь, датчик — обходы и обрыв пайплайна.

    Проверка охватывает всех настроенных тенантов: scheduled-прогон текущего
    процесса не доказывает готовность соседнего. Применима только при наличии
    и генома (genetic_variants>0), и конституций.
    """
    dbs = _tenant_db_paths(include_current=True)
    hollow = scan_hollow_constitutions(dbs)
    if hollow:
        raise AssertionError(
            "ПОЛЫЕ конституции (effect_allele пуст) у: " + "; ".join(hollow)
            + " — backfill_effect_alleles + перегенерация (genome_pipeline S3→S6).")
    return {"tenants_checked": len(dbs)}


check("конституции не полые (effect_allele заполнен до генерации)",
      check_constitutions_not_hollow)


def check_constitutions_trigger_alive():
    """Пульс пересборки конституций по новым вводным (решение владельца 2026-09-25: «дальше
    только по триггерам»). Расписание раз в неделю зовёт generate_constitutions --if-changed;
    каждый успешный прогон отмечается в system_config. Молчание дольше TRIGGER_STALE_DAYS —
    расписание мертво или прогон падает: вводные копятся, конституции стареют молча
    (так они простояли с 5 июля по 25 сентября). Все тенанты с конституциями."""
    import sqlite3 as _sq
    import generate_constitutions as _gc
    quiet = []
    for p in _tenant_db_paths(include_current=True):
        rc = _sq.connect(f"file:{p}?mode=ro", uri=True)
        rc.row_factory = _sq.Row
        try:
            try:
                if not rc.execute("SELECT COUNT(*) FROM constitutions").fetchone()[0]:
                    continue
            except _sq.OperationalError as e:   # нет таблицы — нет конституций; иная ошибка — громко
                if not _absent_table(e):
                    raise
                continue
            days = _gc.trigger_silence_days(rc, get_today())
            if days is None or days > _gc.TRIGGER_STALE_DAYS:
                quiet.append(f"{Path(p).parent.parent.name}: "
                             + ("ни разу" if days is None else f"{days} дн."))
        finally:
            rc.close()
    if quiet:
        warn("пересборка конституций по новым вводным молчит",
             "; ".join(quiet) + " — launchd com.larry.health.constitutions* / лог health_constitutions*.log")
    return {"quiet": quiet}


check("пульс пересборки конституций по новым вводным", check_constitutions_trigger_alive)


def check_safety_net_can_judge():
    """«Судить нечем» у предохранителя — долг системы в инженерную очередь.
    Порог может существовать без возможности суждения: аналит не отображён
    в lab_name_loinc или у кратного порога нет референса. Служебное сообщение
    «недостаточно входных данных» не должно выдаваться за вывод в брифе.
    safety_net отделяет такой случай в запись тенанта, которую читает этот датчик.

    warn, класс fix: будить владельца нечем, чинит инженер (отображение/референс).
    Сиблинги — только счётчики (cross-tenant правило _iter_tenant_ro). Нет записи или она
    старая — не судим: жив ли сам бриф, стережёт check_morning_brief_gate_liveness."""
    import sqlite3 as _sq
    import safety_net as _sn
    debts = []
    for p in _tenant_db_paths(include_current=True):
        rc = _sq.connect(f"file:{p}?mode=ro", uri=True)
        rc.row_factory = _sq.Row
        try:
            try:
                items = _sn.cannot_judge_open(rc, get_today())
            except _sq.OperationalError as e:   # нет system_config — не тенант; иное — громко
                if not _absent_table(e):
                    raise
                continue
            if items:
                kinds = sorted({str(i.get("source")) for i in items})
                debts.append(f"{Path(p).parent.parent.name}: {len(items)} ({', '.join(kinds)})")
        finally:
            rc.close()
    if debts:
        warn("предохранителю судить нечем — порог есть, данных для суждения нет",
             "; ".join(debts) + f" — запись {_sn.CANNOT_JUDGE_KEY} в system_config тенанта; "
             "lab_trend → отображение loinc_match.py, norm_unresolved → референс бланка")
    return {"tenants_with_debts": len(debts)}


check("предохранителю есть по чему судить", check_safety_net_can_judge)


GENOTYPE_PROFILE_KEY = "pii.genotype_profile_min_hits"
GENOTYPE_PROFILE_SEED = 3   # seed (§9 п.4): пишется в system_config, читается оттуда


def check_public_genotype_profile():
    """Профиль генотипа тенанта в публичной зоне (2026-09-25, нить genotype-scrub).

    Перепись отдельных литералов не видит профиль: раскрывать может сочетание
    признаков в фикстуре. Судья — pii_census.genotype_profiles + profile_leaks
    против raw_snps всех тенантов; в сообщение идут только путь, строка и число,
    без генотипов. Граница: только dict-литералы в .py; при одном тенанте судья слеп."""
    import sqlite3 as _sq
    import config_db as _cfg
    import pii_census as _pc
    root = Path(__file__).resolve().parent
    raw = _cfg.get_config(GENOTYPE_PROFILE_KEY)
    if raw in (None, ""):
        _cfg.upsert_config(GENOTYPE_PROFILE_KEY, value_text=str(GENOTYPE_PROFILE_SEED),
                           category="integrity", source="integrity_tests")
        raw = _cfg.get_config(GENOTYPE_PROFILE_KEY)
    min_hits = int(str(raw))
    profiles = {}
    for p in _pc.public_files(root):
        if p.endswith(".py"):
            # Отслеживаемый файл, который не читается, — поломка дерева, не «профиля нет»:
            # исключение уходит в check() громко.
            src = (root / p).read_text(encoding="utf-8", errors="ignore")
            ps = _pc.genotype_profiles(src)
            if ps:
                profiles[p] = ps
    rsids = sorted({r for ps in profiles.values() for _, d in ps for r in d})
    tenants = {}
    for p in _tenant_db_paths(include_current=True):
        rc = _sq.connect(f"file:{p}?mode=ro", uri=True)
        try:
            try:
                rows = rc.execute(f"SELECT rsid, genotype FROM raw_snps WHERE rsid IN "
                                  f"({','.join('?' * len(rsids))})", rsids).fetchall() if rsids else []
            except _sq.OperationalError as e:
                if not _absent_table(e):
                    raise
                rows = []
            tenants[Path(p).parent.parent.name] = {
                r: "".join(sorted(g.replace("/", ""))) for r, g in rows if g}
        finally:
            rc.close()
    leaks = _pc.profile_leaks(profiles, tenants, min_hits)
    if leaks:
        warn("в публичной зоне — профиль генотипа тенанта",
             "; ".join(f"{p}:{ln} ({t}: {n} различающих)" for p, ln, t, n in leaks)
             + " — фикстуру заменить синтетикой")
    return {"profiles": sum(len(v) for v in profiles.values()), "tenants": len(tenants), "leaks": len(leaks)}


check("профиль генотипа тенанта в публичной зоне", check_public_genotype_profile, repo_only=True)



def check_single_canonical_db():
    """R1/R2 (split-brain prevention, 2026-06-18): ровно один health.db, не в синк-папке.

    Корень бага: health.db исторически жил в iCloud; iCloud Drive реплицирует .db
    между устройствами без merge-протокола -> форк (split-brain канон<->копия).
    Канон обязан быть на локальном диске Studio; в iCloud-дереве и в корне репо
    health.db быть НЕ должно. См. db_reconciliation_2026-06-18, CLAUDE.md §8/§10.
    """
    issues = []
    p = str(db.DB_PATH)
    if db._is_primary() and any(s in p for s in (
            "Mobile Documents", "CloudDocs", "Dropbox", "OneDrive", "Google Drive")):
        issues.append(f"canonical DB_PATH в синк-папке: {p}")
    icloud = infra_config.CLOUD_HEALTH_DIR     # облачная папка установки (BL-PUB-12); нет — нечего стеречь
    for cand in ((icloud / "data" / "health.db", icloud / "health.db") if icloud else ()):
        if cand.exists():
            issues.append(f"health.db в iCloud (split-brain risk): {cand}")
    repo_db = Path(__file__).parent / "health.db"
    if repo_db.exists():
        issues.append(f"stray health.db в корне репо: {repo_db}")
    # Вложенный форк: процесс с HEALTH_DATA_DIR на уровень ниже корня тенанта заводит
    # <data>/data/health.db с сидами (замер 27.09: такой лежал у владельца с 14.09, невиден).
    import secrets_paths as _sp
    for stray in _sp.nested_db_forks(_tenant_db_paths()):
        issues.append(f"вложенный health.db рядом с каноном: {stray}")
    if issues:
        raise AssertionError("split-brain: " + "; ".join(issues))
    return {"ok": True}


check("единственный канонический health.db (R1/R2 split-brain)", check_single_canonical_db)


CROSS_TENANT_FP = ["hrv", "resting_hr", "sleep_total", "readiness", "spo2_avg"]


def scan_cross_tenant(cur_db_path, sibling_paths, cand=None):
    """Ищет идентичные Oura-отпечатки между БД cur и siblings.

    Чистая функция от путей (тестируема в отрыве от db.DB_PATH/glob). Требует
    все поля отпечатка non-null И идентичны — иначе не совпадение. Возвращает
    список строк-совпадений, либо None если у cur < 3 fingerprint-колонок.
    """
    import sqlite3 as _sq
    CAND = cand or CROSS_TENANT_FP

    def _open(p):
        c = _sq.connect(f"file:{p}?mode=ro", uri=True)
        c.row_factory = _sq.Row
        return c

    def _cols(conn):
        return {r[1] for r in conn.execute("PRAGMA table_info(daily_metrics)")}

    cc = _open(cur_db_path)
    try:
        fp = [c for c in CAND if c in _cols(cc)]
        if len(fp) < 3:
            return None
        cur_rows = {r["date"]: {c: r[c] for c in fp}
                    for r in cc.execute(f"SELECT date,{','.join(fp)} FROM daily_metrics")}
    finally:
        cc.close()

    hits = []
    for sp in sibling_paths:
        rc = _open(sp)
        try:
            common = [c for c in fp if c in _cols(rc)]
            if len(common) < 3:
                continue
            for r in rc.execute(f"SELECT date,{','.join(common)} FROM daily_metrics"):
                d = r["date"]
                if d not in cur_rows:
                    continue
                sib = [r[c] for c in common]
                cur = [cur_rows[d][c] for c in common]
                if all(v is not None for v in sib) and sib == cur:
                    hits.append(f"{d} ↔ {Path(sp).parent.parent.name} ({','.join(common)})")
        finally:
            rc.close()
    return hits


def check_cross_tenant_contamination():
    """Belt-and-suspenders к fail-closed secrets_dir() (2026-07-03).

    Инцидент 2026-07-02: процесс с HEALTH_DATA_DIR=health_partner, но без
    HEALTH_SECRETS_DIR, тянул Oura-токен владельца → данные владельца осели в БД
    партнёра. Корень закрыт (secrets_dir raise). Этот датчик ловит РЕЦИДИВ по
    следствию: у двух РАЗНЫХ людей идентичный мультиполевой Oura-отпечаток на
    одну дату физически невозможен — это подпись кросс-тенант утечки.
    """
    cur_path = Path(db.DB_PATH).resolve()
    if _is_dev_clone(cur_path):
        return "текущая БД — дев/стейджинг-клон, скан пропущен"
    siblings = [p for p in _tenant_db_paths(include_current=False) if p != cur_path]
    if not siblings:
        return "нет sibling-тенантов (одиночный профиль)"

    hits = scan_cross_tenant(cur_path, siblings)
    if hits is None:
        warn("cross-tenant датчик: мало fingerprint-колонок", str(CROSS_TENANT_FP))
        return None
    if hits:
        raise AssertionError(
            "кросс-тенант загрязнение (идентичные Oura-отпечатки у разных людей): "
            + "; ".join(hits[:5]) + (f" +{len(hits)-5}" if len(hits) > 5 else ""))
    return f"чисто ({len(siblings)} тенант(ов))"


check("нет кросс-тенант загрязнения Oura (fail-closed belt-and-suspenders)",
      check_cross_tenant_contamination)


def check_one_document_one_event():
    """Разбор документов создаёт ровно одно событие на файл (нить intake-tails, 24.09).
    Повторный импорт одного источника не должен создавать новые события."""
    import import_medical_events as _ime
    dups = _ime.duplicate_sources()
    if dups:
        raise AssertionError(
            f"{len(dups)} файл(ов) разобраны повторно: "
            + "; ".join(f"{a[:60]}×{n}" for a, n in dups[:3]))
    return "по одному событию на файл"


check("разбор документов: один файл — одно событие", check_one_document_one_event)


def check_longitudinal_freshness():
    """longitudinal_analysis запускается по расписанию `com.larry.health.longitudinal`.
    След последнего запуска не покрывает последний плановый запуск по живому плисту —
    WARN (до 23.09 — литерал «>8 дней»). Защищает от silent skip launchd-агента
    (FileVault/Studio offline/plist broken)."""
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT MAX(date) as last_date FROM agent_reports "
            "WHERE agent_type = 'longitudinal_analysis'"
        ).fetchone()
    if not row or not row["last_date"]:
        warn("longitudinal_analysis ни разу не запускался",
             "launchd com.larry.health.longitudinal не работает?")
        return None
    age_days = (today - date.fromisoformat(row["last_date"][:10])).days
    covered, fire = _schedule_covers(LONGITUDINAL_LABEL, row["last_date"][:10])
    if covered is None:
        _schedule_unjudged("longitudinal_analysis", LONGITUDINAL_LABEL)
    elif not covered:
        warn(f"longitudinal_analysis устарел: {age_days}д (плановый запуск {fire:%d.%m %H:%M} не отметился)",
             f"последний: {row['last_date'][:10]}. Запусти вручную или проверь launchd.")
    return age_days


check("longitudinal_analysis свежесть", check_longitudinal_freshness)


def check_longitudinal_gate_applied():
    """В свежем longitudinal-саммари статистический гейт обязан быть применён.
    gate_applied=False/отсутствие → корреляции уходят в конституции без фильтра
    (autocorr-фантомы, тавтологии, trend-confounds). Закрывает detection-without-delivery
    для correlation_gate."""
    import json as _json
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT findings FROM agent_reports "
            # тотальный порядок (read-your-writes): id DESC разрешает ничью по дате
            "WHERE agent_type = 'longitudinal_analysis' ORDER BY date DESC, id DESC LIMIT 1"
        ).fetchone()
    if not row or not row["findings"]:
        return None  # отсутствие саммари покрывает check_longitudinal_freshness
    try:
        gate = _json.loads(row["findings"]).get("gate")
    except (ValueError, TypeError) as exc:
        warn("longitudinal findings не парсятся как JSON", repr(exc))
        return None
    if not gate or not gate.get("gate_applied"):
        warn("longitudinal: статистический гейт НЕ применён",
             "корреляции попадают в конституции без фильтра (correlation_gate); "
             "проверь последний прогон longitudinal_analysis и импорт correlation_gate.")
    return gate


check("longitudinal гейт применён", check_longitudinal_gate_applied)


_UNSET = object()


def _last_belief_day() -> "str | None":
    """Дата последней опубликованной веры (YYYY-MM-DD) — якорь «прогон точно был».
    None = веры нет вовсе; тогда отсутствие квитанции законно (свежая машина)."""
    try:
        with db.get_conn() as conn:
            row = conn.execute("SELECT MAX(date) d FROM agent_reports "
                               "WHERE agent_type = 'longitudinal_analysis'").fetchone()
    except Exception as exc:  # noqa: BLE001
        warn("якорь квитанции не прочитан", f"{type(exc).__name__}: {exc} — БД недоступна, "
             "проверить пропажу квитанции нечем")
        return None
    return (row["d"][:10] if row and row["d"] else None)


def _last_belief_run_id() -> "str | None":
    """`run_id` прогона, записавшего последнюю веру. ИДЕНТИЧНОСТЬ, а не день.

    VG-R4-03: сверка «квитанция не старше веры» шла по ДАТЕ. Два прогона в одни сутки —
    ручной перезапуск, репетиция, параллельная сессия — по дате неотличимы, и квитанция
    ОДНОГО прогона подтверждала веру ДРУГОГО. Худший случай воспроизводим: упавший прогон,
    затем dry-run в тот же день — на диске `applied`, в БД вчерашняя вера, дат совпадают,
    датчик молчит. `run_id` уезжает в веру внутри `gate` и сверяется здесь буквально.
    None = вера старого формата (до 2026-07-26) — тогда падаем на сверку по дате."""
    try:
        from belief_contract import read_belief
        return (read_belief() or {}).get("run_id")
    except Exception as exc:  # noqa: BLE001
        warn("run_id веры не прочитан", f"{type(exc).__name__}: {exc} — сверка квитанции с "
             "верой пойдёт по дате, то есть слабее")
        return None


def check_gate_run_receipt(artifact: "Path | None" = None, belief_day=_UNSET):
    """Отказ гейта — событие с читателем, а не «строки просто нет».

    С 2026-07-26 упавший гейт НЕ публикует веру (fail-closed, P1-01). Побочный эффект:
    в agent_reports не появляется НИЧЕГО, и «прогон упал» становится неотличим от «прогон
    не запускался» — ровно тот класс молчания, который убил рельсу warn 13.07. Квитанция
    прогона (пишет longitudinal каждым прогоном, и успешным, и упавшим) делает отказ видимым.

    Уровень WARN, не FAIL: вера не испорчена, она заморожена на прошлом значении, и её
    возраст читатели показывают явно.

    ОТСУТСТВИЕ квитанции проверяется ЗДЕСЬ ЖЕ (ревью R3, VG-R3-03). До 2026-07-26 этот чек
    молчал на отсутствии, «делегируя» ветку liveness-датчику, а тот делегировал её обратно
    сюда — делегирование в пустоту, и оба молчания были заморожены тестами как правильное
    поведение. Якорь для отличия «ещё не было прогона» от «квитанция пропала» — сама вера:
    если в agent_reports есть longitudinal-строка, значит прогон случался, и квитанция обязана
    существовать и быть не старше этой строки.
    """
    from belief_contract import GATE_RUN_RECEIPT
    path = artifact or GATE_RUN_RECEIPT
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        _belief_day = belief_day if belief_day is not _UNSET else _last_belief_day()
        if _belief_day:
            warn("квитанция прогона гейта пропала",
                 f"вера от {_belief_day} есть, а {path.name} отсутствует — прогон был, "
                 "квитанции нет: отказ гейта больше не будет виден. Проверь права на logs/ "
                 "и последний прогон longitudinal_analysis.")
        return None   # прогонов ещё не было — законная тишина (свежая машина/новый деплой)
    except ValueError as exc:
        # Битая квитанция ≠ отсутствующая: кто-то писал и не дописал. Молчать нельзя —
        # тогда отказ гейта прячется за повреждением (класс «сторож ослеп молча»).
        warn("квитанция прогона гейта не читается", f"{path.name}: {exc!r}")
        return None
    if rec.get("status") == "failed":
        warn("longitudinal: гейт УПАЛ, вера не обновлена",
             f"прогон {rec.get('at')} завершился отказом ({rec.get('error')}); новых корреляций "
             "в конституциях нет, читатели подают прошлые с их датой. Перезапусти "
             "longitudinal_analysis на Studio и проверь импорт correlation_gate.")
        return rec
    if rec.get("status") == "started":
        # Конверт открыт и не закрыт (VG-R4-03). Раньше такого состояния не существовало:
        # убитый на полпути прогон оставлял на диске ПРОШЛУЮ applied, и «прогон не вернулся»
        # было неотличимо от «прогон прошёл успешно» — снова отсутствие события как норма.
        warn("longitudinal: прогон начался и не закрыл квитанцию",
             f"старт {rec.get('at')}, run_id={rec.get('run_id')} — процесс убит на полпути "
             "(OOM/SIGKILL/питание) либо идёт прямо сейчас. Проверь, жив ли процесс, и "
             "перезапусти longitudinal_analysis, если нет.")
        return rec
    # Квитанция — ТЭГИРОВАННЫЙ СОЮЗ, а не мешок полей (ревью R5, VG-R5-05). Живая продовая
    # квитанция на 2026-07-26 имела вид `applied + dry_run=true + published=false + phase=null`
    # без `run_id` — артефакт старой репетиции, которая писала в боевой путь (тот самый VG-R4-03,
    # с тех пор починенный). Датчик ветвился только по `failed|started` и принимал её как
    # успешный прогон: «код задеплоен» читалось как «путь исполнялся». Ниже — запрет несовместимых
    # комбинаций. Пока хоть одна стоит, доказательства прожига отремонтированного пути НЕТ.
    _in_sandbox = path.parent.name == "dryrun"        # песочница репетиции — её dry_run законен
    _bad = []
    if rec.get("status") == "applied":
        if rec.get("dry_run") and not _in_sandbox:
            _bad.append("dry_run=true в боевом пути")
        if not rec.get("published"):
            _bad.append("published=false")
        if rec.get("phase") != "closed":
            _bad.append(f"phase={rec.get('phase')!r} (ожидается 'closed')")
        if not rec.get("run_id"):
            _bad.append("run_id отсутствует")
    if _bad:
        warn("квитанция гейта не описывает завершённый боевой прогон",
             f"{path.name}: {', '.join(_bad)}. Это не доказательство успешного прогона: "
             "либо на диске лежит артефакт старой версии/репетиции, либо конверт закрыт неверно. "
             "Отремонтированный путь считается НЕ прожжённым, пока не появится квитанция "
             "status=applied, phase=closed, published=true, dry_run отсутствует, run_id совпадает "
             "с верой. Запусти longitudinal_analysis на Studio и сверь run_id.")
        return rec
    # Сверка ИДЕНТИЧНОСТИ, а не даты (VG-R4-03). Дата совпадает у двух прогонов одних суток;
    # `run_id` — нет. Расхождение значит: веру записал ОДИН прогон, а квитанцию оставил другой.
    _belief_day = belief_day if belief_day is not _UNSET else _last_belief_day()
    _at = str(rec.get("at", ""))[:10]
    _rid, _brid = rec.get("run_id"), _last_belief_run_id()
    if _rid and _brid and _rid != _brid:
        warn("квитанция и вера — от РАЗНЫХ прогонов",
             f"квитанция run_id={_rid}, вера run_id={_brid}. Дата могла совпасть, но это два "
             "разных прогона: успешная квитанция подтверждает не ту веру, что лежит в БД. "
             "Обычная причина — ручной перезапуск или параллельная сессия поверх ночного.")
    elif _belief_day and _at and _at < _belief_day:
        # Fallback для веры старого формата (run_id нет): слабее, но лучше молчания.
        warn("квитанция прогона гейта отстала от веры",
             f"вера от {_belief_day}, квитанция от {_at} — прогон, записавший веру, свою "
             "квитанцию не оставил; отказ следующего прогона может остаться невидимым.")
    return rec


check("квитанция прогона гейта", check_gate_run_receipt)


def check_belief_fresh_vs_data(conn=None):
    """Вера — это КЭШ, посчитанный из `daily_metrics`. У кэша не было инвалидации.

    Если исходные поля меняются после расчёта, вера остаётся снимком старых
    данных. Связь «данные изменились → кэш устарел» должна проверяться
    независимо от успешного завершения прежнего расчёта.

    Почему WARN, а не FAIL: вера не испорчена, она посчитана на прошлом снимке, и это
    информация для читателя, а не повод рушить ночной прогон. Почему не авто-ремонт
    (§13, ступень 1): единственное безопасное авто-действие — пересчёт, то есть запуск
    `longitudinal_analysis` со своим гейтом и своей квитанцией. Дёргать его из датчика
    значит связать отказ одного задания с отказом другого; пересчёт остаётся у своего
    расписания, а датчик называет, что именно устарело.

    ГРАНИЦА, названная вслух: механизм видит изменения ТОЛЬКО через `data_repair_log`.
    Правка мимо журнала (прямой UPDATE, импортёр, миграция без записи) им не ловится, и
    молчание этого датчика не означает «данные не менялись». Другой якорь — отметка
    времени записи в самих `daily_metrics` — дороже и в этот шаг не входит.

    ТЕНАНТ (ложный путь C-19): датчик покрывает ровно того тенанта, которого отдаёт
    `health_db.get_conn()` — то есть ту же область, что и сама вера и соседний
    `check_gate_run_receipt`. Это не покрытие всех тенантов; станет вера
    по-тенантной — за ней обязан пойти и этот датчик.
    """
    from belief_contract import read_belief
    b = read_belief(conn)
    if b["generated_at"] is None:
        return None                      # веры ещё нет — ветка check_gate_run_receipt
    if b["stale_vs_data"] is None:
        warn("устаревание веры не проверено",
             "журнал репарации не прочитан — сказать, менялись ли данные под верой, "
             "нечем. Это НЕ «данные не менялись»: проверь health.db и data_repair_log.")
        return b
    if b["stale_vs_data"]:
        warn("вера построена ДО последнего изменения данных",
             f"вера от {b['generated_at']}, данные правились {b['data_changed_at']} "
             f"(журнал репарации). Числа в конституциях посчитаны на снимке, которого "
             f"больше нет. Перезапусти longitudinal_analysis на Studio.")
    return b


check("вера свежее правок данных", check_belief_fresh_vs_data)


PROVENANCE_RUN_PREFIX = "auto-provenance-"


def check_sleep_stage_provenance(auto_repair: bool = True):
    """Фальшивые стадии сна не должны существовать — а если появились, чинятся САМИ.

    Если источник не измеряет стадии сна, запись отсутствия нулями создаёт
    ложные наблюдения для корреляций. Датчик проверяет провенанс и стережёт
    повторное появление таких строк через нового писателя или ручную вставку.

    §13 ступень 1, а не эскалация: safe-предикат тотальный и машинный (провенанс записи,
    не суждение о физиологии), уникальная информация не теряется (журнал хранит старые
    значения, откат исполним), действие логируется. Человека звать не за чем — звать
    его на то, что система чинит сама, значит тренировать игнорировать алерты.

    ЗАЧЕМ НЕ ТОЛЬКО АЛЕРТ И НЕ ТОЛЬКО ФИКС: зелёный датчик здесь доказывает, что предикат
    не сработал, — а не что данные чисты. Разница видна только при инъекции, поэтому
    оракул строится на мутациях, а не на зелёном прогоне (линза RST, Automation-Bias).

    ЭСКАЛАЦИЯ — ТОЛЬКО НА РЕЦИДИВ (§13 envelope d). Одна и та же сущность, починенная
    дважды, — это не ремонт, а война с писателем: система и писатель оба «работают
    правильно», данные мерцают, и никто этого не видит. Объём починки идёт в лог, а НЕ
    в порог: числа, отделяющего нормальный объём от аномального, у нас нет, и порог без
    основания был бы ровно тем, за что мы критикуем чужую калибровку.

    ГРАНИЦА: предикат ловит ОДИН класс — нули от известного прибора. Новый способ
    испортить провенанс он не увидит, и его молчание этого не обещает.
    """
    from migrations import repair_sleep_cycle_stages_20260731 as mig
    import health_db as hdb

    # §14 — liveness САМОГО СУЖДЕНИЯ, а не факта запуска. Молчащий датчик здесь
    # неотличим от датчика, чей предикат выродился в «всегда False»: и то и другое
    # выглядит как «данные чисты». Heartbeat это различие не ловит по построению,
    # поэтому перед каждым прогоном предикат сдаёт экзамен на двух эталонах —
    # положительном и отрицательном. Стоит две строки, а стережёт весь датчик.
    _fake = {"source": mig.SOURCE, **{k: 0 for k in mig.JSON_STAGES}}
    _real = {"source": "Oura", **{k: 1.0 for k in mig.JSON_STAGES}}
    if not mig.is_fake_stage_record(_fake) or mig.is_fake_stage_record(_real):
        warn("предикат провенанса сна выродился",
             "эталонная фальшивая запись больше не распознаётся либо настоящая "
             "распознаётся как фальшивая. Датчик молчал бы и на грязных данных, и это "
             "молчание читалось бы как чистота. Чини предикат "
             "(migrations/repair_sleep_cycle_stages_20260731.is_fake_stage_record).")
        return None

    try:
        plan = mig.apply("dry", write=False)
    except Exception as exc:  # noqa: BLE001
        warn("датчик провенанса сна не отработал",
             f"{type(exc).__name__}: {exc} — возврат фальшивых стадий сейчас НЕ стережётся")
        return None
    found = int(plan["json"]) + int(plan["sqlite"]) + int(plan["raw"])
    if found == 0:
        return {"found": 0, "repaired": 0}

    # Рецидив ищется ДО починки: та же причина, уже починенная и не откаченная, значит
    # значение вернулось после нашего ремонта.
    try:
        with hdb.get_conn() as conn:
            prior = conn.execute(
                "SELECT COUNT(*) FROM data_repair_log WHERE reason = ? AND run_id LIKE ? "
                "AND reverted_at IS NULL", (mig.REASON, PROVENANCE_RUN_PREFIX + "%")).fetchone()[0]
    except Exception as exc:  # noqa: BLE001
        warn("журнал репарации не прочитан перед авто-ремонтом",
             f"{type(exc).__name__}: {exc} — рецидив отличить нечем, чиню как первый раз")
        prior = 0

    if prior:
        warn("РЕЦИДИВ провенанса сна: чиненное вернулось",
             f"{found} полей снова подпадают под предикат, при том что {prior} записей уже "
             "чинились авто-ремонтом и не откатывались. Это не ремонт, а война с писателем: "
             "чини ПИСАТЕЛЯ, а не данные. Авто-ремонт остановлен, чтобы не мерцать данными.")
        return {"found": found, "repaired": 0, "recurrence": True}

    if not auto_repair:
        warn("фальшивые стадии сна вернулись", f"{found} полей; авто-ремонт выключен")
        return {"found": found, "repaired": 0}

    run_id = PROVENANCE_RUN_PREFIX + str(get_today())
    res = mig.apply(run_id, write=True)
    print(f"🔧 провенанс сна: авто-ремонт {found} полей, run_id={run_id} "
          f"(json={res['json']} sqlite={res['sqlite']} raw={res['raw']}); "
          f"откат — repair_sleep_cycle_stages_20260731.py --revert {run_id}")
    return {"found": found, "repaired": found, "run_id": run_id}


check("провенанс стадий сна (чинит сам)", check_sleep_stage_provenance)


# Ратчет смешения приборов ограничивает рост относительно настроенного baseline.
# Baseline не является клинической нормой или универсальным допустимым порогом.
# Его значение требует отдельного решения по коду; замена случайным числом
# изменила бы чувствительность датчика.
SLEEP_DEVICE_MIXING_KNOWN = 3


def _foreign_sleep_source(payload) -> "str | None":
    """Прибор блока сна из РАЗОБРАННОГО raw. Не кольцо → имя, иначе None.

    Разбор JSON намеренно НЕ здесь: тихо вернуть None на нечитаемой строке значит молча
    занизить счётчик смешения — ровно тот отказ, ради обнаружения которого датчик и
    написан. Нечитаемый raw обязан ронять чтение в громкую ветку вызывающего.
    """
    src = ((payload or {}).get("sleep") or {}).get("source")
    return src if src and src != "Oura" else None


def check_sleep_device_mixing():
    """Сон от одного прибора, ВСР от другого — в одной строке веры. Ратчет, не порог.

    ЗАЧЕМ. Воспроизводимость связи нужно проверять внутри каждого устройства.
    Приборы могут работать параллельно, поэтому календарное деление на эры
    само по себе не исключает смешение сна и ВСР от разных источников.

    ПОЧЕМУ РАТЧЕТ. Постоянная тревога на уже известном состоянии приучает
    игнорировать датчик; произвольный порог может скрыть ухудшение.
    Ратчет сообщает о росте относительно явно заданного baseline.
    Рост возвращает вопрос о разделении источников для отдельного решения.

    ГРАНИЦА. Ловит смешение ТОЛЬКО там, где чужой прибор дал сон, а кольцо — ВСР.
    Обратный случай (кольцо дало сон, чужой прибор что-то ещё) предикатом не покрыт:
    у телефонного приложения других полей нет. Появится третий прибор — граница врёт.

    ГРАНИЦА ТЕНАНТА (C-19). Смотрит одну базу — ту, что открывает get_conn.
    Другие тенанты не покрыты: состав приборов и baseline должны определяться
    отдельно для каждой установки. Перенос одного baseline между тенантами
    может дать как ложную тревогу, так и ложное отсутствие проблемы.
    """
    _foreign = {"sleep": {"source": "Sleep Cycle", "totalSleep": 7.0}}
    _ring = {"sleep": {"source": "Oura", "totalSleep": 7.0}}
    if _foreign_sleep_source(_foreign) is None or _foreign_sleep_source(_ring) is not None:
        warn("предикат смешения приборов выродился",
             "эталонная чужая запись больше не распознаётся либо запись кольца "
             "распознаётся как чужая. Датчик молчал бы и при растущем смешении, и это "
             "молчание читалось бы как чистота. Чини _foreign_sleep_source.")
        return None

    try:
        import health_db as hdb
        with hdb.get_conn() as conn:
            rows = conn.execute(
                "SELECT date, raw FROM daily_metrics "
                "WHERE raw IS NOT NULL AND hrv IS NOT NULL").fetchall()
        # Разбор ВНУТРИ громкой ветки: нечитаемый raw обязан быть слышен, а не занижать
        # счётчик молча. Одна испорченная строка роняет весь датчик — это дороже, чем
        # тихая недосчитанная ночь, и дешевле, чем ложная зелень.
        mixed = {str(d): src for d, raw in rows
                 if (src := _foreign_sleep_source(json.loads(raw)))}
    except Exception as exc:  # noqa: BLE001
        warn("датчик смешения приборов не отработал",
             f"{type(exc).__name__}: {exc} — рост смешения сейчас НЕ стережётся")
        return None
    if len(mixed) > SLEEP_DEVICE_MIXING_KNOWN:
        warn("смешение приборов в вере выросло",
             f"наблюдений с чужим сном и ВСР кольца {len(mixed)}, замерено и заморожено "
             f"{SLEEP_DEVICE_MIXING_KNOWN} (2026-08-01). Даты: "
             f"{', '.join(sorted(mixed)[:8])}. Требование внешнего статистика — "
             "воспроизводимость внутри устройства — держалось на малости этого числа, а "
             "не на правиле. Выросло → решай: разделять эры в расчёте или объяснять.")
    return {"mixed": len(mixed), "known": SLEEP_DEVICE_MIXING_KNOWN}


check("смешение приборов в вере (ратчет)", check_sleep_device_mixing)


# Три пропущенных недельных прогона longitudinal: пара висит в карантине, а вердикта нет.
# Оракул инженерный (каденция), не клинический — пересматривается по первому реальному случаю.
QUARANTINE_STUCK_DAYS = 21


def _quarantine_pending_rows(conn=None):
    """Чтение pending-строк карантина. Таблица появилась 2026-07-26: на не-мигрированной БД
    её может не быть, и это НЕ «карантин пуст» — возвращаем None (сведений нет) и кричим,
    а не [] (сведения есть, и они пустые). Разница между этими двумя — весь смысл датчика."""
    import sqlite3
    import quarantine_db
    try:
        return quarantine_db.quarantine_rows("pending", conn=conn)
    except sqlite3.OperationalError as exc:
        warn("таблица карантина недоступна", f"{exc!r} — init_db не прогонялся после 2026-07-26?")
        return None


def check_quarantine_stuck(conn=None):
    """Карантин без вердикта дольше QUARANTINE_STUCK_DAYS — затор, а не порядок.

    Ш3 сделал карантин персистентным: пара ждёт ЯВНОГО решения (P1-03 — раньше метка
    снималась сама через один стабильный прогон). У предохранителя есть цена: если вердикт
    не выносить, новые находки копятся в pending, гейт перестаёт публиковать новое — и это
    выглядит как зрелость («находок нет»), а не как затор. Датчик ровно против этого.
    """
    rows = _quarantine_pending_rows(conn)
    if rows is None:
        return None
    stuck = [r for r in rows if (r.get("age_days") or 0) > QUARANTINE_STUCK_DAYS]
    if stuck:
        _lst = ", ".join(f"{r['pair']} ({r['age_days']}д)" for r in stuck[:5])
        warn(f"карантин без вердикта: {len(stuck)} пар > {QUARANTINE_STUCK_DAYS}д",
             f"{_lst}. Вынеси вердикт: python3.11 scripts/adjudicate_quarantine.py "
             "--pair «...» --verdict admit|reject --reason «...» "
             "(разбор случаев: docs/how-to/adjudicate_quarantine.md)")
    # VG-R4-05: до этого места датчик считал ПО ТЕМ ЖЕ отфильтрованным строкам, что и читатель
    # веры. Строка с испорченным status/family/method_epoch выпадала из обеих выборок сразу —
    # и `{pending: 0, stuck: 0}` означало равно «вердиктов не ждут» и «состояние нечитаемо».
    # Здесь читаем таблицу БЕЗ фильтра по контракту: только так порча становится событием.
    corrupt, guarded = [], True
    try:
        import quarantine_db
        corrupt = quarantine_db.corrupt_quarantine_rows(conn)
        guarded = quarantine_db.quarantine_schema_guarded(conn)
    except Exception as exc:   # noqa: BLE001 — недоступность таблицы = сама по себе находка
        warn("карантин: состояние не проверить",
             f"{type(exc).__name__}: {exc} — pending/stuck ниже посчитаны, но за ними может "
             "стоять нечитаемая таблица. «Пусто» и «сломано» опять неотличимы.")
        corrupt = None
    if corrupt:
        _c = "; ".join(f"id={r['id']} {r['pair']}: {r['why']}" for r in corrupt[:5])
        warn(f"карантин: {len(corrupt)} строк вне контракта",
             f"{_c}. Такие строки НЕ видны ни читателю веры, ни счёту застоя выше — "
             "пара может ждать вердикта, которого никто не увидит.")
    if not guarded:
        warn("карантин: живая таблица без CHECK-ограничений",
             "passset_quarantine создана до ревью R4; CREATE TABLE IF NOT EXISTS ограничения "
             "не достраивает, а SQLite не умеет ALTER ADD CONSTRAINT. Защита держится только "
             "на этом датчике. Пересоздание таблицы — решение владельца, не миграция по пути.")
    return {"pending": len(rows), "stuck": len(stuck),
            "corrupt": (len(corrupt) if corrupt is not None else None),
            "schema_guarded": guarded}


check("карантин без вердикта", check_quarantine_stuck)


from belief_contract import MC_GAP_ARTIFACT
# Артефакт пишет ЕЖЕНЕДЕЛЬНЫЙ longitudinal → свежесть судится его расписанием
# (_schedule_covers, LONGITUDINAL_LABEL), не числом (баг Коммита3: порог 3д при недельном
# ритме кричал бы «замолк» каждую середину недели; с 23.09 числа нет вовсе).
MC_GAP_HOWTO = "methodology/validation_gate/HOWTO_mc_gap.md"


def check_mc_gap(artifact: "Path | None" = None):
    """MC-зазор: любой прошедший BY-вердикт лёг в ±2·MCSE от своей линии отбора → решение об
    «открытии» принято на разрешении Монте-Карло → триггер (а) Фаз 1/3-а (точное перечисление
    D / FFT-хвост A; PLAN_remaining Шаг 3). Кросс-процессный: гейт пишет артефакт ночью,
    этот чек читает 07:50.

    Само-активация ПО ФАКТУ существования артефакта (гейт запускался → обязан быть свежим),
    НЕ по env чекера. Freshness-гвард (штамп date) отделяет «зазор пуст» от «гейт замолк»:
    молчать на устаревшем содержимом = stale-read (consistency). Битый JSON → fail-loud, не тихо.
    Отсутствие артефакта — молчим: «гейт ни разу не бегал» уже покрыт check_longitudinal_freshness."""
    path = artifact or MC_GAP_ARTIFACT
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        warn("mc_gap артефакт не читается",
             f"{path.name}: {exc!r} — гейт пишет битый JSON? см. {MC_GAP_HOWTO}")
        return None
    if not isinstance(payload, dict):   # валидный JSON, но не объект → мягко (как sibling passset), не крэш
        warn("mc_gap артефакт неформатен", f"{path.name}: корень не объект — гейт пишет мусор? см. {MC_GAP_HOWTO}")
        return None
    stamp = payload.get("date")
    try:
        age = (today - date.fromisoformat(str(stamp)[:10])).days
    except (ValueError, TypeError):
        warn("mc_gap артефакт без валидного штампа",
             f"date={stamp!r} — не freshness-верифицируем; см. {MC_GAP_HOWTO}")
        return None
    if age < 0:   # штамп в будущем → часы/запись сломаны, не «свежий»
        warn("mc_gap артефакт со штампом из будущего", f"date={stamp!r} (age={age}д) — часы/запись сломаны? см. {MC_GAP_HOWTO}")
        return None
    covered, fire = _schedule_covers(LONGITUDINAL_LABEL, str(stamp)[:10])
    if covered is None:
        _schedule_unjudged("mc_gap датчик", LONGITUDINAL_LABEL)
        return None
    if not covered:
        warn(f"mc_gap датчик замолк: {age}д (плановый запуск {fire:%d.%m %H:%M} не отметился)",
             f"longitudinal не пишет артефакт ({stamp}, sha {payload.get('head_sha','?')}); "
             f"гейт в ungated-фолбэк? см. {MC_GAP_HOWTO}")
        return None
    _mcg = payload.get("mc_gap")
    fams = _mcg.get("families") if isinstance(_mcg, dict) else None
    if not isinstance(fams, dict):   # mc_gap/families не объект → нечего читать (не крэш на .items())
        return {"age_days": age, "flagged": [], "head_sha": payload.get("head_sha")}
    flagged = []
    for fam, data in fams.items():
        if not isinstance(data, dict):  # None = семья не считалась; список/скаляр = мусор → пропуск, не крэш
            continue
        for hit in data.get("flagged", []):
            flagged.append(f"{fam}:{hit.get('pair')} (p={hit.get('p')}, L={hit.get('line')})")
    if flagged:
        warn(f"MC-зазор: {len(flagged)} открытий на разрешении Монте-Карло",
             "решение принято в ±2·MCSE от линии отбора → триггер Фаз 1/3-а (точное "
             f"перечисление/FFT). Пары: {'; '.join(flagged)}. Реакция: {MC_GAP_HOWTO}")
    # Триггер BL-MCGAP-FLIP-1 (23.09): группа на шумовом полу — то, чего датчик зазора не видит.
    # Предикат живёт в correlation_gate (один дом): его же зовёт еженедельная задача владельца.
    import correlation_gate as _cg
    fragile = _cg.mc_resolution_fragile(fams)
    if fragile:
        warn(f"MC-триггер BL-MCGAP-FLIP-1: {len(fragile)} семья(и) пропускают пары на шумовом полу",
             "; ".join(fragile) + f". Контекст и вопрос владельцу: {_cg.MCGAP_TRIGGER_DOC}")
    return {"age_days": age, "flagged": flagged, "fragile": fragile,
            "head_sha": payload.get("head_sha")}


check("mc_gap: открытия на разрешении Монте-Карло (триггер Фаз 1/3-а)", check_mc_gap)


from belief_contract import PASSSET_ARTIFACT
# Снимок пишет еженедельный longitudinal → свежесть по его расписанию (_schedule_covers).
PASSSET_HOWTO = "methodology/validation_gate/HOWTO_passset_flicker.md"


def check_passset_flicker(artifact: "Path | None" = None):
    """Мерцание pass-set: членство прошедших пар изменилось между двумя прогонами → пара впервые
    прибыла/убыла из веры → это ПЕРВЫЙ вход онлайн-множественности (Фаза 4 предохранитель). Пока
    pass-set стабилен, «прошла хоть раз» ≡ «проходит всегда» — peeking-ущерба нет. Первое мерцание =
    триггер строить онлайн-контроллер (§5); мерцающая пара уходит в карантин (Коммит B).

    Читает ГОТОВОЕ поле `flicker_vs_prev` последнего снимка (longitudinal считает его ОДИН раз при
    записи, сравнивая с предыдущим снимком ЛЮБОЙ даты; ключ на head_sha снят 2026-07-25 — при
    104–298 коммитах в неделю он делал детектор немым навсегда. «Методология, а не данные»
    помечается вручную маркером logs/passset_epoch_break.txt). Тот же источник кормит карантин
    конституций (build_ai_summary) — единый расчёт, не два (split-brain-гард, Коммит B).
    Freshness (штамп date) отделяет «стабильно» от «снимок замолк». Битый JSON → fail-loud."""
    path = artifact or PASSSET_ARTIFACT
    if not path.exists():
        return None   # снимок ни разу не писался — покрыто check_longitudinal_freshness
    try:
        hist = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        warn("pass-set снимок не читается", f"{path.name}: {exc!r} — см. {PASSSET_HOWTO}")
        return None
    if not isinstance(hist, list) or not hist:
        warn("pass-set снимок пуст/неформатен", f"{path.name} — см. {PASSSET_HOWTO}")
        return None
    last = hist[-1]
    if not isinstance(last, dict):
        # Находка внешнего ревью 2026-07-25 (P2): валидный JSON-список `[42]` проходил верхний
        # гард и падал на `.get` с AttributeError. Тот же класс чинили в check_mc_gap @417feac —
        # фикс был точечным, sibling остался.
        warn("pass-set снимок неформатен", f"{path.name}: последний элемент не объект — см. {PASSSET_HOWTO}")
        return None
    stamp = last.get("date")
    try:
        age = (today - date.fromisoformat(str(stamp)[:10])).days
    except (ValueError, TypeError):
        warn("pass-set снимок без валидного штампа", f"date={stamp!r} — см. {PASSSET_HOWTO}")
        return None
    if age < 0:   # штамп в будущем → часы/запись сломаны, не «свежий»
        warn("pass-set снимок со штампом из будущего", f"date={stamp!r} (age={age}д) — часы/запись сломаны? см. {PASSSET_HOWTO}")
        return None
    covered, fire = _schedule_covers(LONGITUDINAL_LABEL, str(stamp)[:10])
    if covered is None:
        _schedule_unjudged("pass-set снимок", LONGITUDINAL_LABEL)
        return None
    if not covered:
        warn(f"pass-set снимок замолк: {age}д (плановый запуск {fire:%d.%m %H:%M} не отметился)",
             f"longitudinal не пишет снимок ({stamp}); гейт в ungated-фолбэк? см. {PASSSET_HOWTO}")
        return None
    _rst = last.get("history_reset")
    if _rst:
        # Писатель наткнулся на повреждённый артефакт и начал историю заново. До 2026-07-25 это
        # происходило МОЛЧА: читатель видел валидный свежий файл с одним снимком и говорил
        # «стабильно». Теперь факт сброса едет в снимке и кричит здесь.
        warn("pass-set история сброшена — базы сравнения нет",
             f"{_rst} (снимок {stamp}). Детектор мерцания сравнивать НЕ с чем: до накопления двух "
             f"прогонов «стабильно» ничего не значит. См. {PASSSET_HOWTO}")
    fv = last.get("flicker_vs_prev")
    if not fv:  # пусто/отсутствует = стабильно ИЛИ база намеренно закрыта маркером эпохи
        _br = last.get("epoch_break")
        return {"flicker": [], "note": f"эпоха закрыта вручную: {_br}" if _br else "стабильно"}
    flicker = []
    for fam in ("D", "A", "q_lag"):
        d = fv.get(fam)
        if not d:
            continue
        parts = []
        if d.get("entered"):
            parts.append(f"вошли: {', '.join(d['entered'])}")
        if d.get("left"):
            parts.append(f"убыли: {', '.join(d['left'])}")
        flicker.append(f"{fam} ({'; '.join(parts)})")
    if flicker:
        _p = last.get("prev") or {}
        _ctx = (f"сравнение {_p.get('date','?')} (sha {_p.get('head_sha','?')}) → "
                f"{stamp} (sha {last.get('head_sha','?')})")
        warn(f"pass-set замерцал: {len(flicker)} семей(ья) сменили членство",
             f"первое онлайн-прибытие. {_ctx}. {' | '.join(flicker)}. "
             f"ЕСЛИ между этими снимками ты правил решающее правило гейта — это МЕТОДОЛОГИЯ, "
             f"а не данные: зафиксируй это словами в снимке нити и контроллер не строй. "
             f"⛔ НЕ создавай маркер эпохи ПОСЛЕ этого алерта — он действует только ВПЕРЁД и "
             f"подавит СЛЕДУЮЩИЙ diff, который может быть настоящим прибытием (F2-01). "
             f"Иначе: карантин + строить онлайн-контроллер §5, см. {PASSSET_HOWTO}")
    return {"flicker": flicker, "at": stamp}


check("pass-set мерцание (Фаза 4 триггер онлайн-контроллера)", check_passset_flicker)


PASSSET_DEPTH_MIN_RATIO = 0.6   # доля ожидаемых по календарю снимков, ниже которой история неполна
PASSSET_HISTORY_MAX = 120       # зеркало longitudinal_analysis.PASSSET_HISTORY_MAX (cap → молчим)


def check_passset_history_depth(artifact: "Path | None" = None):
    """История снимков короче календаря → её подчистили или файл пересоздали.

    Зачем (2026-07-25): мерцание считается против ПРЕДЫДУЩЕГО снимка. Потеря истории = детектор
    снова слеп, но рапортует «стабильно» — тихий отказ ровно того класса, что снятый sha-ключ.
    Артефакт живёт в logs/ (вне git, вне бэкапа), в одном экземпляре на Studio — потерять легко,
    заметить нечем. Этот чек делает потерю ГРОМКОЙ, что дешевле переноса истории в БД.

    Оракул: между первым и последним снимком прошло N недель → снимков ожидаем ≈N+1 (каденция
    недельная); меньше 60% — история неполна. При упоре в cap молчим: обрезание сверху легитимно.
    Позит-контроль — tests/unit/test_passset_flicker.py (проредить историю → красный)."""
    path = artifact or PASSSET_ARTIFACT
    if not path.exists():
        return None                  # снимка ещё нет — покрыто check_longitudinal_freshness
    try:
        hist = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None                  # битый JSON уже кричит check_passset_flicker (fail-loud там)
    if not isinstance(hist, list) or len(hist) < 3:
        return None                  # база ещё набирается — судить рано
    try:
        first = date.fromisoformat(str(hist[0].get("date"))[:10])
        last = date.fromisoformat(str(hist[-1].get("date"))[:10])
    except (ValueError, TypeError):
        return None                  # штампы уже сторожит check_passset_flicker
    weeks = max(1, round((last - first).days / 7))
    expected = weeks + 1
    if len(hist) >= PASSSET_HISTORY_MAX:
        return {"snapshots": len(hist), "expected": expected, "capped": True}
    if len(hist) < expected * PASSSET_DEPTH_MIN_RATIO:
        warn(f"история pass-set неполна: {len(hist)} снимков за {weeks} недель (ожидалось ≈{expected})",
             f"историю подчистили или {path.name} пересоздан; детектор мерцания сравнивает с "
             f"предыдущим снимком — без истории он снова слеп, но молчит. См. {PASSSET_HOWTO}")
    return {"snapshots": len(hist), "expected": expected, "capped": False}


check("глубина истории pass-set (тихая потеря базы сравнения)", check_passset_history_depth)


def check_gate_artifacts_liveness(receipt=None, artifacts=None):
    """После УСПЕШНОГО прогона гейта обязательные артефакты обязаны существовать и быть не
    старше самого прогона.

    Аудит validation_gate 2026-07-26 (P2-02): `check_mc_gap`, `check_passset_flicker` и
    `check_passset_history_depth` на отсутствующем файле возвращают None — молча. Обоснование
    «freshness longitudinal уже покрывает» неверно ровно для интересного случая: longitudinal
    свеж, `gate_applied=True`, а отдельный writer получил OSError или файл удалили. Сам writer
    печатает предупреждение в лог, у которого нет обязательного читателя.

    Этот чек и есть тот читатель. Он НЕ дублирует соседей: они судят СОДЕРЖИМОЕ артефакта,
    он — сам факт его появления после applied-прогона. Поэтому их `return None` на отсутствии
    остаётся законным: делегирование явное и проверяемое.
    """
    from belief_contract import GATE_RUN_RECEIPT
    rpath = receipt or GATE_RUN_RECEIPT
    try:
        rec = json.loads(rpath.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None   # квитанции нет/битая — это ветка check_gate_run_receipt, не эта
    if rec.get("status") != "applied":
        return None   # прогон не удался — отсутствие артефактов законно
    run_day = str(rec.get("at", ""))[:10]
    required = artifacts if artifacts is not None else {
        "mc_gap": MC_GAP_ARTIFACT, "pass-set": PASSSET_ARTIFACT}
    missing, stale = [], []
    for name, p in required.items():
        if not Path(p).exists():
            missing.append(name)
            continue
        try:
            stamp = str(json.loads(Path(p).read_text(encoding="utf-8"))[-1].get("date")
                        if name == "pass-set"
                        else json.loads(Path(p).read_text(encoding="utf-8")).get("date"))[:10]
        except (OSError, ValueError, TypeError, IndexError, AttributeError, KeyError):
            stale.append(f"{name} (штамп не читается)")
            continue
        if run_day and stamp < run_day:
            stale.append(f"{name} (штамп {stamp} < прогона {run_day})")
    if missing or stale:
        warn("артефакты гейта не появились после успешного прогона",
             f"прогон {rec.get('at')} завершился applied, но: "
             f"{'нет ' + ', '.join(missing) + '. ' if missing else ''}"
             f"{'устарели ' + ', '.join(stale) + '. ' if stale else ''}"
             "Writer упал молча — датчики мерцания/MC-зазора считают по пустоте.")
    return {"run_day": run_day, "missing": missing, "stale": stale}


check("артефакты гейта после applied-прогона", check_gate_artifacts_liveness)


TRIAGE_LABEL = "com.larry.health.triage"
# Порогов-чисел здесь больше нет (решение владельца 23.09 «общее правило»: тревога после
# ПЕРВОГО пропущенного запуска). Было: рельса — 2 дня (сутки + пропуск), канал — 9 дней
# (неделя + два пропуска). Стало: рельса — квитанция покрывает последний плановый запуск
# триажа по живому плисту; канал — последняя доказанная доставка не раньше последнего
# понедельничной сводки (09:10 по дому — owner_weekly.last_scheduled_at).
# Посылка решения 07.09 сохранена: дайджест — гарантированная доставка раз в неделю.


def check_triage_delivery_liveness(base=None, now=None):
    """ЖИВА ЛИ САМА РЕЛЬСА ДОСТАВКИ warn (integrity → triage → Telegram).

    Инцидент 2026-07-13…26 (13 дней слепоты): `run_triage` вызывался ТОЛЬКО из
    `morning_report.__main__`; morning_report вывели из расписания 13.07 (его функцию передали
    gp_agent) — и доставка уехала вместе с носителем, будучи его ПАССАЖИРОМ. Ни один warn не
    долетел: `run_checks.sh` исправно писал `integrity_latest.json` с пометкой «triage разберётся
    в 08:00», а разбирать было некому. Хуже: `triage.log` показывал свежие записи каждый день —
    это писал юнит-тест, дёргавший guard. Наблюдаемость имитировала жизнь.

    Оракул: если в последнем integrity-json ЕСТЬ warnings, то должна существовать квитанция
    ПРОГОНА триажа, покрывающая последний плановый запуск, и она обязана нести канал у
    всего, что полагалось доставить. Нет warnings — доставлять нечего, молчим.

    FAIL-уровня СОЗНАТЕЛЬНО: сообщение о смерти рельсы не может ехать по этой же рельсе.
    AssertionError → 🚨-канал `run_checks.sh`, который шлёт напрямую, минуя триаж; независимый
    терминус — healthchecks dead-man (email). Решение владельца 2026-07-26.

    СУДИМЫЙ АРТЕФАКТ ОДИН — квитанция (2026-09-07). До сегодня датчик сравнивал маркер
    `triage_done_*.flag` с квитанцией, а они писались по РАЗНЫМ условиям: маркер — каждый
    прогон, квитанция — только когда было что доставлять. В тихий день они расходились, и
    наутро сюда прилетал ложный warn «квитанция отстала от маркера» (2 тихих дня из 2 с
    29.07). Хуже: стоявший здесь `assert stale_receipt or via not in (...)` в этом состоянии
    НЕ ПРОВЕРЯЛ канал вовсе — сторож последнего метра был выключен в день после каждого
    тихого дня (воспроизведено: via='none' при отставшей квитанции FAIL не дал). Теперь
    квитанцию пишет каждый прогон ПЕРЕД маркером, поэтому она же и есть сигнал живости;
    маркер остался тем, чем всегда был по сути, — защёлкой идемпотентности в run_triage,
    и судьёй быть перестал. Два дома одного факта — §15/§18."""
    if base is None and (skip := _owner_judged_in_container("рельса доставки")):
        return skip
    root = Path(base) if base else Path(__file__).resolve().parent
    logs = root / "logs"
    jpath = logs / "integrity_latest.json"
    if not jpath.exists():
        return None            # первый запуск/чистая машина — судить не о чем
    try:
        data = json.loads(jpath.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        warn("integrity_latest.json не читается", f"{exc!r} — триаж не сможет его разобрать")
        return None
    n_warn = len(data.get("warnings") or [])
    rec_path = logs / "triage_delivery_latest.json"
    rec = {}
    if rec_path.exists():
        try:
            loaded = json.loads(rec_path.read_text(encoding="utf-8"))
            rec = loaded if isinstance(loaded, dict) else {}
        except (ValueError, OSError) as exc:
            warn("квитанция прогона триажа не читается", f"{exc!r} — судить о доставке нечем")
    # Без try/except: невалидную дату отсеиваем ФИЛЬТРОМ, а не глушением исключения — иначе это
    # ровно тот класс, который сторож `test_silent_handler_guard` и ловит (он поймал меня здесь).
    # `0 <= age` — штамп из будущего тоже поломка, а не свежесть (урок внешнего ревью 24.07).
    import re as _re
    import triage_agent as _triage   # дом правила «что считать доказанной доставкой» — там
    _d = str(rec.get("date") or "")
    run_day = date.fromisoformat(_d) if _re.fullmatch(r"\d{4}-\d{2}-\d{2}", _d) else None
    age = (today - run_day).days if run_day else None

    # ── 1. Жива ли рельса: квитанция покрывает последний плановый запуск триажа ──
    covered, fire = _schedule_covers(TRIAGE_LABEL, str(run_day) if run_day else None, now)
    assert fire is not None, (
        f"РЕЛЬСА ДОСТАВКИ не судима: расписание {TRIAGE_LABEL} не выводится из живого "
        f"плиста — «не знаю» не равно «рельса жива». В integrity {n_warn} предупреждений.")
    assert age is not None and age >= 0 and covered, (
        f"РЕЛЬСА ДОСТАВКИ warn МЕРТВА: в integrity {n_warn} предупреждений, а последний "
        f"прогон триажа — {run_day or 'НИКОГДА'}"
        f"{f' ({age}д назад)' if age is not None else ''}, плановый запуск {fire:%d.%m %H:%M} "
        f"не отметился. Проверь launchd-агент {TRIAGE_LABEL}: "
        f"launchctl list | grep health.triage. Ни один датчик сейчас НЕ доходит."
    )

    # ── 2. Доказана ли доставка того, что полагалось доставить ─────────────────
    # 'none' — оба канала легли. None/'' — канал не назван: квитанцию писал не триаж (до
    # 2026-08-07 её путь был модульной константой, и тест затирал боевой файл каждую ночь).
    # Безымянный канал = доставка НЕ ДОКАЗАНА (§14: liveness ≠ корректность; §20: зелёный,
    # причинённый чужим файлом, — не зелёный). `sent` по умолчанию True: у квитанций до
    # 2026-09-07 поля нет, но они писались только при отправке.
    via, via_d = rec.get("via"), rec.get("via_digest")
    due = (rec.get("questions") or 0) + (rec.get("digest") or 0)
    proven_now = via in _triage.PROVEN_CHANNELS or via_d in _triage.PROVEN_CHANNELS
    assert not (rec.get("sent", True) and due) or proven_now, (
        f"ДОСТАВКА НЕ ДОКАЗАНА: триаж отработал ({rec.get('date')}), полагалось доставить "
        f"{rec.get('questions', '?')} вопросов и {rec.get('digest', '?')} пунктов дайджеста, "
        f"каналы в квитанции = {via!r}/{via_d!r}. "
        + ("Оба канала вернули 'none' — ни Telegram, ни резервный healthchecks; человек "
           "предупреждений НЕ получил. Проверь секреты telegram_token/telegram_chat_id "
           "и healthcheck_url."
           if "none" in (via, via_d) else
           "Канал НЕ НАЗВАН — квитанцию писал не триаж (чужой процесс или тест). Сверься с "
           "logs/triage.log: строка «USER QUESTIONS sent via=...» — независимый свидетель.")
    )

    # ── 3. Бюджет молчания канала (решение владельца 2026-09-07) ───────────────
    # Пункт 2 молчит в тихий день — доставлять было нечего, доказывать нечего. Но тогда
    # канал может быть мёртв неделями и обнаружиться на первом же настоящем варне. Здесь
    # судится не прогон, а ДАТА последней доказанной доставки: она переносится вперёд
    # писателем квитанции и стареет сама (§18 — у утверждения о соседе есть срок годности).
    import owner_weekly
    weekly = {}
    weekly_path = owner_weekly.receipt_path(Path(base) if base else None)
    if weekly_path.exists():
        try:
            weekly = json.loads(weekly_path.read_text(encoding="utf-8"))
            if not isinstance(weekly, dict):
                raise ValueError("receipt must be an object")
        except (ValueError, OSError):
            weekly = {}
            warn("квитанция недельной сводки не читается", "доставка ею не подтверждена")
    # У сводки засчитываем только названный транспорт, не произвольный last_proven.
    candidates = [_triage.last_proven_of(rec)]
    if weekly.get("sent") is True and weekly.get("via") in _triage.PROVEN_CHANNELS:
        candidates.append(_triage.last_proven_of({"date": weekly.get("date"), "via": weekly["via"]}))
    _lp = max((str(d) for d in candidates if d and _re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(d))
               and str(d) <= today.isoformat()), default="")
    proven_day = date.fromisoformat(_lp) if _re.fullmatch(r"\d{4}-\d{2}-\d{2}", _lp) else None
    proven_age = (today - proven_day).days if proven_day else None
    # Утренний триаж идёт раньше 09:10: до срока сводки судим прошлую неделю.
    digest_day = owner_weekly.last_scheduled_at(now or get_now()).date()
    assert proven_day is not None and digest_day <= proven_day <= today, (
        f"КАНАЛ ДОСТАВКИ НЕ ПОДТВЕРЖДЁН: последняя доказанная доставка — "
        f"{proven_day or 'НИКОГДА'}"
        f"{f' ({proven_age}д назад)' if proven_age is not None else ''}, а плановый дайджест был "
        f"{digest_day:%d.%m}. Рельса жива (триаж отработал {run_day}), но "
        f"по каналу давно ничего не уезжало — доказательства, что он работает, нет. Прогони "
        f"`python3.11 owner_weekly.py` в понедельник после 09:10 и проверь квитанцию доставки."
    )
    return {"warnings": n_warn, "run_day": str(run_day), "age_days": age,
            "delivered_via": via, "digest_via": via_d, "due": due,
            "last_proven": str(proven_day), "proven_age_days": proven_age}


check("жива ли рельса доставки warn (integrity→triage→Telegram)", check_triage_delivery_liveness)


def check_tenant_triage_alive(base=None):
    """Утренний разбор ЧЕЛОВЕКА-ТЕНАНТА жив (28.09, решение владельца «партнёру — свой разбор»).

    У владельца рельсу стережёт check_triage_delivery_liveness (по его logs/ и плисту). У
    тенанта разбор идёт цепочкой в run_checks.sh сразу за его вердиктом и пишет квитанцию в
    <данные тенанта>/logs. Без этого датчика тихо умерший разбор партнёра выглядел бы как
    «вопросов к нему нет». Судим вчерашний прогон: вердикт монитора в logs тенанта уже лежит
    (прошлый прогон), квитанция разбора обязана быть не старше его. FAIL — у тенанта находки
    уходят оператору только из failures (строкой имён)."""
    import os
    import secrets_paths as _sp
    if base is None:
        if _sp.is_owner_data():
            return None                         # владельца стережёт свой датчик
        base = os.environ.get("HEALTH_DATA_DIR")
        if not base:
            return None
    logs = Path(base) / "logs"
    art = logs / "integrity_latest.json"
    if not art.exists():
        return None                             # первый прогон тенанта — судить не о чем
    # Битый JSON не глушим: исключение — само по себе красный вердикт датчика.
    art_day = str(json.loads(art.read_text(encoding="utf-8")).get("date") or "")
    rec = logs / "triage_delivery_latest.json"
    rec_day = str(json.loads(rec.read_text(encoding="utf-8")).get("date") or "") if rec.exists() else ""
    assert art_day and rec_day and rec_day >= art_day, (
        f"утренний разбор тенанта не отработал: вердикт монитора от {art_day or '?'}, "
        f"квитанция разбора — {rec_day or 'НЕТ'} (logs тенанта)")
    return {"verdict_day": art_day, "triage_day": rec_day}


check("утренний разбор человека-тенанта жив", check_tenant_triage_alive)


def check_gp_schedule():
    """GP monthly ≤35д."""
    with db.get_conn() as conn:
        monthly = conn.execute(
            "SELECT MAX(date) as last FROM agent_reports "
            "WHERE agent_name LIKE '%monthly%' OR agent_name LIKE '%month%'"
        ).fetchone()
    if monthly and monthly["last"]:
        age = (today - date.fromisoformat(monthly["last"])).days
        if age > GP_MONTHLY_MAX_AGE_DAYS:
            warn(f"GP monthly отчёт устарел: {age}д (порог {GP_MONTHLY_MAX_AGE_DAYS}д)",
                 f"последний: {monthly['last']}")
    else:
        warn("нет GP monthly отчётов", "gp_agent monthly не запускался?")


check("расписание GP monthly", check_gp_schedule)


# ─────────────────────────────────────────────────────────────────────────────
if not JSON_OUTPUT:
    print(f"\n[9] Консистентность: review_date, гипотезы, периоды")


def check_review_dates():
    """Проблемы и протоколы с просроченным review_date."""
    with db.get_conn() as conn:
        cols_p = [r[1] for r in conn.execute("PRAGMA table_info(problem_list)").fetchall()]
        cols_r = [r[1] for r in conn.execute("PRAGMA table_info(protocols)").fetchall()]
        op = oc = 0
        if "review_date" in cols_p:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM problem_list "
                "WHERE review_date IS NOT NULL AND review_date < date('now') "
                "AND status NOT IN ('resolved')"
            ).fetchone()
            op = row["cnt"] if row else 0
        if "review_date" in cols_r:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM protocols "
                "WHERE review_date IS NOT NULL AND review_date < date('now') "
                "AND status='active'"
            ).fetchone()
            oc = row["cnt"] if row else 0
    if op > 0:
        warn(f"{op} проблем с просроченным review_date", "требуют пересмотра")
    if oc > 0:
        warn(f"{oc} протоколов с просроченным review_date", "требуют пересмотра")
    return (op, oc)


check("review_date проблем и протоколов", check_review_dates)


# check_hypothesis_experiments снят (BL-EXP-1, 2026-07-10): мерил мёртвую модель
# «гипотеза обязана иметь активный эксперимент». Сходимость теперь через consilium-
# круги (resolve_hypothesis: version++/history). Замена — датчик «partial после N
# кругов» — отдельной задачей (см. docs/explanation/hypothesis_engine.md).


def check_periods_expiry():
    """Клинические периоды: фазы watchful_waiting с истёкшей датой без next phase.

    PERIODS-SEMANTICS (2026-06-19, D++ hybrid): переписано на новую семантику.
    После migration deleted_at — soft-delete; current/past через end_date.

    Алерт срабатывает только для watchful_waiting (тип где истечение означает
    «нужно решить что дальше» — наблюдение завершилось, требуется human-decision).
    Whitelist лечебных типов убран — для них истечение естественно.

    Дополнительно: если за период есть **более новая** фаза (start_date >
    end_date этой) — значит решение принято реальностью (рецидив→лечение),
    не алертим. Пример: фаза наблюдения закончилась, на следующий день началась
    новая лечебная фаза — не алерт.
    """
    today = str(db.get_today())
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, name, end_date FROM periods "
            "WHERE type = 'watchful_waiting' "
            "  AND deleted_at IS NULL "
            "  AND end_date IS NOT NULL AND end_date < ?",
            (today,)
        ).fetchall()
        unresolved = []
        for r in rows:
            next_phase = conn.execute(
                "SELECT 1 FROM periods WHERE start_date > ? "
                "AND deleted_at IS NULL LIMIT 1",
                (r["end_date"],)
            ).fetchone()
            if not next_phase:
                unresolved.append(r["name"])

    if unresolved:
        warn(f"{len(unresolved)} клинических периодов watchful_waiting без next phase",
             f"требуют обновления после консультации с врачом: {', '.join(unresolved)}")
    return len(unresolved)


check("актуальность клинических периодов", check_periods_expiry)


# ── Даты терапии: periods против problem_list (нить treatment-facts, 2026-08-30) ─────────
# Замер 30.08: окончание одной терапии жило в четырёх домах (problem_list, periods,
# memory-observation, куратор) четырьмя РАЗНЫМИ датами; владелец назвал пятую — верную.
# Никто не сравнивал дома. Primary дат терапии — `periods`; всё в заголовках
# проблем — копия без инвалидации. Датчик делает расхождение видимым, пока копии не сняты (ход 1).
_RU_MONTHS = ("январ", "феврал", "март", "апрел", "ма[йя]", "июн", "июл", "август",
              "сентябр", "октябр", "ноябр", "декабр")
_MONTH_YEAR_RE = re.compile(r"(" + "|".join(_RU_MONTHS) + r")[а-я]*\s+(20\d\d)", re.I)
_THERAPY_PERIOD_TYPES = ("immunotherapy", "treatment", "chemoradiation", "chemotherapy", "radiotherapy")
TREATMENT_DATE_TOLERANCE_DAYS = 45


def _month_year_dates(text: str) -> list:
    """«март 2020 — май 2020» → [date(2020,3,1), date(2020,5,1)] (условный пример) в порядке появления."""
    out = []
    for m in _MONTH_YEAR_RE.finditer(text or ""):
        stem = m.group(1).lower()
        idx = next(i for i, pat in enumerate(_RU_MONTHS) if re.match(pat, stem))
        out.append(date(int(m.group(2)), idx + 1, 1))
    return out


def _name_stems(text: str) -> set:
    """Стемы слов ≥5 букв (первые 6): «препаратом» и «Препарат» → «препар»."""
    return {w[:6] for w in re.findall(r"[a-zа-яё]{5,}", (text or "").lower())}


def treatment_date_disagreements(periods: list, problems: list, tol_days: int = TREATMENT_DATE_TOLERANCE_DAYS):
    """ЧИСТАЯ функция (тестируема без БД). periods: dict(name,type,start_date,end_date);
    problems: dict(problem_id,title,first_seen,resolved_date).

    Возвращает (mismatches, unpaired): mismatch = (period_name, problem_id, поле, дата периода,
    дата проблемы, Δдней); unpaired — терапии без проблемы с общим стемом названия. Связка по
    стему нарочно грубая и НАЗВАНА: «Химиотерапия — второй курс» ↔ «Exampla» пары не дадут — это
    попадёт в unpaired, а не в тихий зелёный."""
    mismatches, unpaired = [], []
    for per in periods:
        pstems = _name_stems(per["name"])
        mates = [pr for pr in problems if pstems & _name_stems(pr["title"])]
        if not mates:
            unpaired.append(per["name"])
            continue
        p_start = date.fromisoformat(per["start_date"]) if per.get("start_date") else None
        p_end = date.fromisoformat(per["end_date"]) if per.get("end_date") else None
        for pr in mates:
            title_dates = _month_year_dates(pr["title"])
            cands = {"start": [], "end": []}
            if pr.get("first_seen"):
                cands["start"].append(("first_seen", date.fromisoformat(pr["first_seen"][:10])))
            if pr.get("resolved_date"):
                cands["end"].append(("resolved_date", date.fromisoformat(pr["resolved_date"][:10])))
            if len(title_dates) >= 2:
                cands["start"].append(("title", title_dates[0]))
                cands["end"].append(("title", title_dates[-1]))
            for field, ref in (("start", p_start), ("end", p_end)):
                if ref is None:
                    continue
                for src, d in cands[field]:
                    # месяц-год из заголовка = «в этом месяце»: сравниваем по началу месяца периода
                    ref_cmp = ref.replace(day=1) if src == "title" else ref
                    delta = abs((d - ref_cmp).days)
                    if delta > tol_days:
                        mismatches.append((per["name"], pr["problem_id"], f"{field}/{src}",
                                           str(ref), str(d), delta))
    return mismatches, unpaired


# ── Дома фактов о лечении, кроме problem_list (нить treatment-homes, 04.10.2026) ──────────
# Решение владельца 04.10: главный дом дат лечения — `periods`; `medications` и дата ремиссии в
# профиле подчинены. До этой правки датчик сверял одну пару из пяти домов и был зелёным на
# живой базе при двух статусах вне словаря, трёх дублях и режиме, «начатом» датой визита.
_MED_FAMILY = {"chemo": ("treatment", "chemotherapy"), "chemoradiation": ("chemoradiation",),
               "immunotherapy": ("immunotherapy",), "radiation": ("radiotherapy",)}
_REMISSION_DATE_RE = re.compile(r"ремисс[а-я]*\s+с\s+(\d{1,2})\.(\d{4})", re.I)


def _med_date(s):
    """'2024-10' / '2024-10-05' / None → date (месяц без дня = первое число)."""
    if not s:
        return None
    s = str(s)[:10]
    return date.fromisoformat(s if len(s) == 10 else (s + "-01")[:10])


def medication_fact_findings(meds: list, periods: list, statuses: tuple, today: date,
                             diagnosis: str = "", tol_days: int = TREATMENT_DATE_TOLERANCE_DAYS):
    """ЧИСТАЯ функция. meds — подтверждённые строки medications (id,name,modality,start_date,
    end_date,status); periods — живые строки periods (name,type,start_date,end_date).
    Возвращает (findings, unchecked): finding = (вид, текст). Режим внутри своего периода
    (поддерживающая фаза внутри курса) — норма; режим вне всех периодов семьи — расхождение."""
    tol = timedelta(days=tol_days)
    out, unchecked = [], []
    for m in meds:
        tag = f"#{m['id']} {m['name']}"
        st, s, e = m.get("status"), _med_date(m.get("start_date")), _med_date(m.get("end_date"))
        if st not in statuses:
            out.append(("статус", f"{tag}: status={st!r} вне словаря {statuses}"))
        if st == "active" and e and e < today:
            out.append(("статус", f"{tag}: active, но закончен {e}"))
        fam = _MED_FAMILY.get(m.get("modality") or "")
        if not fam or s is None:
            unchecked.append(tag)
            continue
        mates = [p for p in periods if p["type"] in fam and _med_date(p["start_date"])]
        inside = [p for p in mates
                  if s >= _med_date(p["start_date"]) - tol
                  and (e or s) <= (_med_date(p["end_date"]) or today) + tol]
        if not inside:
            out.append(("период", f"{tag} {s}…{e or '?'} не укладывается ни в один период "
                                  f"{'/'.join(fam)} (главный дом дат — periods)"))
        elif st in ("active", None) and all(_med_date(p["end_date"]) and _med_date(p["end_date"]) + tol < today
                                            for p in inside):
            out.append(("статус", f"{tag}: период окончен, а режим не закрыт"))
    by_name = {}
    for m in meds:
        by_name.setdefault(m["name"], []).append(m)
    for name, rows in by_name.items():
        for i, a in enumerate(rows):
            for b in rows[i + 1:]:
                a0, a1 = _med_date(a.get("start_date")), _med_date(a.get("end_date"))
                b0, b1 = _med_date(b.get("start_date")), _med_date(b.get("end_date"))
                apart = a0 and b0 and ((a1 and a1 + tol < b0) or (b1 and b1 + tol < a0))
                if not apart:
                    out.append(("дубль", f"{name}: #{a['id']} и #{b['id']} — одна схема дважды "
                                         f"с пересекающимися или неизвестными датами"))
    m = _REMISSION_DATE_RE.search(diagnosis or "")
    rem = [p for p in periods if p["type"] == "remission" and p.get("start_date")]
    if m and rem:
        stated = date(int(m.group(2)), int(m.group(1)), 1)
        primary = max(_med_date(p["start_date"]) for p in rem)
        if abs((stated - primary.replace(day=1)).days) > tol_days:
            out.append(("ремиссия", f"профиль: ремиссия с {stated:%m.%Y}, periods: с {primary}"))
    return out, unchecked


def check_treatment_dates_agree():
    """Даты терапий в `periods` (primary) против `problem_list` (копии в полях и заголовках),
    `medications` (подтверждённые режимы) и даты ремиссии в профиле."""
    with db.get_conn() as conn:
        periods = [dict(r) for r in conn.execute(
            "SELECT name, type, start_date, end_date FROM periods "
            "WHERE deleted_at IS NULL AND type IN (%s)" % ",".join("?" * len(_THERAPY_PERIOD_TYPES)),
            _THERAPY_PERIOD_TYPES).fetchall()]
        problems = [dict(r) for r in conn.execute(
            "SELECT problem_id, title, first_seen, resolved_date FROM problem_list").fetchall()]
        all_periods = [dict(r) for r in conn.execute(
            "SELECT name, type, start_date, end_date FROM periods WHERE deleted_at IS NULL")]
        meds = [dict(r) for r in conn.execute(
            "SELECT id, name, modality, start_date, end_date, status FROM medications "
            "WHERE COALESCE(confirmation,'proposed') IN ('confirmed','manual')")]
        dx = conn.execute("SELECT value_text FROM patient_profile "
                          "WHERE key='medical.diagnosis'").fetchone()
    from treatment_db import MED_STATUSES
    facts, med_unchecked = medication_fact_findings(
        meds, all_periods, MED_STATUSES, today, diagnosis=(dx[0] if dx else "") or "")
    if facts:
        warn(f"факты о лечении расходятся с главным домом periods ({len(facts)})",
             "; ".join(f"[{k}] {t}" for k, t in facts)
             + ". Главный дом — periods (решение владельца 04.10); копия чинится через log_repair")
    if med_unchecked and not JSON_OUTPUT:
        print(f"     ℹ режимы без даты начала или семьи — НЕ сверены ({len(med_unchecked)}): "
              f"{', '.join(med_unchecked)}")
    mism, unpaired = treatment_date_disagreements(periods, problems)
    if mism:
        lines = [f"{m[0]} ↔ {m[1]} [{m[2]}]: period {m[3]} vs problem {m[4]} (Δ{m[5]}д)" for m in mism]
        warn(f"даты терапии расходятся между periods и problem_list ({len(mism)})",
             "; ".join(lines) + ". Primary — periods; копия в проблеме — кандидат на снятие (нить treatment-facts)")
    if unpaired and not JSON_OUTPUT:
        # Не WARN (это не расхождение), но и не молчание: «не сверено» ≠ «сошлось».
        print(f"     ℹ терапии без пары в problem_list по названию ({len(unpaired)}): "
              f"{', '.join(unpaired)} — датчик их НЕ сверяет")
    return len(mism) + len(facts)


check("даты терапии: periods ↔ problem_list, medications, профиль", check_treatment_dates_agree)


def check_treatment_history_extracted():
    """Лечение — производное из документов (medications), не ручная строка.
    Если есть эпизоды лечения, а medications пуста — extractor/бэкофилл не отработал
    (риск: бриф недосчитывает курсы, как было с числом курсов схемы в брифе). Плюс сигналит,
    сколько режимов ждут человек-гейта."""
    with db.get_conn() as conn:
        # общие слова + имена схем ТЕНАНТА (его словарь синонимов, приватные данные)
        _words = ["хими", "chemo", "иммун", "лечени", "терапи"] + list(db._REGIMEN_SYNONYMS)
        eps = conn.execute(
            "SELECT COUNT(*) c FROM episodes_of_care WHERE "
            + " OR ".join("lower(title) LIKE ?" for _ in _words),
            [f"%{w}%" for w in _words],
        ).fetchone()["c"]
        meds = conn.execute("SELECT COUNT(*) c FROM medications").fetchone()["c"]
        proposed = conn.execute(
            "SELECT COUNT(*) c FROM medications WHERE confirmation='proposed'"
        ).fetchone()["c"]
    if eps > 0 and meds == 0:
        warn("эпизоды лечения есть, а medications пуста",
             "treatment_extractor/бэкофилл не отработал — бриф недосчитает курсы")
    if proposed > 0:
        warn(f"{proposed} режимов лечения ждут подтверждения (гейт)",
             "подтверди карточки в Telegram — иначе не войдут в историю")
    return {"episodes": eps, "medications": meds, "proposed": proposed}


check("лечение извлечено из документов", check_treatment_history_extracted)


# ── Страж рецидива: зашитый диагноз в фикс-сайтах (нить diagnosis-hardcode) ────
# Чистое ядро скана + список сайтов — в diagnosis_guard.py (тестируемо без запуска
# ночной сюиты; единый источник списка — без split-brain). Здесь только чек-обёртка.
# Позит-контроль обоих направлений — tests/unit/test_no_hardcoded_diagnosis.py.
import diagnosis_guard as _dxguard

def check_no_hardcoded_diagnosis():
    """FAIL: снятый онко-литерал вернулся в промпт-билдер (нить diagnosis-hardcode)."""
    hits = _dxguard.scan(Path(__file__).parent)
    assert not hits, "рецидив зашитого диагноза:\n    " + "\n    ".join(hits)


check("нет зашитого диагноза в промпт-билдерах (нить diagnosis-hardcode)",
      check_no_hardcoded_diagnosis)


def check_doc_classifier_seeded():
    """WARN: tenant_doc_patterns.yaml есть, но его паттерны НЕ в doc_patterns — per-tenant
    сид не отработал (тихая дыра: личные паттерны тенанта потеряны, документы не классиф.).
    A3: liveness сида, вынесенного из общего кода в per-tenant yaml (fallback_needs_sensor).
    Утечку owner-PII в чужого тенанта структурно гасят: код больше не сеет её всем + страж
    перепись pii_census ловит ре-хардкод по всей публичной зоне. «Одноразовая чистка БД партнёра»
    числилась здесь сделанной, но не состоялась: 4 строки владельца (врачи, фамилия) лежали в
    doc_patterns партнёра до 2026-09-23 — удалены, копия в ~/health_partner/backups/ (pub-prep)."""
    tenant_rows = db._load_tenant_doc_patterns()
    if not tenant_rows:
        return  # тенант без личного yaml — нечего проверять
    with db.get_conn() as c:
        have = {(r["pattern"], r["doc_type"]) for r in
                c.execute("SELECT pattern, doc_type FROM doc_patterns").fetchall()}
    missing = [(r[0], r[1]) for r in tenant_rows if r[0] and (r[0], r[1]) not in have]
    if missing:
        warn("doc_classifier: per-tenant паттерны не засеяны",
             f"{len(missing)} из yaml нет в doc_patterns: {missing[:5]} — сид сломан?")


check("классификатор документов: per-tenant сид засеян (A3 liveness)",
      check_doc_classifier_seeded)


# ── Страж канона CPIC (нить diagnosis-hardcode A2-full) ───────────────────────
# Дашборд (/pharmacogenetics) и консилиум (pharmaco_context) читают справочник CPIC
# ИЗ БД (cpic_reference_db, 3 проекции, один SEED_VERSION). Пустой/рассогласованный
# канон = молча пустой мед-вывод и «не определён» вместо реального риска (R5). Хард-чек.
import cpic_reference_db as _cpic

def check_cpic_canon_consistent():
    """FAIL: канон CPIC (3 проекции) не засеян или рассогласован по seed_version/ссылкам."""
    with db.get_conn() as c:
        def _count(t):
            try:
                return c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            except Exception:
                return None
        n_cat, n_risk, n_impl = (_count("cpic_drug_catalog"),
                                 _count("cpic_drug_risk"),
                                 _count("cpic_gene_implication"))
        assert n_cat and n_risk and n_impl, (
            f"канон CPIC не засеян: catalog={n_cat} risk={n_risk} impl={n_impl} "
            "(cpic_reference_db.seed не отработал в init_db)")
        vers = set()
        for t in ("cpic_drug_catalog", "cpic_drug_risk", "cpic_gene_implication"):
            vers |= {r[0] for r in c.execute(f"SELECT DISTINCT seed_version FROM {t}").fetchall()}
        _want = _cpic.seed_version()   # SEED_VERSION + отпечатки снимка CPIC и слоя установки
        assert vers == {_want}, (
            f"seed_version рассогласован: {vers} ≠ {{{_want!r}}} (split-brain версий или снимок/слой "
            "изменён, а init_db ещё не пересеял)")
        cat_drugs = {r[0] for r in c.execute("SELECT drug_id FROM cpic_drug_catalog")}
        cat_genes = {r[0] for r in c.execute("SELECT gene FROM cpic_drug_catalog")}
        risk_drugs = {r[0] for r in c.execute("SELECT drug_id FROM cpic_drug_risk")}
        risk_genes = {r[0] for r in c.execute("SELECT gene FROM cpic_drug_risk")}
        impl_genes = {r[0] for r in c.execute("SELECT gene FROM cpic_gene_implication")}
        assert risk_drugs <= cat_drugs, f"drug_risk → препараты вне каталога: {risk_drugs - cat_drugs}"
        assert risk_genes <= cat_genes, f"drug_risk → гены вне каталога: {risk_genes - cat_genes}"
        assert impl_genes <= cat_genes, f"gene_implication → гены вне каталога: {impl_genes - cat_genes}"


check("канон CPIC засеян и консистентен (A2-full: 3 проекции, один seed_version)",
      check_cpic_canon_consistent)


# ── Страж этапа B: PHI-egress + labs per-tenant (нить diagnosis-hardcode) ──────
# Литерал-скан здесь НЕ годится: нейтрализованный код ЛЕГИТИМНО содержит онко-термины
# (egress-blocklist их ловит; CONDITION_LABS их гейтит). Поэтому страж — ПОВЕДЕНЧЕСКИЙ:
# проверяет, что сырое блокируется, а онко-контекст не навязан тенанту без класса.
import pubmed_client as _pm

def check_pubmed_egress_neutral():
    """FAIL: egress-guard пропускает сырую специфику диагноза в PubMed, или онко-паттерн
    детектится у тенанта без онко-класса (нить diagnosis-hardcode B1-B3)."""
    for raw in ("cisplatin chemotherapy", "pancreatic adenocarcinoma",
                "doxorubicin metabolic", "PSA surveillance 2019"):
        assert not _pm.egress_safe(raw), f"egress-guard пропустил сырое: {raw!r}"
    # Личные слова — пробы из словаря pii_census (до 2026-09-23 стояли здесь литералами
    # диагноза владельца): КАЖДОЕ слово фамилии/врачей/диагноза обязано блокироваться.
    import pii_census as _pii
    _probes = _pii.literals(["surname", "doctor", "clinical"])
    assert _probes, "словарь pii_census пуст/недоступен — egress личных слов не проверен"
    for t in _probes:
        assert not _pm.egress_safe(f"{t} outcomes"), "egress-guard пропустил личное слово словаря"
    q = _pm._compose_query("low_hrv", set())
    assert q and _pm.egress_safe(q) and "cancer" not in q.lower(), f"нейтральный запрос загрязнён: {q!r}"
    pats = _pm.detect_patterns_from_stats({"avg_hrv": 18, "avg_deep": 0.8}, {}, {"avg_hrv": 25},
                                          [{"test_name": "CEA", "value": 3.0}], set())
    assert "cipn" not in pats and "cea_trend" not in pats, f"онко-паттерн у тенанта без онко-класса: {pats}"


check("PubMed egress нейтрален (нить diagnosis-hardcode B: нет сырого диагноза в запросе)",
      check_pubmed_egress_neutral)


import labs_db as _labs

def check_labs_freshness_per_tenant():
    """FAIL: онкомаркеры в НЕЙТРАЛЬНОЙ базе лаб-свежести → навязаны всем тенантам, или
    онко-тенант потерял мониторинг маркеров (нить diagnosis-hardcode B6)."""
    neutral = _labs.effective_freshness(set())
    for marker in ("CEA", "CA19-9", "CA125"):
        assert marker not in neutral, f"онкомаркер {marker} в BASE → навязан тенанту без онко"
    onco = _labs.effective_freshness({"oncology"})
    assert "CEA" in onco and onco["CEA"]["priority"] == "critical", "онко-тенант потерял CEA-мониторинг"
    assert "HGB" in neutral, "общий тест HGB пропал из BASE"


check("labs freshness per-tenant (нить diagnosis-hardcode B6: онкомаркеры не в общей базе)",
      check_labs_freshness_per_tenant)


# ── [10] Структура документации ───────────────────────────────────────────────

if not JSON_OUTPUT:
    print("\n[10] Структура документации")

def check_doc_structure():
    """Проверяет что ключевые doc-файлы существуют после Diataxis-реструктуризации."""
    _root = Path(__file__).parent
    required = [
        "README.md", "CHANGELOG.md", "BACKLOG.md",
        "SECURITY.md", "SECURITY_AUDIT_LOG.md",
        "ARCH_SNAPSHOT.md", "TESTING_CONTRACTS.md", "CLAUDE.md",
    ]
    missing = [f for f in required if not (_root / f).exists()]
    assert not missing, f"отсутствуют файлы: {', '.join(missing)}"
    explanation_files = [
        "docs/explanation/data_flow.md",
        "docs/explanation/how_gp_works.md",
        "docs/explanation/genome_pipeline.md",
        "docs/explanation/hypothesis_engine.md",
        "docs/explanation/task_reminders_flow.md",
    ]
    missing_expl = [f for f in explanation_files if not (_root / f).exists()]
    assert not missing_expl, f"отсутствуют explanation-файлы: {', '.join(missing_expl)}"
    howto_files = [
        "docs/how-to/add_domain.md",
        "docs/how-to/update_docs.md",
        "docs/how-to/update_constitutions.md",
        "docs/how-to/run_analysis.md",
    ]
    missing_howto = [f for f in howto_files if not (_root / f).exists()]
    assert not missing_howto, f"отсутствуют how-to-файлы: {', '.join(missing_howto)}"
    changelog = (_root / "CHANGELOG.md").read_text()
    date_rows = [l for l in changelog.splitlines() if l.startswith("| 20")]
    assert date_rows, "CHANGELOG.md не содержит строк с датами (| 20...)"
    security = (_root / "SECURITY.md").read_text()
    assert "## ЧТО УЖЕ ХОРОШО" in security, \
        "SECURITY.md: отсутствует якорь '## ЧТО УЖЕ ХОРОШО' (сломает doc_agent)"


def check_oura_column_completeness():
    """W5K-#169 (2026-05-14): свежесть Oura-колонок в daily_metrics.

    Для каждой Oura-владеемой колонки сравнивает NULL rate за последние
    7 дней vs последние 30 дней. WARN если за 30д заполняется (NULL < 30%),
    но за свежие 7 дней молча пусто (NULL > 70%).

    Ловит: регрессии парсера, изменения endpoint API, destructive merge.
    Не ловит: колонки, которые «всегда пусто» (для этого нужен baseline 90+).
    """
    import sqlite3
    OURA_COLS = [
        "sleep_total", "sleep_deep", "sleep_rem", "sleep_score",
        "hrv", "resting_hr", "readiness",
        "spo2_avg",  # редкое — может ложно тригерить
        "stress_high_min", "recovery_high_min",
        "resilience_level",
        "steps", "active_kcal", "distance_km",
    ]
    regressions = []
    with db.get_conn() as conn:
        for col in OURA_COLS:
            try:
                r7 = conn.execute(
                    f"SELECT 100.0 * SUM(CASE WHEN {col} IS NULL THEN 1 ELSE 0 END) / COUNT(*) "
                    f"FROM daily_metrics WHERE date >= date('now', '-7 days')"
                ).fetchone()[0]
                r30 = conn.execute(
                    f"SELECT 100.0 * SUM(CASE WHEN {col} IS NULL THEN 1 ELSE 0 END) / COUNT(*) "
                    f"FROM daily_metrics WHERE date >= date('now', '-30 days')"
                ).fetchone()[0]
            except Exception as e:
                regressions.append(f"{col}: query error ({e})")
                continue
            if r7 is None or r30 is None:
                continue
            if r30 < 30 and r7 > 70:
                regressions.append(
                    f"{col}: 30d={r30:.0f}% NULL, 7d={r7:.0f}% NULL (регрессия)"
                )
    if regressions:
        raise AssertionError("Oura-колонки регрессировали: " + "; ".join(regressions))


check("Oura-колонки заполняются свежими данными (#169)", check_oura_column_completeness)


check("все doc-файлы на месте, CHANGELOG и SECURITY.md валидны", check_doc_structure, repo_only=True)


def check_translations_fresh():
    """Английский перевод отстал от русского оригинала (28.09.2026, решение владельца:
    документация в двух вариантах). doc_translation.stale_translations() считал это с
    первого дня, но никто не звал — детект без доставки. WARN, не FAIL: перевод догоняет
    позже, а находку разбирает ночной цикл (dev_fix «перевести заново»)."""
    import doc_translation
    stale = doc_translation.stale_translations()
    if stale:
        warn("Переводы документации устарели",
             f"{len(stale)}: " + ", ".join(stale[:10]) + (" …" if len(stale) > 10 else ""))
    return {"stale": len(stale)}


check("Переводы документации не отстают от оригинала", check_translations_fresh)



# ── Survivorship-extension watchdog (SX-08, фаза 8) ─────────────────────────
# Все шесть проверок пишут только WARN (через warn()), не FAIL.
# Цель: довести в утренний morning_test_summary список «что система ждёт от тебя».

import json as _json_sx
# Каталог опросников ЭТОГО тенанта — тот же дом, что у планировщика и диалога (28.09.2026).
# Литерал ~/health/data/instruments заставлял integrity партнёра судить каталог владельца.
from assessment_scheduler import INSTRUMENTS_DIR as SX_INSTRUMENTS_DIR  # noqa: E402


def _sx_instruments_iter():
    """Итератор по инструментам из каталога. Молча пропускает broken JSON."""
    if not SX_INSTRUMENTS_DIR.exists():
        return
    for f in sorted(SX_INSTRUMENTS_DIR.glob("*.json")):
        try:
            yield _json_sx.loads(f.read_text())
        except Exception as e:  # silent-ok: broken JSON в каталоге — пропуск инструмента
            warn(f"survivorship instrument parse failed: {f.name}", str(e)[:100])


def _assessment_task_state(conn, src_id: str, today) -> dict:
    """Состояние канала задач для инструмента: доставленная открытая задача с deadline
    в будущем — вопрос уже у человека (второй колокол здесь = banner blindness, §13);
    задача с sent_at IS NULL старше 2 суток — outbox бота не читается (liveness, §14)."""
    row = conn.execute(
        "SELECT sent_at, deadline, created_at FROM tasks WHERE type='assessment' "
        "AND status IN ('open','snoozed') AND fingerprint=? ORDER BY id DESC LIMIT 1",
        (f"assessment:{src_id}",)).fetchone()
    if not row:
        return {"carried": False, "stuck": False}
    sent, deadline, created = row["sent_at"], row["deadline"], row["created_at"]
    carried = bool(sent) and bool(deadline) and str(deadline) >= str(today)
    # created_at пишет DEFAULT datetime('now') — неразбираемая дата здесь дефект, не
    # случай: пусть краснеет громко, тихий обработчик считал бы outbox живым (§7, §20).
    stuck = bool(not sent and created
                 and (today - date.fromisoformat(str(created)[:10])).days > 2)
    return {"carried": carried, "stuck": stuck}


def check_assessments_freshness():
    """Каждый инструмент — последнее заполнение не старше cadence_days + 14.

    Просрочка молчит, пока открытая задача-опросник ДОСТАВЛЕНА боту (sent_at) и её
    deadline не прошёл: вопрос у человека одним каналом (2026-09-03; до этого задача
    не доставлялась вовсе — клавиатуру никто не слал, и датчик просил действие без
    кнопки). Недоставленная задача старше 2 суток — отдельное WARN: outbox мёртв."""
    today = get_today()
    overdue = []
    stuck = []
    for ins in _sx_instruments_iter():
        iid = ins.get("id", "?")
        # source в lab_results — по конвенции assessment_importer.instrument_source_id (полный
        # id модуля EORTC → короткий). Без этого модуль ВСЕГДА «ни одного заполнения»
        # (чек искал полный id, а импортёр пишет короткий). 2026-06-30.
        from assessment_importer import instrument_source_id
        src_id = instrument_source_id(iid)
        cadence = int(ins.get("cadence_days", 90))
        max_age = cadence + 14
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT MAX(date) as last FROM lab_results WHERE source = ?",
                (f"instrument:{src_id}",),
            ).fetchone()
            task = _assessment_task_state(conn, src_id, today)
        if task["stuck"]:
            stuck.append(iid)
        last = row["last"] if row else None
        if last is None:
            if not task["carried"]:
                overdue.append(f"{iid}: ни одного заполнения")
            continue
        try:
            days_ago = (today - date.fromisoformat(last)).days
        except Exception as e:  # noqa: BLE001 — A5 22.09: битые данные — находка, не пропуск
            warn("survivorship: дата последнего заполнения опросника нечитаема", f"{iid} {last!r}: {type(e).__name__}: {str(e)[:80]}")
            continue
        if days_ago > max_age and not task["carried"]:
            overdue.append(f"{iid}: {days_ago}д с {last} (порог {max_age}д)")
    if overdue:
        warn("survivorship: assessments просрочены", "; ".join(overdue))
    if stuck:
        warn("survivorship: задача-опросник не доставлена боту >2 суток",
             ", ".join(stuck) + " — outbox deliver_unsent_assessment_tasks не читается")
    # BL-SILENT-0ROWS-1: заполнено, а баллы не посчитались (импортёр вернул 0 строк) —
    # задача осталась открытой; без этой строки её «просрочку» молчала бы доставленная задача.
    with db.get_conn() as conn:
        failed = conn.execute("SELECT instrument_id, COUNT(*) n FROM assessment_sessions "
                              "WHERE status='import_failed' GROUP BY instrument_id").fetchall()
    if failed:
        warn("survivorship: опросник заполнен, баллы не посчитались (import_failed)",
             ", ".join(f"{r['instrument_id']}×{r['n']}" for r in failed)
             + " — ключи ответов ≠ items каталога; файлы в data/assessments/, перезапуск assessment_importer.py")


check("survivorship: assessments freshness", check_assessments_freshness)


def check_pro_score_unit_single(db_path=None):
    """PRO-балл под одним именем хранится в ОДНОЙ размерности (§16 клауза идентичности:
    имя + размерность). Независимо придуманный пример: анкета Demo-Q содержит
    17 баллов из 50 (unit='score'), а другой импорт записывает долю шкалы
    (score_0_100: 34.0). Смешение единиц создало бы ложный скачок тренда.
    Храним долю шкалы, чтобы число пунктов не меняло размерность.
    FAIL, не WARN: появление второй единицы означает регресс писателя PRO-строк."""
    import sqlite3
    sql = ("SELECT test_name, GROUP_CONCAT(DISTINCT COALESCE(unit,'')) FROM lab_results "
           "WHERE source LIKE 'instrument:%' GROUP BY test_name "
           "HAVING COUNT(DISTINCT COALESCE(unit,'')) > 1")
    if db_path:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            bad = con.execute(sql).fetchall()
        finally:
            con.close()
        assert not bad, "PRO-балл в двух размерностях: " + "; ".join(f"{n} [{u}]" for n, u in bad)
        return {"mixed": 0}
    red = []
    for _tag, _con, _cur in _iter_tenant_ro():
        bad = _con.execute(sql).fetchall()
        if bad:
            red.append(f"[{_tag}] " + "; ".join(f"{n} [{u}]" for n, u in bad))
    assert not red, "PRO-балл под одним именем в двух размерностях: " + " | ".join(red) + \
        " — единый писатель assessment_importer (score_0_100); чужую строку пересчитать, не смешивать"
    return {"mixed": 0}


check("survivorship: PRO-балл в одной размерности", check_pro_score_unit_single)


LITERATURE_SEARCH_LABEL = "com.larry.health.literature-search"
CONSILIUM_LABEL = "com.larry.health.consilium"


def check_literature_freshness():
    """Последний literature_search покрывает последний плановый запуск по живому плисту
    (до 23.09 — литерал «10 дней» при недельной задаче: второй дом ритма).

    След — МОМЕНТ записи (`created_at`, UTC — SQLite datetime('now')), а не `date`: у
    агентов `date` бывает датой периода (консилиум пишет 31.08 в запуске 01.09)."""
    today = get_today()
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT MAX(date) as last, MAX(created_at) as at FROM agent_reports "
            "WHERE agent_type='literature_search'"
        ).fetchone()
    last = row["last"] if row else None
    at = row["at"] if row else None
    if last is None:
        warn("литература: literature_search не запускался", "agent_reports пуст")
        return
    try:
        days_ago = (today - date.fromisoformat(last)).days
    except Exception as e:  # noqa: BLE001 — A5 22.09: битые данные — находка, не пропуск
        warn("литература: дата последнего literature_search нечитаема — свежесть не судима", f"{last!r}: {type(e).__name__}: {str(e)[:80]}")
        return
    covered, fire = _schedule_covers(LITERATURE_SEARCH_LABEL, _utc_trace(at))
    if covered is None:
        _schedule_unjudged("литература: literature_search", LITERATURE_SEARCH_LABEL)
    elif not covered:
        warn(f"литература: literature_search не запускался {days_ago}д",
             f"последний {last}; плановый запуск {fire:%d.%m %H:%M} не отметился")


check("литература: literature_search freshness", check_literature_freshness)


LITERATURE_SEARCH_PARTNER_LABEL = "com.larry.health.literature-search.partner"
PARTNER_TENANT_TAG = "health_partner"


def check_literature_freshness_partner():
    """Поиск литературы партнёра покрывает свой последний плановый запуск (решение владельца 27.09).

    Кран партнёру включён 27.09 решением владельца «включить»
    (BL-LIT-PARTNER-CRON-1): доставка прямая, ему в бот. check_literature_freshness однотенантный
    (db.get_conn текущего процесса), поэтому смерть партнёрской задачи он не видит — здесь её
    видно. Плиста нет — молчим: до 27.09 кран был выключен осознанно, и снять плист — такое же
    осознанное действие; состояние «одобрено, но не заведено» стережёт check_literature_partner_gate.
    Сиблингу — только возраст в днях (C-19). ГРАНИЦА: метка одна, тенант один (health_partner);
    третий тенант этим датчиком не судится.
    """
    import plist_env_liveness as _pl
    plist = _pl.agents_dir() / f"{LITERATURE_SEARCH_PARTNER_LABEL}.plist"
    if not plist.exists():
        return None
    for _tag, _conn, _cur in _iter_tenant_ro():
        if _tag != PARTNER_TENANT_TAG:
            continue
        row = _conn.execute("SELECT MAX(date) AS last, MAX(created_at) AS at FROM agent_reports "
                            "WHERE agent_type='literature_search'").fetchone()
        if not row or not row["last"]:
            warn(f"[{_tag}] literature_search ни разу не запускался", "кран заведён, отчёта нет")
            return None
        covered, fire = _schedule_covers(LITERATURE_SEARCH_PARTNER_LABEL, _utc_trace(row["at"]))
        if covered is None:
            _schedule_unjudged(f"[{_tag}] literature_search", LITERATURE_SEARCH_PARTNER_LABEL)
        elif not covered:
            days = (get_today() - date.fromisoformat(row["last"][:10])).days
            warn(f"[{_tag}] literature_search не запускался {days}д",
                 f"плановый запуск {fire:%d.%m %H:%M} не отметился")
        return covered
    warn(f"[{PARTNER_TENANT_TAG}] кран литературы заведён, а БД тенанта не найдена",
         f"плист {plist.name} есть — задача бежит в пустоту")
    return None


check("литература: literature_search партнёра", check_literature_freshness_partner)


def check_survivorship_agent_freshness():
    """Последний survivorship_analysis не старше 18 дней.

    Литерал оставлен сознательно (23.09, нить cadence-thresholds): плист задачи еженедельный,
    а анализ пишется раз в ДВЕ недели — чётность ISO-недели проверяет сам
    scripts/run_survivorship_pipeline.sh. Ритм живёт в скрипте, не в плисте, и сверка с
    плановым запуском кричала бы каждую нечётную неделю. 18 = две недели + 4 дня. Перепись —
    BACKLOG BL-THRESHOLDS-1."""
    today = get_today()
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT MAX(date) as last FROM agent_reports "
            "WHERE agent_type='survivorship_analysis'"
        ).fetchone()
    last = row["last"] if row else None
    if last is None:
        warn("survivorship: analyzer не запускался ни разу", "")
        return
    try:
        days_ago = (today - date.fromisoformat(last)).days
    except Exception as e:  # noqa: BLE001 — A5 22.09: битые данные — находка, не пропуск
        warn("survivorship: дата последнего analyzer нечитаема — свежесть не судима", f"{last!r}: {type(e).__name__}: {str(e)[:80]}")
        return
    if days_ago > 18:
        warn(f"survivorship: analyzer не запускался {days_ago}д", f"последний {last}")


check("survivorship: analyzer freshness", check_survivorship_agent_freshness)


def check_pending_proposals_ageing():
    """problem_list_proposals со status='pending' старше 21 дня."""
    today = get_today()
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, created_at, source FROM problem_list_proposals "
            "WHERE status='pending'"
        ).fetchall()
    overdue = []
    for r in rows:
        try:
            cd = (r["created_at"] or "")[:10]
            days_ago = (today - date.fromisoformat(cd)).days
            if days_ago > 21:
                overdue.append(f"#{r['id']} ({r['source']}, {days_ago}д)")
        except Exception as e:  # noqa: BLE001 — A5 22.09: битые данные — находка, не пропуск
            warn("survivorship: дата pending proposal нечитаема — пропущена", f"#{r['id']}: {type(e).__name__}: {str(e)[:80]}")
            continue
    if overdue:
        warn(f"survivorship: {len(overdue)} pending proposals старше 21д", "; ".join(overdue[:5]))


check("survivorship: pending proposals ageing", check_pending_proposals_ageing)


def check_ecg_nonsinus():
    """Записи ЭКГ с тревожным ритмом (AFib / High HR) за последние 48ч → кардио-алерт.
    Клинически важно при сердечно-сосудистом риске лечения.
    Окно 48ч → на суточном прогоне сработает ~1-2 раза на новую запись и затихнет."""
    try:
        import ecg_db
    except Exception as e:  # noqa: BLE001 — A5 22.09: кардио-сенсор не выключается молча
        warn("ЭКГ-сенсор не работает: ecg_db не импортировался", f"{type(e).__name__}: {str(e)[:100]} — AFib/High HR за 48ч НЕ проверены")
        return
    hits = ecg_db.find_nonsinus(48)
    if hits:
        parts = []
        for h in hits[:5]:
            when = (h.get("start_time") or "")[:16].replace("T", " ")
            hr = h.get("avg_hr")
            parts.append(f"{h['classification']} @ {when}" + (f" ({hr:.0f} уд/мин)" if hr is not None else ""))
        warn(f"ЭКГ: не-синусовый ритм за 48ч ({len(hits)})", "; ".join(parts))


check("ЭКГ: не-синусовый ритм", check_ecg_nonsinus)


def check_open_hypotheses_ageing():
    """Гипотезы со status='open' старше 60 дней без перехода в testing."""
    today = get_today()
    overdue = []
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, created_at, value FROM memory "
            "WHERE category='hypothesis' AND active=1"
        ).fetchall()
    for r in rows:
        try:
            payload = _json_sx.loads(r["value"] or "{}")
            if payload.get("status") not in ("open",):
                continue
            cd = (r["created_at"] or "")[:10]
            days_ago = (today - date.fromisoformat(cd)).days
            if days_ago > 60:
                overdue.append(f"#{r['id']} ({days_ago}д)")
        except Exception as e:  # noqa: BLE001 — A5 22.09: битые данные — находка, не пропуск
            warn("survivorship: строка гипотезы нечитаема — пропущена", f"#{r['id']}: {type(e).__name__}: {str(e)[:80]}")
            continue
    if overdue:
        warn(f"survivorship: {len(overdue)} open гипотез старше 60д",
             "; ".join(overdue[:5]))


check("survivorship: open hypotheses ageing", check_open_hypotheses_ageing)


def check_unresolved_evaluations():
    """HV-6: гипотезы в testing, лабные данные есть, outcome отсутствует > 7 дней."""
    import json as _j
    try:
        pending = db.get_hypotheses_awaiting_evaluation()
    except Exception as e:  # noqa: BLE001 — A5 22.09: сенсор не выключается молча
        warn("HV-6: не смог прочитать гипотезы, ждущие оценки — сенсор не выполнен", f"{type(e).__name__}: {str(e)[:100]}")
        return 0
    overdue = []
    today = get_today()
    for h in pending:
        created = h.get("created_date", "")
        if not created:
            continue
        try:
            from datetime import date as _date
            delta = (today - _date.fromisoformat(created)).days
            if delta > 7:
                overdue.append(h)
        except Exception as e:  # noqa: BLE001 — A5 22.09: битая дата — находка, не пропуск
            warn("гипотеза в testing: дата создания нечитаема — пропущена", f"#{h.get('memory_id')}: {created!r}")
    if overdue:
        ids = ", ".join(f"#{h['memory_id']}" for h in overdue[:5])
        warn(
            f"{len(overdue)} гипотез в testing без оценки консилиума (>{7}д): {ids}",
            "запустить /eval_hypothesis <id> или дождаться вт 10:00"
        )
    return len(overdue)


check("hypothesis: оценка консилиумом", check_unresolved_evaluations)


def check_recommendation_engine_health():
    """evaluate_domain_need не должен молча падать в error-path.

    Обзор 2026-07-02: движок глотал любое исключение → needed=False тихо.
    Теперь error-path возвращает наблюдаемый ключ 'error'. Здесь прогоняем
    движок по всем доменам и поднимаем WARN, если хоть один сломался —
    доставка через triage (07:50→08:00). Без датчика немой отказ вернулся бы.
    """
    from services.recommendations import evaluate_domain_need
    DOMAINS = ["sleep", "nutrition", "stress", "recovery", "activity"]
    broken = []
    for d in DOMAINS:
        try:
            res = evaluate_domain_need(d)
        except Exception as e:  # сам вызов не должен кидать — но подстрахуемся
            broken.append((d, f"raised {type(e).__name__}: {e}"))
            continue
        if res.get("error"):
            broken.append((d, res["error"]))
    if broken:
        details = "; ".join(f"{d}: {msg}" for d, msg in broken[:5])
        warn(
            f"recommendation engine: {len(broken)} доменов в error-path — {details}",
            "движок рекомендаций молча замолк; смотри logs (log.error evaluate_domain_need)"
        )
    return len(broken)


check("recommendation engine здоров", check_recommendation_engine_health)


def check_constitution_conflicts_unresolved():
    """memory(category='constitution_conflict') со status='open' старше 45 дней."""
    today = get_today()
    overdue = []
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, created_at, value FROM memory "
            "WHERE category='constitution_conflict' AND active=1"
        ).fetchall()
    for r in rows:
        try:
            payload = _json_sx.loads(r["value"] or "{}")
            if payload.get("status") not in ("open", None):
                continue
            cd = (r["created_at"] or "")[:10]
            days_ago = (today - date.fromisoformat(cd)).days
            if days_ago > 45:
                overdue.append(f"#{r['id']} ({days_ago}д)")
        except Exception as e:  # noqa: BLE001 — A5 22.09: битые данные — находка, не пропуск
            warn("survivorship: строка constitution_conflict нечитаема — пропущена", f"#{r['id']}: {type(e).__name__}: {str(e)[:80]}")
            continue
    if overdue:
        warn(f"survivorship: {len(overdue)} constitution_conflicts старше 45д",
             "; ".join(overdue[:5]))


check("survivorship: constitution conflicts unresolved", check_constitution_conflicts_unresolved)


def check_pharmaco_genome_sync():
    """Фармакогеномика синхронизирована с последним импортом генома.

    Проверяет три условия:
    1. Таблицы genome_imports и pharmaco_phenotypes существуют (созданы миграцией).
    2. Если genome_imports не пуста — pharmaco_phenotypes тоже должна быть непустой.
       Иначе Phase E ещё не запускалась после импорта VCF.
    3. genome_import_id в pharmaco_phenotypes совпадает с max(genome_imports.id).
       Расхождение = новый VCF импортирован, но pharmaco не пересчитана (staleness).

    Метафора (WFR, Таненбаум §7.3.5): pharmaco должна «следовать» за чтением raw_snps.
    Если она отстала — консилиум получит устаревший фармакоконтекст.
    """
    # brief-neutralization step0/Tier-A: per-tenant. Сообщения структурные (счётчики/
    # import-id, без PHI). Сиблинг без pharmaco-таблиц → пропуск (не наша ось); реальный
    # рассинхрон → fail у своего тенанта / warn у сиблинга.
    fails: list[str] = []
    out: dict = {}
    for _tag, _conn, _cur in _iter_tenant_ro():
        tables = {r[0] for r in _conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
            "('genome_imports','pharmaco_phenotypes')")}
        if {"genome_imports", "pharmaco_phenotypes"} - tables:
            if _cur:
                fails.append(f"[{_tag}] pharmaco-таблицы отсутствуют (миграция не прошла?)")
            continue  # сиблинг без генома-пайплайна — не наша ось
        n_imports = _conn.execute("SELECT COUNT(*) FROM genome_imports").fetchone()[0]
        n_pharmaco = _conn.execute("SELECT COUNT(*) FROM pharmaco_phenotypes").fetchone()[0]
        if n_imports > 0 and n_pharmaco == 0:
            _m = f"[{_tag}] genome_imports не пуста, pharmaco_phenotypes пуста — --phase E"
            if _cur:
                fails.append(_m)
            else:
                warn(_m, "")
            continue
        if n_imports == 0:
            out[_tag] = {"status": "no_imports"}
            continue
        latest = _conn.execute("SELECT MAX(id) FROM genome_imports").fetchone()[0]
        pharmaco_ids = {r[0] for r in _conn.execute(
            "SELECT DISTINCT genome_import_id FROM pharmaco_phenotypes "
            "WHERE genome_import_id IS NOT NULL")}
        if pharmaco_ids and latest not in pharmaco_ids:
            warn(f"[{_tag}] pharmaco устарела",
                 f"genome_imports.max_id={latest}, pharmaco к {sorted(pharmaco_ids)} — --phase E")
        out[_tag] = {"n_imports": n_imports, "n_pharmaco": n_pharmaco, "latest": latest}
    assert not fails, "pharmaco рассинхрон: " + " ; ".join(fails)
    return out or None


check("фармакогеномика синхронизирована с геномом (WFR)", check_pharmaco_genome_sync)


def check_traits_genome_sync():
    """Детерминированные черты (Phase F) синхронизированы с последним импортом генома."""
    with db.get_conn() as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
            "('genome_imports','trait_phenotypes')"
        )}
        if 'trait_phenotypes' not in tables:
            warn('trait_phenotypes отсутствует',
                 'запусти vcf_import_pipeline.py --phase F')
            return {'status': 'table_missing'}
        n_imports = conn.execute('SELECT COUNT(*) FROM genome_imports').fetchone()[0]
        n_traits  = conn.execute('SELECT COUNT(*) FROM trait_phenotypes').fetchone()[0]
        if n_imports > 0 and n_traits == 0:
            warn('trait_phenotypes пуста',
                 'запусти vcf_import_pipeline.py --phase F')
            return {'status': 'not_computed'}
        if n_imports == 0:
            return {'status': 'no_imports_yet'}
        latest = conn.execute('SELECT MAX(id) FROM genome_imports').fetchone()[0]
        trait_ids = {r[0] for r in conn.execute(
            'SELECT DISTINCT genome_import_id FROM trait_phenotypes WHERE genome_import_id IS NOT NULL'
        )}
        if trait_ids and latest not in trait_ids:
            warn('traits устарели',
                 f'genome_imports.max_id={latest}, traits привязаны к {trait_ids} — '
                 'запусти vcf_import_pipeline.py --phase F')
        return {'n_imports': n_imports, 'n_trait_rows': n_traits, 'latest': latest}


def check_wellness_genome_sync():
    """Wellness-геномика (Phase G) синхронизирована с последним импортом генома."""
    with db.get_conn() as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
            "('genome_imports','wellness_phenotypes')"
        )}
        if 'wellness_phenotypes' not in tables:
            warn('wellness_phenotypes отсутствует',
                 'запусти vcf_import_pipeline.py --phase G')
            return {'status': 'table_missing'}
        n_imports  = conn.execute('SELECT COUNT(*) FROM genome_imports').fetchone()[0]
        n_wellness = conn.execute('SELECT COUNT(*) FROM wellness_phenotypes').fetchone()[0]
        if n_imports > 0 and n_wellness == 0:
            warn('wellness_phenotypes пуста',
                 'запусти vcf_import_pipeline.py --phase G')
            return {'status': 'not_computed'}
        if n_imports == 0:
            return {'status': 'no_imports_yet'}
        latest = conn.execute('SELECT MAX(id) FROM genome_imports').fetchone()[0]
        wellness_ids = {r[0] for r in conn.execute(
            'SELECT DISTINCT genome_import_id FROM wellness_phenotypes WHERE genome_import_id IS NOT NULL'
        )}
        if wellness_ids and latest not in wellness_ids:
            warn('wellness устарела',
                 f'genome_imports.max_id={latest}, wellness привязана к {wellness_ids} — '
                 'запусти vcf_import_pipeline.py --phase G')
        return {'n_imports': n_imports, 'n_wellness_rows': n_wellness, 'latest': latest}


check('черты генома синхронизированы с геномом (WFR, Phase F)', check_traits_genome_sync)
check('wellness геномика синхронизирована с геномом (WFR, Phase G)', check_wellness_genome_sync)


def check_prs_genome_sync():
    """PRS (Wave 4, Phase H) синхронизированы с последним импортом генома."""
    with db.get_conn() as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
            "('genome_imports','prs_scores','pgs_catalog')"
        )}
        # pgs_catalog/prs_scores absence is expected before genome_weights.py is run
        if 'pgs_catalog' not in tables:
            return {'status': 'weights_not_downloaded'}
        if 'prs_scores' not in tables:
            warn('prs_scores отсутствует',
                 'запусти vcf_import_pipeline.py --phase H')
            return {'status': 'table_missing'}
        n_imports = conn.execute('SELECT COUNT(*) FROM genome_imports').fetchone()[0]
        n_catalog = conn.execute('SELECT COUNT(*) FROM pgs_catalog').fetchone()[0]
        if n_catalog == 0:
            return {'status': 'no_weights_registered'}
        n_scores = conn.execute('SELECT COUNT(*) FROM prs_scores').fetchone()[0]
        if n_imports > 0 and n_scores == 0:
            warn('prs_scores пуста',
                 'запусти vcf_import_pipeline.py --phase H')
            return {'status': 'not_computed'}
        if n_imports == 0:
            return {'status': 'no_imports_yet'}
        latest = conn.execute('SELECT MAX(id) FROM genome_imports').fetchone()[0]
        prs_import_ids = {r[0] for r in conn.execute(
            'SELECT DISTINCT genome_import_id FROM prs_scores WHERE genome_import_id IS NOT NULL'
        )}
        if prs_import_ids and latest not in prs_import_ids:
            warn('PRS устарели',
                 f'genome_imports.max_id={latest}, prs_scores привязаны к {prs_import_ids} — '
                 'запусти vcf_import_pipeline.py --phase H')
        return {'n_imports': n_imports, 'n_prs_rows': n_scores, 'latest': latest}


check('PRS синхронизированы с геномом (WFR, Phase H)', check_prs_genome_sync)


# ─────────────────────────────────────────────────────────────────────────────
if not JSON_OUTPUT:
    print("\n[11] Бэкап и размер БД (backup policy SLA)")

BACKUP_DIR         = Path(str(db.DB_PATH)).parent.parent / "backups"
BACKUP_LABEL       = "com.larry.health.backup"   # SLA — «каждый плановый запуск» (23.09)
DB_SIZE_WARN_MB    = 300  # WARN порог
DB_SIZE_FAIL_MB    = 500  # FAIL порог


def scan_backup_staleness(db_paths, now=None):
    """Чистая/тестируемая: свежесть ежедневного бэкапа по тенантам.
    Бэкап тенанта = <root>/backups/<tenant>_*.db (backup_studio.sh). Возврат
    list[(tenant, kind, age_h|None)], kind in {missing, stale, unjudged}. Пусто = все свежи.
    В отличие от календаря — бэкап ОБЯЗАТЕЛЕН каждому реальному тенанту.

    Свежесть (решение владельца 23.09 «общее правило»): свежайший бэкап покрывает ПОСЛЕДНИЙ
    плановый запуск com.larry.health.backup по живому плисту. До 23.09 — литерал 25 ч."""
    from datetime import datetime as _dtm
    out = []
    for dbp in db_paths:
        root = Path(dbp).parent.parent
        tenant = root.name
        bkdir = root / "backups"
        files = [f for f in bkdir.glob(f"{tenant}_*.db")
                 if not f.name.endswith(("-shm", "-wal"))] if bkdir.exists() else []
        if not files:
            out.append((tenant, "missing", None))
            continue
        mtime = max(f.stat().st_mtime for f in files)
        age_h = round((time.time() - mtime) / 3600, 1)
        covered, _fire = _schedule_covers(
            BACKUP_LABEL, _dtm.fromtimestamp(mtime).isoformat(timespec="seconds"), now)
        if covered is None:
            out.append((tenant, "unjudged", age_h))
        elif not covered:
            out.append((tenant, "stale", age_h))
    return out


def check_backup_freshness():
    """Свежайший ежедневный бэкап КАЖДОГО тенанта покрывает последний плановый запуск бэкапа.
    FAIL — тенант без свежего бэкапа = необратимый риск потери стора.

    Тенант-осознанный (2026-07-10): backup_studio.sh бэкапил только owner, у партнёра
    ежедневных бэкапов не было, а датчик (один BACKUP_DIR) этого не видел. Теперь оба
    петляют тенантов (scan_backup_staleness / _tenant_db_paths)."""
    dbs = _tenant_db_paths(include_current=True)
    bad = scan_backup_staleness(dbs)
    if bad:
        parts = [(f"{t}: бэкапов нет" if k == "missing"
                  else f"{t}: расписание {BACKUP_LABEL} не выводится из плиста — не судимо"
                  if k == "unjudged"
                  else f"{t}: свежайший {a:.1f}ч назад — последний плановый запуск не отметился")
                 for t, k, a in bad]
        raise AssertionError(
            "Бэкап-SLA нарушен — " + "; ".join(parts) + ". backup_studio.sh не отработал?")
    return {"tenants_checked": len(dbs)}


def check_db_size():
    size_mb = Path(str(db.DB_PATH)).stat().st_size / 1024 / 1024
    if size_mb > DB_SIZE_FAIL_MB:
        raise AssertionError(
            f"DB size {size_mb:.0f}MB > FAIL лимит {DB_SIZE_FAIL_MB}MB — "
            f"незафиксированный WAL? Запусти: PRAGMA wal_checkpoint(TRUNCATE)"
        )
    if size_mb > DB_SIZE_WARN_MB:
        warn(
            "DB size приближается к лимиту",
            f"{size_mb:.0f}MB > WARN {DB_SIZE_WARN_MB}MB (FAIL = {DB_SIZE_FAIL_MB}MB)",
        )


check("Свежесть бэкапа (каждый плановый запуск)", check_backup_freshness)
check("Размер DB < 500MB", check_db_size)


LOG_UNLISTED_WARN_MB = 1.0   # замер 01.09: вне конфига 160 файлов, НИ ОДНОГО >1 МБ —
                             # порог даёт сигнал, а не фон (§13: кричащий всегда датчик
                             # выучивает себя игнорировать). FAIL-порог не задаётся здесь:
                             # им служит порог ротации из самого конфига (over_threshold).


def check_logs_all_listed():
    """Слепое пятно ротации: лог РАСТЁТ, а в конфиге ротации его нет.

    Ротация подрезает только перечисленное и про остальное молчит — именно так
    470 МБ копились с мая, и `consilium_eval.log`, ради которого ротация заводилась,
    в списке не значился вовсе. Без этого датчика новый крупный лог находится глазами.

    FAIL, а не WARN, когда файл уже перерос порог ротации: такой лог не подрежет никто,
    и падения integrity читает night_cycle — то есть FAIL доходит до владельца, WARN нет.
    Лечение всегда одно: строка в launchd/health-logs.newsyslog.conf.

    ГРАНИЦА: датчик смотрит каталоги, ВЫЧИСЛЕННЫЕ из конфига (родители его путей), и
    только `*.log`. Лог в новом каталоге или без расширения .log остаётся невидимым —
    цена отказа заводить второй список каталогов. Оракул поведения (в т.ч. негативный
    контроль «названный лог молчит») — tests/unit/test_log_rotate.py.
    """
    import log_rotate
    found = log_rotate.unlisted_logs(min_mb=LOG_UNLISTED_WARN_MB)
    over = [r for r in found if r["over_threshold"]]
    if over:
        raise AssertionError(
            "логи вне конфига ротации переросли её порог, их не подрежет никто: "
            + "; ".join(f"{r['path']} ({r['mb']} МБ)" for r in over[:5])
            + ". Допиши строку в launchd/health-logs.newsyslog.conf")
    if found:
        warn("логи вне конфига ротации растут",
             "; ".join(f"{r['path']} ({r['mb']} МБ)" for r in found[:5]))
    return {"unlisted_logs": len(found)}


check("Логи вне конфига ротации", check_logs_all_listed)


def check_fault_journal():
    """Решение владельца 2026-09-28: сбои идут в ночной цикл, не в Telegram.

    По одной находке на (тенант, место) за 24 часа; артефакт integrity читает
    night_cycle. Повреждённая строка не скрывает остальные записи журнала.

    Журналов может быть несколько (нить partner-faults, 02.10, решение владельца «вариант а»):
    у владельца в контейнере свой журнал в томе, а процессы на хосте (бот и проверка партнёра,
    деплой) пишут в журнал хоста — он смонтирован только для чтения и назван в
    HEALTH_FAULTS_EXTRA (через «:»). Без него сбои партнёра с 30.09 не видел никто (урок C-135).
    Названный, но не смонтированный журнал — находка, а не тишина: иначе слепота вернулась бы молча.
    """
    import fcntl
    import os
    from datetime import datetime, timedelta

    primary = Path(os.environ.get("HEALTH_FAULTS_JOURNAL") or
                   Path(__file__).resolve().parent / "logs" / "faults.jsonl")
    extra = [Path(p) for p in (os.environ.get("HEALTH_FAULTS_EXTRA") or "").split(":") if p]
    now = get_now().astimezone()
    groups = {}
    invalid = 0
    for path in [primary, *extra]:
        if path in extra and not path.parent.is_dir():
            warn("Журнал сбоев не читается", f"{path}: каталог не смонтирован — сбои хоста не видны")
            continue
        if not path.exists():   # журнала нет — сбоев не записано (свежая установка): это ответ, не отказ
            continue
        try:
            with path.open(encoding="utf-8") as journal:
                fcntl.flock(journal, fcntl.LOCK_SH)
                for line in journal:
                    try:
                        record = json.loads(line)
                        if not isinstance(record, dict) or not all(
                                isinstance(record.get(k), str) for k in ("ts", "tenant", "where", "text")):
                            raise ValueError("неверные поля записи")
                        ts = datetime.fromisoformat(record["ts"]).astimezone()
                        if not now - timedelta(hours=24) <= ts <= now:
                            continue
                        key = (record["tenant"], record["where"])
                        n, latest, text = groups.get(key, (0, ts, record["text"]))
                        groups[key] = (n + 1, max(ts, latest), record["text"] if ts >= latest else text)
                    except (ValueError, TypeError, OverflowError) as exc:
                        invalid += 1   # итог — warn после цикла; строка — в лог прогона сразу
                        print(f"  журнал сбоев: битая строка пропущена ({type(exc).__name__})")
        except (OSError, UnicodeError) as exc:
            warn("Журнал сбоев не читается", f"{path}: {exc}")
    if invalid:
        warn("Журнал сбоев не читается", f"{primary}: повреждённых строк: {invalid}")
    for (tenant, where), (n, _, text) in groups.items():
        warn(f"сбой: {where}", f"{n} раз за сутки у {tenant}; последний: {text[:200]}")
    return {"groups": len(groups), "faults": sum(g[0] for g in groups.values())}


check("Сбои бота и служб за сутки", check_fault_journal)


LOGROTATE_LABEL = "com.larry.health.logrotate"
INTEGRITY_LABEL = "com.larry.health.integrity-check"


def check_logrotate_liveness():
    """Ротатор жив И отработал чисто (§14, находка producer_registry 01.09).

    Почему это НЕ дублирует «Логи вне конфига»: тот датчик слеп ровно к смерти агента —
    логи растут, но каждый из них В конфиге, значит находок ноль и всё «чисто». Умерший
    ротатор молчит идеально; именно так 470 МБ и накопились.

    Проверяются две разные вещи, и это сознательно (§14: heartbeat доказывает «запустился»,
    не «работает корректно»): свежесть квитанции — что прогон был; хвост `selftest ok` —
    что в прогоне отработала самопроверка copytruncate. Второе краснеет, когда механизм
    жив, но сломан, — случай, который один heartbeat пропустил бы.

    ГРАНИЦА: квитанция читается ЛОКАЛЬНАЯ. integrity бежит на Studio, значит агент на
    MacBook этим датчиком не покрыт — там свой прогон integrity и своя квитанция.
    """
    import log_rotate
    r = log_rotate.receipt_status()
    if not r["exists"]:
        raise AssertionError(
            "квитанции ротации нет вовсе (logs/logrotate.log) — агент "
            "com.larry.health.logrotate не запускался ни разу. "
            "Установка: docs/how-to/rotate_logs.md")
    # Пороги — из живых плистов (решение владельца 23.09 «общее правило»; до этого 2 ч / 24 ч):
    #   WARN — пропущен хотя бы один запуск: квитанция старше StartInterval агента;
    #   FAIL — агент молчал весь промежуток между двумя утренними проверками integrity
    #          (сутки при нынешнем плисте): до этой проверки не отметился ни разу.
    import plist_env_liveness as _pl
    from datetime import timedelta as _td
    every_s = _pl.start_interval_s(LOGROTATE_LABEL)
    last_check = _pl.last_scheduled_fire(INTEGRITY_LABEL, get_now())
    prev_check = (_pl.last_scheduled_fire(INTEGRITY_LABEL, last_check - _td(seconds=1))
                  if last_check else None)
    if every_s is None or prev_check is None:
        _schedule_unjudged("ротация логов", LOGROTATE_LABEL if every_s is None else INTEGRITY_LABEL)
    age_s = r["age_h"] * 3600
    if prev_check is not None and age_s > (last_check - prev_check).total_seconds():
        raise AssertionError(
            f"ротация молчит {r['age_h']:.1f}ч — весь промежуток между двумя проверками "
            f"integrity; агент не бежит. "
            "Логи растут, и датчик слепого пятна их не увидит: они В конфиге. "
            "launchctl print gui/$(id -u)/com.larry.health.logrotate")
    if not r["selftest_ok"]:
        raise AssertionError(
            "последний прогон ротации не дошёл до `selftest ok` — самопроверка "
            "copytruncate упала или процесс убит. Смотри logs/logrotate_err.log")
    if every_s is not None and age_s > every_s:
        warn("квитанция ротации несвежая",
             f"{r['age_h']:.1f}ч при интервале {every_s // 60} мин — пропущен запуск")
    return {"logrotate_age_h": r["age_h"]}


check("Ротация логов жива и чиста", check_logrotate_liveness)


def check_secret_scope_matches_reality():
    """Объявленный класс секрета против того, что реально лежит в каталогах (02.09).

    Гибрид (решение владельца 02.09): объявление в secrets_paths.SECRET_SCOPE — источник
    истины, наличие файла — ВТОРОЙ независимый источник. Смысл датчика ровно в их
    расхождении, которого не даёт ни чистое объявление, ни чистый факт:

      · объявлено `owner`, а файл появился у ПАРТНЁРА → либо ошибка раскатки (партнёру
        положили то, что общее), либо решение сделать секрет тенантским, которое забыли
        записать. Оба случая надо увидеть, а не узнать по последствиям;
      · объявлено `tenant`, а у партнёра файла НЕТ → тенант работает на дефолтах или
        молча падает fail-closed;
      · файл есть в каталоге, но класса нет вовсе → реестр отстал от реальности. Ровно
        так отстал прежний предикат из трёх имён: календарь партнёра жил вне охраны.

    ГРАНИЦА: судим только по каталогам, которые СУЩЕСТВУЮТ на этой машине. Нет
    партнёрского каталога — про tenant-секреты молчим, а не выдумываем вердикт.
    """
    from secrets_paths import SECRET_SCOPE
    owner_dir = Path.home() / ".health_secrets"
    partner_dir = Path.home() / ".health_secrets_partner"
    if not owner_dir.is_dir():
        return {"skipped": "каталога секретов владельца нет — судить не на чем"}

    def _names(d):
        return {f.name for f in d.iterdir()} if d.is_dir() else None

    own, part = _names(owner_dir), _names(partner_dir)
    problems = []
    for name in sorted((own or set()) | (part or set())):
        scope = SECRET_SCOPE.get(name)
        if scope is None:
            problems.append(f"{name}: лежит в каталоге, класс не объявлен")
        elif scope == "owner" and part is not None and name in part:
            problems.append(f"{name}: объявлен owner, но есть у партнёра")
    if problems:
        raise AssertionError(
            "реестр классов секретов разошёлся с каталогами: " + "; ".join(problems[:6])
            + ". Объяви класс в secrets_paths.SECRET_SCOPE либо разберись, почему файл там.")

    missing = []
    if part is not None:
        missing = [n for n, sc in SECRET_SCOPE.items() if sc == "tenant" and n not in part]
    if missing:
        warn("tenant-секреты отсутствуют у партнёра",
             ", ".join(sorted(missing)[:6]) + " — тенант работает на дефолтах или fail-closed")
    return {"secrets_checked": len(own or ()), "partner_dir": part is not None}


check("Реестр классов секретов против каталогов", check_secret_scope_matches_reality)


# ─────────────────────────────────────────────────────────────────────────────
if not JSON_OUTPUT:
    print("\n[12] Безопасность: SEC-чеклист как датчики (burn-in WARN с 2026-07-06)")


_LLM_TRACTS_UNGUARDED_BASELINE = 20   # замер 2026-08-03 по AST: 21 тракт, гард у 1


def check_llm_tracts_guarded():
    """Число LLM-трактов БЕЗ secret_guard не растёт (§19, решение владельца 2026-08-03).

    Что стережёт: НЕ утечку — от неё защищает сам гард, и он стоит у одного тракта
    (`doc_agent.analyze_diff`). Стережёт ГРАНИЦУ: тридцать первый модуль, который
    начнёт слать текст в Anthropic API мимо гарда, появится с красным датчиком,
    а не молча. Ровно тот класс, что уже реализовался: `service_trouble` завёл
    новый вызов судьи 2026-08-02, и никто этого не посчитал.

    Периметр ВЫЧИСЛЯЕТСЯ (§18): трактом считается модуль, ИМПОРТИРУЮЩИЙ anthropic,
    покрытым — импортирующий secret_guard. Замер, а не список имён: список разошёлся
    бы с деревом так же, как arch_guard.TRACKED. Грубый греп по слову давал 30 и 2 —
    девять файлов упоминают API в прозе, не вызывая его, а этот файл объявлял покрытым
    сам себя. По импортам: 21 и 1.

    Baseline снижается вручную вместе с расширением гарда — вниз ратчет не ходит сам,
    иначе покрытие можно было бы «улучшить», удалив модуль.
    """
    import ast as _ast
    root = Path(__file__).parent

    def _imports(tree):
        """Имена импортированных МОДУЛЕЙ. Не подстрока: первый вариант детектора
        считал трактом любой файл со словом «anthropic» в строке — и этот файл
        объявил покрытым сам себя, потому что упоминает secret_guard в тексте
        проверки. Слово в прозе ≠ вызов (ср. §20: зелёный причинён окружением)."""
        out = set()
        for n in _ast.walk(tree):
            if isinstance(n, _ast.Import):
                out |= {a.name.split(".")[0] for a in n.names}
            elif isinstance(n, _ast.ImportFrom) and n.module:
                out.add(n.module.split(".")[0])
        return out

    tracts, guarded, unreadable = [], [], []
    for p in sorted(root.glob("*.py")):
        if p.name.startswith("test_") or p.name in ("secret_guard.py", Path(__file__).name):
            continue
        try:
            mods = _imports(_ast.parse(p.read_text(encoding="utf-8", errors="ignore")))
        except (OSError, SyntaxError) as e:
            # Сигнал прямо здесь, а не агрегатом после цикла: неразобранный файл
            # выпадает из периметра, то есть тракт внутри него становится невидимым —
            # ровно тот отказ, против которого стоит датчик. Молчать тут нельзя.
            warn("LLM-периметр: файл не разобран",
                 f"{p.name}: {type(e).__name__} — тракт внутри датчик НЕ увидит")
            unreadable.append(p.name)
            continue
        if "anthropic" not in mods:
            continue
        tracts.append(p.stem)
        if "secret_guard" in mods:
            guarded.append(p.stem)
    unguarded = sorted(set(tracts) - set(guarded))
    if len(unguarded) > _LLM_TRACTS_UNGUARDED_BASELINE:
        fresh = [m for m in unguarded if m not in _LLM_TRACTS_KNOWN]
        raise AssertionError(
            f"LLM-трактов без secret_guard стало {len(unguarded)} "
            f"(baseline {_LLM_TRACTS_UNGUARDED_BASELINE}). Новые: {fresh or unguarded}. "
            "Либо проведи вызов через secret_guard, либо прими осознанно и подними "
            "baseline с записью в SECURITY.md (как SEC-21 для hai_*/lab_recognizer).")
    return {"tracts": len(tracts), "guarded": len(guarded), "unguarded": len(unguarded)}


# Замер 2026-08-03 — против него считаются «новые» в сообщении об отказе.
_LLM_TRACTS_KNOWN = {
    "cbcr_hypothesis", "checkin_agent", "consult_prep",
    "food_rule_generator", "generate_constitutions", "genome_context",
    "genome_update_agent", "gp_agent", "hai_core", "hypothesis_consilium_eval",
    "import_medical_events", "lab_extractor", "lab_schedule_extractor",
    "model_health_check", "monthly_consilium", "night_investigator", "task_agent",
    "treatment_extractor", "wellally_consult",
}

check("LLM-тракты без secret_guard не множатся (§19)", check_llm_tracts_guarded)


_DIRECT_CTOR_BASELINE = 0       # 29.09: миграция дописана (было 27 на 2026-08-03, 23 на 29.09)


def check_llm_direct_constructors(root=None):
    """Клиент Anthropic конструируется ТОЛЬКО в llm_client — там гард секретов стоит по конструкции.

    Ратчет 2026-08-03 стоял на 27 и запрещал только рост; миграция застряла на 23, и среди
    оставшихся был cbcr_hypothesis, где с 02.09 читался несуществующий ANTHROPIC_KEY_PATH:
    CBCR молча не рождал гипотез (literature-read.log 07.09, 22.09). Замер X8 (29.09): README
    обещает гард перед КАЖДЫМ вызовом модели — обещание держится только при нуле.
    Первая редакция считала только корень репозитория (glob «*.py») — handlers/, jobs/, bot/
    были вне счёта. Теперь всё дерево, кроме тестов.

    Считается вызов конструктора по AST, а не слово в тексте: первая редакция соседнего
    датчика искала подстроку и объявила покрытым сам integrity_tests.
    """
    import ast as _ast
    root = Path(root) if root else Path(__file__).parent
    hits = []
    for p in sorted(root.rglob("*.py")):
        rel = p.relative_to(root).as_posix()
        if p.name == "llm_client.py" or p.resolve() == Path(__file__).resolve() \
                or rel.startswith(("tests/", ".venv/", ".git/")) or "/site-packages/" in rel:
            continue
        try:
            tree = _ast.parse(p.read_text(encoding="utf-8", errors="ignore"))
        except (OSError, SyntaxError) as e:
            warn("LLM-конструкторы: файл не разобран",
                 f"{rel}: {type(e).__name__} — вызовы внутри не видны")
            continue
        for n in _ast.walk(tree):
            if isinstance(n, _ast.Call):
                f = n.func
                name = getattr(f, "attr", None) or getattr(f, "id", None)
                if name in ("Anthropic", "AsyncAnthropic"):
                    hits.append(f"{rel}:{n.lineno}")
    if len(hits) > _DIRECT_CTOR_BASELINE:
        raise AssertionError(
            f"конструкторов Anthropic мимо llm_client: {len(hits)} (разрешено {_DIRECT_CTOR_BASELINE}). "
            f"Вызов обязан идти через llm_client.guarded_client() — там гард секретов стоит по "
            f"конструкции. Список: {hits[:8]}")
    return {"direct": len(hits), "baseline": _DIRECT_CTOR_BASELINE}


check("Конструкторы Anthropic мимо llm_client не множатся", check_llm_direct_constructors)


def check_llm_guard_blocks_reported():
    """Блокировки гарда не молчат: счётчик за сутки виден человеку.

    Дыра в наблюдаемости, названная при планировании: блок уходит в лог
    вызывающего, и ни ложное срабатывание, ни настоящая находка не видны
    в отчёте. Датчик читает журнал блокировок и сообщает сам факт — а не
    содержимое, потому что содержимое и есть то, что защищаем.
    """
    log = Path(__file__).parent / "logs" / "llm_guard_blocks.log"
    if not log.exists():
        return {"blocks_24h": 0}
    today = get_today().isoformat()
    n = sum(1 for line in log.read_text(errors="ignore").splitlines()
            if line.startswith(today))
    if n:
        warn("Гард LLM блокировал отправку",
             f"{n} раз(а) сегодня. Настоящая находка или ложное срабатывание — "
             "смотри logs/llm_guard_blocks.log (там имена файлов, не значения).")
    return {"blocks_24h": n}


check("Блокировки LLM-гарда видны в отчёте", check_llm_guard_blocks_reported)


_CORR_RE = __import__("re").compile(r"\br\s*=\s*([+-]?0[.,]\d+)")
_CORR_WINDOW_DAYS = 30


def collect_ungrounded(window_days=None):
    """Находки «`r=0.X`, которого нет в ПРИНЯТОЙ вере»
    → (items, known_n, accepted, n_reports, reason).

    `items` — список (дата_отчёта, агент, |r| до сотых, сырая строка). `window_days=None`
    означает ВСЮ историю: это вход обратного заполнения журнала. `reason` — причина
    непринятия веры; она едет наружу, потому что «вера не принята» без причины
    заставляет читателя предупреждения искать её руками.

    Публичный вход намеренно: у формы `r=0.X` и у определения «обосновано» ровно один дом.
    Обратное заполнение, считающее нулевую точку СВОЕЙ копией регулярки, разъехалось бы с
    ночным датчиком молча — и baseline перестал бы соответствовать тому, что читает ратчет.
    """
    from belief_contract import read_belief
    b = read_belief()
    known = set()
    if b.get("accepted") and b.get("data"):
        for c in (b["data"].get("top_correlations") or []) + \
                 (b["data"].get("lab_metric_correlations") or []):
            try:
                known.add(round(abs(float(c.get("r"))), 2))
                # Гейт кладёт в веру и консилиуму не только r, но и r по периодам
                # (r_epoch_median/r_epoch_weakest) — это тоже посчитанное число, не выдумка.
                # Замер 01.10: «медиана r=-0.38» из sleep_core↔sleep_start стала гейтом владельцу.
                for k, v in c.items():
                    if k.startswith("r_") and isinstance(v, (int, float)):
                        known.add(round(abs(float(v)), 2))
            except (TypeError, ValueError):
                # Не глотаем: пустое `known` иначе молча объявило бы ВСЕ
                # корреляции в отчётах необоснованными — ложная тревога вместо
                # находки, и причина её была бы невидима.
                warn("В принятой вере запись без разбираемого r",
                     f"{c!r:.120} — не попадёт в множество подтверждённых")
    sql = ("SELECT agent_type, date, COALESCE(findings,'') || COALESCE(raw_output,'') "
           "FROM agent_reports")
    args = ()
    if window_days is not None:
        sql += " WHERE date >= ?"
        args = ((get_today() - timedelta(days=window_days)).isoformat(),)
    with db.get_conn() as conn:
        rows = conn.execute(sql, args).fetchall()
    items = []
    for a_type, a_date, text in rows:
        for raw in set(_CORR_RE.findall(text or "")):
            val = round(abs(float(raw.replace(",", "."))), 2)
            if val not in known:
                items.append((a_date, a_type, val, raw))
    return items, len(known), bool(b.get("accepted")), len(rows), b.get("reason")


def _log_ungrounded(items, belief_n: int, accepted: bool) -> int:
    """ПИСАТЕЛЬ журнала. Возвращает число НОВЫХ записей.

    Отказ записи НЕ глотается: существующая колонка ещё не доказывает, что
    писатель её заполняет. При молчаливом сбое ратчет мог бы вечно видеть
    baseline и молчать — читатель зеленел бы оттого, что писатель мёртв (§20).

    Определён ДО `check_correlations_grounded` намеренно: `check()` исполняет функцию
    сразу при импорте, и forward-ref дал бы NameError в ночном прогоне — ровно тот
    класс, что уже стоил боевого FAIL 07.08 (`re` в ратчете покрытия статусов).
    """
    if not items:
        return 0
    try:
        with db.get_conn() as conn:
            before = conn.execute("SELECT COUNT(*) FROM ungrounded_correlations").fetchone()[0]
            conn.executemany(
                "INSERT OR IGNORE INTO ungrounded_correlations "
                "(report_date, agent_type, value, belief_n, belief_accepted, logged_at) "
                "VALUES (?,?,?,?,?,?)",
                [(d, a, v, belief_n, int(accepted), str(today)) for d, a, v in items])
            conn.commit()
            after = conn.execute("SELECT COUNT(*) FROM ungrounded_correlations").fetchone()[0]
        return after - before
    except Exception as exc:  # noqa: BLE001 — громко, см. докстринг
        warn("журнал необоснованных корреляций не пишется",
             f"{type(exc).__name__}: {str(exc)[:120]} — находки снова живут только "
             f"в окне {_CORR_WINDOW_DAYS}д и испаряются")
        return 0


def _corr_value_adjudicated(value, report_date) -> bool:
    """Вердикт владельца по значению ЗАПИСАН, и находка не позже дня решения.

    ОДИН дом третьего состояния «решено» для обоих читателей журнала UC-B-09 —
    оконного датчика (check_correlations_grounded) и ратчета
    (check_ungrounded_corr_ratchet). До 2026-08-12 состояние знал только ратчет:
    оконный датчик продолжал звонить про решённый r=+0.42 до истечения окна
    (отчёт 01.08 исторический, из окна выходит лишь 31.08) — колокол после
    принятого решения учит не брать трубку. Рецидив звонит: отчёт ПОЗЖЕ дня
    решения этим фильтром не гасится."""
    import parked_decisions as _pd
    g = _pd.get(f"ungrounded_corr_{value}") or {}
    done_at = g.get("resolved_at") if g.get("status") == "resolved" else None
    return bool(done_at and str(report_date) <= str(done_at))


def check_correlations_grounded():
    """UC-B-09: каждое `r=0.X` в свежих отчётах агентов подтверждено ПРИНЯТОЙ верой.

    Класс: модель печатает коэффициент корреляции, которого никто не считал.
    Для врачебного отчёта выдуманное число неотличимо от измеренного — оно
    выглядит как факт и читается как факт.

    Источник истины — `belief_contract.read_belief()`, а НЕ сырой agent_reports:
    у веры есть приёмка статистическим гейтом, и непринятую подавать ИИ запрещено.
    Замер 2026-08-03, из-за которого источник и уточнён: `longitudinal_analysis`
    пишет в `findings` (14 КБ), а `raw_output` у него ПУСТ — прежняя проза
    BLUEPRINT велела читать ровно `raw_output` и «НЕ findings», то есть была
    инвертирована относительно реальности.

    ОСЬ СУЖДЕНИЯ — ПРОВЕНАНС, НЕ ЧЛЕНСТВО В ВЕРЕ (решение владельца 2026-09-08).
    Датчик строился как детектор ВЫДУМКИ. Замер за месяц с нулевой точки журнала:
    вне baseline ровно две строки, и ни одна не была ни цитатой, ни генерацией —
    обе провенанс. `gp` 01.08 r=0.42: коэффициент настоящий, выдумана ГЛУБИНА
    данных вокруг него («подтверждена на 10 годах»). `monthly_consilium` 31.08
    r=0.76: число посчитано по-настоящему, но ВТОРЫМ производителем мимо гейта
    (`hai_analysis.detect_correlation_drift`, Spearman 90/90 без перестановок и FDR;
    снят из входа консилиума 2026-09-03).

    Отсюда граница, названная вслух: «есть ли r в принятой вере» — СРАВНЕНИЕ, а не
    авторитет (RST: heuristic, выданный за authoritative oracle). Оно вырождается,
    когда вера пуста: предикат становится вакуумно истинным для любого r, и высокая
    чувствительность датчика — артефакт состояния, а не его качество. Замер
    2026-09-07: вера accepted=True/reason=ok, свежая, и НОЛЬ корреляций в ней.

    WARN, не FAIL, и это решение, а не отсрочка: агент вправе процитировать r из
    литературы, а отличить цитату от утечки по тексту нельзя — неавторитетным
    оракулом FAIL не выносят. Календарный триггер промоута СНЯТ (гейт
    `ungrounded_corr_review_2026_09` закрыт вердиктом 2026-09-08). Условие
    пересмотра: построена сеть РОЖДЕНИЯ (`check_correlation_producers_declared`)
    И вера перестала быть пустой; второе висит на открытом инварианте
    `validation_gate.profile_threshold_calibrated_offline`, поэтому это событие,
    а не дата.

    Этот датчик — сеть на УТЕЧКУ (число доехало до отчёта). Сеть на РОЖДЕНИЕ
    (кто в периметре вообще считает корреляцию) — отдельная, ниже.
    """
    items, known_n, accepted, n_reports, reason = collect_ungrounded(_CORR_WINDOW_DAYS)
    # Решённое владельцем не звонит (третье состояние, см. _corr_value_adjudicated);
    # в ЖУРНАЛ при этом пишется всё — история полная, гасится только колокол.
    ungrounded = [f"{a} {d}: r={raw}" for d, a, v, raw in items
                  if not _corr_value_adjudicated(v, d)]
    # ЖУРНАЛ (2026-08-08). Пишем ДО warn: находка обязана пережить скользящее окно.
    # Замер, породивший таблицу: `monthly_consilium 2026-07-08: r=0.55` перестал
    # находиться на 31-й день — не потому что починили, а потому что улика истекла.
    _log_ungrounded([(d, a, v) for d, a, v, _raw in items], known_n, accepted)
    if ungrounded:
        head = "; ".join(sorted(set(ungrounded))[:6])
        tail = f" (+ ещё {len(set(ungrounded)) - 6})" if len(set(ungrounded)) > 6 else ""
        warn("Корреляции в отчётах без судимого провенанса (UC-B-09)",
             f"{head}{tail}. Принятых значений в вере: {known_n}"
             + ("" if accepted else f"; вера НЕ принята ({reason})")
             + ". Вопрос не «выдумано ли число», а КТО его посчитал и судил ли его "
               "гейт: за месяц оба реальных случая (r=0.42 01.08, r=0.76 31.08) были "
               "провенансом, ни один — генерацией. Сеть на рождение производителя — "
               "check_correlation_producers_declared.")
    return {"checked_reports": n_reports, "known_r": known_n,
            "ungrounded": len(set(ungrounded))}


check("Корреляции в отчётах обоснованы (UC-B-09)", check_correlations_grounded)


def _park_owner_gate(gate_id: str, summary: str) -> None:
    """Гейт владельцу через существующий стор. Своего звонаря не заводим: у
    `parked_decisions` + `owner_nag` он уже есть, обязателен (launchd, окно 08–20,
    повтор раз в 3ч) и покрыт `check_doorbell_liveness`. Ступень 4 лестницы.

    Определён ДО регистрации читателя намеренно: `check()` исполняет функцию сразу,
    и forward-ref уронил бы проверку порядка регистрации в pre-commit."""
    try:
        import parked_decisions
        parked_decisions.park(gate_id, "owner_decision", summary)
    except Exception as exc:  # noqa: BLE001 — тишина тут = находка без читателя
        warn("гейт владельцу не поставлен", f"{gate_id}: {type(exc).__name__}: {str(exc)[:100]}")


def check_ungrounded_corr_ratchet():
    """ЧИТАТЕЛЬ журнала — читает СОДЕРЖИМОЕ, а не длину.

    Различает два случая, потому что у них разные диагнозы:
      • значение уже встречалось → ЦИТАТА (литература, протухшая вера, собственный
        прошлый отчёт агента). Замер 08.08: 0.51 напечатан четырежды, 0.76 и 0.99 —
        трижды. Выдумка так не выглядит.
      • значение НОВОЕ → генерация. Вот это и есть находка.

    Порог не выдуман: baseline — множество значений, попавших в журнал обратным
    заполнением (`is_baseline=1`). Второго дома у порога нет, он выводится из самой
    таблицы. Ратчет той же формы, что `producer_registry` и покрытие статусов
    staging: «покрыто либо явно объявлено», а не «стало на N больше».

    Находка ставит гейт владельцу (`parked_decisions`, kind=owner_decision): его
    читает `owner_nag` и звонит, пока решение не ЗАПИСАНО — уйти можно только
    `record_decision` или `defer`, но не «посмотрел». Обязательность читателя —
    решение владельца 2026-08-08 в ответ на вопрос «а кто это будет читать».
    """
    with db.get_conn() as conn:
        has = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                           "AND name='ungrounded_correlations'").fetchone()
        if not has:
            return None
        base = {r[0] for r in conn.execute(
            "SELECT DISTINCT value FROM ungrounded_correlations WHERE is_baseline=1")}
        fresh = conn.execute(
            "SELECT value, COUNT(*), MIN(report_date), MAX(report_date), "
            "       GROUP_CONCAT(DISTINCT agent_type) "
            "FROM ungrounded_correlations WHERE is_baseline=0 GROUP BY value").fetchall()
    novel = [r for r in fresh if r[0] not in base]
    repeats = [r for r in fresh if r[0] in base]
    # ТРЕТЬЕ СОСТОЯНИЕ: значение, по которому владелец УЖЕ вынес вердикт (2026-08-10).
    #
    # До этой правки его не было, и звонок про r=0.42 переоткрывался каждым прогоном:
    # решение «это выдумка глубины, чиним промпт» записано, причина в промпте закрыта,
    # а журнальная строка отчёта 01.08 осталась — она историческая и исчезнуть не может.
    # Колокол, который звонит после принятого решения, учит не брать трубку.
    #
    # База (`is_baseline=1`) для этого не годится: она означает «известное, цитируемое
    # значение», а вердикт был ровно противоположный. Дом решения один и уже есть —
    # `parked_decisions`; здесь мы его СПРАШИВАЕМ, а не заводим второй.
    #
    # РЕЦИДИВ ЗВОНИТ СНОВА: гасится только то, что напечатано НЕ ПОЗЖЕ дня решения.
    # Новый отчёт с тем же коэффициентом — новая находка, и молчать о ней нельзя.
    adjudicated = []
    if novel:
        # Один дом третьего состояния: _corr_value_adjudicated (общий с оконным
        # датчиком check_correlations_grounded с 2026-08-12).
        still = []
        for row in novel:
            if _corr_value_adjudicated(row[0], row[3]):   # row[3] = MAX(report_date)
                adjudicated.append(row)
            else:
                still.append(row)
        novel = still
    if not base:
        # Нулевой точки нет → ратчет НЕ откалиброван, и «всё новое» здесь означало бы
        # только то, что обратное заполнение не запускалось. Гейты владельцу в этом
        # состоянии не ставим: семь звонков, ни один из которых не про генерацию, —
        # это шум, который учит игнорировать звонок. Но и молчать нельзя (иначе
        # зелёный ОТ незаполненного дома, §20) — поэтому один громкий warn о причине.
        if fresh:
            warn("UC-B-09: журнал не откалиброван",
                 f"{len(fresh)} значений без нулевой точки — обратное заполнение "
                 f"(plans/backfill_ungrounded_2026-08-08.py --execute) не запускалось")
        return {"baseline": 0, "novel": 0, "repeats": 0, "uncalibrated": len(fresh)}
    if novel:
        detail = "; ".join(f"r={v} ×{n} ({who}, {d1}…{d2})" for v, n, d1, d2, who in novel[:6])
        warn(f"UC-B-09: НОВЫХ необоснованных значений — {len(novel)}",
             f"{detail}. Повтор известного = цитата, новое значение = агент напечатал "
             f"коэффициент, которого никто не считал. База (обратное заполнение "
             f"08.08): {len(base)} значений")
        for v, n, d1, d2, who in novel:
            _park_owner_gate(
                f"ungrounded_corr_{v}",
                f"В отчёте агента «{who}» за {d2} напечатан коэффициент связи r={v}, которого "
                f"система не считала: среди принятых расчётов такого числа нет. Значит, это "
                f"либо цитата из статьи, либо модель число придумала.\n\n"
                f"• Цитата — число запомнят как известное, больше вопрос не вернётся.\n"
                f"• Выдумка — чиним промпт агента, чтобы он не печатал непосчитанных цифр; "
                f"на долгой дистанции это выгоднее: выдуманное число в медотчёте читается как факт.\n"
                f"Если промолчишь — вопрос будет ждать, отчёты не меняются.")
    return {"baseline": len(base), "novel": len(novel), "repeats": len(repeats),
            "adjudicated": len(adjudicated)}


check("UC-B-09: ратчет журнала необоснованных корреляций", check_ungrounded_corr_ratchet)


# ── Сеть РОЖДЕНИЯ производителя корреляций (UC-B-09, 2026-09-08) ─────────────
# Ключ — имя модуля, значение — ПОЧЕМУ ему позволено считать корреляцию. Значение
# не парсится, но обязано быть правдой: его сверяет человек при ревизии, как
# allowlist в producer_registry.
CORR_PRODUCERS = {
    "correlation_gate": "сам валид-гейт — он и есть судья",
    "longitudinal_analysis": "производит веру, и только через correlation_gate "
                             "(gate_correlations на выходе)",
    "hai_analysis": "диагностика дрейфа, НЕ для веры: detect_correlation_drift снят "
                    "из входа консилиума 2026-09-03, живых вызывающих нет",
}
# Вызовы, по которым узнаётся производитель. Проверяется ИМЯ ВЫЗЫВАЕМОГО, а не
# подстрока в файле: слово в прозе ≠ вычисление (тот же урок, что у LLM-периметра —
# грубый греп объявил производителем девять файлов, которые лишь упоминают).
_CORR_CALLS = frozenset({"spearmanr", "pearsonr", "kendalltau", "corrcoef", "corr"})


def check_correlation_producers_declared(root=None, declared=None):
    """WARN: КТО в периметре считает корреляцию — объявлен либо находка.

    Сеть на РОЖДЕНИЕ, парная к `check_correlations_grounded` (сеть на утечку).
    Разница в оракуле, и она принципиальна. Утечку судить приходится
    НЕАВТОРИТЕТНО: цитату из литературы от протащенного мимо гейта числа по тексту
    отчёта не отличить — поэтому там WARN и не может быть иначе. Рождение судится
    АВТОРИТЕТНО: множество модулей, вычисляющих корреляцию, вычислимо, и «объявлен
    или нет» — двоичный факт.

    Класс, который это закрывает, реализовался ровно один раз и был найден ПОЗДНО:
    `hai_analysis.detect_correlation_drift` (Spearman 90/90 без перестановок и FDR)
    печатал r во вход консилиума, и обнаружилось это лишь тогда, когда число
    доехало до отчёта владельца (31.08, вердикт 03.09). Сеть на рождение назвала бы
    его в день появления. Честно: обоснование — выборка из ОДНОГО случая; ратчет
    строится дёшево (клон `check_llm_tracts_guarded`), поэтому цена оправдана, но
    выдавать это за статистику нельзя.

    Симметрия обеих сторон, как у `producer_registry`: модуль считает и не объявлен
    → находка; объявлен, а считать перестал → реестр протух, тоже находка. Иначе
    покрытие «улучшается» удалением кода, а список тихо расходится с деревом.

    ГРАНИЦЫ, названные вслух:
      · периметр — корень репозитория, как у LLM-сети; `scripts/` и `methodology/`
        (мастерская и harness) вне суда по тем же причинам, что в §15;
      · детектор знает ИМЕНА вызовов (`_CORR_CALLS`). Корреляция, посчитанная руками
        через ковариацию или чужой обёрткой, невидима — это не «покрыто всё», а
        «покрыто то, что названо»;
      · датчик отвечает на вопрос «кто считает», НЕ на «доезжает ли до отчёта
        владельцу»: последнее требует обхода графа вызовов и здесь не делается.
        Поэтому объявление несёт причину словами, и её судит человек.

    `root`/`declared` инъектируются тестом: подменяются ФАЙЛЫ и СПИСОК, а не слой
    под ними (§20 — зелёный обязан быть причинён тестом, не деревом машины)."""
    import ast as _ast
    root = Path(root) if root else Path(__file__).parent
    declared = CORR_PRODUCERS if declared is None else declared
    found = {}
    for p in sorted(root.glob("*.py")):
        if p.name == Path(__file__).name or p.name.startswith("test_"):
            continue
        try:
            tree = _ast.parse(p.read_text(encoding="utf-8", errors="ignore"))
        except (OSError, SyntaxError) as e:
            # Громко здесь же: неразобранный файл выпадает из периметра, то есть
            # производитель внутри него становится невидимым — ровно тот отказ,
            # против которого датчик и стоит.
            warn("периметр производителей r: файл не разобран",
                 f"{p.name}: {type(e).__name__} — производителя внутри датчик НЕ увидит")
            continue
        hits = set()
        for n in _ast.walk(tree):
            if isinstance(n, _ast.Call):
                f = n.func
                name = f.attr if isinstance(f, _ast.Attribute) else getattr(f, "id", None)
                if name in _CORR_CALLS:
                    hits.add(name)
        if hits:
            found[p.stem] = sorted(hits)
    undeclared = sorted(set(found) - set(declared))
    stale = sorted(set(declared) - set(found))
    if undeclared:
        warn(f"производители корреляций без объявления: {len(undeclared)}",
             "; ".join(f"{m} ({', '.join(found[m])})" for m in undeclared)
             + " — объяви в CORR_PRODUCERS с причиной либо проведи через "
               "correlation_gate. Число, посчитанное мимо гейта, доезжает до отчёта "
               "владельца неотличимым от судимого (случай r=0.76, 31.08).")
    if stale:
        warn(f"CORR_PRODUCERS протух: {len(stale)}",
             ", ".join(stale) + " — объявлены, но корреляций больше не считают; "
             "объявление, пережившее свой предмет, — ложная безопасность (§18)")
    return {"declared": len(declared), "found": sorted(found),
            "undeclared": undeclared, "stale": stale}


check("UC-B-09: производители корреляций объявлены", check_correlation_producers_declared)


def check_heldout_ready():
    """Гаситель нити validation-gate-repair (2026-08-08): оживить held-out/лаги ДАННЫМИ, не памятью.

    §18: отложенное «решим в сентябре» без гасителя схлопывается в тишину — held-out дозреет молча,
    и никто не узнает. Здесь СБОР входов (frozen_at, дата, q_lag из веры) + парковка владельцу;
    само РЕШЕНИЕ — в чистой `heldout_readiness.parks_due` (тестируется отдельно, см. её __main__).
    Идемпотентно: park() апсертит, но resolved НЕ переоткрываем — иначе рецидив-звонок после решения;
    ключ heldout-гейта несёт frozen_at (parks_due), поэтому НОВЫЙ freeze звонит НОВЫМ ключом,
    и resolved старого его не глушит (решение владельца 2026-08-23).
    Нет frozen_at → None (нить не сконфигурена). read_belief не бросает; неожиданный сбой — громкий
    ❌ через check(), а не тихий except (§7: не плодим молчаливые обработчики)."""
    import signal_family as _sf
    import heldout_readiness
    import i18n
    import parked_decisions
    import belief_contract
    fz = _sf.FROZEN_AT
    if not fz:
        return None
    try:
        weeks = (get_today() - date.fromisoformat(str(fz))).days // 7
    except ValueError:
        warn("гаситель held-out: frozen_at не разобран", f"signal_family.FROZEN_AT={fz!r} — не ISO-дата")
        return None
    # read_belief возвращает dict даже при отказе (не бросает); неожиданный сбой ловит check() громко.
    _b = belief_contract.read_belief()
    _fam = ((((_b.get("data") or {}).get("gate") or {}).get("mc_gap") or {}).get("families") or {})
    q_lag_passed = int((_fam.get("q_lag") or {}).get("passed") or 0)
    parked = []
    for gate_id, summary in heldout_readiness.parks_due(
            weeks, q_lag_passed, _sf.HELDOUT_FLOOR_WEEKS, frozen_at=str(fz), lang=i18n.lang_of()):
        rec = parked_decisions.get(gate_id)
        if rec is not None and rec.get("status") == "resolved":
            continue   # владелец уже решил — не переоткрывать (park() рецидивит resolved)
        _park_owner_gate(gate_id, summary)
        parked.append(gate_id)
    return {"weeks_post_freeze": weeks, "q_lag_passed": q_lag_passed, "parked": parked}


check("Held-out/лаги: гаситель оживления нити (validation-gate-repair)", check_heldout_ready)


def _sec_log(msg: str) -> None:
    """След авто-ремонта прав. Отдельный файл, а не общий лог: рецидив («один и тот же
    файл чинится каждую ночь») должен читаться одним `tail`, иначе конверт §13 пункт (d)
    выполнен формально — запись есть, а увидеть её нельзя."""
    p = Path(__file__).resolve().parent / "logs" / "security_repairs.log"
    try:
        p.parent.mkdir(exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(f"{today} {msg}\n")
    except OSError as exc:
        # НЕ `pass`: отказ следа — это отказ пункта (d) конверта §13, то есть ремонт
        # перестаёт быть посчитанным. print уезжает в run_checks.log и виден.
        print(f"⚠️ след авто-ремонта не записан: {exc!r} — ремонт прав идёт вслепую")


def check_security_sensors():
    """SECURITY.md «Квартальный чеклист» → код (security_sensors.py).

    Burn-in: все находки — WARN (не гейтят утренний отчёт). Промоут категории
    в FAIL — отдельным решением после ≥7 чистых дней (план agents_md_adoption).
    """
    import security_sensors
    # §13 ступень 1 ДО эскалации: права на .db чиним сами (обоснование конверта —
    # в docstring repair_db_perms). Ремонт идёт ПЕРЕД сбором находок, поэтому в
    # findings останется только то, что починить не удалось, — молчания не возникает.
    security_sensors.repair_db_perms(log=_sec_log)
    findings = security_sensors.collect_security_findings()
    for cat, items in findings.items():
        if items:
            head = "; ".join(items[:6])
            tail = f" (+ ещё {len(items) - 6})" if len(items) > 6 else ""
            warn(f"security:{cat}", head + tail)
    return {k: len(v) for k, v in findings.items()}


check("SEC-датчики отработали (perms/ports/tokens)", check_security_sensors, machine=True)


def check_changelog_freshness(changelog=None):
    """BL-DOCAGENT-DEAD-1: у каждой слитой нити с работой кода есть строка журнала.

    Смерть №2 (2026-06-18..07-06) была тихой: doc_agent не бежал нигде. Смерть №3
    (2026-09-12..23) — тоже: хук после коммита на ветке нити выходит до doc_agent, а
    слияние хук не зовёт; сторож «>8 коммитов после записи» звенел, но как вопрос
    владельцу «чинить ли». С 23.09 строку пишет закрытие нити (doc_agent --thread-merge),
    а сторож судит не число-порог, а факт: слитая нить с кодом без строки `[нить <slug>]`.
    Один дом «что считать работой нити» — git_facts (им же пишет писатель строки).
    Граница: нить, слитая дважды под одним именем, считается покрытой первой строкой.
    """
    import git_facts
    merges = git_facts.thread_merges()
    if merges is None:
        return "git недоступен (песочница) — журнал не судится"
    text = Path(changelog or Path(__file__).parent / "CHANGELOG.md").read_text(encoding="utf-8")
    missing = []
    for sha, slug in merges:
        subjects = git_facts.thread_code_subjects(sha)
        if subjects is None:
            warn("журнал изменений: работа нити не читается", f"git log {sha[:8]} не ответил")
            return None
        if subjects and f"[нить {slug}]" not in text:
            missing.append(slug)
    if missing:
        warn(f"журнал изменений: {len(missing)} слит(ых) нит(ей) без строки",
             ", ".join(missing[-5:]) + " — строку пишет scripts/thread_finish.sh "
             "(doc_agent --thread-merge); восстановить: doc_agent --thread-merge <sha> --thread <slug>")
    return {"thread_merges": len(merges), "missing": len(missing)}


check("журнал изменений: у каждой слитой нити есть строка", check_changelog_freshness)


_ARCH_STAMPS = (("ARCH_SNAPSHOT.md", re.compile(r"Авто-генерировано gen_blueprint\.py (\d{4}-\d\d-\d\d)")),
                ("ARCH_SNAPSHOT.en.md", re.compile(r"Generated by gen_blueprint\.py (\d{4}-\d\d-\d\d)")))
_EN_REFS = ("ARCH_SNAPSHOT.en.md", "TESTING_CONTRACTS.en.md")


def check_arch_map_fresh(root=None):
    """Карта проекта (ARCH_SNAPSHOT ru/en) пересобрана после последнего закрытия нити.

    Решение владельца 29.09 (нить arch-en-gen): карту пересобирает scripts/arch_regen.sh при
    каждом закрытии нити. До этого четыре генератора не звал никто, и реестр модулей простоял
    с 28.06 (C-89). Пересборка может не пройти (Studio спит, генератор упал) — закрытие нити
    от этого не падает, поэтому отказ видит этот датчик: штамп реестра старше последнего
    слияния нити больше чем на сутки. Он же считает пояснения без перевода в английских
    справочниках (метка ⟨untranslated⟩) — их пополняет память перевода при пересборке.
    Граница: штамп пишет только gen_blueprint; остальные блоки судятся сторожем
    tests/consistency/test_arch_generated_refs.py (названные файлы существуют), не свежестью.
    """
    import subprocess
    root = Path(root or Path(__file__).parent)
    try:
        last = subprocess.run(["git", "-C", str(root), "log", "-1", "--merges", "--format=%cs"],
                              capture_output=True, text=True, timeout=30).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "git недоступен (песочница) — карта не судится"
    if not last:
        return "слияний нет — карта не судится"
    stale = []
    for name, rx in _ARCH_STAMPS:
        path = root / name
        m = rx.search(path.read_text(encoding="utf-8")) if path.exists() else None
        if not m:
            stale.append(f"{name}: штампа генератора нет")
        elif date.fromisoformat(m.group(1)) + timedelta(days=1) < date.fromisoformat(last):
            stale.append(f"{name}: реестр от {m.group(1)}, последнее слияние {last}")
    untranslated = sum((root / n).read_text(encoding="utf-8").count("⟨untranslated⟩")
                       for n in _EN_REFS if (root / n).exists())
    if stale:
        warn("карта проекта не пересобрана после закрытия нити",
             "; ".join(stale) + " — пересборка: scripts/arch_regen.sh (лог logs/arch_regen.log на MacBook)")
    if untranslated:
        warn(f"английские справочники: {untranslated} пояснений без перевода",
             "метка ⟨untranslated⟩ — память перевода пополняет scripts/arch_regen.sh "
             "(desc_translation --refresh); отказ по форме виден в его логе")
    return {"last_merge": last, "stale": len(stale), "untranslated": untranslated}


check("карта проекта пересобрана после закрытия нити", check_arch_map_fresh)


def check_consilium_freshness():
    """monthly_consilium пишет agent_report('monthly_consilium'). Молчание = гипотезы
    не оцениваются MDT. След обязан покрывать последний плановый запуск по живому плисту
    (до 23.09 — литерал «40д», monthly + люфт). Producer для context_gate_orphan."""
    rows = db.get_agent_report("monthly_consilium", n=1)
    if not rows:
        warn("консилиум ни разу не сохранял отчёт", "monthly_consilium в agent_reports пуст")
        return {"status": "never"}
    d = (rows[0].get("date") or "")[:10]  # get_agent_report → list, как в check_gp_reports
    if d:
        age = (today - date.fromisoformat(d)).days
        # След — момент записи (created_at, UTC): `date` консилиума — последний день
        # месяца-периода (31.08), а запуск — 1-го числа 04:00 по плисту.
        with db.get_conn() as conn:
            at = conn.execute("SELECT MAX(created_at) FROM agent_reports "
                              "WHERE agent_type='monthly_consilium'").fetchone()[0]
        covered, fire = _schedule_covers(CONSILIUM_LABEL, _utc_trace(at))
        if covered is None:
            _schedule_unjudged("MDT-консилиум", CONSILIUM_LABEL)
        elif not covered:
            warn("MDT-консилиум не бежал",
                 f"последний {d} — {age}д; плановый запуск {fire:%d.%m %H:%M} не отметился")
        return {"last": d, "age_days": age}
    return {"status": "no_date"}


check("MDT-консилиум свеж (producer)", check_consilium_freshness)


def scan_calendar_staleness(db_paths, current_path, error_age_h):
    """Чистая/тестируемая: находки про кэш календаря по тенантам.
    Возврат list[(tenant, kind, age_h|None)], kind in {missing, stale}. Пусто = все свежи.
    'missing' — только для current_path (owner всегда с календарём); чужой тенант без
    кэша молча пропускаем — может не использовать календарь."""
    cur = Path(current_path).resolve()
    out = []
    for dbp in db_paths:
        tenant = Path(dbp).parent.parent.name
        p = Path(dbp).parent / "calendar_cache.json"
        if not p.exists():
            if Path(dbp).resolve() == cur:
                out.append((tenant, "missing", None))
            continue
        age_h = (time.time() - p.stat().st_mtime) / 3600
        if age_h > error_age_h:
            out.append((tenant, "stale", round(age_h, 1)))
    return out


def check_calendar_freshness():
    """calendar_cache.json кормит GP-контекст; fetcher hourly. Молчание = устаревший
    календарь в промпте (stale-intermediate-layer). Пороги из fetcher, не дублируем.

    Тенант-осознанный (2026-07-10): календарь есть у КАЖДОГО тенанта (partner-джоба
    calendar-sync.partner). Инцидент: токен партнёра протух → его кэш гнил 9 дней, а
    однотенантный gcf.cache_path() видел только owner и молчал. Логика — в чистой
    scan_calendar_staleness (тестируема без home/fs)."""
    import google_calendar_fetcher as gcf
    dbs = _tenant_db_paths(include_current=True)
    for tenant, kind, age_h in scan_calendar_staleness(dbs, db.DB_PATH, gcf.CACHE_ERROR_AGE_H):
        if kind == "missing":
            warn("calendar cache отсутствует",
                 f"{tenant}: {Path(db.DB_PATH).parent}/calendar_cache.json — fetcher не отработал ни разу?")
        else:
            warn("calendar cache устарел",
                 f"{tenant}: {age_h:.0f}ч > {gcf.CACHE_ERROR_AGE_H}ч — hourly sync мёртв?")
    return {"tenants_checked": len(dbs)}


check("календарь свеж (producer)", check_calendar_freshness)


def check_tenant_biometrics_freshness():
    """Биометрия свежа У КАЖДОГО тенанта, а не только у владельца.

    check_data_freshness ходит через db.get_day() и видит базу текущего
    процесса. Исключение для ещё не активированного импорта сиблинга должно
    прекращаться при появлении входных данных. Иначе описание окружения
    переживает своё условие (§18), а остановка импорта остаётся невидимой.
    Этот датчик проверяет свежесть отдельно у каждого тенанта.

    Порог — общий `DATA_FRESHNESS_DAYS` (§9: дом уже есть, второго не заводим).
    Сиблингу — только возраст в днях, без дат и значений (C-19: монитор не должен сам
    стать cross-tenant утечкой). WARN, не FAIL: каденция чужого тенанта не краснит
    ночной прогон владельца (та же линия, что у `check_lab_freshness`).
    """
    out = {}
    for _tag, _conn, _cur in _iter_tenant_ro():
        # Спрашиваем ПРЯМО, а не ловим исключение: тихий except скрыл бы и битую БД
        # (та же дисциплина, что в check_lab_review_queue_movement). Отсутствие
        # таблицы/колонки — законное состояние тенанта, отказ БД — нет.
        if not _conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                             "AND name='daily_metrics'").fetchone():
            continue
        cols = {r[1] for r in _conn.execute("PRAGMA table_info(daily_metrics)")}
        hrv_cols = [c for c in ("hrv", "readiness_hrv_balance") if c in cols]
        if not hrv_cols:
            continue
        where = " OR ".join(f"{c} IS NOT NULL" for c in hrv_cols)
        row = _conn.execute(f"SELECT MAX(date) FROM daily_metrics WHERE {where}").fetchone()
        last = row[0] if row else None
        if not last:
            warn(f"[{_tag}] биометрии нет вовсе", "импорт не отработал ни разу?" if _cur else "")
            continue
        age = (today - date.fromisoformat(str(last)[:10])).days
        out[_tag] = age
        if age > DATA_FRESHNESS_DAYS:
            warn(f"[{_tag}] биометрия устарела: {age}д (порог {DATA_FRESHNESS_DAYS}д)",
                 f"последняя: {last}" if _cur else "")
    return out or None


check("биометрия свежа у каждого тенанта (producer: oura-import.partner)",
      check_tenant_biometrics_freshness)


def check_producer_registry():
    """context_gate_orphan: каждый scheduled-производитель покрыт датчиком или
    явно освобождён. Ловит класс BL-DOCAGENT-DEAD-1 — задачу, добавленную/
    гейтнутую без сторожа факта исполнения. Находки — WARN (burn-in), одной
    строкой (анти-фатига)."""
    import producer_registry
    for finding in producer_registry.collect_producer_findings():
        warn("producer_registry", finding)
    return {"status": "checked"}


check("реестр производителей полон (context_gate_orphan)", check_producer_registry)


# Зеркала сидов `system_config` (health_db.init_db). НЕ альтернативные нормы —
# резерв на случай недоступной БД; равенство держит coherence-тест.
_INTAKE_FALLBACK = {"lab.intake_pulse_max_min": 10.0,
                    "lab.intake_grace_hours": 1.0,
                    "lab.review_stale_days": 7.0}


def _intake_cfg(key: str, conn=None) -> float:
    try:
        import config_db
        return float(config_db.get_config(key, _INTAKE_FALLBACK[key], conn=conn))
    except Exception:  # noqa: BLE001 — нет БД/таблицы: резерв, но ГРОМКО (§14)
        warn(f"{key} недоступен в БД", f"взят резерв {_INTAKE_FALLBACK[key]}")
        return _INTAKE_FALLBACK[key]


def check_lab_intake_pulse():
    """§14 для вотчера входа: доказываем, что ЦИКЛ крутится, а не что процесс жив.

    `check_daemons_alive` смотрит PID из launchctl — это отвечает на вопрос «не
    упал ли», но не на «опрашивает ли». KeepAlive перезапускает только упавшего;
    процесс, застрявший в блокирующем вызове, живёт с PID и не делает ничего.
    Замер 31.07, ради которого датчик и появился: вотчер поднят 29.07, лог 0 байт,
    файл состояния не тронут двое суток — отличить «файлов не приносили» от
    «вотчер ослеп» было НЕЧЕМ.

    Пульс — mtime файла состояния (`process_once` делает `touch` каждым опросом).
    Studio-only; warn, не fail — молчание входа не портит данные, оно их не пускает.

    ПЕРИМЕТР ВЫЧИСЛЯЕТСЯ (§18, ложный путь C-19 tenant-слепота): тенанты берутся
    из `lab_intake_watcher.intake_jobs()` — обход launchd по тому, ЧТО джоба
    исполняет. До 2026-08-01 здесь стояла проза «плист вотчера ровно один,
    партнёрского не существует»; партнёрская джоба существовала с 16.07 под именем
    `com.larry.health.watcher.partner` и трое суток слала тенанту по сообщению в
    двадцать секунд — при зелёном датчике, который честно объявил, что смотреть
    больше некуда. Периметр, описанный словами, не стареет вместе с миром.
    Terminus §14: датчик живёт в ночном integrity, чья живость доказывается
    присутствием ночного Telegram-дайджеста."""
    import socket
    if not infra_config.is_primary():
        return None
    import lab_intake_watcher
    import secrets_paths as _sp
    # Плист переехавшего тенанта остался в LaunchAgents, но не загружен: его вход принимает
    # контейнер, а здесь пульс заморожен (замер 02.10: «вход молчит 2959м» у владельца).
    jobs = [(lb, hb) for lb, hb in lab_intake_watcher.intake_jobs()
            if not any(_sp.moved_to_container(r) for r in hb.parents)]
    if not jobs:
        warn("вход: вотчер не установлен ни у одного тенанта",
             "в LaunchAgents нет джобы, исполняющей lab_intake_watcher.py")
        return {"jobs": 0}
    limit = _intake_cfg("lab.intake_pulse_max_min")
    pulse = {}
    for label, hb in jobs:
        if not hb.exists():
            warn(f"вход: вотчер ни разу не отметился ({label})",
                 f"{hb} отсутствует — launchd выгружен или первый запуск не дошёл")
            pulse[label] = None
            continue
        age_min = (get_now().timestamp() - hb.stat().st_mtime) / 60
        if age_min > limit:
            warn(f"вход молчит: цикл вотчера не крутится ({label})",
                 f"пульс {int(age_min)}м назад (>{int(limit)}м) — процесс жив, "
                 f"но опроса нет; проверь launchctl list {label}")
        pulse[label] = int(age_min)
    return {"pulse_min": pulse}


check("пульс вотчера входа (§14)", check_lab_intake_pulse)


def blind_spot_ages(candidates, seen, decided, watermark, grace_s, now):
    """Чистое ядро датчика слепоты: (имя, mtime) → возрасты застрявших, в секундах.

    Отделено от IO, потому что сам датчик Studio-only и на тест-хосте не
    исполняется — без этого разделения у ветвлений не было бы ни одного
    запускаемого оракула, а «датчик есть» означало бы «датчик не проверен».

    Застрял = новее водяного знака · старше отсрочки · нет в staging (`seen`)
    · нет записанного решения (`decided`).
    """
    out = []
    for name, mtime in candidates:
        if name in seen or name in decided:
            continue
        if mtime < watermark:
            continue          # лежал до включения вотчера — не его забота
        if (now - mtime) < grace_s:
            continue          # ещё в отсрочке: распознавание идёт минуты
        out.append(now - mtime)
    return out


def check_lab_intake_blind_spot():
    """Вторая половина предиката (§17): пульс есть, а работы нет.

    Пульс доказывает, что цикл крутится, и молчит о том, ЧТО цикл при этом
    видит. Обратная сторона — файл лежит в наблюдаемом каталоге дольше отсрочки,
    новее водяного знака, и в staging его нет: вотчер смотрит и не берёт.
    Предикат считается ДРУГИМ путём, чем решение вотчера: каталоги берём из
    `watched_dirs()` (конфигурация — один дом), но «взят или нет» спрашиваем у
    БАЗЫ, а не у его состояния. Спроси у состояния — получишь согласованность
    вотчера с самим собой, а не с каноном.

    Файл, который вотчер осознанно отклонил (`notlab`) или пометил сайдкаром
    (`.failed`/`.norows`), здесь НЕ находка: решение принято и записано.
    Имена файлов не печатаем — в них персональные данные; печатаем счёт и возраст.
    Studio-only; warn."""
    import json
    import socket
    import sqlite3 as _sq
    if not infra_config.is_primary():
        return None
    import health_db as _db
    import lab_intake_watcher as _w
    hb = _w.heartbeat_path()
    if not hb.exists():
        return None      # об отсутствии состояния уже сказал пульс — не дублируем
    try:
        state = json.loads(hb.read_text())
    except Exception as e:  # noqa: BLE001
        warn("вход: состояние вотчера нечитаемо",
             f"{type(e).__name__} — водяной знак потерян, вотчер начнёт архив заново")
        return None
    watermark = float(state.get("watermark", 0) or 0)
    decided = set(state.get("notlab", [])) | set(state.get("forced", []))
    try:
        c = _sq.connect(f"file:{_db.DB_PATH}?mode=ro", uri=True)
        seen = {Path(r[0]).name for r in
                c.execute("SELECT DISTINCT source_file FROM lab_results_staging") if r[0]}
        c.close()
    except Exception as e:  # noqa: BLE001
        warn("вход: не смог прочитать staging для предиката слепоты", str(e)[:120])
        return None
    grace_s = _intake_cfg("lab.intake_grace_hours") * 3600
    now = get_now().timestamp()
    cand = []
    for d in _w.watched_dirs():
        if not d.exists():
            continue
        for f in d.glob("*"):
            if not f.is_file() or f.name.startswith("."):
                continue
            if f.suffix.lower() not in _w.DOC_EXTS:
                continue
            if (f.with_suffix(f.suffix + ".failed").exists()
                    or f.with_suffix(f.suffix + ".norows").exists()):
                continue   # решение записано сайдкаром — не находка
            try:
                cand.append((f.name, f.stat().st_mtime))
            except OSError:
                continue
    stuck = blind_spot_ages(cand, seen, decided, watermark, grace_s, now)
    if stuck:
        warn("вход видит и не берёт",
             f"{len(stuck)} файл(ов) новее водяного знака не доехали до staging; "
             f"старший ждёт {int(max(stuck) / 3600)}ч (имена не печатаем — ПДн)")
    return {"stuck": len(stuck)}


check("вход не слеп: новые файлы доезжают до staging (§17)", check_lab_intake_blind_spot)


def check_lab_review_queue_movement(paths=None):
    """Очередь человека движется. Непустая очередь без движения = потерянные
    измерения: строки ждут подтверждения и в канон не идут, а «ничего не
    произошло» читается как «всё хорошо» (BL-DOCAGENT-DEAD-1, тот же класс).

    Проверяется возраст ожидающих строк у обоих тенантов; warn.
    paths — инъекция тестов (§20: тело обязано исполняться набором,
    урок check_staging_status_coverage)."""
    import sqlite3 as _sq
    days = _intake_cfg("lab.review_stale_days")
    import re as _re
    for path in (paths if paths is not None else _tenant_db_paths(include_current=True)):
        tag = Path(path).parent.parent.name
        conn = _sq.connect(f"file:{path}?mode=ro", uri=True)
        # Отсутствие таблицы — законное состояние тенанта, а не отказ. Спрашиваем
        # ПРЯМО, а не ловим исключение: тихий except здесь скрыл бы и битую БД.
        has = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                           "AND name='lab_results_staging'").fetchone()
        # Возраст — от СМЕНЫ статуса (когда строка реально попала к человеку), не от
        # разбора документа (2026-08-31): бланк 2023 года, триажированный сегодня,
        # числился «ждёт 23 дня» при реальных нуле. NULL у старых строк → created_at,
        # честно завышает, как раньше. Колонку добавляет init_db идемпотентно; БД
        # тенанта, где init ещё не бежал, читается по-старому, а не падает.
        row = None
        if has:
            _c = [r[1] for r in conn.execute("PRAGMA table_info(lab_results_staging)")]
            _age = ("COALESCE(status_changed_at, created_at)"
                    if "status_changed_at" in _c else "created_at")
            row = conn.execute(
                f"SELECT COUNT(*), MIN({_age}) FROM lab_results_staging "
                "WHERE review_status='review'").fetchone()
        conn.close()
        n, oldest = (row or (0, None))
        if not n or not oldest:
            continue
        head = str(oldest)[:10]
        if not _re.fullmatch(r"\d{4}-\d{2}-\d{2}", head):
            warn(f"очередь ревью[{tag}]: дата не разбирается",
                 f"created_at={str(oldest)[:30]!r} — возраст очереди посчитать нечем")
            continue
        waited = (today - date.fromisoformat(head)).days
        if waited > days:
            # Причины ожидания различаются: отсутствует результат,
            # справочное имя или назначенное каноническое имя.
            # Например, потерянный качественный результат нельзя восстановить
            # одним подтверждением строки в дашборде. Алерт должен назвать
            # недостающий вход и нужное действие; иначе он приучает
            # игнорировать сигналы (§13, banner-blindness).
            blk = _review_queue_blockers(path)
            detail = "; ".join(f"{k} — {v}" for k, v in blk.items() if v)
            warn(f"очередь ревью[{tag}] не движется",
                 f"{n} строк, самая старая {waited}д (>{int(days)}д). Из них: {detail}. "
                 f"Что делать: docs/how-to/lab_review_queue.md")
    return None


def _review_queue_blockers(db_path) -> dict:
    """Раскладка очереди ревью по ПРИЧИНЕ, а не один счётчик.

    Только считает, ничего не чинит. Порядок ключей — по убыванию бессилия человека:
    нет результата → нет имени → действительно ждёт решения. Первые две категории
    человек в дашборде разрешить не может в принципе, и называть их «ждут человека»
    значит звать его на работу, которой нет.
    """
    import sqlite3 as _sq
    import lab_canon as _lc
    conn = _sq.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT value, value_text, canonical_name, raw_name FROM lab_results_staging "
            "WHERE review_status='review'").fetchall()
    finally:
        conn.close()
    out = {"нет результата, подтверждать нечего": 0,
           "имя не сведено к канону": 0,
           "ждёт твоего решения": 0}
    for value, value_text, cname, raw in rows:
        if value is None and not (value_text or "").strip():
            out["нет результата, подтверждать нечего"] += 1
            continue
        # Нормализуем и СОХРАНЁННОЕ имя тоже. Первая редакция брала `canonical_name`
        # как есть — и строка `Plateletcrit` осталась в «имя не сведено» уже ПОСЛЕ
        # того, как алиас `plateletcrit → PCT` был заведён: датчик судил сырое поле,
        # а промоут судит нормализованное. Два разных ответа на один вопрос — ровно
        # то расхождение ключей, ради которого этот датчик и переписан.
        cn = _lc.normalize(cname or raw or "")
        if cn not in _lc.CANONICALS:
            out["имя не сведено к канону"] += 1
            continue
        out["ждёт твоего решения"] += 1
    return out


check("очередь ревью движется (детект-без-доставки)", check_lab_review_queue_movement)


# Карта покрытия статусов staging: статус → КТО его стережёт. Единственный дом этого
# знания; `docs/reference/lab_staging_statuses.md` ссылается сюда, а не перечисляет
# заново (иначе два дома разъедутся молча — §15/§18).
_STAGING_STATUS_WATCHERS = {
    "pending":  "check_staging_status_coverage (здесь же — возраст очереди классификации)",
    "review":   "check_lab_review_queue_movement",
    "auto":     "check_promotion_backlog_stale",
    "gold":     "check_promotion_backlog_stale",
    "rejected": "не ждёт никого: решение уже принято (спрашивать второй раз = учить игнорировать датчик)",
    "promoted": "не ждёт никого: строка уехала в канон",
    # Статус specialized означает, что строку принял её целевой спец-слой.
    # Оставлять принятую строку в pending нельзя: монитор ошибочно назовёт
    # её необработанной, хотя перенос уже состоялся.
    "specialized": "не ждёт никого: строка принята спец-слоем (lab_specialized, по вердикту класса)",
}


def check_staging_status_coverage(paths=None):
    """РАТЧЕТ: ни одна строка staging не лежит в статусе, который никто не стережёт.

    `paths` — только для инъекции в тестах. Аргумент появился 2026-08-07 не для
    красоты: первая редакция судилась ТОЛЬКО по карте (`_STAGING_STATUS_WATCHERS`),
    тело функции не исполнялось ни одним тестом — и `NameError: name 're' is not
    defined` доехал до боевого прогона. Тест, судящий данные вместо механизма,
    зелен ровно тогда, когда механизм не запускался (§20).

    Возможный сбой: писатель ставит `pending`, а датчики спрашивают только
    `review_status='review'` и `('auto','gold')`. Строка остаётся вне покрытия.
    Устаревший канон при этом не доказывает отсутствия свежего входа:
    данные могут ждать в staging. Состояние канона и очереди читаются вместе.

    Почему ратчет, а не третий датчик на `pending`: третий хардкоженный список
    воспроизвёл бы ровно тот дефект, который чиним, — следующий новый статус снова
    провалился бы молча. Форма взята у `producer_registry` (задача обязана быть либо
    покрытой, либо явно освобождённой); здесь то же самое про статус строки.
    Непокрытый статус — находка, а не умолчание.

    Вторая половина (не ратчет, а собственно сторож `pending`): очередь КЛАССИФИКАЦИИ
    тоже обязана двигаться. `lab_triage`, который разбирает `pending`, вызывающего в
    проде не имеет — это ручной инструмент; значит молчание здесь ожидаемо и потому
    особенно нуждается в датчике. Порог — общий `lab.review_stale_days` (§9: дом уже
    есть, второго не заводим).
    """
    import re as _re
    import sqlite3 as _sq
    days = _intake_cfg("lab.review_stale_days")
    for path in (paths if paths is not None else _tenant_db_paths(include_current=True)):
        tag = Path(path).parent.parent.name
        conn = _sq.connect(f"file:{path}?mode=ro", uri=True)
        has = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                           "AND name='lab_results_staging'").fetchone()
        rows = conn.execute(
            "SELECT COALESCE(review_status,'pending'), COUNT(*), MIN(created_at) "
            "FROM lab_results_staging GROUP BY 1").fetchall() if has else []
        conn.close()
        for status, n, oldest in rows:
            if status not in _STAGING_STATUS_WATCHERS:
                warn(f"staging[{tag}]: статус «{status}» не стережёт никто",
                     f"{n} строк; добавь его в _STAGING_STATUS_WATCHERS с именем датчика "
                     f"или с обоснованием, почему ждать нечего")
                continue
            if status != "pending" or not n or not oldest:
                continue
            head = str(oldest)[:10]
            if not _re.fullmatch(r"\d{4}-\d{2}-\d{2}", head):
                warn(f"staging[{tag}]: дата не разбирается",
                     f"created_at={str(oldest)[:30]!r} — возраст очереди посчитать нечем")
                continue
            waited = (today - date.fromisoformat(head)).days
            if waited > days:
                warn(f"staging[{tag}]: {n} строк не классифицированы {waited}д (>{int(days)}д)",
                     "они не в очереди человека и не в очереди промоута — их не видит "
                     "ни один другой датчик. Разбирает lab_triage (ручной запуск): "
                     "docs/how-to/lab_review_queue.md")
    return None


check("покрытие статусов staging (ни одна строка не молчит)", check_staging_status_coverage)


def check_glossary_targets_known():
    """Цель подтверждённого алиаса обязана быть ИЗВЕСТНЫМ каноническим именем.

    Промоут принимает решение человека первым, но только если канон эту цель
    знает: иначе подтверждение завело бы новое имя мимо справочника и разъехало
    аналит на два тренда молча. Провал на следующую ступень безопасен, но нем —
    читателем этой немоты и служит датчик. Плюс отдельно конфликты форматов:
    два разных ответа человека на одно имя машина выбрать не может (§13).
    Оба тенанта; warn."""
    import sqlite3 as _sq
    import lab_canon
    import labs_db as _ldb
    for path in _tenant_db_paths(include_current=True):
        tag = Path(path).parent.parent.name
        conn = _sq.connect(f"file:{path}?mode=ro", uri=True)
        has = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                           "AND name='lab_name_aliases'").fetchone()
        if not has:
            conn.close()
            continue      # глоссария у тенанта нет — стеречь нечего, это не отказ
        targets = _ldb.confirmed_alias_targets(conn=conn)
        clash = _ldb.confirmed_alias_conflicts(conn=conn)
        conn.close()
        unknown = sorted(t for t in targets if t and t not in lab_canon.CANONICALS)
        if unknown:
            warn(f"глоссарий[{tag}]: цель не известна канону",
                 f"{len(unknown)}: {', '.join(unknown[:8])} — подтверждение человека "
                 f"НЕ применяется (провал на следующую ступень), пока имя не сведено")
        if clash:
            warn(f"глоссарий[{tag}]: одно имя, разные ответы человека",
                 f"{len(clash)}: {', '.join(r for r, _v in clash[:8])} — выброшены "
                 f"из словаря, выбирать между ответами человека машина не может")
    return None


check("цели глоссария известны канону (F-14)", check_glossary_targets_known)


def check_unit_conversion_coverage(conn=None):
    """WARN burn-in (plan_consilium_under_manifest 2026-07-08 Фаза 1): аналит в
    единицах, НЕ сводимых к одной через lab_canon (_norm_unit+to_conventional) →
    траектория конверта сравнит несопоставимое (условно CRP 1.0 mg/L vs 0.1 mg/dL = мнимый
    ×10). Ратчет покрытия канонизации под рост лаб (лаборатории разных стран):
    baseline = worklist, гонишь к нулю расширением _norm_unit (косметика) / правилом
    (магнитуда). Логика — чистая lab_canon.unit_convergence_gaps (тестируема)."""
    import contextlib
    import health_db as _db
    import lab_canon
    from collections import defaultdict
    _sql = ("SELECT test_name, unit, COALESCE(specimen, '') FROM lab_results "
            "WHERE unit IS NOT NULL AND unit != '' AND value IS NOT NULL")
    try:
        # `conn` — только инъекция теста (тело обязано исполняться набором, §20);
        # в проде — боевой коннект.
        with (contextlib.nullcontext(conn) if conn is not None else _db.get_conn()) as c:
            rows = c.execute(_sql).fetchall()
    except Exception as e:  # noqa: BLE001 — таблицы может не быть в урезанном окружении
        warn("не смог посчитать покрытие конверсии единиц", str(e)[:120])
        return None
    by = defaultdict(set)
    for tn, unit, specimen in rows:
        canon = tn if tn in lab_canon.CANONICALS else lab_canon.normalize(tn)
        # Размерность — признак идентичности (ADR 2026-08-12): NRBC «%» и «10⁹/л» — две
        # величины, и «несходимость единиц» между ними — не находка, а тавтология.
        # Ключ группы — уточнённое имя, тем же правилом, что у промоута (dimension_key).
        canon = lab_canon.dimension_key(canon, unit)
        # Материал — из КОЛОНКИ, не из единицы (дом один, §16). Прежний `specimen_key`
        # выводил материал из единицы и не видел, что Phosphorus в мкг/л — моча
        # (панель ИСП-МС): датчик звал сывороточный и мочевой
        # фосфор «несходимостью», а сходиться им не положено. Рендер страницы 2026-08-31.
        if specimen and specimen != "blood":
            canon = "%s[%s]" % (canon, specimen)
        by[canon].add(unit)
    gaps = lab_canon.unit_convergence_gaps(by)
    if gaps:
        warn("покрытие конверсии единиц (worklist канонизации)",
             "%d: %s" % (len(gaps), " | ".join(gaps[:12])))
    return {"gaps": len(gaps)}


check("покрытие конверсии единиц лабов (несходимость)", check_unit_conversion_coverage)


def check_urine_name_convention(conn=None):
    """Конвенция имён мочевого домена — Urine_* (решение владельца 2026-08-31).

    Ратчет против возврата двух конвенций: мочевая строка канона с plain-именем,
    чей Urine_*-двойник ИЗВЕСТЕН канону, — находка (так одна ИСП-МС панель лежала
    двумя наборами строк). Сырые имена вне карты (органические кислоты, этап имён)
    не судятся: у них Urine_-двойника в CANONICALS нет. `conn` — инъекция теста.
    """
    import contextlib
    import health_db as _db
    import lab_canon
    try:
        with (contextlib.nullcontext(conn) if conn is not None else _db.get_conn()) as c:
            rows = c.execute("SELECT test_name, COUNT(*) FROM lab_results "
                             "WHERE specimen='urine' GROUP BY 1").fetchall()
    except Exception as e:  # noqa: BLE001 — таблицы может не быть в урезанном окружении
        warn("не смог посчитать конвенцию имён мочи", str(e)[:120])
        return None
    bad = sorted(f"{n}×{k}" for n, k in rows
                 if not (n or "").startswith("Urine_")
                 and f"Urine_{n}" in lab_canon.CANONICALS)
    if bad:
        warn("моча под plain-именем при известном Urine_-каноне (конвенция 2026-08-31)",
             f"{len(bad)}: {', '.join(bad[:8])} — прогони migrations/urine_name_convention "
             f"либо разберись, каким путём строка вошла")
    return {"plain_urine": len(bad)}


check("конвенция имён мочи (Urine_*)", check_urine_name_convention)


def check_morning_brief_gate_liveness():
    """Гейт анти-повтора жив: context_cards пополняется. Если гейт тихо упал в
    ungated-фолбэк, записи прекратятся → повтор вернётся молча, и никто не заметит.
    Само-активируется по факту непустоты таблицы (гейт был запущен → обязан пополняться),
    НЕ по env чекера — иначе run_checks со своим окружением всегда бы пропускал. Оба тенанта.

    С 23.09 — тревога после ПЕРВОГО пропущенного брифа (решение владельца «общее правило»):
    последняя запись обязана покрывать день последнего планового брифа, время которого берётся
    из того же system_config тенанта, что читает бот (`schedule.morning_brief` в поясе
    `schedule.active_tz`). До 23.09 — литерал «3 дня»."""
    import json as _json
    import sqlite3 as _sq
    import plist_env_liveness as _pl
    from zoneinfo import ZoneInfo
    for path in _tenant_db_paths(include_current=True):
        tag = Path(path).parent.parent.name
        try:
            conn = _sq.connect(f"file:{path}?mode=ro", uri=True)
            row = conn.execute("SELECT MAX(date) FROM context_cards").fetchone()
            cfg = dict(conn.execute(
                "SELECT key, COALESCE(value_json, value_text) FROM system_config "
                "WHERE key IN ('schedule.morning_brief', 'schedule.active_tz')").fetchall())
            conn.close()
        except Exception as e:  # noqa: BLE001 — «нет таблицы» законно, иное — вслух
            if not _absent_table(e):
                warn(f"morning_brief[{tag}]: чтение гейта упало", f"{type(e).__name__}: {str(e)[:80]}")
            continue
        last = row[0] if row else None
        if last is None:
            continue  # context_cards пуста — гейт на этом тенанте не активирован, стеречь нечего
        try:
            bt = _json.loads(cfg.get("schedule.morning_brief") or "")
            tz = ZoneInfo(str(cfg.get("schedule.active_tz") or ""))
            fire = _pl._fire_of({"Hour": int(bt["hour"]), "Minute": int(bt["minute"])},
                                get_now(tz).replace(tzinfo=None))
            missed = date.fromisoformat(last) < fire.date()
        except (ValueError, TypeError, KeyError, LookupError) as e:
            warn(f"morning_brief[{tag}]: живость гейта не судима",
                 f"время брифа/пояс/дата записи не читаются ({type(e).__name__}) — "
                 f"«не знаю» не равно «гейт жив»")
            continue
        if missed:
            # Оговорки про «бриф заблокирован монитором» здесь НЕТ намеренно
            # (12.08): критический вердикт бриф не задерживает, он вешает шапку
            # недостоверности. Значит context_cards пополняется и при критическом
            # падении, и пустота по-прежнему означает ровно то, что написано.
            warn(f"morning_brief[{tag}]: гейт замолк",
                 f"context_cards последняя запись {last}, а плановый бриф был {fire:%d.%m %H:%M} — "
                 f"фолбэк ungated?")
    return _brief_provider_failures_warn()


def _brief_provider_failures_warn():
    """Отказавшие провайдеры карточек брифа — НАРУЖУ (2026-08-11).

    Повод, замеренный: 11.08 в логе бота лежало «assemble_cards recovery
    провайдер упал: AttributeError», карточка восстановления не собиралась, и
    об этом не знал никто. `brief_pipeline` проглатывает отказ ПРАВИЛЬНО — бриф
    не должен падать из-за одной секции, — но единственным следом была строка в
    59-мегабайтном логе.

    Читатель заводится здесь, а не своим датчиком: у живости брифа дом уже есть,
    и второй развёлся бы с ним. Уровень WARN, а не FAIL: бриф вышел, данные в
    нём верны, отсутствует ЧАСТЬ. Это состояние хозяйства, а не ложь о здоровье.

    Артефакт «про сегодня»: если он старше суток, его писал вчерашний прогон, и
    молчать о нём правильно — сегодня отказов не было.
    """
    import json as _json
    path = Path(__file__).parent / "logs" / "brief_provider_failures.json"
    # СПРАШИВАЕМ, А НЕ ЛОВИМ: «файла нет» — законное состояние (брифа с отказами
    # не было), и оно проверяется вопросом. Ловить его исключением значило бы
    # приравнять к нему НЕЧИТАЕМЫЙ артефакт, а это разные вещи: второе — находка.
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = _json.load(fh)
    except (OSError, ValueError) as exc:
        # Не тихий обработчик: артефакт есть и не читается — значит писатель
        # сломан, и «отказов нет» из этого НЕ следует (§14, «датчик мёртв» ≠ «чисто»).
        warn("артефакт отказов провайдеров брифа не читается",
             f"{type(exc).__name__} на {path.name} — писатель brief_pipeline сломан, "
             f"молчание датчика теперь ничего не доказывает")
        return {"provider_failures": "не читается"}
    failures = data.get("failures") or {}
    if not failures or str(data.get("date") or "") != str(today):
        return {"provider_failures": 0}
    warn(f"бриф: {len(failures)} провайдер(ов) карточек упали сегодня",
         ", ".join(f"{k} ({v})" for k, v in sorted(failures.items()))
         + ". Бриф вышел без этих секций; отказ проглочен намеренно, "
           "но молчать о нём нельзя")
    return {"provider_failures": len(failures)}


check("morning_brief: gate liveness", check_morning_brief_gate_liveness)

# ── Пересказ уже сказанного пользователем ─────────────────────────────────
# Инструкция в промпте (patient_context.brief_notes) не доказывает соблюдение.
# Датчик сверяет доставленный текст с входной репликой, а не с другим брифом:
# сравнение двух результатов одного преобразования не обходит его ошибки (§17).
_RETELL_WINDOW_DAYS = 3     # окно реплик владельца назад от даты брифа
_RETELL_THETA_SEED = 0.15   # доля слов первого предложения, пришедших из его реплики


def _retell_tokens(text: str) -> set:
    import re as _re
    return {w for w in _re.findall(r"[а-яёa-z0-9]+", (text or "").lower()) if len(w) > 3}


def retell_score(first_sentence: str, owner_messages: list) -> float:
    """ЧИСТАЯ доля слов первого предложения брифа, пришедших из реплики владельца.

    Containment, а не Jaccard: реплика бывает длинной, предложение коротким, и симметричная
    мера тонула бы в длине реплики. 0.0, если считать не по чему (нет реплик ≥5 слов или
    само предложение короче 5 слов) — молчание, а не ложная тревога."""
    st = _retell_tokens(first_sentence)
    said = [t for t in (_retell_tokens(m) for m in (owner_messages or [])) if len(t) >= 5]
    if len(st) < 5 or not said:
        return 0.0
    return max(len(st & tt) / len(st) for tt in said)


def check_brief_does_not_retell_owner():
    """WARN: утренний бриф открывается пересказом того, что владелец сказал системе сам.

    Бриф может повторять входную реплику, хотя context_cards пополняется
    и проверка живости зелёная. Получателю нужна новая информация,
    поэтому проверяется содержание доставленного текста.

    Смотрим только первое предложение. Границы: пересказ в следующих абзацах
    не виден, а общая лексика домена может давать ложные совпадения.
    Выбор порога требует отдельной калибровки и проверки устойчивости;
    опубликованных наблюдений здесь нет. Порог задаётся конфигом тенанта,
    исходный fallback требует отдельного решения по коду.
    """
    import re as _re
    import sqlite3 as _sq
    import config_db as _cfg
    try:
        theta = float(_cfg.get_config("brief.retell_first_sentence_theta", _RETELL_THETA_SEED))
    except (TypeError, ValueError) as e:
        warn("порог датчика пересказа не читается как число", f"{e} — беру seed-дефолт")
        theta = _RETELL_THETA_SEED
    for path in _tenant_db_paths(include_current=True):
        root = Path(path).parent.parent
        tag = root.name
        briefs = sorted((root / "data" / "reports").glob("2026-*.md"))
        if not briefs or not _re.fullmatch(r"\d{4}-\d{2}-\d{2}", briefs[-1].stem):
            continue  # тенант без доставленных брифов (или файл не датирован) — стеречь нечего
        last = briefs[-1]
        day = date.fromisoformat(last.stem)
        if (today - day).days > 2:
            continue  # бриф протух — это забота другого датчика, не этого
        if not Path(path).exists():
            continue  # база тенанта недоступна — её стережёт check_tenant_dbs_reachable
        try:
            conn = _sq.connect(f"file:{path}?mode=ro", uri=True)
            conn.text_factory = lambda b: b.decode("utf-8", "replace")
            rows = conn.execute(
                "SELECT content FROM conversation_history WHERE role='user' "
                "AND date(created_at) >= date(?, ?) AND date(created_at) < ?",
                (str(day), f"-{_RETELL_WINDOW_DAYS} days", str(day)),
            ).fetchall()
            conn.close()
        except _sq.Error as e:
            warn(f"датчик пересказа не прочитал чат тенанта {tag}", str(e)[:120])
            continue
        first = _re.split(r"(?<=[.!?])\s+",
                          _re.sub(r"\s+", " ", last.read_text().strip()))[0]
        score = retell_score(first, [r[0] for r in rows])
        if score >= theta:
            warn(f"morning_brief[{tag}]: бриф {day} открывается пересказом слов владельца",
                 f"совпадение {score:.2f} ≥ порога {theta} — он это сам и сказал; "
                 f"начало: «{first[:70]}…»")


check("morning_brief: не пересказывает слова владельца", check_brief_does_not_retell_owner)


def check_profile_reconciler_fresh():
    """WARN: patient_profile разошёлся с живым источником — reconciler отстал или мёртв.

    Профиль пациента — снимок фактов, чей предмет живёт ВНЕ файла (вес на весах,
    события в `events`): транзиент, которому §18 предписывает счётчик, а не дату.
    `profile_reconciler` и есть этот счётчик для своих ключей; датчик делает счётчик
    видимым — хранимое разошлось с пересчитанным живым, значит ремонтник не отработал.
    Кейс, породивший нить: ручной вес против расходящегося живого замера ехал в бриф месяц,
    и ни один оракул не краснел (строка «НЕТ» в таблице оракулов снимка profile-staleness).

    Периметр — сами `_RECONCILERS` (§17: вычисляется, не описывается). Алерт называет
    КЛЮЧИ, не значения (значение профиля — health-данные, §16/§19). Studio-only,
    tenant-БД read-only. Свежий замер до следующего прогона ремонтника — самозаживающий
    транзиент (WARN гаснет после его джобы), стойкое расхождение — мёртвый ремонтник.
    Регистрация здесь, а не рядом с launchd-датчиком: `check()` исполняет fn немедленно,
    а `_tenant_db_paths` определён выше по файлу — ранняя регистрация дала бы NameError
    (forward-ref, стережёт check_integrity_registration_order).
    """
    import sqlite3 as _sq
    import profile_reconciler as _pr
    for path in _tenant_db_paths(include_current=True):
        tag = Path(path).parent.parent.name
        if not Path(path).exists():
            continue  # база тенанта недоступна — её стережёт check_tenant_dbs_reachable
        try:
            conn = _sq.connect(f"file:{path}?mode=ro", uri=True)
            diverged = _pr.drift(conn)
            conn.close()
        except _sq.Error as e:
            warn(f"датчик профиля не прочитал базу тенанта {tag}", str(e)[:120])
            continue
        if diverged:
            warn(f"patient_profile[{tag}]: разошёлся с живым источником",
                 f"ключи: {', '.join(diverged)} — profile_reconciler отстал; "
                 f"прогони `python3.11 profile_reconciler.py` и проверь его джобу")


check("patient_profile: не отстаёт от живых источников", check_profile_reconciler_fresh)


def check_time_contract():
    """Контракт единого времени: прод-код читает часы через _time_inject.

    Гибрид-ратчет: новый прямой datetime.now/date.today/utcnow вне seam → FAIL;
    унаследованный (в baseline) → WARN. Плюс прод-liveness: тест-клок не течёт
    в живой прогон (иначе freshness-датчики ослепнут). См. time_contract_sensor.
    """
    import time_contract_sensor as _tc
    _tc.assert_clock_live()
    fails, warns = _tc.check()
    if warns:
        warn("time-contract: унаследованные прямые часы",
             f"{len(warns)} сайтов в обход _time_inject (baseline — разгребаем батчами)")
    assert not fails, (
        "Новый прямой вызов часов в обход _time_inject. Почини одним из двух:\n"
        "  1) через seam: .now(tz)→get_now(tz), .today()→get_today(), "
        ".utcnow()→get_utcnow();\n"
        "  2) если это осознанный штамп/CLI — пометь строку `# time-inject: ok`.\n"
        "  Подробно: docs/explanation/time-inject-contract.md. Сайты: "
        + "; ".join(f"{f['file']}:{f['line']}" for f in fails[:8])
    )
    return f"{len(warns)} унаследованных, 0 новых"


check("контракт единого времени (_time_inject)", check_time_contract)


def check_probe_liveness():
    """§14 для НОСИТЕЛЯ ПРОБ: у датчика, на котором стоят обещания реестра, обязан быть пульс.

    До 2026-07-29 три `holds` опирались на пробы, которые не запускал никто — ни launchd,
    ни suite. Это liveness-дыра в чистом виде: обещание выглядело подтверждённым, а
    подтверждающий механизм между запусками молчал и молчал бы вечно.

    Три РАЗЛИЧИМЫХ состояния, и различие принципиально (решение владельца 2026-07-29):
      · exit=1 — сломан ИНВАРИАНТ. Статус уже упал вычислением (`effective_status`), здесь
        мы делаем это СОБЫТИЕМ: FAIL → ночной монитор → триаж → Telegram.
      · exit=2 — сломана САМА ПРОБА. Об инварианте не говорит ничего, статус не трогаем, но
        молчать нельзя: неисправная оснастка = обещания снова без пульса.
      · артефакта нет / протух — носитель не отработал. Ровно тот случай, ради которого §14
        и написан: тихо умерший guard = ложная безопасность.

    Порог 10 дней: носитель недельный, один пропуск (перезагрузка, отпуск) не повод шуметь,
    два — повод.
    """
    import datetime as _dt

    import intent_registry as _ir

    # На КЛОНЕ носитель не живёт: `logs/` в снимок не синхронизируется, артефакта там нет
    # по построению, и крик был бы ложным. Признак клона берём существующий
    # (`_DEV_CLONE_MARKERS`, введён для изоляции тенантов), а не заводим второй: два дома
    # одного понятия разъезжаются молча.
    _root_name = Path(_ir.ROOT).name
    if any(m in _root_name for m in _DEV_CLONE_MARKERS):
        return f"клон {_root_name} — носитель проб живёт только в каноническом репозитории"
    if skip := _owner_judged_in_container("носитель проб"):
        return skip

    data = _ir.probe_verdicts()
    assert "broken" not in data, (
        f"артефакт вердиктов проб не читается ({data['broken']}) — «датчик мёртв» нельзя "
        f"путать с «нарушений нет»: {_ir.PROBE_VERDICTS_PATH}")

    # Сколько обещаний реально стоит на пробах — считаем, а не предполагаем. Ноль означает,
    # что этой проверке нечего стеречь, и молчать она обязана по другой причине.
    backed = [inv for e in _ir.load_registry() for inv in (e.get("invariants") or [])
              if inv.get("status") == "holds" and _ir.probes_of(inv)]
    if not backed:
        return "обещаний на пробах нет — носителю нечего подтверждать"

    assert data, (
        f"{len(backed)} обещаний реестра стоят на пробах, но носитель не оставил ни одного "
        f"вердикта: {_ir.PROBE_VERDICTS_PATH} отсутствует. Прогони "
        f"scripts/run_probes.sh (Studio) либо понизь статусы честно")

    ran = str(data.get("ran_at") or "")[:10]
    try:
        # `get_today()`, а не `date.today()`: контракт единого времени. Свой же датчик
        # `check_time_contract` поймал прямой вызов часов в этой функции сразу — 2026-07-29,
        # тем же прогоном, каким проверялся носитель. Штатный пример того, что механизм
        # ловит автора правила ровно так же, как всех прочих (§11, догфудинг).
        age = (get_today() - _dt.date.fromisoformat(ran)).days
    except ValueError:
        raise AssertionError(f"вердикты проб без разбираемой даты прогона: ran_at={ran!r}")
    assert age <= _ir.PROBE_VERDICT_TTL_DAYS, (
        f"носитель проб молчит {age} дней (порог {_ir.PROBE_VERDICT_TTL_DAYS}). "
        f"{len(backed)} обещаний реестра всё это время выглядят подтверждёнными, ничем "
        f"их не подтверждая")

    probes = data.get("probes") or {}
    broken_inv = sorted(p for p, v in probes.items() if (v or {}).get("exit") == 1)
    broken_harness = sorted(p for p, v in probes.items() if (v or {}).get("exit") == 2)
    assert not broken_inv, (
        f"ПРОБА КРАСНАЯ — сломан инвариант: {broken_inv}. Статус в реестре уже понижен "
        f"вычислением (effective_status), обещание больше не действует. Разбери прогон в "
        f"logs/, почини либо понизь статус в yaml явно")
    assert not broken_harness, (
        f"сломана сама ОСНАСТКА проб: {broken_harness}. Об инвариантах это не говорит "
        f"ничего — и именно поэтому опасно: обещания снова без пульса, пока проба не чинится")

    no_material = sorted(p for p, v in probes.items() if (v or {}).get("exit") == 3)
    if no_material:
        # exit=3 «СУДИТЬ НЕ НА ЧЕМ» (решение владельца 2026-08-12, вариант A): состояние
        # ДАННЫХ — статус не падает (инвариант не сломан) и это не FAIL «чини оснастку»
        # (оснастка исправна). Но живого переподтверждения нет, и обещание стоит на
        # СТАРЕЮЩЕМ датированном доказательстве. Возраст предъявляется здесь ежедневно
        # WARN-строкой — датчик гаснет сам, как только
        # материал появится и проба снова вынесет вердикт 0/1.
        aged = []
        overdue = False
        for e in _ir.load_registry():
            for inv in (e.get("invariants") or []):
                if inv.get("status") != "holds" or \
                        not (set(_ir.probes_of(inv)) & set(no_material)):
                    continue
                # Возраст — существующим домом `verification_age_days` (битая дата = None
                # = отсутствие подтверждения, урок DG-05), а не своим разбором с тихим
                # except: ратчет тихих обработчиков не растим.
                va = str(inv.get("verified_at") or "")[:10]
                days = _ir.verification_age_days(inv, get_today())
                aged.append(f"{inv.get('id')}: подтверждён {va} ({days}д назад)"
                            if days is not None else
                            f"{inv.get('id')}: даты подтверждения НЕТ")
                if days is None or days > _ir.PROBE_STALE_EVIDENCE_DAYS:
                    overdue = True
        # Две метки — две каденции (2026-08-31): свежее доказательство едет в недельный
        # дайджест (standing), просроченное — ежедневно (decide). Метки различаются
        # ТЕКСТОМ намеренно: класс в triage_agent.WARN_CLASSES — по подстроке.
        if overdue:
            warn(f"Обещание проб на просроченном доказательстве (>{_ir.PROBE_STALE_EVIDENCE_DAYS}д)",
                 f"{', '.join(no_material)} — материала для переподтверждения всё нет; "
                 f"понизь статус честно либо дай пробе материал: {'; '.join(aged) or '—'}")
        else:
            warn("Пробам судить не на чем (состояние данных)",
                 f"{', '.join(no_material)} — живое переподтверждение сейчас невозможно; "
                 f"обещания стоят на датированном доказательстве: {'; '.join(aged) or '—'}")

    return f"вердикты свежие ({age}д), проб {len(probes)}, обещаний на них {len(backed)}"


check("носитель проб жив и зелен (§14)", check_probe_liveness)


SUITE_MARKER_PATH = Path.home() / "health_scripts" / "logs" / "suite_last_run.json"
# В контейнере владельца дерево — /app, а не ~/health_scripts (30.09): отметку туда кладёт
# scripts/test_on_studio.sh, и монитор читает её из logs/ своего дерева.
import os as _os  # noqa: E402
if _os.environ.get("HEALTH_RUNTIME") == "container":
    SUITE_MARKER_PATH = Path(__file__).resolve().parent / "logs" / "suite_last_run.json"
# Имя каталога, из которого исполняется датчик, — модульная константа, а не вычисление
# внутри функции: иначе признак клона непроверяем нигде, кроме ~/health_staging, и тест
# test_clone_is_skipped_and_says_so краснел в scheduled-прогоне из ~/health_scripts
# КАЖДЫЙ ДЕНЬ с 2026-07-30 (31 запись в run_checks.log), а алерт читался как шум.
SUITE_ROOT_NAME = Path(__file__).resolve().parent.name
SUITE_STALE_DAYS = 10  # у носителя проб недельный ритм и порог 10; свой ритм не заводим


def check_suite_freshness():
    """§14 для САМОГО НАГРУЖЕННОГО датчика проекта — набора тестов.

    Замер 2026-07-30: `pytest` не запускает НИЧТО, кроме рук человека. Единственное место
    в `launchd/` и `scripts/`, где встречается слово pytest, — `scripts/test_on_studio.sh`.
    Датчика возраста последнего прогона не было. Следствие, названное прямо: восемь
    обещаний реестра, признанных в тот день «защищёнными», защищены механизмом, чья
    собственная живость не измерялась ничем. Тот же класс, ради которого написан §14,
    ровно на один уровень выше.

    ЧТО ЭТОТ ДАТЧИК ДЕЛАЕТ И ЧЕГО НЕ ДЕЛАЕТ. Он не делает набор свежим — он делает
    НЕСВЕЖЕСТЬ видимой. Календарного прогона тут нет намеренно (Р-1: система, нарочно
    шумящая по расписанию, приучает игнорировать алерт). Возраст предъявляется, решение
    прогнать — человека. Это в точности язык Р-3: у доказательства есть срок годности, и
    молчание больше не читается как «всё хорошо».

    Отметку обнуляет только ПОЛНЫЙ ЗЕЛЁНЫЙ прогон. Частичный (селектор) не обнуляет — он
    говорит о подмножестве; красный не обнуляет — он не подтверждение. Смысл поля
    `ran_at` поэтому строгий: «когда набор последний раз целиком подтвердил код».

    NB 23.09: «pytest запускают только руки» устарело — с тех пор есть ночная задача
    `com.larry.health.test-suite`. Эту отметку она НЕ пишет (пишет только
    `scripts/test_on_studio.sh`), поэтому смерть ночи этот датчик не видит; её судит
    `check_nightly_suite_liveness` ниже.

    WARN, а не FAIL, и это решение, а не робость: «я две недели не гонял тесты» —
    сообщение о знании, не дефект системы. FAIL здесь тренировал бы banner-blindness
    (§13), а рядом уже стоит `check_probe_liveness`, который FAIL'ит по делу.
    """
    import json as _json

    _root_name = SUITE_ROOT_NAME
    if any(m in _root_name for m in _DEV_CLONE_MARKERS):
        return f"клон {_root_name} — `logs/` в снимок не синхронизируется, отметки там нет"

    if not SUITE_MARKER_PATH.exists():
        warn("набор тестов: отметки о прогоне нет вовсе",
             f"{SUITE_MARKER_PATH} отсутствует. Прогони ./scripts/test_on_studio.sh — "
             f"пока отметки нет, «тесты зелёные» ничем не подтверждено")
        return None
    try:
        data = _json.loads(SUITE_MARKER_PATH.read_text(encoding="utf-8"))
        ran = str(data["ran_at"])[:10]
        age = (get_today() - date.fromisoformat(ran)).days
    except (ValueError, KeyError, TypeError, OSError) as e:
        warn("набор тестов: отметка о прогоне не читается",
             f"{SUITE_MARKER_PATH}: {e}. «Датчик мёртв» нельзя путать с «всё хорошо»")
        return None

    where = data.get("commit") or "?"
    if age > SUITE_STALE_DAYS:
        warn(f"набор тестов не гонялся целиком {age} дней (порог {SUITE_STALE_DAYS})",
             f"последний полный зелёный прогон: {ran} на {where}. Обещания реестра всё это "
             f"время опираются на прогон, которого не было. Прогони "
             f"./scripts/test_on_studio.sh")
    return f"полный прогон {age}д назад ({ran}, {where})"


check("набор тестов гонялся недавно (§14 для носителя доказательств)", check_suite_freshness)


def check_nightly_suite_liveness(rows=None, now=None, la_dir=None, host=None):
    """§14 для НОЧНОЙ задачи тестов (`com.larry.health.test-suite`, 00:00).

    Не путать с `check_suite_freshness` выше: тот читает отметку, которую пишет
    деплойный `scripts/test_on_studio.sh`, и отвечает «когда код последний раз целиком
    подтверждён». Этот отвечает «состоялась ли ночь». Замер 23.09 (нить
    nightly-liveness): у ночной задачи не было датчика вовсе. Реестр производителей
    числил её покрытой «dead-man healthchecks.io + morning_test_summary» — неверно
    в обеих половинах: dead-man пингует монитор 07:50 (у него свой pytest), а итог
    `morning_test_summary` пишется изнутри той самой задачи и о её смерти сообщить
    не может. Утренний отчёт при отсутствии итога блок тестов молча пропускает.
    Слепли: красная серия `night_cycle`, повтор flaky, диагнозы, агентские отчёты.

    Порога-литерала нет: итог свеж, если покрывает последний плановый запуск по
    ЖИВОМУ плисту (`morning_test_summary.summary_covers_last_run`). «Не судимо»
    (плиста нет, форма расписания не выводится) — WARN, а не тишина. Studio-only.
    Аргументы — швы для тестов."""
    import socket
    if not infra_config.is_primary(host or None):
        return None  # ночная задача живёт только на Studio
    if rows is None and (skip := _owner_judged_in_container("ночной прогон тестов")):
        return skip
    import morning_test_summary as _mts
    if rows is None:
        import agent_reports_db
        rows = agent_reports_db.get_agent_report("morning_test_summary", n=1)
    latest = rows[0].get("date") if rows else None
    covered = _mts.summary_covers_last_run(latest, now=now, la_dir=la_dir)
    if covered is None:
        warn("ночной прогон тестов: живость не судима",
             f"расписание {_mts.SUITE_LABEL} не выводится из живого плиста (нет плиста "
             f"или форма расписания не поддержана) — «не знаю» не равно «прогон был»")
        return None
    if not covered:
        warn(f"ночной прогон тестов не состоялся (последний итог: {latest or 'нет вовсе'})",
             f"{_mts.SUITE_LABEL} не записал итог после последнего планового запуска. "
             f"Ослепли: красная серия ночного цикла, повтор flaky, диагнозы падений; "
             f"утренний отчёт блок тестов при этом молча пропускает")
        return None
    return f"ночной итог {latest} покрывает последний плановый запуск"


check("ночной прогон тестов состоялся (§14)", check_nightly_suite_liveness)


# ── §14 для ночного цикла (нить night-cycle 2026-08-02) ──────────────────────
# Пороги «N дней тишины» сняты 23.09 (нить schedule-liveness): квитанция обязана покрывать
# последний плановый запуск по живому плисту (_schedule_covers). Было 2 дня у цикла и 1 день
# у колокола — оба числа повторяли ритм плиста вторым домом.
NIGHT_CYCLE_LABEL = "com.larry.health.night-cycle"
DOORBELL_LABEL = "com.larry.health.owner-nag"


def _nc_receipt_path():
    """Квитанция пульса ночного цикла. Тот же env+default, что night_cycle пишет
    (контракт делится через env HEALTH_NIGHT_CYCLE_RECEIPT; расхождение дефолтов
    fail-safe: читатель увидит 'нет отметки' и заwarnит, а не примет мёртвое за живое)."""
    import os
    p = os.environ.get("HEALTH_NIGHT_CYCLE_RECEIPT")
    return Path(p) if p else Path(__file__).parent / "logs" / "night_cycle_last_run.json"


def _nag_receipt_path():
    """Квитанция колокола — тот же env+default, что owner_nag пишет (HEALTH_NAG_RECEIPT)."""
    import os
    p = os.environ.get("HEALTH_NAG_RECEIPT")
    return Path(p) if p else Path(__file__).parent / "logs" / "owner_nag_last_run.json"


def check_night_cycle_liveness():
    """§14 для движка ночного цикла: тихо умерший движок = падения ночью никто не
    расследует, а датчики зелены. Молчание дольше порога — WARN (знание, не дефект)."""
    import json as _json
    if _is_dev_clone(Path(__file__).parent):
        return f"клон — logs не синхронизируются, пропуск"
    if skip := _owner_judged_in_container("ночной цикл"):
        return skip
    p = _nc_receipt_path()
    if not p.exists():
        warn("ночной цикл: отметки о прогоне нет",
             f"{p} отсутствует — движок ни разу не отметился? launchd com.larry.health.night-cycle")
        return None
    try:
        data = _json.loads(p.read_text(encoding="utf-8"))
        age = (get_today() - date.fromisoformat(str(data["ran_at"])[:10])).days
        covered, fire = _schedule_covers(NIGHT_CYCLE_LABEL, str(data["ran_at"]), get_now())
    except (ValueError, KeyError, TypeError, OSError) as e:
        warn("ночной цикл: отметка не читается", f"{p}: {e}. «Датчик мёртв» ≠ «всё хорошо»")
        return None
    if covered is None:
        _schedule_unjudged("ночной цикл", NIGHT_CYCLE_LABEL)
    elif not covered:
        warn(f"ночной цикл не отметился после планового запуска {fire:%d.%m %H:%M} "
             f"(последняя отметка {age}д назад)",
             "движок мёртв? падения ночью не расследуются")
    return f"ночной цикл: прогон {age}д назад (seen={data.get('seen')})"


def check_doorbell_liveness():
    """§14 для КОЛОКОЛА, поведенческий: молчание = launchd owner_nag мёртв (WARN);
    via='none' при состоявшемся звонке = оба канала легли, доставки нет — решения
    владельца не всплывут (FAIL, это не 'нечего слать', а отказ рельсы)."""
    import json as _json
    if _is_dev_clone(Path(__file__).parent):
        return "клон — пропуск"
    if skip := _owner_judged_in_container("колокол"):
        return skip
    p = _nag_receipt_path()
    if not p.exists():
        warn("колокол: отметки о прогоне нет", f"{p} отсутствует — owner_nag ни разу?")
        return None
    try:
        data = _json.loads(p.read_text(encoding="utf-8"))
        age = (get_today() - date.fromisoformat(str(data["ran_at"])[:10])).days
        covered, fire = _schedule_covers(DOORBELL_LABEL, str(data["ran_at"]), get_now())
    except (ValueError, KeyError, TypeError, OSError) as e:
        warn("колокол: отметка не читается", f"{p}: {e}")
        return None
    if covered is None:
        _schedule_unjudged("колокол", DOORBELL_LABEL)
    elif not covered:
        warn(f"колокол не отметился после планового запуска {fire:%d.%m %H:%M} "
             f"(последняя отметка {age}д назад)",
             "launchd owner_nag мёртв? решения владельца звонить некому")
    if data.get("rang") and data.get("via") == "none":
        fail_("колокол звонил, но оба канала легли (via=none)",
              "доставка мертва — решения владельца не всплывут; проверь telegram_token/healthcheck_url")
    return f"колокол: прогон {age}д назад, via={data.get('via')}"


check("ночной цикл жив (§14)", check_night_cycle_liveness)
check("колокол решений жив (§14, поведенческий)", check_doorbell_liveness)


def _has_tables(conn, *names) -> bool:
    """Есть ли у этой БД все названные таблицы. Вопрос вместо `except: continue`.

    Отличие существенное: исключение проглотило бы и «таблицы нет» (законно —
    урезанное окружение), и любую другую поломку доступа. Датчик, молчащий
    по неизвестной причине, — это ложная безопасность (§14).
    """
    have = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    return all(n in have for n in names)

# ── Ратчет предиката (b) границы доменов ───────────────────────────────────
# Ключ — (тенант, источник, материал, имя). Значение — причина, проверяемая
# по бланку. Нормализация в _boundary_names может объединить разные тесты
# и дать ложное срабатывание. Объявление допустимо лишь пока жива причина:
# разведение имён требует удалить исключение, иначе оно скроет другую строку.
def _load_domain_drift_declared() -> dict:
    """Объявленные дрейфы домена — ДАННЫЕ тенанта (документ, даты, значения со страниц бланка),
    поэтому живут в приватной зоне (private/domain_drift_declared.yaml), не в коде
    (чтение не автором 2026-09-26). Нет файла — объявлений нет."""
    import yaml as _y
    p = Path(__file__).resolve().parent / "private" / "domain_drift_declared.yaml"
    if not p.exists():
        return {}
    rows = _y.safe_load(p.read_text(encoding="utf-8")) or []
    return {(r["tenant"], r["source"], r["specimen"], r["test"]): r["why"] for r in rows}


_DOMAIN_DRIFT_DECLARED = _load_domain_drift_declared()


def check_domain_boundary():
    """Граница двух домов лабораторных данных стережётся (работа B, 2026-08-01).

    `lab_results` и `specialized_lab_results` объявили границу в docstring обоих
    писателей и не стерегли её ничем: `lab_promote.BLOOD` определена на строке 42
    и не использована в модуле ни разу. Каждая таблица имеет объявленного
    единственного писателя — Primary-Based Protocol соблюдён на уровне ТАБЛИЦЫ, —
    но владелец решения о принадлежности строки домену не объявлен нигде, поэтому
    решают оба писателя по разошедшимся словарям.

    Два вопроса, два предиката (§17: односторонний предикат защищает от одного
    и молчит о другом):
      (a) пересечение домов пусто — ключ НЕСЁТ материал;
      (b) одно измерение не стоит под двумя датами — оракул вердикта 4, ловит
          расхождение застывшего спец-слоя с каноном.

    Логика чистая и живёт в `lab_canon`; здесь только чтение и доставка. Канал
    доставки выбран осознанно: ночной триаж → Telegram → владелец. У Гейта-2b его нет
    (печатает в stdout ручной команды), и родиться невидимым этот датчик не должен.
    """
    import collections
    import lab_canon as _lc
    problems = []
    seen = {}
    for tag, conn, is_current in _iter_tenant_ro():
        # (ратчет предиката (b) объявлен ниже файла-константой _DOMAIN_DRIFT_DECLARED)
        # C-19 (ложный путь preflight): датчик на ОДНОМ тенанте — не покрытие всех.
        # Сиблингу — только структурные счётчики: монитор целостности не имеет права
        # сам стать cross-tenant утечкой.
        # Отсутствие таблицы спрашивается ВОПРОСОМ, а не ловится исключением:
        # `except: continue` тут неотличим от тишины по любой другой причине.
        if not _has_tables(conn, "lab_results", "specialized_lab_results"):
            continue
        canon = [dict(r) for r in conn.execute(
            "SELECT date, source, specimen, test_name, unit FROM lab_results")]
        spec = [dict(r) for r in conn.execute(
            "SELECT date, source, specimen, analyte_raw, analyte_canonical, "
            "panel_type, unit FROM specialized_lab_results")]
        both = _lc.domain_boundary_violations(canon, spec)
        drift = [d for d in _lc.same_measurement_two_dates(canon, spec)
                 if (tag, d["source"], d["specimen"], d["name"])
                 not in _DOMAIN_DRIFT_DECLARED]
        seen[tag] = {"intersection": len(both), "date_drift": len(drift),
                     "canon": len(canon), "specialized": len(spec)}
        if both:
            det = ""
            if is_current:
                by = collections.Counter(v["panel_type"] or "?" for v in both)
                det = " (%s)" % ", ".join("%s=%d" % kv for kv in by.most_common())
            problems.append(
                "[%s] в ОБОИХ домах разом %d строк(и)%s. Одно измерение с двумя "
                "владельцами: читатель канона видит таксоны как аналиты" % (
                    tag, len(both), det))
        if drift:
            det = ""
            if is_current:
                ex = drift[0]
                det = " напр. %s [%s] спец=%s канон=%s" % (
                    ex["name"], ex["panel_type"], ex["date_specialized"],
                    ",".join(ex["dates_canon"]))
            problems.append(
                "[%s] одно измерение под двумя датами: %d.%s Спец-слой отстал "
                "от staging и знать об этом не может" % (tag, len(drift), det))
    assert not problems, " | ".join(problems)
    return seen


check("граница двух домов лабораторных данных цела", check_domain_boundary)


def check_lab_class_verdicts_complete():
    """Каждый класс, который РЕАЛЬНО есть в данных, имеет вердикт человека.

    Форма решения владельца 2026-07-31: «новый класс без вердикта обязан
    краснеть, а не проваливаться в умолчание». Умолчание в промоуте есть и оно
    безопасное (класс без вердикта в клинический канон не едет), но безопасное
    умолчание без сигнала — это тихо накапливающийся долг: строки копятся в
    спец-слое, и никто не узнаёт, что человека не спросили.

    Считается по СТРОКАМ staging, а не по словарю в коде: словарь скажет о том,
    что мы уже знаем, а данные — о том, что приехало.

    НО ТОЛЬКО ПО ЖИВЫМ СТРОКАМ. Строки с review_status='rejected'
    уже исключены из промоута: повторно требовать решения по ним не нужно.
    Например, отклонённая позиция счёта не создаёт долг клинического канона.

    Форма промаха та же, что ловит §17: подсчёт всех строк без проверки их
    дальнейшего маршрута подменяет вопрос о полноте действующих вердиктов.
    Фильтр здесь тот же, что у промоута (`COALESCE`, потому что NULL != 'rejected'
    в SQLite) — иначе датчик и писатель судили бы разные множества.
    """
    import collections
    import lab_canon as _lc
    import labs_db as _ldb
    problems = []
    out = {}
    for tag, conn, is_current in _iter_tenant_ro():
        if not _has_tables(conn, "lab_results_staging", "lab_domain_verdicts"):
            continue      # таблиц у тенанта нет — стережёт миграция, не этот датчик
        verdicts = _ldb.domain_verdicts(conn=conn)
        rows = conn.execute(
            "SELECT raw_name, panel FROM lab_results_staging "
            "WHERE COALESCE(review_status,'pending') != 'rejected'").fetchall()
        if not verdicts:
            continue
        missing = collections.Counter()
        for raw, panel in rows:
            cls = _lc.classify_row(raw, panel)
            if cls not in verdicts:
                missing[cls] += 1
        out[tag] = {"classes": len(set(_lc.classify_row(r, p) for r, p in rows)),
                    "unjudged": len(missing)}
        if missing:
            det = (": " + ", ".join("%s=%d" % kv for kv in missing.most_common(6))
                   if is_current else "")
            problems.append(
                "[%s] классов без вердикта человека: %d%s — решение о доме "
                "принимает не машина (§13)" % (tag, len(missing), det))
    assert not problems, " | ".join(problems)
    return out


check("у каждого класса лаб-строк есть вердикт о доме", check_lab_class_verdicts_complete)


def check_machine_judge_alive(host_logs=None, now=None):
    """Проверки машины Studio дошли до владельца: квитанция машинного прогона свежа, его находки —
    здесь, под своими метками (нить machine-judge, 03.10).

    Контейнер машины не видит, прогон партнёра — не хозяин машины. Их судья — машинный прогон
    на хосте (scripts/machine_check.sh); здесь он судится по ДВУМ признакам, оба из журналов
    хоста (HEALTH_HOST_LOGS): (1) результат покрывает последний плановый запуск его ЖИВОГО
    плиста — копия лежит рядом с результатом, второго дома ритма нет; (2) находки
    переносятся сюда как есть — так они попадают на стол ночного цикла и ремонта с тем же
    именем и вредом датчика. Молчащий судья — громко (blind_sensor_is_loud)."""
    import os as _os
    if MACHINE_SCOPE:
        return "это и есть машинный прогон"
    if _host_judged_here():
        return "проверки машины судит этот прогон"
    import plist_env_liveness as _pl
    if host_logs is None:
        if not _pl.in_container():
            return f"прогон тенанта на хосте — машину судит {MACHINE_LABEL}; судья проверяется у владельца"
        host_logs = _os.environ.get("HEALTH_HOST_LOGS")
        if not host_logs:
            return "журналов хоста здесь нет (установка без хоста-владельца) — судить машину нечем"
    res = Path(host_logs) / MACHINE_RESULT
    if not res.exists():
        warn("машинный судья не отметился ни разу",
             f"{res} нет — {MACHINE_LABEL} не установлен или не запускался; проверки Studio "
             f"({len(_not_judged_here)}) сейчас не судит никто")
        return None
    try:
        data = json.loads(res.read_text(encoding="utf-8"))
        ran = str(data["ran_at"])
    except (ValueError, KeyError, OSError) as e:
        warn("машинный судья: результат не читается", f"{res}: {e!r} — «датчик мёртв» ≠ «всё хорошо»")
        return None
    covered = _pl.artifact_covers_last_fire(MACHINE_LABEL, ran, now or get_now(),
                                            la_dir=Path(host_logs) / "launchd_live")
    if covered is None:
        warn("машинный судья: живость не судима",
             f"расписание {MACHINE_LABEL} не выводится из копии живого плиста — «не знаю» ≠ «жив»")
    elif not covered:
        warn("машинный судья молчит",
             f"последний результат {ran[:16]}, плановый запуск после него не отметился — "
             f"проверки Studio ({len(_not_judged_here)}) не судятся")
    if data.get("broken"):
        warn("машинный судья: прогон упал", str(data["broken"])[:300])
    so = data.get("sensor_of") or {}
    # Общие датчики (machine=True) идут и здесь, и там: одна находка — одна строка на столе.
    seen = {lab for lab, _ in _warnings} | {lab for lab, _ in _failures}
    for label, detail in data.get("failures") or []:
        if label not in seen:
            _sensor_of.setdefault(label, so.get(label, ""))
            fail_(label, detail)
    for label, detail in data.get("warnings") or []:
        if label not in seen:
            _sensor_of.setdefault(label, so.get(label, ""))
            warn(label, detail)
    return {"ran_at": ran, "fail": len(data.get("failures") or []),
            "warn": len(data.get("warnings") or [])}


check("проверки машины Studio дошли (машинный судья)", check_machine_judge_alive)


# ── Итог ──────────────────────────────────────────────────────────────────────
elapsed = time.time() - start_time

if JSON_OUTPUT:
    output = {
        "pass": PASS, "fail": FAIL, "warn": WARN,
        "elapsed_s": round(elapsed, 2),
        "failures": _failures,
        "code_failures": _code_failures,   # подмножество: ошибки КОДА, не данных (см. check())
        "critical": _critical,             # подмножество: датчики с critical=True (см. check())
        "warnings": _warnings,
        "sensor_of": _sensor_of,           # метка находки → датчик (порядок ночного ремонта)
        "date": str(today),
        # C-19 (нить validation-gate-repair, 2026-08-12): чьи ДАННЫЕ судил прогон.
        # БД = тенант (принцип health_db: tenant_id не хранится); артефакт один на
        # репозиторий, а брифы читают его ОБА тенанта — без провенанса партнёрский
        # бриф судил свою достоверность по вердикту о чужой базе. Читатель
        # (triage_agent.latest_verdict) сверяет этот ключ со СВОИМ DB_PATH.
        "db_path": str(db.DB_PATH),
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
else:
    print(f"\n{'─' * 58}")
    print(f"Итог: {PASS} PASS  {FAIL} FAIL  {WARN} WARN  |  {elapsed:.1f}с")
    if _failures:
        print("\nПадения:")
        for label, detail in _failures:
            print(f"  ❌ {label}")
            print(f"     {detail}")
    if _warnings:
        print("\nПредупреждения:")
        for label, detail in _warnings:
            print(f"  ⚠️  {label}: {detail}")
    if _critical:
        print("\nКРИТИЧЕСКИЕ (утренний бриф будет заблокирован):")
        for label, detail in _critical:
            print(f"  🛑 {label}")
            print(f"     {detail}")
    if FAIL == 0:
        print("\n✅ Система целостна.")
    else:
        print(f"\n⛔ {FAIL} проблем целостности.")

# Код 2 — есть критическое падение; 1 — обычные FAIL. Оба ставятся ПОСЛЕ полного прогона:
# артефакт уже напечатан, остальные датчики уже отработали (см. комментарий у _critical).
if not WARN_ONLY and __name__ == "__main__":
    if _critical:
        sys.exit(2)
    if FAIL > 0:
        sys.exit(1)
