#!/usr/bin/env python3.11
"""bug_report.py — отчёт о сбое, который посторонняя установка отправляет разработчику.

Решения владельца 19.10 (нить bug-report): человек сам вызывает команду в боте, отчёт уходит
в приёмник владельца, читает его ночной конвейер Claude. Личных данных в отчёте нет ПО
УСТРОЙСТВУ: он собирается только из разрешённых полей, а не вычищается. Вычищать нечем —
у постороннего словарь личных слов пустой (public_zone.zero_means_known_classes_only).

Схема живёт здесь одна: её читают и сборщик, и приёмник (bug_inbox). Ни одного поля со
свободным текстом: каждое — перечисление, число или строка по шаблону. Это закрывает и
утечку, и второй риск — отчёт читает агент с правом на код, и текст внутри отчёта был бы
каналом для чужой инструкции.

Пример (значения выдуманы):
    {"schema": 1, "report_id": "3f9c…:4", "release": "v0.2.1", "platform": "linux/aarch64",
     "category": "report_missing", "connected": {"oura": true, ...},
     "faults": [{"type": "KeyError", "at": ["labs_db.py:build_lab_history_context"], "n": 3, "fp": "7f3a1c"}],
     "screen": ["07:30 jobs.brief.header"]}
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from _time_inject import get_now

SCHEMA = 1
CATEGORIES = ("report_missing", "button_silent", "lab_not_read", "other")
MAX_BYTES = 8192
MAX_FAULT_GROUPS = 10
MAX_TRACE = 20
FAULT_WINDOW_DAYS = 7
MAX_DETAIL = 4
MAX_DETAIL_LENGTH = 120
# Place travels inside each token so the receiver can apply the SAME allowlist without
# changing legacy at/fp (at may contain traceback frames, not the journal's where).
_IDENT = r"[a-z_][a-z_]*"
_DETAIL_RULES = (
    (re.compile(r"run_checks\.[a-z_]+"),
     # Цифры в имени теста — код, не данные (test_uc_a_03_…); параметры [..] не пропускаются.
     re.compile(r"tests/(?:[a-z0-9_]+/)*test_[a-z0-9_]+\.py(?:::(?:Test[A-Za-z0-9_]+|test_[a-z0-9_]+)){1,2}")),
    (re.compile(r"monthly_consilium|consilium_roster"),
     re.compile(r"[a-z_]+:(?:[A-Z][A-Za-z_]*|unanswered)")),
    (re.compile(r"llm_admission"),
     re.compile(_IDENT + r":(?:o[134](?:-[a-z0-9]+)*|(?:claude|gpt|gemini|deepseek|qwen|llama|mistral)(?:[.-][a-z0-9]+)+):"
                r"(?:all_failed|next_not_admitted|removed|call_error)")),
)


def _detail_token_ok(token: object) -> bool:
    if not isinstance(token, str) or len(token) > MAX_DETAIL_LENGTH:
        return False
    place, sep, value = token.partition(":")
    return bool(sep and any(p.fullmatch(place) and v.fullmatch(value) for p, v in _DETAIL_RULES))


def _detail(record: dict) -> list[str]:
    place, values = record.get("where"), record.get("detail")
    if not isinstance(place, str) or not any(p.fullmatch(place) for p, _ in _DETAIL_RULES):
        return []
    if not isinstance(values, list):
        return []
    return list(dict.fromkeys(f"{place}:{v}" for v in values
                             if isinstance(v, str) and _detail_token_ok(f"{place}:{v}")))[:MAX_DETAIL]


# Шаблоны узкие нарочно: чем шире строка, тем больше в неё помещается фраза для читающего агента.
_SHAPES = {"report_id": re.compile(r"[0-9a-f]{16}:\d{1,6}"),
           "release": re.compile(r"v\d{1,3}\.\d{1,3}\.\d{1,3}|local"),
           "platform": re.compile(r"[a-z0-9_]{1,16}/[a-z0-9_]{1,16}")}
_CODE_AT = re.compile(r"[A-Za-z0-9_./-]{1,60}\.py:[A-Za-z_][A-Za-z0-9_<>]{0,60}")
_WHERE = re.compile(r"[a-z_][a-z0-9_]*(\.[A-Za-z0-9_]+){0,4}(\.py)?(:\d{1,5})?")
_EXC = re.compile(r"[A-Z][A-Za-z0-9_]{0,60}")
_TRACE = re.compile(r"\d\d:\d\d [a-z0-9_.]{1,100}")
_FP = re.compile(r"[0-9a-f]{6}")
_INTEGRATIONS = ("oura", "apple_health", "withings", "genome", "calendar", "llm")


def fingerprint(exc_type: str, at: list[str]) -> str:
    """Один сбой — один отпечаток во всех выпусках: тип + «файл:функция», без номеров строк
    (номер строки едет с каждой правкой, и один сбой стал бы новой карточкой в каждом выпуске)."""
    return hashlib.sha256("|".join([exc_type, *at[-3:]]).encode()).hexdigest()[:6]


def _shape(record: dict) -> tuple[str, list[str]]:
    """Тип и место сбоя из записи журнала — только то, что проходит шаблоны. Текст записи
    (`text`) и `tenant` не читаются вовсе: в первом бывает текст исключения, во втором — имя
    каталога ключей, то есть иногда имя человека."""
    exc = record.get("exc_type") or ""
    exc = exc if _EXC.fullmatch(exc) else "none"
    at = [f for f in record.get("frames") or [] if isinstance(f, str) and _CODE_AT.fullmatch(f)]
    if not at:
        where = record.get("where") or ""
        at = [where if _WHERE.fullmatch(where) or _own_code_at(where) else "other"]
    return exc, at


def _own_code_at(where: str) -> bool:
    """«файл.py:функция», который fault сам собирает из tech («handlers/reports.py:cmd_report»),
    — место, только если файл лежит в коде установки: путь с диска пользователя не пройдёт.
    Без этого все такие сбои без кадров сливались в один «none в other» (карточка report:eafa46)."""
    if not _CODE_AT.fullmatch(where):
        return False
    rel = where.split(":", 1)[0]
    # Наружу уходит ИСХОДНАЯ строка, а не разрешённый путь: «Users/maria/../../handlers/x.py»
    # резолвится внутрь кода, но несёт имя с диска. Поэтому только чистый относительный путь.
    if rel.startswith("/") or any(c in ("", ".", "..") for c in rel.split("/")):
        return False
    root = Path(__file__).resolve().parent
    p = (root / rel).resolve()
    return p.is_file() and p.is_relative_to(root)


def faults_from_journal(path: Path, now: datetime | None = None) -> list[dict]:
    """Сбои журнала за окно, сгруппированные по отпечатку; самые частые — первыми."""
    now = now or get_now(timezone.utc)
    since = now - timedelta(days=FAULT_WINDOW_DAYS)
    groups: dict[str, dict] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines:
        try:
            rec = json.loads(line)
            if datetime.fromisoformat(rec["ts"]).astimezone(timezone.utc) < since:
                continue
        except (ValueError, KeyError, TypeError):   # битая строка журнала — не сбой отчёта
            continue
        exc, at = _shape(rec)
        fp = fingerprint(exc, at)
        g = groups.setdefault(fp, {"type": exc, "at": at, "n": 0, "fp": fp})
        g["n"] += 1
        detail = _detail(rec)
        if detail:
            g["detail"] = list(dict.fromkeys([*g.get("detail", []), *detail]))[:MAX_DETAIL]
        rel = rec.get("release")
        if isinstance(rel, str) and _REL.fullmatch(rel) and \
                release_key(rel) > release_key(g.get("rel", "v0.0.0")):
            g["rel"] = rel          # самая новая версия, на которой сбой ещё случался
    return sorted(groups.values(), key=lambda g: -g["n"])[:MAX_FAULT_GROUPS]


_REL = re.compile(r"v\d{1,3}\.\d{1,3}\.\d{1,3}")


def release_key(rel: str) -> tuple:
    """«v0.2.7» → (0, 2, 7); не версия («local», пусто) — (), меньше любой версии."""
    return tuple(int(x) for x in rel[1:].split(".")) if _REL.fullmatch(rel or "") else ()


def build(category: str, *, report_id: str, release: str, platform: str,
          connected: dict[str, bool], faults: list[dict], screen: list[str]) -> dict:
    """Отчёт из разрешённых полей. Всё, что не проходит шаблон, заменяется на 'other' или
    отбрасывается — сборщик не бросает: отчёт нужен именно тогда, когда что-то сломано."""
    def tok(k: str, v: str, fallback: str) -> str:
        return v if isinstance(v, str) and _SHAPES[k].fullmatch(v) else fallback
    report = {
        "schema": SCHEMA,
        "report_id": report_id,                      # рождает установка; не прошёл — schema_problems отобьёт
        "release": tok("release", release, "local"),
        "platform": tok("platform", platform, "other/other"),
        "category": category if category in CATEGORIES else "other",
        "connected": {k: bool(connected.get(k)) for k in _INTEGRATIONS},
        "faults": [dict(g) for g in faults[:MAX_FAULT_GROUPS]],
        "screen": [s for s in screen if isinstance(s, str) and _TRACE.fullmatch(s)][-MAX_TRACE:],
    }

    # Optional diagnostics must not make a formerly deliverable report too large.
    for g in reversed(report["faults"]):
        if len(json.dumps(report, ensure_ascii=False).encode()) <= MAX_BYTES:
            break
        g.pop("detail", None)
    return report


def schema_problems(report: object) -> list[str]:
    """Пусто — отчёт проходит схему. Судит и сборщик (перед отправкой), и приёмник (на входе):
    приёмник не верит, что отправитель — наш сборщик. Лишнее поле — отказ, а не пропуск."""
    if not isinstance(report, dict):
        return ["не объект"]
    p: list[str] = []
    want = {"schema", "report_id", "release", "platform", "category", "connected", "faults", "screen"}
    if set(report) != want:
        # имён чужих полей не повторяем: отказ не должен эхом нести присланный текст
        p.append(f"состав полей: лишних {len(set(report) - want)}, нет {sorted(want - set(report))}")
        return p
    if report["schema"] != SCHEMA:
        p.append("schema")
    for k, rx in _SHAPES.items():
        if not (isinstance(report[k], str) and rx.fullmatch(report[k])):
            p.append(k)
    if report["category"] not in CATEGORIES:
        p.append("category")
    c = report["connected"]
    if not (isinstance(c, dict) and set(c) == set(_INTEGRATIONS) and all(v is True or v is False for v in c.values())):
        p.append("connected")
    f = report["faults"]
    if not (isinstance(f, list) and len(f) <= MAX_FAULT_GROUPS and all(_fault_ok(g) for g in f)):
        p.append("faults")
    s = report["screen"]
    if not (isinstance(s, list) and len(s) <= MAX_TRACE and all(isinstance(x, str) and _TRACE.fullmatch(x) for x in s)):
        p.append("screen")
    if not p and len(json.dumps(report, ensure_ascii=False).encode()) > MAX_BYTES:
        p.append("размер")
    return p


def _fault_ok(g: object) -> bool:
    if not (isinstance(g, dict) and set(g) - {"rel", "detail"} == {"type", "at", "n", "fp"}):
        return False
    if "rel" in g and not (isinstance(g["rel"], str) and _REL.fullmatch(g["rel"])):
        return False
    if "detail" in g and not (isinstance(g["detail"], list) and 1 <= len(g["detail"]) <= MAX_DETAIL
                              and all(_detail_token_ok(t) for t in g["detail"])):
        return False
    at_ok = (isinstance(g["at"], list) and 1 <= len(g["at"]) <= 6
             and all(isinstance(a, str) and (_CODE_AT.fullmatch(a) or _WHERE.fullmatch(a) or a == "other")
                     for a in g["at"]))
    return (isinstance(g["type"], str) and (_EXC.fullmatch(g["type"]) or g["type"] == "none") and at_ok
            and type(g["n"]) is int and 1 <= g["n"] <= 100000
            and isinstance(g["fp"], str) and bool(_FP.fullmatch(g["fp"])))


_MARKERS = {"oura": ("oura_token", "oura_oauth.json"), "apple_health": ("hae_ingest_token",),
            "withings": ("withings_oauth.json",), "calendar": ("google_calendar_token.json",),
            "llm": ("anthropic_key", "openai_key", "gemini_key")}


def _connected() -> dict[str, bool]:
    """Подключено ли — по НАЛИЧИЮ файла, значение не читается никогда (урок C-104)."""
    from secrets_paths import owner_secrets_dir, secrets_dir
    import health_db
    dirs = [Path(secrets_dir()), Path(owner_secrets_dir()), Path(health_db.DB_PATH).parent]
    out = {k: any((d / n).exists() for d in dirs for n in names) for k, names in _MARKERS.items()}
    import genome_db
    out["genome"] = genome_db.genome_present()
    return out


def collect(category: str, report_id: str) -> dict:
    """Отчёт этой установки сейчас: версия, платформа, подключения, сбои недели, след экрана."""
    import os
    import platform
    import notify
    import screen_trace
    return build(category, report_id=report_id,
                 release=os.environ.get("HEALTH_RELEASE", ""),
                 platform=f"{platform.system().lower()}/{platform.machine().lower()}",
                 connected=_connected(), faults=faults_from_journal(notify.faults_journal()),
                 screen=screen_trace.recent())
