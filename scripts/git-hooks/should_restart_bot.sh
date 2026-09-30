#!/bin/bash
# should_restart_bot.sh — выделенная тестируемая проверка (audit 2026-06-17).
#
# Вход: список изменённых файлов (аргументы ИЛИ построчно на stdin).
# Выход: exit 0 → бот надо перезапустить (есть .py или launchd .plist);
#        exit 1 → перезапуск не нужен (только docs/.md/прочее).
#
# Зачем отдельно: post-commit раньше держал эту логику inline и был
# непокрываем. Теперь покрыт tests/unit/test_post_commit_hook.py (#5 pos+neg).
set -uo pipefail

files="$*"
if [[ -z "$files" ]] && [[ ! -t 0 ]]; then
    files="$(cat)"
fi

if echo "$files" | tr ' ' '\n' | grep -qE '\.py$|\.plist$'; then
    exit 0
fi
exit 1
