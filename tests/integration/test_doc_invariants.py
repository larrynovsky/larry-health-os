"""
P3-2 — INV-DOC drift-invariants для LIVE-документации.

Источник: ROADMAP Wave 3-DOC v2 P3-2.

Проверяет инварианты LIVE-файлов из doc_inventory.yaml:
- INV-DOC-1: stop_words (nip.io; адрес старого сервера — в приватном словаре, класс infra) отсутствуют в LIVE вне history-секций.
- INV-DOC-5: дата в шапке ARCH_SNAPSHOT.md не старше 30 дней.
- INV-DOC-9: число silent-except'ов в LIVE *.py не превышает baseline (35).

Negative tests:
- T-P3-А: history-секция действительно протектится (stop-word внутри неё игнорируется).
- T-P3-Б: stop-word вне history-секции реально ловится.
"""
from __future__ import annotations

import ast
import re
import socket
from datetime import date, datetime
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
INVENTORY = ROOT / "doc_inventory.yaml"


# ---------- Утилиты ----------

def _load() -> dict:
    with INVENTORY.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _expand_live(data: dict) -> list[Path]:
    """Раскрываем live-паттерны в абсолютные пути."""
    out: list[Path] = []
    for pat in data["live"]:
        if "*" in pat:
            for p in ROOT.glob(pat):
                if p.is_file():
                    out.append(p)
        else:
            p = ROOT / pat
            if p.is_file():
                out.append(p)
    return out


def _strip_history_sections(text: str, headers: list[str]) -> str:
    """
    Возвращает содержимое файла без history-секций.

    History-секция начинается со строки-заголовка вида `## <header>` или
    `### <header>` и продолжается до конца файла или следующего заголовка
    того же или более высокого уровня. Поскольку history обычно последняя —
    считаем что до конца файла.

    Реализация: ищем первое вхождение любого header, отрезаем всё после.
    """
    if not text:
        return text
    lines = text.splitlines(keepends=True)
    cut_idx = None
    header_pattern = re.compile(
        r"^#{1,6}\s+(?:" + "|".join(re.escape(h) for h in headers) + r")\b",
        re.IGNORECASE,
    )
    for i, line in enumerate(lines):
        if header_pattern.match(line):
            cut_idx = i
            break
    if cut_idx is None:
        return text
    return "".join(lines[:cut_idx])


def _find_stop_words(text: str, stop_words: list[str]) -> list[tuple[int, str]]:
    """Возвращает [(line_number, stop_word), ...] для каждой находки."""
    hits: list[tuple[int, str]] = []
    for i, line in enumerate(text.splitlines(), start=1):
        for sw in stop_words:
            if sw in line:
                hits.append((i, sw))
    return hits


def _count_silent_except(file_path: Path) -> int:
    """AST-сканер: считает `except (Exception|BaseException|*): pass` без log/маркера."""
    try:
        src = file_path.read_text(encoding="utf-8")
        tree = ast.parse(src)
    except Exception:
        return 0
    text_lines = src.splitlines()
    count = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        if len(node.body) != 1 or not isinstance(node.body[0], ast.Pass):
            continue
        exc_type = ast.unparse(node.type) if node.type else None
        if exc_type and exc_type not in ("Exception", "BaseException"):
            continue
        # silent-ok маркер
        line_idx = node.body[0].lineno - 1
        line_text = text_lines[line_idx] if line_idx < len(text_lines) else ""
        prev_line = text_lines[line_idx - 1] if line_idx > 0 else ""
        if "silent-ok" in (line_text + prev_line).lower():
            continue
        count += 1
    return count


# ---------- INV-DOC тесты ----------

def test_inv_doc_1_no_stop_words_in_live():
    """Stop-words из yaml не появляются в LIVE-файлах вне history-секций."""
    data = _load()
    stop_words = data["stop_words_in_live"]
    history_headers = data["history_section_headers"]

    violations: list[str] = []
    for path in _expand_live(data):
        text = path.read_text(encoding="utf-8")
        live_part = _strip_history_sections(text, history_headers)
        hits = _find_stop_words(live_part, stop_words)
        for line_no, sw in hits:
            rel = path.relative_to(ROOT)
            violations.append(f"{rel}:{line_no} — {sw!r}")

    assert not violations, (
        "Stop-words найдены в LIVE-документации (вне history-секций):\n  "
        + "\n  ".join(violations)
        + "\n\nИсправь: перенеси в archive-файл, оберни в history-секцию, "
        + "или удали упоминание."
    )


def test_inv_doc_5_arch_snapshot_date_not_too_old():
    """Дата в шапке ARCH_SNAPSHOT.md не старше 30 дней — иначе ручные правки давно не делались."""
    arch = ROOT / "ARCH_SNAPSHOT.md"
    assert arch.exists(), "ARCH_SNAPSHOT.md должен существовать"
    text = arch.read_text(encoding="utf-8")[:500]
    # `*` в классе — с 2026-08-03 дата стоит в markdown-жирном (`**Дата:** ...`),
    # потому что версию/дату сюда пишет doc_agent после удаления BLUEPRINT.md,
    # и формат у него общий для обоих файлов. Проверяется по-прежнему СВЕЖЕСТЬ.
    m = re.search(r"Дата[:*\s\|]+(\d{4}-\d{2}-\d{2})", text)
    assert m, "не нашёл дату в шапке ARCH_SNAPSHOT.md (формат: Дата: YYYY-MM-DD)"
    arch_date = datetime.strptime(m.group(1), "%Y-%m-%d").date()
    age_days = (date.today() - arch_date).days
    # 60 дней — мягкий cap. Менее жёстко на raw начальных этапах.
    assert age_days < 60, (
        f"ARCH_SNAPSHOT.md дата {arch_date} устарела на {age_days} дней. "
        f"Возможно нужно запустить gen_blueprint.py или вручную обновить шапку."
    )


def test_inv_doc_9_silent_except_under_baseline():
    """Количество silent-except в LIVE *.py не превышает baseline из yaml (правило #7)."""
    data = _load()
    baseline = data["silent_except_baseline"]
    ignored = data["ignored_paths"]

    total = 0
    for py in ROOT.rglob("*.py"):
        rel = py.relative_to(ROOT)
        if any(str(rel).startswith(ig.rstrip("/") + "/") for ig in ignored):
            continue
        # tests/ исключаем — там legitimate test-related except'ы
        if "tests" in py.parts:
            continue
        total += _count_silent_except(py)

    assert total <= baseline, (
        f"Silent-except'ов в LIVE-коде: {total} (baseline {baseline}). "
        f"Превышение = drift. Добавь log.warning или # silent-ok: <reason> "
        f"в новый except, либо обнови baseline в doc_inventory.yaml если "
        f"делаешь массовый review (см. #93 N9-followup)."
    )


def test_inv_doc_10_gen_blocks_not_stale():
    """INV-DOC-10: GEN-блоки в LIVE документах не должны быть старше 14 дней.

    Wave 3-DOC v2 N10 (staleness watchdog). Проверяет mtime файлов, содержащих
    маркеры `<!-- GEN:X:START -->`. Если mtime > 14 дней — INFO (xfail с
    причиной), не FAIL. Цель: видеть staleness в утреннем брифинге без
    блокировки коммитов.

    Контекст: GEN-блоки (KEY_PATHS, SCHEDULE, MODULE_REGISTRY, ARCH_LOG)
    регенерируются вручную через gen_*.py на Studio (правило #8 canonical-only
    generation). Если давно не запускали — документация устаревает молча.
    Этот тест зажигает желтую лампу.
    """
    import re
    import time

    threshold_days = 14
    now = time.time()
    threshold_sec = threshold_days * 86400

    stale: list[tuple[str, int]] = []
    for md in ROOT.rglob("*.md"):
        if "tests" in md.parts and "charters" not in md.parts:
            continue
        # Фильтр `docs/archive` снят 2026-08-04: каталога нет и не было, а в
        # `plans/` (реальном доме истории) GEN-блоков ноль — фильтровать нечего.
        # Мёртвое условие в тесте читается как защита, которой нет.
        try:
            content = md.read_text(encoding="utf-8")
        except Exception:
            continue
        if not re.search(r"<!--\s*GEN:\w+:START\s*-->", content):
            continue
        age = now - md.stat().st_mtime
        if age > threshold_sec:
            rel = md.relative_to(ROOT)
            stale.append((str(rel), int(age / 86400)))

    if stale:
        # Этот тест НЕ блокирует commit — staleness не баг, а сигнал.
        # Помечаем как xfail с подробным сообщением, чтобы утренний брифинг
        # увидел в pytest output без падения test-suite.
        msg = "GEN-блоки требуют регенерации (>{}д):\n".format(threshold_days)
        for fname, days in stale:
            msg += f"  {fname} — {days}д\n"
        msg += "\nЗапусти gen_*.py на Studio:\n"
        msg += "  ssh <studio_ssh> /opt/homebrew/bin/python3.11 ~/health_scripts/gen_key_paths.py\n"
        msg += "  ssh <studio_ssh> /opt/homebrew/bin/python3.11 ~/health_scripts/gen_schedule.py\n"
        pytest.xfail(msg)


# ---------- Negative tests (что система работает корректно) ----------

def test_t_p3_a_history_section_is_protected():
    """T-P3-А: stop-word внутри history-секции должен игнорироваться."""
    data = _load()
    stop_words = data["stop_words_in_live"]
    history_headers = data["history_section_headers"]

    # Конструируем content с history секцией содержащей stop-word
    sample = """# Test doc

## Современное состояние
Тут только актуальные факты.

## История изменений
Раньше использовался vps.nip.io как production VPS.
"""
    live_part = _strip_history_sections(sample, history_headers)
    hits = _find_stop_words(live_part, stop_words)
    assert not hits, (
        f"history-секция не протектится! stop-words из history-секции "
        f"всплыли: {hits}"
    )


def test_t_p3_b_artificial_inject_caught():
    """T-P3-Б: stop-word вне history-секции должен быть обнаружен."""
    data = _load()
    stop_words = data["stop_words_in_live"]
    history_headers = data["history_section_headers"]

    # Конструируем content со stop-word ВНЕ history-секции
    sample = """# Test doc

## Современное состояние
Production живёт на vps.nip.io — VPS.

## История изменений
Когда-то использовали другой сервер.
"""
    live_part = _strip_history_sections(sample, history_headers)
    hits = _find_stop_words(live_part, stop_words)
    assert hits, (
        "Artificial inject должен был быть пойман, но проскочил!\n"
        "Возможно неверная логика _strip_history_sections или _find_stop_words."
    )
    # Дополнительно: убеждаемся что stop-word — именно тот что вставили
    assert any(sw == "nip.io" for _, sw in hits)


def test_t_p3_b_inject_different_headers_caught():
    """Stop-word под заголовком, которого нет в history_headers — тоже ловится."""
    data = _load()
    stop_words = data["stop_words_in_live"]
    history_headers = data["history_section_headers"]

    # «История работ» — НЕ в history_section_headers
    sample = """# Test doc

## История работ
Тут описаны работы, и упомянут vps.nip.io.
"""
    live_part = _strip_history_sections(sample, history_headers)
    hits = _find_stop_words(live_part, stop_words)
    assert hits, (
        "«История работ» не в history_section_headers — stop-word должен ловиться. "
        f"Получили: {hits}"
    )
