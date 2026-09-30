"""Каталог preflight-квитанций спрашивается у git, а не клеится из `root/.git`.

ЗАЧЕМ. §21 завёл деревья нитей, и в дереве `.git` — ФАЙЛ с указателем gitdir, а не
каталог. `os.makedirs(root/.git/preflight_receipts)` там падает NotADirectoryError,
ошибка глотается (квитанция best-effort), и preflight-гейт блокирует КАЖДЫЙ коммит из
дерева нити требованием прогнать preflight, который только что прогнан. Поймано на
первом же коммите из дерева нити 13.09 — причём ДВУМЯ нитями независимо, с одинаковым
диагнозом и почти одинаковым лекарством.

Тест судит ВЕТВЛЕНИЕ исполнением (подменяя ответ git), а не написание строки: в этом
репозитории уже был случай, когда тест проверял текст строки, а не то, что она
вычисляет, и девять суток не замечал корень «/».
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from project_context import indexer  # noqa: E402


def test_worktree_gitdir_is_followed(monkeypatch):
    """git отдаёт gitdir ДЕРЕВА НИТИ — идём туда, а не в root/.git."""
    gd = "/repo/.git/worktrees/some-thread"
    monkeypatch.setattr(subprocess, "check_output", lambda *a, **k: (gd + "\n").encode())
    assert indexer.receipts_dir("/wt/some-thread") == gd + "/preflight_receipts"


def test_the_absolute_form_is_asked_for(monkeypatch):
    """Спрашивается именно `--absolute-git-dir`. Относительная форма («.git») потребовала
    бы склейки с корнем — второй развилки, которая молча разойдётся с первой."""
    seen = {}

    def fake(argv, *a, **k):
        seen["argv"] = list(argv)
        return b"/repo/.git\n"

    monkeypatch.setattr(subprocess, "check_output", fake)
    indexer.receipts_dir("/repo")
    assert "--absolute-git-dir" in seen["argv"], seen["argv"]


def test_falls_back_when_git_is_unavailable(monkeypatch):
    """Нет git — старое поведение, а не исключение: квитанция best-effort, но путь
    обязан быть, иначе читатель и писатель разъедутся молча."""
    def boom(*a, **k):
        raise OSError("git нет")
    monkeypatch.setattr(subprocess, "check_output", boom)
    assert indexer.receipts_dir("/repo") == "/repo/.git/preflight_receipts"


def test_writer_and_reader_share_one_home():
    """Позитивный контроль на главное: у пути ОДИН дом. Читатель квитанции обязан
    звать ту же функцию, что и писатель — иначе гейт требует того, чего не может быть.
    Это вторая половина вклада нити question-answer-channel: сам путь чинили обе нити,
    а расхождение писателя с читателем — только здесь."""
    src = (ROOT / "project_context" / "receipt.py").read_text(encoding="utf-8")
    assert "indexer.receipts_dir(" in src, (
        "читатель квитанции клеит путь сам — второй дом, разъедется с писателем")
    assert '".git","preflight_receipts"' not in src.replace(" ", ""), (
        "в читателе осталась склейка root/.git — тот самый второй дом")
