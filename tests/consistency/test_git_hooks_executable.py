"""Хук без бита исполнения — это выключенные разом ВСЕ гейты, и молча.

Инцидент 2026-08-20: правка `scripts/git-hooks/pre-commit` через смонтированную
ФС Cowork сняла бит исполнения (в HEAD 100755, на диске и в индексе стало 100644).
Git перестал запускать хук, сообщив об этом одной строкой в stderr. Пока он был
выключен, не работали dispgate, dupgate, intent-гейт, L2-квитанция, ruff, ратчет
и отбор затронутых тестов. Поймал это не сторож, а побочный hint git.

ПОЧЕМУ ПРОВЕРКА ЖИВЁТ ЗДЕСЬ, А НЕ В САМОМ ХУКЕ. Проверка внутри pre-commit
бесполезна по построению: неисполняемый хук не запустится ровно тогда, когда
она нужна (who-watches-the-watcher, §14 — терминус у корневого монитора).

ПОЧЕМУ РЕЖИМ В ИНДЕКСЕ, А НЕ НА ДИСКЕ. Индекс — это то, что доедет до второй
машины: checkout восстановит права из git. Сломанный режим на диске чинится
одним chmod, сломанный в git тиражируется. Диск на Studio дополнительно
стережёт integrity_tests.check_git_hooks_executable (ночной, вне хуков).
ГРАНИЦА, названная вслух: диск MacBook не стережёт никто — там нет всегда-живого
монитора, а хук стеречь сам себя не может.
"""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Ратчет: эти обязаны быть исполняемыми. Файл выбыл осознанно — вычеркни строку
# тем же коммитом; красное требует решения человека, а не правки теста.
MUST_BE_EXEC = {
    "scripts/git-hooks/pre-commit",
    "scripts/git-hooks/post-commit",
    "scripts/git-hooks/post-commit-macbook",
    "scripts/git-hooks/pre_commit_check.py",
    "scripts/git-hooks/should_restart_bot.sh",
}


def _modes() -> dict[str, str]:
    # Staging без `.git` (scripts/test_on_studio.sh копирует дерево, не репозиторий):
    # судить режим В ИНДЕКСЕ там нечем — это не находка, а отсутствие предмета.
    # Явный skip с причиной, не тихий зелёный: на MacBook и в каноне Studio
    # индекс есть, и там тест судит по-настоящему. Замер 2026-09-01: два красных
    # на staging держали маркер «полный зелёный прогон» невыставленным 11 дней.
    import pytest
    if not (ROOT / ".git").exists():
        pytest.skip("нет .git — режим в индексе судить нечем (staging-копия дерева)")
    out = subprocess.run(["git", "ls-files", "-s", "scripts/git-hooks/"],
                         cwd=ROOT, capture_output=True, text=True, check=True).stdout
    modes = {}
    for line in out.splitlines():
        if not line or "__pycache__" in line:
            continue
        mode, _rest = line.split(" ", 1)
        path = line.split("\t", 1)[1]
        modes[path] = mode
    return modes


def test_hook_files_are_executable_in_git():
    modes = _modes()
    broken = {p: modes.get(p, "ОТСУТСТВУЕТ") for p in MUST_BE_EXEC
              if modes.get(p) != "100755"}
    assert not broken, (
        f"хуки потеряли бит исполнения в git: {broken}. "
        "Git молча перестанет их запускать — это выключает ВСЕ гейты сразу. "
        "Fix: git update-index --chmod=+x <путь> && chmod 755 <путь>")


def test_baseline_files_all_present():
    """Ратчет в обратную сторону: файл исчез — решение человека, не тишина."""
    modes = _modes()
    missing = MUST_BE_EXEC - set(modes)
    assert not missing, (
        f"хуки исчезли из git: {sorted(missing)}. Если осознанно — вычеркни из "
        "MUST_BE_EXEC тем же коммитом.")
