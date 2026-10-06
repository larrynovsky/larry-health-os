"""Ночной ремонт (слово владельца 30.09, 1б/2б/3б): гейт коммита в main и обвязка слияния патча.

Studio и стенд подменены: гейт получает список ожидающих починок функцией, land — готовый каталог починки и
прогон тестов-оракулов. Настоящий git — во временном каталоге (гейт и land читают ветку, HEAD, индекс)."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

import night_repair as nr

pytestmark = pytest.mark.unit


def _git(cwd, *a):
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    # Предмет — ночной ремонт, а он живёт на хосте Studio (placement.yaml: host), где git есть.
    # В контейнере владельца git нет по замыслу образа: там эти тесты судить нечем, их судит
    # полный прогон при слиянии нити (решение владельца 05.10). Пропуск только по отсутствию git.
    if shutil.which("git") is None:
        pytest.skip("git нет в этой среде: ночной ремонт судится на хосте, при слиянии нити")
    bare = tmp_path / "studio.git"
    r = tmp_path / "mac"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "t@t"); _git(r, "config", "user.name", "t")
    (r / "code.py").write_text("X = 1\n")
    _git(r, "add", "."); _git(r, "commit", "-q", "-m", "init")
    _git(tmp_path, "clone", "-q", "--bare", str(r), str(bare))
    _git(r, "remote", "add", "studio", str(bare)); _git(r, "fetch", "-q", "studio")
    return r


def test_gate_blocks_main_only_and_passes_on_silence(repo):
    code, text = nr.fix_gate(repo, pending=lambda: (["2026-10-01-abcd"], True, 3))
    assert code == nr.GATE_CODE and "2026-10-01-abcd" in text
    assert nr.fix_gate(repo, pending=lambda: ([], True, 3)) == (0, "")
    code, text = nr.fix_gate(repo, pending=lambda: ([], True, 50))
    assert code == 0 and "не бежал 50 ч" in text, "ремонт молчит дольше суток — каждая сессия видит строку"
    assert "ни разу" in nr.fix_gate(repo, pending=lambda: ([], True, None))[1]
    code, text = nr.fix_gate(repo, pending=lambda: ([], False, None))
    assert code == 0 and "не ответила" in text, "Studio молчит — пропуск вслух, не блок"
    _git(repo, "checkout", "-q", "-b", "thread/x")
    assert nr.fix_gate(repo, pending=lambda: (["id"], True, 3))[0] == 0, "2б: деревья нитей не блокируются"


def _carrier(tmp_path, kind, patch="", tests=()):
    d = tmp_path / "carrier" / "rid"
    d.mkdir(parents=True)
    (d / "meta.json").write_text(json.dumps({"id": "rid", "kind": kind, "tests": list(tests)}))
    (d / "NIGHT_FIX.md").write_text("Причина")
    (d / "review.md").write_text("принять")
    if patch:
        (d / "fix.patch").write_text(patch)
    return d


PATCH = ("diff --git a/code.py b/code.py\n--- a/code.py\n+++ b/code.py\n@@ -1 +1 @@\n-X = 1\n+X = 2\n"
         "diff --git a/tests/test_x.py b/tests/test_x.py\nnew file mode 100644\n--- /dev/null\n+++ b/tests/test_x.py\n"
         "@@ -0,0 +1 @@\n+import code\n")


def test_land_requires_red_without_fix_and_green_with_it(repo, tmp_path):
    d = _carrier(tmp_path, nr.KIND_CODE, PATCH, ["tests/test_x.py"])
    seen = []

    def run_tests(tree, sel):   # «зелёный» ⇔ правка кода на месте
        seen.append((tree / "tests" / "test_x.py").exists())
        return (tree / "code.py").read_text() == "X = 2\n"
    code, text = nr.land_fix(repo, "rid", run_tests=run_tests, fetch=lambda rid: d)
    assert code == 0, text
    assert seen == [True, True], "оба прогона видят тест: без правки — только тестовая часть патча"
    assert "code.py" in _git(repo, "diff", "--cached", "--name-only")
    head = _git(repo, "rev-parse", "HEAD")
    assert (repo / ".git" / nr.LANDING).read_text().split() == ["rid", head]
    assert nr.fix_gate(repo, pending=lambda: (["rid"], True, 3))[0] == 0, "маркер пропускает коммит слияния"


def test_land_refuses_oracle_that_is_green_without_fix(repo, tmp_path):
    d = _carrier(tmp_path, nr.KIND_CODE, PATCH, ["tests/test_x.py"])
    code, text = nr.land_fix(repo, "rid", run_tests=lambda t, s: True, fetch=lambda rid: d)
    assert code == 1 and "без правки" in text
    assert _git(repo, "diff", "--cached", "--name-only") == ""


def test_land_refuses_environment_fix(repo, tmp_path):
    d = _carrier(tmp_path, nr.KIND_ENV)
    code, text = nr.land_fix(repo, "rid", run_tests=lambda t, s: pytest.fail("не гонять"), fetch=lambda rid: d)
    assert code == 1 and "close rid" in text, "3б: среду закрывают словами о сделанном, не патчем"


def test_secret_in_author_output_is_never_carried():
    key = "sk-ant-" + "x" * 30
    assert nr._leaks(["Причина: ...", "diff ... " + key], key)
    assert not nr._leaks(["Причина: ..."], key)
    assert not nr._leaks([key], ""), "нет ключа — нечего искать"


def test_settings_deny_neighbor_secrets_from_config(monkeypatch):
    """Каталоги секретов соседей — из infra_config.NEIGHBORS, не литералом в публичном json."""
    import json as _json
    from pathlib import Path as _P
    import infra_config
    import night_repair
    monkeypatch.setattr(infra_config, "NEIGHBORS", {"crm": {"path": _P("/x/crm"), "secrets": _P("/x/.crm_secrets")}})
    s = _json.loads(_P(night_repair._settings()).read_text(encoding="utf-8"))
    assert "/x/.crm_secrets/**" in s["sandbox"]["filesystem"]["denyRead"]
    assert "Read(/x/.crm_secrets/**)" in s["permissions"]["deny"]
    monkeypatch.setattr(infra_config, "NEIGHBORS", {})
    s = _json.loads(_P(night_repair._settings()).read_text(encoding="utf-8"))
    assert not any("crm" in x for x in s["sandbox"]["filesystem"]["denyRead"])


def test_close_receipt_names_fix_commit_not_later_head(repo, monkeypatch, tmp_path):
    """01.10: после коммита починки post-commit положил коммит doc_agent, и квитанция
    «слито <HEAD>» назвала CHANGELOG вместо починки."""
    d = _carrier(tmp_path, "code")
    monkeypatch.setattr(nr, "_fetch", lambda rid: d)
    notes = []
    monkeypatch.setattr(nr, "_move_remote", lambda rid, where, note: notes.append(note) or True)
    base = _git(repo, "rev-parse", "HEAD")
    (repo / ".git" / nr.LANDING).write_text(f"rid {base}")
    (repo / "code.py").write_text("X = 2\n")
    _git(repo, "commit", "-qam", "починка (ночная починка rid)")
    fix = _git(repo, "rev-parse", "HEAD")
    (repo / "CHANGELOG.md").write_text("x\n")
    _git(repo, "add", "."); _git(repo, "commit", "-qm", "docs: артефакты doc_agent")
    _git(repo, "push", "-q", "studio", "main"); _git(repo, "fetch", "-q", "studio")
    code, text = nr.close_fix(repo, "rid")
    assert code == 0 and notes == [f"слито {fix[:7]}"], (text, notes)
