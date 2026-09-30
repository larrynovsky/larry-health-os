"""Гейт одноразовости на НАСТОЯЩЕМ пути: CLI → check() → снимок индекса → git, в одноразовом
клоне репозитория.

ЗАЧЕМ ОТДЕЛЬНЫЙ СЛОЙ. Внешнее ревью вернуло REQUEST CHANGES при 21 зелёном контроле, потому что
все они проверяли ЧИСТЫЙ evaluate_new_modules — ось Function. Четыре обхода жили на осях
Structure (путь хук → CLI → check) и Interfaces (граница индекс/рабочее дерево), и оттуда их не
видно ни одним юнит-тестом: evaluate получает источники уже инжектированными, то есть заведомо
когерентными. Этот файл — недостающий слой.

ЧТО ЭТОТ ФАЙЛ ДОКАЗЫВАЕТ И ЧЕГО НЕ ДОКАЗЫВАЕТ. Он исполняет ту же команду, что зовёт хук
(`python -m project_context dispgate --block`), в настоящем git-репозитории с настоящим индексом.
Он НЕ исполняет pre-commit целиком: там ещё пять гейтов со своими условиями (квитанция preflight,
дубль-гейт), и падение любого из них перекрасило бы этот контроль по чужой причине. Связка
«хук зовёт именно эту команду и пробрасывает её код выхода» проверяется отдельно —
`tests/unit/test_dispgate.py::test_gate_is_wired_into_canonical_hook`. Названо вслух, чтобы
покрытие не выглядело шире, чем оно есть.

BASELINE. Прогон этого файла против кода до ремонта (`2cd294e`) даёт 8 КРАСНЫХ из 12; зелёными
остаются ровно четыре негат-контроля, которые обязаны быть зелёными и до, и после. Контроль,
зелёный по обе стороны правки, не доказывает ничего — проверено 2026-07-26 на клоне.

ПОЧЕМУ КЛОН СИНХРОНИЗИРУЕТСЯ С РАБОЧИМ ДЕРЕВОМ. `git clone` берёт HEAD, то есть проверял бы
ЗАКОММИЧЕННЫЙ гейт. Для приёмки ремонта это ложно-зелёный класс §12 наоборот: правка есть, а
контроль её не видит. Поэтому пакет в клон копируется из рабочего дерева и коммитится там.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

import pytest

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_CLI = [sys.executable, "-m", "project_context", "dispgate", "--block"]
_MOD = "def do_thing(x):\n    return x + 1\n"
_TEST = "import zz_a\ndef test_it():\n    assert zz_a.do_thing(1) == 2\n"


def _sidecar(module="zz_a", public=("do_thing",), depends_on=()):
    return json.dumps({
        "module": module, "public": list(public), "depends_on": list(depends_on),
        "disposability": {"verdict": "disposable", "items": {f"D{i}": "ок" for i in range(1, 7)},
                          "rationale": "r", "date": "2026-07-26", "oracle": "agent"},
    }, ensure_ascii=False)


def _git(repo, *args, check=True):
    return subprocess.run(["git", *args], cwd=repo, check=check, capture_output=True, text=True)


def _write(repo, rel, text):
    p = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(text)


def _gate(repo, env=None):
    e = {**os.environ, "ALLOW_WRITE_NONPRIMARY": "1", **(env or {})}
    r = subprocess.run(_CLI, cwd=repo, capture_output=True, text=True, env=e)
    return r.returncode, (r.stdout + r.stderr)


@pytest.fixture(scope="module")
def _clone():
    d = tempfile.mkdtemp(prefix="dispgate-hook-")
    repo = os.path.join(d, "repo")
    if os.path.isdir(os.path.join(_REPO, ".git")):
        subprocess.run(["git", "clone", "-q", _REPO, repo], check=True, capture_output=True)
    else:
        # Правка нити validation-gate-repair по ревью R5 (VG-R5-10), а не изменение замысла
        # этого теста. Санкционированный прогон `scripts/test_on_studio.sh` rsync'ает staging
        # БЕЗ `.git` намеренно, поэтому клонировать нечего: 11 setup-ошибок делали ОБЩИЙ набор
        # нечитаемым — регрессию соседней подсистемы стало не отличить от постоянной красноты
        # носителя. Здесь собирается одноразовый репозиторий из того же дерева.
        shutil.copytree(_REPO, repo, symlinks=True,
                        ignore=shutil.ignore_patterns(".git", "__pycache__", "logs", "outputs",
                                                      "*.pyc", ".pytest_cache"))
        _git(repo, "init", "-q")
        _git(repo, "add", "-A")
        _git(repo, "-c", "user.email=t@t", "-c", "user.name=t",
             "commit", "-qm", "staging snapshot without .git", "--no-verify", check=False)
    # Синхронизация с рабочим деревом: контроль обязан судить ТЕКУЩИЙ гейт, а не закоммиченный.
    for name in os.listdir(os.path.join(_REPO, "project_context")):
        if name.endswith((".py", ".json")):
            shutil.copy2(os.path.join(_REPO, "project_context", name),
                         os.path.join(repo, "project_context", name))
    shutil.rmtree(os.path.join(repo, "contracts"), ignore_errors=True)
    shutil.copytree(os.path.join(_REPO, "contracts"), os.path.join(repo, "contracts"))
    _git(repo, "add", "project_context", "contracts")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-qm", "sync gate under test", "--no-verify", check=False)
    yield repo
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def repo(_clone):
    _git(_clone, "reset", "-q", "--hard", "HEAD")
    _git(_clone, "clean", "-qfd")
    return _clone


# ── Structure/Interfaces: матрица частичного стейджа (DG-01) ─────────────────

@pytest.mark.host_only
def test_only_module_staged_blocks(repo):
    """Сайдкар и тест лежат untracked в дереве — для будущего коммита их не существует."""
    _write(repo, "zz_a.py", _MOD)
    _write(repo, "contracts/zz_a.json", _sidecar())
    _write(repo, "tests/unit/test_zz_a.py", _TEST)
    _git(repo, "add", "zz_a.py")
    code, out = _gate(repo)
    assert code == 6, f"частичный стейдж прошёл (DG-01): {out}"
    assert "нет сайдкара" in out and "нет теста" in out


@pytest.mark.host_only
def test_module_and_sidecar_staged_but_test_untracked_blocks(repo):
    _write(repo, "zz_a.py", _MOD)
    _write(repo, "contracts/zz_a.json", _sidecar())
    _write(repo, "tests/unit/test_zz_a.py", _TEST)
    _git(repo, "add", "zz_a.py", "contracts/zz_a.json")
    code, out = _gate(repo)
    assert code == 6 and "нет теста" in out, out


@pytest.mark.host_only
def test_everything_staged_passes(repo):
    """Негат-контроль: честно собранный модуль гейт не красит."""
    _write(repo, "zz_a.py", _MOD)
    _write(repo, "contracts/zz_a.json", _sidecar())
    _write(repo, "tests/unit/test_zz_a.py", _TEST)
    _git(repo, "add", "zz_a.py", "contracts/zz_a.json", "tests/unit/test_zz_a.py")
    code, out = _gate(repo)
    assert code == 0, f"ложняк на соответствующем модуле: {out}"


@pytest.mark.host_only
def test_verdict_is_judged_by_staged_sidecar_not_by_tree(repo):
    """Застейджен пустой вердикт, в дереве потом дописан полный — судить обязан застейдженный."""
    _write(repo, "zz_a.py", _MOD)
    broken = json.dumps({"module": "zz_a", "public": ["do_thing"], "depends_on": [],
                         "disposability": {}}, ensure_ascii=False)
    _write(repo, "contracts/zz_a.json", broken)
    _write(repo, "tests/unit/test_zz_a.py", _TEST)
    _git(repo, "add", "zz_a.py", "contracts/zz_a.json", "tests/unit/test_zz_a.py")
    _write(repo, "contracts/zz_a.json", _sidecar())          # правка ТОЛЬКО в дереве
    code, out = _gate(repo)
    assert code == 6, f"гейт зачёл незастейдженную правку сайдкара: {out}"


@pytest.mark.host_only
def test_foreign_module_deleted_from_tree_still_judged(repo):
    """DG-14: чужой модуль удалён из дерева, но остаётся в индексе — K3 обязан сработать."""
    _write(repo, "zz_a.py", "import health_db as db\ndef do_thing(x):\n    return db._PRIMARY_HOST\n")
    _write(repo, "contracts/zz_a.json", _sidecar())
    _write(repo, "tests/unit/test_zz_a.py", _TEST)
    _git(repo, "add", "zz_a.py", "contracts/zz_a.json", "tests/unit/test_zz_a.py")
    os.remove(os.path.join(repo, "health_db.py"))            # удаление НЕ застейджено
    code, out = _gate(repo)
    assert code == 6 and "чужие приватные" in out, f"K3 промахнулся по индексу: {out}"


# ── периметр: настоящая граница, из юнит-слоя не проверяемая (DG-03) ─────────

@pytest.mark.host_only
def test_leading_underscore_module_is_in_perimeter(repo):
    _write(repo, "_escape_probe.py", "def secret_logic(x):\n    return x * 2\n")
    _git(repo, "add", "_escape_probe.py")
    code, out = _gate(repo)
    assert code == 6, f"_*.py прошёл мимо гейта (DG-03a): {out}"


@pytest.mark.host_only
def test_init_with_definition_is_in_perimeter(repo):
    _write(repo, "zz_pkg/__init__.py", "def pkg_entry(x):\n    return x\n")
    _git(repo, "add", "zz_pkg/__init__.py")
    code, out = _gate(repo)
    assert code == 6, f"__init__.py с определением прошёл (DG-03b): {out}"


@pytest.mark.host_only
def test_reexport_only_init_passes(repo):
    _write(repo, "zz_pkg/__init__.py", "from zz_pkg.core import do_thing  # noqa: F401\n")
    _git(repo, "add", "zz_pkg/__init__.py")
    code, out = _gate(repo)
    assert code == 0, f"ре-экспортный __init__.py заблокирован без предмета суждения: {out}"


# ── K1: referential integrity depends_on на настоящем каталоге контрактов ────

@pytest.mark.host_only
def test_depends_on_missing_contract_blocks_on_real_contracts_dir(repo):
    dep = [{"module": "project_context/nowhere", "uses": ["x"], "why": "y"}]
    _write(repo, "zz_a.py", _MOD)
    _write(repo, "contracts/zz_a.json", _sidecar(depends_on=dep))
    _write(repo, "tests/unit/test_zz_a.py", _TEST)
    _git(repo, "add", "zz_a.py", "contracts/zz_a.json", "tests/unit/test_zz_a.py")
    code, out = _gate(repo)
    assert code == 6 and "не существует" in out, out


@pytest.mark.host_only
def test_depends_on_real_contract_resolves(repo):
    dep = [{"module": "project_context/indexer", "uses": ["build"], "why": "индекс"}]
    _write(repo, "zz_a.py", _MOD)
    _write(repo, "contracts/zz_a.json", _sidecar(depends_on=dep))
    _write(repo, "tests/unit/test_zz_a.py", _TEST)
    _git(repo, "add", "zz_a.py", "contracts/zz_a.json", "tests/unit/test_zz_a.py")
    code, out = _gate(repo)
    assert code == 0, f"существующий контракт не разрешился: {out}"


# ── Platform: запуск вне репозитория ─────────────────────────────────────────

def test_outside_git_repo_gives_structural_result_without_traceback():
    """Политика кода выхода при отказе git — DG-13, вне объёма ремонта: здесь фиксируется
    только то, что отказ не превращается в traceback и не сорит в stderr."""
    d = tempfile.mkdtemp(prefix="dispgate-nogit-")
    try:
        e = {**os.environ, "PYTHONPATH": _REPO, "ALLOW_WRITE_NONPRIMARY": "1"}
        r = subprocess.run(_CLI, cwd=d, capture_output=True, text=True, env=e)
        assert "Traceback" not in (r.stdout + r.stderr), r.stdout + r.stderr
        assert "fatal:" not in (r.stdout + r.stderr), "git ругается в лицо оператору"
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ── главное свойство: баг внутри гейта не является разрешением ───────────────

@pytest.mark.host_only
def test_internal_error_blocks_instead_of_passing(repo):
    """Ревью 2026-07-27, корень F-02/F-05: общий `except` в CLI печатал «пропускаю (fail-open)»
    и возвращал 0 на ЛЮБОЙ внутренней ошибке. Это делало тихим по умолчанию каждый будущий
    дефект гейта, а не только два найденных. Отказ ОКРУЖЕНИЯ гейт отдаёт структурно и отдельно
    (см. test_outside_git_...), поэтому исключение здесь означает дефект — и обязано блокировать.
    """
    _write(repo, "zz_a.py", _MOD)
    _write(repo, "contracts/zz_a.json", _sidecar())
    _write(repo, "tests/unit/test_zz_a.py", _TEST)
    _git(repo, "add", "zz_a.py", "contracts/zz_a.json", "tests/unit/test_zz_a.py")
    # искусственный дефект внутри чистого evaluator — вход при этом безупречен
    p = os.path.join(repo, "project_context", "dispgate.py")
    src = open(p, encoding="utf-8").read()
    # Якорь по ПРЕФИКСУ сигнатуры, а не по её точному тексту. 2026-07-29: сигнатура получила
    # параметр `perimeter`, дословный якорь перестал совпадать, замена молча стала no-op — и
    # тест покраснел так, будто гейт пропустил внутреннюю ошибку. Мутация, которая не мутирует,
    # лжёт в сторону паники; ассерт ниже делает это невозможным.
    anchor = re.search(r"^def evaluate_new_modules\([^)]*\):$", src, re.M)
    assert anchor, "сигнатура evaluate_new_modules не найдена — проба протухла, а не гейт сломался"
    mutated = src[:anchor.end()] + "\n    raise RuntimeError('synthetic internal defect')" \
        + src[anchor.end():]
    assert mutated != src
    open(p, "w", encoding="utf-8").write(mutated)
    code, out = _gate(repo)
    assert code == 6, f"внутренняя ошибка гейта прошла как разрешение: exit={code}\n{out}"
    assert "ВНУТРЕННЯЯ ОШИБКА" in out, out


@pytest.mark.host_only
def test_typed_schema_failures_block_through_real_cli(repo):
    """F-02/F-05 через настоящий путь: валидный JSON неверного ТИПА обязан давать блок."""
    import json as _j
    pol_path = os.path.join(repo, "project_context", "disposability.json")
    original = open(pol_path, encoding="utf-8").read()

    _write(repo, "zz_a.py", _MOD)
    _write(repo, "contracts/zz_a.json", _sidecar())
    _write(repo, "tests/unit/test_zz_a.py", _TEST)
    _git(repo, "add", "zz_a.py", "contracts/zz_a.json", "tests/unit/test_zz_a.py")

    for key, val in (("items", "wrong"), ("limits", "wrong")):
        pol = _j.loads(original)
        pol[key] = val
        with open(pol_path, "w", encoding="utf-8") as fh:
            _j.dump(pol, fh, ensure_ascii=False)
        code, out = _gate(repo)
        assert code == 6, f"чек-лист с {key} неверного типа прошёл: exit={code}\n{out}"
    open(pol_path, "w", encoding="utf-8").write(original)

    for dep in ([{"module": 7, "uses": [], "why": "x"}],
                [{"module": "nowhere/missing", "uses": [], "why": "x", "legacy": True}]):
        _write(repo, "contracts/zz_a.json", _sidecar(depends_on=dep))
        _git(repo, "add", "contracts/zz_a.json")
        code, out = _gate(repo)
        assert code == 6, f"depends_on {dep} прошёл: exit={code}\n{out}"


@pytest.mark.host_only
def test_nested_definition_in_init_is_in_perimeter(repo):
    """F-04 через настоящий путь."""
    _write(repo, "zz_pkg/__init__.py", "if True:\n    def live_entry():\n        return 1\n")
    _git(repo, "add", "zz_pkg/__init__.py")
    code, out = _gate(repo)
    assert code == 6, f"определение под if освободило __init__.py: {out}"


@pytest.mark.host_only
def test_same_violation_from_subdirectory_gives_same_verdict(repo):
    """F-09: ручной запуск из подкаталога судил не тот корень и молча говорил «чисто»."""
    _write(repo, "zz_a.py", _MOD)
    _git(repo, "add", "zz_a.py")
    nested = os.path.join(repo, "nested", "cwd")
    os.makedirs(nested, exist_ok=True)
    root_code, _ = _gate(repo)
    e = {**os.environ, "PYTHONPATH": repo, "ALLOW_WRITE_NONPRIMARY": "1"}
    sub = subprocess.run(_CLI, cwd=nested, capture_output=True, text=True, env=e)
    assert root_code == 6 and sub.returncode == 6, \
        f"из корня {root_code}, из подкаталога {sub.returncode}: {sub.stdout + sub.stderr}"


# ── Operations: что видит человек в момент блока ─────────────────────────────

@pytest.mark.host_only
def test_block_message_names_the_rule_and_the_way_out(repo):
    _write(repo, "zz_a.py", _MOD)
    _git(repo, "add", "zz_a.py")
    code, out = _gate(repo)
    assert code == 6
    assert "§15" in out, "сообщение не называет норму"
    assert "project_context disposability" in out, "нет команды, дающей чек-лист и скелет"
    assert "not_disposable" in out, "не сказано, что отрицательный вердикт легален"
