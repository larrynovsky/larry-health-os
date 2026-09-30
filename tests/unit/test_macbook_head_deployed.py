"""Оракул «код MacBook доехал до Studio» (git_facts.undeployed_head, 27.09).

Песочный репозиторий изображает Studio: ветка main — развёрнутый код, refs/backups/wip —
снимок ноутбука, чей родитель — HEAD MacBook. Коммит ноутбука, которого нет в main дольше
часа, обязан назваться «не доехал»; свежий (едет) и доехавший — нет.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


@pytest.fixture()
def стенд(tmp_path):
    repo = tmp_path / "repo"; repo.mkdir()
    e = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    e["HOME"] = str(tmp_path)

    def g(*a, date=None):
        env = dict(e)
        if date:
            env["GIT_COMMITTER_DATE"] = env["GIT_AUTHOR_DATE"] = date
        return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True,
                              timeout=60, env=env).stdout.strip()

    g("init", "-q", "-b", "main"); g("config", "user.email", "t@t"); g("config", "user.name", "t")
    (repo / "a").write_text("1")
    g("add", "a"); g("commit", "-qm", "развёрнуто", "--no-verify", date="2026-09-27T08:00:00")

    def ноутбук_ушёл_вперёд(date):
        """Коммит ноутбука (не в main) + снимок поверх него, как делает backup.sh --wip."""
        tree = g("rev-parse", "HEAD^{tree}")
        c = g("commit-tree", tree, "-p", "HEAD", "-m", "работа ноутбука", date=date)
        snap = g("commit-tree", tree, "-p", c, "-m", "snapshot")
        g("update-ref", "refs/backups/wip", snap)

    import git_facts as gf
    old = gf.ROOT
    gf.ROOT = repo
    try:
        yield g, ноутбук_ушёл_вперёд, gf
    finally:
        gf.ROOT = old


NOW = 1_790_000_000  # фиксированные «часы» теста


def _ts(g, rev):
    return int(g("log", "-1", "--format=%ct", rev))


@pytest.mark.host_only
def test_stuck_commit_is_named(стенд):
    g, ahead, gf = стенд
    ahead("2026-09-27T08:30:00")
    now = _ts(g, "refs/backups/wip^1") + 2 * 3600
    r = gf.undeployed_head(now=now)
    assert r["deployed"] is False and r["age_s"] == 7200


@pytest.mark.host_only
def test_fresh_commit_is_still_in_flight(стенд):
    g, ahead, gf = стенд
    ahead("2026-09-27T08:30:00")
    now = _ts(g, "refs/backups/wip^1") + 600
    assert gf.undeployed_head(now=now)["deployed"] is True


@pytest.mark.host_only
def test_deployed_head_is_clean_and_no_snapshot_is_unjudged(стенд):
    g, ahead, gf = стенд
    assert gf.undeployed_head(now=NOW) is None              # снимка нет — «не судил», не «чисто»
    tree = g("rev-parse", "HEAD^{tree}")
    g("update-ref", "refs/backups/wip", g("commit-tree", tree, "-p", "HEAD", "-m", "snapshot"))
    assert gf.undeployed_head(now=NOW)["deployed"] is True


def test_sensor_speaks_on_stuck_and_on_unjudged(monkeypatch):
    """Датчик называет застрявший HEAD; «судить не на чем» говорит вслух, а не молчит."""
    import git_facts as gf
    import integrity_tests as I
    said = []
    monkeypatch.setattr(I, "warn", lambda label, detail="": said.append(label))
    monkeypatch.setattr(gf, "undeployed_head",
                        lambda **k: {"head": "abc", "age_s": 7200, "deployed": False})
    I.check_macbook_head_deployed()
    monkeypatch.setattr(gf, "undeployed_head",
                        lambda **k: {"head": "abc", "age_s": 7200, "deployed": True})
    I.check_macbook_head_deployed()
    monkeypatch.setattr(gf, "undeployed_head", lambda **k: None)
    I.check_macbook_head_deployed()
    assert said == ["код MacBook не доехал до Studio", "не сверил, доехал ли код MacBook до Studio"]
