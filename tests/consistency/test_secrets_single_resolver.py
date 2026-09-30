"""Единственность резолвера секретов + fail-closed импорт bot.filters.

Класс бага (обзор 2026-07-02, продолжение инцидента кросс-тенант утечки):
локальные копии логики `env HEALTH_SECRETS_DIR или ~/.health_secrets` в
модулях (bot/filters, google_calendar_fetcher) — дубль значения. Пока копии
идентичны — работает; разъедутся — split-brain маршрутизации секретов.
check_contracts ловит хардкод БЕЗ env-awareness; этот датчик ловит следующий
слой — env-aware, но СВОЙ резолвер вместо канонического secrets_paths.

Плюс: fail-closed инвариант bot.filters — отсутствие chat_id БЛОКИРУЕТ
(owner_chat_id() raise, старт-гейт raise), а не «подставим дефолт». Резолв
ЛЕНИВЫЙ (2026-07-17): импорт больше НЕ падает (иначе сбор pytest tests/ хрупок
к утечке env — инцидент 2026-07-15) — fail-closed перенесён на ЧТЕНИЕ и СТАРТ.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.consistency

_REPO = Path(__file__).resolve().parents[2]

# Паттерны самостоятельного резолва (оба стиля кавычек).
_RESOLVE_PATTERNS = (
    'environ.get("HEALTH_SECRETS_DIR"',
    "environ.get('HEALTH_SECRETS_DIR'",
)


def test_secrets_single_resolver():
    offenders = []
    for py in _REPO.rglob("*.py"):
        rel = str(py.relative_to(_REPO))
        if ("__pycache__" in rel or rel.startswith("tests/")
                or rel == "secrets_paths.py"):
            continue
        src = py.read_text(encoding="utf-8", errors="ignore")
        if any(p in src for p in _RESOLVE_PATTERNS):
            offenders.append(rel)
    assert not offenders, (
        f"Самостоятельный резолв HEALTH_SECRETS_DIR вне secrets_paths.py: {offenders}. "
        "Используй from secrets_paths import secrets_dir — единый источник.")


def test_bot_filters_import_safe_without_chat_id(tmp_path):
    """Пустой каталог секретов → import bot.filters НЕ падает (ленивый резолв,
    2026-07-17). Раньше импорт райзил на module-level → сбор pytest tests/ был
    хрупок к утечке env. Fail-closed перенесён на ЧТЕНИЕ/СТАРТ (проверки ниже).
    Subprocess — как в проде (тенант = процесс), без загрязнения module-кэша."""
    empty = tmp_path / "empty_secrets"
    empty.mkdir()
    r = subprocess.run(
        [sys.executable, "-c", "import bot.filters"],
        env={**os.environ, "HEALTH_SECRETS_DIR": str(empty)},
        cwd=str(_REPO), capture_output=True, text=True, timeout=60,
    )
    if r.returncode != 0 and "No module named 'telegram'" in r.stderr:
        pytest.skip("python-telegram-bot не установлен в этом окружении")
    assert r.returncode == 0, (
        "import bot.filters УПАЛ при отсутствии chat_id — резолв не ленивый "
        f"(сбор pytest снова хрупок к утечке env):\n{r.stderr}")


def test_owner_chat_id_and_start_gate_fail_closed_without_secret(tmp_path):
    """Fail-closed на ЧТЕНИИ и СТАРТЕ: без секрета owner_chat_id() и
    assert_owner_configured() райзят (НИКОГДА не None — иначе auth-чек
    `id != None` короткозамкнётся в fail-OPEN). Subprocess — изоляция env."""
    empty = tmp_path / "empty_secrets"
    empty.mkdir()
    prog = (
        "import bot.filters as f\n"
        "import sys\n"
        "for fn in (f.owner_chat_id, f.assert_owner_configured):\n"
        "    try:\n"
        "        fn(); print('NO_RAISE', fn.__name__); sys.exit(2)\n"
        "    except RuntimeError:\n"
        "        pass\n"
        "print('OK')\n"
    )
    r = subprocess.run(
        [sys.executable, "-c", prog],
        env={**os.environ, "HEALTH_SECRETS_DIR": str(empty)},
        cwd=str(_REPO), capture_output=True, text=True, timeout=60,
    )
    if r.returncode != 0 and "No module named 'telegram'" in r.stderr:
        pytest.skip("python-telegram-bot не установлен в этом окружении")
    assert r.returncode == 0 and "OK" in r.stdout, (
        "owner_chat_id()/assert_owner_configured() не fail-closed без секрета:\n"
        f"stdout={r.stdout}\nstderr={r.stderr}")
