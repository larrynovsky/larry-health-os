#!/bin/bash
# rotate_backup_snapshots.sh <backups_dir> <tenant> [days] — снимки перед актами в <tenant>/backups/
# живут <days> дней (docs/BACKUP_POLICY.md R1b, решение владельца 27.09: «правило 60 дней»).
#
# Зачем отдельно от rotate_act_snapshots.sh: тот судит каталог ДАННЫХ, где рядом лежит живой
# канон, и узнаёт снимок по устройству. Здесь канона нет — в backups/ всё снимок, кроме двух
# вещей: ежедневных бэкапов <tenant>_YYYY-MM-DD.db (их срок — R1, 30 дней, ведёт backup_studio.sh)
# и папки keep/ (снимок, нужный дольше срока, кладут туда осознанно). Замер 27.09: без срока здесь
# лежало 16 ГБ, из них 13 — у партнёра (снимки справочника PGS по 5–10 ГБ ежемесячно).
# Удаляет вместе с -wal/-shm; сироты -wal/-shm — тоже; пустые папки — тоже. Печатает удалённое.
set -u
DIR="${1:?backups_dir}"; TENANT="${2:?tenant}"; DAYS="${3:-60}"
[ -d "$DIR" ] || exit 0

is_daily() { [[ "$(basename "$1")" =~ ^${TENANT}_[0-9]{4}-[0-9]{2}-[0-9]{2}\.db(-wal|-shm)?$ ]]; }

find "$DIR" -type f -mtime +"$DAYS" ! -name '*-wal' ! -name '*-shm' ! -path "$DIR/keep/*" -print0 |
while IFS= read -r -d '' f; do
    is_daily "$f" && continue
    rm -f "$f" "$f-wal" "$f-shm" && echo "$f"
done

find "$DIR" -type f \( -name '*-wal' -o -name '*-shm' \) ! -path "$DIR/keep/*" -print0 |
while IFS= read -r -d '' s; do
    base="${s%-*}"
    [ -e "$base" ] || { rm -f "$s" && echo "$s"; }
done

find "$DIR" -mindepth 1 -type d -empty ! -path "$DIR/keep" -delete 2>/dev/null
exit 0
