#!/bin/zsh
# watch_and_test.sh — автоматический запуск тестов после изменения кода.
#
# Запускается как launchd-агент на Studio.
# Смотрит на *.py и *.sh в health_scripts/.
# Debounce: 10с после последнего изменения (ждёт окончания rsync).
# При FAIL: notify.fault (не чаще 1 раза в час на тип ошибки).
# При PASS: только лог.

# ДЕРЕВО ВЫЧИСЛЯЕТСЯ, НЕ ЗАДАЁТСЯ (2026-09-02). Хардкод пути означал, что КОПИЯ
# этого скрипта в ~/health_staging гоняет тесты и шлёт алерты о БОЕВОМ дереве —
# песочница переставала быть песочницей. Замер 02.09: обе копии несли одну строку.
# ПОЧЕМУ НЕ ГОЛЫЙ ${BASH_SOURCE[0]} (2026-09-13). Шапка файла — `#!/bin/zsh`, и плист
# зовёт его `/bin/zsh watch_and_test.sh`. В zsh переменной BASH_SOURCE НЕ СУЩЕСТВУЕТ:
# она разворачивалась в пустоту, `dirname ""` давал `.`, а launchd стартует из `/`, и
# SCRIPT_DIR становился "/". Следствия, замеренные 13.09: свой лог скрипт писать не мог
# (`//logs` — read-only, 2696 строк в stderr за 9 суток, канала у них нет), _TREE_KEY
# считался от "/" — то есть у боевого дерева и песочницы снова стал ОДИН замок, ровно то,
# что правка 02.09 и закрывала, — а `fswatch "$SCRIPT_DIR"` девять суток обходил ВСЮ
# файловую систему вместо репозитория. Зелёного оракула у правки 02.09 не было вовсе.
# Форма ниже верна в обоих оболочках: в bash берётся BASH_SOURCE, в zsh — $0.
_SELF="${BASH_SOURCE[0]:-$0}"
SCRIPT_DIR="$(cd "$(dirname "$_SELF")" && pwd)"
# Вход для оракула: печатает вычисленное дерево и выходит ДО mkdir и до fswatch,
# поэтому проверка не запускает вотчер и не трогает боевые каталоги.
if [[ "$1" == "--print-tree" ]]; then
    printf '%s\n' "$SCRIPT_DIR"
    exit 0
fi
LOG="$SCRIPT_DIR/logs/code_watcher.log"
PY="/opt/homebrew/bin/python3.11"
# Маркеры в /tmp КЛЮЧУЮТСЯ ДЕРЕВОМ: общий путь давал два дерева на одном замке и
# одной cooldown-марке — песочница могла заглушить боевой алерт на час (§14).
_TREE_KEY="$(printf '%s' "$SCRIPT_DIR" | shasum | cut -c1-8)"
LOCK="/tmp/health_code_test.${_TREE_KEY}.lock"
MARKER="/tmp/health_code_changed.${_TREE_KEY}"
DEBOUNCE_SEC=10
ALERT_COOLDOWN=3600  # секунд между одинаковыми TG-алертами

mkdir -p "$SCRIPT_DIR/logs"

# $1 — ключ алерта (smoke / integrity), $2 — текст
send_telegram_once() {
    local key="$1" msg="$2"
    local stamp_file="/tmp/health_alert_sent_${key}.${_TREE_KEY}"
    local now last elapsed

    now=$(date +%s)
    last=$(cat "$stamp_file" 2>/dev/null || echo "0")
    elapsed=$((now - last))

    if [[ $elapsed -lt $ALERT_COOLDOWN ]]; then
        echo "$(date '+%H:%M:%S') ⏭ алерт ${key} пропущен (cooldown ${elapsed}s < ${ALERT_COOLDOWN}s)" >> "$LOG"
        return 0
    fi

    # Без текста результатов: в нём могут оказаться медицинские значения.
    (cd "$SCRIPT_DIR" && "$PY" -c 'import notify, sys; notify.fault("watch_and_test: " + sys.argv[1] + " failed", person_key=None)' "$key")

    echo "$now" > "$stamp_file"
}

run_tests() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') ▶ запускаем тесты после изменений" >> "$LOG"
    cd "$SCRIPT_DIR"

    # smoke_tests.py — быстро, критично
    SMOKE_OUT=$(HEALTH_DATA_DIR=$HOME/health $PY smoke_tests.py 2>&1)
    SMOKE_EXIT=$?

    if [[ $SMOKE_EXIT -ne 0 ]]; then
        echo "$(date '+%H:%M:%S') ❌ smoke_tests FAIL" >> "$LOG"
        FAILED=$(echo "$SMOKE_OUT" | grep "❌" | head -5)
        send_telegram_once "smoke" "🚨 Health OS deploy: smoke_tests упали.
${FAILED}"
        rm -f "$LOCK"
        return 1
    fi

    echo "$(date '+%H:%M:%S') ✅ smoke_tests OK" >> "$LOG"

    # integrity_tests.py — --warn-only, не блокирует
    INTEGRITY_OUT=$(HEALTH_DATA_DIR=$HOME/health $PY integrity_tests.py --json 2>/dev/null)
    FAIL_COUNT=$(echo "$INTEGRITY_OUT" | $PY -c "import sys,json; print(json.load(sys.stdin).get('fail',0))" 2>/dev/null || echo "0")

    if [[ "$FAIL_COUNT" -gt 0 ]]; then
        FAILURES=$(echo "$INTEGRITY_OUT" | $PY -c "
import sys,json
d=json.load(sys.stdin)
lines=[str(f) for f in d.get('failures',[])[:3]]
print('\n'.join(lines))
" 2>/dev/null || echo "см. лог")
        send_telegram_once "integrity" "⚠️ Health OS deploy: integrity_tests ${FAIL_COUNT} падений.
${FAILURES}"
        echo "$(date '+%H:%M:%S') ⚠ integrity ${FAIL_COUNT} FAIL" >> "$LOG"
    else
        echo "$(date '+%H:%M:%S') ✅ integrity OK" >> "$LOG"
        # Сбрасываем cooldown при успехе — следующий фейл снова уведомит
        rm -f /tmp/health_alert_sent_integrity
    fi

    echo "$(date '+%H:%M:%S') ✅ тесты завершены" >> "$LOG"
    rm -f "$LOCK"
}

echo "$(date '+%Y-%m-%d %H:%M:%S') code_watcher started" >> "$LOG"

# Следим за .py и .sh файлами
/opt/homebrew/bin/fswatch \
    --event Created --event Updated --event Renamed \
    --include '\.py$' --include '\.sh$' \
    --recursive \
    "$SCRIPT_DIR" | while read -r changed_file; do

    # Обновляем маркер последнего изменения
    echo "$(date +%s)" > "$MARKER"

    # Запускаем фоновый debounce-runner если не запущен
    if [[ ! -f "$LOCK" ]]; then
        touch "$LOCK"
        (
            while true; do
                sleep $DEBOUNCE_SEC
                LAST=$(cat "$MARKER" 2>/dev/null || echo "0")
                NOW=$(date +%s)
                ELAPSED=$((NOW - LAST))
                if [[ $ELAPSED -ge $DEBOUNCE_SEC ]]; then
                    run_tests
                    break
                fi
            done
        ) &
    fi
done
