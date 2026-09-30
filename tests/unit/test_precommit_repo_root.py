"""Pre-commit считает корень ТОГО дерева, которое коммитят.

Повод (13.09, догфудинг §21). Файл хука живёт в главной копии — `.git/hooks/
pre-commit` симлинк на неё, и он ОБЩИЙ для всех деревьев нитей. Пока корень
вычислялся от пути самого файла, коммит из дерева нити судился по файлам главной
копии при индексе нити (GIT_INDEX_FILE наследуется): застейдженные правки
объявлялись «незастейдженными», а pytest гейтов гонял чужой код — зелёный на
не-той версии (§12), спрятанный внутри хука.

Почему тест ЗАПУСКАЕТ хук, а не читает его исходник. В этом репозитории уже был
ровно такой тест-обманка: `watch_and_test.sh` девять суток считал своим корнем
«/», а сторож зеленел, потому что сверял НАПИСАНИЕ строки `SCRIPT_DIR=...`.
Оракул на вычисление обязан спрашивать вычисление.

Отдельная грабля, пойманная первым же ночным прогоном 13.09: `--print-root`
стоял НИЖЕ запрета коммитов на Studio, и на Studio — то есть ровно там, где
бежит полный набор, — хук отвечал отказом политики вместо корня. Проверка
механизма не может жить там, где механизм до неё не доходит. Запрос корня
поднят выше всех политик; то, что эти три теста зелёные на Studio, и есть
оракул на саму эту грабку.
"""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HOOK = ROOT / "scripts" / "git-hooks" / "pre-commit"


def _root_from(cwd: Path) -> str:
    r = subprocess.run(["/bin/bash", str(HOOK), "--print-root"],
                       cwd=str(cwd), capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


@pytest.mark.host_only
def test_root_is_the_tree_being_committed(tmp_path):
    """Хук, вызванный из ЧУЖОГО дерева, называет ЕГО корень, а не свой дом."""
    other = tmp_path / "repo"
    other.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=str(other), check=True)
    got = _root_from(other)
    assert Path(got).resolve() == other.resolve(), (
        f"хук назвал корнем {got!r} вместо дерева, из которого его позвали — "
        "это и есть дефект, из-за которого гейты судили чужие файлы")


def test_root_is_own_repo_when_called_from_it():
    """Негативный контроль: из своего дерева ответ прежний, регрессии нет."""
    assert Path(_root_from(ROOT)).resolve() == ROOT.resolve()


def test_falls_back_outside_any_repo(tmp_path):
    """Вне репозитория `rev-parse` молчит — остаётся путь от файла хука.
    Fallback обязан быть живым: без него хук упал бы с пустым корнем."""
    got = _root_from(tmp_path)
    assert got, "пустой корень — хук потерял бы doc_inventory и упал невнятно"
    assert Path(got).resolve() == ROOT.resolve()
