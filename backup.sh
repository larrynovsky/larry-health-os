#!/bin/bash
# INTENT: code_backup — замысел и инварианты: subsystem_intent.yaml
#         (тёплый слой: docs/explanation/code_backup.md; политика: docs/BACKUP_POLICY.md)
# backup.sh — ежедневный git-commit на MacBook (только source-of-truth для кода).
#
# Запускается LaunchAgent com.larry.health.backup в 03:00 local на MacBook.
#
# Что делает (с 2026-07-24 — НЕ коммит в main, см. блок «Git SNAPSHOT» ниже):
#   1. снимок рабочего дерева во временный индекс -> commit-tree
#      -> refs/backups/daily-<дата> -> push -f этого ref на Studio.
#      main, индекс и working tree не тронуты; деплоя нет; гейты не обходятся.
#      Восстановление: git checkout refs/backups/daily-<дата> -- <файл>
#   Прежняя редакция этой строки («git add -A && git commit») описывала
#   поведение до 2026-07-24 и разошлась с телом скрипта на 25 строк ниже.
#
# Что НЕ делает (с 2026-05-09, после BUG-BACKUP-DEAD):
#   - НЕ бэкапит health.db / базы соседних проектов — это работа Studio (backup_studio.sh).
#     macOS TCC блокировал sqlite3 на ~/Library/Mobile Documents/.
#   - НЕ копирует в iCloud. health.db в iCloud быть НЕ должно: iCloud Drive
#     реплицирует .db без merge -> split-brain (инцидент 2026-06-18: iCloud-форк
#     разошёлся с каноном и удалён). Канон единственный, на локальном диске
#     Studio; инвариант стережёт integrity_tests.check_single_canonical_db (R1/R2).
#
# SQLite-бэкап делает Studio в ~/health/backups/ (см. backup_studio.sh).
# Оффсайт для БД пока не настроен (см. backlog).
#
# Лог: ~/health_scripts/logs/backup.log

set -e
LOG="$HOME/health_scripts/logs/backup.log"
DATE=$(date +%Y-%m-%d)

# ── Режимы (2026-09-07, решение владельца: снимок раз в 3 часа) ──────────────
# Что чинится: окно потери. Суточный снимок в 03:00 означал, что работа, написанная
# и потерянная до следующих 03:00, не спасена ничем — а сторож незакоммиченного
# (uncommitted_watchdog) с 2026-05-23 живёт на Studio и сторожит деплой-таргет, не
# ту машину, где код пишут. Трёхчасовой снимок сокращает окно с ≤24ч до ≤3ч.
#
# `--wip` пишет ОДИН перекатывающийся ref (история не растёт, ротация не нужна);
# суточные refs и их ротация 14 остаются как были — они и есть история.
# ── ЛОГ И ЛОВУШКА ОТКАЗА ЗАВОДЯТСЯ ПЕРВЫМИ (14.09, внешнее ревью) ───────────
# Было наоборот, и это давало ДВА тихих отказа.
#   1. Разбор аргумента стоял ДО `log`, поэтому опечатка в режиме («--WIP»,
#      «wip» без дефисов) уходила в usage, не оставив в логе ни строки.
#      Опечатка в plist = молчащая джоба без следа.
#   2. `cp .git/index` стоит вне цепочки `&&`, а `set -e` убивает скрипт на
#      первой же ошибке — раньше, чем скрипт дойдёт до СВОЕЙ ветки «FAILED —
#      manual recovery нужен». В логе оставалось «started» и ничего больше.
#      Замерено ревьюером на пустом репозитории и на дереве нити.
# Цена второго выросла 14.09: с переходом `--wip` на «снимать всегда» этот
# участок бежит 8 раз в сутки безусловно, а не раз в сутки на грязном дереве.
# Сверху ложится датчик незакоммиченного, который про застывший ref сам
# говорит, что «ноутбук выключен» и «джоба мертва» отсюда неразличимы, —
# отказ был замаскирован дважды.
mkdir -p "$(dirname "$LOG")"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG"; }

_FINISHED=0
_on_exit() {
    local rc=$?
    [ "$_FINISHED" = 1 ] && return 0
    log "=== backup ОБОРВАН: rc=$rc (штатного finished не было) ==="
}
trap _on_exit EXIT

MODE="${1:-daily}"
case "$MODE" in
    --wip)  SNAP_REF="refs/backups/wip";           KIND="wip"   ;;
    daily)  SNAP_REF="refs/backups/daily-$DATE";   KIND="daily" ;;
    # ФИГУРНЫЕ СКОБКИ ОБЯЗАТЕЛЬНЫ. `«$MODE»` съедает переменную: закрывающая
       # кавычка-ёлочка это два байта (C2 BB), и bash в этой локали считает C2
       # частью ИМЕНИ — подставляется пустая `${MODE\xc2}`, а 0xBB остаётся
       # битым байтом в логе. Поймано оракулом, а не чтением.
    *) log "ОТКАЗ: неизвестный режим ${MODE} (ожидалось: --wip или daily)"
       echo "usage: backup.sh [--wip]" >&2; exit 2 ;;
esac

log "=== backup (MacBook git) started ==="

# Git SNAPSHOT кода — ВНЕ main (решение владельца 2026-07-24; раньше: commit
# --no-verify в main + push → ночью деплоился непроверенный WIP, обходились
# ВСЕ гейты (dupgate/L2/доки), review-гейт doc_agent аннулировался, история
# зарастала «auto: daily backup»). Теперь: снимок дерева во временном индексе
# → commit-tree → refs/backups/daily-<дата> → push этого ref на Studio.
# Offsite сохранён; деплоя нет (updateInstead трогает дерево только на
# checked-out ветке); main/index/working tree НЕ тронуты; гейты не обходятся,
# потому что интеграции нет — снимок ≠ коммит в main.
# Восстановление: git checkout refs/backups/daily-<дата> -- <файл>
cd $HOME/health_scripts
# СКОЛЬКО РЕПЛИК СНИМОК НЕ ВИДИТ (14.09, внешнее ревью F5). Снимок берётся с
# ГЛАВНОЙ копии, а работа нити живёт в своём дереве под $HOME/.worktrees — туда
# снимок не заглядывает. Датчик незакоммиченного судит этот снимок и раньше
# молчал «всё хорошо», хотя честный ответ «про N деревьев не знаю». Датчик
# живёт на Studio и о деревьях MacBook узнать не может — значит факт обязан
# ехать В САМОМ снимке. Дом — сообщение коммита снимка: нового носителя не
# заводим. Снимок НА КАЖДОЕ ДЕРЕВО осознанно не строится (замер 16.09: p90
# интервала между коммитами внутри нити — 11 минут, незакоммиченного в живых
# деревьях было 0 строк). Копируется ЗАКОММИЧЕННОЕ — см. блок ниже.
_UNCOVERED=$(( $(git worktree list 2>/dev/null | wc -l) - 1 ))
[ "$_UNCOVERED" -lt 0 ] && _UNCOVERED=0
# ── РАБОТА НИТИ ЖИВЁТ В ОДНОМ ЭКЗЕМПЛЯРЕ (16.09, BL-WIP-WORKTREES-1) ─────────
# Замер 16.09: post-commit пушит ТОЛЬКО main («не main → выход ДО push»), а этот
# скрипт снимал только главную копию. Значит закоммиченная работа нити от первого
# коммита до слияния существует на ОДНОМ диске. В момент замера так лежали 992
# строки двух нитей (22 ч и 62 ч), и `git cat-file -e` на Studio отвечал «нет».
# Ирония измерена: риск растёт ровно от соблюдения §21 — чем строже нити живут в
# своих деревьях, тем больше работы вне единственного канала на Studio.
#
# Лечение нативной возможностью git, без своего формата и своего модуля: push
# веток нитей в ИНЕРТНЫЙ namespace refs/backups/thread/*. `updateInstead` на
# Studio трогает дерево только для checked-out ветки, поэтому деплоя такой push
# не вызывает — тот же приём, что у суточных снимков, доказанный в бою.
#
# СПИСОК ВЕТОК ЕДЕТ В СООБЩЕНИЕ СНИМКА. Датчик живёт на Studio и перечислить
# ветки MacBook не может — тот же предел, что у worktrees_uncovered. Поэтому
# факт пишет тот, кто его знает, а судит получатель: по списку он проверяет
# НАЛИЧИЕ объекта у себя (квитанция в точке потребления), а не наш рапорт об
# успешном push.
_THREADS=$(git for-each-ref --format='%(refname:short):%(objectname:short=12):%(committerdate:unix)' \
    refs/heads/thread/ 2>/dev/null | sed 's|^thread/||' | paste -sd, - )
[ -z "$_THREADS" ] && _THREADS="нет"
if git rev-parse --git-dir > /dev/null 2>&1; then
    # ── РЕШЕНИЕ «СНИМАТЬ ЛИ»: начало блока (14.09.2026) ─────────────────────
    # ПОЧЕМУ `--wip` СНИМАЕТ ВСЕГДА, даже на чистом дереве. До 14.09 условие
    # было одно на оба режима: «дерево грязное — снимай, иначе просто запиши в
    # лог». Для истории (daily) это верно: снимок, равный HEAD, не несёт ничего.
    # Для wip это ломало ДАТЧИК. `refs/backups/wip` читает
    # integrity_tests.check_macbook_uncommitted, и у состояния «чисто» не
    # оказалось писателя: уборка дерева — ровно то событие, которое НЕ двигает
    # ref. Замер 13-14.09: снимок замер на 13.09 21:20, три файла закоммичены в
    # 21:26, и датчик двое суток называл их незакоммиченными; погасить его могла
    # бы только новая грязь. Хуже второе следствие: ref перестаёт свежеть на
    # ИСПРАВНОЙ машине, и датчик уходит в «снимок устарел» — состояние, про
    # которое он сам говорит, что два чтения («ноутбук выключен» и «джоба
    # мертва») отсюда неразличимы. То есть здоровая чистая машина выглядела
    # как умерший бэкап.
    # Цена: один commit-объект раз в 3 часа с тем же деревом, что у HEAD —
    # новых blob'ов и tree'в не создаётся.
    if [ "$KIND" = "wip" ] || ! git diff --quiet || ! git diff --cached --quiet || [ -n "$(git ls-files --others --exclude-standard)" ]; then
    # ── РЕШЕНИЕ «СНИМАТЬ ЛИ»: конец блока ──────────────────────────────────
        TMP_INDEX=$(mktemp /tmp/health_backup_index.XXXXXX)
        cp .git/index "$TMP_INDEX"
        if GIT_INDEX_FILE="$TMP_INDEX" git add -A >> "$LOG" 2>&1 \
           && TREE=$(GIT_INDEX_FILE="$TMP_INDEX" git write-tree) \
           && SNAP=$(git commit-tree "$TREE" -p HEAD -m "snapshot: $KIND backup $DATE (вне main, см. backup.sh) worktrees_uncovered=$_UNCOVERED threads=$_THREADS") \
           && git update-ref "$SNAP_REF" "$SNAP"; then
            log "git snapshot: $SNAP_REF = $SNAP (main не тронут)"
            if git push -f studio "$SNAP_REF" >> "$LOG" 2>&1; then
                log "git push snapshot: OK"
            else
                log "git push snapshot: FAILED — manual recovery нужен"
            fi
            # ── КОПИЯ ВЕТОК НИТЕЙ (только wip: раз в 3 ч, а не раз в сутки) ──
            if [ "$KIND" = "wip" ]; then
                if [ "$_THREADS" != "нет" ]; then
                    if git push -f studio 'refs/heads/thread/*:refs/backups/thread/*' >> "$LOG" 2>&1; then
                        log "git push веток нитей: OK ($_THREADS)"
                    else
                        log "git push веток нитей: FAILED — работа нитей осталась в одном экземпляре"
                    fi
                fi
                # ЧИСТКА ПО ДОСТИЖИМОСТИ, НЕ ПО ОТСУТСТВИЮ ВЕТКИ (решение владельца 16.09).
                # `git push --prune` удалил бы копию ветки, УДАЛЁННОЙ ЛОКАЛЬНО, — а ветка
                # исчезает и при слиянии (норма), и при потере дерева (авария), и
                # различить эти два случая ему нечем: автоматика убрала бы последнюю
                # копию ровно тогда, когда она нужна. Здесь удаляется только
                # ДОКАЗУЕМО доехавшее: tip достижим из локального main.
                # Второй предохранитель бесплатный: если объекта копии нет локально
                # (ветка потеряна и не слита), `--is-ancestor` вернёт ошибку — и копия
                # останется. Отказ в сторону сохранения, а не удаления.
                # ТРИ ИСХОДА, А НЕ ДВА (21.09). С 21.09 закрытие нити ПЕРЕСТАВЛЯЕТ ветку
                # перед слиянием (нить close-rebase-once). Если main успел уйти вперёд,
                # у ветки появляются НОВЫЕ коммиты, а старый tip копии предком main не
                # становится никогда — копия закрытой нити висела бы вечно. Живой
                # пример в первый же день: close-rebase-once, 8ef630c.
                #   1. tip — предок main → удалить (как прежде);
                #   2. коммиты копии уже в main ПО СОДЕРЖАНИЮ (`git cherry` без «+»:
                #      перестановка без правок) → удалить: доказано доехавшее;
                #   3. локальной ветки нет, а в main есть слияние этой нити, но
                #      содержание НЕ совпало (перестановка с правкой конфликта) →
                #      НЕ удалять, а ПЕРЕНЕСТИ в refs/backups/thread-closed/: доказать
                #      «доехало» нечем, значит данные остаются (решение владельца 16.09:
                #      удаляется только доказанное), но из списка «работа в одном
                #      экземпляре» копия уходит — её нить закрыта, и это факт истории.
                git ls-remote studio 'refs/backups/thread/*' 2>/dev/null | \
                while read -r RSHA RREF; do
                    [ -n "$RSHA" ] || continue
                    RSLUG="${RREF#refs/backups/thread/}"
                    if git merge-base --is-ancestor "$RSHA" main 2>/dev/null; then
                        git push studio --delete "$RREF" >> "$LOG" 2>&1 \
                            && log "чистка копий: удалён $RREF (доехал до main)"
                    elif git cat-file -e "$RSHA" 2>/dev/null \
                         && ! git cherry main "$RSHA" 2>/dev/null | grep -q '^+'; then
                        git push studio --delete "$RREF" >> "$LOG" 2>&1 \
                            && log "чистка копий: удалён $RREF (содержание уже в main после перестановки)"
                    elif ! git show-ref --verify --quiet "refs/heads/thread/$RSLUG" \
                         && [ -n "$(git log main --merges -1 --format=%h --fixed-strings \
                                    --grep="Merge branch 'thread/$RSLUG'" 2>/dev/null)" ]; then
                        if git push studio "$RSHA:refs/backups/thread-closed/$RSLUG" >> "$LOG" 2>&1 \
                           && git push studio --delete "$RREF" >> "$LOG" 2>&1; then
                            log "чистка копий: $RREF → refs/backups/thread-closed/ (нить закрыта, содержание переписано при закрытии)"
                        fi
                    fi
                done
            fi
        else
            log "git snapshot: FAILED — manual recovery нужен"
        fi
        rm -f "$TMP_INDEX"
        # Ротация: локально держим 14 последних СУТОЧНЫХ снимков (offsite на Studio не
        # трогаем). `refs/backups/wip` из ротации исключён по построению — он один и
        # перекатывается; без фильтра сортировка съела бы суточные, оставив wip.
        if [ "$KIND" = "daily" ]; then
            git for-each-ref --format='%(refname)' refs/backups/daily-* | sort -r | tail -n +15 | \
                while read -r R; do git update-ref -d "$R" && log "ротация: удалён $R"; done
        fi
    else
        # Сюда попадает только daily на чистом дереве: истории нечего добавить.
        # У wip этой ветки больше нет — см. блок «РЕШЕНИЕ «СНИМАТЬ ЛИ»» выше.
        log "git: no changes (daily, чистое дерево — суточный снимок не нужен)"
    fi
else
    log "git: not a repository, skipped"
fi

_FINISHED=1
log "=== backup (MacBook git) finished ==="
