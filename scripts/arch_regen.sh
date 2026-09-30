#!/bin/bash
# arch_regen.sh [<главная копия>] [<слаг нити>] — пересборка карты проекта (ARCH_SNAPSHOT и
# TESTING_CONTRACTS, русские и английские) на Studio из задеплоенного main и коммит результата.
#
# Решение владельца 29.09 (нить arch-en-gen): карта пересобирается при каждом закрытии нити.
# До этого четыре генератора ARCH не звал никто, и реестр модулей простоял с 28.06 (C-89).
# Генераторы Studio-only (читают launchd, БД тенанта), коммитит только MacBook (§1, §C) —
# поэтому сборка идёт в одноразовом каталоге Studio, а файлы возвращаются сюда и коммитятся здесь.
#
# Отказ не роняет закрытие нити: код выхода ≠ 0 и строка в logs/arch_regen.log; ночной датчик
# check_arch_map_fresh видит штамп реестра старше последнего слияния (доставка — не молчание).
# Память перевода пополняется на Studio (модель — только через гард секретов, desc_translation).
set -u
MAIN_TREE="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
SLUG="${2:-manual}"

# Только настоящая главная копия: песочницы тестов закрытия нити зовут thread_finish.sh на копии
# репозитория — им нельзя ходить на Studio и коммитить карту.
if [[ "$(cd "$MAIN_TREE" && pwd -P)" != "$(cd "$HOME/health_scripts" 2>/dev/null && pwd -P)" ]]; then
    echo "arch_regen: не главная копия ($MAIN_TREE) — пропуск"
    exit 0
fi

PY=/opt/homebrew/bin/python3.11
LOG="$MAIN_TREE/logs/arch_regen.log"
mkdir -p "$MAIN_TREE/logs"
log() { echo "$(date '+%F %T') [$SLUG] $*" >> "$LOG"; }

STUDIO="$(cd "$MAIN_TREE" && "$PY" -c 'import infra_config; print(infra_config.STUDIO_SSH)' 2>/dev/null)"
[[ -n "$STUDIO" ]] || { log "FAIL: адрес Studio не прочитан"; exit 3; }
REMOTE="/tmp/arch_regen_$$"
FILES=(ARCH_SNAPSHOT.md ARCH_SNAPSHOT.en.md TESTING_CONTRACTS.md TESTING_CONTRACTS.en.md
       methodology/i18n/desc_memory.en.yaml .arch_graph.json .arch_graph.en.json)
GENS="gen_blueprint.py gen_key_paths.py gen_arch_blocks.py gen_schedule.py gen_testing_contracts.py"

# Одноразовый каталог: снимок main + private (адреса, пути установки), стирается в любом исходе.
if ! ssh -o ConnectTimeout=15 "$STUDIO" "set -e; rm -rf $REMOTE; mkdir $REMOTE; cd ~/health_scripts; \
      git archive HEAD | tar -x -C $REMOTE; cp -R private $REMOTE/ 2>/dev/null || true; cd $REMOTE; \
      $PY desc_translation.py --refresh >> gen.log 2>&1 || echo 'refresh failed' >> gen.log; \
      for g in $GENS; do $PY \$g >> gen.log 2>&1; $PY \$g --lang en >> gen.log 2>&1; done; \
      $PY arch_guard.py >> gen.log 2>&1 || true; $PY arch_guard.py --lang en >> gen.log 2>&1 || true"; then
    log "FAIL: сборка на Studio не прошла (Studio недоступна или генератор упал)"
    ssh -o ConnectTimeout=15 "$STUDIO" "rm -rf $REMOTE" 2>/dev/null
    exit 4
fi
rc=0
for f in "${FILES[@]}"; do
    scp -q "$STUDIO:$REMOTE/$f" "$MAIN_TREE/$f" || { log "FAIL: не вернулся $f"; rc=5; }
done
ssh "$STUDIO" "grep -E 'added|untranslated|rejected|Traceback|Error' $REMOTE/gen.log | tail -30" >> "$LOG" 2>/dev/null
ssh -o ConnectTimeout=15 "$STUDIO" "rm -rf $REMOTE"
[[ $rc == 0 ]] || exit $rc

cd "$MAIN_TREE" || exit 7
TRACKED=($(git ls-files -- "${FILES[@]}"))
if git diff --quiet -- "${TRACKED[@]}"; then
    log "OK: карта не изменилась"
    exit 0
fi
git add -- "${TRACKED[@]}"
if git commit -q -m "docs(arch): карта проекта пересобрана при закрытии нити $SLUG" -- "${TRACKED[@]}"; then
    log "OK: карта пересобрана и закоммичена"
else
    log "FAIL: коммит пересобранной карты не прошёл (гейты) — файлы застейджены"
    exit 6
fi
