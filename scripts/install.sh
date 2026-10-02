#!/usr/bin/env bash
# Larry Health OS — установка из готового образа одной командой.
# Делает то же, что урок docs/tutorials/first_install.md (шаги 2–7), и сначала проверяет всё,
# что для этого нужно. Чего-то не хватает — говорит, чем поправить, и ничего не трогает.
#
#   curl -fsSL https://github.com/larrynovsky/larry-health-os/releases/latest/download/install.sh -o install.sh
#   bash install.sh            # проверка + установка (повторный запуск = обновление)
#   bash install.sh --check    # только проверка, ничего не меняет
#
# Повторный запуск обновляет compose.yaml (имя образа) и пересоздаёт контейнеры; .env с поясом
# и ключи не трогает. Значения ключей не печатаются никогда, ни целиком, ни частью: трассировка
# (bash -x) выключается, ключи уходят в curl через stdin, переменные окружения с ключами
# снимаются до первой внешней команды. Граница: окружение, с которым скрипт запущен
# (--non-interactive), видно другим процессам того же пользователя, пока он работает.
set +x
set -euo pipefail

# Ключи из окружения забираем и снимаем сразу — дочерние процессы их не унаследуют.
ENV_TG="${TELEGRAM_TOKEN:-}"; ENV_ID="${TELEGRAM_CHAT_ID:-}"; ENV_AK="${ANTHROPIC_KEY:-}"; ENV_LK="${LLM_KEY:-}"
unset TELEGRAM_TOKEN TELEGRAM_CHAT_ID ANTHROPIC_KEY LLM_KEY

RELEASE_URL="https://github.com/larrynovsky/larry-health-os/releases/latest/download"
IMAGE_NAME="larry-health-os"
DIR="${HOME}/health-docker"
MODE=install
DIST=""            # каталог с compose.yaml/health.env вместо выпуска (CI, проверка невыпущенного)
INTERACTIVE=1
VERIFY=1           # проверять токен Telegram и ключ Anthropic по сети
PROVIDER="${LLM_PROVIDER:-}"   # пусто = anthropic (урок — один путь); выбор — docs/how-to/llm_provider.md
CI_FAKE=0          # --ci-fake-keys: ключи заведомо поддельные (CI) — бот обязан упасть на InvalidToken
FAILS=0
WARNS=0
FIXES=()
SELF=""
[ -f "$0" ] && SELF="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"

case "${LANG:-}${LC_ALL:-}" in *ru*|*RU*) RU=1 ;; *) RU=0 ;; esac
say() { if [ "$RU" = 1 ]; then printf '%s\n' "$1"; else printf '%s\n' "$2"; fi; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$(say "$1" "$2")"; }
warn() { WARNS=$((WARNS + 1)); printf '  \033[33m!\033[0m %s\n' "$(say "$1" "$2")"; }
fail() { FAILS=$((FAILS + 1)); printf '  \033[31m✗\033[0m %s\n' "$(say "$1" "$2")"; FIXES+=("$(say "$3" "$4")"); }
die()  { printf '\033[31m%s\033[0m\n' "$(say "$1" "$2")" >&2; exit 1; }
abspath() { case "$1" in /*) printf '%s' "$1" ;; *) printf '%s/%s' "$PWD" "$1" ;; esac; }

usage() {
  say "Использование: bash install.sh [--check] [--dir КАТАЛОГ] [--non-interactive] [--no-verify]
  --check            только проверить зависимости, ничего не менять
  --dir КАТАЛОГ      куда ставить (по умолчанию ~/health-docker)
  --non-interactive  без вопросов: пояс из HEALTH_TZ, ключи из TELEGRAM_TOKEN,
                     TELEGRAM_CHAT_ID, ANTHROPIC_KEY
  --no-verify        не проверять ключи по сети (и их формат)
  --dist КАТАЛОГ     взять compose.yaml и health.env из каталога, а не из выпуска
  --provider ИМЯ     поставщик моделей: anthropic (по умолчанию), openai, gemini;
                     ключ не-Anthropic в --non-interactive — из LLM_KEY" \
"Usage: bash install.sh [--check] [--dir DIR] [--non-interactive] [--no-verify]
  --check            only check dependencies, change nothing
  --dir DIR          where to install (default ~/health-docker)
  --non-interactive  no questions: time zone from HEALTH_TZ, keys from TELEGRAM_TOKEN,
                     TELEGRAM_CHAT_ID, ANTHROPIC_KEY
  --no-verify        do not verify keys online (nor their format)
  --dist DIR         take compose.yaml and health.env from DIR instead of the release
  --provider NAME    model provider: anthropic (default), openai, gemini;
                     a non-Anthropic key in --non-interactive comes from LLM_KEY"
}

while [ $# -gt 0 ]; do
  case "$1" in
    --check) MODE=check ;;
    --dir) [ $# -ge 2 ] || die "--dir: нужен каталог" "--dir needs a directory"; DIR="$(abspath "$2")"; shift ;;
    --dist) [ $# -ge 2 ] || die "--dist: нужен каталог" "--dist needs a directory"; DIST="$(abspath "$2")"; shift ;;
    --non-interactive) INTERACTIVE=0 ;;
    --provider) [ $# -ge 2 ] || die "--provider: нужно имя" "--provider needs a name"; PROVIDER="$2"; shift ;;
    --no-verify) VERIFY=0 ;;
    --ci-fake-keys) VERIFY=0; CI_FAKE=1 ;;
    -h|--help) usage; exit 0 ;;
    *) usage; die "Неизвестный параметр: $1" "Unknown option: $1" ;;
  esac
  shift
done
if [ "$MODE" = install ] && [ "$INTERACTIVE" = 1 ] && [ ! -t 0 ]; then
  die "Нет терминала для вопросов. Скачайте скрипт файлом и запустите: bash install.sh (или --non-interactive)." \
      "No terminal for questions. Download the script as a file and run: bash install.sh (or --non-interactive)."
fi

case "${PROVIDER:-anthropic}" in
  anthropic|openai|gemini) ;;
  *) die "Неизвестный поставщик моделей: ${PROVIDER} (anthropic, openai, gemini)" "Unknown model provider: ${PROVIDER} (anthropic, openai, gemini)" ;;
esac

# ── 1. Проверка ──────────────────────────────────────────────────────────────────────────────
say "Проверяю, всё ли есть для установки…" "Checking prerequisites…"

OS="$(uname -s)"
WSL=0
if [ "$OS" = Linux ] && grep -qi microsoft /proc/version 2>/dev/null; then WSL=1; fi
case "$OS" in
  Darwin) ok "macOS $(sw_vers -productVersion 2>/dev/null || true)" "macOS $(sw_vers -productVersion 2>/dev/null || true)" ;;
  Linux)
    if [ "$WSL" = 1 ]; then ok "Linux внутри Windows (WSL)" "Linux inside Windows (WSL)"
    else ok "Linux" "Linux"; fi ;;
  *) fail "система ${OS} не поддерживается" "system ${OS} is not supported" \
          "Нужны macOS, Linux или Windows через WSL2 (урок, раздел «Windows»)." \
          "You need macOS, Linux, or Windows via WSL2 (tutorial, section 'Windows')." ;;
esac

case "$(uname -m)" in
  x86_64|amd64|arm64|aarch64) ok "процессор $(uname -m)" "CPU $(uname -m)" ;;
  *) fail "процессор $(uname -m): образ собран только для amd64 и arm64" \
          "CPU $(uname -m): the image is built only for amd64 and arm64" \
          "Нужна машина на amd64 (Intel/AMD) или arm64 (Apple Silicon, ARM)." \
          "You need an amd64 (Intel/AMD) or arm64 (Apple Silicon, ARM) machine." ;;
esac

if command -v curl >/dev/null 2>&1; then ok "curl" "curl"
else
  fail "нет curl" "curl is missing" "Поставьте curl: sudo apt install curl (Linux/WSL)." \
       "Install curl: sudo apt install curl (Linux/WSL)."
fi

# Контейнер работает от пользователя 1000. На Linux с другим номером ключи надо отдать ему —
# для этого нужны права root (sudo), и это выясняется до любых изменений.
PRIV=""
NEED_CHOWN=0
if [ "$OS" = Linux ] && [ "$(id -u)" != 1000 ]; then
  NEED_CHOWN=1
  if [ "$(id -u)" != 0 ]; then
    if command -v sudo >/dev/null 2>&1; then PRIV=sudo; ok "sudo есть (понадобится для ключей)" "sudo available (needed for the keys)"
    else
      fail "ваш номер пользователя не 1000, а sudo нет — контейнер не сможет прочесть ключи" \
           "your user id is not 1000 and there is no sudo — the container will not be able to read the keys" \
           "Запустите от пользователя с номером 1000 или поставьте sudo." \
           "Run as the user with id 1000, or install sudo."
    fi
  fi
fi

if [ "$WSL" = 1 ]; then
  if [ "$(ps -p 1 -o comm= 2>/dev/null)" = systemd ]; then ok "systemd в Ubuntu включён" "systemd is enabled in Ubuntu"
  else
    fail "systemd в Ubuntu выключен — Докер не будет стартовать сам" \
         "systemd is off in Ubuntu — Docker will not start on its own" \
         "Урок, раздел «Windows», шаг Б: строка про /etc/wsl.conf, затем в PowerShell wsl --shutdown." \
         "Tutorial, section 'Windows', step B: the /etc/wsl.conf line, then wsl --shutdown in PowerShell."
  fi
  win_home=""
  if command -v powershell.exe >/dev/null 2>&1; then
    # shellcheck disable=SC2016  # $env:USERPROFILE — переменная PowerShell, не bash
    win_home="$(powershell.exe -NoProfile -Command '$env:USERPROFILE' 2>/dev/null | tr -d '\r' || true)"
    [ -n "$win_home" ] && win_home="$(wslpath "$win_home" 2>/dev/null || true)"
  fi
  if [ -n "$win_home" ] && grep -qs '^instanceIdleTimeout=-1' "${win_home}/.wslconfig"; then
    ok "Windows не будет останавливать Ubuntu (.wslconfig)" "Windows will not stop Ubuntu (.wslconfig)"
  else
    warn "не вижу instanceIdleTimeout=-1 в .wslconfig — Windows остановит систему, когда вы закроете окно Ubuntu (урок, «Windows», шаг Б)" \
         "no instanceIdleTimeout=-1 in .wslconfig — Windows will stop the system when you close the Ubuntu window (tutorial, 'Windows', step B)"
  fi
fi

DOCKER_OK=0
if ! command -v docker >/dev/null 2>&1; then
  if [ "$OS" = Darwin ]; then
    fail "нет Докера" "Docker is missing" \
         "Поставьте любой Докер: Docker Desktop, OrbStack или Colima (урок, шаг 1: brew install colima docker docker-compose)." \
         "Install any Docker: Docker Desktop, OrbStack or Colima (tutorial, step 1: brew install colima docker docker-compose)."
  else
    fail "нет Докера" "Docker is missing" \
         "Поставьте Docker Engine: curl -fsSL https://get.docker.com | sudo sh && sudo usermod -aG docker \$USER, затем откройте терминал заново." \
         "Install Docker Engine: curl -fsSL https://get.docker.com | sudo sh && sudo usermod -aG docker \$USER, then reopen the terminal."
  fi
else
  if derr="$(docker info --format '{{.ServerVersion}}' 2>&1)"; then
    ok "Докер ${derr} отвечает" "Docker ${derr} is running"
    DOCKER_OK=1
  elif printf '%s' "$derr" | grep -qi 'permission denied'; then
    fail "Докер есть, но вам он не разрешён" "Docker is installed but you are not allowed to use it" \
         "sudo usermod -aG docker \$USER, затем закройте и откройте терминал (в WSL — ещё wsl --shutdown в PowerShell)." \
         "sudo usermod -aG docker \$USER, then close and reopen the terminal (in WSL also wsl --shutdown in PowerShell)."
  elif [ "$OS" = Darwin ]; then
    fail "Докер не запущен" "Docker is not running" \
         "Запустите Docker Desktop/OrbStack или: colima start --profile health --cpu 2 --memory 4 --disk 20" \
         "Start Docker Desktop/OrbStack, or: colima start --profile health --cpu 2 --memory 4 --disk 20"
  else
    fail "Докер не запущен" "Docker is not running" "sudo systemctl enable --now docker" \
         "sudo systemctl enable --now docker"
  fi
  if docker compose version >/dev/null 2>&1; then
    ok "docker compose $(docker compose version --short 2>/dev/null || true)" "docker compose $(docker compose version --short 2>/dev/null || true)"
  else
    fail "нет дополнения docker compose" "the docker compose plugin is missing" \
         "macOS: урок, шаг 1 (ссылка на docker-compose в ~/.docker/cli-plugins). Linux: sudo apt install docker-compose-plugin." \
         "macOS: tutorial, step 1 (link docker-compose into ~/.docker/cli-plugins). Linux: sudo apt install docker-compose-plugin."
  fi
fi

if [ "$DOCKER_OK" = 1 ]; then
  mem="$(docker info --format '{{.MemTotal}}' 2>/dev/null || echo 0)"
  case "$mem" in ''|*[!0-9]*) mem=0 ;; esac
  mem_gb="$(awk -v b="$mem" 'BEGIN { printf "%.1f", b / 1073741824 }')"
  if [ "$mem" -ge 3500000000 ]; then ok "памяти у Докера: ${mem_gb} ГБ" "memory available to Docker: ${mem_gb} GB"
  else
    warn "у Докера мало памяти: ${mem_gb} ГБ (нужно около 4) — распознавание документов может падать" \
         "Docker has little memory: ${mem_gb} GB (about 4 needed) — document recognition may fail"
  fi
fi

# Место: там, куда ляжет установка, и (на Linux) там, где Докер хранит образы. На Маке образы
# живут на диске виртуальной машины — его размер задан при её создании (у Colima — --disk).
free_gb() { df -Pk "$1" 2>/dev/null | awk 'NR==2 {printf "%d", $4 / 1048576}'; }
parent="$DIR"; while [ ! -d "$parent" ]; do parent="$(dirname "$parent")"; done
g="$(free_gb "$parent")"
if [ -n "$g" ] && [ "$g" -ge 2 ]; then ok "место для установки (${parent}): ${g} ГБ" "space for the installation (${parent}): ${g} GB"
elif [ -n "$g" ]; then warn "в ${parent} свободно ${g} ГБ" "${g} GB free in ${parent}"; fi
if [ "$DOCKER_OK" = 1 ] && [ "$OS" = Linux ]; then
  droot="$(docker info --format '{{.DockerRootDir}}' 2>/dev/null || true)"
  g="$( [ -n "$droot" ] && free_gb "$droot" || true)"
  if [ -n "$g" ] && [ "$g" -ge 20 ]; then ok "место для образов Докера: ${g} ГБ" "space for Docker images: ${g} GB"
  elif [ -n "$g" ]; then warn "для образов Докера свободно ${g} ГБ, нужно около 20" "${g} GB free for Docker images, about 20 needed"
  else warn "не удалось проверить место для образов Докера (${droot:-?})" "could not check space for Docker images (${droot:-?})"; fi
fi

if command -v curl >/dev/null 2>&1; then
  if [ -n "$DIST" ]; then
    if [ -f "${DIST}/compose.yaml" ] && [ -f "${DIST}/health.env" ]; then ok "файлы выпуска — из ${DIST}" "release files from ${DIST}"
    else fail "в ${DIST} нет compose.yaml или health.env" "${DIST} has no compose.yaml or health.env" "Проверьте --dist." "Check --dist."; fi
  elif curl -fsIL -o /dev/null --max-time 20 "${RELEASE_URL}/compose.yaml"; then ok "GitHub доступен, выпуск на месте" "GitHub reachable, release found"
  else
    fail "не скачивается ${RELEASE_URL}/compose.yaml" "cannot download ${RELEASE_URL}/compose.yaml" \
         "Проверьте интернет; за корпоративным прокси нужен доступ к github.com." \
         "Check the internet connection; behind a corporate proxy github.com must be allowed."
  fi
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 https://ghcr.io/v2/ || true)"
  if [ "$code" = 401 ] || [ "$code" = 200 ]; then ok "реестр образов ghcr.io доступен" "image registry ghcr.io reachable"
  else
    fail "ghcr.io недоступен (ответ ${code:-нет})" "ghcr.io unreachable (response ${code:-none})" \
         "Образ скачивается с ghcr.io: проверьте интернет и прокси." \
         "The image is downloaded from ghcr.io: check the internet connection and proxy."
  fi
fi

# Каталог установки: пустой, новый или уже наш. Чужое не трогаем. Остатки прерванного
# скачивания (*.part, .env.download) — наши: повтор после обрыва не должен упираться в отказ.
dir_has_foreign() {
  local f
  [ -d "$1" ] || return 1
  for f in "$1"/* "$1"/.[!.]*; do
    [ -e "$f" ] || continue
    case "${f##*/}" in compose.yaml.part|.env.download|.env.download.part|.env.part) ;; *) return 0 ;; esac
  done
  return 1
}
OURS=0
if [ -f "${DIR}/compose.yaml" ]; then
  if grep -Eq "^[[:space:]]*image:[[:space:]]*[^#[:space:]]*${IMAGE_NAME}" "${DIR}/compose.yaml"; then OURS=1
  else
    fail "в ${DIR} лежит чужой compose.yaml" "${DIR} contains a compose.yaml that is not ours" \
         "Укажите другой каталог: --dir ~/health-docker" "Choose another directory: --dir ~/health-docker"
  fi
elif dir_has_foreign "$DIR"; then
  fail "каталог ${DIR} не пуст и это не установка Health" "${DIR} is not empty and is not a Health installation" \
       "Укажите пустой или новый каталог: --dir ~/health-docker" "Choose an empty or new directory: --dir ~/health-docker"
fi

running=0
if [ "$OURS" = 1 ] && [ "$DOCKER_OK" = 1 ] \
   && (cd "$DIR" && docker compose ps --status running --services 2>/dev/null | grep -qx dashboard); then
  running=1
  ok "установка в ${DIR} уже работает — будет обновление" "an installation in ${DIR} is already running — this will update it"
fi
if [ "$running" = 0 ]; then
  # Встроенная проверка bash: подключились — значит, порт кто-то слушает. Не зависит от lsof/ss.
  if (exec 3<>/dev/tcp/127.0.0.1/8001) 2>/dev/null; then
    fail "порт 8001 занят другой программой — дашборду некуда встать" "port 8001 is taken by another program — the dashboard cannot start" \
         "Найдите её: lsof -nP -iTCP:8001 -sTCP:LISTEN (macOS) или ss -ltnp | grep 8001 (Linux) — и остановите." \
         "Find it: lsof -nP -iTCP:8001 -sTCP:LISTEN (macOS) or ss -ltnp | grep 8001 (Linux) — and stop it."
  else ok "порт 8001 свободен" "port 8001 is free"
  fi
fi

echo
if [ "$FAILS" -gt 0 ]; then
  say "Не хватает (${FAILS}). Поправьте и запустите скрипт ещё раз:" "Missing (${FAILS}). Fix and run the script again:"
  for f in "${FIXES[@]}"; do printf '  → %s\n' "$f"; done
  exit 1
fi
if [ "$WARNS" -gt 0 ]; then say "Всё на месте (предупреждений: ${WARNS})." "All set (warnings: ${WARNS})."
else say "Всё на месте." "All set."; fi
[ "$MODE" = check ] && exit 0

# ── 2. Файлы запуска ─────────────────────────────────────────────────────────────────────────
mkdir -p "$DIR"
cd "$DIR"
fetch() {  # fetch <имя в выпуске> <куда>: целиком или никак
  if [ -n "$DIST" ]; then cp "${DIST}/$1" "$2.part"
  else curl -fsSL -o "$2.part" "${RELEASE_URL}/$1"; fi
  mv "$2.part" "$2"
}
fetch compose.yaml compose.yaml
image="$(grep -m1 'image:' compose.yaml | awk '{print $2}')"
ok "compose.yaml: ${image}" "compose.yaml: ${image}"

tz_ok() {  # имя пояса существует в базе часовых поясов (или похоже на неё, если базы нет)
  case "$1" in ''|/*|*..*) return 1 ;; esac
  printf '%s' "$1" | grep -Eq '^[A-Za-z0-9_+/-]+$' || return 1
  if [ -d /usr/share/zoneinfo ]; then [ -f "/usr/share/zoneinfo/$1" ]; else return 0; fi
}
if [ -f .env ]; then
  ok ".env уже есть — пояс и настройки не трогаю" ".env already exists — keeping time zone and settings"
else
  tz="${HEALTH_TZ:-}"
  if [ -z "$tz" ]; then
    if [ -L /etc/localtime ]; then tz="$(readlink /etc/localtime | sed 's#.*/zoneinfo/##')"; fi
    if [ -z "$tz" ] && command -v timedatectl >/dev/null 2>&1; then tz="$(timedatectl show -p Timezone --value 2>/dev/null || true)"; fi
    if [ "$INTERACTIVE" = 1 ]; then
      while :; do
        printf '%s [%s]: ' "$(say "Ваш часовой пояс" "Your time zone")" "${tz:-Europe/Berlin}"
        read -r answer
        answer="${answer:-${tz:-Europe/Berlin}}"
        tz_ok "$answer" && { tz="$answer"; break; }
        warn "нет такого пояса: ${answer} (пример: Europe/Berlin)" "no such time zone: ${answer} (example: Europe/Berlin)"
      done
    fi
  fi
  tz_ok "$tz" || die "Нет такого часового пояса: «${tz}» (пример: Europe/Berlin)." "No such time zone: '${tz}' (example: Europe/Berlin)."
  # .env появляется только целиком и с поясом: прерванная установка не оставит выпускной UTC.
  fetch health.env .env.download
  sed -e "s#^TZ=.*#TZ=${tz}#" -e "s#^HEALTH_TZ=.*#HEALTH_TZ=${tz}#" .env.download > .env.part
  rm -f .env.download
  mv .env.part .env
  ok "часовой пояс: ${tz}" "time zone: ${tz}"
fi
# Поставщик моделей: названный явно (--provider / LLM_PROVIDER) пишется в .env; не названный —
# берётся из .env (повтор = обновление не меняет выбор), иначе anthropic.
if [ -n "$PROVIDER" ]; then
  if grep -q '^HEALTH_LLM_PROVIDER=' .env; then
    sed "s#^HEALTH_LLM_PROVIDER=.*#HEALTH_LLM_PROVIDER=${PROVIDER}#" .env > .env.part && mv .env.part .env
  else printf 'HEALTH_LLM_PROVIDER=%s\n' "$PROVIDER" >> .env; fi
else
  PROVIDER="$(sed -n 's/^HEALTH_LLM_PROVIDER=//p' .env | tail -n1)"; PROVIDER="${PROVIDER:-anthropic}"
fi
ok "поставщик моделей: ${PROVIDER}" "model provider: ${PROVIDER}"

# ── 3. Ключи ─────────────────────────────────────────────────────────────────────────────────
SEC="${DIR}/secrets"
[ -d "$SEC" ] || mkdir -m 700 "$SEC"
as_priv() { if [ -n "$PRIV" ]; then "$PRIV" "$@"; else "$@"; fi; }
has_secret() { [ -s "${SEC}/$1" ] 2>/dev/null || { [ "$NEED_CHOWN" = 1 ] && as_priv test -s "${SEC}/$1" 2>/dev/null; }; }
put_secret() {  # put_secret <файл> <значение>; значение не печатается и не попадает в аргументы
  if [ -w "$SEC" ]; then (umask 077; printf '%s' "$2" > "${SEC}/$1")
  else (umask 077; printf '%s' "$2" | as_priv tee "${SEC}/$1" >/dev/null); fi
}
ask_secret() {  # ask_secret <значение из окружения> <вопрос ru> <вопрос en> <скрыть 0|1>
  local v="$1"
  if [ -z "$v" ] && [ "$INTERACTIVE" = 1 ]; then
    printf '%s: ' "$(say "$2" "$3")" >&2
    if [ "$4" = 1 ]; then read -rs v; echo >&2; else read -r v; fi
  fi
  [ -n "$v" ] || die "Не задано: $3" "Not set: $3"
  printf '%s' "$v"
}
# Проверки по сети. curl -q первым: пользовательский .curlrc (verbose/trace) не читается.
TG_NAME=""; TG_STATUS=""
tg_check() {  # итог — в TG_STATUS (ok / bad / net), имя бота — в TG_NAME; зовётся без $(), иначе имя теряется
  local out code
  out="$(printf 'url = "https://api.telegram.org/bot%s/getMe"\n' "$1" \
         | curl -q -s --max-time 20 -w '\n%{http_code}' -K - 2>/dev/null || true)"
  code="$(printf '%s' "$out" | tail -n1)"
  TG_NAME="$(printf '%s' "$out" | sed -n 's/.*"username":"\([^"]*\)".*/\1/p')"
  case "$code" in 200) TG_STATUS=ok ;; 401|404) TG_STATUS=bad ;; *) TG_STATUS=net ;; esac
}
key_check() {  # key_check <поставщик> <ключ> → ok / bad / net (список моделей бесплатен у всех)
  local cfg code
  case "$1" in   # хосты встроены (methodology/llm_providers.json), пользовательского адреса нет
    anthropic) cfg='header = "x-api-key: %s"\nheader = "anthropic-version: 2023-06-01"\nurl = "https://api.anthropic.com/v1/models"\n' ;;
    openai) cfg='header = "Authorization: Bearer %s"\nurl = "https://api.openai.com/v1/models"\n' ;;
    gemini) cfg='header = "x-goog-api-key: %s"\nurl = "https://generativelanguage.googleapis.com/v1beta/models"\n' ;;
  esac
  # shellcheck disable=SC2059  # формат — константа из case выше, ключ — аргумент
  code="$(printf "$cfg" "$2" | curl -q -s -o /dev/null -w '%{http_code}' --max-time 20 -K - 2>/dev/null || true)"
  case "$code" in 200) echo ok ;; 400|401|403) echo bad ;; *) echo net ;; esac
}
keep_unverified() {  # сеть подвела, а не ключ: сохранить без проверки?
  [ "$INTERACTIVE" = 1 ] || return 1
  printf '%s [y/N]: ' "$(say "Не удалось проверить — сохранить без проверки?" "Could not check — save without checking?")" >&2
  local a; read -r a
  case "$a" in y|Y|д|Д) return 0 ;; *) return 1 ;; esac
}

while ! has_secret telegram_token; do
  say "Токен бота: @BotFather → /newbot (урок, шаг 4)." "Bot token: @BotFather → /newbot (tutorial, step 4)."
  t="$(ask_secret "$ENV_TG" "Токен бота (не будет виден)" "Bot token (input hidden)" 1)"; ENV_TG=""
  if [ "$VERIFY" = 1 ]; then
    if ! printf '%s' "$t" | grep -Eq '^[0-9]+:[A-Za-z0-9_-]{20,}$'; then
      warn "это не похоже на токен (цифры:буквы)" "this does not look like a token (digits:letters)"
      [ "$INTERACTIVE" = 1 ] && continue; exit 1
    fi
    tg_check "$t"
    case "$TG_STATUS" in
      ok) ok "бот @${TG_NAME}" "bot @${TG_NAME}" ;;
      bad) warn "Telegram не принял токен" "Telegram rejected the token"; [ "$INTERACTIVE" = 1 ] && continue; exit 1 ;;
      *) warn "Telegram недоступен — токен не проверен" "Telegram unreachable — token not checked"
         keep_unverified || { [ "$INTERACTIVE" = 1 ] && continue; exit 1; } ;;
    esac
  fi
  put_secret telegram_token "$t"
done
while ! has_secret telegram_chat_id; do
  say "Ваш числовой id: напишите @userinfobot." "Your numeric id: message @userinfobot."
  c="$(ask_secret "$ENV_ID" "Ваш id" "Your id" 0)"; ENV_ID=""
  if ! printf '%s' "$c" | grep -Eq '^-?[0-9]+$'; then
    warn "id — только цифры" "the id is digits only"
    [ "$INTERACTIVE" = 1 ] && continue; exit 1
  fi
  put_secret telegram_chat_id "$c"
done
case "$PROVIDER" in
  anthropic) KF=anthropic_key; KV="$ENV_AK"; KN="Anthropic"
             KH_RU="Ключ Anthropic: console.anthropic.com → API Keys (и пополните баланс)."
             KH_EN="Anthropic key: console.anthropic.com → API Keys (and top up the balance)." ;;
  openai)    KF=openai_key; KV="$ENV_LK"; KN="OpenAI"
             KH_RU="Ключ OpenAI: platform.openai.com → API keys (и пополните баланс)."
             KH_EN="OpenAI key: platform.openai.com → API keys (and top up the balance)." ;;
  gemini)    KF=gemini_key; KV="$ENV_LK"; KN="Gemini"
             KH_RU="Ключ Gemini: aistudio.google.com → Get API key."
             KH_EN="Gemini key: aistudio.google.com → Get API key." ;;
esac
ENV_AK=""; ENV_LK=""
while ! has_secret "$KF"; do
  say "$KH_RU" "$KH_EN"
  k="$(ask_secret "$KV" "Ключ ${KN} (не будет виден)" "${KN} key (input hidden)" 1)"; KV=""
  if [ "$VERIFY" = 1 ]; then
    case "$(key_check "$PROVIDER" "$k")" in
      ok) ok "ключ ${KN} принят (баланс так не проверить — пополните его)" "${KN} key accepted (the balance cannot be checked this way — top it up)" ;;
      bad) warn "${KN} не принял ключ" "${KN} rejected the key"; [ "$INTERACTIVE" = 1 ] && continue; exit 1 ;;
      *) warn "${KN} недоступен — ключ не проверен" "${KN} unreachable — key not checked"
         keep_unverified || { [ "$INTERACTIVE" = 1 ] && continue; exit 1; } ;;
    esac
  fi
  put_secret "$KF" "$k"
done
t=""; k=""

# Права ключей приводятся всегда, а не только у новых файлов: каталог 700, файлы 600, владелец —
# пользователь контейнера (1000) там, где он отличается от вашего. Не вышло — установка стоп.
if [ "$NEED_CHOWN" = 1 ]; then
  say "Отдаю ключи пользователю контейнера (1000)…" "Handing the keys to the container user (1000)…"
  as_priv chown -R 1000:1000 "$SEC" || die "Не удалось сменить владельца ${SEC}." "Could not change the owner of ${SEC}."
fi
{ as_priv chmod 700 "$SEC" && as_priv find "$SEC" -type f -exec chmod 600 {} +; } \
  || die "Не удалось выставить права ключей в ${SEC}." "Could not set permissions on the keys in ${SEC}."
ok "ключи в ${SEC} (только для владельца)" "keys in ${SEC} (owner-only)"

# ── 4. Запуск ────────────────────────────────────────────────────────────────────────────────
say "Запускаю (первый раз скачивается образ — несколько минут)…" "Starting (the first run downloads the image — a few minutes)…"
docker compose up -d
dash_ok() { curl -fs -o /dev/null --max-time 5 http://127.0.0.1:8001/; }
for _ in $(seq 60); do dash_ok && break; sleep 5; done
# Пульс служб: первая строка — метки неотмечающихся служб, вторая — их описание целиком.
labels="?"; down="?"
for _ in $(seq 36); do
  out="$(docker compose exec -T cron python3 -c "import daemon_liveness as d; x=list(map(str, d.find_down_daemons())); print(' '.join(s.split()[0] for s in x)); print('; '.join(x))" 2>/dev/null || printf '?\n?')"
  labels="$(printf '%s\n' "$out" | sed -n 1p)"; down="$(printf '%s\n' "$out" | sed -n 2p)"
  [ -z "$labels" ] && break
  sleep 5
done
bad_token=0
docker compose exec -T cron sh -c 'grep -q InvalidToken /app/logs/bot_err.log' 2>/dev/null && bad_token=1

# Не-Anthropic: работают только роли, чьи модели прошли допуск (таблица выпуска в образе);
# остальные функции отказывают громко, а не отвечают непроверенной моделью.
if [ "$PROVIDER" != anthropic ]; then
  roles="$(docker compose exec -T cron python3 -c "import hai_core, llm_client as c; p = c.provider(); print(' '.join(r for r in ('opus', 'sonnet', 'haiku', 'haiku_pinned') if hai_core.admitted_models(p, r)) or '-')" 2>/dev/null || echo '?')"
  warn "${PROVIDER}: допущены роли моделей: ${roles} — остальное не работает (docs/how-to/llm_provider.md)" \
       "${PROVIDER}: admitted model roles: ${roles} — everything else is off (docs/how-to/llm_provider.md)"
fi

# Итог считается в конце, по свежему состоянию.
BROKEN=0
if dash_ok; then ok "дашборд: http://127.0.0.1:8001" "dashboard: http://127.0.0.1:8001"
else BROKEN=1; warn "дашборд не отвечает — docker compose ps" "the dashboard does not answer — docker compose ps"; fi
if [ -z "$labels" ]; then ok "все службы отмечаются" "all services report in"
else
  warn "не отмечаются: ${down}" "not reporting: ${down}"
  if [ "$bad_token" = 1 ]; then
    warn "бот: Telegram не принял токен — исправьте ${SEC}/telegram_token и docker compose restart bot" \
         "bot: Telegram rejected the token — fix ${SEC}/telegram_token and docker compose restart bot"
  fi
  # Единственное исключение — CI с заведомо поддельными ключами: там бот обязан упасть на токене.
  if ! { [ "$CI_FAKE" = 1 ] && [ "$labels" = com.larry.health.bot ] && [ "$bad_token" = 1 ]; }; then BROKEN=1; fi
fi

# ── 5. Автозапуск ────────────────────────────────────────────────────────────────────────────
if [ "$OS" = Darwin ]; then
  ctx="$(docker context show 2>/dev/null || true)"
  if [ "$ctx" = colima-health ]; then
    label=com.larry.health.colima
    agent="${HOME}/Library/LaunchAgents/${label}.plist"
    if [ -z "$DIST" ] && ! launchctl print "gui/$(id -u)/${label}" >/dev/null 2>&1; then
      mkdir -p "${HOME}/Library/LaunchAgents"
      if curl -fsSL "${RELEASE_URL}/${label}.plist" | sed "s#__HOME__#${HOME}#g" > "${agent}.part" \
         && plutil -lint "${agent}.part" >/dev/null 2>&1; then
        mv "${agent}.part" "$agent"
        launchctl bootstrap "gui/$(id -u)" "$agent" 2>/dev/null || true
      else
        rm -f "${agent}.part"
      fi
    fi
    if launchctl print "gui/$(id -u)/${label}" >/dev/null 2>&1; then ok "автозапуск Colima при входе" "Colima starts at login"
    else warn "автозапуск Colima не включился — урок, шаг 7" "Colima autostart is not on — tutorial, step 7"; fi
  elif printf '%s' "$ctx" | grep -q '^colima'; then
    warn "Colima с профилем «${ctx#colima-}»: готовый агент рассчитан на профиль health — автозапуск включите сами (урок, шаг 7)" \
         "Colima profile '${ctx#colima-}': the ready agent is for the health profile — set up autostart yourself (tutorial, step 7)"
  else
    warn "автозапуск: включите в Докере «запускать при входе» (Docker Desktop: Settings → General → Start Docker Desktop when you sign in)" \
         "autostart: turn on 'start at login' in your Docker app (Docker Desktop: Settings → General → Start Docker Desktop when you sign in)"
  fi
  warn "Мак не должен засыпать: sudo pmset -a sleep 0" "the Mac must not sleep: sudo pmset -a sleep 0"
elif [ "$WSL" = 1 ]; then
  warn "автозапуск после перезагрузки Windows — урок, раздел «Windows», шаг Г" \
       "autostart after a Windows reboot — tutorial, section 'Windows', step D"
elif command -v systemctl >/dev/null 2>&1; then
  if systemctl is-enabled docker >/dev/null 2>&1; then ok "Докер стартует при загрузке" "Docker starts at boot"
  else warn "Докер не стартует при загрузке: sudo systemctl enable docker" "Docker does not start at boot: sudo systemctl enable docker"; fi
fi

if [ -n "$SELF" ] && ! [ "$SELF" -ef "${DIR}/install.sh" ]; then cp "$SELF" "${DIR}/install.sh.part" && mv "${DIR}/install.sh.part" "${DIR}/install.sh"; fi
echo
if [ "$BROKEN" = 1 ]; then
  say "Установка не завершена: система запущена, но не всё живо (см. выше). Причина — в журнале бота: cd ${DIR} && docker compose exec -T cron tail -n 20 /app/logs/bot_err.log" \
      "Installation not finished: the system started, but not everything is alive (see above). The cause is in the bot log: cd ${DIR} && docker compose exec -T cron tail -n 20 /app/logs/bot_err.log"
  exit 1
fi
say "Готово. Напишите своему боту в Telegram /start — он проведёт знакомство (урок, шаг 8)." \
    "Done. Send /start to your bot in Telegram — it will walk you through onboarding (tutorial, step 8)."
say "Обновить потом — тот же скрипт: cd ${DIR} && bash install.sh" "To update later, run the same script: cd ${DIR} && bash install.sh"
