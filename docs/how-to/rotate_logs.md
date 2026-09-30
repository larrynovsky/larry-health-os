[English](rotate_logs.en.md) · **Русский**

# Ротация логов

**Замер 01.09.2026:** ротации не было ни у одного лога, ни на одной машине —
`health_import_poll.log` 204 МБ и `health_bot.log` 71 МБ на Studio, `code_watcher.log` 46/31 МБ
на Studio/MacBook. Суммарно ~470 МБ, растут с мая. Конфиг ротации:
`launchd/health-logs.newsyslog.conf` (конфиг машины владельца; публичная установка получает шаблон).

**Первый боевой прогон 01.09 21:03:** Studio — 6 файлов, 366 МБ → 11,7 МБ архивов, у всех
`inode_kept: true`; MacBook — 3 файла, 81 МБ. Полевая проверка того, что писатели не осиротели:
через минуту после обрезки `health_import_poll.log` снова 7,6 КБ, `health_bot.log` — 660 Б, то
есть живые процессы с открытым дескриптором продолжили писать В ТОТ ЖЕ файл.

## Почему newsyslog, а не RotatingFileHandler

Логи пишутся **двумя** способами, и второй не чинится из Python:

| способ | кто | пример |
|---|---|---|
| `logging.FileHandler` | `bot/main`, `import_oura`, `import_apple_health`, `reminders_sync`, `vcf_import_pipeline` | `~/health_bot.log` |
| редирект launchd (`StandardOutPath` / `StandardErrorPath`) | все `.plist` | `logs/bot_err.log`, `logs/dashboard.err.log` |

`RotatingFileHandler` покрыл бы только первую строку: у файлов из второй дескриптор держит
launchd, и ротация изнутри процесса невозможна. `newsyslog` ротирует **файл**, а не писателя —
покрывает оба способа и не требует правок кода.

## Что установлено

**Механизм — свой, без root:** `log_rotate.py` + агент пользователя
`launchd/com.larry.health.logrotate.plist` (раз в 30 мин). Ставится **без sudo**:

```sh
cp ~/health_scripts/launchd/com.larry.health.logrotate.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.larry.health.logrotate.plist
launchctl kickstart -k gui/$(id -u)/com.larry.health.logrotate     # прогнать сейчас
```

Установлен 2026-09-01 агентом на **обе машины** (Studio и MacBook — логи у каждой свои).
Решение владельца: «поставь без меня»; штатный `newsyslog` требует записи в `/etc/newsyslog.d/`
и запускается от root, а `sudo` закрыт политикой моста (`blockedCommands` Desktop Commander) —
снимать эту защиту ради одной установки агент не стал.

**Ключевая механика — copytruncate, а не переименование.** Логи пишут два разных писателя, и
оба держат ОТКРЫТЫЙ дескриптор: переименуй файл — писатель продолжит писать в переименованный
inode, а живой лог замолчит навсегда. Поэтому содержимое копируется в `.0.gz`, а файл
обрезается на месте (inode сохраняется). Оракул на это —
`tests/unit/test_log_rotate.py::test_open_writer_keeps_writing_after_rotation`.

Те же ассерты стоят в `__main__` и исполняются **на каждом запуске агента** (раз в 30 мин):
если copytruncate когда-нибудь сломается, `logs/logrotate_err.log` покраснеет `AssertionError`,
а не промолчит. Строка `selftest ok` в конце `logs/logrotate.log` — признак, что проверка прошла.

## Альтернатива: newsyslog (если однажды понадобится)

Конфиг `launchd/health-logs.newsyslog.conf` — **общий дом списка логов**: его читает и
`log_rotate.py`. Штатный ротатор ставится так (нужен sudo, шаг владельца):

```sh
sudo cp ~/health_scripts/launchd/health-logs.newsyslog.conf /etc/newsyslog.d/health.conf
sudo newsyslog -nv | grep health
```

**Не включать оба одновременно** — два исполнителя одного списка будут ротировать наперегонки.
Включаешь newsyslog → выгрузи агента: `launchctl bootout gui/$(id -u)/com.larry.health.logrotate`.

## Оракул: сработало или нет

```sh
ls -laS ~/health_scripts/logs/*.log ~/health*.log | head -5   # ни одного файла > ~5 МБ
ls ~/*.gz ~/health_scripts/logs/*.gz 2>/dev/null | head       # появились архивы .0.gz
launchctl print gui/$(id -u)/com.larry.health.logrotate | grep -E "state|last exit"
tail -3 ~/health_scripts/logs/logrotate.log                   # что ротировано в прошлый раз
```

Сухой прогон в любой момент, ничего не меняет:
`/opt/homebrew/bin/python3.11 ~/health_scripts/log_rotate.py --dry-run`

## Новый лог

Список путей — в `launchd/health-logs.newsyslog.conf` (формат newsyslog, общий дом для обоих
механизмов). `newsyslog` на macOS не понимает `*.log` в пути (ни один из шести системных конфигов
`/etc/newsyslog.d/` glob не использует, в man он не документирован), поэтому пути перечислены
явно. Завёл модуль, который пишет свой лог, или добавил `StandardErrorPath` в новый `.plist` —
допиши строку в конфиг и перекопируй.

## Датчик: лог растёт, а в конфиге его нет

`integrity_tests` → «Логи вне конфига ротации» (`check_logs_all_listed`), считает
`log_rotate.unlisted_logs()`:

* **WARN от 1 МБ** — виден в прогоне integrity. Замер 01.09: вне конфига 160 файлов на двух
  машинах, ни одного больше 1 МБ, — порог даёт сигнал, а не фон.
* **FAIL, когда файл перерос порог ротации** (тот, что в самом конфиге, 5 МБ): такой лог не
  подрежет никто. FAIL выбран сознательно — падения `integrity_latest.json` читает `night_cycle`
  и доводит до владельца, WARN остаётся в прогоне.

Лечение всегда одно: строка в `launchd/health-logs.newsyslog.conf`.

Посмотреть вручную:
`/opt/homebrew/bin/python3.11 -c "import log_rotate; print(log_rotate.unlisted_logs(min_mb=0.3))"`

### Датчик на сам агент

`integrity_tests` → «Ротация логов жива и чиста» (`check_logrotate_liveness`). Нужен потому,
что предыдущий датчик к смерти агента **слеп**: логи растут, но все они В конфиге — находок ноль,
тишина выглядит как порядок. Ровно так 470 МБ и накопились.

Смотрит две разные вещи (§14: heartbeat доказывает «запустился», не «работает корректно»):
свежесть квитанции `logs/logrotate.log` (FAIL от 24ч, WARN от 2ч при интервале 30 мин) и её
хвост `selftest ok` — прошла ли в прогоне самопроверка copytruncate. Второе краснеет, когда
агент жив, но сломан. Задача внесена в `producer_registry.MONITORED` — ратчет покрытия поймал
её в тот же вечер, когда агент встал.

Квитанция читается **локальная**: integrity бежит на Studio, значит агент на MacBook покрыт
своим прогоном integrity там, а не этим.

**Живость датчика (§14):** пустой или неразобранный конфиг — не «всё чисто», а отказ:
`_parse_conf` кидает `ValueError`, и ротация, и датчик падают громко. Без этого
молчание сломанного датчика выглядело бы как порядок.

**Чего датчик НЕ видит:** лог в каталоге, которого нет в конфиге, и файл без расширения `.log`.
Каталоги вычисляются из самого конфига (родители его путей) — второй список каталогов был бы
вторым домом и разошёлся бы с первым. Цена названа, а не забыта.
