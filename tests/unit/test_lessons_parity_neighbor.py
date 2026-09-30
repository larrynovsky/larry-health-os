"""Паритет общего движка и собственного скрипта настроенного соседнего проекта.

Решение владельца 28.09.2026: у соседнего проекта остаётся свой scripts/lessons.py
(у партнёров нет health_scripts, их свод запрещает копировать чужой механизм), формат общий.
На одном файле сверяются форма, группы и подача по КАЖДОМУ ключу предмета.

Пути — только из infra_config.NEIGHBORS. Нет соседей — нечего сверять. Нет скрипта
на этой машине — пропуск, как прежде. Расхождение с другой стороны этот сторож
увидит при следующей правке движка, а не в момент правки соседнего проекта.
Синтетический контроль ниже проверяет сам сторож и его способность краснеть.
"""
import contextlib
import io
import json
import subprocess
import sys

import pytest

import infra_config
from project_context import lessons

pytestmark = pytest.mark.unit


def _engine(argv, root):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = lessons.main(argv, root=root)
    return rc, buf.getvalue()


def _theirs(argv, root, script):
    r = subprocess.run([sys.executable, str(script), *argv], cwd=root,
                       capture_output=True, text=True, timeout=60)
    return r.returncode, r.stdout, r.stderr


def _compare(neighbor):
    root = neighbor["lessons"].parent
    script = neighbor["path"] / "scripts" / "lessons.py"
    rc_e, out_e = _engine([], root)
    rc_t, _, err_t = _theirs([], root, script)
    assert rc_e == rc_t, f"проверка формы: движок {rc_e}, их скрипт {rc_t}\n{out_e}\n{err_t}"
    assert sorted(out_e.splitlines()) == sorted(err_t.splitlines())
    assert _engine(["--groups"], root)[1] == _theirs(["--groups"], root, script)[1]
    for key in lessons.load(root).get("subjects") or {}:
        assert _engine(["--subject", key], root)[1] == _theirs(["--subject", key], root, script)[1], key


@pytest.mark.parametrize("neighbor", [n for n in infra_config.NEIGHBORS.values() if n.get("lessons")])
def test_engine_matches_neighbor_script(neighbor):
    if not (neighbor["path"] / "scripts" / "lessons.py").is_file():
        pytest.skip("скрипта соседнего проекта нет на этой машине")
    _compare(neighbor)


@pytest.mark.parametrize("fault", [None, "rc", "form", "groups", "alpha", "beta"])
def test_parity_oracle_green_and_red_on_neutral_neighbor(tmp_path, monkeypatch, fault):
    root = tmp_path / "lab-notes"
    (root / "scripts").mkdir(parents=True)
    (root / "lessons.yaml").write_text(
        'address_kinds: [rule]\nsubjects: {alpha: First, beta: Second}\nlessons: []\n')
    # Независимые эталоны CLI: фиктивный сосед не импортирует наш движок.
    outputs = {"groups": "", "alpha": "по предмету «alpha» подавать нечего\n",
               "beta": "по предмету «beta» подавать нечего\n"}
    if fault in outputs:
        outputs[fault] = "расхождение\n"
    script = f'''import sys
outputs = {outputs!r}
args = sys.argv[1:]
if args == ["--groups"]:
    print(outputs["groups"], end="")
elif args[:1] == ["--subject"]:
    print(outputs[args[1]], end="")
else:
    print({"расхождение" if fault == "form" else ""!r}, end="", file=sys.stderr)
    sys.exit({1 if fault == "rc" else 0})
'''
    (root / "scripts" / "lessons.py").write_text(script)
    monkeypatch.setattr(infra_config, "NEIGHBORS", infra_config._neighbors({
        "lab-notes": {"path": str(root), "lessons": "lessons.yaml"},
    }))
    if fault is None:
        _compare(infra_config.NEIGHBORS["lab-notes"])
    else:
        with pytest.raises(AssertionError):
            _compare(infra_config.NEIGHBORS["lab-notes"])
