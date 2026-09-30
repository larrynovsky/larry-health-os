#!/usr/bin/env python3.11
"""producer_registry.py — реестр-ратчет периодических производителей (context_gate_orphan).

Публичное: audit_producers(scheduled_labels, judged) → list[str] находок (пусто = каждая
scheduled-задача классифицирована); schedule_judged_labels() — покрытие, вычисленное из
кода integrity_tests (с 23.09); collect_producer_findings() — прод-вход.

Зачем (BL-DOCAGENT-DEAD-1): задача, гейтнутая по хосту/флагу так, что не бежит
нигде, молчит невидимо — «ничего не произошло» = «всё хорошо». Точечных
freshness-датчиков россыпь, но НЕТ гарантии, что каждый scheduled-производитель
покрыт. Ратчет закрывает класс: любая scheduled launchd-задача обязана быть либо
в MONITORED (назван датчик факта исполнения), либо в EXEMPT (обоснование, почему
её молчание не важно). Новая задача вне обоих → находка. Симметрично: ключ
реестра без реального plist → находка (реестр протух).

Enforcement правила feedback_context_gate_orphan: пункт 2 (датчик факта
исполнения) — здесь как ратчет покрытия, не отдельный датчик на каждую.

ГРАНИЦА, НАЗВАННАЯ ВСЛУХ (13.09). Ратчет покрывает ТОЛЬКО scheduled-задачи —
те, у кого есть расписание (StartCalendarInterval/StartInterval). KeepAlive-демоны
в него не входят по замыслу: у них нет «должен был побежать в 07:50», а значит нет
и события, чьё отсутствие можно заметить. Их молчание ловится не здесь, а двумя
другими датчиками: check_daemons_alive (есть ли PID) и stderr_watch (пишет ли он
ошибки в свой лог). Записано, потому что 13.09 слепое пятно KeepAlive-демонов
закрыли датчиком stderr, а сама перепись об этом не знала — и «покрыто ли»
приходилось выяснять чтением трёх файлов вместо одного.

Потребитель: integrity_tests §[12]. Тесты: tests/unit/test_producer_registry.py.
Burn-in: находки — WARN (не гейтят утренний отчёт), сгруппированы в одну строку.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

# ── MONITORED: задача → чем покрыт факт её исполнения ─────────────────────────
# Значение — имя датчика/механизма (для аудита; не парсится, но обязано быть
# правдой, сверяется глазами при ревизии allowlist).
MONITORED: dict[str, str] = {
    # ⚰️ 2026-09-23 (нить producer-census): longitudinal, night-cycle и owner-nag отсюда
    # УШЛИ — их покрытие ВЫЧИСЛЯЕТСЯ из кода (schedule_judged_labels ниже): проверка,
    # которая зовёт `_schedule_covers(<метка>)` и зарегистрирована через check(), и есть
    # датчик факта исполнения. Руками здесь — только то, что вычислить нельзя.
    "com.larry.health.assessment-scheduler": "check_assessments_freshness",
    "com.larry.health.backup": "check_backup_freshness (SLA 25ч)",
    # ⚰ 26.09: com.larry.health.daily снят (iCloud-путь Apple Health, каталог пуст с 06.07);
    # Apple Health — REST /hae/ingest (KeepAlive-дашборд), живость приёма — check_hae_arrivals_have_owner.
    "com.larry.health.import-poll": "check_data_freshness (тот же канал биометрии)",
    "com.larry.health.oura-import": "check_data_freshness",
    "com.larry.health.literature-read": "check_literature_freshness",
    # 27.09: кран партнёру включён решением владельца; чтение судится косвенно, как у владельца
    # (свежесть поиска), — смерть одного reader+curator при живом поиске этим не видна.
    "com.larry.health.literature-read.partner": "check_literature_freshness_partner (косвенно: свежесть поиска партнёра)",
    "com.larry.health.survivorship": "check_survivorship_agent_freshness (литерал 18д: ритм «чётные недели» живёт в скрипте, не в плисте — BL-THRESHOLDS-1)",
    "com.larry.health.night-repair": "night_repair --gate: last_run.json старше 36 ч — строка каждой сессии при коммите в main (pre-commit MacBook; контейнерный монитор хоста не видит)",
    "com.larry.health.pipaudit": "check_security_sensors → pip_audit staleness (>8д)",
    "com.larry.health.uncommitted-watchdog": "check_watchdog_liveness",
    # 28.09: понедельничная сводка оператору; её квитанцию судит рельса доставки (п.3 —
    # доказанная доставка не старше последнего понедельника 09:10, owner_weekly.last_scheduled_at).
    "com.larry.health.owner-weekly": "check_triage_delivery_liveness п.3 (квитанция owner_weekly)",
    "com.larry.health.integrity-check": "dead-man healthchecks.io (сам оркестратор проверок)",
    # 2026-09-23: прежнее значение «dead-man healthchecks.io + morning_test_summary»
    # было ложным в обеих половинах — dead-man пингует integrity-check (свой pytest
    # в 07:50), а morning_test_summary пишется изнутри этой же задачи. Датчика не
    # было; заведён в нити nightly-liveness.
    "com.larry.health.test-suite": "check_nightly_suite_liveness (итог ночи покрывает последний плановый запуск по живому плисту)",
    "com.larry.health.calendar-sync": "check_calendar_freshness (cache mtime vs fetcher CACHE_ERROR_AGE_H)",
    # ── 2026-08-07: реестр ОТСТАЛ, а не датчиков не было ────────────────────────
    # Все четыре датчика существовали в integrity_tests и на момент находки уже
    # работали; ратчет кричал «нет датчика факта исполнения» про задачи, которые
    # покрыты. Класс — census-асимметрия в сторону реестра: MONITORED пополняли
    # руками, а датчики заводили отдельными нитями, и сверки не было ни одной.
    "com.larry.health.probes": "check_probe_liveness",
    "com.larry.health.triage": "check_triage_delivery_liveness (маркер triage_done + квитанция канала)",
    # ── Покрытие задач каждого тенанта ────────────────────────────────────────
    # Освобождение «тенант пуст до активации» нельзя считать бессрочным:
    # поступление данных требует контроля свежести независимо от ручного статуса.
    # scan_calendar_staleness обходит всех тенантов; check_data_freshness
    # однотенантный, поэтому биометрии нужен check_tenant_biometrics_freshness.
    # ── 2026-09-01: агент ротации логов заведён вместе со своим датчиком ────────
    # Ратчет поймал его в тот же вечер, когда агент встал: задача была, датчика не
    # было. Артефакт у неё датируемый (stdout-квитанция), поэтому не EXEMPT.
    "com.larry.health.logrotate": "check_logrotate_liveness (свежесть квитанции logs/logrotate.log + хвост `selftest ok`)",
    "com.larry.health.calendar-sync.partner": "check_calendar_freshness (per-tenant: scan_calendar_staleness по всем тенантам)",
    "com.larry.health.oura-import.partner": "check_tenant_biometrics_freshness (per-tenant HRV, 2026-08-07)",
    # ── 2026-09-07: снова census-асимметрия, тот же класс, что 2026-08-07 выше ──
    # Ратчет кричал «нет датчика факта исполнения» про задачу, покрытую с 05.09:
    # датчик завели вместе с нитью weekly-digest, а в реестр строку не внесли.
    # Косвенность названа вслух: судится ДОСТАВКА, не запуск, и задержка до 2 суток
    # (генератор сб 22:00 → сторож пн 07:50). Этого достаточно: не побежал генератор →
    # нет файла недели → первый assert роняет FAIL. Побежал, но выдал пустой/
    # заблокированный текст → второй assert. Прямой пульс генератора остаётся хвостом
    # BL-DIGEST-2. Решение владельца 2026-09-07.
    "com.larry.health.weekly-digest": "check_weekly_digest_delivered (пн: артефакт недели + last_sent_week у каждого тенанта; косвенно, лаг ≤2д)",
}

# ── EXEMPT: задача → почему её молчание не требует датчика ────────────────────
EXEMPT: dict[str, str] = {
    "com.larry.healthbot.morningwake": "caffeinate-будильник, не производит артефакт",
    # ⚰️ 2026-08-31: com.larry.healthbot.checkinwake снят из EXEMPT — задача удалена
    # 2026-08-17 вместе с автозапуском вечернего чекина (CHANGELOG 13.5), ключ пережил
    # свой plist на две недели и ратчет census-симметрии кричал каждую ночь.
    "com.larry.health.test-api-report": "мета-отчёт API-трат; молчание не влияет на здоровье",
    # ⚰️ 2026-08-07: обе партнёрские записи переехали в MONITORED — условие снятия
    # («при загрузке данных партнёра») выполнилось, замер см. там. Освобождение,
    # пережившее свой предмет, — это ложная безопасность, а не мелочь реестра.
    # discovery НОВЫХ PGS-моделей из каталога не критичен; существующие PRS и их
    # привязка к геному покрыты check_prs_genome_sync.
    "com.larry.health.pgs-discovery": "существующие PRS покрыты check_prs_genome_sync; новые модели не срочны",
    # идемпотентный нормализатор профиля перед генерацией; датируемого артефакта не
    # производит, эффект виден в свежести downstream (конституции/GP-отчёты).
    "com.larry.health.profile-reconciler": "идемпотентный; эффект в свежести downstream, своего артефакта нет",
    # обратный канал Reminders→SQLite (статусы задач); GP-задачи создаются и
    # дублируются в Telegram — молчание не теряет медданные.
    "com.larry.health.reminders-sync": "вторичный обратный канал; медданные не теряются при молчании",
    # upstream-tracker промптов (huifer/WellAlly); дрейф редкий и не срочный,
    # ловится ручной ревизией + собственным алертом check_wellally_updates.
    "com.larry.health.wellally-check": "upstream-tracker промптов; дрейф не срочен, есть свой алерт",
    # одноразовая календарная (Year=2026 Month=6 Day=15 — дата прошла); не
    # периодический производитель.
}

# Префикс, по которому распознаём наши scheduled-джобы (партнёрский тенант тоже).
_LABEL_PREFIX = "com.larry.health"


def _scan_scheduled_labels(launchagents_dir: Path) -> list[str]:
    """IO: labels наших plist с StartCalendarInterval/StartInterval (только Studio).

    Отделено от чистой логики ради тестируемости audit_producers.
    """
    out = []
    for p in sorted(launchagents_dir.glob(f"{_LABEL_PREFIX}*.plist")):
        try:
            text = p.read_text(errors="ignore")
        except OSError:
            continue
        if "StartCalendarInterval" not in text and "StartInterval" not in text:
            continue  # KeepAlive-демоны — не scheduled-производители
        m = re.search(r"<key>Label</key>\s*<string>([^<]+)</string>", text)
        if m:
            out.append(m.group(1))
    return out


def schedule_judged_labels(src_path: Path | None = None) -> dict[str, list[str]]:
    """Метка задачи → зарегистрированные проверки, судящие её живость по живому плисту.

    ЗАЧЕМ (23.09). Дважды — 07.08 и 07.09 — ратчет кричал «нет датчика факта исполнения»
    про задачи, которые были покрыты: датчик заводили нитью, а строку в MONITORED вносить
    забывали (census-асимметрия в сторону реестра). Для датчиков, судящих живость через
    `_schedule_covers(<метка>, ...)`, факт покрытия виден в коде — значит, его не пишут
    руками, а читают из кода.

    Читается ИСХОДНИК integrity_tests.py (AST), модуль не импортируется: импорт исполняет
    весь ночной монитор. Считается только проверка, зарегистрированная через check(): функция,
    которую никто не зовёт, ничего не покрывает. Метка — строковый литерал или имя
    модульной строковой константы; иное (выражение) не угадывается и не засчитывается."""
    path = src_path or Path(__file__).with_name("integrity_tests.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    consts = {t.id: n.value.value for n in tree.body if isinstance(n, ast.Assign)
              for t in n.targets if isinstance(t, ast.Name)
              and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str)}
    registered = {c.args[1].id for c in ast.walk(tree)
                  if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
                  and c.func.id == "check" and len(c.args) >= 2
                  and isinstance(c.args[1], ast.Name)}
    out: dict[str, list[str]] = {}
    for fn in tree.body:
        if not isinstance(fn, ast.FunctionDef) or fn.name not in registered:
            continue
        for c in ast.walk(fn):
            if (isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
                    and c.func.id == "_schedule_covers" and c.args):
                a = c.args[0]
                label = consts.get(a.id) if isinstance(a, ast.Name) else getattr(a, "value", None)
                if isinstance(label, str) and fn.name not in out.get(label, []):
                    out.setdefault(label, []).append(fn.name)
    return out


def audit_producers(scheduled_labels: list[str],
                    judged: dict[str, list[str]] | None = None) -> list[str]:
    """Ратчет покрытия. Пусто = каждый scheduled-производитель классифицирован.

    Находки двух видов:
      1. scheduled-задача не в MONITORED и не в EXEMPT → нужен датчик или exempt;
      2. ключ MONITORED/EXEMPT без реального plist → реестр протух (census-симметрия);
      3. задача покрыта вычисленно (`judged`) И записана в MONITORED руками → два дома одного
         факта, ручной снова разъедется с кодом.
    Аргументы — для инъекции в тестах; в проде через _scan_scheduled_labels и
    schedule_judged_labels.
    """
    labels = set(scheduled_labels)
    judged = judged or {}
    classified = set(MONITORED) | set(EXEMPT) | set(judged)
    found: list[str] = []

    unclassified = sorted(labels - classified)
    if unclassified:
        found.append(
            f"{len(unclassified)} scheduled-задач без датчика факта исполнения "
            f"и без EXEMPT-обоснования: {', '.join(unclassified)} "
            f"(context_gate_orphan: реши датчик или exempt)"
        )

    stale = sorted(classified - labels)
    if stale:
        found.append(
            f"реестр производителей протух — ключи без plist: {', '.join(stale)} "
            f"(задача снята/переименована? почисти MONITORED/EXEMPT)"
        )

    twice = sorted(set(MONITORED) & set(judged))
    if twice:
        found.append(
            f"реестр производителей: покрытие вычисляется из кода, но записано и руками — "
            f"{', '.join(twice)} (убери строку из MONITORED: второй дом разъедется)"
        )
    return found


def collect_producer_findings(launchagents_dir: Path | None = None) -> list[str]:
    """Прод-вход: скан + аудит. На не-Studio (нет каталога) — пусто (не наш хост)."""
    import plist_env_liveness
    d = launchagents_dir or plist_env_liveness.agents_dir()
    if not d.is_dir():
        return []
    return audit_producers(_scan_scheduled_labels(d), schedule_judged_labels())
