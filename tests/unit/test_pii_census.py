"""pii_census: перепись и pre-commit-ратчет личных данных в публичной зоне.

Словарь здесь СИНТЕТИЧЕСКИЙ (zorblax, Quuxville, Blorptown) — тест лежит в публичной
зоне, и настоящий литерал в нём сам был бы утечкой. Репозиторий — одноразовый git во
tmp_path: гейт читает ИНДЕКС и HEAD, и проверять его на подменённом чтении значило бы
проверить не то (§20).
"""
import json
import subprocess

import pytest

import pii_census as pc

TERMS = """literals:
  person: [zorblax]
patterns:
  geo: ['\\bQuuxville\\b']
"""
ZONES = """private:
  - glob: "private/*"
    why: dictionary
  - glob: "journal/*"
    why: journal
"""


def _git(root, *a):
    return subprocess.run(["git", "-C", str(root), *a], check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "private").mkdir()
    (tmp_path / "private/pii_terms.yaml").write_text(TERMS)
    (tmp_path / "publication_zones.yaml").write_text(ZONES)
    (tmp_path / "journal").mkdir()
    (tmp_path / "journal/log.md").write_text("zorblax zorblax zorblax")
    (tmp_path / "code.py").write_text("x = 1\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "init")
    return tmp_path


def _stage(repo, name, text):
    (repo / name).write_text(text)
    _git(repo, "add", name)


def _commit(repo, name, text, baseline=None):
    (repo / name).write_text(text)
    if baseline is not None:
        (repo / "private/pii_baseline.json").write_text(json.dumps(baseline))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "c")


@pytest.mark.host_only
def test_публичное_содержимое_и_имя_файла_считаются_приватное_нет(repo):
    (repo / "zorblax_notes.md").write_text("Zorblax lives in Quuxville; Quuxvilleish is not a hit\n")
    c = pc.census(repo, ["zorblax_notes.md", "journal/log.md", "code.py"])
    assert c == {"zorblax_notes.md": {"person": 2, "geo": 1}}   # имя + текст; journal не судится


def test_judge_различает_рост_и_убыль():
    grew, shrank = pc.judge({"a": {"x": 3}, "b": {"x": 1}}, {"a": 2, "b": 4, "c": 1}, ["a", "b", "c"])
    assert grew == {"a": (2, 3)} and shrank == {"b": 1, "c": 0}


@pytest.mark.host_only
def test_негативный_контроль_новый_литерал_блокирует_коммит(repo, capsys):
    _stage(repo, "code.py", "owner = 'zorblax'\n")
    assert pc._staged(repo) == 1
    out = capsys.readouterr().out
    assert "code.py: было 0, стало 1" in out and "строка 1: [person]" in out


@pytest.mark.host_only
def test_новый_файл_с_литералом_в_имени_блокирует(repo):
    _stage(repo, "zorblax.md", "clean\n")
    assert pc._staged(repo) == 1


@pytest.mark.host_only
def test_приватная_зона_не_блокирует(repo):
    _stage(repo, "journal/log.md", "zorblax " * 10)
    assert pc._staged(repo) == 0


@pytest.mark.host_only
def test_уборка_опускает_долг_и_достейдживает_его(repo):
    _commit(repo, "code.py", "a = 'zorblax zorblax'\n", baseline={"code.py": 2})
    _stage(repo, "code.py", "who = 'zorblax'\n")          # было 2, стало 1 — не рост
    assert pc._staged(repo) == 0
    assert json.loads(_git(repo, "show", ":private/pii_baseline.json")) == {"code.py": 1}


@pytest.mark.host_only
def test_старое_вхождение_при_правке_рядом_не_блокирует(repo):
    _commit(repo, "code.py", "a = 'zorblax'\n", baseline={"code.py": 1})
    _stage(repo, "code.py", "a = 'zorblax'\nb = 2\n")
    assert pc._staged(repo) == 0


@pytest.mark.host_only
def test_завышенный_долг_не_даёт_права_вернуть_убранное(repo):
    """Долг 5 при одном вхождении в HEAD: второе — новая утечка, хоть и «в пределах долга»."""
    _commit(repo, "code.py", "a = 'zorblax'\n", baseline={"code.py": 5})
    _stage(repo, "code.py", "a = 'zorblax'\nb = 'zorblax'\n")
    assert pc._staged(repo) == 1


@pytest.mark.host_only
def test_перенос_из_приватной_зоны_в_публичную_блокирует(repo):
    _git(repo, "mv", "journal/log.md", "notes.md")
    assert pc._staged(repo) == 1


@pytest.mark.host_only
def test_новое_слово_словаря_видит_старое_без_блока_и_поднимает_долг(repo, monkeypatch):
    _commit(repo, "code.py", "city = 'Blorptown'\n")
    (repo / "private/pii_terms.yaml").write_text(TERMS.replace("[zorblax]", "[zorblax, blorptown]"))
    _stage(repo, "code.py", "city = 'Blorptown'\nx = 2\n")
    assert pc._staged(repo) == 0                              # вхождение уже лежало в HEAD
    monkeypatch.setattr(pc, "ROOT", repo)
    assert pc.main(["rebaseline"]) == 0
    assert json.loads((repo / "private/pii_baseline.json").read_text()) == {"code.py": 1}


@pytest.mark.host_only
def test_rebaseline_отказывает_на_настоящем_росте(repo, monkeypatch):
    _commit(repo, "code.py", "x = 1\n", baseline={"other.py": 1})
    (repo / "code.py").write_text("x = 'zorblax'\n")
    monkeypatch.setattr(pc, "ROOT", repo)
    assert pc.main(["rebaseline"]) == 1


@pytest.mark.host_only
def test_без_словаря_суд_отказывает(repo):
    (repo / "private/pii_terms.yaml").unlink()
    with pytest.raises(SystemExit, match="нет словаря"):
        pc.census(repo, ["code.py"])


@pytest.mark.host_only
def test_выгрузка_это_копия_без_отслеживаемых_приватных_файлов(repo):
    """Рабочая копия владельца отслеживает приватные зоны — не выгрузка. Выгрузка их не
    отслеживает, даже если установщик разложил файлы в private/ на диске."""
    assert pc.is_public_export(repo) is False
    _git(repo, "rm", "-rq", "--cached", "private", "journal")
    _git(repo, "commit", "-qm", "export")
    assert (repo / "private/pii_terms.yaml").exists()      # на диске лежит, но не в git
    assert pc.is_public_export(repo) is True


def test_двоичный_файл_судится_только_по_имени():
    """PNG, декодированный с errors=ignore, давал случайные «слова» словаря (27.09).
    Тело с NUL-байтом не судится; имя файла — судится, как и раньше."""
    import re
    terms = {"t": re.compile("zorblax")}
    png = "\x89PNG\r\n\x1a\n\0\0zorblax\0"
    assert pc._scan("pic.png", png, terms) == {}
    assert pc._scan("zorblax.png", png, terms) == {"t": 1}
    assert pc._scan("code.py", "x = 'zorblax'", terms) == {"t": 1}


def test_без_самого_git_ответ_из_манифеста(monkeypatch):
    """Образ контейнера: нет ни .git, ни программы git (замер 30.09 07:50 — FileNotFoundError
    из conftest уронил весь утренний прогон). Ответ берётся из манифеста, как в песочнице."""
    import git_facts

    def no_git(*a, **k):
        raise FileNotFoundError("git")
    monkeypatch.setattr(pc.subprocess, "run", no_git)
    monkeypatch.setattr(git_facts, "source", lambda: "manifest")
    monkeypatch.setattr(git_facts, "tracked", lambda *g: ["private/x.yaml", "code.py"])
    assert pc._tracked(git_facts.ROOT) == ["private/x.yaml", "code.py"]
