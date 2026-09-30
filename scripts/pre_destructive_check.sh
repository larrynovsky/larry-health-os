#!/bin/bash
# pre_destructive_check.sh — runnable guard перед reset/checkout/merge.
#
# Запускать ВРУЧНУЮ на MacBook перед любой git-операцией, меняющей working
# tree на Studio. Сравнивает md5 всех *.py и *.sh между MacBook и Studio
# одним batch'ем (per-file ssh = stdin-ловушка + медленно).
#
# Контекст: 2026-05-10 reset --hard удалил 3 ssh-edited файла, которые я
# не закоммитил. CLAUDE.md §4: «Risk → runnable check, не пункт в плане».
#
# Usage:
#   bash $HOME/health_scripts/scripts/pre_destructive_check.sh
#
# Exit codes:
#   0 = всё синхронизировано, можно делать destructive
#   1 = найдены расхождения (список выведен), commit на Studio до операции
#   2 = инфра-ошибка (Studio offline, и т.п.)

set -uo pipefail

STUDIO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && /opt/homebrew/bin/python3.11 -c 'import infra_config; print(infra_config.STUDIO_SSH)')"   # адрес — private/infra.yaml
LOCAL_DIR="$HOME/health_scripts"
REMOTE_DIR="$HOME/health_scripts"

# Sanity: Studio доступен.
if ! ssh -o ConnectTimeout=5 -o BatchMode=yes "$STUDIO" "echo ok" >/dev/null 2>&1; then
    echo "Основная машина недоступна. Проверь сеть (Tailscale) и что машина включена и не заблокирована."
    exit 2
fi

# Получаем все md5 на Studio одним batch'ем.
# git ls-files выдаёт пути; xargs md5 -q даёт хеши в том же порядке.
# Формат вывода: "<md5>  <path>" — md5 -r выдаёт именно так.
REMOTE_HASHES=$(ssh "$STUDIO" "cd $REMOTE_DIR && git ls-files '*.py' '*.sh' | xargs md5 -r 2>/dev/null" 2>/dev/null)

if [[ -z "$REMOTE_HASHES" ]]; then
    echo "Не удалось получить hash'и от Studio."
    exit 2
fi

# Сохраняем remote hashes во временный файл и считаем local.
REMOTE_TMP=$(mktemp)
LOCAL_TMP=$(mktemp)
trap "rm -f $REMOTE_TMP $LOCAL_TMP" EXIT

echo "$REMOTE_HASHES" | sort > "$REMOTE_TMP"

# Локальный batch: тот же список файлов (из remote результата), md5 -r.
FILES=$(echo "$REMOTE_HASHES" | awk '{$1=""; sub(/^[ \t]+/,""); print}')
cd "$LOCAL_DIR"
echo "$FILES" | xargs md5 -r 2>/dev/null | sort > "$LOCAL_TMP"

# Сравниваем.
DIFF_OUTPUT=$(diff "$LOCAL_TMP" "$REMOTE_TMP" 2>/dev/null || true)

if [[ -z "$DIFF_OUTPUT" ]]; then
    N_TOTAL=$(wc -l < "$REMOTE_TMP" | tr -d ' ')
    echo "OK · все $N_TOTAL .py/.sh синхронизированы между MacBook и Studio"
    echo "    Можно безопасно делать destructive (reset/checkout/merge)"
    exit 0
fi

# Анализ: какие файлы расходятся, какие отсутствуют.
echo "Расхождения найдены — НЕ делай destructive до commit'а на Studio:"
echo ""

# Файлы, у которых hash отличается (присутствуют в обоих, но разный md5).
LOCAL_PATHS=$(awk '{$1=""; sub(/^[ \t]+/,""); print}' "$LOCAL_TMP" | sort)
REMOTE_PATHS=$(awk '{$1=""; sub(/^[ \t]+/,""); print}' "$REMOTE_TMP" | sort)

COMMON=$(comm -12 <(echo "$LOCAL_PATHS") <(echo "$REMOTE_PATHS"))
ONLY_LOCAL=$(comm -23 <(echo "$LOCAL_PATHS") <(echo "$REMOTE_PATHS"))
ONLY_REMOTE=$(comm -13 <(echo "$LOCAL_PATHS") <(echo "$REMOTE_PATHS"))

# DIFFS = присутствуют в обоих, но hash отличается.
if [[ -n "$COMMON" ]]; then
    DIFFS=""
    while IFS= read -r f; do
        [[ -z "$f" ]] && continue
        L=$(grep " $f$" "$LOCAL_TMP" | awk '{print $1}')
        R=$(grep " $f$" "$REMOTE_TMP" | awk '{print $1}')
        if [[ "$L" != "$R" ]]; then
            DIFFS="${DIFFS}  $f\n"
        fi
    done <<< "$COMMON"
    if [[ -n "$DIFFS" ]]; then
        echo "DIFF (содержимое отличается, потенциально ssh-only правка):"
        echo -e "$DIFFS"
    fi
fi

if [[ -n "$ONLY_REMOTE" ]]; then
    echo "MISSING на MacBook (есть на Studio):"
    while IFS= read -r f; do
        [[ -n "$f" ]] && echo "  $f"
    done <<< "$ONLY_REMOTE"
    echo ""
fi

if [[ -n "$ONLY_LOCAL" ]]; then
    echo "MISSING на Studio (есть локально, нет в git Studio):"
    while IFS= read -r f; do
        [[ -n "$f" ]] && echo "  $f"
    done <<< "$ONLY_LOCAL"
    echo ""
fi

echo "Что делать:"
echo "  1. Если DIFF — закоммить на Studio: ssh $STUDIO 'cd $REMOTE_DIR && git add <file> && git commit -m ...'"
echo "  2. Если MISSING на MacBook — git fetch studio && git reset --hard studio/main"
echo "  3. Если MISSING на Studio — выяснить почему файл не на canonical"

exit 1
