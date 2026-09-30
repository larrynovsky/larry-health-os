"""Каждый доменный *_db, который ре-экспортирует health_db, импортируется ПЕРВЫМ в чистом процессе.

BL-TEST-COLLECT-ALONE-1 (2026-09-24): 28 из 29 модулей падали на цикле (модуль → health_db →
`from <модуль> import ...` у недостроенного модуля), если их импортировали раньше health_db.
Жило конвенцией «import health_db ПЕРВЫМ»; совет урока «запустите упавший тест отдельно» на
test_brief_schedule не работал. Список модулей берётся из самого health_db, а не руками."""
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _reexported() -> list[str]:
    src = (REPO / "health_db.py").read_text(encoding="utf-8")
    return sorted(set(re.findall(r"^from ([a-z_]+_db) import", src, re.M)))


def test_reexported_list_is_not_empty():
    assert len(_reexported()) >= 20        # иначе тест ниже проверял бы пустоту


def test_each_domain_module_imports_first(tmp_path):
    env = {**os.environ, "HEALTH_DATA_DIR": str(tmp_path), "PYTHONPATH": str(REPO)}
    bad = {}
    for m in _reexported():
        r = subprocess.run([sys.executable, "-c", f"import {m}"], cwd=REPO, env=env,
                           capture_output=True, text=True, timeout=60)
        if r.returncode:
            bad[m] = (r.stderr.strip().splitlines() or ["?"])[-1][:160]
    assert not bad, f"модуль нельзя импортировать первым (цикл с health_db): {bad}"
