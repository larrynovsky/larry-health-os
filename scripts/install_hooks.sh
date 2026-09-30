#!/bin/bash
# install_hooks.sh — устанавливает git-hooks через symlink.
#
# Идемпотентный: можно запускать многократно. См. P1-4 в ROADMAP.
# Запускать ТОЛЬКО на Studio (canonical-only generation — правило #8).

set -uo pipefail

HOSTNAME=$(hostname)
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Каталог хуков спрашиваем у git, а не собираем как REPO_ROOT + /.git/hooks
# (2026-09-11, подготовка к worktree-на-задачу). В linked worktree `.git` —
# ФАЙЛ-указатель, и сборка строкой даёт путь ЧЕРЕЗ файл: mkdir и ln падают с
# «Not a directory». Тот же класс уже стрелял в соседнем проекте 31.08 на первом пробном
# worktree — там post-checkout молча уходил в fail-open и переставал обновлять
# pre-commit вовсе. Лечение оттуда же: у git есть ответ на этот вопрос.
# --git-common-dir, а не --git-dir: в worktree первый даёт ОБЩИЙ каталог (где и
# живут хуки), второй — приватный каталог этого дерева, где хуков нет.
# git отдаёт --git-common-dir ОТНОСИТЕЛЬНЫМ (просто «.git»), когда спрошен из
# корня репозитория, — поэтому резолвим его от REPO_ROOT, а не от текущего
# каталога процесса. Найдено тестом 2026-09-11: без `cd "$REPO_ROOT"` правка
# против путей, зависящих от места запуска, сама зависела от места запуска и
# падала с exit 3, если скрипт звали не из корня.
GIT_COMMON="$(git -C "$REPO_ROOT" rev-parse --git-common-dir 2>/dev/null)"
HOOKS_DIR="$(cd "$REPO_ROOT" 2>/dev/null && cd "$GIT_COMMON" 2>/dev/null && pwd)/hooks"
if [[ "$HOOKS_DIR" == "/hooks" ]]; then
    echo "install_hooks.sh: не удалось спросить у git каталог хуков — не git-дерево?" >&2
    exit 3
fi

# Из ДЕРЕВА НИТИ установщик не работает, и починки пути было мало.
# Каталог хуков общий, а симлинки он строит от REPO_ROOT — то есть указывал бы
# в дерево нити. Дерево нити живёт часы и удаляется вместе с задачей; после
# этого хуки сломаны у ВСЕХ деревьев разом, включая главное, и обнаружилось бы
# это первым же коммитом после уборки. Установка хуков — операция над
# репозиторием целиком, её место в главной копии.
# Признак linked worktree: приватный git-dir отличается от общего.
if [[ "$(git -C "$REPO_ROOT" rev-parse --git-dir)" != "$(git -C "$REPO_ROOT" rev-parse --git-common-dir)" ]]; then
    echo "install_hooks.sh: это дерево задачи (linked worktree), а не главная копия." >&2
    echo "  Каталог хуков общий: симлинки отсюда указали бы в дерево, которое скоро удалят," >&2
    echo "  и сломали бы хуки всем деревьям. Запусти из главной копии репозитория." >&2
    exit 4
fi

# MacBook writer-узел (2026-07-17): свои хуки — pre-commit (общий) + post-commit-macbook
# (push→Studio + рестарт бота/дашборда). Studio-вариант post-commit (локальный gated-рестарт)
# сюда НЕ подходит. Раньше скрипт просто отказывал на не-Studio → MacBook-хук ставился руками.
# Идемпотентно (симлинки на трекаемые источники).
if ! /opt/homebrew/bin/python3.11 "$REPO_ROOT/infra_config.py" is-primary; then  # основная машина — private/infra.yaml
    echo "install_hooks.sh: MacBook-режим (хост: $HOSTNAME)."
    mkdir -p "$HOOKS_DIR"
    chmod +x "$REPO_ROOT/scripts/git-hooks/pre-commit" \
             "$REPO_ROOT/scripts/git-hooks/pre_commit_check.py" \
             "$REPO_ROOT/scripts/git-hooks/post-commit-macbook" 2>/dev/null || true
    ln -sf "$REPO_ROOT/scripts/git-hooks/pre-commit" "$HOOKS_DIR/pre-commit"
    ln -sf "$REPO_ROOT/scripts/git-hooks/post-commit-macbook" "$HOOKS_DIR/post-commit"
    # pre-merge-commit — ТОТ ЖЕ хук (2026-09-12). git проводит слияние через
    # ДРУГОЙ хук, и без этой строки merge-коммит проходит мимо ВСЕХ гейтов:
    # ponytail, дубль-гейта, гейта одноразовости, квитанции замысла, ruff и
    # прогона затронутых тестов. Замерено пробой, не предположено.
    # До ввода worktree-на-задачу дыра была теоретической — сливать было нечего;
    # с ветками на нить она становится главной дорогой.
    ln -sf "$REPO_ROOT/scripts/git-hooks/pre-commit" "$HOOKS_DIR/pre-merge-commit"
    # prepare-commit-msg — штамп экспозиции к захвату (BL-THREAD-GATE-1, 27.09): где сделан
    # коммит и сколько деревьев нитей было живо. Не блокирует; только пишет трейлер.
    chmod +x "$REPO_ROOT/scripts/git-hooks/prepare-commit-msg" 2>/dev/null || true
    ln -sf "$REPO_ROOT/scripts/git-hooks/prepare-commit-msg" "$HOOKS_DIR/prepare-commit-msg"
    if [[ "$(readlink "$HOOKS_DIR/post-commit")" == *"post-commit-macbook" ]]; then
        echo "MacBook: pre-commit + pre-merge-commit + post-commit-macbook установлены (симлинки)."
        exit 0
    fi
    echo "install_hooks.sh: MacBook-симлинки не создались корректно"; exit 3
fi
HOOK_SRC="$REPO_ROOT/scripts/git-hooks/pre-commit"
HOOK_DST="$HOOKS_DIR/pre-commit"
PY_CHECK="$REPO_ROOT/scripts/git-hooks/pre_commit_check.py"

if [[ ! -f "$HOOK_SRC" ]]; then
    echo "install_hooks.sh: $HOOK_SRC не найден"
    exit 2
fi
if [[ ! -f "$PY_CHECK" ]]; then
    echo "install_hooks.sh: $PY_CHECK не найден"
    exit 2
fi

chmod +x "$HOOK_SRC" "$PY_CHECK"
mkdir -p "$HOOKS_DIR"
ln -sf "$HOOK_SRC" "$HOOK_DST"

if [[ -L "$HOOK_DST" ]] && [[ "$(readlink "$HOOK_DST")" == "$HOOK_SRC" ]]; then
    echo "pre-commit hook установлен: $HOOK_DST → $HOOK_SRC"
else
    echo "install_hooks.sh: symlink не создан корректно"
    exit 3
fi

# ── pre-merge-commit (2026-09-12) ────────────────────────────────────────────
# Тот же хук: git зовёт при слиянии ДРУГОЙ, и без этой строки merge-коммит
# проходит мимо всех гейтов. Причина та же, что в MacBook-ветке выше.
MERGE_DST="$HOOKS_DIR/pre-merge-commit"
ln -sf "$HOOK_SRC" "$MERGE_DST"
if [[ -L "$MERGE_DST" ]] && [[ "$(readlink "$MERGE_DST")" == "$HOOK_SRC" ]]; then
    echo "pre-merge-commit hook установлен: $MERGE_DST → $HOOK_SRC"
else
    echo "install_hooks.sh: pre-merge-commit symlink не создан корректно"
    exit 3
fi

# ── post-receive (2026-09-13, BL-STUDIO-HOOKS-1) ─────────────────────────────
# Этим замыкается круг: приём push зовёт установщик, установщик ставит НАБОР.
# До сих пор на Studio набор устаревал молча — появившийся 12.09 pre-merge-commit
# пришлось ставить руками, и ручное не наследуется.
# Бутстрап (самый первый симлинк post-receive) остаётся ручным: его некому
# сделать, кроме человека, и это честная граница — её стережёт ночной
# test_git_hook_symlink_on_studio.
RECV_SRC="$REPO_ROOT/scripts/git-hooks/studio-post-receive"
RECV_DST="$HOOKS_DIR/post-receive"
if [[ -f "$RECV_SRC" ]]; then
    chmod +x "$RECV_SRC"
    ln -sf "$RECV_SRC" "$RECV_DST"
    if [[ -L "$RECV_DST" ]] && [[ "$(readlink "$RECV_DST")" == "$RECV_SRC" ]]; then
        echo "post-receive hook установлен: $RECV_DST → $RECV_SRC"
    else
        echo "install_hooks.sh: post-receive symlink не создан корректно"
        exit 3
    fi
fi

# ── post-commit (audit 2026-06-17) ───────────────────────────────────────────
POST_SRC="$REPO_ROOT/scripts/git-hooks/post-commit"
POST_DST="$HOOKS_DIR/post-commit"
SHOULD_SRC="$REPO_ROOT/scripts/git-hooks/should_restart_bot.sh"
if [[ ! -f "$POST_SRC" ]]; then
    echo "install_hooks.sh: $POST_SRC не найден"
    exit 2
fi
chmod +x "$POST_SRC" "$SHOULD_SRC"
ln -sf "$POST_SRC" "$POST_DST"
if [[ -L "$POST_DST" ]] && [[ "$(readlink "$POST_DST")" == "$POST_SRC" ]]; then
    echo "post-commit hook установлен: $POST_DST → $POST_SRC"
else
    echo "install_hooks.sh: post-commit symlink не создан корректно"
    exit 3
fi

if [[ -x "$HOOK_DST" ]]; then
    echo "hook исполняемый, готов к работе"
else
    echo "install_hooks.sh: $HOOK_DST не исполняемый"
    exit 4
fi
