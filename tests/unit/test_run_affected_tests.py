"""Оракул: вердикт прогона затронутых тестов РАЗЛИЧАЕТ три разных исхода.

ЧТО СТЕРЕЖЁТ. До 12.09 прогон судился одним признаком — кодом возврата pytest.
Он одинаков для «тесты красные» и для «файл не собрался, прогон оборван». Хук
печатал «ЗАТРОНУТЫЕ ТЕСТЫ КРАСНЫЕ» в обоих случаях, и во втором это была ложь:
проверено ноль. Замер того дня: 29 файлов из набора не собирались вовсе, то
есть попадание в яму было бытом, а не редкостью.

ПОЧЕМУ ТРИ СЛУЧАЯ, А НЕ ОДИН. Датчик, который отличает только «хорошо» от
«плохо», нельзя починить — по его выходу не видно, что именно сломалось.
Проверяем ровно ту величину, ради которой заведён скрипт: что «зелёное»,
«красное» и «непроверенное» — три разных ответа, а не два.

ГРАНИЦА ЧЕСТНО. Тест судит СКРИПТ, а не хук: что pre-commit зовёт именно его и
печатает человеку правильную строку — отдельное утверждение, под отдельным
ратчетом ниже. Полноту отбора тестов (`affected_tests.py`) он тоже не судит —
она неполна по построению, и это сказано в самом хуке.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.test_meta(status="implemented", confirmation="confirmed"),
]

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "run_affected_tests.sh"

ЗЕЛЁНЫЙ = "def test_ok():\n    assert True\n"
КРАСНЫЙ = "def test_bad():\n    assert False, 'нарочно'\n"
# Ошибка НА ИМПОРТЕ — ровно то, что даёт `secrets_dir` и отсутствующий numpy:
# файл не собирается, и pytest без спец-флага обрывает на нём всю сессию.
НЕСОБИРАЕМЫЙ = "import модуль_которого_нет  # noqa\n\ndef test_never():\n    pass\n"


def _прогон(tmp_path: Path, файлы: dict[str, str]) -> tuple[int, dict[str, int], str]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    пути = []
    for имя, тело in файлы.items():
        p = tmp_path / имя
        p.write_text(textwrap.dedent(тело), encoding="utf-8")
        пути.append(str(p))

    env = dict(os.environ)
    env["PYTHON"] = sys.executable
    env["AFFECTED_LOG"] = str(tmp_path / "run.log")
    env["AFFECTED_XML"] = str(tmp_path / "run.xml")
    for v in ("GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE"):
        env.pop(v, None)

    r = subprocess.run(["bash", str(SCRIPT), *пути], cwd=str(REPO_ROOT),
                       capture_output=True, text=True, timeout=300, env=env)
    числа = {}
    for кусок in r.stdout.split():
        if "=" in кусок:
            k, v = кусок.split("=", 1)
            if v.isdigit():
                числа[k] = int(v)
    return r.returncode, числа, r.stdout + r.stderr


def test_всё_зелёное_даёт_ноль(tmp_path):
    rc, n, out = _прогон(tmp_path, {"test_a.py": ЗЕЛЁНЫЙ, "test_b.py": ЗЕЛЁНЫЙ})
    assert rc == 0, f"зелёный набор дал код {rc}:\n{out}"
    assert n.get("passed") == 2 and n.get("errors") == 0, out


def test_упавший_тест_отличается_от_несобравшегося(tmp_path):
    """Главное утверждение: два разных события — два разных ответа."""
    rc_кр, n_кр, out_кр = _прогон(tmp_path / "кр", {"test_a.py": КРАСНЫЙ})
    rc_нс, n_нс, out_нс = _прогон(tmp_path / "нс", {"test_a.py": НЕСОБИРАЕМЫЙ})

    assert rc_кр == 1, f"упавший тест должен давать 1, дал {rc_кр}:\n{out_кр}"
    assert rc_нс == 2, f"несобравшийся файл должен давать 2, дал {rc_нс}:\n{out_нс}"
    assert rc_кр != rc_нс, (
        "«красное» и «непроверенное» неразличимы по коду возврата — вернулся "
        "ровно тот дефект, ради которого скрипт заведён")
    assert n_кр.get("failed") == 1 and n_кр.get("errors") == 0, out_кр
    assert n_нс.get("errors") == 1 and n_нс.get("failed") == 0, out_нс


def test_несобравшийся_файл_не_отменяет_прогон_остальных(tmp_path):
    """Ядро правки. Без --continue-on-collection-errors pytest обрывает сессию
    на первом же несобравшемся файле, и вердикт относится не к тому набору:
    два здоровых теста рядом не выполнятся вовсе."""
    rc, n, out = _прогон(tmp_path, {
        "test_a.py": ЗЕЛЁНЫЙ, "test_b.py": НЕСОБИРАЕМЫЙ, "test_c.py": ЗЕЛЁНЫЙ})
    assert rc == 2, f"набор с несобравшимся файлом должен давать 2, дал {rc}:\n{out}"
    assert n.get("passed") == 2, (
        "здоровые тесты не выполнились — один несобравшийся файл снова отменил "
        f"весь прогон:\n{out}")
    assert n.get("errors") == 1, out


def test_пустой_вход_не_притворяется_зелёным_прогоном(tmp_path):
    """Ноль тестов — законный исход (нечего гонять), но он обязан читаться как
    ноль, а не как «всё проверено»."""
    env = dict(os.environ)
    env["PYTHON"] = sys.executable
    r = subprocess.run(["bash", str(SCRIPT)], cwd=str(REPO_ROOT),
                       capture_output=True, text=True, timeout=60, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "total=0" in r.stdout, r.stdout


def test_хук_зовёт_скрипт_и_различает_исходы():
    """Ратчет: сам pre-commit обязан пользоваться скриптом и РАЗБИРАТЬ его код.

    Без этого скрипт может быть сколь угодно честным, а хук — по-прежнему
    печатать одну строку на все случаи. Проверяется носитель, не поведение:
    граница названа в докстроке модуля.

    МАРКЕР — СТРОКА КОДА, А НЕ СЛОВО. Первая редакция искала подстроку
    «run_affected_tests.sh» где угодно в файле. Мутация (хук зовёт pytest
    напрямую) осталась ЗЕЛЁНОЙ: имя скрипта упомянуто ещё и в комментарии над
    вызовом, и ратчет цеплялся за прозу, пережившую удаление кода. Ровно этот
    дефект внешнее ревью нашло в реестре соседний проект (О-14) несколькими часами
    раньше — и я повторил его здесь, в тот же день. Поэтому маркер теперь —
    подстрока, которая исчезает вместе с вызовом.
    """
    hook = (REPO_ROOT / "scripts" / "git-hooks" / "pre-commit").read_text(encoding="utf-8")

    ВЫЗОВ = '"$REPO_ROOT/scripts/run_affected_tests.sh" $_AFF'
    assert ВЫЗОВ in hook, (
        "pre-commit не зовёт скрипт — вердикт снова снимается кодом возврата "
        "pytest, а он не различает «красное» и «непроверенное»")

    # Разбор исхода: без него три ответа скрипта схлопываются обратно в один.
    assert "_ARC=$?" in hook and "case $_ARC in" in hook, (
        "pre-commit не разбирает код возврата скрипта")
    for ветка, смысл in (("      2)", "часть не проверена"),
                         ("      1)", "тесты красные"),
                         ("      0)", "всё зелёное")):
        assert ветка in hook, (
            f"pre-commit не различает исход «{смысл}» — ветка пропала")
