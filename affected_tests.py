#!/usr/bin/env python3.11
"""affected_tests.py — какие тесты обязан прогнать тот, кто правит эти файлы.

ЗАЧЕМ. 2026-08-03 я дважды за день прогнал не то: сначала проверил утверждение
чтением вместо запуска, потом взял выборочные наборы вместо всех затронутых —
и красный `test_blueprint_points_to_git_canon` уехал в прод. Оба раза дефект был
не в знании, а в ОТБОРЕ: список тестов выбирал человек по памяти о том, что
связано. Память о связях — то самое утверждение о соседях без гасителя (§18).

ЧТО СЧИТАЕТСЯ СВЯЗЬЮ. Тест затронут, если выполняется хоть одно:
  1. импортирует изменённый Python-модуль (прямо или через его импортёров, глубина 1);
  2. упоминает ИМЯ изменённого файла в своём исходнике.
Второй признак существеннее первого и добавлен именно из-за утреннего случая:
доковые сторожа не импортируют ничего, они ЧИТАЮТ файл по имени. Без него
правка `.md` не выбрала бы ни одного теста — ровно та дыра, что сработала.

ЧЕГО НЕ ЛОВИТ, названо вслух:
  · тест, собирающий путь из переменных (`ROOT / name / "x.md"`) — имени в тексте нет;
  · связь через данные (тест читает таблицу, которую пишет изменённый модуль);
  · глубина 1 у импортёров: правка health_db не потянет весь транзитивный хвост
    намеренно — иначе выбор вырождается в «все тесты» и перестаёт быть выбором.
Поэтому это отбор ПОВЕРХ ночного полного прогона, а не вместо него. Полный прогон
остаётся единственным доказательством; здесь — сокращение окна между поломкой
и её обнаружением с суток до минут.
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
TESTS = ROOT / "tests"
SELECTION_CAP = 40          # выше потолка выбор бессмысленен — честнее сказать «гони всё»


def _module_imports(path: Path) -> set[str]:
    """Стемы модулей, импортированных файлом. AST, не подстрока: слово в докстроке
    не импорт (тот же урок, что у check_llm_tracts_guarded 2026-08-03)."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
    except (OSError, SyntaxError) as e:
        print(f"⚠ {path.name}: не разобран ({type(e).__name__}) — его связи невидимы",
              file=sys.stderr)
        return set()
    out: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            out |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module:
            out.add(n.module.split(".")[0])
    return out


def _project_stems() -> set[str]:
    return {p.stem for p in ROOT.glob("*.py")} | {
        d.name for d in ROOT.iterdir() if d.is_dir() and (d / "__init__.py").exists()}


def select(changed: list[str]) -> tuple[list[str], str | None]:
    """→ (пути тестов, причина-отказа-от-выбора | None).

    Причина непустая = выбор не состоялся и надо гнать полный набор. Молчаливое
    усечение здесь было бы хуже отсутствия отбора: оно читалось бы как «всё проверено».
    """
    changed_paths = [Path(c) for c in changed]
    changed_stems = {p.stem for p in changed_paths if p.suffix == ".py"}
    changed_names = {p.name for p in changed_paths}

    # Импортёры изменённых модулей, глубина 1 — их тесты тоже под подозрением.
    project = _project_stems()
    seeds = set(changed_stems)
    if changed_stems:
        for p in ROOT.glob("*.py"):
            if p.stem in changed_stems:
                continue
            if _module_imports(p) & changed_stems & project:
                seeds.add(p.stem)

    hits: list[str] = []
    for t in sorted(TESTS.rglob("test_*.py")):
        try:
            src = t.read_text(encoding="utf-8", errors="ignore")
        except OSError as e:
            print(f"⚠ {t}: не прочитан ({type(e).__name__}) — пропущен", file=sys.stderr)
            continue
        if _module_imports(t) & seeds or any(n in src for n in changed_names):
            hits.append(str(t.relative_to(ROOT)))
    if len(hits) > SELECTION_CAP:
        return hits, (f"затронуто {len(hits)} тестов (> {SELECTION_CAP}) — "
                      "правка широкая, выбор не сужает; гони полный набор")
    return hits, None


def _staged() -> list[str]:
    """Изменённые файлы берутся из ИНДЕКСА, а не из рабочего дерева (§15):
    судится то, что реально уйдёт в коммит, поэтому частичный стейдж не обманет."""
    out = subprocess.run(["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
                         cwd=ROOT, capture_output=True, text=True, check=True)
    return [l for l in out.stdout.splitlines() if l]


def main() -> int:
    changed = sys.argv[1:] or _staged()
    if not changed:
        return 0
    hits, refusal = select(changed)
    if refusal:
        print(f"affected_tests: {refusal}")
        return 0
    for h in hits:
        print(h)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
