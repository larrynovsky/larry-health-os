[English](update_docs.en.md) · **Русский**

# Как обновлять документацию при изменениях в проекте

> **Тип документа:** How-to (Diataxis).
> Карта документации: CLAUDE.md (в закрытой части).
> Команды деплоя: [docs/how-to/git_workflow.md](git_workflow.md).

---

## При добавлении нового модуля (.py файла)

1. Если модуль несёт новый ЗАМЫСЕЛ — запись в `subsystem_intent.yaml` (intent · code_anchors · invariants).
   Если модуль лишь исполняет существующий замысел — дополнить `code_anchors` у него.
   Грабли («мир оказался не таким») — не сюда, а в `lessons.yaml` (C-NN; формат — `docs/reference/lessons_format.md`):
   их печатает `preflight` тому, кто работает. Прозаический раздел в BLUEPRINT снят 2026-08-02.
2. Добавить в `ARCH_SNAPSHOT.md` — в таблицу модулей и в граф зависимостей
3. Если модуль импортирует внешний API — добавить в `ARCH_SNAPSHOT.md §ВНЕШНИЕ ИНТЕГРАЦИИ`
4. Запустить `gen_blueprint.py` для обновления автогенерируемых секций:

```bash
ssh <studio_ssh> "cd ~/health_scripts && /opt/homebrew/bin/python3.11 gen_blueprint.py"
# Генератор бежит на Studio (CLAUDE.md (в закрытой части) §8: canonical-only generation),
# но забирается результат git-ом, НЕ rsync (скрипты удалены 2026-06-28):
git fetch studio && git reset --hard studio/main
```

---

## При изменении расписания LaunchAgent или APScheduler

1. Обновить таблицу в `ARCH_SNAPSHOT.md §РАСПИСАНИЕ`
2. Обновить поток в `docs/explanation/data_flow.md`

---

## При изменении схемы БД (новая таблица или колонка)

```bash
# Применить миграцию на Studio:
ssh <studio_ssh> "/opt/homebrew/bin/python3.11 ~/health_scripts/health_db.py migrate"
```

Обновить `ARCH_SNAPSHOT.md §DB SCHEMA` — добавить CREATE TABLE или ALTER TABLE.

---

## При любом значимом изменении кода

`doc_agent.py` добавляет запись в `CHANGELOG.md` автоматически: за нить — при её закрытии (`scripts/thread_finish.sh`, строка из заголовков коммитов нити с меткой `[нить <slug>]`), за коммит прямо в main — через post-commit hook.
Вручную запускать не нужно. Проверить, что hook сработал:

```bash
tail -5 ~/health_scripts/CHANGELOG.md
```

---

## При изменении секретов или путей

Обновить `ARCH_SNAPSHOT.md §ВНЕШНИЕ ИНТЕГРАЦИИ` и `§КЛЮЧЕВЫЕ ПУТИ`.

---

## Команды деплоя (шпаргалка)

```bash
# В своей рабочей копии MacBook — проверить синтаксис до коммита:
/opt/homebrew/bin/python3.11 -m py_compile <file>.py && echo OK

# Закоммитить правки на MacBook:
git add <file>
git commit -m 'docs: ...' -- <file>
# Прямой коммит в main сам запускает post-commit: git push studio main + рестарт.
# Для работы длиннее одного коммита — дерево scripts/thread_start.sh <slug>;
# после коммитов закрыть нить ИЗ ГЛАВНОЙ копии:
cd ~/health_scripts
scripts/thread_finish.sh <slug>

# Проверить доставку перед доверием к integrity (§12):
git fetch studio
git log studio/main   # содержит проверяемый коммит?

# Запустить integrity_tests:
ssh <studio_ssh> "/opt/homebrew/bin/python3.11 ~/health_scripts/integrity_tests.py 2>&1 | tail -15"
```

---

## При фиксе tech debt

Убрать строку из `BACKLOG.md`. Запись в `CHANGELOG.md` создаёт `doc_agent` при закрытии нити или через post-commit для коммита в `main`; вручную не дописывать (CLAUDE.md (в закрытой части) § C «Среда»).
