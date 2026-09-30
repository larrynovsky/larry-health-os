[English](run_tests.en.md) · **Русский**

# Как запускать тесты

> **Тип документа:** How-to (Diataxis) — конкретные команды для конкретных задач.
> Если хочешь понять **зачем** так — `docs/explanation/test_architecture.md`.
> Если хочешь увидеть **что есть** — `tests/README.md`, `USE_CASES.md`.

---

## Локально на MacBook (pre-commit)

```bash
cd ~/.worktrees/health_scripts/<slug>  # дерево нити; для разовой правки — ~/health_scripts

# Локальный прогон на фикстурах (не заменяет полный прогон на Studio)
python3.11 -m pytest tests/

# Только unit
python3.11 -m pytest tests/unit/

# Только consistency-сценарии
python3.11 -m pytest -m consistency

# Конкретный UC
python3.11 -m pytest tests/unit/test_uc_i_02_sec.py -v

# Один тест по имени
python3.11 -m pytest tests/ -k "test_owner_filter_passes"

# С реальным Anthropic API (платно)
python3.11 -m pytest -m "requires_anthropic_key" --override-ini="addopts="
```

## Полный прогон при закрытии нити

Работа длиннее одного коммита — в дереве, созданном `scripts/thread_start.sh <slug>`. После коммитов в дереве нити запусти из главной копии MacBook:

```bash
cd ~/health_scripts
scripts/thread_finish.sh <slug>
```

Скрипт переставляет ветку на текущий `main`, проверяет гейты и гонит полный прогон на Studio из дерева нити **до слияния и деплоя**. Отдельно повторять его перед закрытием не нужно. Порядок и разбор отказов — [thread_worktree.md](thread_worktree.md).

## На Studio (после git-push деплоя, для отладки)

Перед доверием к результату проверь с MacBook: `git fetch studio && git log studio/main` должен показывать проверяемый коммит (CLAUDE.md (в закрытой части) §12).

```bash
ssh <studio_ssh> "cd ~/health_scripts && /opt/homebrew/bin/python3.11 -m pytest tests/"
```

Или полный pyramid:

```bash
ssh <studio_ssh> "cd ~/health_scripts && bash run_full_test_suite.sh"
```

Ночью это запускается само в 00:00 через `com.larry.health.test-suite`.

## Ручной триггер ночного suite (для отладки handler)

```bash
ssh <studio_ssh> "launchctl kickstart -k gui/$(id -u)/com.larry.health.test-suite"
# Логи:
ssh <studio_ssh> "tail -f ~/health_test_suite.log"
```

## Что делать с XFAIL

Тесты, помеченные `xfail`, документируют **известные баги** на `partial` UC.
Они не падают как красные, но и не зелёные. После починки бага xfail
«загорится» как `XPASS` — это сигнал убрать декоратор `@pytest.mark.xfail`.

Список известных XFAIL см. в `USE_CASES.md` (секции «Подтверждённый баг» в
UC-I-03, UC-A-03).

## Что делать с SKIPPED

`skipped` — тест требует ресурса, которого нет:
- `requires_anthropic_key` — нет API ключа.
- `requires_studio` — запускается только на Studio (основная машина — `infra_config.is_primary()`).
- модуль ещё не реализован.

Это не failure. Когда ресурс появится — снять skip или переписать как
полноценный тест.

## Установка зависимостей

```bash
# pytest и unit-зависимости
/opt/homebrew/bin/python3.11 -m pip install pytest --break-system-packages
```

На Studio то же — но через ssh с тем же путём.

## Что добавить в `~/.gitignore`

```
tests/.pytest_cache/
tests/reports/
tests/.diagnosis_cache/
**/__pycache__/
.pytest_cache/
```

## Связанные документы

- `tests/README.md` — структура каталогов, маркеры, как добавлять.
- `docs/how-to/add_new_uc.md` — процесс UC-J-02.
- `docs/how-to/handle_test_failure.md` — что делать при ночном fail.
