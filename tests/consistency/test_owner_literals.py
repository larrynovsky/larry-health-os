"""C-193: owner means this installation's operator, never the project author."""
import subprocess
import sys
from pathlib import Path

import pytest

import pii_census as pc


@pytest.mark.parametrize('src', [
    '("author-assistant" if is_owner() else "",)',
    'x = "" if not is_owner() else "author-assistant"',
    'if is_owner():\n x = "author-assistant"',
    'if not is_owner():\n pass\nelse:\n send(name="author-assistant")',
    'x = is_owner() and "author-assistant"',
    'x = is_owner_dir(root) or 42',
    'x = sp.is_owner_data() and {"assistant": "author-assistant"}',
    'if is_owner():\n return -42',
    'if is_owner():\n return {"author-assistant": configured_value}',
    'if is_owner():\n x = {"assistant": {"name": "author-assistant"}}',
    'if ready and sp.is_owner():\n send(default=3.14)',
    'if is_owner():\n i18n.t("key", name="author-assistant")',
    'if is_owner():\n return os.getenv("NAME", "author-assistant")',
    'if is_owner():\n return Path(os.environ.get("ROOT", "/author/home"))',
    'if is_owner():\n send(f"author-{42}")',
])
def test_data_literals(src):
    assert pc.owner_literals(src)


@pytest.mark.parametrize('src', [
    'x = "author-assistant"',  # not owner-selected
    'x = get_config() if is_owner() else ""',
    'if is_owner():\n return [True, False, None, "", -1, 0, 1]',
    'if is_owner():\n i18n.t("key")',
    'send(person_key="key" if is_owner() else "other.key")',
    'person_key = "key" if is_owner() else "other.key"',
    'if is_owner():\n return Path.home() / "health" / "data"',
    'if is_owner():\n return os.environ.get("HEALTH_DATA_DIR", "")',
    'if is_owner():\n return {}',
    'if is_owner():\n return Path(os.environ["HOME"]) / "health"',
    'if is_owner():\n return os.path.join(os.getenv("HOME"), "health")',
    'if is_owner():\n return count > 42',
    '"is_owner() and 42"',  # source-looking text is data, not an AST condition
])
def test_non_data_and_documented_exemptions(src):
    assert pc.owner_literals(src) == []


def test_allow_is_exact():
    src = 'def reminders_list_name():\n if is_owner_data():\n  return "Health"'
    assert pc.owner_literals(src, 'reminders_sync.py') == []
    assert pc.owner_literals(src.replace('Health', 'author-assistant'), 'reminders_sync.py')
    assert pc.owner_literals(src, 'other.py')
    assert pc.owner_literals(src.replace('reminders_list_name', 'other'), 'reminders_sync.py')
    assert all(reason.strip() for reason in pc.OWNER_LITERAL_ALLOW.values())


def test_public_tree():
    hits = {p: lines for p in pc.public_files() if p.endswith('.py')
            if (lines := pc.owner_literals((pc.ROOT / p).read_text(), p))}
    assert hits == {}


@pytest.mark.host_only
def test_staged_positive_control(tmp_path):
    """Real index + CLI, empty PII dictionary; no commit or real patient data."""
    def git(*args):
        subprocess.run(['git', '-C', str(tmp_path), *args], check=True, capture_output=True)

    def run():
        return subprocess.run([sys.executable, str(Path(pc.__file__).resolve()), '--staged'],
                              cwd=tmp_path, capture_output=True, text=True)

    git('init', '-q')
    (tmp_path / 'private').mkdir()
    (tmp_path / 'publication_zones.yaml').write_text('private:\n  - glob: private/*\n')
    (tmp_path / 'private/pii_terms.yaml').write_text('literals: {}\npatterns: {}\n')
    src = '("author-assistant" if is_owner() else "",)\n'
    (tmp_path / 'candidate.py').write_text(src)
    (tmp_path / 'private/candidate.py').write_text(src)
    git('add', '.')
    # A clean worktree version cannot hide the bad indexed version.
    (tmp_path / 'candidate.py').write_text('x = ""\n')
    result = run()
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'candidate.py' in result.stdout and 'C-193' in result.stdout
    assert 'author-assistant' not in result.stdout  # no literal payload in diagnostics
    git('add', 'candidate.py')
    assert run().returncode == 0  # private candidate is ignored
    (tmp_path / 'candidate.py').write_text('if is_owner(:\n')
    git('add', 'candidate.py')
    assert run().returncode == 1  # parse failure must not go green
