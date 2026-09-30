"""
Гард против дрейфа фикстурной схемы (2026-06-28, план v2 «самозащита»).

Фикстура `tests/fixtures/health_schema.sql` — ручной слой, который трижды
молча отставал от боевой схемы и блокировал характеризацию. Этот тест ловит
отставание ГРОМКО: если в боевой ~/health/data/health.db появилась таблица,
которой нет в фикстуре, тест падает и называет её.

Запускается только на Studio (где есть боевая БД); на MacBook/CI без БД — skip.
FTS-таблицы исключены (тестам не нужны, конфликт shadow-таблиц).
"""
from __future__ import annotations

import re
import sqlite3
import tempfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
PROD_DB = Path.home() / "health" / "data" / "health.db"


def _fixture_tables() -> set[str]:
    sql = (ROOT / "tests" / "fixtures" / "health_schema.sql").read_text(encoding="utf-8")
    sql = re.sub(r"^\s*CREATE\s+TABLE\s+sqlite_sequence\s*\([^)]*\)\s*;\s*$",
                 "", sql, flags=re.IGNORECASE | re.MULTILINE)
    db = sqlite3.connect(":memory:")
    db.executescript(sql)
    return {r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}


def test_fixture_schema_covers_prod_tables():
    if not PROD_DB.exists():
        pytest.skip("боевая БД недоступна (не Studio) — гард дрейфа фикстуры пропущен")
    prod = sqlite3.connect(f"file:{PROD_DB}?mode=ro", uri=True)
    prod_tabs = {r[0] for r in prod.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' AND name NOT LIKE '%fts%' AND sql IS NOT NULL")}
    missing = sorted(prod_tabs - _fixture_tables())
    assert not missing, (
        "Фикстура tests/fixtures/health_schema.sql отстала от боевой схемы — "
        f"отсутствуют таблицы: {missing}. Добавь их (CREATE TABLE IF NOT EXISTS "
        "из боевой sqlite_master) и перезапусти."
    )
