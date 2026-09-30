#!/bin/zsh
# icloud_conflict_check.sh — детектирует конфликтные копии iCloud в health/
# Запускается каждые 6 часов через launchd на MacBook.

HEALTH_DIR="$HOME/Library/Mobile Documents/com~apple~CloudDocs/health"
MARKER="/tmp/.last_conflict_check"
_SELF="${BASH_SOURCE[0]:-$0}"
SCRIPT_DIR="$(cd "$(dirname "$_SELF")" && pwd)"
PY="/opt/homebrew/bin/python3.11"

# Find conflicted copies (skip .tmp_ files from atomic imports, skip .DS_Store)
CONFLICTS=$(find "$HEALTH_DIR" \
    -name "*(conflicted copy)*" \
    ! -name ".DS_Store" \
    ! -name ".tmp_*" \
    2>/dev/null)

if [[ -n "$CONFLICTS" ]]; then
    # Имена документов могут содержать личные данные, в общий журнал их не пишем.
    (cd "$SCRIPT_DIR" && "$PY" -c 'import notify; notify.fault("icloud_conflict_check: conflicting copies found", person_key=None)')
    echo "$(date): conflict found, logged" >> /tmp/conflict_check.log
fi

touch "$MARKER"
