"""Копии плистов в `launchd/` несут выбор поставщика ИИ, как живые плисты Studio.

09.11 (BL-LLM-PROVIDER-STRICT-1) живые плисты получили
EnvironmentVariables.HEALTH_LLM_PROVIDER=anthropic ручной миграцией (docs/how-to/llm_provider.md,
шаг 3). Копии в `launchd/` не тронули, и ночной датчик `check_launchd_inventory` варнил
расхождение по env:HEALTH_LLM_PROVIDER (карточка 2026-11-10).

ЭТАЛОН — значение той миграции, а не `placement.yaml::native_environment`: это запасное значение
compose-override, плисты его не берут (llm_provider.md:78-80, install.render_launchd). Замер
10.11 на Studio: у всех живых плистов, где ключ есть, значение anthropic (55 из 55).

ГРАНИЦА: тест проверяет согласованность внутри репозитория (§20 — читает только его файлы).
Причину карточки подтверждает лишь ночной `check_launchd_inventory` после деплоя.
"""
from __future__ import annotations

import plistlib
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
LAUNCHD = ROOT / "launchd"
MIGRATED_PROVIDER = "anthropic"   # миграция 09.11, llm_provider.md шаг 3; меняется вместе с живыми плистами
COPIES = sorted(LAUNCHD.glob("com.larry.*.plist"))   # корень — копии Studio


@pytest.mark.parametrize("path", COPIES, ids=lambda p: p.name)
def test_copy_is_parseable(path):
    """Битую копию `plist_env_liveness._load_plists` молча пропускает — сверка её не видит.
    Так жила копия constitutions: `--` внутри XML-комментария (найдено 2026-11-10)."""
    plistlib.loads(path.read_bytes())


def test_copies_exist():
    assert COPIES, "нет ни одной копии плиста в launchd/"


@pytest.mark.parametrize("path", COPIES, ids=lambda p: p.name)
def test_copy_with_env_carries_migrated_provider(path):
    """Разбор — в теле теста: битая копия краснеет адресно, а не роняет сбор модуля."""
    env = plistlib.loads(path.read_bytes()).get("EnvironmentVariables")
    if env is None:
        pytest.skip("копия без окружения (colima) — миграция её не касалась")
    assert env.get("HEALTH_LLM_PROVIDER") == MIGRATED_PROVIDER, (
        f"{path.name}: HEALTH_LLM_PROVIDER должен быть {MIGRATED_PROVIDER!r}, как в живых плистах после 09.11")
