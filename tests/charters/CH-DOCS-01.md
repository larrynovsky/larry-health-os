# CH-DOCS-01: квартальный аудит документационной инфраструктуры

**Тип:** Charter (RST), ручной процесс, не automated.
**Частота:** после инцидента с документацией / при большом doc-рефакторинге (решение 2026-07-02: расписание чартеров не ведётся, чартер = процедура постмортема).
**Время:** ~30 мин.
**Ответственный:** владелец.

## Назначение

Wave 3-DOC v2 ставит на ноги enforcement-цепочку (yaml SSOT → INV-DOC тесты → pre-commit hook → morning briefing → meta-test). Эта цепочка автоматически ловит **известные** классы drift'а. Однако:

- Появляются **новые** классы (например, BUG-PLIST-XML обнаружен через P5-1 при автогенерации, был бы пропущен без неё).
- Меняются **источники правды** (новые таблицы БД, новые plist'ы, переименованные модули) — yaml-инвентарь устаревает.
- **Сами тесты могут испортиться** молча (например, маркер xfail остался после фикса бага — тест становится false-positive).

Quarterly review закрывает это белое пятно. Не заменяет автоматику, а проверяет её живость.

## Чек-лист (по порядку, ~30 мин)

### 1. doc_inventory.yaml — актуальность классификации

- [ ] Есть ли новые `.md` в репо, не классифицированные ни в `live`, ни в `archive`? Запустить `pytest tests/integration/test_doc_inventory.py::test_every_md_classified` — должно зелёное.
- [ ] Прочитать `stop_words_in_live` — нет ли там устаревших паттернов (например, IP больше не используются)? Удалить лишнее.
- [ ] `silent_except_baseline` — сравнить с фактическим количеством. Если фактическое **намного меньше** — снизить baseline (форсирует не возвращать долг назад). Если **выше** на 1-2 — допустимо, кто-то добавил с legit `# silent-ok:`.

### 2. Pre-commit hook — реальность сходится с конфигом

- [ ] `ls -la ~/health_scripts/.git/hooks/pre-commit` — symlink на `scripts/git-hooks/pre-commit`?
- [ ] Сделать тестовый commit на ветке: добавить в LIVE-файл (tests/README.md) строку с любым stop-word'ом из `doc_inventory.yaml:stop_words_in_live`, попытаться закоммитить — должен заблокироваться с понятным сообщением.
- [ ] Откатить: `git checkout main && git branch -D test/charter-ci`.

### 3. GEN-блоки — отрабатывают на свежей FS

- [ ] `ssh <studio_ssh> /opt/homebrew/bin/python3.11 ~/health_scripts/gen_key_paths.py` — `up-to-date`?
- [ ] `ssh <studio_ssh> /opt/homebrew/bin/python3.11 ~/health_scripts/gen_schedule.py` — `up-to-date`?
- [ ] Если **не** up-to-date — значит появились/удалились пути или launchd-агенты, генератор регенерил блок. Закоммитить.
- [ ] Любые `⚠️ parse error` в выводе SCHEDULE — открыть проблемный plist и починить.

### 4. xfail-маркеры — не устарели ли

- [ ] `pytest --runxfail tests/unit/test_health_db_signatures_contract.py` — если xfail-помеченный тест **проходит** при принудительном запуске → баг починен, надо удалить из `KNOWN_REGRESSIONS`.
- [ ] Аналогично `tests/integration/test_uc_i_03_null_not_zero.py` и любые другие xfail.

### 5. Sanity — морnыный брифинг видит то же что мы

- [ ] Проверить вчерашний брифинг: `cat ~/health/reports/$(date -v-1d +%Y-%m-%d).md` — секция «Тесты» есть, цифры разумные.
- [ ] Если секция отсутствует или цифры «0 pass» — копать `morning_report.py:_test_summary` (см. BUG-MORNING-SILENT #85 как прецедент).

### 6. Memory ↔ реальность

- [ ] Прочитать `~/.../memory/feedback_health_scripts_design_principles.md` — действуют ли все 5 принципов? Нет ли новых, которые стоило бы добавить?

## Артефакт

Открыть `BACKLOG.md` → добавить запись:

```
## CH-DOCS-01 audit YYYY-MM-DD

Что нашёл: <список>
Что починил сразу: <список>
Что перенёс в backlog: <ID задач>
```

## Связанные

- `CLAUDE.md` §1-8 — правила (источник истины enforcement)
- `doc_inventory.yaml` — SSOT классификации
- `tests/integration/test_doc_enforcement_alive.py` — meta-test (запускается автоматически каждую ночь)
- [TEST_ARCHITECTURE.md](../../TEST_ARCHITECTURE.md) — слои, фикстуры, стратегия
