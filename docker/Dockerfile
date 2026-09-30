# syntax=docker/dockerfile:1
# Образ Health OS (нить docker-install, этап 5). Сборка из корня репозитория:
#   python3 scripts/install.py --docker --tz <пояс>     # рендер .dockerignore и расписания
#   docker buildx build -f docker/Dockerfile -t health-os:local .
# Образ несёт ровно публичную зону (.dockerignore из publication_zones.yaml). Данные, секреты и
# приватная методология владельца — тома, не слои.

# ── Сборка колёс: timezonefinder на linux/arm64 колеса не имеет и собирается из исходников ──
FROM python:3.11.15-slim AS wheels
RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.lock /tmp/requirements.lock
RUN pip wheel --no-cache-dir -w /wheels -r /tmp/requirements.lock

# ── Рабочий образ ─────────────────────────────────────────────────────────────────────────
FROM python:3.11.15-slim
ARG TARGETARCH
# supercronic v0.2.49: SHA-1 совпали с опубликованными в релизе (сверено 29.09 скачиванием),
# SHA-256 посчитаны от тех же бинарей — сверяем более сильным.
ARG SUPERCRONIC_VERSION=v0.2.49
ARG SUPERCRONIC_SHA256_amd64=a53ae236602c7338aba3fbaff40bda6300eae3b9fedb8261eb06cfe3724430c1
ARG SUPERCRONIC_SHA256_arm64=02aa0cb229ba09050cba6638059dadb9eedc2276632ea43d6a57a2f8c1629dd5

# Языки OCR — аргумент сборки, не код (решение владельца 29.09): установка тенанта с другими
# языками документов собирает образ с `--build-arg OCR_LANGS="eng rus <код>"` и пишет тот же
# список в system_config `ocr.languages`. Язык без пакета config_db.ocr_languages отбросит громко.
ARG OCR_LANGS="eng rus"
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr $(for l in $OCR_LANGS; do printf 'tesseract-ocr-%s ' "$l"; done) \
        sqlite3 tzdata ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

RUN set -eu; \
    case "$TARGETARCH" in \
      amd64) sha="$SUPERCRONIC_SHA256_amd64" ;; \
      arm64) sha="$SUPERCRONIC_SHA256_arm64" ;; \
      *) echo "архитектура $TARGETARCH не поддержана" >&2; exit 1 ;; \
    esac; \
    curl -fsSLo /usr/local/bin/supercronic \
      "https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-${TARGETARCH}"; \
    echo "$sha  /usr/local/bin/supercronic" | sha256sum -c -; \
    chmod +x /usr/local/bin/supercronic

COPY --from=wheels /wheels /wheels
COPY requirements.lock /tmp/requirements.lock
RUN pip install --no-cache-dir --no-index -f /wheels -r /tmp/requirements.lock \
    && pip check \
    && rm -rf /wheels

# Пользователь и раскладка повторяют нативные: HOME=/home/health, данные ~/health,
# секреты ~/.health_secrets — личность данных и предохранитель канона считаются от них (этап 4).
RUN useradd --create-home --home-dir /home/health --uid 1000 health
WORKDIR /app
COPY --chown=health:health . /app
# WORKDIR создаёт /app от root: без передачи владения рендер расписания при старте падал
# на build/ (поймано первым живым прогоном образа 29.09 — планировщик не стартовал бы).
RUN mkdir -p /app/build /app/logs && chown health:health /app /app/build /app/logs
USER health
# Шаблоны установки (private/infra.yaml, базовая методология) — при сборке, с основной машиной
# health-os (hostname из compose). Каталоги данных и секретов здесь — временные: настоящие
# живут в томах, база создаётся при старте (init_db идемпотентен).
RUN HEALTH_PRIMARY_HOST=health-os python3 scripts/install.py --apply --non-interactive --files-only \
        --data-dir /tmp/build-init/health --secrets-dir /tmp/build-init/secrets \
    && rm -rf /tmp/build-init
ENV HOME=/home/health \
    HEALTH_RUNTIME=container \
    PYTHONUNBUFFERED=1
