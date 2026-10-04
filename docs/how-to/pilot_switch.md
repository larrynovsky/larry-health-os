[English](pilot_switch.en.md) · **Русский**

# Как перевести тенанта из нативной установки в контейнер и вернуть обратно

Рецепт для основной машины, где тенант живёт нативно (launchd), а Docker поднят в своём профиле
Colima. Запись базы тенанта переезжает целиком: натив и контейнер никогда не пишут одновременно.
Почему так устроено — план нити docker-install (закрытая часть репозитория), этап 11.
Установка Docker и профиля — [установка в Докере](install_docker.md).

Все команды — на основной машине, из `~/health_scripts`. Переменные, которые меняются от тенанта
к тенанту, задайте один раз:

```sh
export PATH=/opt/homebrew/bin:$PATH
T=~/health                      # корень данных тенанта (у второго человека — ~/health_<имя>)
S=~/.health_secrets             # каталог секретов тенанта (у второго — ~/.health_secrets_<имя>)
CTX=colima-health               # контекст Docker своего профиля Colima — не общий
PROJECT=health                  # имя проекта compose; на репетиции — health-rehearsal
TZ_HOST=$(readlink /etc/localtime | sed 's#.*/zoneinfo/##')
PY=/opt/homebrew/bin/python3.11
```

## Перед началом

- Ночная проверка зелёная на коммите, который сейчас на машине (`git log -1`).
- Образ этого коммита есть: `docker --context $CTX images health-os`. Нет — `bash scripts/deploy_container.sh`
  (только сборка и запуск; до шага 3 он поднимет пустую базу — поэтому до переключения не запускайте).
- Решение владельца на переключение получено. Шаги 2–4 делаются подряд, при нём.
- У профиля Colima задан DNS: в `~/.colima/<профиль>/colima.yaml` — `network.dns` и `docker: {"dns": [...]}`
  (например, адрес вашего роутера и `1.1.1.1`). Без него после перезагрузки машины контейнеры не находили
  адреса в сети (замер 30.09 12:53; правка другой сессией и `colima restart`). Проверка:
  `docker --context $CTX exec <контейнер> python3 -c "import socket; print(socket.gethostbyname('api.telegram.org'))"`.
- Перед переключением прогоните утренний конвейер внутри контейнера тенанта на копии данных — `run_checks.sh --scheduled`
  и полный набор тестов — и разберите каждый красный: «среда контейнера» или «станок разработчика» (метка
  `host_only`). Урок C-106: первое утро пилота владельца дало 0 тестов, 5 красных монитора и шум в журнале бота.

## 1. Подготовить файлы (живое не трогается)

```sh
$PY scripts/install.py --docker --tz "$TZ_HOST"
$PY scripts/install.py --owner-override --tz "$TZ_HOST"
$PY scripts/install.py --pilot-split          # какие службы выгрузятся, какие останутся
```

## 2. Остановить нативные службы тенанта

```sh
for l in $($PY scripts/install.py --pilot-split | awk '$1=="bootout"{print $2}'); do
  launchctl bootout gui/$(id -u)/$l 2>/dev/null; launchctl disable gui/$(id -u)/$l; done
launchctl list | grep -F -f <($PY scripts/install.py --pilot-split | awk '$1=="bootout"{print $2}')   # пусто
launchctl list | grep -c '\.partner$'                                                               # как было
```

Плисты не удаляются: они нужны для отката. `disable` обязателен: без него `bootout` живёт только до перезагрузки, и launchd загрузит службы снова (записка finish-prep 30.09); проверка — `launchctl print-disabled gui/$(id -u)`. Службе нужно время на штатное завершение — список пустеет не сразу (замер 30.09: бот и вотчер дольше 3 с); проверяйте повтором, а не одной командой.

## 3. Перенести базу

```sh
mkdir -p ~/health_switch && $PY -c "import sqlite3,sys; s=sqlite3.connect(sys.argv[1]); d=sqlite3.connect(sys.argv[2]); s.backup(d); d.close(); print('снимок готов')" \
  "$T/data/health.db" ~/health_switch/health.db
(cd build/docker && docker --context $CTX compose -p $PROJECT create)
docker --context $CTX run --rm -v ${PROJECT}_health-home:/home/health -v ~/health_switch:/in:ro health-os:local \
  sh -c 'mkdir -p /home/health/health/data && cp /in/health.db /home/health/health/data/health.db && sha256sum /home/health/health/data/health.db'
shasum -a 256 ~/health_switch/health.db          # тот же хеш
echo container > "$T/RUNTIME"                     # метку читают деплой-хук и нативный бэкап
chmod 400 "$T/data/health.db" && chmod a-w "$T/data"
```

Права — `400`, не `444`: ремонт прав монитора (`security:db_perms`, любой тенант на хосте) считает нарушением всё, что читают группа и прочие, и возвращает `600` — запрет записи снимается молча (замер 30.09 12:52 после перезагрузки: монитор партнёра снял его, и `code-watcher` записал сиды в замороженную копию; с тех пор `code-watcher` в списке выгружаемых).

## 4. Запустить

```sh
(cd build/docker && docker --context $CTX compose -p $PROJECT up -d)
docker --context $CTX compose -p $PROJECT ps     # все Up; cron — healthy
```

## 5. Открыть входы только в tailnet

```sh
TS=/Applications/Tailscale.app/Contents/MacOS/Tailscale
$TS serve --bg --tcp 8001 tcp://127.0.0.1:8001    # приём Apple Health и дашборд — прежний адрес
$TS serve status
```

CalDAV (если тенант пользуется Напоминаниями): создайте `~/.health_caldav/config` и `users` по образцу
пробы этапа 3, пароль — новый, в `users` — его bcrypt-хеш (`htpasswd_encryption = bcrypt` в `config`), права 600; `$TS serve --bg --https=5232 http://127.0.0.1:5232`;
положите `caldav.json` ({"url", "username", "password"}) в `$S` (права 600); перенесите открытые задачи:

```sh
docker --context $CTX compose -p $PROJECT exec cron python3 -c "import tasks_db, task_agent as a; t=tasks_db.get_open_tasks(limit=100000); n=a.create_reminders_for_tasks(t); want=sum(x.get('type') not in a.REMINDER_TYPES_EXCLUDED for x in t); print(n, want); assert n==want"
```

## 6. Включить теневого сторожа

```sh
cp build/docker/host/com.larry.health.pilot-shadow.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.larry.health.pilot-shadow.plist
$PY pilot_shadow.py; tail -3 ~/Library/Logs/health-shadow.log      # «совпало»
```

Тот же сторож раз в час смотрит и за замороженной копией: права ровно 400, файл базы и журнала не менялся
после первого замера (`~/health_shadow/frozen_copy.json`), ни одна служба из списка выгрузки не загружена.
Любое из трёх — сообщение владельцу.

## 6а. Проверить бэкапы

Бэкап базы тенанта делает задача контейнера (03:00) в каталог хоста `~/container_backups/<тенант>` (700;
подключается поверх `<данные>/backups`, создаёт `scripts/deploy_container.sh`) — не в образ диска ВМ Colima,
где лежит сама база. После первой ночи: `ls -la ~/container_backups/health` — файл за сегодня есть,
`sqlite3 <файл> 'PRAGMA integrity_check'` — `ok`.

## 7. Первая проверка

Тенант пишет боту и присылает один документ; число строк событий или анализов выросло (замерьте до и после):
`docker --context $CTX compose -p $PROJECT exec cron python3 -c "import health_db as d; print(tuple(d.get_conn().execute('select (select count(*) from events), (select count(*) from lab_results)').fetchone()))"`.

## Откат

```sh
(cd build/docker && docker --context $CTX compose -p $PROJECT stop)
mkdir -p ~/health_rollback && docker --context $CTX run --rm -v ${PROJECT}_health-home:/home/health -v ~/health_rollback:/out health-os:local \
  python3 -c "import sqlite3; s=sqlite3.connect('/home/health/health/data/health.db'); d=sqlite3.connect('/out/health.db'); s.backup(d)"
launchctl bootout gui/$(id -u)/com.larry.health.pilot-shadow 2>/dev/null
$TS serve --tcp=8001 off
chmod u+w "$T/data" "$T/data/health.db" && mv "$T/data/health.db" "$T/data/health.db.frozen-$(date +%F)"
cp ~/health_rollback/health.db "$T/data/health.db" && rm -f "$T/RUNTIME"
rm -f "$S/caldav.json"                   # только если клали на шаге 5: Напоминания вернутся в iCloud
for l in $($PY scripts/install.py --pilot-split | awk '$1=="bootout"{print $2}'); do
  launchctl enable gui/$(id -u)/$l; launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/$l.plist 2>/dev/null; done
```

Если старые системные задачи в iCloud отмечали выполненными (решение владельца 30.09), снимите эти отметки ДО загрузки `reminders-sync`: нативная служба прочитает их как сделанное и закроет задачи в базе.
Учтите: нативный `reminders-sync` владельца с 08.08 по 30.09 не прочитал ящик ни разу (таймаут osascript
во всех 422 прогонах), так что после отката галочки на iPhone снова могут не доходить; с 30.09 такой сбой
пишется в журнал сбоев, и его видит ночной монитор.
`code-watcher` в списке выгрузки: он гоняет тесты на данных тенанта (`$HOME/health`), и после переезда
был вторым писателем замороженной копии (замер 30.09 12:52) — при откате включается вместе с остальными.

Проверка отката: `launchctl list | grep com.larry.health.bot` — загружен; бот отвечает; том контейнера
не удаляйте, пока откат не проверен (`compose down` без `-v`).
