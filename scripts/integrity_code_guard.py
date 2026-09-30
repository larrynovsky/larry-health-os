#!/usr/bin/env python3.11
"""integrity_code_guard.py — pre-commit страж класса «сломанный check()».

Закрывает дыру (инцидент 2026-07-17): датчик в integrity_tests с ошибкой КОДА (NameError из-за
forward-ref, опечатка, битый импорт, несовпадение сигнатуры) проходит `test_on_studio` вникуда —
монитор там НЕ запускается (он про живые данные), а `check()`-обёртка ГЛОТАЕТ исключение → pytest
зелёный. Всплывает лишь в ночном `integrity_tests.py --json` с алертом человеку (лаг до 07:50).

Ключ различения: `check()` пишет AssertionError (провал ДАННЫХ — на суточном снапшоте ложен) и
любое ДРУГОЕ исключение (ошибка КОДА — валидна ВСЕГДА, снапшот не важен) в разные списки. Этот
страж гоняет монитор и падает ТОЛЬКО на `code_failures`, игнорируя data-FAIL/WARN. Поэтому его
можно ставить в pre-commit-гейт (test_on_studio.sh шаг 5) на снапшоте канона.

Запуск: python3.11 scripts/integrity_code_guard.py   (в окружении с HEALTH_DATA_DIR, напр. staging)
Exit: 0 — код чист; 1 — есть код-ошибки (перечислены); 2 — монитор не смог выдать --json (тоже баг).
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable or "/opt/homebrew/bin/python3.11"

r = subprocess.run([PY, str(ROOT / "integrity_tests.py"), "--json"],
                   capture_output=True, text=True)
try:
    data = json.loads(r.stdout)
except Exception:
    print("❌ integrity code-guard: монитор не выдал парсируемый --json "
          "(вероятно код-ошибка на импорте). stderr:", file=sys.stderr)
    print((r.stderr or r.stdout)[-600:], file=sys.stderr)
    sys.exit(2)

code_failures = data.get("code_failures", [])
if code_failures:
    print(f"❌ integrity code-guard: {len(code_failures)} КОД-ошибок в integrity_tests "
          "(сломанный check, НЕ провал данных):")
    for label, msg in code_failures:
        print(f"   ❌ {label} — {msg}")
    print("Почини ДО коммита (data-FAIL на снапшоте — отдельно, их страж игнорирует).")
    sys.exit(1)

print(f"✅ integrity code-guard: 0 код-ошибок "
      f"(data-FAIL={data.get('fail', 0)} на снапшоте игнорируются как ложные).")
sys.exit(0)
