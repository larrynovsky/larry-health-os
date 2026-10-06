#!/usr/bin/env python3.11
"""
weekly_digest.py — еженедельный дайджест изменений системы для тенантов.

Домен: «коммиты недели → человеческий текст + вердикт гейта + файл в outputs/».
НЕ шлёт в Telegram (доставка — jobs/scheduled), НЕ читает чужие БД.
План: plans/PLAN_weekly_digest_2026-09-05.md. Эксплуатация («почему не пришёл»,
перегенерация, тон): docs/how-to/weekly_digest.md.

  python3.11 weekly_digest.py --dry-run [--week 2026-W36]   # печать, без файла
  python3.11 weekly_digest.py --week 2026-W36               # outputs/weekly_digest/<week>.json
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
from dataclasses import dataclass, asdict
from _time_inject import get_now  # seam: часы через контракт, не datetime.now()
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).parent
OUT_DIR = ROOT / "outputs" / "weekly_digest"
PROMPT_PATH = ROOT / "data" / "prompts" / "weekly_digest.md"
log = logging.getLogger("weekly_digest")

# Модели: hai_core тянет health_db → на MacBook падает по R1/R2 (тот же приём, что doc_agent).
_MODEL_FALLBACK = {"sonnet": "claude-sonnet-4-6", "haiku": "claude-haiku-4-5"}
try:
    import hai_core as _hc

    def _model(role: str) -> str:
        return _hc.get_model(role)
except Exception:  # noqa: BLE001 — R1/R2 raise на не-Studio хостах
    def _model(role: str) -> str:
        return _MODEL_FALLBACK[role]

# Файлы-«витрины»: коммит, трогающий их, меняет то, что тенант видит или получает.
# Дом — system_config['digest.visible_hints'] (value_json, §9); кортеж ниже — код-дефолт
# при недоступной БД (MacBook dry-run), не второй дом.
_VISIBLE_HINTS_DEFAULT = ("jobs/scheduled", "gp_agent", "gp_context", "brief_", "dashboard", "assessment",
                 "safety_net", "telegram_bot", "handlers/", "bot/",
                 "monthly_consilium", "hai_chat", "notify", "trend_alerts", "consult")

_THREAD_RE = [
    re.compile(r"docs\(handoff\):\s*([\w\-]+)"),
    re.compile(r"plan\(([\w\-]+)\)"),
    re.compile(r"\[нить\s+([\w\-]+)\]"),
    re.compile(r"\bнить\s+([a-z][\w\-]{3,})"),      # только латинские имена нитей
]


@dataclass
class Commit:
    sha: str
    day: str
    subject: str
    body: str
    files: list[str]


@dataclass
class GateVerdict:
    verdict: str            # pass | blocked
    kind: str               # "" | lexicon | judge | guard | translation
    hits_class: list[str]   # классы терминов, не сами значения


# ── Сбор ──────────────────────────────────────────────────────────────────────

def _week_bounds(week: str) -> tuple[date, date]:
    """ISO-неделя → (понедельник, воскресенье)."""
    y, w = week.split("-W")
    mon = date.fromisocalendar(int(y), int(w), 1)
    return mon, mon + timedelta(days=6)


def _collect(since: date, until: date) -> list[Commit]:
    # Разделитель записи стоит В НАЧАЛЕ, а тело закрыто своим \x1f — и то и другое
    # обязательно. Прежняя редакция ставила %x1e в КОНЕЦ формата и разбирала тело по
    # эвристике «строка со слэшем — это файл». `--name-only` печатает список файлов ПОСЛЕ
    # формата, то есть после разделителя, и он доставался следующей записи: замер 14.09 на
    # живой истории дал 97 «коммитов» вместо двадцати, у большинства sha равнялась обрезку
    # имени файла ('BACKLOG', 'CLAUDE.'), а в files попадали строки тела (блок ponytail —
    # там есть слэши). Наружу это не выходило никак: дайджест всё равно печатал текст,
    # просто относил коммиты не к тем нитям и считал «витринность» по чужому списку файлов.
    # \x1f и \x1e в именах файлов и в тексте коммита невозможны, поэтому разбор однозначный
    # и эвристика больше не нужна.
    fmt = "%x1e%H%x1f%ad%x1f%s%x1f%b%x1f"
    raw = subprocess.run(
        ["git", "log", f"--since={since}", f"--until={until + timedelta(days=1)}",
         "--date=short", f"--format={fmt}", "--name-only"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout
    out = []
    for rec in raw.split("\x1e"):
        if not rec.strip():
            continue
        parts = rec.split("\x1f")
        if len(parts) < 5:
            log.warning("_collect: запись git не разобралась (%d полей): %r", len(parts), rec[:120])
            continue
        sha, day, subject, body, tail = parts[0], parts[1], parts[2], parts[3], parts[4]
        files = [l for l in tail.splitlines() if l.strip()]
        out.append(Commit(sha.strip()[:7], day.strip(), subject.strip(), body.strip(), files))
    return out


def _thread_name(c: Commit) -> str:
    for rx in _THREAD_RE:
        m = rx.search(c.subject) or rx.search(c.body)
        if m:
            return m.group(1).lower()
    for f in c.files:                                   # нить по пути handoff/plans
        m = re.match(r"docs/handoff/([\w\-]+)/", f) or re.match(r"plans/PLAN_([\w\-]+?)_20", f)
        if m:
            return m.group(1).lower().replace("_", "-")
    head = c.subject.split(":")[0].strip()
    return head.lower() if 0 < len(head) <= 24 and " " not in head else "прочее"


def thread_last_activity(as_of: date, lookback_days: int = 120) -> dict[str, date]:
    """slug нити → дата последнего коммита, который к ней отнесён.

    ПОЧЕМУ ЗДЕСЬ, А НЕ В НОВОМ МОДУЛЕ. Способность «коммит → имя нити» уже живёт в этом
    файле (`_thread_name`: четыре признака в заголовке и теле плюс путь `docs/handoff/<slug>/`
    и `plans/PLAN_<slug>_20…`). Второй такой разбор в соседнем модуле разошёлся бы с этим
    молча — и отчёт назвал бы застрявшей нить, по которой вчера был коммит. Дом один,
    читателей два: дайджест недели и производитель находок «нить застряла» (night_cycle).

    Граница: молчание git — не доказательство простоя. Нить могли вести в дереве, не
    коммитя; поэтому находка кладётся владельцу как ВОПРОС («добить или закрыть»), а не
    как вердикт. Нить, не появившаяся ни в одном коммите за окно, в ответе отсутствует —
    отличить «давно не трогали» от «никогда не существовала» по одному git нельзя, и
    подставлять сюда дату из другого источника значило бы смешать два предмета."""
    commits = _collect(as_of - timedelta(days=lookback_days), as_of)
    last: dict[str, date] = {}
    for c in commits:
        try:
            day = date.fromisoformat(c.day)
        except ValueError:      # формат даты git внезапно другой — молча пропускать нельзя
            log.warning("thread_last_activity: неразбираемая дата коммита %s: %r", c.sha, c.day)
            continue
        slug = _thread_name(c)
        if slug == "прочее":
            continue
        if slug not in last or day > last[slug]:
            last[slug] = day
    return last


def _group_threads(commits: list[Commit]) -> dict[str, list[Commit]]:
    threads: dict[str, list[Commit]] = {}
    for c in commits:
        threads.setdefault(_thread_name(c), []).append(c)
    return threads


def _visible_hints() -> tuple[str, ...]:
    try:
        import config_db
        v = config_db.get_config("digest.visible_hints")
        if isinstance(v, list) and v:
            return tuple(v)
    except Exception as e:  # noqa: BLE001 — БД недоступна (MacBook) → код-дефолт
        log.warning("visible_hints: system_config недоступен (%s) — код-дефолт", type(e).__name__)
    return _VISIBLE_HINTS_DEFAULT


def _is_visible(commits: list[Commit]) -> bool:
    hints = _visible_hints()
    return any(h in f for c in commits for f in c.files for h in hints)


# ── Скраб секретных путей до промпта (WSTG-INFO-05; гард llm_client — второй рубеж) ──

def _scrub_secret_paths(text: str) -> str:
    return re.sub(r"[\w~/.\-]*\.health_secrets[\w/.\-]*", "<secret-path>", text)


MAJOR_MIN_COMMITS = 3   # нить меньше — «доделка», решает код, а не модель (замечание владельца 05.09)


def _major_threads(threads: dict[str, list[Commit]], k: int = 3) -> list[str]:
    """Обязательные сюжеты: k самых крупных нитей (кроме «прочее»). Выбор — код, не модель."""
    cands = [(n, len(cs)) for n, cs in threads.items() if n != "прочее" and len(cs) >= MAJOR_MIN_COMMITS]
    return [n for n, _ in sorted(cands, key=lambda x: -x[1])[:k]]


def _threads_as_text(threads: dict[str, list[Commit]]) -> str:
    majors = _major_threads(threads)
    parts = ["ОБЯЗАТЕЛЬНЫЕ СЮЖЕТЫ (в этом порядке): " + (", ".join(majors) or "нет — только доделки")]
    for name, cs in sorted(threads.items(), key=lambda kv: -len(kv[1])):
        vis = "ВИДИМО" if _is_visible(cs) else "внутреннее"
        size = "КРУПНОЕ" if name in majors else "доделка"
        parts.append(f"### нить: {name} · {len(cs)} коммитов · {vis} · {size}")
        for c in cs:
            parts.append(f"- {c.day} {c.subject}")
            if c.body and size == "КРУПНОЕ":       # доделкам тело не показываем: драма живёт в телах
                parts.append("  " + c.body[:400].replace("\n", " "))
    return _scrub_secret_paths("\n".join(parts))


# ── LLM ───────────────────────────────────────────────────────────────────────

def _client():
    import llm_client
    return llm_client.guarded_client()


def _render_digest(threads: dict[str, list[Commit]], week: str) -> str:
    system = PROMPT_PATH.read_text(encoding="utf-8")
    since, until = _week_bounds(week.split(" ")[0])
    user = (f"Неделя {week}: {since.strftime('%d.%m')}–{until.strftime('%d.%m.%Y')}. "
            f"Коммиты, сгруппированные в нити:\n\n{_threads_as_text(threads)}")
    r = _client().messages.create(task="weekly_digest._render_digest", model=_model("sonnet"), max_tokens=1500, system=system,
                                  messages=[{"role": "user", "content": user}])
    return "".join(b.text for b in r.content if getattr(b, "type", "") == "text").strip()


_JUDGE = ("Ниже текст, который уйдёт нескольким людям с разной медициной. Ответь одним словом "
          "ДА или НЕТ. ДА — только если в тексте есть ЗНАЧЕНИЕ анализа, ДАТА измерения, название "
          "ПРЕПАРАТА, ДИАГНОЗ, ФАМИЛИЯ врача/лаборатории или имя пациента, либо фраза вида «у тебя/твой "
          "<показатель> <значение/динамика>». Описание того, что система умеет, за чем следит и как "
          "раньше ошибалась (включая «тревога по такому-то показателю молчала») — НЕТ.\n\nТЕКСТ:\n")


def _judge_leaks(text: str) -> bool:
    r = _client().messages.create(task="weekly_digest._judge_leaks", model=_model("haiku"), max_tokens=5,
                                  messages=[{"role": "user", "content": _JUDGE + text}])
    ans = "".join(b.text for b in r.content if getattr(b, "type", "") == "text").strip().upper()
    return ans.startswith("ДА")


_FIDELITY = ("Ниже СЫРЬЁ (записи об изменениях системы) и АБЗАЦ дайджеста, написанный по нему. "
             "Ответь одним словом ДА или НЕТ: утверждает ли абзац что-то, чего в сырье нет, или "
             "искажает то, что там есть (другой механизм, другое условие, другой эффект)? Метафоры и "
             "ирония — не искажение; проверяй только предметные утверждения.\n\nСЫРЬЁ:\n{raw}\n\nАБЗАЦ:\n{par}")


# Шапка и хвост дайджеста на обоих языках (нить digest-lang, 28.09): структура судится
# по ним, и английский текст без своих меток прошёл бы мимо исключения шапки из суда чисел.
_HEADS = ("*Что изменилось", "*What changed")
_TAILS = ("*Ещё починили", "*Also fixed")


def _sujets(text: str) -> list[str]:
    """Абзацы-сюжеты: заголовок *…* + текст, без шапки и хвоста «Ещё починили»."""
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
    return [b for b in blocks[1:] if b.startswith("*") and not b.startswith(_TAILS)]


def _judge_fidelity(text: str, raw_major: str) -> list[int]:
    """Номера сюжетов (1-based), которые судья счёл искажающими сырьё."""
    bad = []
    for i, par in enumerate(_sujets(text), 1):
        r = _client().messages.create(task="weekly_digest._judge_fidelity", model=_model("haiku"), max_tokens=5,
                                      messages=[{"role": "user", "content": _FIDELITY.format(raw=raw_major, par=par)}])
        ans = "".join(b.text for b in r.content if getattr(b, "type", "") == "text").strip().upper()
        if ans.startswith("ДА"):
            bad.append(i)
    return bad


# ── Гейт: детерминированный лексикон ─────────────────────────────────────────

_VALUE_UNIT = re.compile(r"\d+[.,]?\d*\s?(?:ng/[dm]?l|mg/[dm]?l|g/[dm]?l|мкг|ммоль|мкмоль|нг/мл|мг/л|Ед/л|U/L|IU/L|pg/ml|%\s*от)", re.I)
_MEAS_DATE = re.compile(r"\b\d{1,2}\.\d{2}\.20\d{2}\b")


def _lexicon() -> frozenset[str]:
    """Термины, привязывающие текст к человеку: снятые литералы diagnosis_guard (фамилии,
    диплотипы, статусы лечения). Аналиты канона НЕ входят — владелец принял, что
    возможности называются по имени (решение 2026-09-05). Per-tenant словарь
    (problem_list, treatment_db, doc_patterns) добавляется на Studio, где есть БД."""
    terms: set[str] = set()
    try:
        # Личные слова (фамилии владельца и врачей, диагноз и режим) — из словаря pii_census:
        # до 2026-09-23 они приходили отсюда же через diagnosis_guard.SITES литералами в коде.
        import pii_census
        terms.update(t.lower() for t in pii_census.literals(["surname", "doctor", "clinical"])
                     if re.fullmatch(r"[а-яёa-z ]{4,}", t.lower()))
    except Exception as e:  # noqa: BLE001
        log.warning("lexicon: словарь pii_census недоступен: %s", e)
    try:
        import diagnosis_guard
        for lits in diagnosis_guard.SITES.values():
            terms.update(l.lower() for l in lits if re.fullmatch(r"[а-яёa-z ]{4,}", l.lower()))
    except Exception as e:  # noqa: BLE001
        log.warning("lexicon: diagnosis_guard недоступен: %s", e)
    terms.discard("онкомаркер")   # возможность, не факт о человеке (решение владельца)
    return frozenset(terms)


_NUM = re.compile(r"\d+(?:[.,]\d+)?")


def _unsupported_numbers(text: str, raw: str) -> list[str]:
    """Числа в тексте, которых нет в сырье (коммитах) — модель дорисовала (dry-run 05.09:
    «три клика», «сотни мегабайт», «двое суток»). Числа словами не ловятся — граница названа."""
    have = {n.replace(",", ".") for n in _NUM.findall(raw)}
    return sorted({n for n in _NUM.findall(text) if n.replace(",", ".") not in have})


BANNED_PATH = ROOT / "data" / "prompts" / "weekly_digest_banned_terms.txt"


def _banned_terms() -> frozenset[str]:
    try:
        return frozenset(l.strip().lower() for l in BANNED_PATH.read_text(encoding="utf-8").splitlines()
                         if l.strip() and not l.startswith("#"))
    except FileNotFoundError:
        log.warning("banned_terms: %s нет — класс jargon в гейте слеп", BANNED_PATH.name)
        return frozenset()


def gate_text(text: str, lex: frozenset[str], raw: str = "") -> GateVerdict:
    """Первая строка — заголовок с датами недели, он не судится по дате/числам."""
    text = text.partition("\n")[2] if text.startswith(_HEADS) else text
    low = text.lower()
    hits = sorted({t for t in lex if t in low})
    classes = [f"lexicon:{t[:3]}…" for t in hits]
    if _VALUE_UNIT.search(text):
        classes.append("value_with_unit")
    if _MEAS_DATE.search(text):
        classes.append("measurement_date")
    jargon = sorted(t for t in _banned_terms() if re.search(r"(?<![а-яa-z])" + re.escape(t) + r"[а-яa-z]{0,2}(?![а-яa-z])", low))
    if jargon:
        classes.append("jargon:" + ",".join(jargon))
    if raw:
        bad = _unsupported_numbers(text, raw)
        if bad:
            classes.append("unsupported_numbers:" + ",".join(bad))
    return GateVerdict("blocked" if classes else "pass", "lexicon" if classes else "", classes)


# ── Per-tenant: словарь из СВОЕЙ БД, вердикт-файл, outbox ────────────────────

def tenant_tag() -> str:
    """Имя каталога данных тенанта: health | health_partner | health_<имя>."""
    d = os.environ.get("HEALTH_DATA_DIR")
    return Path(d).name if d else "health"


def tenant_lexicon() -> frozenset[str]:
    """Термины, привязывающие текст к ЭТОМУ пациенту, из его БД через существующих читателей
    (дубль-гейт 05.09: свой SQL по problem_list/medications/doc_patterns был бы третьим читателем
    каждой таблицы). Читается только своя БД (R1). Недоступна → пусто с предупреждением:
    пустой словарь = гейт слепее, но не молчит (лог)."""
    terms: set[str] = set()
    readers = (
        ("problem_list", lambda: [r.get("title") for r in __import__("problems_db").get_problem_list()]),
        ("medications", lambda: [r.get("name") for r in __import__("treatment_db").get_medications(include_proposed=True)]),
        ("doc_patterns", lambda: [r.get("pattern") for r in __import__("config_db").get_doc_patterns()]),
    )
    for name, read in readers:
        try:
            terms.update(t.lower() for t in read() if t)
        except Exception as e:  # noqa: BLE001 — R1/R2 вне Studio или нет таблицы у тенанта
            log.warning("tenant_lexicon: %s недоступен (%s)", name, type(e).__name__)
    return frozenset(t for t in terms if len(t) >= 4)


def _digest_path(week: str) -> Path:
    return OUT_DIR / f"{week}.json"


def _verdict_path(week: str, tag: str) -> Path:
    return OUT_DIR / f"{week}.gate.{tag}.json"


def read_digest(week: str) -> dict | None:
    p = _digest_path(week)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def write_tenant_verdict(week: str, tag: str | None = None) -> GateVerdict | None:
    """Гейт словарём ЭТОГО тенанта над готовым текстом; пишет <week>.gate.<tag>.json.
    Идемпотентно: файл есть → не пересчитывается. Нет дайджеста → None (ждём)."""
    tag = tag or tenant_tag()
    d = read_digest(week)
    if d is None or d.get("text") is None:
        return None
    vp = _verdict_path(week, tag)
    if vp.exists():
        return GateVerdict(**json.loads(vp.read_text(encoding="utf-8")))
    import i18n
    text = text_for(d, i18n.lang_of())
    v = (gate_text(text, tenant_lexicon()) if text is not None
         else GateVerdict("blocked", "translation", d.get("en_problems") or ["no_translation"]))
    tmp = vp.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(v), ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, vp)
    return v


def expected_tags() -> list[str]:
    """Теги всех реальных тенантов — тот же предикат, что у датчика cross-tenant
    (secrets_paths.tenant_db_paths). НЕ через integrity_tests: его импорт исполняет весь монитор
    (check() зовёт fn на импорте) — первый прогон outbox 05.09 09:32 завис на этом."""
    import secrets_paths as _sp
    tags = {Path(p).parent.parent.name for p in _sp.tenant_db_paths()}
    tags.add(tenant_tag())
    return sorted(tags)


def verdicts(week: str, tags: list[str]) -> dict[str, str | None]:
    """Вердикты ожидаемых тенантов (нет файла → None = ждём) ПЛЮС любой вердикт-файл недели в папке.
    С 30.09 тенанты живут в двух рантаймах (владелец — контейнер, партнёр — хост), и expected_tags
    каждого видит только себя; общая папка дайджеста (том, digest-container 05.10) несёт вердикты
    обоих. Чужой файл с blocked → блок всем («блок любого — блок всем», shared_text_gate_fail_closed).
    Чего это НЕ возвращает: ожидания соседа, чей файл ещё не лёг, — его тег здесь неизвестен."""
    out = {}
    for vp in sorted(OUT_DIR.glob(f"{week}.gate.*.json")):
        try:
            out[vp.name[len(week) + len(".gate."):-len(".json")]] = json.loads(
                vp.read_text(encoding="utf-8"))["verdict"]
        except (ValueError, KeyError, OSError):   # битый чужой вердикт — не pass: fail-closed
            out[vp.name] = "blocked"
    for t in tags:
        out.setdefault(t, None)
    return out


SEND_HOUR = 9        # вс 09:00 местного тенанта


def due(now: datetime, digest: dict | None, verd: dict[str, str | None], last_sent: str | None) -> str:
    """Чистое решение outbox-читателя. Возвращает: send | wait | blocked | done | not_yet.
    now — местное время тенанта. Неделя дайджеста = ISO-неделя, содержащая now (сб-генерация)."""
    if digest is None:
        return "wait"
    week = digest["week"]
    if last_sent and last_sent >= week:
        return "done"
    if digest.get("text") is None or digest.get("gate", {}).get("verdict") != "pass":
        return "blocked"
    if any(v == "blocked" for v in verd.values()):
        return "blocked"
    if now.isoweekday() != 7 or now.hour < SEND_HOUR:
        return "not_yet"
    if any(v is None for v in verd.values()):
        return "wait"
    return "send"


def alarm(decision: str, now: datetime, digest: dict | None, out_dir_exists: bool) -> bool:
    """Звать ли оператора на этом тике (раз в неделю — решает вызывающий меткой alerted_week).
    blocked — сразу; wait после 09:00 вс — когда текст есть, а вердикта соседа нет, ИЛИ когда
    текста нет, хотя папка генератора у этого читателя есть (генератор не бежал / не дописал).
    Папки нет — у установки нет генератора (посторонний контейнер), молчание законно; том
    владельца стережёт test_docker_render. До 05.10 «текста нет» молчало всегда — W40 потерян."""
    if decision == "blocked":
        return digest is not None
    if decision != "wait" or now.isoweekday() != 7 or now.hour < SEND_HOUR:
        return False
    return digest is not None or out_dir_exists


def current_week(now: datetime | None = None) -> str:
    y, w, _ = (now or get_now()).isocalendar()
    return f"{y}-W{w:02d}"


# ── Сборка ───────────────────────────────────────────────────────────────────

# ── Язык получателя (нить digest-lang, 28.09) ────────────────────────────────
# Дайджест один на всех тенантов и пишется по-русски; все судьи (утечка, верность сырью)
# работают над русским. Английский — ПЕРЕВОД уже прошедшего гейт текста, не второе
# сочинение: новых утверждений в нём быть не должно. Это проверяется механически (числа,
# абзацы, сюжеты, шапка, кириллица) и тем же gate_text; судьи-модели по-английски его не
# читают — граница названа. Нет перевода → английский тенант даёт blocked «translation»,
# и тот же путь алерта, что у любого блока.
_TRANSLATE = (
    "Translate this weekly digest from Russian into plain English for a reader who is not "
    "technical. Keep the structure exactly: the same paragraphs in the same order; every *bold* "
    "heading stays a *bold* heading. The first line starts with \"*What changed\" (keep its dates "
    "exactly). A paragraph that starts with \"*Ещё починили\" starts with \"*Also fixed\". Keep every "
    "number exactly as written, in the same notation. Add nothing, drop nothing, no notes. Address "
    "the reader as \"you\". Output only the translation.\n\n")
_CYR = re.compile(r"[А-Яа-яЁё]")


def _translate(text: str) -> str:
    r = _client().messages.create(task="weekly_digest._translate", model=_model("sonnet"), max_tokens=2000,
                                  messages=[{"role": "user", "content": _TRANSLATE + text}])
    return "".join(b.text for b in r.content if getattr(b, "type", "") == "text").strip()


def translation_problems(ru: str, en: str) -> list[str]:
    """Механическая сверка перевода с оригиналом: что перевод добавил, потерял или не перевёл."""
    def blocks(t):
        return len([b for b in re.split(r"\n\s*\n", t) if b.strip()])
    def nums(t):
        return sorted(n.replace(",", ".") for n in _NUM.findall(t))
    out = []
    if _CYR.search(en):
        out.append("cyrillic")
    if blocks(ru) != blocks(en):
        out.append("paragraphs")
    if len(_sujets(ru)) != len(_sujets(en)):
        out.append("sujets")
    if nums(ru) != nums(en):
        out.append("numbers")
    if ru.startswith(_HEADS) and not en.startswith(_HEADS[1]):
        out.append("header")
    return out


def _english(text: str, lex: frozenset[str], raw: str) -> tuple[str | None, list[str]]:
    try:
        en = _translate(text)
    except Exception as e:  # noqa: BLE001 — перевод не роняет русский дайджест; причина в записи
        log.error("translate failed: %s", type(e).__name__)
        return None, [type(e).__name__]
    probs = translation_problems(text, en) or gate_text(en, lex, raw).hits_class
    return (None if probs else en), probs


def text_for(digest: dict, lang: str) -> str | None:
    """Текст недели на языке человека; английского нет — None (не русский вместо него)."""
    return digest.get("text") if lang == "ru" else digest.get("text_" + lang)


def build(week: str, *, dry_run: bool) -> dict:
    since, until = _week_bounds(week)
    commits = _collect(since, until)
    threads = _group_threads(commits)
    result = {"week": week, "range": f"{since}..{until}", "commits": len(commits),
              "threads": {k: len(v) for k, v in threads.items()},
              "generated_at": get_now().isoformat(timespec="seconds"),
              "model": _model("sonnet"), "text": None, "gate": None}
    if not commits:
        result["gate"] = asdict(GateVerdict("pass", "", []))
        result["text"] = ""
        return result
    lex = _lexicon()
    raw = _threads_as_text(threads)
    majors = _major_threads(threads)
    raw_major = _threads_as_text({n: threads[n] for n in majors}) if majors else raw

    def _judge_all(t: str) -> GateVerdict:
        v = gate_text(t, lex, raw)
        if v.verdict == "pass" and _judge_leaks(t):
            v = GateVerdict("blocked", "judge", ["judge:yes"])
        if v.verdict == "pass":
            bad = _judge_fidelity(t, raw_major)
            if bad:
                v = GateVerdict("blocked", "fidelity", [f"fidelity:{i}" for i in bad])
        return v

    try:
        text = _render_digest(threads, week)
        v = _judge_all(text)
        if v.verdict == "blocked":   # один повтор с поправкой, затем отказ
            log.warning("gate blocked (%s) — регенерирую с поправкой", v.hits_class)
            threads_note = dict(threads)
            text2 = _render_digest(threads_note, week + f" (ПОВТОР: предыдущий вариант отклонён гейтом: {v.hits_class} — убери факты о конкретном человеке, технические слова, числа, которых нет в сырье, и утверждения, которых нет в нитях)")
            text, v = text2, _judge_all(text2)
    except Exception as e:  # noqa: BLE001 — SecretLeakBlocked/GuardUnavailable/API
        v, text = GateVerdict("blocked", "guard", [type(e).__name__]), ""
        log.error("render failed: %s", type(e).__name__)
    result["gate"] = asdict(v)
    result["text"] = text if v.verdict == "pass" else None
    if result["text"]:
        result["text_en"], result["en_problems"] = _english(text, lex, raw)
    result["_rejected_preview"] = text if v.verdict == "blocked" and dry_run else None
    if not dry_run:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        tmp = OUT_DIR / f"{week}.json.tmp"
        tmp.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, OUT_DIR / f"{week}.json")
        if v.verdict == "blocked":
            _alert_blocked(week, v)   # сразу, в сб 22:xx — не ждать воскресного тика бота
    return result


def _alert_blocked(week: str, v: GateVerdict) -> None:
    """Служебный алерт оператору при блоке: класс причины, без текста и без терминов
    (owner-канал; hits_class несёт только классы и префиксы). Не бросает."""
    try:
        from notify import fault
        fault(f"weekly_digest: blocked ({v.kind})", person_key=None)
    except Exception as e:  # noqa: BLE001 — алерт не роняет генератор
        log.error("_alert_blocked: %s", type(e).__name__)


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--week", default=None, help="ISO-неделя, напр. 2026-W36 (default: текущая)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--tenant-verdict", action="store_true", help="только вердикт словарём этого тенанта")
    a = ap.parse_args(argv)
    week = a.week or current_week()
    if a.tenant_verdict:
        v = write_tenant_verdict(week)
        print(json.dumps(asdict(v) if v else None, ensure_ascii=False))
        return 0 if v and v.verdict == "pass" else 2
    r = build(week, dry_run=a.dry_run)
    print(json.dumps({k: v for k, v in r.items() if k != "text"}, ensure_ascii=False, indent=1))
    print("\n" + "=" * 60 + "\n" + (r["text"] or "<нет текста>") + "\n" + "=" * 60)
    return 0 if r["gate"]["verdict"] == "pass" else 2


if __name__ == "__main__":
    # Одна запускаемая проверка нетривиальной логики (ponytail): группировка + гейт.
    if "--selftest" in sys.argv:
        cs = [Commit("a", "2026-09-01", "docs(handoff): norm-from-documents — снимок", "", ["docs/x.md"]),
              Commit("b", "2026-09-01", "safety_net: пол по величине", "", ["safety_net.py"]),
              Commit("c", "2026-09-01", "x", "", ["tests/t.py"])]
        g = _group_threads(cs)
        assert set(g) == {"norm-from-documents", "safety_net", "x"}, g
        assert _is_visible(g["safety_net"]) and not _is_visible(g["x"])
        lex = frozenset({"в ремиссии", "доктор_икс"})
        assert gate_text("Пациент в ремиссии", lex).verdict == "blocked"
        assert gate_text("CEA 12.5 ng/mL", lex).hits_class == ["value_with_unit"]
        assert gate_text("Тренд онкомаркеров теперь ждёт второго забора", lex).verdict == "pass"
        assert "jargon:коммит" in gate_text("Сделали коммит", lex).hits_class[0] if _banned_terms() else True
        assert gate_text("Каталог логики", lex).verdict == "pass"   # «лог» внутри слова не ловится
        assert _sujets("*Шапка*\n\n*А*\nтекст\n\n*Б*\nтекст\n\n*Ещё починили:* x") == ["*А*\nтекст", "*Б*\nтекст"]
        assert _major_threads({"a": cs * 2, "b": cs, "прочее": cs * 5}) == ["a", "b"]
        from datetime import datetime as _dt
        D = {"week": "2026-W36", "text": "t", "gate": {"verdict": "pass"}}
        sun9, sat = _dt(2026, 9, 6, 9, 0), _dt(2026, 9, 5, 23, 0)
        ok = {"health": "pass", "health_partner": "pass"}
        assert due(sun9, None, ok, None) == "wait"
        assert due(sat, D, ok, None) == "not_yet"
        assert due(_dt(2026, 9, 6, 8, 59), D, ok, None) == "not_yet"
        assert due(sun9, D, ok, None) == "send"
        assert due(sun9, D, {"health": "pass", "health_partner": None}, None) == "wait"
        assert due(sun9, D, {"health": "pass", "health_partner": "blocked"}, None) == "blocked"
        assert due(sun9, {**D, "text": None, "gate": {"verdict": "blocked"}}, ok, None) == "blocked"
        assert due(sun9, D, ok, "2026-W36") == "done"
        assert due(sun9, D, ok, "2026-W35") == "send"
        assert _unsupported_numbers("три клика и 300 МБ, шкала 0 до 100", "score_0_100") == ["300"]
        assert _scrub_secret_paths("файл ~/.health_secrets/anthropic_key") == "файл <secret-path>"
        print("selftest ok")
        sys.exit(0)
    sys.exit(main())
