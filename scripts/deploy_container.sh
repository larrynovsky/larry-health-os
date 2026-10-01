#!/bin/bash
# Деплой тенанта, живущего в контейнере (пилот волны Б, docker-install этап 11). Зовёт post-commit
# с MacBook по ssh, в фоне, когда в корне тенанта лежит метка RUNTIME=container.
# Порядок: образ из HEAD (git archive — в контекст едет только закоммиченное) → рендер compose и
# override владельца → compose up (пересоздаёт службы, чей образ сменился) → сверка: у cron образ
# этого коммита. Хост и образ обязаны быть одним коммитом — иначе теневой сторож краснеет на разнице
# кода. Отказ любого шага — notify.fault (громко), успех — строка в логе.
# Сборка — классическим сборщиком (DOCKER_BUILDKIT=0): buildx на Studio нет (замер 29.09).
set -u
LOG="$HOME/Library/Logs/health-container-deploy.log"
mkdir -p "$(dirname "$LOG")"
exec >>"$LOG" 2>&1
export PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin
CTX="colima-health"
PROJECT="health"
DONE=0
STEP="старт"
alarm() {
    /opt/homebrew/bin/python3.11 -c 'import sys, notify; notify.fault("контейнер владельца не обновился: " + sys.argv[1], person_key=None)' "$1" \
        || echo "$(date '+%F %T') ещё и notify.fault не сработал"
}
on_exit() {
    [ "$(cat "$LOCK/pid" 2>/dev/null)" = "$$" ] && rm -rf "$LOCK"
    if [ "$DONE" != 1 ]; then
        echo "$(date '+%F %T') FAIL на шаге: ${STEP}"
        alarm "${STEP} (лог: ${LOG})"
    fi
}
trap on_exit EXIT
# Один деплой за раз. Замер 30.09 06:59: закрытие нити даёт несколько коммитов подряд (слияние,
# журнал, карта), каждый post-commit звал деплой в фоне — два compose up пересоздавали одни службы
# одновременно, оба упали на конфликте имён, бот и дашборд владельца лежали до следующего деплоя.
# Ждущий строит HEAD на момент захвата — поздний деплой накрывает ранние, а не теряется.
LOCK="$HOME/.health_container_deploy.lock"
STEP="очередь деплоя"
for i in $(seq 1 180); do
    if mkdir "$LOCK" 2>/dev/null; then echo $$ > "$LOCK/pid"; break; fi
    p=$(cat "$LOCK/pid" 2>/dev/null)
    if [ -n "$p" ] && ! kill -0 "$p" 2>/dev/null; then rm -rf "$LOCK"; continue; fi
    [ "$i" = 1 ] && echo "$(date '+%F %T') жду предыдущий деплой (pid ${p:-?})"
    sleep 5
done
[ "$(cat "$LOCK/pid" 2>/dev/null)" = "$$" ] || exit 1
cd "$HOME/health_scripts" || exit 1
SHA=$(git rev-parse --short HEAD)
TZ_HOST=$(readlink /etc/localtime | sed 's#.*/zoneinfo/##')
echo "$(date '+%F %T') деплой ${SHA} (пояс ${TZ_HOST})"
STEP="сборка образа ${SHA}"
# Классический сборщик не задаёт TARGETARCH сам (это делает только BuildKit; замер 29.09: «TARGETARCH:
# parameter not set» на шаге supercronic) — архитектура берётся у движка профиля.
case "$(docker --context "$CTX" info --format '{{.Architecture}}')" in
    aarch64|arm64) ARCH=arm64 ;; x86_64|amd64) ARCH=amd64 ;; *) ARCH=unknown ;;
esac
STEP="языки OCR тенанта"
# Языки OCR — данные тенанта (system_config ocr.languages, решение владельца 29.09), не код образа.
# Источник — база работающего контейнера (после переключения авторитетна она); контейнера нет —
# база на хосте; нет и её — умолчание образа (ARG в Dockerfile), с записью в лог. Замер 30.09:
# первый образ пилота собран без этого аргумента — одного из языков тенанта в контейнере не было.
Q="SELECT value_text FROM system_config WHERE key='ocr.languages'"
LANGS=$(docker --context "$CTX" exec "${PROJECT}-cron-1" sh -c "sqlite3 \"\$HEALTH_DATA_DIR/data/health.db\" \"$Q\"" 2>/dev/null | tail -1)
[ -n "$LANGS" ] || LANGS=$(sqlite3 "$HOME/health/data/health.db" "$Q" 2>/dev/null | tail -1)
OCR_ARGS=()
if [ -n "$LANGS" ]; then
    OCR_ARGS=(--build-arg "OCR_LANGS=${LANGS//+/ }")
    echo "$(date '+%F %T') языки OCR тенанта: ${LANGS}"
else
    echo "$(date '+%F %T') языки OCR тенанта не найдены — умолчание образа"
fi
STEP="манифест git для образа"
# В образе нет ни .git, ни git: тесты и сторожа, спрашивающие «что отслеживается», берут ответ из
# манифеста (git_facts, тот же, что у песочницы test_on_studio). Замер 30.09 07:50: без него утренний
# pytest в контейнере упал на сборе (INTERNALERROR) и не прогнал ни одного теста.
FACTS_DIR=$(mktemp -d)
/opt/homebrew/bin/python3.11 -c 'import sys, pathlib, git_facts; git_facts.write_manifest(pathlib.Path(sys.argv[1]))' \
    "$FACTS_DIR/.git_facts.json" || exit 1
STEP="сборка образа ${SHA}"
# ${arr[@]+...}: /bin/bash 3.2 на macOS с set -u падает на пустом массиве.
git archive --add-file="$FACTS_DIR/.git_facts.json" HEAD | DOCKER_BUILDKIT=0 docker --context "$CTX" build -q -f docker/Dockerfile \
    --build-arg "TARGETARCH=${ARCH}" ${OCR_ARGS[@]+"${OCR_ARGS[@]}"} \
    -t "health-os:${SHA}" -t health-os:local - || exit 1
STEP="языки OCR в образе ${SHA} (${LANGS:-умолчание})"
if [ -n "$LANGS" ]; then
    HAVE=$(docker --context "$CTX" run --rm --entrypoint tesseract "health-os:${SHA}" --list-langs 2>&1)
    for l in ${LANGS//+/ }; do
        printf '%s\n' "$HAVE" | grep -qx "$l" || exit 1
    done
fi
STEP="манифест git в образе ${SHA}"
docker --context "$CTX" run --rm --entrypoint python "health-os:${SHA}" -c \
    "import git_facts, sys; sys.exit(git_facts.source() != 'manifest' or git_facts.head_sha() != '${SHA}')" || exit 1
rm -rf "$FACTS_DIR"
STEP="рендер compose и override"
/opt/homebrew/bin/python3.11 scripts/install.py --docker --tz "$TZ_HOST" >/dev/null || exit 1
/opt/homebrew/bin/python3.11 scripts/install.py --owner-override --tz "$TZ_HOST" >/dev/null || exit 1
STEP="каталог бэкапов на хосте"
# Бэкапы базы — на диске хоста (install.OWNER_BACKUPS_REL), только владельцу учётки: без каталога
# docker создал бы его сам с чужими правами.
mkdir -p "$HOME/container_backups/health" && chmod 700 "$HOME/container_backups" "$HOME/container_backups/health" || exit 1
STEP="compose up"
(cd build/docker && docker --context "$CTX" compose -p "$PROJECT" up -d --remove-orphans) || exit 1
STEP="сверка образа службы cron"
RUNNING=$(docker --context "$CTX" inspect --format '{{.Image}}' "${PROJECT}-cron-1")
WANT=$(docker --context "$CTX" image inspect --format '{{.Id}}' "health-os:${SHA}")
[ -n "$RUNNING" ] && [ "$RUNNING" = "$WANT" ] || exit 1
DONE=1
# Уборка старых образов. Замер 01.10: каждый коммит в main оставлял образ (109 штук, 19,5 ГБ),
# диск виртуальной машины Докера (20 ГБ) заполнился на 100% в 13:26 — сборки падали, база
# владельца отвечала «disk I/O error», документ из входящих не разобрался. Оставляем только
# образ этого коммита (он же :local); откат — пересборка из git, образы данных не несут.
# Отказ уборки деплой не роняет: службы уже на новом образе, а место проверяется ниже.
for t in $(docker --context "$CTX" image ls health-os --format '{{.Tag}}'); do
    [ "$t" = "$SHA" ] || [ "$t" = "local" ] || docker --context "$CTX" rmi "health-os:${t}" >/dev/null 2>&1 || true
done
docker --context "$CTX" image prune -f >/dev/null 2>&1 || true
# Место на диске Докера — тот же диск, где база владельца. Меньше 15% свободного — тревога сразу,
# а не когда база перестанет писать. Граница: меряется только на деплое (а их десятки в день).
USED=$(docker --context "$CTX" exec "${PROJECT}-cron-1" df -P /home/health 2>/dev/null | awk 'NR==2 {gsub("%","",$5); print $5}')
if [ -n "$USED" ] && [ "$USED" -ge 85 ]; then
    /opt/homebrew/bin/python3.11 -c 'import sys, notify; notify.fault("диск Докера владельца занят на " + sys.argv[1] + "% — база может перестать писать", person_key=None)' "$USED" \
        || echo "$(date '+%F %T') ещё и notify.fault не сработал"
    echo "$(date '+%F %T') ВНИМАНИЕ: диск Докера занят на ${USED}%"
fi
echo "$(date '+%F %T') OK ${SHA}: образ собран, службы пересозданы, cron на образе этого коммита"
