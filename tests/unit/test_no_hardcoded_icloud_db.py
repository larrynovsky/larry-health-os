"""Guard: LIVE-модули не хардкодят iCloud-расположение health.db.

Источник пути к БД — только `health_db.DB_PATH` (hostname-aware). iCloud-копия БД
удалена (R1/R2, split-brain fix 2026-06-18). Инцидент того же дня: `smoke_tests` и
`check_contracts` хардкодили iCloud-путь + `.exists()` → деплой упал после удаления
форка. Этот guard — исполняемый датчик на регрессию того же класса (урок:
при удалении канонического пути грепать не только writers, но и существование).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).parents[2]

# Конструкции ПУТИ к health.db из iCloud-корня (а не слово health.db в комментарии).
PATTERNS = [
    re.compile(r'CloudDocs[^"\n]*health\.db'),     # полный литерал .../CloudDocs/.../health.db
    re.compile(r'/\s*"data/health\.db"'),          # ICLOUD / "data/health.db"
    re.compile(r'"data"\s*/\s*"health\.db"'),      # ICLOUD / "data" / "health.db"
]

# health_db.py — канонический резолвер (легитимно строит iCloud-путь).
# _*.py — дормантные patch-скрипты. (vps_sync.py удалён 2026-06-29, multitenancy Phase 0.)
EXCLUDE = {"health_db.py", "integrity_tests.py"}


def _live_py():
    return [p for p in sorted(ROOT.glob("*.py"))
            if p.name not in EXCLUDE and not p.name.startswith("_")]


def test_patterns_catch_known_bad_forms():
    """Positive control: guard реально ловит старые сломанные формы (не слеп)."""
    bad = [
        'must_exist(ICLOUD / "data/health.db")',
        'ICLOUD / "health" / "data" / "health.db"',
        '"/Users/x/Library/Mobile Documents/com~apple~CloudDocs/health/data/health.db"',
    ]
    for sample in bad:
        assert any(p.search(sample) for p in PATTERNS), f"guard слеп к: {sample}"


def test_no_live_module_hardcodes_icloud_db_path():
    offenders = []
    for p in _live_py():
        text = p.read_text(encoding="utf-8", errors="replace")
        for pat in PATTERNS:
            if pat.search(text):
                offenders.append(f"{p.name} ~ /{pat.pattern}/")
    assert not offenders, (
        "Хардкод iCloud-пути к health.db — используй health_db.DB_PATH "
        "(iCloud-копия удалена, R1/R2): " + "; ".join(offenders)
    )
