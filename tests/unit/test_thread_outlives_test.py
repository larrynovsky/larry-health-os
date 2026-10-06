"""Поток не переживает свой тест; исключение в потоке — красный (05.10.2026, test-thread-leak).

Замер 05.10: поток теста фонового консилиума дописывал вердикт после отката подмены базы — в
живую базу владельца, каждую ночь с 30.08; тест был зелёным, след — одна строка предупреждения
в ночном журнале. Хук вырезается из tests/conftest.py по маркерам и исполняется настоящим
pytest в отдельном процессе: оракул бьёт по тому коду, что идёт ночью, а не по пересказу.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]


def _guard_source() -> str:
    src = (ROOT / "tests" / "conftest.py").read_text(encoding="utf-8")
    m = re.search(r"# --- thread_guard begin ---\n(.*?)# --- thread_guard end ---", src, re.S)
    assert m, "маркеры thread_guard в tests/conftest.py пропали — тест бьёт мимо"
    return m.group(1)


def _run(tmp_path: Path, body: str, join_s: float = 0.5) -> subprocess.CompletedProcess:
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (tmp_path / "conftest.py").write_text(
        "import threading as _threading\nimport pytest\n" + _guard_source()
        + f"\n_THREAD_JOIN_S = {join_s}\n", encoding="utf-8")
    (tmp_path / "test_case.py").write_text(body, encoding="utf-8")
    ini = (ROOT / "pytest.ini").read_text(encoding="utf-8")
    fw = re.search(r"^filterwarnings =\n((?:[ \t]+\S.*\n)+)", ini, re.M)
    assert fw, "filterwarnings в pytest.ini пропал"
    w = [f"-W{x.strip()}" for x in fw.group(1).splitlines()]
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                           "--rootdir", str(tmp_path), "-c", str(tmp_path / "pytest.ini"), *w,
                           str(tmp_path / "test_case.py")],
                          capture_output=True, text=True, timeout=60, cwd=tmp_path)


LEAK = """
import threading, time
def test_leaves_thread():
    threading.Thread(target=time.sleep, args=(5,), daemon=True).start()
"""

FINISHES = """
import threading, time
box = []
def test_thread_finishes_during_join():
    threading.Thread(target=lambda: (time.sleep(0.2), box.append(1)), daemon=True).start()
def test_after():
    assert box == [1]
"""

RAISES = """
import threading
def test_thread_raises():
    t = threading.Thread(target=lambda: 1 / 0)
    t.start(); t.join()
"""


def test_thread_alive_after_join_is_red_with_name(tmp_path):
    r = _run(tmp_path, LEAK)
    assert "1 failed" in r.stdout, r.stdout + r.stderr
    assert "thread outlived its test" in r.stdout


def test_thread_is_awaited_before_fixture_teardown(tmp_path):
    """Поток, дописывающий за 0.2 с, дожидается в фазе вызова — следующий тест видит запись."""
    r = _run(tmp_path, FINISHES)
    assert "2 passed" in r.stdout, r.stdout + r.stderr


def test_exception_in_thread_is_red(tmp_path):
    r = _run(tmp_path, RAISES)
    assert "1 failed" in r.stdout, r.stdout + r.stderr


POOL = """
import threading, time
def test_pool_idle_worker():
    threading.Thread(target=time.sleep, args=(5,), name="AnyIO worker thread", daemon=True).start()
"""


def test_library_pool_idle_worker_is_named_exception(tmp_path):
    """Граница сторожа: простаивающий рабочий пула AnyIO не красит тест; любой другой — красит."""
    r = _run(tmp_path, POOL)
    assert "1 passed" in r.stdout, r.stdout + r.stderr

