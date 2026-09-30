"""memory_channel_registry.py — реестр каналов сырого транскрипта в промпт + датчик (M6).

Инвариант (staleness): каждый читатель сырого conversation_history, чьё содержимое
уходит в промпт модели, делает ВРЕМЯ наблюдаемым (штамп [ГГГГ-ММ-ДД]). Иначе модель
может принять старую реплику за сегодняшнюю. Выдуманный немедицинский пример:
«мастерская закрыта» без даты ошибочно становится сегодняшним статусом.

Датчик ловит НОВЫЙ непокрытый читатель conversation_history в контекст-модулях. Честная
граница (как §9-лексикон): узкий синтаксический прокси на raw-transcript читателей, НЕ
поведенческое доказательство «датирует» — его держат тесты M2–M5 + реплей-гейт M3.
Сиблинг producer_registry/lexicon_registry (тот же ратчет+census-паттерн). Fail-closed.
"""
from __future__ import annotations
import ast
from pathlib import Path

# Модули, собирающие контекст для модели.
_CONTEXT_MODULES = ("hai_core.py", "hai_chat.py", "hai_context.py", "patient_context.py")
_RAW_SOURCE = "conversation_history"

# verdict: dated (читает в промпт, несёт штамп) / writer (пишет транскрипт) / infra (DDL).
REGISTERED: dict[str, dict] = {
    "hai_core._ensure_history_table": {"verdict": "infra",  "note": "CREATE TABLE, не в промпт"},
    "hai_core.save_message":          {"verdict": "writer", "note": "INSERT транскрипта, пишет не читает"},
    "hai_core.get_history":           {"verdict": "dated",  "note": "SELECT→промпт; штамп из created_at (M2)"},
    "patient_context.pending_chat":   {"verdict": "dated",  "note": "SELECT→промпт; штамп из created_at (M5)"},
}


def _scan_raw_readers(root: Path) -> dict[str, dict]:
    """Функции в контекст-модулях, чей исходник упоминает conversation_history."""
    found: dict[str, dict] = {}
    for name in _CONTEXT_MODULES:
        p = root / name
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8")
        tree = ast.parse(text)
        mod = name[:-3]
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                seg = ast.get_source_segment(text, node) or ""
                if _RAW_SOURCE in seg:
                    found[f"{mod}.{node.name}"] = {"file": name, "line": node.lineno}
    return found


def audit_channels(scanned: dict[str, dict]) -> list[str]:
    findings: list[str] = []
    for q in sorted(scanned):
        if q not in REGISTERED:
            m = scanned[q]
            findings.append(
                f"Новый читатель сырого {_RAW_SOURCE} вне реестра: {q} ({m['file']}:{m['line']}). "
                f"Датируй строки [ГГГГ-ММ-ДД] из created_at и зарегистрируй вердикт в "
                f"memory_channel_registry.REGISTERED. См. docs/how-to/date_memory_channel.md"
            )
    # census-симметрия: реестр не может тихо протухнуть (registered без реальной функции).
    for q in sorted(REGISTERED):
        if q not in scanned:
            findings.append(f"Реестр протух: {q} в REGISTERED, но не найден в коде — обнови memory_channel_registry")
    return findings


def collect_channel_findings(root: Path | None = None) -> list[str]:
    root = root or Path(__file__).resolve().parent
    try:
        return audit_channels(_scan_raw_readers(root))
    except Exception as e:  # fail-closed: сломанный датчик = находка, не тишина
        return [f"memory_channel_registry датчик сломан ({type(e).__name__}: {e}) — почини"]


if __name__ == "__main__":
    import sys
    fs = collect_channel_findings()
    if fs:
        print("НАХОДКИ M6:")
        for x in fs:
            print("  " + x)
        sys.exit(1)
    print("OK: все читатели сырого conversation_history в реестре, реестр не протух.")
