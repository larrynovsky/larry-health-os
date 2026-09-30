#!/usr/bin/env python3.11
"""pre_commit_check.py — Python-логика git pre-commit для Health OS.

Запускается из scripts/git-hooks/pre-commit. Получает список staged файлов
в argv[1:]. Возвращает exit code:
  0 = всё чисто
  1 = найдены нарушения (commit заблокирован)
  2 = инфра-ошибка (yaml не парсится, и т.п.)

Проверки:
  1. INV-DOC-1: stop-words в LIVE файлах
  2. INV-DOC-9 drift: silent-except в *.py LIVE превышает baseline (35)
  3. HISTORY append-only: запрещены удаления из домов истории (plans/)
  4. §9 лексикон-в-коде: доменные списки строк вне БД/реестра (если staged .py)

Без escape-hatch (design choice 2026-05-11 §1).
"""
from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
INVENTORY_PATH = REPO_ROOT / "doc_inventory.yaml"


def load_inventory() -> dict:
    try:
        with open(INVENTORY_PATH) as f:
            return yaml.safe_load(f)
    except Exception as e:
        print(f"❌ pre-commit: doc_inventory.yaml не парсится: {e}")
        sys.exit(2)


def classify_file(path: str, inv: dict) -> str:
    """Возвращает 'live' / 'archive' / 'ignored' / 'other'."""
    p = path.lstrip("./")
    if p in (inv.get("live") or []):
        return "live"
    if p in (inv.get("archive") or []):
        return "archive"
    if p in (inv.get("ignored_paths") or []):
        return "ignored"
    for glob in (inv.get("live_globs") or []):
        if Path(p).match(glob):
            return "live"
    return "other"


def get_history_headers(inv: dict) -> list[str]:
    return inv.get("history_section_headers") or []


def is_inside_history_section(content: str, line_no: int, headers: list[str]) -> bool:
    """True если строка попадает в защищённую history-секцию.

    Логика повторяет test_doc_invariants.py: ищем последний header перед line_no.
    Если такой header — из whitelist, секция защищена.
    """
    lines = content.splitlines()
    last_h2_idx = -1
    last_h2_text = ""
    for i, line in enumerate(lines[: line_no - 1], start=1):
        m = re.match(r"^(#{2,3})\s+(.+)$", line)
        if m:
            last_h2_idx = i
            last_h2_text = m.group(2).strip()
    if last_h2_idx < 0:
        return False
    for hdr in headers:
        if hdr.lower() in last_h2_text.lower():
            return True
    return False


def check_stop_words(staged: list[str], inv: dict) -> list[str]:
    """Возвращает список нарушений вида 'file:line — stop_word'."""
    violations = []
    stop_words = inv.get("stop_words_in_live") or []
    headers = get_history_headers(inv)
    if not stop_words:
        return violations
    pattern = re.compile("|".join(re.escape(w) for w in stop_words))

    for f in staged:
        cls = classify_file(f, inv)
        if cls != "live":
            continue
        path = REPO_ROOT / f
        if not path.exists() or path.suffix not in (".md",):
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except Exception:
            continue
        for line_no, line in enumerate(content.splitlines(), start=1):
            m = pattern.search(line)
            if m:
                if is_inside_history_section(content, line_no, headers):
                    continue
                violations.append(f"{f}:{line_no} — {m.group(0)!r}")
    return violations


def count_silent_except_in_live(inv: dict) -> int:
    """AST-сканер: считаем `except ...: pass` без log/silent-ok в LIVE *.py.

    Scope совпадает с tests/integration/test_doc_invariants.py:test_inv_doc_9 —
    все `*.py` в репо минус ignored_paths и каталог tests/. Если hook и nightly
    test расходятся в scope — hook пропускает нарушения, которые nightly ловит,
    и enforcement становится дырявым (расхождение слоёв защиты).
    """
    ignored = inv.get("ignored_paths") or []
    count = 0
    for path in REPO_ROOT.rglob("*.py"):
        rel = path.relative_to(REPO_ROOT)
        rel_str = str(rel)
        if any(rel_str.startswith(ig.rstrip("/") + "/") for ig in ignored):
            continue
        if "tests" in path.parts:
            continue
        if not path.exists():
            continue
        try:
            src = path.read_text(encoding="utf-8")
            tree = ast.parse(src)
        except Exception:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                # Только bare `except:` или `except Exception:`
                if node.type is None or (
                    isinstance(node.type, ast.Name) and node.type.id == "Exception"
                ):
                    if all(isinstance(b, ast.Pass) for b in node.body):
                        # Есть ли silent-ok комментарий?
                        try:
                            line_start = node.lineno
                            line_end = (node.end_lineno or line_start) + 1
                            block = "\n".join(src.splitlines()[line_start - 1 : line_end])
                            if "# silent-ok:" in block:
                                continue
                        except Exception:
                            pass  # silent-ok: AST may not provide end_lineno on edge cases
                        count += 1
    return count


HISTORY_DIRS = ("plans/",)


def check_archive_append_only(staged_deletions: list[str]) -> list[str]:
    """Нарушения append-only для домов истории: удаление плана/отчёта/аудита.

    До 2026-08-04 префикс был `docs/archive/` — каталога с таким именем в
    репозитории нет и не было, поэтому гейт не срабатывал НИ РАЗУ и был
    неотличим от работающего. Дом истории разовых документов — `plans/`
    (doc_inventory.yaml::archive), там 21 файл, и на часть из них ссылается
    CLAUDE.md §15 как на доказательства (`plans/verify_*.py`).

    Удалить всё же нужно → `git commit --no-verify` осознанно: файл остаётся
    в истории git, гейт защищает от ТИХОЙ зачистки, а не от потери.
    """
    return [f for f in staged_deletions if f.startswith(HISTORY_DIRS)]


def _selftest_append_only() -> None:
    """Позитивный контроль гейта: краснеет на доме истории, молчит на прочем.

    Раньше проверки не было — и мёртвый префикс `docs/archive/` прожил
    незамеченным (сколько именно, неизвестно: гейт молчал по построению).
    Зовётся из main() на каждом коммите: цена — три сравнения списков.
    """
    assert check_archive_append_only(["plans/PLAN_x_2026-01-01.md"]) == \
        ["plans/PLAN_x_2026-01-01.md"], "гейт не заметил удаление из plans/"
    assert check_archive_append_only(["docs/how-to/x.md", "health_db.py"]) == [], \
        "гейт ругается на файлы вне дома истории"
    assert check_archive_append_only([]) == [], "пустой список — не нарушение"


def check_lexicons() -> list[str]:
    """§9-датчик: доменные лексиконы (строк-коллекции) вне БД/реестра.

    Импорт lexicon_registry ленивый (REPO_ROOT в пути). Fail-closed: если сам
    датчик упал — блок с явным сообщением (§1, без escape-hatch), не молчаливый пропуск.
    """
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    try:
        import lexicon_registry as _lex
        findings = _lex.collect_lexicon_findings(REPO_ROOT)
    except Exception as e:  # fail-closed: сломанный датчик = блок, не тишина
        return [f"§9-датчик лексиконов сломан ({type(e).__name__}: {e}) — почини, "
                f"без него коммит §9 не проверен. См. docs/how-to/move_lexicon_to_db.md"]
    out: list[str] = []
    if findings:
        out.append("§9: доменные лексиконы вне БД/реестра "
                   "(вынеси в system_config или зарегистрируй в lexicon_registry.REGISTERED):")
        out.extend(f"  {f}" for f in findings)
        out.append("  → как чинить: docs/how-to/move_lexicon_to_db.md")
    return out


def check_memory_channels() -> list[str]:
    """M6-датчик: новый читатель сырого conversation_history вне реестра (класс утечки
    staleness — старая реплика подаётся как сегодняшняя). Fail-closed."""
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    try:
        import memory_channel_registry as _mc
        findings = _mc.collect_channel_findings(REPO_ROOT)
    except Exception as e:  # fail-closed
        return [f"M6-датчик каналов памяти сломан ({type(e).__name__}: {e}) — почини"]
    out: list[str] = []
    if findings:
        out.append("Каналы памяти→промпт: новый непокрытый читатель сырого транскрипта "
                   "(датируй [ГГГГ-ММ-ДД] + зарегистрируй в memory_channel_registry.REGISTERED):")
        out.extend(f"  {f}" for f in findings)
        out.append("  → как чинить: docs/how-to/date_memory_channel.md")
    return out


def get_staged_deletions() -> list[str]:
    """git diff --cached --name-only --diff-filter=D."""
    try:
        out = subprocess.check_output(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=D"],
            text=True,
            cwd=REPO_ROOT,
        )
        return [l.strip() for l in out.splitlines() if l.strip()]
    except subprocess.CalledProcessError:
        return []


def main() -> int:
    staged = sys.argv[1:]
    inv = load_inventory()
    baseline = inv.get("silent_except_baseline", 35)

    failures = []

    # 1. Stop-words.
    sw = check_stop_words(staged, inv)
    if sw:
        failures.append("Stop-words в LIVE документации:")
        for v in sw:
            failures.append(f"  {v}")

    # 2. Silent-except drift.
    current = count_silent_except_in_live(inv)
    if current > baseline:
        failures.append(
            f"Silent-except в LIVE: {current} > baseline {baseline} "
            f"(drift на +{current - baseline}). Добавь log.warning или "
            f"# silent-ok: <reason>."
        )

    # 3. Archive append-only. Самопроверка ПЕРЕД применением: гейт с мёртвым
    # префиксом уже жил здесь незамеченным (2026-08-04), молчание неотличимо
    # от работы.
    _selftest_append_only()
    deletions = get_staged_deletions()
    archive_violations = check_archive_append_only(deletions)
    if archive_violations:
        failures.append(f"Удаление из дома истории {HISTORY_DIRS} запрещено "
                        f"(осознанно — только --no-verify):")
        for v in archive_violations:
            failures.append(f"  {v}")

    # 4. §9 лексикон-в-коде (доменный список строк вне БД/реестра).
    #    Только если staged .py — лексикон вводится через .py. Скан репо-глобальный
    #    (ратчет по вхождениям), БД не нужна — чистый статик-анализ. fail-closed.
    if any(f.endswith(".py") for f in staged):
        failures.extend(check_lexicons())
        failures.extend(check_memory_channels())

    if failures:
        print("❌ pre-commit: найдены нарушения")
        for line in failures:
            print(line)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
