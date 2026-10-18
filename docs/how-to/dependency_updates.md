[English](dependency_updates.en.md) · **Русский**

# Как обновлять зависимости (SEC-19, 2026-07-06)

Рецепт для системного python3.11 на Studio (среда второго тенанта) и для образа контейнера
(среда владельца, раздел ниже).
Канон версий: `requirements.txt` (в git). Аудит уязвимостей: weekly
`com.larry.health.pipaudit` → `logs/pip_audit_latest.json` → nightly
`security_sensors` → triage → Telegram.

## Когда обновлять

- Пришёл варн `security:pip_audit` с advisory-id — обновляй уязвимый пакет.
- Квартальный аудит SECURITY.md — плановая ревизия отставших пакетов.
- В остальное время — не обновляй: свежесть сама по себе не ценность.

## Шаги (по одному пакету за заход)

1. **Cooldown**: целевой версии ≥3 дней от даты релиза на PyPI (supply-chain
   защита от свежескомпрометированных релизов). Исключение — security-фикс,
   закрывающий активный advisory: ставь сразу.
2. На Studio: `ssh <studio_ssh> "/opt/homebrew/bin/python3.11 -m pip install 'pkg==X.Y.Z' --break-system-packages"`.
3. Прогнать регрессию: `bash scripts/test_on_studio.sh` (или дождаться nightly).
4. Обновить lock с MacBook (single-writer: файл в git правится здесь):
   `{ head -3 requirements.txt; ssh <studio_ssh> "/opt/homebrew/bin/python3.11 -m pip freeze"; } > requirements.txt`
   — поправив дату во 2-й строке заголовка.
5. Внепланово перегенерить аудит: `ssh <studio_ssh> "cd ~/health_scripts && /opt/homebrew/bin/python3.11 scripts/pip_audit_check.py"` — варн должен погаснуть.
6. Commit lock (+ при advisory — строчка в SECURITY.md, если решение нетривиальное).

## Образ контейнера (владелец живёт в контейнере с 30.09)

Пакеты образа ставятся из того же `requirements.txt`, поэтому шаги выше обновляют и образ:
следующий деплой соберёт его с новой версией. Отдельно от замка в образе живут **pip и
setuptools** самого Python: их версии — `ARG PIP_VERSION` и `ARG SETUPTOOLS_VERSION` в
`docker/Dockerfile`, по умолчанию базовый `python:3.11-slim` несёт старые.

1. Проверить пакет до живой среды — в одноразовом контейнере текущего образа:
   `ssh <studio_ssh> "DOCKER_CONTEXT=colima-health docker run --rm --entrypoint sh health-os:local -c 'pip install -q --no-deps --target /tmp/x pkg==X.Y.Z && PYTHONPATH=/tmp/x python3 -c \"import pkg\"'"`
2. Обновить замок по шагам выше (нативная среда Studio — среда второго тенанта).
3. pip/setuptools образа — поправить `ARG` в `docker/Dockerfile`, cooldown тот же.
4. Собрать образ под отдельным тегом из рабочей копии, не трогая `health-os:local`:
   `git archive $(git stash create) | ssh <studio_ssh> 'DOCKER_CONTEXT=colima-health DOCKER_BUILDKIT=0 docker build -q -f docker/Dockerfile --build-arg TARGETARCH=arm64 -t health-os:depcheck -'`
5. Аудит внутри собранного образа: `docker run --rm --entrypoint python3 health-os:depcheck -m pip_audit`
   — уязвимостей быть не должно (кроме тех, что в `_PIP_AUDIT_ALLOW`).
6. Коммит; деплой соберёт рабочий образ сам. После деплоя — тот же аудит в `health-cron-1`.

## Advisory без фикса

Если у уязвимости нет исправленной версии или она неприменима (функция не
используется, поверхность недостижима) — id в `_PIP_AUDIT_ALLOW`
(`security_sensors.py`) с комментарием-обоснованием. Пустой allowlist —
нормальное состояние; каждый пункт ревизуется квартальным чеклистом.

## Восстановление окружения с нуля

`/opt/homebrew/bin/python3.11 -m pip install -r requirements.txt --break-system-packages`

## Чего НЕ делать

- Не обновлять «всё сразу» (`pip install -U ...` списком) — при регрессии
  бинарный поиск виновника невозможен.
- Не править версии только в lock без установки на Studio — lock описывает
  фактическое окружение, а не желаемое (иначе он врёт, см.
  feedback_stale_intermediate_layer).
- Не запускать pip-audit из nightly integrity — сеть в 07:50 гейтит утренний
  отчёт; сеть только в weekly-джобе.
