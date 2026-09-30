"""Оракул на вычисление дерева в watch_and_test.sh.

Зачем он есть (13.09). Правка 02.09 «дерево вычисляется, не задаётся» взяла
${BASH_SOURCE[0]} — переменную, которой в zsh не существует, — а шапка скрипта и
плист оба зовут zsh. SCRIPT_DIR схлопнулся в "/", и девять суток вотчер обходил
всю файловую систему, писал лог в //logs и делил замок с песочницей. Ни один тест
этого не поймал, потому что теста не было вовсе.

§20: зелёный обязан быть причинён тестом, а не окружением. Поэтому проверка гоняет
скрипт ТЕМ ЖЕ интерпретатором, что плист (/bin/zsh), из ТОГО ЖЕ каталога, из
которого его стартует launchd (cwd="/"), и на КОПИИ в tmp — чтобы совпадение с
боевым путём не могло сделать её зелёной.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
import pytest

SRC = Path(__file__).resolve().parents[2] / "watch_and_test.sh"
ZSH = "/bin/zsh"


def _tree_of(script: Path) -> str:
    """Что скрипт считает своим деревом, при запуске из / как это делает launchd."""
    r = subprocess.run([ZSH, str(script), "--print-tree"],
                       cwd="/", capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, f"--print-tree упал: {r.returncode} {r.stderr!r}"
    return r.stdout.strip()


def test_shebang_is_zsh():
    """Позитив контракта: тест гоняет zsh не по своему выбору, а потому что так зовут
    шапка и плист. Сменят шапку на bash — тест обязан это заметить, иначе он начнёт
    проверять не тот интерпретатор и снова разойдётся с боевым запуском."""
    assert SRC.read_text(encoding="utf-8").splitlines()[0].strip() == "#!/bin/zsh"


@pytest.mark.host_only
def test_tree_is_own_directory_under_zsh(tmp_path):
    """Позитив: копия в tmp считает своим деревом СВОЙ каталог, а не cwd запуска."""
    dst = tmp_path / "watch_and_test.sh"
    shutil.copy(SRC, dst)
    dst.chmod(0o755)
    assert _tree_of(dst) == os.path.realpath(tmp_path)


@pytest.mark.host_only
def test_negative_control_bash_source_form_collapses_to_root(tmp_path):
    """Исполненный негативный контроль: ПРЕЖНЯЯ форма под тем же zsh даёт "/".

    Без этой проверки позитив выше зеленел бы и на сломанном скрипте, запущенном из
    правильного каталога, — то есть доказывал бы не механизм, а везение с cwd."""
    broken = tmp_path / "broken.sh"
    broken.write_text(
        '#!/bin/zsh\n'
        'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n'
        'printf \'%s\\n\' "$SCRIPT_DIR"\n',
        encoding="utf-8")
    broken.chmod(0o755)
    r = subprocess.run([ZSH, str(broken)], cwd="/", capture_output=True, text=True, timeout=30)
    assert r.stdout.strip() == "/", (
        "прежняя форма перестала схлопываться в / — значит, дискриминатор этого "
        f"оракула мёртв и позитив выше ничего не доказывает (получено {r.stdout!r})")
