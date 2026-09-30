#!/usr/bin/env python3.11
"""intent_registry.py — общая логика реестра замысла подсистем.

Единый источник двух вещей, которые ОБЯЗАНЫ считаться одинаково у писателя
(doc_agent.py) и у сторожа (tests/consistency/test_intent_validation_gate.py):

  * provenance записи — хэш её СМЫСЛОВЫХ полей (intent + claim/status инвариантов),
    вшитый в тёплую страницу. Хэш страницы ≠ хэш записи → страница устарела
    (staleness deviation): первичка-реестр уехала, реплика-страница отстала.
  * status-honesty — запрещённые «отмывающие» формулировки на странице для
    инвариантов со status != holds (H2: выдать непостроенное за работающее).

provenance НЕ включает last_reconciled, check-маркеры, warm_guard — правки этих
полей НЕ должны триггерить перегенерацию (поле-уровневый триггер, F6).
Детерминирован, без сети и LLM. Первичная копия — этот yaml, не iCloud.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
REGISTRY_PATH = ROOT / "subsystem_intent.yaml"

_NON_HOLDS = {"open", "doc_drift", "designed_not_built"}
_PROV_RE = re.compile(r"intent-provenance:\s*(\S+)\s+(sha256:[0-9a-f]{12})")


def load_registry() -> list[dict]:
    return yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8")) or []


def get_entry(entry_id: str, entries: list[dict] | None = None) -> dict:
    for e in entries if entries is not None else load_registry():
        if e["id"] == entry_id:
            return e
    raise KeyError(f"нет записи id={entry_id!r} в {REGISTRY_PATH.name}")


def _norm(s: str) -> str:
    """Схлопывает пробелы/переносы: folded-скаляр yaml (>) не должен менять хэш."""
    return " ".join((s or "").split())


def limits_of(inv: dict) -> list[str]:
    """Именованные ГРАНИЦЫ доказанного у инварианта — список строк, поле `limits`.

    Зачем поле, а не проза в claim (2026-07-28, найдено исполнением). Раздел «пределы»
    тёплой страницы строился ТОЛЬКО из non-holds инвариантов. Значит подъём статуса до
    `holds` физически удалял оговорки из того, что видит читатель: доказан один сценарий
    из трёх — страница сообщала «обещание надёжно выполнено». Оговорка в комментарии yaml
    генератору не видна вовсе, оговорка внутри claim была пересказана им как УСИЛЕНИЕ.

    `limits` разводит две разные вещи, которые до сих пор путал один флаг `status`:
    «выполнено ли обещание» и «насколько широко это проверено». Инвариант может честно
    держаться и при этом иметь названную границу — сейчас это выразить было нечем.
    """
    raw = inv.get("limits") or []
    return [_norm(x) for x in raw if _norm(x)]


def limited_invariants(entry: dict) -> list[dict]:
    """Инварианты с названными границами — НЕЗАВИСИМО от статуса."""
    return [inv for inv in entry.get("invariants", []) if limits_of(inv)]


def entry_provenance(entry: dict) -> str:
    """sha256[:12] по смысловым полям: intent + инварианты (id, claim, status, limits).

    `limits` попадает в хэш ТОЛЬКО когда непусто. Иначе добавление поля переписало бы
    провенанс всех 17 подсистем разом и объявило устаревшими страницы, которых правка
    не касалась: сторож — не повод для массового шума. Записи без границ сохраняют
    прежний хэш побайтово."""
    def _inv(inv: dict) -> dict:
        d = {"id": inv["id"], "claim": _norm(inv.get("claim", "")),
             "status": inv.get("status", "")}
        lim = limits_of(inv)
        if lim:                      # ключа нет вовсе, если границ нет → хэш прежний
            d["limits"] = lim
        # `verified_at` — по тому же правилу «только когда непусто» и по той же причине:
        # 78 holds-инвариантов в реестре, дата подтверждения сегодня есть у одного. Включи
        # ключ безусловно — и провенанс переписался бы у всех 18 подсистем разом, объявив
        # устаревшими страницы, которых правка не касалась. Возраст обязан быть виден на
        # странице (Р-3), поэтому появление даты страницу устаревает — но только ту.
        va = str(inv.get("verified_at") or "").strip()
        if va:
            d["verified_at"] = va
        vo = verified_on(inv)
        if vo:                       # то же правило «только когда непусто», та же причина
            d["verified_on"] = vo
        pr = probes_of(inv)
        if pr:                       # носитель доказательства — часть смысла записи
            d["probe"] = pr
        return d

    payload = {
        "intent": _norm(entry.get("intent", "")),
        "invariants": [_inv(inv) for inv in entry.get("invariants", [])],
    }
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


def provenance_comment(entry: dict) -> str:
    return f"<!-- intent-provenance: {entry['id']} {entry_provenance(entry)} -->"


def page_provenance(page_text: str) -> str | None:
    m = _PROV_RE.search(page_text)
    return m.group(2) if m else None


def provenance_stale(entry: dict, page_text: str) -> bool:
    """True = страница не выведена из текущей записи (нет метки ИЛИ хэш разошёлся)."""
    return page_provenance(page_text) != entry_provenance(entry)


def non_holds_invariants(entry: dict) -> list[dict]:
    return [inv for inv in entry.get("invariants", [])
            if inv.get("status") in _NON_HOLDS]


# Потолок годности дорогого подтверждения. 90 дней — решение владельца Р-4 (2026-07-29):
# совпадает с квартальным потолком аудита чисел (§9), то есть не заводит нового ритма.
# 30 отвергнуты явно: пропустишь дважды — привыкнешь к жёлтому.
VERIFICATION_TTL_DAYS = 90


def verified_at(inv: dict) -> "datetime.date | None":
    """Дата последнего ПОДТВЕРЖДЕНИЯ инварианта (поле `verified_at`), не дата правки текста.

    Зачем поле (Р-3, 2026-07-29). Реестр делил мир на «есть статус» и «нет статуса», и
    молчание читалось как «всё хорошо». Но `holds`, подтверждённый год назад на другом
    коде, — не то же самое, что подтверждённый вчера. Возраст обнуляет ТОЛЬКО
    подтверждение: прогон пробы при правке подсистемы либо ручной прогон. Календарь
    возраст не обнуляет — он его лишь предъявляет.

    Битую дату возвращаем как None, а не глушим: неразобранная дата — это отсутствие
    подтверждения, и вести себя она обязана как отсутствие (урок DG-05: битая дата,
    проходящая проверку, молча обнуляет счётчик)."""
    raw = str(inv.get("verified_at") or "").strip()
    if not raw:
        return None
    try:
        return datetime.date.fromisoformat(raw)
    except ValueError:
        return None


def verification_age_days(inv: dict, today: "datetime.date") -> "int | None":
    at = verified_at(inv)
    return None if at is None else (today - at).days


VERIFICATION_PLACES = ("canon", "staging")


def verified_on(inv: dict) -> "str | None":
    """ГДЕ получено подтверждение: 'canon' | 'staging' (решение владельца Р-9, 2026-07-29).

    Зачем отдельно от даты. После Р-7 живые пробы исполняются на снимке канона, а не на
    боевой базе. «Прогнали на каноне» и «прогнали на его снимке» — разные утверждения,
    и без этого поля они в реестре неразличимы. Оговорка прозой в `limits` не годится:
    28.07 уже выяснилось, что прозу генератор тёплой страницы пересказывает как заверение,
    а поле он обязан назвать.

    Неизвестное значение → None, то есть судится как отсутствие. Умолчания «наверное
    канон» нет намеренно: оно было бы премией за молчание в сторону более сильного
    утверждения."""
    raw = str(inv.get("verified_on") or "").strip().lower()
    return raw if raw in VERIFICATION_PLACES else None


def stale_verification(inv: dict, today: "datetime.date",
                       ttl: int = VERIFICATION_TTL_DAYS) -> bool:
    """True = подтверждение протухло ЛИБО его нет вовсе.

    Отсутствие подтверждения приравнено к протухшему СОЗНАТЕЛЬНО: иначе инвариант без
    поля выглядел бы свежее инварианта с честной старой датой — премия за молчание."""
    age = verification_age_days(inv, today)
    return age is None or age > ttl


_PROBE_RE = re.compile(r"plans/probe_[A-Za-z0-9_\-]+\.py")


def probes_of(inv: dict) -> list:
    """Пробы, названные ПОЛЕМ `probe` — явная ссылка, а не упоминание в тексте.

    Зачем поле, когда есть `probe_backed`. 2026-07-29 замерено: из трёх инвариантов,
    поднятых пробами, `probe_backed` находил ОДИН. Ссылка на пробу была записана
    YAML-комментарием, а комментарий не попадает в данные — сторож, требующий у
    probe-backed holds дату и место, честно зеленел на выборке из одного элемента.
    Два обещания несли дату добровольно; удали её — не покраснело бы ничего.

    Поле — единственный носитель ссылки, на который может опереться механизм. Текстовый
    поиск остаётся как страховка от того, что автор сослался иначе, но НЕ как основной путь:
    охват страховки не измерим, а охват поля измерим и сверяется
    tests/consistency/test_probe_coverage.py.
    """
    raw = inv.get("probe")
    if not raw:
        return []
    items = raw if isinstance(raw, (list, tuple)) else [raw]
    return [str(p).strip() for p in items if str(p).strip()]


def probe_backed(inv: dict) -> bool:
    """Инвариант, чей статус снят ПРОБОЙ (test), а не автоматической проверкой (check).

    Различие не косметическое, и от него зависит, нужна ли дата подтверждения.
    `check: {file, present, absent}` — это CHECK: он переисполняется каждым ночным
    прогоном и на каждом коммите, то есть подтверждает себя непрерывно; спрашивать с
    него «когда последний раз проверяли» бессмысленно — ответ всегда «только что».
    Проба — это TEST: её запускает человек, её результат датирован, и между запусками
    она не говорит ничего. Возраст (Р-3/Р-4) осмыслен ровно для второго класса.

    Признак — упоминание `plans/probe_*.py` где угодно в инварианте (claim, limits,
    комментарии в значениях). Искать только в одном поле значило бы зависеть от того,
    где автор сослался на пробу."""
    return bool(_PROBE_RE.search(json.dumps(inv, ensure_ascii=False)))


PROBE_VERDICTS_PATH = ROOT / "logs" / "probe_verdicts.json"
PROBE_VERDICT_TTL_DAYS = 10          # недельный носитель + запас на пропущенный прогон
# «Судить не на чем» (exit 3) — состояние данных, обещание стоит на датированном
# доказательстве. Пока доказательству меньше этого срока — строка в недельном дайджесте
# (решение владельца 2026-08-31, отменяет ежедневный WARN варианта A от 2026-08-12:
# 32 дня подряд одна и та же строка без действия — тренировка не читать канал, §13).
# Старше — ежедневно, как просроченное обещание. Число — политика ратчета (§9 класс 2).
PROBE_STALE_EVIDENCE_DAYS = 90


def probe_verdicts(path: "Path | None" = None) -> dict:
    """Что сказал последний прогон носителя проб. Пусто = не запускался или артефакт битый.

    Битый json НЕ проглатывается в «пусто» молча — это было бы «датчик мёртв» =
    «всё хорошо». Возвращаем пометку `broken`, и читатели обязаны её отличать."""
    p = path or PROBE_VERDICTS_PATH
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        return {"broken": str(exc)[:120]}
    return data if isinstance(data, dict) else {"broken": "не объект"}


def effective_status(inv: dict, verdicts: "dict | None" = None) -> str:
    """Статус, КОТОРЫЙ ПРАВДА СЕЙЧАС — с учётом последнего прогона пробы.

    Решение владельца 2026-07-29: красная проба роняет статус САМА, без человека. Роняем
    ВЫЧИСЛЕНИЕМ, а не правкой yaml, и это осознанная разница с буквой решения:

      · yaml — авторский документ, его правит человек коммитом; авто-правка на Studio
        создала бы незакоммиченное на канонической машине (§1) и грязное дерево под
        деплоем (§12);
      · вычисление роняет статус НЕМЕДЛЕННО и во всех читателях сразу — тёплая страница,
        тесты, дашборд видят `open`, не дожидаясь, пока человек дойдёт до файла.

    Смысл решения сохранён: реестр перестаёт врать без участия человека. Человек нужен,
    чтобы вернуть `holds`, — и это правильная асимметрия.

    exit=2 (сломана САМА проба) статус НЕ роняет: об инварианте это не говорит ничего.
    Такой прогон обязан быть громким иначе — это делает `check_probe_liveness`.

    exit=3 (СУДИТЬ НЕ НА ЧЕМ — состояние данных; решение владельца 2026-08-12, вариант A)
    статус тоже НЕ роняет: инвариант не ломался, оснастка исправна, просто материала для
    суждения нет (пример: в семье D снимка pass-set меньше двух пар). Обещание остаётся
    стоять на ДАТИРОВАННОМ подтверждении (verified_at); его возраст ежедневно
    предъявляет `check_probe_liveness` WARN-строкой.
    """
    status = inv.get("status", "")
    if status != "holds":
        return status
    declared = probes_of(inv)
    if not declared:
        return status
    v = (verdicts if verdicts is not None else probe_verdicts()).get("probes") or {}
    for probe in declared:
        if (v.get(probe) or {}).get("exit") == 1:
            return "open"
    return status


def unverified_holds(entry: dict, today: "datetime.date",
                     ttl: int = VERIFICATION_TTL_DAYS) -> list[dict]:
    """`holds`-инварианты, опирающиеся на пробу, чьё подтверждение протухло или отсутствует."""
    return [inv for inv in entry.get("invariants", [])
            if inv.get("status") == "holds" and probe_backed(inv)
            and stale_verification(inv, today, ttl)]


def entry_code_files(entry: dict) -> set[str]:
    """Файлы кода подсистемы: из `code_anchors` (до `::`) и из `check.file` инвариантов.

    Два источника, а не один, потому что они и наполняются по-разному: якоря пишет автор
    записи, `check.file` — автор инварианта, и расходятся они регулярно."""
    files: set[str] = set()
    for anchor in entry.get("code_anchors") or []:
        head = str(anchor).split("::", 1)[0].strip()
        if head.endswith(".py"):
            files.add(head)
    for inv in entry.get("invariants") or []:
        f = str((inv.get("check") or {}).get("file") or "").strip()
        if f.endswith(".py"):
            files.add(f)
    return files


def demands_reconfirmation(entry: dict, staged: "list[str]",
                           today: "datetime.date") -> list[tuple[str, str]]:
    """Р-1: правка кода подсистемы ОБЕСЦЕНИВАЕТ прежнее подтверждение немедленно.

    Возврат: [(id инварианта, дата подтверждения или 'НЕТ')] для тех `holds`, чьё
    подтверждение старше этой правки. Пусто = либо код подсистемы не тронут, либо
    подтверждения переставлены на сегодня, либо статусы понижены.

    Почему «немедленно», а не «через N дней»: подтверждение было про ДРУГОЙ код.
    Календарь (Р-4) отвечает на другой вопрос — сколько прошло с последнего прогона при
    неизменном коде; здесь код изменился, и возраст обнулять нечем.

    Чистая функция: список staged приходит параметром. Иначе позитивный контроль этого
    правила пришлось бы ставить на настоящем git-индексе, то есть на состоянии, которое
    тест не контролирует."""
    touched = entry_code_files(entry) & set(staged or [])
    if not touched:
        return []
    out: list[tuple[str, str]] = []
    for inv in entry.get("invariants") or []:
        # Только проба-опирающиеся: check-опирающиеся переисполняются этим же коммитом
        # (интент-гейт гоняет якорные тесты), и требовать с них ручного переподтверждения
        # значило бы заставлять человека расписываться за то, что машина уже сделала.
        if inv.get("status") != "holds" or not probe_backed(inv):
            continue
        at = verified_at(inv)
        if at is None or at < today:
            out.append((inv.get("id", "<без id>"), str(at) if at else "НЕТ"))
    return out


def entry_delta(old: dict | None, new: dict) -> list[str]:
    """Детерминированная дельта записи реестра (old→new) — факты-строки, БЕЗ LLM и
    домыслов. Источник истины раздела «что поменялось» тёплой страницы (hybrid:
    факт отсюда, LLM только переводит в живую фразу). Чистая, симметрична provenance
    (те же смысловые поля: intent + инварианты id/claim/status)."""
    if old is None:
        return ["новая подсистема в реестре"]
    facts: list[str] = []
    if _norm(old.get("intent", "")) != _norm(new.get("intent", "")):
        facts.append("правка замысла (intent)")
    o = {i["id"]: i for i in old.get("invariants", [])}
    n = {i["id"]: i for i in new.get("invariants", [])}
    for iid, inv in n.items():
        if iid not in o:
            facts.append(f"+ инвариант {iid} (status={inv.get('status')})")
            continue
        if o[iid].get("status") != inv.get("status"):
            facts.append(f"{iid}: статус {o[iid].get('status')}→{inv.get('status')}")
        if _norm(o[iid].get("claim", "")) != _norm(inv.get("claim", "")):
            # НАПРАВЛЕНИЕ правки claim машинно не выводится, и это надо сказать вслух:
            # 2026-07-28 генератор получил голое «правка claim», не знал, усиление это или
            # ослабление, и пересказал добавленное ОГРАНИЧЕНИЕ как заверение. При нехватке
            # факта нарратив дрейфует в лестную сторону — поэтому факт несёт запрет догадки.
            facts.append(f"{iid}: правка claim (направление НЕ определено машинно — "
                         f"описывать нейтрально, НЕ как усиление)")
        _lo, _ln = limits_of(o[iid]), limits_of(inv)
        for _added in [x for x in _ln if x not in _lo]:
            facts.append(f"{iid}: + ГРАНИЦА доказанного — «{_added}»")
        for _gone in [x for x in _lo if x not in _ln]:
            facts.append(f"{iid}: − снята граница — «{_gone}» (значит доказано шире)")
        # Симметрия с provenance обязана держаться пофакту, а не по обещанию в докстринге:
        # поле, попавшее в хэш, но не в дельту, устаревает страницу МОЛЧА — генератор
        # получает «ничего не менялось» и не упоминает изменение, которое сам же и вызвало.
        # Поймано 2026-07-29 на первой же регенерации после ввода `verified_at`.
        _ov, _nv = str(o[iid].get("verified_at") or ""), str(inv.get("verified_at") or "")
        if _ov != _nv:
            facts.append(f"{iid}: подтверждение {_ov or 'НЕТ'}→{_nv or 'НЕТ'} "
                         f"(дата ПРОГОНА, не правки текста; молчание = не подтверждено)")
        _op, _np = verified_on(o[iid]), verified_on(inv)
        if _op != _np:
            # Пояснение про staging добавляется ТОЛЬКО когда новое место и есть staging.
            # Первая редакция клеила его к любому переходу — и генератор тёплой страницы
            # приписал `staging` инварианту, у которого место `canon` (поймано 2026-07-29
            # чтением сгенерированной страницы). Факт обязан быть про СВОЙ инвариант:
            # общий комментарий внутри частного факта читается как часть факта.
            _tail = (" staging = снимок канона: настоящий код и данные, но НЕ боевая база; "
                     "описывать как есть, НЕ как «проверено на живой системе»"
                     if _np == "staging" else
                     " canon = боевая база владельца."
                     if _np == "canon" else "")
            facts.append(
                f"{iid}: место подтверждения {_op or 'НЕ НАЗВАНО'}→{_np or 'НЕ НАЗВАНО'}."
                + _tail)
        _opr, _npr = probes_of(o[iid]), probes_of(inv)
        if _opr != _npr:
            facts.append(
                f"{iid}: носитель доказательства {_opr or 'НЕ НАЗВАН'}→{_npr or 'НЕ НАЗВАН'} — "
                f"файл пробы, которым снят статус. Названный носитель значит, что механизм "
                f"ВИДИТ эту связь; до 2026-07-29 ссылка жила в комментарии и была невидима")
    for iid in o:
        if iid not in n:
            facts.append(f"− инвариант {iid} удалён")
    if facts:
        return facts
    # working==HEAD: старую версию страницы не восстановить — честно об этом
    return ["запись реестра не менялась vs git HEAD (регенерация без смены смысла; "
            "если смысл менялся — он уже в истории: git log -p subsystem_intent.yaml)"]


# ── Сторож подъёма статуса (CLAUDE.md §13, клауза условия закрытия; 2026-09-21) ──────────
# Норма: по критерию, который предложил агент (`closes_when_by: claude`), агент обещание
# НЕ закрывает. Утром 21.09 норму записали текстом, и в тот же день её нарушили ДВАЖДЫ
# (4fbbe31 и f83845a) — оба раза поймано чтением свода уже ПОСЛЕ коммита. Сторож судит
# ПЕРЕХОД, а не состояние: у записи со статусом holds поля `closes_when` уже нет, авторство
# критерия ушло вместе с ним, и по одному снимку реестра нарушение не видно вовсе.
CRITERION_AUTHORS_MAY_CLOSE = frozenset({"owner"})


def unapproved_promotions(old_entries: "list[dict] | None",
                          new_entries: "list[dict] | None") -> list[str]:
    """Подъёмы до holds, сделанные НЕ по критерию владельца. Чистая: два снимка реестра.

    Нарушение = инвариант был не-holds, стал holds, а критерий закрытия в СТАРОМ снимке
    принадлежал не владельцу (в т.ч. отсутствовал: молчание — не утверждение). Законный
    путь — два коммита: сначала `closes_when_by: owner` (утверждение критерия — видимый
    акт с автором), потом подъём.

    ЧЕГО НЕ ВИДИТ (названо, не спрятано): правдивость отметки `owner` — агент может
    поставить её сам; сторож делает это отдельным называемым коммитом, а не невозможным.
    Инвариант, РОДИВШИЙСЯ holds, не судится — критерия у него не было никогда.
    """
    old = {(e.get("id"), i.get("id")): i
           for e in (old_entries or []) for i in e.get("invariants", [])}
    out = []
    for e in new_entries or []:
        for inv in e.get("invariants", []):
            was = old.get((e.get("id"), inv.get("id")))
            if was is None or inv.get("status") != "holds" or was.get("status") == "holds":
                continue
            author = str(was.get("closes_when_by") or "").strip() or "НЕ НАЗВАН"
            if author not in CRITERION_AUTHORS_MAY_CLOSE:
                out.append(
                    f"{e.get('id')}::{inv.get('id')}: {was.get('status')}→holds по критерию, "
                    f"автор которого — {author}. Сначала утверди критерий у владельца и "
                    f"запиши `closes_when_by: owner` ОТДЕЛЬНЫМ коммитом (CLAUDE.md §13)")
    return out


def registry_at(rev: str, root: "Path | None" = None) -> "list[dict] | None":
    """Реестр из git-ревизии (`HEAD`, `:` = индекс, `<sha>^`). None — файла там нет."""
    import subprocess
    spec = ":subsystem_intent.yaml" if rev == ":" else f"{rev}:subsystem_intent.yaml"
    r = subprocess.run(["git", "show", spec], cwd=str(root or ROOT),
                       capture_output=True, text=True)
    return (yaml.safe_load(r.stdout) or []) if r.returncode == 0 else None


def status_laundering(entry: dict, page_text: str, lang: str = "ru") -> list[str]:
    """«Отмывающие» формулировки: инвариант status!=holds, а на странице его
    запрещённая утвердительная форма (warm_guard.absent). Пусто = чисто.

    lang="en" судит английский перевод по warm_guard.absent_en (28.09, нить explain-en):
    русские фразы в английском тексте не встретятся никогда, и сторож молчал бы всегда.
    Паритет absent/absent_en держит test_intent_warm_guard_en."""
    key = "absent" if lang == "ru" else f"absent_{lang}"
    low = page_text.lower()
    hits: list[str] = []
    # non-holds ∪ инварианты с названными границами: `holds` с границей отмывается ровно
    # так же — «работает» вместо «работает в проверенных пределах». Дедуп по id, порядок
    # реестра сохраняется.
    _seen: set = set()
    _scope = [i for i in non_holds_invariants(entry) + limited_invariants(entry)
              if not (i["id"] in _seen or _seen.add(i["id"]))]
    for inv in _scope:
        for phrase in (inv.get("warm_guard") or {}).get(key, []):
            if phrase.lower() in low:
                hits.append(f"{inv['id']}: «{phrase}»")
    return hits
