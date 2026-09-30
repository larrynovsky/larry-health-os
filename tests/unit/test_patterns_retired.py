"""Паттерны сняты (2026-09-26, решение владельца, BL-PATTERNS-FROZEN-1): таблица patterns —
архив. Сторож краснеет, если рабочий код снова начнёт её писать или читать, а арбитр чата —
просить у модели «patterns»: иначе замороженные строки марта вернутся в каждый промпт.
Разовые аудиторские скрипты `_*.py` читать архив могут — они не кормят промпты.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN = re.compile(r"get_active_patterns|save_pattern|(FROM|INTO|UPDATE)\s+patterns\b", re.I)


def _prod_files():
    for p in ROOT.rglob("*.py"):
        rel = p.relative_to(ROOT)
        if rel.parts[0] in {"tests", ".git", "build"} or p.name.startswith("_") and p.parent == ROOT:
            continue
        if "__pycache__" in rel.parts or rel.parts[0].startswith("."):
            continue
        yield rel, p


def test_no_production_code_touches_patterns():
    hits = [f"{rel}:{i}" for rel, p in _prod_files()
            for i, line in enumerate(p.read_text(encoding="utf-8", errors="ignore").splitlines(), 1)
            if FORBIDDEN.search(line)]
    assert not hits, f"код снова трогает снятые паттерны: {hits}"


def test_arbiter_does_not_ask_for_patterns():
    src = (ROOT / "hai_chat.py").read_text(encoding="utf-8")
    assert '"patterns"' not in src and "patterns:" not in src


def test_guard_sees_a_planted_reader(tmp_path):
    """Сторож не слеп: строка-читатель, как была до снятия, им распознаётся."""
    assert FORBIDDEN.search("    patterns  = db.get_active_patterns()")
    assert FORBIDDEN.search('"SELECT * FROM patterns WHERE is_active=1"')
