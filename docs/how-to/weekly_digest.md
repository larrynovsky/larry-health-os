[English](weekly_digest.en.md) · **Русский**

# Еженедельный дайджест изменений: перегенерировать, проверить, понять «почему не пришёл»

> **Тип документа:** How-to (Diátaxis). Зачем так устроено: [docs/explanation/multitenancy.md](../explanation/multitenancy.md) (абзац «Один текст на всех»). План нити — в закрытой части проекта.

Кто что делает: `launchd com.larry.health.weekly-digest` (Studio, **сб 22:00**, окружение владельца)
→ `weekly_digest.py` пишет `outputs/weekly_digest/<ISO-неделя>.json` → каждый бот раз в час
(`jobs/scheduled.weekly_digest_outbox`) пишет свой `<неделя>.gate.<тенант>.json` и **вс ≥ 09:00**
местного времени шлёт текст, если все вердикты `pass` → метка `system_config.weekly_digest.last_sent_week`
→ понедельник: `integrity_tests.check_weekly_digest_delivered` краснеет, если у кого-то метки нет.

## Посмотреть текст, не отправляя (dry-run)

На MacBook или Studio, из каталога репо:

```
python3.11 weekly_digest.py --dry-run --week 2026-W36
```

Печатает сводку (число коммитов, нити, вердикт гейта) и текст. Без `--week` — текущая ISO-неделя.
Код выхода 2 = гейт заблокировал; в `_rejected_preview` — отклонённый текст.

## Перегенерировать неделю

На Studio (только там есть БД для per-tenant словаря):

```
python3.11 weekly_digest.py --week 2026-W36
```

Файл перезаписывается атомарно. Вердикт-файлы тенантов **не** перезаписываются — удали
`outputs/weekly_digest/2026-W36.gate.*.json`, иначе боты сочтут старый вердикт действительным.
Если неделя уже помечена доставленной — повторно бот не отправит (`due` → `done`).

## Почему не пришёл — четыре причины

0. **Бот не видит папку генератора.** Генератор живёт на хосте (ему нужен git), бот владельца с
   30.09 — в контейнере. Папка `outputs/weekly_digest` попадает туда томом из
   `scripts/install.py --owner-override`. Проверка:
   `docker --context colima-health exec health-bot-1 ls /app/outputs/weekly_digest` — пусто или
   «No such file» при файле на хосте = тома нет: перегенерировать override и `scripts/deploy_container.sh`.
   Так 04.10 владельцу не пришла неделя W40 (нить digest-container).
1. **Нет файла `<неделя>.json`** — генератор не бежал. `launchctl list | grep weekly-digest`,
   лог `logs/weekly_digest.log` / `_err.log`. С 05.10 бот, видящий папку, зовёт оператора в вс после 09:00.
2. **`text: null`** — гейт заблокировал оба варианта. В `gate.kind`: `lexicon` (термин/число/дата —
   `hits_class` даёт класс), `judge` (факт о человеке), `fidelity` (абзац искажает нить), `guard`
   (гард секретов `llm_client` или API). Оператор получил `notify_operator` сразу из генератора (сб 22:xx) и с первого тика бота.
3. **Нет `<неделя>.gate.<тенант>.json`** одного из тенантов — его бот не бежал или его словарь
   заблокировал (`verdict: blocked`). Доставка ждёт всех; алерт оператору — на первом тике после 09:00 вс.

## Поправить тон или запретить слово

Тон — `data/prompts/weekly_digest.md` (образцы интонации внутри). Запрещённые технические слова —
`data/prompts/weekly_digest_banned_terms.txt` (основа + ≤2 буквы окончания, целым словом).
Витрины (какие файлы считаются «видимыми» тенанту) — `system_config['digest.visible_hints']`
(value_json, список подстрок путей); при отсутствии — код-дефолт в модуле.
Крупность сюжета: нить ≥ 3 коммитов, top-3 обязательны (`MAJOR_MIN_COMMITS`) — решает код.

## Установить/обновить расписание на Studio

```
ssh studio 'cp ~/health_scripts/launchd/com.larry.health.weekly-digest.plist ~/Library/LaunchAgents/ && launchctl unload ~/Library/LaunchAgents/com.larry.health.weekly-digest.plist 2>/dev/null; launchctl load ~/Library/LaunchAgents/com.larry.health.weekly-digest.plist'
```

Расписание попадёт в ARCH_SNAPSHOT через `gen_schedule.py`.
