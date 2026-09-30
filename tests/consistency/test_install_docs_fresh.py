"""Install-провенанс и контроли ремонта; синтетические страницы не трогают соседнюю нить.

Метки на обеих страницах стоят с нити image-release (30.09): временный strict xfail
проверки наличия снят. Отсутствие, повтор или устаревшая метка — всегда красный.
"""
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scripts import install

pytestmark = pytest.mark.consistency
ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("page", install.INSTALL_DOC_PAGES)
def test_install_page_has_stamp(page):
    text = (ROOT / page).read_text(encoding="utf-8")
    assert install.INSTALL_PROVENANCE.search(text), install.install_doc_problem(page, text)


@pytest.mark.parametrize("page", install.INSTALL_DOC_PAGES)
def test_install_page_is_fresh(page):
    text = (ROOT / page).read_text(encoding="utf-8")
    if "<!-- install-provenance:" not in text:
        pytest.skip("отсутствие метки судится отдельно строгим временным xfail")
    problem = install.install_doc_problem(page, text)
    assert problem is None, problem


def test_sensor_is_red_on_missing_stale_malformed_and_changed_facts(monkeypatch):
    """Оракул ломаем по фактам, а не по тексту проверяющей функции: старый hash краснеет."""
    page = install.INSTALL_DOC_PAGES[0]
    facts = install.install_facts()
    stamp = f"<!-- install-provenance: {install.install_facts_hash(facts)} -->"
    text = stamp + "\n# Страница\n"
    assert install.install_doc_problem(page, text) is None
    for broken in ("# Без метки\n", "<!-- install-provenance: sha256:000000000000 -->",
                   "<!-- install-provenance: broken -->", stamp + stamp):
        problem = install.install_doc_problem(page, broken)
        assert problem and f"python3.11 doc_agent.py --regen-install {page}" in problem
    monkeypatch.setattr(install, "install_facts", lambda: {**facts, "ocr_langs": "eng deu"})
    assert "устарела" in install.install_doc_problem(page, text)


def _sandbox(tmp_path, monkeypatch, replies):
    """Изолированный doc_agent с управляемыми ответами модели и без секретов/сети/уведомлений."""
    import doc_agent as da
    import llm_client
    import secret_guard
    monkeypatch.setattr(da, "ROOT", tmp_path)
    monkeypatch.setattr(da, "_read_secret", lambda name: "fake")
    monkeypatch.setattr(da, "_log", Mock())
    monkeypatch.setattr(da, "notify_telegram", Mock())
    monkeypatch.setattr(secret_guard, "find_secret_values", lambda prompt: [])
    create = Mock(side_effect=[SimpleNamespace(content=[SimpleNamespace(text=r)], stop_reason="end_turn")
                               for r in replies])
    monkeypatch.setattr(llm_client, "guarded_client", lambda: SimpleNamespace(messages=SimpleNamespace(create=create)))
    path = tmp_path / install.INSTALL_DOC_PAGES[0]
    path.parent.mkdir(parents=True)
    return da, path, create


def _reply(page, changes=None):
    return json.dumps({"page": page, "tutorial_changes": changes or []}, ensure_ascii=False)


_OLD = ("# Установка\n\n## Службы\n\nстарый факт\n\n"
        + "".join(f"Неизменная строка {i}.\n" for i in range(20))
        + "\n<!-- tutorial:run -->\n```bash\necho health-os:local\n```\n")


def test_regen_dry_run_shows_diff_and_writes_nothing(tmp_path, monkeypatch, capsys):
    body = _OLD.replace("старый факт", "новый факт")
    da, path, create = _sandbox(tmp_path, monkeypatch, [_reply(body)])
    path.write_text(_OLD, encoding="utf-8")
    translation = Mock()
    monkeypatch.setattr(da, "translate_page", translation)
    assert da.regenerate_install_page(install.INSTALL_DOC_PAGES[0], dry_run=True)
    diff = capsys.readouterr().out
    assert "-старый факт" in diff and "+новый факт" in diff and "+<!-- install-provenance:" in diff
    assert path.read_text(encoding="utf-8") == _OLD
    assert not path.with_suffix(".en.md").exists()
    translation.assert_not_called()
    prompt = create.call_args.kwargs["messages"][0]["content"]
    assert _OLD in prompt and '"ocr_langs": "eng rus"' in prompt


@pytest.mark.parametrize("body", [
    _OLD.replace("## Службы\n", ""),
    _OLD.replace("## Службы", "## Другое имя"),
    "\n".join(_OLD.splitlines()[:6] + _OLD.splitlines()[-5:]),
    _OLD.replace("echo health-os:local", "echo unsafe:v1"),
])
def test_regen_rejects_lost_headings_lines_and_protected_commands(tmp_path, monkeypatch, capsys, body):
    da, path, _ = _sandbox(tmp_path, monkeypatch, [_reply(body)])
    path.write_text(_OLD, encoding="utf-8")
    en = path.with_suffix(".en.md")
    en.write_text("existing English\n", encoding="utf-8")
    assert not da.regenerate_install_page(install.INSTALL_DOC_PAGES[0])
    assert path.read_text(encoding="utf-8") == _OLD and en.read_text() == "existing English\n"
    assert "ничего НЕ записано" in capsys.readouterr().out


def test_tutorial_change_needs_exact_current_fact_and_preserves_rest():
    import doc_agent as da
    facts = {"services": [{"image": "ghcr.io/example/health:v1"}]}
    new = _OLD.replace("health-os:local", "ghcr.io/example/health:v1")
    change = {"index": 0, "fact_path": ["services", 0, "image"],
              "before": "health-os:local", "after": "ghcr.io/example/health:v1"}
    assert da._install_rewrite_problems(_OLD, new, facts, [change]) == []
    for bad in ([], [{**change, "fact_path": ["unknown"]}], [{**change, "index": -1}],
                [{**change, "after": "unsafe:v1"}]):
        assert da._install_rewrite_problems(_OLD, new, facts, bad)
    assert da._install_rewrite_problems(_OLD, new.replace("echo ", "rm "), facts, [change])
    assert da._install_rewrite_problems(_OLD, _OLD.replace("<!-- tutorial:run -->", ""), facts, [])


def test_regen_writes_stamp_and_reuses_real_translation_path(tmp_path, monkeypatch):
    import doc_translation as dt
    ru = "# Установка\n\n## Службы\n\n<!-- tutorial:run -->\n```bash\necho ok\n```\n"
    en_body = ru.replace("Установка", "Installation").replace("Службы", "Services")
    da, path, create = _sandbox(tmp_path, monkeypatch, [_reply(ru), en_body])
    path.write_text(ru, encoding="utf-8")
    assert da.regenerate_install_page(install.INSTALL_DOC_PAGES[0])
    updated = path.read_text(encoding="utf-8")
    en = path.with_suffix(".en.md").read_text(encoding="utf-8")
    assert install.install_doc_problem(install.INSTALL_DOC_PAGES[0], updated) is None
    assert dt.mark_of(en) == (install.INSTALL_DOC_PAGES[0], dt.text_hash(updated))
    assert dt.pair_problems(updated, en) == []
    assert create.call_count == 2
    da.notify_telegram.assert_not_called()


@pytest.mark.parametrize("reply", ["not JSON", '{"page": null, "tutorial_changes": []}',
                                  '{"page": "# Page"}'])
def test_malformed_model_reply_is_not_written(tmp_path, monkeypatch, reply):
    da, path, _ = _sandbox(tmp_path, monkeypatch, [reply])
    path.write_text(_OLD, encoding="utf-8")
    assert not da.regenerate_install_page(install.INSTALL_DOC_PAGES[0])
    assert path.read_text(encoding="utf-8") == _OLD


def test_truncated_reply_and_secret_guard_write_nothing(tmp_path, monkeypatch):
    import secret_guard
    da, path, create = _sandbox(tmp_path, monkeypatch, [])
    path.write_text(_OLD, encoding="utf-8")
    create.side_effect = None
    create.return_value = SimpleNamespace(content=[SimpleNamespace(text=_reply(_OLD))], stop_reason="max_tokens")
    assert not da.regenerate_install_page(install.INSTALL_DOC_PAGES[0])
    assert path.read_text(encoding="utf-8") == _OLD
    create.reset_mock()
    monkeypatch.setattr(secret_guard, "find_secret_values", lambda prompt: ["fake_secret_filename"])
    assert not da.regenerate_install_page(install.INSTALL_DOC_PAGES[0])
    assert path.read_text(encoding="utf-8") == _OLD
    create.assert_not_called()
    assert not da.regenerate_install_page("../outside.md")
    create.assert_not_called()


def test_regen_cli_all_dry_run_and_failure(monkeypatch):
    import doc_agent as da
    regen = Mock(return_value=True)
    monkeypatch.setattr(da, "regenerate_install_page", regen)
    monkeypatch.setattr(sys, "argv", ["doc_agent.py", "--regen-install", "--dry-run"])
    da.main()
    assert [c.args[0] for c in regen.call_args_list] == list(install.INSTALL_DOC_PAGES)
    assert all(c.kwargs == {"dry_run": True} for c in regen.call_args_list)
    regen.return_value = False
    monkeypatch.setattr(sys, "argv", ["doc_agent.py", "--regen-install", install.INSTALL_DOC_PAGES[1]])
    with pytest.raises(SystemExit) as error:
        da.main()
    assert error.value.code == 1


def test_intent_translation_keeps_its_entry_checks(monkeypatch):
    import doc_agent as da
    entry = {"id": "example", "explanation": "docs/explanation/example.md"}
    monkeypatch.setattr(da.ir, "get_entry", lambda entry_id: entry)
    translate = Mock(return_value=True)
    monkeypatch.setattr(da, "translate_page", translate)
    assert da.translate_intent_page("example", dry_run=True, notify=False)
    translate.assert_called_once_with(entry["explanation"], entry=entry, dry_run=True, notify=False)


@pytest.mark.parametrize("staged", ["scripts/install.py", "docker/Dockerfile", "bot/filters.py",
                                  "templates/launchd/example.plist.tmpl", *install.INSTALL_DOC_PAGES,
                                  "docs/tutorials/first_install.en.md", "docs/how-to/install_docker.en.md"])
def test_precommit_runs_sensor_and_delivers_red(tmp_path, staged):
    """Исполняем настоящий блок хука с управляемым красным pytest, без настоящего git/коммита."""
    hook = (ROOT / "scripts/git-hooks/pre-commit").read_text()
    block = hook[hook.index("# ── INSTALL:"):hook.index("# ── TESTING_CONTRACTS.md auto-regen")]
    runner = tmp_path / "python"
    runner.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$REPO_ROOT/called"\necho stale\nexit "${CHECK_RC:-1}"\n')
    runner.chmod(0o700)
    env = {**os.environ, "STAGED": staged, "REPO_ROOT": str(tmp_path), "PYTHON": str(runner),
           "TMPDIR": str(tmp_path)}
    script = 'git() { return 0; }\n' + block
    result = subprocess.run(["/bin/bash", "-c", script], env=env, capture_output=True, text=True)
    assert result.returncode == 1
    assert "test_install_docs_fresh.py" in (tmp_path / "called").read_text()
    assert "python3.11 doc_agent.py --regen-install <page>, застейджь страницу" in result.stdout
    (tmp_path / "called").unlink()
    result = subprocess.run(["/bin/bash", "-c", script], env={**env, "STAGED": "other.txt"},
                            capture_output=True, text=True)
    assert result.returncode == 0 and not (tmp_path / "called").exists()
    result = subprocess.run(["/bin/bash", "-c", 'git() { echo docs/tutorials/first_install.md; }\n' + block],
                            env=env, capture_output=True, text=True)
    assert result.returncode == 3 and not (tmp_path / "called").exists()
    result = subprocess.run(["/bin/bash", "-c", script], env={**env, "CHECK_RC": "0"},
                            capture_output=True, text=True)
    assert result.returncode == 0 and (tmp_path / "called").exists()


def test_regen_accepts_json_in_one_outer_fence(tmp_path, monkeypatch, capsys):
    """30.09: модель вернула ответ в ```json … ``` — отказ «Expecting value» дважды подряд."""
    body = _OLD.replace("старый факт", "новый факт")
    da, path, _ = _sandbox(tmp_path, monkeypatch, ["```json\n" + _reply(body) + "\n```"])
    path.write_text(_OLD, encoding="utf-8")
    monkeypatch.setattr(da, "translate_page", Mock())
    assert da.regenerate_install_page(install.INSTALL_DOC_PAGES[0], dry_run=True)
    assert "+новый факт" in capsys.readouterr().out
