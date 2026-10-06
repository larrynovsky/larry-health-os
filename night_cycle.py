# INTENT: night_decision_loop
"""night_cycle.py — оркестратор ночного цикла: связывает расследование, парковку
и пульс. Читает падения (integrity_latest), судит каждое через night_investigator,
маршрутизирует:
  transient      → в лог, гейт НЕ создаётся (само-зажило; рецидив всплывёт);
  dev_fix        → parked_decisions.park(kind='dev_fix') [Этап 1: парк для консоли;
                   подготовка патча + ревью §17 — следующий слой конвейера];
  owner_decision → parked_decisions.park(kind='owner_decision') с park_reason.

Зачем цикл устроен так и какие развилки были: docs/explanation/night_cycle.md.

Идемпотентно: id гейта — стабильный slug падения, повторная ночь не плодит дублей,
рецидив (решённое вернулось) переоткрывает (правила в parked_decisions.park).

Пульс §14: пишет logs/night_cycle_last_run.json (ran_at, seen, parked, suppressed) —
его свежесть читает check_night_cycle_liveness. Улики Этапа-1 минимальны (текст
падения); углубление сбора (прогон теста, git) — следующий слой, харнесс, не LLM.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import finding_identity
import night_investigator
import owner_gate
import parked_decisions
import i18n
from _time_inject import get_now



def _work_title(slug: str) -> str:
    """Человеческое название нити (колонка «Название» docs/handoff/INDEX.md), иначе слаг.
    Холодное чтение 28.09: владелец не понимает технических имён работ."""
    import owner_nag
    return owner_nag._thread_title(slug) or slug.replace("-", " ")


def _days(n) -> str:
    from _fmt_helpers import fmt_count
    return fmt_count(int(n), "days")


def _nights(n) -> str:
    from _fmt_helpers import fmt_count
    return fmt_count(int(n), "nights")

def _integrity_path() -> Path:
    p = os.environ.get("HEALTH_INTEGRITY_LATEST")
    return Path(p) if p else Path(__file__).parent / "logs" / "integrity_latest.json"


def _heartbeat_path() -> Path:
    p = os.environ.get("HEALTH_NIGHT_CYCLE_RECEIPT")
    return Path(p) if p else Path(__file__).parent / "logs" / "night_cycle_last_run.json"


def _slug(msg: str, kind: str = "integrity") -> str:
    """Стабильный id находки. Имя считает ЕДИНСТВЕННЫЙ дом — finding_identity.

    Было (до 2026-09-13): категория до ':' + хэш ВСЕГО текста. Хэш ломался от смены
    любого счётчика внутри сообщения, то есть «73 строки» и «78 строк» давали разные
    id — при подключении предупреждений это плодило бы по новому гейту каждую ночь.
    Вторая редакция правила имени здесь не заводится (§15): id = kind + имя из
    finding_identity.

    Цена перехода названа вслух: у прежних записей в сторе id старой формы. Все они
    resolved, переоткрывать нечего; если старое падение вернётся, оно откроет гейт с
    новым id — это потеря сцепки с историческим гейтом, а не потеря находки."""
    return f"{kind}:{finding_identity.finding_key(msg)}"[:180]


def _load_failures() -> list[tuple[str, str]]:
    """(id, текст) по всем падениям integrity_latest. Пусто/нечитаемо → пусто
    молча ЗДЕСЬ нельзя: но отсутствие файла — законно (ещё не гоняли), пустой
    список падений — норма. Битый JSON поднимаем (пусть увидит датчик)."""
    path = _integrity_path()
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for msg in (data.get("failures") or []) + (data.get("code_failures") or []):
        m = str(msg).strip()
        if m:
            out.append((_slug(m), m))
    return out


def standing_count() -> int:
    """Сколько standing-находок в последнем integrity: действия нет по решению владельца,
    стол они не занимают и «стареют в недельной сводке» (owner_weekly, 28.09)."""
    return sum(1 for *_, cls in _load_warnings() if cls == "standing")


def _load_warnings() -> list[tuple[str, str, str, str]]:
    """(id, метка, текст улик, класс) по предупреждениям integrity_latest.

    ЗАЧЕМ ЭТО ПОЯВИЛОСЬ (2026-09-13). Цикл читал только `failures`, а весь поток,
    который реально доходил до владельца, живёт в `warnings`: замер за 01.07–13.09 —
    369 строк владельцу, 0 падений в артефакте на 13.09 при 5 предупреждениях. То есть
    автономная машина, построенная ровно против курьерства, полгода простаивала, пока
    человек руками переносил предупреждения в чат. Одна строка чтения.

    Формат `warnings` — пары [метка, детали]; метка и есть предмет, детали переменны
    (счётчики, списки id). Имя считается ПО МЕТКЕ, не по паре: иначе каждая смена
    счётчика в деталях рождала бы новую находку."""
    path = _integrity_path()
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for item in (data.get("warnings") or []):
        if isinstance(item, (list, tuple)) and item:
            label, detail = str(item[0]).strip(), str(item[1] if len(item) > 1 else "").strip()
        else:
            label, detail = str(item).strip(), ""
        if not label:
            continue
        out.append((_slug(label, "warn"), label,
                    (label + ("\n" + detail if detail else "")),
                    finding_identity.class_of_warning(label, detail)))
    return out


def owner_card(label: str, verdict: dict, today: date | None = None) -> str | None:
    """Карточка для стола владельца — или None, если диагноз для человека НЕ ГОТОВ.

    ПРАВИЛО ТРЁХ ПОЛЕЙ (решение владельца 2026-09-13). На стол кладётся только то, на
    что он может ответить, НЕ ПОНИМАЯ устройства системы: вопрос его словами · закрытый
    список вариантов · цена каждого. Нет трёх — это не эскалация, а недоделанный
    диагноз, и он возвращается в инженерную очередь.

    Повод дословный. Первый же гейт, приехавший новым путём 13.09, нёс summary: «канал
    систематически выбрасывает кандидатов быстрее, чем успевает их использовать, что
    указывает на несбалансированность порогов фильтрации или темпа пополнения пула».
    Это написано инженеру. Владелец на такое ответить не может, а значит стол,
    наполненный такими карточками, он перестанет открывать — и мы получим прежний
    телеграм-список под новым именем.

    Проверка СТРУКТУРНАЯ (поля есть или нет), не смысловая: читаемость формулировки
    машинно не судится, и притворяться, что судится, нельзя. Граница названа вслух."""
    ask = (verdict or {}).get("owner_ask") or {}
    if not isinstance(ask, dict):
        return None
    q = str(ask.get("question") or "").strip()
    # ПРЕДМЕТ (30.09, владелец: «формулировки не содержательные, не понимаю, зачем они
    # мне»). Звонок называет решение первой строкой карточки. Когда первой строкой по
    # инструкции шла рамка «это про систему, срочность средняя», звонок 30.09 из двух
    # строк не назвал ни одного предмета. Нет предмета — карточка не готова (четвёртое
    # поле к правилу трёх), и она уходит в инженерную очередь, как и прочие неготовые.
    subject = " ".join(str(ask.get("subject") or "").split())
    raw_options = ask.get("options") or []
    if not isinstance(raw_options, list):
        return None
    opts = [o for o in raw_options
            if isinstance(o, dict) and str(o.get("label") or "").strip()
            and str(o.get("cost") or "").strip()]
    if not q or not subject or len(opts) < 2:
        return None
    # Улики и park_reason остаются у расследователя; карточка не пересылает их владельцу.
    import re
    prose = " ".join([subject, q] + [str(o[k]) for o in opts for k in ("label", "cost")])
    if re.search(r"§|\b\w+\.(?:py|sh|json|ya?ml|md)\b|\b\w+_\w+\b|\b\w+\(\)|\bpytest\b", prose):
        return None
    default = _silence_trio(verdict, today or get_now().date())
    silence = (i18n.t("owner.card.default", choice=default["default"], on=default["auto_after"])
               if default else i18n.t("owner.card.wait"))
    options = "\n".join(i18n.t("owner.card.option", option=str(o["label"]).strip(),
                              cost=str(o["cost"]).strip()) for o in opts)
    return i18n.t("owner.card.template", subject=subject, question=q, options=options,
                  silence=silence)


def _announce_defaults(swept: list[dict]) -> None:
    """Применённые умолчания — одной строкой в понедельничную сводку, без медданных."""
    import notify
    notify.weekly(i18n.t("owner.weekly.defaults", count=len(swept)))


SILENCE_DAYS = 14
"""Срок молчания владельца, после которого карточка решается умолчанием.

Решение владельца 2026-09-13 (вариант В), его слова: «срок по умолчанию — две
недели». Число живёт ЗДЕСЬ одним домом и сверяется с how-to тестом
test_silence_days_matches_howto: свод, доки и код не имеют права разъехаться в
сроке, за который система принимает решение за человека."""


def _silence_trio(verdict: dict, today: date) -> dict:
    """Тройка умолчания для карточки — или пустой dict (умолчания не будет).

    Выдаётся, только когда СОВПАЛО всё: судья назвал рекомендованный вариант,
    он есть среди options, назван откат, и owner_gate.silence_default_allowed
    разрешил делегировать МОЛЧАНИЕМ именно это действие (не необратимое, не
    канон, категория известна).

    Ни одного условия нет — карточка просто ждёт владельца вечно, как раньше.
    Это и есть fail-closed: умолчание — привилегия, выдаваемая явно, а не
    поведение по умолчанию у механизма с именем «умолчание»."""
    ask = (verdict or {}).get("owner_ask") or {}
    rec = str(ask.get("recommended") or "").strip()
    rollback = str(ask.get("rollback") or "").strip()
    labels = {str(o.get("label") or "").strip()
              for o in (ask.get("options") or []) if isinstance(o, dict)}
    if not rec or rec not in labels or not rollback:
        return {}
    action = verdict.get("action") or {}
    ok, _why = owner_gate.silence_default_allowed(action)
    if not ok:
        return {}
    executor = action.get("category")
    if executor not in DEFAULT_EXECUTORS:
        # 23.09: вариант, который нечем исполнить, молчанием не решается — иначе запись
        # «решено умолчанием» сообщала бы о действии, которого не было.
        return {}
    return {"default": rec, "rollback": rollback,
            "auto_after": today + timedelta(days=SILENCE_DAYS), "executor": executor}


DEFAULT_EXECUTORS: dict = {}
"""Категория действия → функция, ИСПОЛНЯЮЩАЯ вариант по умолчанию: fn(карточка) -> bool.

Пусто — и это решение, а не заготовка (владелец 23.09). До этого дня умолчание только
записывало «решено: Заблокировать» и звонило «решится само», а исполнять выбранное было
нечему: ни один вариант ни одной карточки не имел исполнителя. Пока реестр пуст, молчание
ничего не решает — карточка ждёт владельца. Исполнитель появляется здесь вместе со своим
тестом, и только тогда его категория снова получает право на умолчание."""


def _execute_default(rec: dict) -> bool:
    """Исполнить вариант по умолчанию карточки. True — исполнено, можно записать решение."""
    fn = DEFAULT_EXECUTORS.get(rec.get("executor"))
    if fn is None:
        return False
    try:
        return bool(fn(rec))
    except Exception as e:  # noqa: BLE001 — не исполнено = не решено; вслух
        print(f"night_cycle: исполнитель умолчания {rec.get('executor')!r} упал на "
              f"{rec.get('id')}: {e!r}", file=sys.stderr)
        return False


def _drop_unexecutable_defaults(today: date) -> int:
    """Снять тройку умолчания с карточек, чей вариант нечем исполнить (самолечение).

    Карточки, получившие тройку до 23.09, несут в звонке «решится само ДД.ММ», которое не
    исполнится. Тройка снимается, карточка остаётся на столе и ждёт владельца."""
    dropped = 0
    for g in parked_decisions.list_open(today):
        if g.get("auto_after") and g.get("executor") not in DEFAULT_EXECUTORS:
            parked_decisions.drop_default(g["id"])
            dropped += 1
    return dropped


NOT_ON_DESK_PREFIXES = ("warn:", "integrity:")


def _retire_not_on_desk(today: date) -> int | None:
    """Снять карточки, чей повод исчез со стола (решение владельца 23.09).

    Карточки из проверки (`warn:*`, `integrity:*`) заводились каждое утро, но не
    снимались: 23.09 на столе из 11 карточек у пяти находки уже не было в свежей
    проверке, а одна висела с класса, который владелец 21.09 перевёл в `standing`
    (стол не для неё). Снимается карточка, чьего имени нет среди находок СЕГОДНЯШНЕЙ
    проверки, или чья находка теперь `standing`. Автор — `not_on_desk`.

    Судим только по свежей проверке: артефакта нет, он не сегодняшний или без даты —
    `None`, ничего не снимается (пустота вчерашнего файла — не «находок нет»). Граница
    вслух: проверка, которая бежит не каждый день (понедельничная), будет сниматься на
    следующий день и переоткрываться рецидивом в свой день."""
    path = _integrity_path()
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if str(data.get("date") or "") != today.isoformat():
        print(f"night_cycle: проверка не сегодняшняя (date={data.get('date')!r}) — "
              "снимать карточки не по чему", file=sys.stderr)
        return None
    present = {fid for fid, _m in _load_failures()}
    standing = set()
    for fid, _label, _ev, cls in _load_warnings():
        (standing if cls == "standing" else present).add(fid)
    retired = 0
    for g in parked_decisions.list_open(today):
        gid = g["id"]
        if not gid.startswith(NOT_ON_DESK_PREFIXES) or gid in present:
            continue
        why = ("находка теперь standing — стол не для неё" if gid in standing
               else "находки нет в проверке")
        parked_decisions.record_decision(
            gid, f"снято: {why} ({today.strftime('%d.%m.%Y')})", by="not_on_desk")
        retired += 1
    return retired


STALLED_DAYS = 6
"""Сколько дней без движения делают нить «застрявшей».

Заказ владельца (`BACKLOG.md::BL-STALLED-THREADS-1`), его слова: «раз в неделю получать
список нитей проекта, по которым дольше шести дней ничего не происходит». Число живёт
ЗДЕСЬ одним домом и сверяется с записью заказа тестом — ровно тем же приёмом, что
SILENCE_DAYS выше: свод, доки и код не имеют права разъехаться в сроке."""

STALLED_EMAIL_WEEKDAY = 0
"""Понедельник. Письмо — РЕДКОЕ и читаемое целиком (граница почтового канала,
docs/how-to/email_channel.md). Воскресенье занято дайджестом изменений в Telegram:
один предмет — один канал по каденции, иначе владелец получает две сводки подряд
и глушит обе."""


def _handoff_index_path() -> Path:
    """Указатель нитей. Путь — через env, тем же приёмом, что _integrity_path.

    Вход производителя обязан быть подменяемым: иначе любой тест, зовущий run(), молча
    читал бы НАСТОЯЩИЙ docs/handoff и настоящую историю git, и его вердикт зависел бы от
    того, сколько нитей стоит в проекте сегодня (§20 — зелёный и красный, причинённые
    окружением, одинаково бесполезны). Замерено сразу: два характеризационных теста
    ночного цикла покраснели ровно на этом, как только у цикла появился третий источник."""
    p = os.environ.get("HEALTH_HANDOFF_INDEX")
    return Path(p) if p else Path(__file__).parent / "docs" / "handoff" / "INDEX.md"


def _open_threads(index_md: Path) -> dict[str, str]:
    """slug → статус нити из таблицы `docs/handoff/INDEX.md`, кроме закрытых.

    ПОЧЕМУ БЕЗ ЭТОГО НЕЛЬЗЯ (замер 14.09). Каталогов нитей 41, коммитов за шесть дней нет
    у тридцати — но двадцать шесть из них ЗАКРЫТЫ, и молчание закрытой нити не находка, а
    норма. Отчёт без этого фильтра положил бы владельцу тридцать карточек в первую же
    ночь и умер бы вместе с каналом.

    Статус — свободный текст, который ведёт человек: ни один механизм его не пишет и не
    сверяет (`intentgate` явно выводит INDEX.md за периметр как указатель). Отсюда
    предикат МЯГКИЙ в одну сторону: закрытой считается только строка, где статус прямо
    говорит «closed». Нить, чей статус человек забыл переставить, попадёт в отчёт лишним
    вопросом — это дешевле, чем пропустить настоящую. Обратная ошибка (нить стоит, а
    статус говорит closed) здесь непокрыта и названа вслух: сторожа на неё нет."""
    return {s: st for s, (st, _h) in _index_rows(index_md).items()
            if "closed" not in st.lower()}


def _closed_threads(index_md: Path) -> dict[str, str]:
    """slug → Handoff ID нитей, чей статус в INDEX прямо говорит «closed».

    Строгий предикат в обратную сторону от `_open_threads`: закрытой считается только
    строка, где это написано; нить, которой в INDEX нет вовсе (переименована, строку
    потеряли), сюда не попадает — снять её карточку значило бы угадать."""
    return {s: h for s, (st, h) in _index_rows(index_md).items() if "closed" in st.lower()}


def _index_rows(index_md: Path) -> dict[str, tuple[str, str]]:
    """slug → (статус, Handoff ID) из таблицы указателя. Один разбор для обоих читателей."""
    rows: dict[str, tuple[str, str]] = {}
    for line in index_md.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| `"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 4:
            continue
        slug = cells[0].strip("` ")
        if slug:
            rows[slug] = (cells[-1], cells[-2].strip("` "))
    return rows


STALLED_GATE_PREFIX = "thread-stalled:"


def _retire_closed_stalled(index_md: Path, today: date) -> int:
    """Снять карточки «нить стоит — добить или закрыть?» у нитей, закрытых в INDEX.

    ЗАЧЕМ (решение владельца 23.09, вариант А). Производитель карточки заводил, но не
    снимал: 23.09 на столе висели три вопроса о нитях, закрытых в тот же день, и колокол
    звонил бы о решённом до ответа в консоли. Ответ на вопрос «добить или закрыть» — сам
    акт закрытия; автор записывается как `thread_closed`, не `owner` и не `default`
    (`decision_has_an_author`).

    Граница вслух: машина не проверяет, что нить закрыта СЛОВОМ владельца, — она видит
    только статус в INDEX. Норма «нить закрывается по решению владельца» держится
    ритуалом закрытия, не этим кодом."""
    closed = _closed_threads(index_md)
    retired = 0
    for g in parked_decisions.list_open(today):
        gid = g["id"]
        if not gid.startswith(STALLED_GATE_PREFIX):
            continue
        slug = gid[len(STALLED_GATE_PREFIX):]
        if slug in closed:
            parked_decisions.record_decision(
                gid, f"вопрос снят: нить закрыта в INDEX ({closed[slug]}), "
                     f"{today.strftime('%d.%m.%Y')}", by="thread_closed")
            retired += 1
    return retired


def _stalled_threads(today: date) -> dict:
    """Нити без движения дольше STALLED_DAYS → карточки владельцу + недельное письмо.

    Производитель в форме, которую заказчик и просил: находка с УСТОЙЧИВЫМ именем
    (`thread-stalled:<slug>`), припаркованная обычным `park`. Отдельного расписания и
    отдельного отправителя не появляется — карточка стареет и доставляется тем же
    конвейером, что остальное.

    Тройка умолчания НЕ заполняется намеренно: «добить или закрыть» — вопрос, у которого
    осмысленного варианта по умолчанию нет, и карточка обязана ждать владельца сколько
    угодно. Это ровно тот случай, ради которого умолчание сделано привилегией, а не
    поведением по умолчанию.

    Письмо говорит о ПЕРЕМЕНЕ, а не о состоянии: поимённо — только те, чья карточка
    заведена сегодня; давно стоящие — одной строкой числом. Иначе четыре одинаковых имени
    неделя за неделей, и письмо перестают открывать (тот же banner-blindness, §13)."""
    try:
        import weekly_digest
    except Exception as e:  # noqa: BLE001 — модуль недоступен: сказать вслух, не молчать
        print(f"night_cycle: производитель застрявших нитей не запущен: {e!r}")
        return {"stalled_seen": 0, "stalled_new": 0, "stalled_mailed": None, "stalled_retired": 0}

    index_md = _handoff_index_path()
    if not index_md.exists():
        print(f"night_cycle: указателя нитей нет ({index_md}) — производитель молчит",
              file=sys.stderr)
        return {"stalled_seen": 0, "stalled_new": 0, "stalled_mailed": None, "stalled_retired": 0}
    retired = _retire_closed_stalled(index_md, today)
    open_threads = _open_threads(index_md)
    last = weekly_digest.thread_last_activity(today)
    fresh_named, aged = [], 0
    for slug in sorted(open_threads):
        seen_on = last.get(slug)
        if seen_on is None or (today - seen_on).days <= STALLED_DAYS:
            continue                       # либо шевелится, либо git о ней не знает вовсе
        gate_id = f"{STALLED_GATE_PREFIX}{slug}"
        idle = (today - seen_on).days
        already_open = parked_decisions.get(gate_id) is not None
        parked_decisions.park(
            gate_id, "owner_decision",
            i18n.t("owner.card.stalled", work=_work_title(slug), days=_days(idle)),
            ref=f"docs/handoff/{slug}/")
        if already_open:
            aged += 1
        else:
            fresh_named.append((slug, idle))

    mailed = None
    if today.weekday() == STALLED_EMAIL_WEEKDAY and (fresh_named or aged):
        body = [i18n.t("owner.card.stalled", work=_work_title(s), days=_days(d))
                for s, d in fresh_named]
        if aged:
            body.append(i18n.t("owner.card.stalled_older", count=aged))
        try:
            import notify
            mailed = notify.email_owner(i18n.t("owner.card.stalled_subject"), "\n\n".join(body))
        except Exception as e:  # noqa: BLE001
            mailed = f"exception:{e!r}"
        if mailed is not True:
            # Общего датчика на почтовый канал нет и не будет, пока производитель один
            # (датчик на канал без производителей сторожил бы пустоту). Значит замечать
            # провал обязан ЭТОТ код — вслух, в stderr, который читает stderr_watch.
            print(f"night_cycle: письмо о застрявших нитях НЕ ушло: {mailed!r}",
                  file=sys.stderr)
    return {"stalled_seen": len(fresh_named) + aged, "stalled_new": len(fresh_named),
            "stalled_mailed": mailed, "stalled_retired": retired}


RED_TEST_PREFIX = "test:"
"""Карточка инженерной очереди на один упорно красный тест: `test:<селектор pytest>`.

Решение владельца 06.10 («пересмотри, должна быть автопочинка»): красный тест не
вопрос владельцу, а вход ночного ремонта (`night_repair`, берёт `dev_fix`). До этого
красные тесты в очередь не попадали вовсе — ремонт видел их только приложением к
чужим карточкам; 04–06.10 одни и те же три теста были красными три ночи подряд."""

RED_STREAM_NIGHTS = 2
"""Сколько ПОДРЯД красных ночей делают тест карточкой.

Одна красная ночь — обычное дело (флак, свежий коммит под чужой нитью). Две подряд —
тест сам не позеленеет, его пора чинить."""


def _selector(test_id: str) -> str:
    """`unit/tests.unit.test_x::t[p]` (id отчёта) → `tests/unit/test_x.py::t[p]` (селектор pytest).

    Селектор — то, что ночной ремонт может прогнать оракулом; id отчёта прогнать нельзя.
    Незнакомая форма возвращается как есть: карточка всё равно заведётся, имя — улика."""
    head, sep, tail = test_id.partition("::")
    mod = head.split("/", 1)[-1]
    if not sep or not mod.startswith("tests."):
        return test_id
    return mod.replace(".", "/") + ".py::" + tail


def _red_tests_to_repair() -> dict:
    """Тест красный RED_STREAM_NIGHTS ночей подряд → карточка `dev_fix` для ночного ремонта.

    Заменяет `_red_stream_returned` (14.09 – 06.10), который на две красные ночи спрашивал
    владельца, строить ли автопочинку. Вопрос устарел 30.09 (ночной ремонт построен,
    инвариант `night_repair_prepares_session_lands`), а владелец 06.10 ответил: должна быть.

    ЧТО ДЕЛАЕТ. Пересечение имён двух последних ночей → `park("test:<селектор>", "dev_fix")`
    (повтор не плодит карточку, решённая переоткрывается рецидивом — правила `park`).
    Открытая `test:*` карточка, чьего теста нет среди красных последней ночи, снимается
    (`not_on_desk`): тест позеленел — починка доехала или он был флаком.

    НЕСВЕЖИЙ ВХОД = «НЕ ЗНАЮ», НЕ «ЗЕЛЕНО» (23.09, нить nightly-liveness): последняя
    запись не покрывает последний плановый запуск → `None`, ничего не паркуется и не
    снимается. Громкость — у `check_nightly_suite_liveness`.

    СНИМАТЬ МОЖНО ТОЛЬКО ПО ПОЛНОМУ СПИСКУ. До 06.10 `regression_ids` обрезался пятью;
    в старой записи число `regression` больше длины списка — тогда не снимаем ничего
    (живой красный тест мог не попасть в список).

    ЧЕГО НЕ ЛОВИТ, вслух. Красное вне ночного набора (ручной прогон, монитор 07:50).
    Тест, красный через ночь (красный, зелёный, красный), карточкой не станет."""
    try:
        import agent_reports_db
    except Exception as e:  # noqa: BLE001 — модуль недоступен: сказать вслух, не молчать
        print(f"night_cycle: производитель красных тестов не запущен: {e!r}", file=sys.stderr)
        return {"red_parked": None, "red_retired": None}

    rows = agent_reports_db.get_agent_report("morning_test_summary", n=RED_STREAM_NIGHTS)
    import morning_test_summary as _mts
    if _mts.summary_covers_last_run(rows[0].get("date") if rows else None) is False:
        print("night_cycle: итог последней ночи тестов не записан — красные тесты "
              "не судимы (check_nightly_suite_liveness)", file=sys.stderr)
        return {"red_parked": None, "red_retired": None}
    nights: list[tuple[int, list[str]]] = []
    for r in rows:
        try:
            s = json.loads(r.get("findings") or "{}")
        except (ValueError, TypeError) as e:
            # Нераспознанный вход = отказ инструмента, не «красного нет» (§14).
            print(f"night_cycle: отчёт о тестах за {r.get('date')} не разобран: {e!r}",
                  file=sys.stderr)
            return {"red_parked": None, "red_retired": None}
        nights.append((int(s.get("regression") or 0), list(s.get("regression_ids") or [])))
    if not nights:
        return {"red_parked": 0, "red_retired": 0}

    last_n, last_ids = nights[0]
    persistent = set(last_ids)
    for _n, ids in nights[1:]:
        persistent &= set(ids)
    if len(nights) < RED_STREAM_NIGHTS:
        persistent = set()
    parked = 0
    for tid in sorted(persistent):
        sel = _selector(tid)
        parked_decisions.park(
            RED_TEST_PREFIX + sel, "dev_fix",
            f"Тест красный {_nights(RED_STREAM_NIGHTS)} подряд в ночном наборе: {sel}. "
            f"Найди причину и почини; оракул — сам этот тест (красный без правки, зелёный с ней).")
        parked += 1

    retired: int | None = 0
    if last_n != len(last_ids):
        retired = None  # список обрезан — снимать не по чему
    else:
        red_now = {_selector(t) for t in last_ids}
        today = get_now().date()
        for g in parked_decisions.list_open(today):
            gid = g["id"]
            if gid.startswith(RED_TEST_PREFIX) and gid[len(RED_TEST_PREFIX):] not in red_now:
                parked_decisions.record_decision(
                    gid, f"снято: тест зелёный в ночном наборе ({today.strftime('%d.%m.%Y')})",
                    by="not_on_desk")
                retired += 1
    return {"red_parked": parked, "red_retired": retired}


def run() -> dict:
    """Один проход цикла. Возвращает сводку {seen, parked, suppressed}."""
    seen = parked = suppressed = 0
    today = get_now().date()
    # Стол потерял память (30.09: переезд в контейнер без logs/) — парковать НЕЛЬЗЯ:
    # решённое вернулось бы на стол как новое, и владелец получил бы вопросы, на которые
    # уже ответил. Fail-closed (§13, ступень 2): цикл стоит, колокол говорит одну строку
    # о потере памяти (owner_nag.run) вместо списка.
    lost = parked_decisions.memory_lost()
    if lost:
        summary = {"ran_at": get_now().isoformat(), "desk_memory_lost": list(lost),
                   "seen": 0, "parked": 0, "suppressed": 0}
        _write_heartbeat(summary)
        return summary
    # Подметаем ДО парковки новых: рецидив, переоткрывший карточку сегодня, не
    # должен попасть под срок, истёкший у её прошлой жизни.
    dropped = _drop_unexecutable_defaults(today)
    swept = parked_decisions.sweep_defaults(today, execute=_execute_default)
    if swept:
        _announce_defaults(swept)
    for fid, msg in _load_failures():
        seen += 1
        verdict = night_investigator.investigate(
            {"id": fid, "source": "integrity", "summary": msg, "evidence": msg})
        cls = verdict.get("class")
        if cls == "transient":
            suppressed += 1
            continue
        if cls == "owner_decision":
            card = owner_card(msg, verdict, today)
            if card is None:
                parked_decisions.park(fid, "dev_fix", verdict.get("diagnosis") or msg)
            else:
                parked_decisions.park(fid, "owner_decision", card, **_silence_trio(verdict, today))
        else:  # dev_fix (или всё, что owner_gate пропустил как авто)
            parked_decisions.park(fid, "dev_fix", verdict.get("diagnosis") or msg)
        parked += 1

    # ── Предупреждения. Маршрут по КЛАССУ находки, а не по тексту ───────────────
    # standing = датированное состояние, действия сейчас нет: НЕ паркуется вовсе —
    # иначе стол владельца наполнится тем, о чём решение уже принято, и его перестанут
    # открывать (banner-blindness, §13). Такие находки стареют в недельной сводке.
    # decide/fix идут тем же путём, что падения: улики собирает харнесс, LLM только
    # СУДИТ, owner_gate может лишь ПОНИЗИТЬ до владельца (§13-клауза, структурно).
    w_seen = w_parked = w_standing = w_unready = 0
    for fid, label, evidence, cls in _load_warnings():
        w_seen += 1
        if cls == "standing":
            w_standing += 1
            continue
        verdict = night_investigator.investigate(
            {"id": fid, "source": "integrity-warn", "summary": label, "evidence": evidence})
        vcls = verdict.get("class")
        if vcls == "transient":
            suppressed += 1
            continue
        # Находка класса fix (реестр УЖЕ назвал её инженерной) уходит владельцу только по
        # СТРУКТУРНОЙ причине — необратимо или пишет в его домен. «Судья назвал категорию
        # не из трёх разрешённых» причиной больше не считается (23.09, решение владельца
        # «да»): так на стол попадали «сохранить файл в общую систему?» и «пересчитать
        # показатели сейчас?». Инженерная очередь ничего не исполняет сама, поэтому это
        # не повышение прав автономного актора — только выбор, чей стол.
        to_owner = cls == "decide" or (
            vcls == "owner_decision"
            and owner_gate.owner_is_the_oracle(verdict.get("action") or {})[0])
        if to_owner:
            # Класс находки тоже умеет ТОЛЬКО понижать: находка, объявленная делом
            # человека, не становится авто-фиксом от того, что судья счёл иначе.
            card = owner_card(label, verdict, today)
            if card is None:
                # Правило трёх полей: недоделанный диагноз на стол не кладём. Он НЕ
                # исчезает — уходит в инженерную очередь с явной пометкой и считается
                # отдельно, иначе «не смог сформулировать» стало бы тихим отказом
                # эскалировать (ровно то, против чего §13 и построен).
                w_unready += 1
                parked_decisions.park(
                    fid, "dev_fix",
                    "ДИАГНОЗ НЕ ГОТОВ ДЛЯ ВЛАДЕЛЬЦА (нет вопроса его словами, либо "
                    "меньше двух вариантов с ценой) — доработать и вернуть на стол. "
                    f"Черновик: {verdict.get('diagnosis') or label}")
            else:
                parked_decisions.park(fid, "owner_decision", card,
                                      **_silence_trio(verdict, today))
        else:
            parked_decisions.park(fid, "dev_fix", verdict.get("diagnosis") or label)
        w_parked += 1

    summary = {"ran_at": get_now().isoformat(), "seen": seen,
               "parked": parked, "suppressed": suppressed,
               "warn_seen": w_seen, "warn_parked": w_parked, "warn_standing": w_standing,
               "warn_unready": w_unready, "defaults_applied": len(swept),
               "defaults_dropped": dropped}
    try:
        summary["off_desk_retired"] = _retire_not_on_desk(today)
    except Exception as e:  # noqa: BLE001 — вслух, но не фатально
        print(f"night_cycle: снятие карточек без повода упало: {e!r}", file=sys.stderr)
        summary["off_desk_retired"] = f"error:{e!r}"
    # Третий источник находок — не падения и не предупреждения, а ОТСУТСТВИЕ событий:
    # нить, по которой ничего не происходит, никакой датчик не роняет. Заказ владельца
    # BL-STALLED-THREADS-1. Отказ производителя не имеет права уронить цикл: конвейер
    # существует ради двух источников выше, и третий не должен становиться их условием.
    try:
        summary.update(_stalled_threads(today))
    except Exception as e:  # noqa: BLE001 — вслух, но не фатально
        print(f"night_cycle: производитель застрявших нитей упал: {e!r}", file=sys.stderr)
        summary.update({"stalled_seen": 0, "stalled_new": 0, "stalled_mailed": f"error:{e!r}",
                        "stalled_retired": None})
    # Четвёртый источник — упорно красные тесты: карточка ночному ремонту, не вопрос
    # владельцу (решение 06.10). Та же защита: отказ не роняет цикл.
    try:
        summary.update(_red_tests_to_repair())
    except Exception as e:  # noqa: BLE001 — вслух, но не фатально
        print(f"night_cycle: производитель красных тестов упал: {e!r}", file=sys.stderr)
        summary.update({"red_parked": None, "red_retired": f"error:{e!r}"})
    try:
        parked_decisions.remember_size()
    except Exception as e:  # noqa: BLE001 — вслух, но не фатально: без отметки сторож слепнет, стол цел
        print(f"night_cycle: память стола не записана: {e!r}", file=sys.stderr)
    _write_heartbeat(summary)
    return summary


def main() -> dict:
    """Точка входа launchd: проход цикла, затем звонок (23.09).

    Колокол в минуту цикла ждёт его (`owner_nag._cycle_pending`) — звонит цикл, закончив,
    чтобы число на столе было уже после снятий. Звонок вынесен из `run()`: `run()` зовут
    тесты, и звонить из них нельзя."""
    summary = run()
    try:
        import owner_nag
        summary["bell"] = owner_nag.ring_after_cycle()
    except Exception as e:  # noqa: BLE001 — звонок не роняет цикл, но и не молчит
        print(f"night_cycle: звонок после цикла не удался: {e!r}", file=sys.stderr)
    return summary


def _write_heartbeat(summary: dict) -> None:
    path = _heartbeat_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, default=str))
