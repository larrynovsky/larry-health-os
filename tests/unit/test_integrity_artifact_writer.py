"""Артефакт монитора остаётся ЧИТАЕМЫМ в прогон с падениями (2026-08-10).

Инцидент: `logs/integrity_latest.json` содержал два JSON-документа подряд —
полный отчёт плюс аварийный литерал `{"fail":1,...}`. `json.load` падал с
`Extra data: line 73 column 1`, и `triage_agent` с `night_cycle` разбирать его
не могли. Причина — одна строка `run_checks.sh`:

    INTEGRITY_JSON=$($PY integrity_tests.py --json || echo '{"fail":1,...}')

`$(A || B)` не выбирает между A и B: исполняет A, забирает её stdout и при
ненулевом коде ДОПИСЫВАЕТ stdout B. Монитор возвращает ненулевой код именно
когда есть падения — значит канал доставки глох ровно в тот прогон, ради
которого он и существует.

Тесты гоняют НАСТОЯЩИЙ `run_checks.sh` через шов `HEALTH_PY`, а не копию его
логики: стеречь копию значило бы стеречь не тот код, что работает ночью.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]

_REPORT = '{\n  "pass": 133,\n  "fail": 1,\n  "warn": 15,\n  "failures": [["носитель проб", "сломана оснастка"]]\n}'


def _stub(tmp_path: Path, *, stdout: str, code: int) -> Path:
    """Заглушка питона: на `integrity_tests.py --json` печатает то, что велено,
    и выходит с заданным кодом; на всё остальное честно зовёт настоящий питон
    (иначе `parse_int` и соседние шаги сломались бы и тест мерил бы не то)."""
    real = shutil.which("python3.11") or shutil.which("python3")
    p = tmp_path / "py_stub.sh"
    p.write_text(
        "#!/bin/bash\n"
        'case "$*" in\n'
        "  *integrity_tests.py*--json*)\n"
        f"    cat <<'EOF_STUB'\n{stdout}\nEOF_STUB\n"
        f"    exit {code} ;;\n"
        "esac\n"
        f'exec "{real}" "$@"\n',
        encoding="utf-8")
    p.chmod(0o755)
    return p


def _run(tmp_path: Path, *, stdout: str, code: int, preexisting: str | None = None):
    work = tmp_path / "repo"
    work.mkdir()
    shutil.copy(ROOT / "run_checks.sh", work / "run_checks.sh")
    (work / "logs").mkdir()
    art = work / "logs" / "integrity_latest.json"
    if preexisting is not None:
        art.write_text(preexisting, encoding="utf-8")
    env = dict(os.environ, HEALTH_PY=str(_stub(tmp_path, stdout=stdout, code=code)))
    subprocess.run(["bash", str(work / "run_checks.sh"), "--scheduled"],
                   cwd=work, env=env, capture_output=True, timeout=120)
    return art


def test_artifact_is_one_document_when_run_has_failures(tmp_path):
    """⭐ Ровно инцидент: монитор нашёл падения и вышел ненулевым кодом.

    Артефакт обязан остаться ОДНИМ разбираемым документом. Аварийный литерал
    не имеет права примешаться к настоящему выводу.
    """
    art = _run(tmp_path, stdout=_REPORT, code=1)
    assert art.exists(), "артефакт не написан вовсе"
    data = json.loads(art.read_text(encoding="utf-8"))   # красное = Extra data
    assert data["fail"] == 1 and data["pass"] == 133, data


def test_clean_run_still_writes_the_artifact(tmp_path):
    """Негатив: зелёный прогон писал артефакт и обязан продолжать."""
    art = _run(tmp_path, stdout='{"pass": 140, "fail": 0, "warn": 2}', code=0)
    assert json.loads(art.read_text(encoding="utf-8"))["pass"] == 140


def test_unparseable_output_keeps_the_previous_artifact(tmp_path):
    """Нечитаемый свежий хуже читаемого вчерашнего.

    Если монитор выдал мусор (упал на импорте, оборвался), прежний артефакт
    обязан уцелеть: затерев его, мы отдали бы триажу пустоту, и он честно
    доложил бы «проблем нет». Тихая ложь дороже устаревшей правды.
    """
    old = '{"pass": 99, "fail": 0, "warn": 1}'
    art = _run(tmp_path, stdout="Traceback (most recent call last):\n  boom",
               code=1, preexisting=old)
    assert json.loads(art.read_text(encoding="utf-8"))["pass"] == 99


def test_empty_output_falls_back_without_corrupting(tmp_path):
    """Монитор не сказал НИЧЕГО — фолбэк обязан сработать и остаться один."""
    art = _run(tmp_path, stdout="", code=2)
    assert json.loads(art.read_text(encoding="utf-8"))["fail"] == 1


def test_dangerous_substitution_is_not_back(tmp_path):
    """Сторож формы, а не поведения: `|| echo` ВНУТРИ подстановки — тот самый дефект.

    Поведенческие тесты выше поймают его на исполнении, этот — на чтении диффа,
    раньше и дешевле.
    """
    src = (ROOT / "run_checks.sh").read_text(encoding="utf-8")
    body = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    assert "--json 2>/dev/null || echo" not in body, (
        "фолбэк вернулся внутрь $(...) — вывод дописывается к настоящему")


if __name__ == "__main__":
    import tempfile
    for _n, _f in sorted(globals().items()):
        if _n.startswith("test_"):
            with tempfile.TemporaryDirectory() as d:
                _f(Path(d))
            print("ok", _n)
