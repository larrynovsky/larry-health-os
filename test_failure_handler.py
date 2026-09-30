#!/usr/bin/env python3.11
# INTENT: night_test_triage
"""
test_failure_handler.py — обработчик failures из ночного test suite.

Вызывается из `run_full_test_suite.sh` после генерации `summary.json` и
junit.xml-ов слоёв. См. TEST_ARCHITECTURE.md §11–13.

Делает (в порядке слоёв):

**Уровень A — Triage (детерминированный, без LLM):**
- ловит retry-able паттерны в выводе тестов: `database is locked` → retry один раз;
- помечает flaky (Anthropic timeout, iCloud-evicted) — не алертит;
- идемпотентен по дню через `logs/test_failure_done_{date}.flag`.

**Уровень B — Diagnosis (Haiku):**
- для каждого оставшегося CRITICAL failure после уровня A — один Haiku-вызов с
  фиксированным prompt'ом (TEST_ARCHITECTURE.md §12.2);
- сохраняет markdown в `tests/reports/{date}/{UC}_diagnosis.md`;
- кэширует по `test_id+commit_hash` в `tests/.diagnosis_cache/`.

**Severity по статусу UC** (USE_CASES.md §2.1):
- failing test против `implemented` UC = REGRESSION → CRITICAL → TG-алерт + Reminder 10:00;
- failing test против `partial`/`intended` UC = expected_gap → INFO в gap-report;
- failing test без записи в `uc_index.yaml` → пока считаем CRITICAL (UC-J-03 поможет позже).

**Уровень C (Repair) здесь не реализован.** Норма — CLAUDE.md §13 (конверт
авто-ремонта + поправка владельца 2026-08-03, сузившая запрет до трёх
одновременных условий: обратимость, независимый ревьюер, краснеющий вниз по
потоку оракул). Эта строка — указатель, не пересказ: до 2026-09-14 она
ссылалась на TEST_ARCHITECTURE.md §12.3, где вердикт «не реализуем» пережил
поправку на 42 дня.

CLI:
    python3.11 test_failure_handler.py [--date YYYY-MM-DD] [--dry-run]
"""
from __future__ import annotations

import llm_client
import infra_config  # основная машина — данные установки (private/infra.yaml)
import hai_core

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from doc_translation import strip_switch

SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))

from _time_inject import get_today  # noqa: E402
from project_context import enrich as _pc_enrich  # noqa: E402  # L0a: карта в алерт (best-effort)

LOGS_DIR = SCRIPT_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)
DIAGNOSIS_CACHE = SCRIPT_DIR / "tests" / ".diagnosis_cache"
DIAGNOSIS_CACHE.mkdir(parents=True, exist_ok=True)

# API spend logging — для T-0.17 monthly report
API_SPEND_LOG = LOGS_DIR / "test_api_spend.log"

# Hostname для решения «делать ли AppleScript Reminders» (только Studio)
import socket
_STUDIO = infra_config.is_primary()

# ── Helpers ──────────────────────────────────────────────────────────────────


def _log(msg: str) -> None:
    # Путь берётся из LOGS_DIR В МОМЕНТ ЗАПИСИ (23.09): константа LOG_FILE вычислялась на импорте,
    # и тесты, подменявшие LOGS_DIR, писали в БОЕВОЙ лог — 874 фальшивые строки «AUTO retry
    # test_z::test_concurrent_write» с 08.05, в которых тонула бы любая настоящая.
    log_file = LOGS_DIR / "test_failure_handler.log"
    log_file.parent.mkdir(exist_ok=True)
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(f"{get_today()} {msg}\n")


def _send_telegram(text: str) -> None:
    import notify
    import i18n
    notify.weekly(i18n.t("owner.weekly.regression"))


def _create_reminder(title: str, body: str, due_hour: int = 10) -> bool:
    """Создать Reminder в списке Health на сегодня в указанный час.

    Возвращает True если успех. На non-Studio (без iCloud Reminders) — False.
    """
    if not _STUDIO:
        _log(f"INFO: not on Studio — Reminder skipped ({title!r})")
        return False
    today = get_today()
    # AppleScript: создать reminder
    body_escaped = body.replace('"', '\\"').replace("\n", "\\n")
    script = f'''
        tell application "Reminders"
            tell list "Health"
                make new reminder with properties {{
                    name: "{title}",
                    body: "{body_escaped}",
                    due date: (current date) + (10 * hours)
                }}
            end tell
        end tell
    '''
    try:
        subprocess.run(["osascript", "-e", script],
                        capture_output=True, check=True, timeout=10)
        return True
    except Exception as e:
        _log(f"WARN: AppleScript Reminder failed: {e}")
        return False


# ── Уровень A — Triage паттернов ─────────────────────────────────────────────


RETRYABLE_PATTERNS = [
    (re.compile(r"OperationalError.*database is locked", re.I),
     "database_locked", "retry"),
    (re.compile(r"anthropic.*(timeout|connection)", re.I),
     "anthropic_timeout", "flaky_today"),
    (re.compile(r"iCloud.*evicted", re.I),
     "icloud_evicted", "flaky_today"),
    (re.compile(r"requests\.exceptions\.ConnectionError", re.I),
     "network_error", "flaky_today"),
]


RERUN_TIMEOUT_S = 600


def _node_id(f: "FailureEntry") -> Optional[str]:
    """junit classname («tests.unit.test_x» или «tests.unit.test_x.TestC») → pytest node id.
    Ищем самый длинный префикс, который существует файлом .py; остаток — классы. None — не нашли."""
    parts = f.classname.split(".")
    for k in range(len(parts), 0, -1):
        path = SCRIPT_DIR.joinpath(*parts[:k]).with_suffix(".py")
        if path.exists():
            return "::".join([str(path.relative_to(SCRIPT_DIR)), *parts[k:], f.name])
    return None


def _rerun_isolated(f: "FailureEntry") -> tuple:
    """Оракул уровня A (23.09, flaky_pattern_can_swallow_a_real_failure): повтор ОДНОГО упавшего
    теста отдельно. Прошёл → падение было случайным; упал снова → это не шум, а поломка, чей
    текст случайно совпал с паттерном. Возврат (исход, пояснение): исход — "passed" | "failed" |
    "inconclusive". Не состоявшийся повтор НЕ считается «случайностью»: вызывающий обязан
    отдать такое падение дальше, а не проглотить."""
    node = _node_id(f)
    if node is None:
        return "inconclusive", f"файл теста для {f.classname!r} не найден"
    cmd = [sys.executable, "-m", "pytest", node, "-q", "-p", "no:cacheprovider"]
    if f.layer == "llm_judge":
        cmd.append("--override-ini=addopts=")          # как в run_full_test_suite.sh
    try:
        r = subprocess.run(cmd, cwd=SCRIPT_DIR, capture_output=True, text=True,
                           timeout=RERUN_TIMEOUT_S)
    except Exception as e:  # noqa: BLE001 — повтор не состоялся: не шум, а неизвестность
        return "inconclusive", f"повтор не запустился: {type(e).__name__}: {e}"
    if r.returncode == 0:
        return "passed", "повтор отдельно прошёл"
    if r.returncode == 1:
        return "failed", "повтор отдельно упал снова"
    return "inconclusive", f"повтор вернул код {r.returncode} (тест не выбран/ошибка сбора)"


RERUN_VERDICTS = "rerun_verdicts.json"   # в каталоге отчётов дня; пишет ночной прогон


def cached_rerun(reports_dir: Path):
    """Оракул-читатель для НЕ ночных вызывающих (утренняя сводка): вердикт повтора берётся из
    того, что записал ночной обработчик, а не добывается вторым повтором — у вердикта один дом.
    Нет записи о тесте → "inconclusive" (падение не проглатывается)."""
    path = reports_dir / RERUN_VERDICTS
    try:
        # Нет файла = ночью совпавших с паттерном падений не было; случайным всё равно ничего
        # не признаётся (вердикт по умолчанию — inconclusive), так что безопасно.
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError) as e:
        _log(f"WARN: вердикты повтора за {reports_dir.name} не прочитаны ({e!r}) — совпавшие падения не признаются случайными")
        data = {}

    def _rerun(f):
        v = data.get(f.test_id)
        return (v[0], v[1]) if isinstance(v, list) and len(v) == 2 else (
            "inconclusive", "ночной вердикт повтора не найден")
    return _rerun


@dataclass
class FailureEntry:
    layer: str
    classname: str  # путь pytest до класса
    name: str       # имя теста
    message: str    # short error message
    longrepr: str = ""  # полный traceback

    @property
    def test_id(self) -> str:
        """Уникальный ID теста: layer/classname::name."""
        return f"{self.layer}/{self.classname}::{self.name}"

    def matches_pattern(self) -> Optional[tuple[str, str]]:
        """Возвращает (pattern_name, action) если найден retryable паттерн."""
        text = self.longrepr or self.message
        for regex, name, action in RETRYABLE_PATTERNS:
            if regex.search(text):
                return name, action
        return None


def parse_junit(junit_path: Path) -> list[FailureEntry]:
    """Извлечь failure-entries из junit.xml. Skipped и passed игнорируем."""
    if not junit_path.exists():
        return []
    layer = junit_path.stem.replace("_junit", "")
    entries: list[FailureEntry] = []
    try:
        root = ET.parse(junit_path).getroot()
    except ET.ParseError as e:
        _log(f"ERROR: junit parse {junit_path}: {e}")
        return []
    for case in root.iter("testcase"):
        for tag in ("failure", "error"):
            elem = case.find(tag)
            if elem is None:
                continue
            entries.append(FailureEntry(
                layer=layer,
                classname=case.get("classname", ""),
                name=case.get("name", ""),
                message=elem.get("message", "")[:500],
                longrepr=(elem.text or "")[:5000],
            ))
    return entries


# ── UC status lookup (UC-J-03 пока не реализован — простой fallback) ────────


def _uc_status_for_test(test_name: str) -> str:
    """Грубо: парсим имя файла теста типа `test_uc_a_01_lab.py` → ищет UC-A-01.

    Затем читает USE_CASES.md и достаёт status. Когда UC-J-03 готов — заменим
    на чтение uc_index.yaml.

    Возвращает: 'implemented' | 'partial' | 'intended' | 'speculative' | 'unknown'.
    """
    m = re.search(r"test_uc_([a-z])_(\d+)", test_name, re.I)
    if not m:
        return "unknown"
    uc_id = f"UC-{m.group(1).upper()}-{m.group(2)}"
    use_cases = SCRIPT_DIR / "USE_CASES.md"
    if not use_cases.exists():
        return "unknown"
    try:
        for line in strip_switch(use_cases.read_text(encoding="utf-8")).splitlines():
            if uc_id in line and "|" in line:
                # формат: | `UC-X-NN` | ... | P0 | type | status | confirmation |
                cells = [c.strip(" `") for c in line.split("|")]
                if len(cells) >= 6:
                    status = cells[5]
                    if status in ("implemented", "partial", "intended", "speculative"):
                        return status
    except Exception as e:
        _log(f"WARN: USE_CASES.md parse for {uc_id}: {e}")
    return "unknown"


def _is_critical(failure: FailureEntry) -> bool:
    """REGRESSION = падение теста против `implemented` UC.

    `partial` (gap-часть) и `intended` — expected_gap, не CRITICAL.
    `unknown` (не нашли UC) — CRITICAL по умолчанию (безопасный default).
    """
    status = _uc_status_for_test(failure.name) or _uc_status_for_test(failure.classname)
    if status == "implemented":
        return True
    if status in ("partial", "intended", "speculative"):
        return False
    return True  # unknown → CRITICAL


# ── Уровень B — Haiku diagnosis ──────────────────────────────────────────────


def _git_head() -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(SCRIPT_DIR), "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL, timeout=5,
        ).decode().strip()[:12]
    except Exception:
        return "no-git"


def _diagnose_failure(failure: FailureEntry, reports_dir: Path) -> Optional[Path]:
    """Haiku-диагноз. Сохраняет markdown в reports_dir/{test_id_safe}_diagnosis.md.

    Кэш: tests/.diagnosis_cache/{test_id_hash}_{commit}.md — повторное падение
    того же теста на том же коммите → cache hit, без API-вызова.
    """
    import hashlib
    test_id_safe = re.sub(r"[^A-Za-z0-9_]+", "_", failure.test_id)[:80]
    out_path = reports_dir / f"{test_id_safe}_diagnosis.md"
    cache_key = hashlib.sha256(failure.test_id.encode()).hexdigest()[:16]
    commit = _git_head()
    cache_path = DIAGNOSIS_CACHE / f"{cache_key}_{commit}.md"

    if cache_path.exists():
        # Cache hit — копируем
        out_path.write_text(cache_path.read_text(encoding="utf-8"), encoding="utf-8")
        _log(f"INFO: diagnosis cache hit for {failure.test_id}")
        return out_path

    # Cache miss — реальный API-вызов

    if not api_key_file.exists():
        _log(f"WARN: anthropic_key missing — skip diagnosis for {failure.test_id}")
        return None

    try:
        import anthropic
        client = llm_client.guarded_client()
        prompt = _build_diagnosis_prompt(failure)
        resp = client.messages.create(
            model=hai_core.get_model("haiku_pinned"),
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text
        usage = resp.usage
        # Лог spend (T-0.17)
        _log_api_spend(failure.test_id, "claude-haiku-4-5", usage, cache_hit=False)
    except Exception as e:
        _log(f"ERROR: Haiku diagnosis failed for {failure.test_id}: {e}")
        return None

    # L0a: вклеиваем карту задетой подсистемы по трейсбеку (не по имени теста —
    # test_X.py не содержит стем модуля-под-тестом, а трейсбек несёт реальные файлы).
    # Кэш привязан к commit → правка кода = новый коммит = новая карта, не устареет.
    text += _pc_enrich.pointer_for(f"{failure.message}\n{failure.longrepr}", str(SCRIPT_DIR))

    cache_path.write_text(text, encoding="utf-8")
    out_path.write_text(text, encoding="utf-8")
    return out_path


def _build_diagnosis_prompt(failure: FailureEntry) -> str:
    return f"""Ты — диагност автотестов Health OS. Читай контекст и пиши markdown
с гипотезой причины и 2-3 предложениями что попробовать.
Не чини. Не пиши код. Не используй эмодзи. Без воды.

**Failing test:** `{failure.test_id}`
**Layer:** {failure.layer}

**Error message:**
```
{failure.message}
```

**Traceback (last lines):**
```
{failure.longrepr[-2000:]}
```

Вопросы:
1. Какая наиболее вероятная причина (1-2 предложения)?
2. Что проверить в первую очередь?
3. Это похоже на регрессию свежего коммита или старый баг?
"""


def _log_api_spend(test_id: str, model: str, usage, cache_hit: bool) -> None:
    """T-0.17: запись каждого Haiku-вызова в лог для месячного отчёта."""
    import json as _j
    rec = {
        "ts": str(get_today()),
        "test_id": test_id,
        "model": model,
        "tokens_in": getattr(usage, "input_tokens", 0) if usage else 0,
        "tokens_out": getattr(usage, "output_tokens", 0) if usage else 0,
        "cache_hit": cache_hit,
    }
    with open(API_SPEND_LOG, "a", encoding="utf-8") as f:
        f.write(_j.dumps(rec, ensure_ascii=False) + "\n")


# ── Главный entrypoint ──────────────────────────────────────────────────────


@dataclass
class HandleResult:
    auto_fixed: list[str] = field(default_factory=list)
    flaky: list[str] = field(default_factory=list)
    critical: list[FailureEntry] = field(default_factory=list)
    expected_gap: list[FailureEntry] = field(default_factory=list)


def handle_test_failures(reports_dir: Path, dry_run: bool = False,
                         rerun=None) -> HandleResult:
    """Основная функция. Идемпотентна по дню через flag-файл.
    rerun — оракул повтора (по умолчанию `_rerun_isolated`); параметр — шов для тестов."""
    rerun = rerun or _rerun_isolated
    today = get_today()
    flag = LOGS_DIR / f"test_failure_done_{today}.flag"
    if flag.exists() and not dry_run:
        _log(f"INFO: handler уже выполнен сегодня ({today}) — пропуск")
        return HandleResult()

    if not reports_dir.exists():
        _log(f"WARN: {reports_dir} не существует — нет junit.xml")
        return HandleResult()

    # Сбор failures из всех слоёв
    all_failures: list[FailureEntry] = []
    for junit in sorted(reports_dir.glob("*_junit.xml")):
        all_failures.extend(parse_junit(junit))

    if not all_failures:
        _log(f"INFO: 0 failures в {reports_dir}")
        if not dry_run:
            flag.touch()
        return HandleResult()

    result = HandleResult()

    # Уровень A — паттерны
    remaining: list[FailureEntry] = []
    verdicts: dict = {}
    for f in all_failures:
        match = f.matches_pattern()
        if match:
            pattern_name, action = match
            # До 23.09 совпадение текста с паттерном САМО было вердиктом: «retry» ничего не
            # повторял (стояла заглушка), «flaky_today» не проверял ничего. Настоящая поломка
            # с таким текстом уходила молча. Теперь вердикт выносит повтор, паттерн — повод.
            outcome, why = rerun(f)
            verdicts[f.test_id] = [outcome, why]
            if outcome == "passed":
                if action == "retry":
                    result.auto_fixed.append(f"{f.test_id} (pattern={pattern_name}, retry: {why})")
                    _log(f"AUTO retry: {f.test_id} ({pattern_name}) — {why}")
                else:  # flaky_today
                    result.flaky.append(f"{f.test_id} (pattern={pattern_name}: {why})")
                    _log(f"FLAKY: {f.test_id} ({pattern_name}) — {why}")
                continue
            _log(f"NOT FLAKY: {f.test_id} — текст совпал с {pattern_name}, но {why}")
            f.message = f"[паттерн {pattern_name}, но {why}] {f.message}"[:500]
        remaining.append(f)

    if verdicts and not dry_run:
        (reports_dir / RERUN_VERDICTS).write_text(
            json.dumps(verdicts, ensure_ascii=False, indent=1), encoding="utf-8")

    # Severity classification
    for f in remaining:
        if _is_critical(f):
            result.critical.append(f)
        else:
            result.expected_gap.append(f)

    # Уровень B — diagnosis для CRITICAL
    if not dry_run:
        for f in result.critical:
            diag = _diagnose_failure(f, reports_dir)
            if diag:
                _log(f"DIAGNOSIS saved: {diag}")

    # TG-алерт для CRITICAL
    if result.critical and not dry_run:
        names = "\n".join(f"• {f.test_id}" for f in result.critical[:5])
        msg = (f"⚠️ Test regression: {len(result.critical)} fail(s)\n"
               f"{names}\n"
               f"Diagnosis: tests/reports/{today}/")
        _send_telegram(msg)

    # Инженерный разбор читает отчёт; отдельных напоминаний владельцу нет.

    if not dry_run:
        flag.touch()

    _log(f"SUMMARY: auto_fixed={len(result.auto_fixed)} flaky={len(result.flaky)} "
         f"critical={len(result.critical)} expected_gap={len(result.expected_gap)}")
    return result


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--date", default=None,
                    help="YYYY-MM-DD; по умолчанию сегодня")
    p.add_argument("--dry-run", action="store_true",
                    help="не отправляет TG/Reminders, не пишет flag")
    args = p.parse_args()

    date = args.date or str(get_today())
    reports_dir = SCRIPT_DIR / "tests" / "reports" / date

    result = handle_test_failures(reports_dir, dry_run=args.dry_run)

    print(json.dumps({
        "date": date,
        "auto_fixed": result.auto_fixed,
        "flaky": result.flaky,
        "critical_count": len(result.critical),
        "critical": [f.test_id for f in result.critical],
        "expected_gap_count": len(result.expected_gap),
    }, ensure_ascii=False, indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main())
