#!/bin/bash
# INTENT: agent_coordination — замысел и инварианты: python3 -m project_context intent agent_coordination
# Завести дерево и ветку для одной нити. Половина пары с thread_finish.sh.
#
# ЗАЧЕМ. Несколько агентских нитей работали в ОДНОМ рабочем каталоге, а индекс
# git у каталога один. 12.09 это сработало четыре раза за день: дважды чужой
# `git add` унёс мои готовые файлы в чужой коммит (код в проде, а квитанция и
# разбор — под чужим заголовком), один раз чужой незакоммиченный файл попал в
# МОЙ индекс и заблокировал мой коммит сообщением про чужой модуль, и один раз
# тест записал коммит в общую историю, удалив все отслеживаемые файлы.
# Общий индекс уносит своё и приносит чужое.
#
# Своё дерево закрывает обе стороны: у каждого дерева свой индекс и свои файлы.
# Историю оно НЕ разделяет — `.git` общий, — поэтому четвёртый случай (тест
# пишет в общую историю) им не лечится; от него защищает изоляция окружения в
# tests/conftest.py.
#
# Использование:  scripts/thread_start.sh <короткое-имя-нити>
# Пример:         scripts/thread_start.sh merge-gates
set -uo pipefail

SLUG="${1:-}"
if [[ -z "$SLUG" ]]; then
    echo "thread_start.sh: нужно короткое имя нити" >&2
    echo "  пример: scripts/thread_start.sh merge-gates" >&2
    exit 2
fi
if [[ ! "$SLUG" =~ ^[a-z0-9][a-z0-9-]*$ ]]; then
    echo "thread_start.sh: имя нити — латиница, цифры и дефис: '$SLUG'" >&2
    exit 2
fi

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || {
    echo "thread_start.sh: не репозиторий" >&2; exit 2; }
REPO_NAME="$(basename "$REPO_ROOT")"
BRANCH="thread/$SLUG"
# Деревья ВНЕ репозитория: внутри они попали бы в индекс и в чужие коммиты —
# ровно та беда, от которой заводятся.
TREE="$HOME/.worktrees/$REPO_NAME/$SLUG"

if git -C "$REPO_ROOT" show-ref --verify --quiet "refs/heads/$BRANCH"; then
    echo "thread_start.sh: ветка $BRANCH уже есть." >&2
    echo "  дерево: $(git -C "$REPO_ROOT" worktree list | grep "$BRANCH" || echo 'не найдено')" >&2
    exit 3
fi

mkdir -p "$(dirname "$TREE")"
git -C "$REPO_ROOT" worktree add -q -b "$BRANCH" "$TREE" || exit 4

# Хуки: каталог .git/hooks ОБЩИЙ для всех деревьев, поэтому ставить их из
# дерева нити нельзя — симлинки указали бы в дерево, которое скоро удалят, и
# сломали бы хуки всем сразу. Установщик это знает и отказывает (exit 4,
# оракул tests/unit/test_install_hooks_worktree.py). Здесь просто убеждаемся,
# что хуки на месте: дерево нити пользуется общими.
if [[ ! -e "$(git -C "$REPO_ROOT" rev-parse --git-common-dir)/hooks/pre-commit" ]]; then
    echo "⚠️  хуков нет в общем каталоге — запусти scripts/install_hooks.sh из главной копии" >&2
fi

echo "✅ нить '$SLUG' готова"
echo "   дерево: $TREE"
echo "   ветка:  $BRANCH"
echo "   закрыть: scripts/thread_finish.sh $SLUG"
