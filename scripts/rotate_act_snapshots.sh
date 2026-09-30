#!/bin/bash
# rotate_act_snapshots.sh <data_dir> [days] — снимки перед актами живут столько же, сколько
# ежедневные бэкапы (docs/BACKUP_POLICY.md R1b, решение владельца 27.09, BL-SNAPSHOT-SPRAWL-1).
#
# Снимок узнаётся по УСТРОЙСТВУ, а не по имени: имена у актов свои (presnap_, pre-, preop_,
# before_, .bak…), список имён отстал бы на первом новом акте. Снимок — это:
#   * любой SQLite-файл в <data_dir> (глубина 2), кроме самого канона <data_dir>/health.db;
#   * любой файл с «.bak» в имени (копии не-SQLite: JSON каталога инструментов и т.п.);
#   * всё в <data_dir>/backups/ (разовые дампы актов: .sql, .json, .db).
# Старше <days> дней — удаляется вместе со своими -wal/-shm; -wal/-shm без базы — сироты.
# Печатает каждый удалённый путь; живой канон и его -wal/-shm не трогает никогда.
set -u
DIR="${1:?data_dir}"; DAYS="${2:-30}"; CANON="$DIR/health.db"
[ -d "$DIR" ] || exit 0

is_snapshot() {
    local f="$1"
    [ "$f" = "$CANON" ] && return 1
    case "$f" in "$DIR/backups/"*|*.bak*) return 0;; esac
    head -c 15 "$f" 2>/dev/null | grep -q "SQLite format 3"
}

find "$DIR" -maxdepth 2 -type f -mtime +"$DAYS" ! -name '*-wal' ! -name '*-shm' -print0 |
while IFS= read -r -d '' f; do
    is_snapshot "$f" || continue
    rm -f "$f" "$f-wal" "$f-shm" && echo "$f"
done

find "$DIR" -maxdepth 2 -type f \( -name '*-wal' -o -name '*-shm' \) -print0 |
while IFS= read -r -d '' s; do
    base="${s%-*}"
    [ "$base" = "$CANON" ] && continue
    [ -e "$base" ] || { rm -f "$s" && echo "$s"; }
done
exit 0
