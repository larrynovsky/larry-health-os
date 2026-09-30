#!/bin/zsh
# Следит за папкой health/ и запускает импорт при появлении новых PDF/JPEG/PNG/HEIC
# Запускается автоматически через launchd

HEALTH="$HOME/Library/Mobile Documents/com~apple~CloudDocs/health"
LOG="$HOME/health_watcher.log"
PYTHON="/opt/homebrew/bin/python3.11"
SCRIPTS="$HOME/health_scripts"
LOCK="/tmp/health_import.lock"

# Очищаем lock при любом завершении (штатном, SIGTERM, SIGINT)
trap 'rm -f "$LOCK"' EXIT TERM INT

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Watcher started, watching: $HEALTH" >> "$LOG"

# Ждём стабилизации файла (iCloud может писать файл частями)
wait_stable() {
  local file="$1"
  local prev_size=-1
  local curr_size
  for i in 1 2 3 4 5; do
    sleep 2
    curr_size=$(stat -f%z "$file" 2>/dev/null || echo -1)
    if [[ "$curr_size" == "$prev_size" && "$curr_size" != "-1" ]]; then
      return 0
    fi
    prev_size="$curr_size"
  done
  return 1
}

/opt/homebrew/bin/fswatch \
  --event Created \
  --event Updated \
  --include '\.(pdf|PDF|jpg|jpeg|png|heic|HEIC)$' \
  --recursive \
  --exclude '/data/' \
  --exclude '/.claude/' \
  "$HEALTH" | while read -r changed_file; do

  # Пропускаем неподдерживаемые расширения
  [[ "$changed_file" =~ \.(pdf|PDF|jpg|jpeg|png|heic|HEIC)$ ]] || continue
  # Пропускаем папку data/ (туда сами пишем)
  [[ "$changed_file" == */data/* ]] && continue

  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Новый файл: $changed_file" >> "$LOG"

  # Ждём полной загрузки файла из iCloud
  if ! wait_stable "$changed_file"; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ⚠ файл не стабилизировался, пропускаем: $changed_file" >> "$LOG"
    continue
  fi

  # Защита от параллельного запуска (LOCK покрывает оба скрипта)
  if [ -f "$LOCK" ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Импорт уже запущен, пропускаем" >> "$LOG"
    continue
  fi

  touch "$LOCK"
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Запускаю импорт..." >> "$LOG"

  # Шаг 1: lab маркеры → lab_results (только PDF, regex-based)
  "$PYTHON" "$SCRIPTS/import_all.py" >> "$LOG" 2>&1
  STATUS_ALL=$?

  # Шаг 2: нарратив → events (PDF + JPEG + HEIC, LLM-based)
  "$PYTHON" -u "$SCRIPTS/import_medical_events.py" >> "$LOG" 2>&1
  STATUS_MED=$?

  rm -f "$LOCK"

  if [[ $STATUS_ALL -eq 0 && $STATUS_MED -eq 0 ]]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ✅ Импорт завершён" >> "$LOG"
  else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ❌ Ошибка: import_all=$STATUS_ALL import_medical_events=$STATUS_MED" >> "$LOG"
  fi
done
