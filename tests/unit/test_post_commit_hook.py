"""C1 (audit 2026-06-17): post-commit перезапускает бота только при .py/.plist.

Тестируем вынесенную проверку scripts/git-hooks/should_restart_bot.sh
(exit 0 = рестарт нужен). Принцип #5: позитив + негатив.
"""
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parent.parent.parent
SHOULD = ROOT / "scripts" / "git-hooks" / "should_restart_bot.sh"


def _restart_needed(files) -> bool:
    """exit 0 → True (рестарт нужен), exit 1 → False."""
    rc = subprocess.run(["bash", str(SHOULD), *files]).returncode
    return rc == 0


def test_script_exists_and_executable():
    assert SHOULD.exists(), f"{SHOULD} отсутствует"


def test_md_only_no_restart():
    assert _restart_needed(["a.md"]) is False
    assert _restart_needed(["docs/x.md", "CHANGELOG.md"]) is False


def test_py_triggers_restart():
    assert _restart_needed(["b.py"]) is True
    assert _restart_needed(["a.md", "handlers/meta.py"]) is True


def test_plist_triggers_restart():
    assert _restart_needed(["com.larry.health.bot.plist"]) is True


def test_empty_no_restart():
    assert _restart_needed([]) is False


# ── MacBook deploy-хук: рестарт дашборда (инцидент 2026-07-11) ──────────────────
MACBOOK_HOOK = ROOT / "scripts" / "git-hooks" / "post-commit-macbook"


def test_macbook_hook_restarts_dashboard():
    """Регресс-guard: MacBook-деплой обязан рестартить дашборд (owner+partner).
    Инцидент 2026-07-11: HAE freshness-fix ехал на СТАРОМ коде, пока дашборд не
    перезапустили руками — post-commit рестартил только бота. Ловит удаление строки."""
    assert MACBOOK_HOOK.exists(), f"{MACBOOK_HOOK} отсутствует"
    txt = MACBOOK_HOOK.read_text()
    assert "com.larry.health.dashboard" in txt and "kickstart" in txt, \
        "post-commit-macbook больше не рестартит дашборд — эндпоинты поедут на старом коде"
