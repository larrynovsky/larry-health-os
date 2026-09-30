#!/bin/bash
# backup_studio.sh — ежедневный SQLite backup на Mac Studio.
#
# Запускается LaunchAgent com.larry.health.backup в 03:00 local.
#
# Что делает:
#   1. sqlite3 .backup для health.db КАЖДОГО реального тенанта
#      (~/health*/data/health.db, минус дев-клоны) →
#      ~/<tenant>/backups/<tenant>_$DATE.db
#   2. sqlite3 .backup для БД соседнего проекта (если есть)
#   3. Ротация: бэкапы старше 30 дней удаляются (по каждому тенанту);
#      тем же сроком — снимки перед актами в <tenant>/data (scripts/rotate_act_snapshots.sh)
#
# Мультитенант (2026-07-10): раньше бэкапился ТОЛЬКО owner (~/health), у партнёра
# ежедневных бэкапов не было вовсе — непокрытый активный стор. Теперь петля по
# тенантам, зеркалит _tenant_db_paths()/_DEV_CLONE_MARKERS из integrity_tests.
# Провал одного тенанта НЕ роняет остальных; итоговый exit=FAIL, датчик
# check_backup_freshness ловит несвежесть/отсутствие по каждому тенанту.
#
# Что НЕ делает:
#   - НЕ git commit — это работа MacBook (source of truth для git).
#   - НЕ копирует в iCloud — TCC блокирует sqlite3 на iCloud-путь.
#
# Источник правды для здоровья — $HOME/health*/ на Studio (single primary).
# Лог: ~/health/logs/backup.log

LOG="$HOME/health/logs/backup.log"
DATE=$(date +%Y-%m-%d)
FAIL=0
ACT_SNAPSHOT_DAYS=60   # R1b: срок снимков перед актами (решение владельца 27.09); ежедневные — 30 (R1)

mkdir -p "$(dirname "$LOG")"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG"; }

log "=== backup_studio started ==="

# ── 1. health.db по всем реальным тенантам ──────────────────────────────────
# Дев-клоны (staging/_dev/_test/_clone/_bak) НЕ бэкапим — зеркало _DEV_CLONE_MARKERS.
FOUND=0
for HEALTH_DB in "$HOME"/health*/data/health.db; do
    [ -f "$HEALTH_DB" ] || continue
    TENANT_ROOT=$(dirname "$(dirname "$HEALTH_DB")")
    TENANT=$(basename "$TENANT_ROOT")
    case "$TENANT" in
        *staging*|*_dev*|*_test*|*_clone*|*_bak*)
            log "[$TENANT] skip (дев-клон)"; continue;;
    esac
    # Тенант, переехавший в контейнер (docker-install, этап 11): его канон — в томе контейнера,
    # бэкап делает задача контейнера; нативный файл заморожен (только чтение) до отката.
    # Метка — файл RUNTIME в корне тенанта со словом container; её же читает деплой-хук.
    if [ "$(cat "$TENANT_ROOT/RUNTIME" 2>/dev/null)" = "container" ]; then
        log "[$TENANT] skip (живёт в контейнере — бэкап делает контейнер)"; continue
    fi
    FOUND=$((FOUND+1))
    BK_DIR="$TENANT_ROOT/backups"
    BK="$BK_DIR/${TENANT}_$DATE.db"
    mkdir -p "$BK_DIR"

    # WAL checkpoint перед бэкапом: сбрасываем WAL в основной файл и обрезаем.
    WAL_RESULT=$(sqlite3 "$HEALTH_DB" "PRAGMA wal_checkpoint(TRUNCATE);" 2>&1)
    log "[$TENANT] wal_checkpoint(TRUNCATE): $WAL_RESULT"

    if ! sqlite3 "$HEALTH_DB" ".backup '$BK'" 2>>"$LOG"; then
        log "[$TENANT] ERROR: .backup не удался"; FAIL=1; continue
    fi
    # SEC (F-2): бэкап содержит медданные — закрыть от прочих локальных (600).
    chmod 600 "$BK"
    SIZE=$(du -h "$BK" | cut -f1)
    INTEGRITY=$(sqlite3 "$BK" "PRAGMA integrity_check;" 2>&1 | head -1)
    log "[$TENANT] $BK ($SIZE, integrity=$INTEGRITY)"
    [ "$INTEGRITY" != "ok" ] && { log "[$TENANT] ERROR: integrity=$INTEGRITY"; FAIL=1; }

    # Ротация >30д — по этому тенанту
    DEL=$(find "$BK_DIR" -name "${TENANT}_*.db" -mtime +30 -delete -print 2>/dev/null | wc -l | tr -d ' ')
    KEPT=$(ls "$BK_DIR/${TENANT}_"*.db 2>/dev/null | wc -l | tr -d ' ')
    log "[$TENANT] rotation: -$DEL; kept $KEPT"

    # Снимки перед актами — 60 дней (R1b, решение владельца 27.09): рядом с каноном (data/)
    # и в backups/ (всё, кроме ежедневных бэкапов и папки keep/)
    SNAP=$(bash "$(dirname "$0")/scripts/rotate_act_snapshots.sh" "$TENANT_ROOT/data" "$ACT_SNAPSHOT_DAYS" | wc -l | tr -d ' ')
    SNAPB=$(bash "$(dirname "$0")/scripts/rotate_backup_snapshots.sh" "$BK_DIR" "$TENANT" "$ACT_SNAPSHOT_DAYS" | wc -l | tr -d ' ')
    log "[$TENANT] act snapshots rotation (${ACT_SNAPSHOT_DAYS}д): data -$SNAP, backups -$SNAPB"
done

[ "$FOUND" -eq 0 ] && { log "ERROR: не найдено ни одного health.db тенанта"; FAIL=1; }

# ── 2. Базы соседних проектов (только явно настроенные в private/infra.yaml) ──
# HEALTH_PY — тот же выбор интерпретатора, что в run_checks.sh; launchd PATH пуст.
if ! NEIGHBOR_DBS=$(cd "$(dirname "$0")" && "${HEALTH_PY:-/opt/homebrew/bin/python3.11}" -c '
import infra_config
for n in infra_config.NEIGHBORS.values():
    if n.get("db"):
        print(str(n["db"]) + "\t" + str(n["path"] / "backups"))
' 2>>"$LOG"); then
    log "ERROR: конфигурация соседних проектов не прочитана"; FAIL=1
fi
while IFS=$'\t' read -r NEIGHBOR_DB NEIGHBOR_BACKUPS; do
    [ -n "$NEIGHBOR_DB" ] || continue
    PREFIX=$(basename "$NEIGHBOR_DB"); PREFIX="${PREFIX%.*}"
    mkdir -p "$NEIGHBOR_BACKUPS"
    if [ -f "$NEIGHBOR_DB" ]; then
        BK="$NEIGHBOR_BACKUPS/${PREFIX}_$DATE.db"
        BK_QUOTED=${BK//\\/\\\\}; BK_QUOTED=${BK_QUOTED//\"/\\\"}
        if sqlite3 "$NEIGHBOR_DB" ".backup \"$BK_QUOTED\"" 2>>"$LOG"; then
            chmod 600 "$BK"
            SIZE=$(du -h "$BK" | cut -f1)
            log "$PREFIX.db: $BK ($SIZE)"
        else
            log "$PREFIX.db: ERROR .backup не удался"; FAIL=1
        fi
        DEL=$(find "$NEIGHBOR_BACKUPS" -name "${PREFIX}_*.db" -mtime +30 -delete -print 2>/dev/null | wc -l | tr -d ' ')
        log "$PREFIX rotation: -$DEL"
    else
        log "$PREFIX.db: skip (not found)"
    fi
done <<< "$NEIGHBOR_DBS"

log "=== backup_studio finished (FAIL=$FAIL) ==="
exit $FAIL
