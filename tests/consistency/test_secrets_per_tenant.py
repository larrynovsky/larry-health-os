"""Multitenancy (2026-06-30): Telegram-секреты изолированы по HEALTH_SECRETS_DIR.

R5: owner-check / доставка алёрта тенанта A не должны резолвиться в секреты
тенанта B. notify и bot.filters берут token/chat_id из HEALTH_SECRETS_DIR
текущего процесса (default ~/.health_secrets = канон владельца), без fallback на
чужой каталог.

Каждый тенант — отдельный процесс (так в проде), поэтому проверяем через
subprocess: это и вернее, и не загрязняет module-кэш pytest. Уровень: consistency.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.consistency

_REPO = Path(__file__).resolve().parents[2]


def _run(code: str, secrets_dir: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "HEALTH_SECRETS_DIR": secrets_dir}
    return subprocess.run(
        [sys.executable, "-c", code],
        env=env, cwd=str(_REPO), capture_output=True, text=True, timeout=60,
    )


def _mk_tenant(tmp_path: Path, name: str, chat_id: int) -> Path:
    d = tmp_path / name
    d.mkdir()
    (d / "telegram_token").write_text(f"token-{name}")
    (d / "telegram_chat_id").write_text(str(chat_id))
    return d


def test_notify_secrets_follow_tenant(tmp_path):
    a = _mk_tenant(tmp_path, "tenant_a", 111)
    b = _mk_tenant(tmp_path, "tenant_b", 222)
    code = "import notify; print(notify._SECRETS)"
    out_a = _run(code, str(a)).stdout.strip()
    out_b = _run(code, str(b)).stdout.strip()
    assert out_a == str(a), out_a
    assert out_b == str(b), out_b
    assert out_a != out_b  # процесс B не видит каталог A


def test_filters_chat_id_follows_tenant(tmp_path):
    a = _mk_tenant(tmp_path, "f_a", 111)
    b = _mk_tenant(tmp_path, "f_b", 222)
    code = "import bot.filters as f; print(f.get_chat_id())"
    r_a = _run(code, str(a))
    if r_a.returncode != 0 and "telegram" in r_a.stderr.lower():
        pytest.skip("python-telegram-bot не установлен в этом окружении")
    assert r_a.stdout.strip() == "111", r_a.stderr
    r_b = _run(code, str(b))
    assert r_b.stdout.strip() == "222", r_b.stderr


# ── D2 (2026-07-23): secrets_dir() сведён на is_owner() — единый сигнал владельца. ──
# Морозит поведение резолвера по 4 комбинациям env (owner/tenant × secrets задан/нет),
# чтобы рефактор «inline dd/name → is_owner()» не изменил ничего тихо. Fail-closed сохранён.
_D2_CODE = (
    "import secrets_paths as s\n"
    "print('owner', s.is_owner())\n"
    "try:\n"
    "    print('dir', s.secrets_dir())\n"
    "except Exception as e:\n"
    "    print('dir RAISE', type(e).__name__)\n"
)


def _run_d2(tmp_path, data_dir=None, secrets_dir=None):
    env = {**os.environ}
    env.pop("HEALTH_DATA_DIR", None)
    env.pop("HEALTH_SECRETS_DIR", None)
    if data_dir is not None:
        env["HEALTH_DATA_DIR"] = data_dir
    if secrets_dir is not None:
        env["HEALTH_SECRETS_DIR"] = secrets_dir
    r = subprocess.run([sys.executable, "-c", _D2_CODE], env=env, cwd=str(_REPO),
                       capture_output=True, text=True, timeout=60)
    return r.stdout.strip()


def test_d2_owner_default(tmp_path):
    """Владелец без env → is_owner True, secrets_dir = ~/.health_secrets (дефолт легитимен)."""
    out = _run_d2(tmp_path)
    assert "owner True" in out, out
    assert f"dir {Path.home() / '.health_secrets'}" in out, out


def test_d2_owner_explicit_health(tmp_path):
    """HEALTH_DATA_DIR=.../health (имя каталога health) → всё ещё владелец."""
    d = tmp_path / "health"; d.mkdir()
    out = _run_d2(tmp_path, data_dir=str(d))
    assert "owner True" in out, out
    assert f"dir {Path.home() / '.health_secrets'}" in out, out


def test_d2_tenant_fail_closed(tmp_path):
    """Тенант (имя ≠ health) без HEALTH_SECRETS_DIR → is_owner False + secrets_dir ОТКАЗ
    (иначе взял бы секреты владельца — доказанная кросс-тенант утечка)."""
    d = tmp_path / "partner"; d.mkdir()
    out = _run_d2(tmp_path, data_dir=str(d))
    assert "owner False" in out, out
    assert "dir RAISE RuntimeError" in out, out


def test_d2_tenant_with_secrets(tmp_path):
    """Тенант с заданным HEALTH_SECRETS_DIR → is_owner False, secrets_dir = его каталог."""
    d = tmp_path / "partner"; d.mkdir()
    s = tmp_path / "partner_secrets"; s.mkdir()
    out = _run_d2(tmp_path, data_dir=str(d), secrets_dir=str(s))
    assert "owner False" in out, out
    assert f"dir {s}" in out, out
