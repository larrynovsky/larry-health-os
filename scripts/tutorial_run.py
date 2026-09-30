#!/usr/bin/env python3
"""Прогон урока установки так, как его прочтёт человек (решение владельца 30.09, «да» на п.3).

Исполняет по порядку блоки bash, перед которыми стоит `<!-- tutorial:run -->`, одной оболочкой
на блок (`bash -euo pipefail`). Подмен ровно две, обе видны здесь:
  - адрес файлов последнего выпуска → file://<--dist> (до первого выпуска и для проверки
    ещё не выпущенной версии CI кладёт туда файлы, собранные из этого же коммита);
  - заглушки ключей из шага 5 → поддельные значения (бот с ними падает на InvalidToken —
    это и есть доказательство, что он стартовал и прочёл секрет).
Всё остальное в блоке исполняется как написано: разошёлся текст урока с системой — прогон
краснеет. Блоки только для Мака (brew, Colima, launchctl) не помечаются: их CI не исполнит.

    python3 scripts/tutorial_run.py docs/tutorials/first_install.md --dist "$PWD/dist"
    python3 scripts/tutorial_run.py docs/tutorials/first_install.md --list   # что исполнится
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

MARK = "<!-- tutorial:run -->"
BLOCK = re.compile(re.escape(MARK) + r"\s*\n```bash\n(.*?)\n```", re.S)
RELEASE_URL = "https://github.com/larrynovsky/larry-health-os/releases/latest/download/"
FAKE = {"ТОКЕН_ОТ_BOTFATHER": "123456:ci-fake-token", "TOKEN_FROM_BOTFATHER": "123456:ci-fake-token",
        "ВАШ_ID": "1", "YOUR_ID": "1", "КЛЮЧ_ANTHROPIC": "sk-ant-ci-fake", "ANTHROPIC_KEY": "sk-ant-ci-fake"}


def blocks(text: str) -> list[str]:
    return BLOCK.findall(text)


def substituted(block: str, dist: str) -> str:
    out = block.replace(RELEASE_URL, f"file://{dist.rstrip('/')}/")
    for k, v in FAKE.items():
        out = out.replace(f"'{k}'", f"'{v}'")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("page")
    ap.add_argument("--dist", help="каталог с compose.yaml и health.env вместо выпуска")
    ap.add_argument("--list", action="store_true", help="показать блоки и выйти")
    a = ap.parse_args(argv)
    found = blocks(Path(a.page).read_text(encoding="utf-8"))
    if not found:
        print(f"⛔ в {a.page} нет блоков {MARK} — прогонять нечего", file=sys.stderr)
        return 1
    for i, b in enumerate(found, 1):
        code = substituted(b, a.dist or "DIST")
        print(f"── блок {i}/{len(found)} ──\n{code}", flush=True)
        if a.list:
            continue
        if not a.dist:
            ap.error("--dist обязателен для прогона")
        r = subprocess.run(["bash", "-euo", "pipefail", "-c", code])
        if r.returncode:
            print(f"⛔ блок {i} урока {a.page} упал (код {r.returncode})", file=sys.stderr)
            return 1
    print(f"✅ {len(found)} блоков урока исполнены")
    return 0


if __name__ == "__main__":
    sys.exit(main())
