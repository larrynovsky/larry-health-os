"""parked_decisions.py — стор гейтов НОЧНОГО ЦИКЛА, ждущих владельца.

Единственный источник правды «что висит на решении оператора» для автономного
ночного цикла: согласование плана, закрытие этапа, решение по подготовленному
dev-фиксу. НЕ общий стор всех решений владельца — доменные (принадлежность
канону §16, лаб-имена) живут в своих домах (lab_domain_verdicts,
field_reviews_db, doc_reviews_db); ночной цикл туда ДЕЛЕГИРУЕТ, а не дублирует
(§13-клауза fail-closed, сверка §11 2026-08-02).

Дом данных — JSON в logs/ (как triage_delivery_latest, suite_last_run): это
ops-meta состояние dev-конвейера, НЕ health-данные. Поэтому НЕ health.db (там
живут health-очереди doc/field/lab reviews) и НЕ новая SQLite (контенция
ничтожна: писатель — ночной движок раз в сутки, резолвер — человек в темпе
человека, читатель — нытьё). Атомарная запись + flock на read-modify-write
закрывают гонку писатель↔резолвер.

Контракт «reacted = записанное решение, не тап»: гейт уходит из list_open ТОЛЬКО
через record_decision (непустая строка-решение) или defer (до будущей даты).
Чтения состояние не меняют — нытьё звонит, пока решение не ЗАПИСАНО, а не пока
оператор не «посмотрел» (§17: квитанция, не прокси).

Умолчание по молчанию (2026-09-13) контракт НЕ ослабляет: sweep_defaults не
трогает стор напрямую, а зовёт тот же record_decision — путь выхода остался
один. Новое здесь другое: у решения появился АВТОР (decided_by owner|default),
потому что «решено» без автора через месяц читается как слово владельца.
"""
from __future__ import annotations

import fcntl
import json
import os
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Optional

from _time_inject import get_today

KINDS = ("plan_approval", "stage_closure", "dev_fix", "owner_decision")


def _store_path() -> Path:
    """Путь к JSON-стору. env HEALTH_PARKED_DB переопределяет (тесты)."""
    p = os.environ.get("HEALTH_PARKED_DB")
    if p:
        return Path(p)
    return Path(__file__).parent / "logs" / "parked_decisions.json"


@contextmanager
def _locked():
    """flock вокруг read-modify-write: писатель (движок) и резолвер (консоль)
    не теряют записи друг друга. Лок-файл рядом со стором, создаётся при нужде."""
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_suffix(path.suffix + ".lock")
    fd = os.open(str(lock), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _load() -> dict:
    path = _store_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        # Битый стор — НЕ молчим и НЕ теряем: пустой словарь означал бы «висящего
        # нет», а это ложное спокойствие (§14). Поднимаем — датчик живости увидит.
        raise


def _save(data: dict) -> None:
    """Атомарная запись: temp в том же каталоге + os.replace (durable rename)."""
    path = _store_path()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(path))


def park(gate_id: str, kind: str, summary: str, ref: Optional[str] = None,
         default: Optional[str] = None, rollback: Optional[str] = None,
         auto_after: Optional[date] = None, executor: Optional[str] = None) -> str:
    """Поставить гейт на решение владельца (идемпотентно по gate_id).

    gate_id — СТАБИЛЬНЫЙ ключ (напр. 'pytest:test_clone_is_skipped'), чтобы
    повторное ночное обнаружение того же падения не плодило дублей.
    Правила upsert при существующем id:
      - open      → обновить summary/ref, остаться open;
      - deferred  → обновить summary/ref, УВАЖИТЬ defer (не будить раньше срока);
      - resolved  → РЕЦИДИВ: переоткрыть (status=open, снять решение, created=сегодня).

    Тройка умолчания (решение владельца 2026-09-13, вариант В: «молчание =
    делегирование»): default — какой вариант применится сам, rollback — как его
    откатить, auto_after — с какой даты. Либо ВСЕ ТРИ, либо НИ ОДНОГО: карточка
    с вариантом, но без отката — обещание, которое нечем отменить, а с датой без
    варианта — таймер в никуда. Без тройки карточка умолчанием не закрывается
    НИКОГДА (fail-closed): право закрыться молча производитель выдаёт ЯВНО.

    executor (23.09) — имя исполнителя варианта по умолчанию. Без него тройка не
    выдаётся вовсе: до 23.09 умолчание только ЗАПИСЫВАЛО «решено: Заблокировать», а
    исполнять было нечему — обещание действия без действия (см. sweep_defaults).
    """
    if kind not in KINDS:
        raise ValueError(f"неизвестный kind={kind!r}, ожидается один из {KINDS}")
    if not summary or not summary.strip():
        raise ValueError("summary обязателен: это строка контекста для колокола")
    trio = (default, rollback, auto_after)
    if all(x is not None for x in trio) and not executor:
        raise ValueError("тройка умолчания без исполнителя: вариант, который нечем "
                         "исполнить, не решается молчанием (23.09)")
    if any(x is not None for x in trio) and not all(x is not None for x in trio):
        raise ValueError(
            "тройка умолчания неполна: нужны ВСЕ три (default, rollback, auto_after) "
            f"или ни одного; дано default={default!r} rollback={rollback!r} "
            f"auto_after={auto_after!r}")
    today = get_today().isoformat()
    with _locked():
        data = _load()
        rec = data.get(gate_id)
        if rec is None or rec.get("status") == "resolved":
            rec = {"created": today, "status": "open",
                   "decision": None, "resolved_at": None, "defer_until": None}
        rec.update(kind=kind, summary=summary.strip(), ref=ref)
        if all(x is not None for x in trio):
            rec.update(default=default.strip(), rollback=rollback.strip(),
                       auto_after=auto_after.isoformat(), executor=executor)
        data[gate_id] = rec
        _save(data)
    return gate_id


def list_open(today: Optional[date] = None) -> list[dict]:
    """Гейты, которые СЕЙЧАС требуют внимания: open, плюс deferred, чей срок
    наступил (defer_until <= today). Чистое чтение — состояние не меняет."""
    t = (today or get_today()).isoformat()
    out = []
    for gid, rec in _load().items():
        st = rec.get("status")
        due = rec.get("defer_until")
        if st == "open" or (st == "deferred" and due is not None and due <= t):
            out.append({"id": gid, **rec})
    return sorted(out, key=lambda r: (r.get("created") or "", r["id"]))


AUTHORS = ("owner", "default", "thread_closed", "not_on_desk")
"""Кто снял карточку. `thread_closed` (решение владельца 23.09, вариант А): вопрос
«добить или закрыть нить» снят тем, что нить закрыта в указателе — ответ дал акт
закрытия, а не слово владельца в консоли и не молчание. Отдельный автор — чтобы через
месяц это не читалось ни как «владелец ответил», ни как «решилось умолчанием».
`not_on_desk` (решение владельца 23.09): повод карточки исчез со стола сам — находки нет
в свежей проверке, или её класс стал `standing` (стол не для неё). Снимает ночной цикл."""


def record_decision(gate_id: str, decision: str, by: str = "owner") -> None:
    """Записать решение владельца — единственный способ снять гейт «насовсем».
    decision обязан быть непустым: пустое = тап, а тап не считается реакцией
    (§17). Нытьё замолкает по факту записи, не по факту взгляда.

    by — КТО решил: 'owner' (человек сказал), 'default' (истёк срок молчания,
    см. sweep_defaults) или 'thread_closed' (вопрос о нити снят её закрытием, см. AUTHORS). Поле существует, чтобы стор не выдавал умолчание за
    слово владельца: через месяц «решено» без автора читается как его выбор, а
    это ровно та подмена, ради предотвращения которой умолчание и записывается."""
    if not decision or not decision.strip():
        raise ValueError("decision пуст: реакция = записанное решение, не тап")
    if by not in AUTHORS:
        raise ValueError(f"by={by!r}: ожидается один из {AUTHORS}")
    with _locked():
        data = _load()
        rec = data.get(gate_id)
        if rec is None:
            raise KeyError(f"нет гейта {gate_id!r}")
        rec.update(status="resolved", decision=decision.strip(),
                   resolved_at=get_today().isoformat(), decided_by=by)
        _save(data)


def sweep_defaults(today: Optional[date] = None, execute=None) -> list[dict]:
    """Закрыть умолчанием карточки, у которых срок молчания истёк.

    Решение владельца 2026-09-13 (вариант В): молчание = делегирование. Через
    оговорённый срок система применяет вариант по умолчанию и ЗАПИСЫВАЕТ, что
    решено умолчанием, каким вариантом и как откатить.

    Закрывается ТОЛЬКО карточка, у которой одновременно: status == 'open'
    (deferred — это явный акт владельца «не сейчас», перебивать его нельзя),
    заполнена вся тройка (default, rollback, auto_after), и auto_after <= today.
    Всё остальное не трогается — в том числе карточка, провисевшая год без
    тройки. Это и есть граница «не применяется к медицинскому и необратимому»:
    её держит не список доменов здесь, а то, что тройку проставляет ТОЛЬКО
    производитель, уже спросивший owner_gate.requires_owner.

    ИСПОЛНЕНИЕ ДО ЗАПИСИ (23.09, решение владельца). `execute(rec) -> bool` обязан
    исполнить вариант; запись «решено умолчанием» делается, только если он вернул True.
    Без `execute` не закрывается ничего: до 23.09 здесь записывалось «решено:
    Заблокировать» при том, что блокировать было нечему, — владелец получил бы
    сообщение о действии, которого не было.

    Возврат: список {'id', 'decision'} по закрытым — для лога и колокола.
    """
    t = today or get_today()
    closed = []
    for rec in list_open(t):
        if rec.get("status") != "open":
            continue
        d, rb, after = rec.get("default"), rec.get("rollback"), rec.get("auto_after")
        if not (d and rb and after) or after > t.isoformat():
            continue
        if execute is None or not execute(rec):
            continue
        decision = (f"решено умолчанием {t.strftime('%d.%m.%Y')}: {d} · откат: {rb}")
        record_decision(rec["id"], decision, by="default")
        closed.append({"id": rec["id"], "decision": decision})
    return closed


def defer(gate_id: str, until: date) -> None:
    """Отложить до будущей даты — тоже решение (перенос), гасит звонок до срока.
    until в прошлом/сегодня бессмысленно (гейт всплыл бы сразу) — отвергаем."""
    today = get_today()
    if until <= today:
        raise ValueError(f"defer until={until} не в будущем (today={today})")
    with _locked():
        data = _load()
        rec = data.get(gate_id)
        if rec is None:
            raise KeyError(f"нет гейта {gate_id!r}")
        rec.update(status="deferred", defer_until=until.isoformat())
        _save(data)


def drop_default(gate_id: str) -> None:
    """Снять с карточки тройку умолчания: карточка остаётся на столе и ждёт владельца.

    Для карточек, получивших тройку до 23.09 без исполнителя: их «решится само ДД.ММ»
    в звонке было обещанием, которое нечем исполнить."""
    with _locked():
        data = _load()
        rec = data.get(gate_id)
        if rec is None:
            raise KeyError(f"нет гейта {gate_id!r}")
        for k in ("default", "rollback", "auto_after", "executor"):
            rec.pop(k, None)
        _save(data)


def get(gate_id: str) -> Optional[dict]:
    """Один гейт по id или None. Чистое чтение."""
    rec = _load().get(gate_id)
    return {"id": gate_id, **rec} if rec is not None else None


# ── Память стола (30.09, нить desk-guard) ───────────────────────────────────────
# Стор живёт в logs/, а logs/ — не база: при переезде в контейнер (30.09) база
# переехала, стол — нет. Ночной цикл положил на ПУСТОЙ стол решённое 10.08 и 23.08,
# и владелец получил звонок с ними. Ключи стола не удаляются никогда (park/record/
# defer/drop_default правят поля записи, не выкидывают её), поэтому число ключей
# монотонно: если оно меньше запомненного, стол потерял память — ровно этот класс.
# Запомненное держится В БАЗЕ (system_config), потому что база — то, что переезжает.
MEMORY_KEY = "parked_decisions.known_ids"


def _remembered() -> Optional[int]:
    """Запомненное число ключей из базы; None — базы нет/нечитаема (судить нечем).

    Отметка в базе описывает КАНОНИЧЕСКИЙ стол (logs/). Стол, уведённый env
    HEALTH_PARKED_DB (тесты, стенд), — другой стол: сравнивать его с отметкой базы
    значило бы судить tmp-файл по памяти боевого (стенд судит живую базу контейнера)."""
    if os.environ.get("HEALTH_PARKED_DB"):
        return None
    try:
        import config_db
        return int(config_db.get_config(MEMORY_KEY, 0) or 0)
    except Exception as e:  # noqa: BLE001 — нет базы в этом процессе: не судим, но вслух
        import logging
        logging.getLogger(__name__).warning("parked_decisions: память стола не прочитана: %r", e)
        return None


def memory_lost() -> Optional[tuple[int, int]]:
    """(ключей сейчас, помнили) если стол забыл карточки; None — цел или судить нечем."""
    known = _remembered()
    now = len(_load())
    return (now, known) if known is not None and now < known else None


def remember_size() -> None:
    """Запомнить число ключей стола в базе (только вверх). Писатель один — ночной цикл."""
    known = _remembered()
    now = len(_load())
    if known is not None and now > known:
        import config_db
        config_db.upsert_config(MEMORY_KEY, value_num=now, category="ops",
                                source="parked_decisions.remember_size")


if __name__ == "__main__":
    import tempfile
    import unittest.mock as _m
    with tempfile.TemporaryDirectory() as d:
        os.environ["HEALTH_PARKED_DB"] = str(Path(d) / "p.json")
        park("a", "dev_fix", "x"); park("b", "dev_fix", "y")
        with _m.patch(__name__ + "._remembered", return_value=2):
            assert memory_lost() is None, "стол цел"
        with _m.patch(__name__ + "._remembered", return_value=5):
            assert memory_lost() == (2, 5), "стол забыл 3 карточки"
        with _m.patch(__name__ + "._remembered", return_value=None):
            assert memory_lost() is None, "без базы — не судим"
    print("ok")
