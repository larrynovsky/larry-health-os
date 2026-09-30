[English](git_workflow.en.md) · **Русский**

# Git workflow

> **Тип документа:** How-to (Diátaxis) — конкретные команды для повседневных задач.
> Архитектурное обоснование: [docs/explanation/git_architecture.md](../explanation/git_architecture.md).
>
> **История моделей:**
> - 2026-05-09 — Studio canonical, MacBook без `.git/`.
> - 2026-05-23 — **Sprint 7-GIT**: MacBook имеет `.git/` как клон Studio, push через `git push studio main`. Текущая модель — см. ниже.

---

## Где живёт git (актуально с 2026-05-23)

- **Studio canonical**: `~/health_scripts/.git/` (production reference).
- **MacBook clone**: `~/health_scripts/.git/` (working copy с full history).
- **Remote `studio` на MacBook** = `<studio_ssh>:~/health_scripts`.
- **Studio config**: `receive.denyCurrentBranch=updateInstead` — push в checked-out branch обновляет working tree.

`STUDIO="<studio_ssh>"` (из `~/.infrastructure.md`).

---

## Посмотреть git status

```bash
ssh <studio_ssh> "cd ~/health_scripts && git status"
ssh <studio_ssh> "cd ~/health_scripts && git status -s"   # короткий
```

## Посмотреть git log

```bash
ssh <studio_ssh> "cd ~/health_scripts && git log --oneline -10"
ssh <studio_ssh> "cd ~/health_scripts && git log --since='1 week ago' --oneline"
ssh <studio_ssh> "cd ~/health_scripts && git log --all --oneline --graph -20"
```

## Сделать commit (с MacBook — основной flow)

Работа длиннее одного коммита — в своём дереве:

```bash
cd ~/health_scripts
scripts/thread_start.sh <slug>
cd ~/.worktrees/health_scripts/<slug>
```

Коммитить в рабочей копии MacBook:

```bash
git add file1.py file2.md
git commit -m 'feat: ...' -- file1.py file2.md
# В main post-commit делает git push studio main + рестарт служб.
# В thread/<slug> деплой штатно пропускается.
```

После коммитов закрыть нить **из главной копии**:

```bash
cd ~/health_scripts
scripts/thread_finish.sh <slug>
```

Закрытие включает ребейз на `main`, полный прогон на Studio, слияние `--no-ff` и явный вызов деплоя. Разбор отказов — [thread_worktree.md](thread_worktree.md). Для разовой правки в главной копии используй коммит с явными путями, как выше: он не заберёт остальной индекс.

## Проверить синтаксис до commit

Дешевле, чем разбирать упавший деплой (в SSH PATH нет Homebrew — путь полный):

```bash
/opt/homebrew/bin/python3.11 -m py_compile <file>.py
```

## Studio — только чтение и запуск

Commit на Studio **отклоняет pre-commit**: single-writer = MacBook, Studio deploy-only (`e017929`). Правка файла по ssh без коммита оставит Studio dirty и сломает следующий push — `receive.denyCurrentBranch=updateInstead` требует чистое дерево. Все правки и коммиты — на MacBook (CLAUDE.md (в закрытой части) § C «Среда»).

Аварийный обход в pre-commit остаётся (`scripts/git-hooks/pre-commit`), но открывается только по явному запросу владельца в текущей сессии:

```bash
# На Studio, ТОЛЬКО emergency:
ssh <studio_ssh> "cd ~/health_scripts && HEALTH_ALLOW_STUDIO_COMMIT=1 git commit -am '...'"
# На MacBook сразу после — иначе следующий push упадёт на fast-forward:
cd ~/health_scripts && git fetch studio && git reset --hard studio/main
```

После ssh-ЗАПУСКА кода на Studio (тесты, генерация, миграции — это разрешено и коммитом не является) следующую сессию в главной копии MacBook начинай с `git fetch studio && git reset --hard studio/main` (CLAUDE.md (в закрытой части) § C «Среда»).

⚠️ Скрипты `scripts/sync_from_studio.sh` / `sync_to_studio.sh` **удалены 2026-06-28** (CLAUDE.md (в закрытой части) §6 RETIRED). Если встретишь их в тексте — текст устарел, команда одна: `git fetch studio && git reset --hard studio/main`.

## Ночной снимок кода (ежедневно 03:00) — НЕ commit в main

`~/Library/LaunchAgents/com.larry.health.backup.plist` на MacBook вызывает `~/health_scripts/backup.sh`. **С 2026-07-24 (решение владельца) он в `main` не коммитит и `main` не пушит.** Собирает дерево во временном индексе → `commit-tree` → `refs/backups/daily-<дата>` → `push -f` этого ref на Studio. `main`, индекс и working tree не тронуты, деплоя нет, гейты не обходятся. Локально держится 14 последних снимков.

До 2026-07-24 скрипт делал `git commit --no-verify` + `git push studio main` — ночью деплоился непроверенный WIP в обход всех гейтов. Если где-то ещё написано «ночной `git add -A` уедет в canonical» — это старая редакция.

Достать файл из ночного снимка:

```bash
git checkout refs/backups/daily-2026-08-02 -- path/to/file.py
```

Если ночной снимок не сработал — проверить лог:

```bash
tail -20 ~/health_scripts/logs/backup.log
# Ищем "git snapshot: FAILED" или "git push snapshot: FAILED"
```

## Что делать когда post-commit hook сообщил `push FAILED`

Сообщение в `logs/deploy.log`: `Studio впереди MacBook или dirty working tree`. Это значит либо:
- История Studio опережает MacBook. Recovery: `git fetch studio && git rebase studio/main`, затем `git push studio main` вручную.
- Working tree на Studio dirty (кто-то редактирует через ssh без commit). Не коммить на Studio и не удаляй чужую работу; согласуй сохранение правок на MacBook через `docs/handoff/<нить>/inbox/<твой-слаг>.md` (CLAUDE.md (в закрытой части) §22).

```bash
# Проверить разрыв
git fetch studio && git log --oneline HEAD..studio/main
# Подтянуть Studio
git rebase studio/main   # или git reset --hard studio/main если локальные есть только в push
# Retry
git push studio main
```

## Откат файла к предыдущей версии

Откат делается на MacBook — он единственный писатель; на Studio правка доедет обычным post-commit push. Откат ЧЕРЕЗ Studio (`ssh … git checkout` + `rsync` обратно) — старая rsync-модель, она снята 2026-06-28 и оставляет Studio dirty.

```bash
cd ~/health_scripts
git checkout HEAD~1 -- путь/к/файлу
git commit -m 'revert: <файл> к HEAD~1, причина' -- путь/к/файлу
# post-commit сам запушит на Studio и рестартит бота
tail -3 logs/deploy.log
```

## Посмотреть git blame

```bash
ssh <studio_ssh> "cd ~/health_scripts && git blame файл.py | head -30"
```

## Восстановить commit из reflog

reflog хранится ~30 дней. Восстановить «забытый» commit:

```bash
cd ~/health_scripts
git reflog | head -20
# Найти SHA нужного commit'а
git checkout <SHA> -- файл
git commit -m 'restore: файл из <SHA>' -- файл
```

## Восстановить полностью утерянный репо (катастрофа)

Если Studio погиб и git с ним:

1. **Из Time Machine** — восстановить `~/health_scripts/.git/` целиком.
2. **Из bundle**: на MacBook лежит `/tmp/macbook_health_v2.bundle` от 2026-05-09 (момент миграции). Это начальная точка восстановления — всё что после нужно докоммитить руками или собрать из Time Machine.

## Сделать новый bundle для архива

При значимых вехах:

```bash
ssh <studio_ssh> "cd ~/health_scripts && git bundle create /tmp/health_$(date +%Y-%m-%d).bundle --all"
scp <studio_ssh>:/tmp/health_*.bundle ~/Documents/git_bundles/
```

## Что делать когда правил .py или .md файл (с 2026-05-23)

Для разовой правки в главной копии MacBook (длиннее одного коммита — через `thread_start`/`thread_finish`, см. выше):

```bash
# 1. Правка локально через Edit/Write
# 2. Commit на MacBook
cd ~/health_scripts && git add файл && git commit -m '...' -- файл
# post-commit hook сам делает:
#   - git push studio main   (атомарно, fast-forward только)
#   - ssh studio "launchctl restart bot"
# 3. Проверка
tail -3 ~/health_scripts/logs/deploy.log
# Ожидаем: "push + bot restart OK"
```

Правка прямо на Studio через ssh-edit запрещена, в том числе для одной строки: Studio — deploy-only (CLAUDE.md (в закрытой части) § C «Среда»).

## Что делать когда хук блокирует commit

Pre-commit hook на MacBook проверяет doc-invariants. Если блокирует:

1. Прочитать сообщение hook'а — он указывает причину.
2. Исправить **причину**, не обходить через `--no-verify`.
3. Если hook сам сломан (yaml deadlock) — escape-hatch: `git commit --no-verify` с явным комментарием почему.

**Запрет**: AI-помощник не использует `--no-verify` без явного запроса пользователя в текущей сессии.

## Связанные документы

- [docs/explanation/git_architecture.md](../explanation/git_architecture.md) — почему такой workflow.
- CLAUDE.md § C «Среда» (в закрытой части) — норма: кто пишет, деплой-путь, надгробия прежних моделей.
- [docs/BACKUP_POLICY.md §R0](../BACKUP_POLICY.md) — split MacBook (снимок кода) / Studio (sqlite).
- [docs/how-to/update_docs.md](update_docs.md) — workflow при изменении документации.
