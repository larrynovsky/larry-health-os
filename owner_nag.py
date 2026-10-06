"""owner_nag.py — консолидированный колокол: зовёт владельца к консоли, когда
есть нерешённые гейты, и повторяет, пока решение не ЗАПИСАНО.

Раз в вызов смотрит открытые гейты владельца (owner_gates: parked_decisions.list_open()
без инженерной очереди dev_fix); если они есть И
сейчас окно 08–20 (местное время дома) — шлёт ОДНО служебное сообщение оператору
(notify_operator), консолидируя все гейты в один звонок. Кнопок нет: действие —
в сессии Claude, не в боте. Звонок раз в день (28.09); запуски launchd после
первого звонка пишут только пульс. ОКНО
проверяется здесь (решение владельца Дом А: окно тестируемо кодом, не только
плистом).

НЕ хранит решения (это parked_decisions), НЕ расследует, НЕ формулирует доменных
решений. Мед-данные тенанта в текст не попадают: канал notify_operator ведёт к
владельцу, а гейты ночного цикла — ops-meta (план/этап/вопрос владельцу), не health.

Пульс §14: каждый вызов пишет квитанцию (ran_at, rang, via, open_count) в
logs/owner_nag_last_run.json. via='none' = оба канала легли → check_doorbell_
liveness покраснеет. Пульс доказывает «запустился И канал ответил», не «оператор
прочитал» — последнее машинно недоступно.
"""
from __future__ import annotations

import json
import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import parked_decisions
import i18n
import region_pack
from _time_inject import get_now

# notify импортируется ЛЕНИВО внутри run(): его импорт-time `secrets_dir()` требует
# конфигурации секретов, а owner_nag должен импортироваться и без неё (инспекция, тест).

HOME_TZ = ZoneInfo(region_pack.value("timezone", "UTC"))   # таймзона дома — пакет региона (pub-prep)
WINDOW_START = 8   # 08:00 — раньше не будим
WINDOW_END = 20    # 20:00 — последний звонок; 20 включительно, ночью тишина


def _receipt_path() -> Path:
    """Квитанция пульса. env HEALTH_NAG_RECEIPT переопределяет (тесты)."""
    p = os.environ.get("HEALTH_NAG_RECEIPT")
    if p:
        return Path(p)
    return Path(__file__).parent / "logs" / "owner_nag_last_run.json"



ENGINEERING_KIND = "dev_fix"
"""Карточка инженерной очереди. Колокол её НЕ считает (23.09, решение владельца «да»):
оракул у неё не владелец, а звонок «на столе N» зовёт именно его. Находка, из которой
она выросла, до владельца всё равно доезжает — утренним триажем (decide ежедневно,
fix — понедельничным дайджестом); очередь целиком с возрастом старшей — строкой того же
дайджеста (`engineering_queue_line` ниже, зовёт triage_agent). До 23.09 колокол звонил и по ней:
«anyio 4.13.0» стояло в одном счёте с вопросами о здоровье."""


def owner_gates(today: date) -> list[dict]:
    """Открытые карточки, решение по которым — за владельцем (всё, кроме инженерных)."""
    return [g for g in parked_decisions.list_open(today) if g.get("kind") != ENGINEERING_KIND]


def _age_days(created: Optional[str], today: date) -> Optional[int]:
    """Возраст карточки в днях. None — дата нечитаема (не роняем звонок из-за неё)."""
    try:
        return (today - date.fromisoformat(str(created)[:10])).days
    except (TypeError, ValueError):
        return None


def _plural_days(n: int) -> str:
    from _fmt_helpers import fmt_count
    return fmt_count(n, "days", "ru")


def engineering_queue_line(gates, today):
    """Чистая: открытые карточки → строка понедельничного дайджеста (triage_agent) об
    инженерной очереди или None.

    ЗАЧЕМ (23.09). Колокол перестал считать карточки dev_fix — звонок «на столе N» зовёт
    владельца, а оракул у них не он. Но без читателя очередь стала бы ящиком в стол:
    кроме колокола её не читал никто. Каждая находка в ней и так доезжает триажем; чего
    нет больше нигде — что они ВИСЯТ и сколько. Поэтому раз в неделю — счёт и возраст
    старшей, и слова, которыми отдать её сессии. Сторожа возраста нет: за жизнь стола
    (08.08–23.09) карточек dev_fix было четыре, и строки в дайджесте для такого потока
    хватает. Станет очередь расти — строка это покажет числом, тогда и строить."""
    dev = [g for g in gates if g.get("kind") == ENGINEERING_KIND]
    if not dev:
        return None
    ages = [a for a in (_age_days(g.get("created"), today) for g in dev)
            if a is not None]
    oldest = i18n.t("owner.weekly.queue_age", days=_plural_days(max(ages))) if ages else ""
    return i18n.t("owner.weekly.queue", count=len(dev), age=oldest)


_FRAME = re.compile(
    r"^\W*(?:это\s+)?(?:про|касается)\s+(?:систем|работ|твоё|твое|твою|твоей|здоров|чь)"
    r"|^\W*срочност|^\W*(?:не\s*)?срочно\b|^\W*urgency|^\W*(?:not\s+)?urgent\b"
    r"|^\W*(?:this is\s+)?about\s+(?:the system|your health)", re.IGNORECASE)
"""Фраза-рамка карточки («Про систему, не срочно.», «Это про работу системы…»)."""

_THREAD_PREFIX = "thread-stalled:"
_MAX_LINES = 7          # строк-вопросов в звонке; остальные — числом, их покажет сессия
_LINE_MAX = 180


def _thread_title(slug: str) -> Optional[str]:
    """Человеческое название нити из docs/handoff/INDEX.md (колонка «Название»), первая фраза."""
    try:
        idx = (Path(__file__).parent / "docs" / "handoff" / "INDEX.md").read_text(encoding="utf-8")
    except OSError:
        return None
    for line in idx.splitlines():
        cells = [c.strip() for c in line.split("|")]
        if len(cells) > 3 and cells[1].strip("`") == slug:
            return re.split(r"[.;:](?:\s|$)", cells[2], maxsplit=1)[0][:120] or None
    return None


def _question(g: dict, today: date) -> str:
    """Одна строка: что решить, простыми словами (решение владельца 28.09).

    Холодное чтение показало: «на столе 3» без предметов не говорит, про что решения, а
    инженерная сводка карточки («пишет в домен владельца ['lab_domain_verdicts']») не даёт
    решить. Колокол теперь называет каждое решение; полную карточку с вариантами и ценой
    каждого показывает сессия по фразе «разбери решения»."""
    gid = str(g.get("id") or "")
    if gid.startswith(_THREAD_PREFIX):
        slug = gid[len(_THREAD_PREFIX):]
        title = _thread_title(slug) or slug
        age = _age_days(g.get("created"), today)
        stood = f" стоит уже {_plural_days(age)}" if age else " стоит"
        return f"Работа «{title}»{stood}. Доделать или закрыть?"
    human = _HUMAN_BY_ID.get(gid) or next(
        (q for p, q in _HUMAN_BY_PREFIX if gid.startswith(p)), None)
    if human:
        return human
    return _headline(g.get("summary") or "") or gid


def _headline(summary: str) -> str:
    """Строка звонка из карточки: первый абзац; если он длинный — первая фраза и ВОПРОС.

    Холодное чтение 28.09: резка по символу давала «Это может быть реальным сдвигом …» и
    «слишком близко к гр…» — главное сообщение дня не говорило, о чём решение. Вопрос карточки
    стоит в конце первого абзаца («Что делать?») — его терять нельзя, середину можно."""
    para = summary.strip().splitlines()[0].strip() if summary.strip() else ""
    # 30.09: рамка «про систему / про здоровье, срочность» не предмет. Звонок 30.09 из
    # двух строк состоял только из рамок — владелец не понял, о чём решения. Рамку снимаем,
    # строкой звонка становится то, что за ней; нет ничего за ней — следующий абзац.
    sentences = [x.strip() for x in re.split(r"(?<=[.!?…;])\s+", para) if x.strip()]
    while sentences and _FRAME.match(sentences[0]):
        sentences.pop(0)
    if sentences:
        para = " ".join(sentences)
    else:
        rest = [p.strip() for p in summary.strip().split("\n\n")[1:] if p.strip()]
        para = rest[0].splitlines()[0].strip() if rest else para
    if len(para) <= _LINE_MAX:
        return para
    parts = [x.strip() for x in re.split(r"(?<=[.!?…])\s+", para) if x.strip()]
    if len(parts) >= 2:
        head, ask = parts[0], parts[-1]
        if len(head) + len(ask) + 3 <= _LINE_MAX * 2:
            return f"{head} … {ask}"
    cut = para[:_LINE_MAX].rsplit(" ", 1)[0]
    return cut + " …"


# Карточки с инженерной сводкой, у которых есть постоянный id: вопрос человеческими словами.
_HUMAN_BY_ID: dict[str, str] = {}
_HUMAN_BY_PREFIX = (
    ("vg:heldout",
     "Созрела проверка найденных связей на новых данных. Решить, что делать со связями, "
     "которые её прошли."),
)


def _compose(gates: list[dict], today: Optional[date] = None) -> str:
    """Один звонок в день: сколько решений ждут, каждое — строкой, и как ответить.

    Решение владельца 28.09 (холодное чтение сообщений): звонок раз в день, с самими
    вопросами простыми словами; без путей к файлам и слова «консоль». Действие —
    одна фраза для Claude: она открывает полные карточки с вариантами и ценой.
    """
    today = today or get_now(HOME_TZ).date()
    lines = [f"🔔 Ждут твоего решения: {len(gates)}"]
    for i, g in enumerate(gates[:_MAX_LINES], 1):
        lines.append(f"{i}. {_question(g, today)}")
    if len(gates) > _MAX_LINES:
        lines.append(f"…и ещё {len(gates) - _MAX_LINES}")
    due = sorted((g["auto_after"], g.get("default") or "")
                 for g in gates if g.get("auto_after") and g.get("default"))
    if due:
        d = date.fromisoformat(due[0][0])
        lines.append(f"Если промолчишь — {d.strftime('%d.%m')} одно решится само: «{due[0][1]}».")
    lines.append("")
    lines.append("Чтобы решить, напиши Claude в проекте health: «разбери решения». "
                 "Он покажет каждое с вариантами.")
    return "\n".join(lines)


def _memory_lost_text(now_ids: int, known: int) -> str:
    return (f"⚠️ Стол решений потерял память: карточек {now_ids}, а было {known}. "
            "Вопросы со стола не шлю — среди них были бы уже решённые тобой. "
            "Ночной цикл остановлен до починки. Напиши Claude в проекте health: "
            "«стол решений потерял память».")


def _rang_today(now: datetime) -> bool:
    """Звонок сегодня уже был — раз в день достаточно (решение владельца 28.09)."""
    try:
        last = json.loads(_receipt_path().read_text(encoding="utf-8"))
        return bool(last.get("rang")) and \
            datetime.fromisoformat(last["ran_at"]).date() == now.date()
    except (OSError, ValueError, KeyError, TypeError):
        return False


def _write_receipt(now: datetime, rang: bool, via: Optional[str], open_count: int) -> None:
    path = _receipt_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(
        {"ran_at": now.isoformat(), "rang": rang, "via": via, "open_count": open_count},
        ensure_ascii=False, indent=2), encoding="utf-8")


def run(now: Optional[datetime] = None) -> dict:
    """Один проход колокола. now инъектируется (Дом А: окно тестируемо кодом).

    Звонит ТОЛЬКО если есть открытые гейты И сейчас окно 08–20 по местному времени дома. Всегда
    пишет квитанцию пульса — даже когда молчит (иначе «не звонил» и «сдох» не
    различить, §14). Возвращает квитанцию."""
    now = now or get_now(HOME_TZ)
    gates = owner_gates(now.date())
    in_window = WINDOW_START <= now.hour <= WINDOW_END
    rang, via = False, None
    if _cycle_pending(now):
        # Звонок ждёт цикл: цикл в конце позвонит сам (ring_after_cycle). Квитанция
        # пишется всё равно — «не звонил» и «сдох» различимы (§14).
        receipt = {"ran_at": now.isoformat(), "rang": False, "via": None,
                   "open_count": len(gates), "waiting_for": "night_cycle"}
        _write_receipt(now, False, None, len(gates))
        return receipt
    lost = parked_decisions.memory_lost()
    if lost and in_window and not _rang_today(now):
        # Стол забыл карточки (нить desk-guard, 30.09): список со стола был бы ложью —
        # решённое читалось бы как новое. Одна строка вместо списка, раз в день; гейтов
        # может не быть вовсе (пустой стол после переезда) — звоним всё равно.
        import notify
        via = notify.notify_operator(_memory_lost_text(*lost))
        _write_receipt(now, True, via, len(gates))
        return {"ran_at": now.isoformat(), "rang": True, "via": via,
                "open_count": len(gates), "desk_memory_lost": list(lost)}
    if gates and in_window and _rang_today(now):
        receipt = {"ran_at": now.isoformat(), "rang": False, "via": None,
                   "open_count": len(gates), "skipped": "уже звонил сегодня"}
        # квитанцию пульса пишем, но «rang» сегодняшнего звонка не затираем
        _write_receipt(now, True, "earlier_today", len(gates))
        return receipt
    if gates and in_window:
        import notify
        via = notify.notify_operator(_compose(gates, now.date()))
        rang = True
    receipt = {"ran_at": now.isoformat(), "rang": rang, "via": via, "open_count": len(gates)}
    _write_receipt(now, rang, via, len(gates))
    return receipt


NIGHT_CYCLE_LABEL = "com.larry.health.night-cycle"
OWNER_NAG_LABEL = "com.larry.health.owner-nag"


def _cycle_receipt_at():
    """Момент последней квитанции ночного цикла или None (нет/нечитаема)."""
    p = os.environ.get("HEALTH_NIGHT_CYCLE_RECEIPT")
    path = Path(p) if p else Path(__file__).parent / "logs" / "night_cycle_last_run.json"
    try:
        return datetime.fromisoformat(json.loads(path.read_text(encoding="utf-8"))["ran_at"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _cycle_pending(now: datetime) -> bool:
    """Звонок этого запуска совпал с запуском ночного цикла, а цикл ещё не отчитался.

    ЗАЧЕМ (23.09). Колокол и цикл стартуют в 08:00 оба; колокол успевал раньше и
    насчитал 14 карточек, а через 40 секунд цикл снял три — владелец получил число,
    устаревшее до прочтения. Теперь в эту минуту колокол ждёт, а звонит цикл,
    закончив (`ring_after_cycle`).

    Ждёт ТОЛЬКО тот запуск колокола, чей плановый момент не позже запуска цикла: в
    11:00 мёртвый цикл звонка не глушит. Расписания обоих — из живых плистов, без
    литералов; не выводятся — не ждём (лучше устаревшее число, чем тишина)."""
    import plist_env_liveness as pl
    cycle_fire = pl.last_scheduled_fire(NIGHT_CYCLE_LABEL, now)
    nag_fire = pl.last_scheduled_fire(OWNER_NAG_LABEL, now)
    if cycle_fire is None or nag_fire is None or nag_fire > cycle_fire:
        return False
    if cycle_fire.date() != pl._naive_local(now).date():
        return False
    done = _cycle_receipt_at()
    return done is None or pl._naive_local(done) < cycle_fire


def ring_after_cycle(now: Optional[datetime] = None) -> dict:
    """Позвонить после цикла — если колокол после начала цикла ещё не звонил сам."""
    import plist_env_liveness as pl
    cycle_fire = pl.last_scheduled_fire(NIGHT_CYCLE_LABEL, now or get_now())
    try:
        last = json.loads(_receipt_path().read_text(encoding="utf-8"))
        rang_after = bool(last.get("rang")) and cycle_fire is not None and \
            pl._naive_local(datetime.fromisoformat(last["ran_at"])) >= cycle_fire
    except (OSError, ValueError, KeyError, TypeError):
        rang_after = False
    if rang_after:
        return {"rang": False, "reason": "колокол уже звонил после начала цикла"}
    return run(now)


if __name__ == "__main__":
    # ТОЛЬКО один боевой прогон: это точка входа launchd-джобы. Никаких повторных
    # run(now=...) здесь — run() ПИШЕТ квитанцию, и второй вызов с иной датой затёр бы
    # боевой пульс (баг пойман живым smoke 2026-08-03: селф-тест с 2026-01-01 отравлял
    # квитанцию на 214д, датчик колокола ложно кричал «мёртв»). Окно вне-окна проверяет
    # tests/unit/test_owner_nag.py::test_silent_outside_window, не __main__.
    print(json.dumps(run(), ensure_ascii=False))
