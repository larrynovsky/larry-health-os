#!/bin/sh
# Фиктивные секреты стенда: гард secrets_dir доволен (каталог задан), настоящие токены не в игре —
# отправка наружу и утечка невозможны. Один дом списка для стенда закрытия нити
# (scripts/test_on_studio.sh, 06.10.2026).
set -e
d="$1"
[ -n "$d" ] || { echo "usage: stand_secrets.sh <dir>" >&2; exit 2; }
mkdir -p "$d"
for f in anthropic_key healthcheck_url oura_token sync_token telegram_token; do
  printf 'STAGING_DUMMY' > "$d/$f"
done
printf '999999999' > "$d/telegram_chat_id"
printf '{}' > "$d/google_calendar_token.json"
chmod 600 "$d"/*
