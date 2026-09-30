[English](add_new_uc.en.md) · **Русский**

# Как добавить новый UC и тест

> **Тип документа:** How-to (Diataxis).
> Контекст: `docs/explanation/test_architecture.md` — почему такой процесс.
> Полный каталог UC: `USE_CASES.md`. Машинный индекс: `uc_index.yaml`.

---

## Сценарий 1: новый UC с нуля

### Шаг 1: Добавить запись в `USE_CASES.md` §3 каталог

Найди подходящую группу (A импорт / B аналитика / ... / X external deps).
Добавь строку в таблицу:

```markdown
| `UC-X-NN` | Краткая прагматика | P0 | check | intended | proposed |
```

- `intended` если кода ещё нет.
- `partial` если часть работает.
- `implemented` если уже работает (но тогда зачем UC?).
- `proposed` пока ты не подтвердил.

### Шаг 2: Раскрытое описание в §4 (только P0)

Скопируй шаблон из существующего P0 UC:

```markdown
#### UC-X-NN: Название

**Lifecycle:** `status=intended · confirmation=proposed · ...`
**Owner:** `module.py`

**Given:** ...

**When:** ...

**Then:**
1. ...

**B (negative invariants):**
- ...

**E (cross-check):**
- ...

**NOT-Then:**
- ...
```

### Шаг 3: Подтвердить UC

Когда формулировка устраивает — поменяй `confirmation: proposed → confirmed`
в каталоге §3. Это разблокирует генерацию теста.

### Шаг 4: Создать тест-план

```bash
# В tests/plans/UC-X-NN.md
```

Структура: что проверяем, оракулы (B/E/C/D), какие фикстуры, что НЕ покрывает.
Образец: `tests/plans/UC-I-02.md`.

### Шаг 5: Сгенерировать skeleton

```bash
python3.11 generate_test.py UC-X-NN --dry-run    # показать план
python3.11 generate_test.py UC-X-NN              # ждёт yes → создаёт skeleton
python3.11 generate_test.py UC-X-NN --auto-confirm  # для CI/Claude
```

Skeleton попадёт в `tests/{layer}/test_uc_x_nn_*.py` где layer — из `type`.
`generate_test.py` откажет если `confirmation != confirmed`.

### Шаг 6: Заполнить тесты

В skeleton — `pytest.skip("заглушка")`. Замени на реальные проверки из плана.

### Шаг 7: Обновить `uc_index.yaml`

Добавь запись с `modules / data / tests / oracle / risk`. Запусти
`validate_uc_index.py` — должно быть OK.

### Шаг 8: Прогнать тесты

```bash
python3.11 -m pytest tests/{layer}/test_uc_x_nn_*.py -v
```

Если зелёный — закоммить.

---

## Сценарий 2: UC появляется автоматически из git diff (UC-J-01)

После коммита в B-скоуп `propose_uc.py` создаёт файл в
`tests/plans/proposed/{date}_{sha}.md`.

```bash
# Триггер вручную (обычно — через post-commit hook)
python3.11 propose_uc.py
```

Внутри — список изменённых файлов и затронутых групп UC. Дальше:

1. Прочитать proposal.
2. Решить: меняет ли коммит контракт UC?
   - Да → обновить соответствующий UC в `USE_CASES.md` (Then/B/E/NOT-Then).
   - Нет → закрыть proposal (move в `proposed/applied/`).

`propose_uc.py` **никогда не пишет в `USE_CASES.md` сам**. Это явный
guard-инвариант UC-J-01.

---

## Сценарий 3: Изменить существующий UC

1. Открыть `USE_CASES.md` §3 каталог. Найти запись.
2. Возможно, нужно сменить `confirmation: confirmed → proposed` (новое
   поведение требует ревью).
3. Обновить раскрытое описание в §4 (Then/B/E/NOT-Then).
4. Если у UC есть `tests` в `uc_index.yaml` — пересмотреть тесты:
   старая логика всё ещё актуальна? добавить новые проверки?
5. Запустить `validate_uc_index.py` — должно быть OK.

---

## Сценарий 4: UC переходит в другой статус

| Переход | Что делать |
|---|---|
| `intended` → `partial` | Появилась частичная реализация. Снять `xfail` с тех тестов, которые теперь проходят. |
| `partial` → `implemented` | Полная реализация. Снять все `xfail`. Если проходят — UC закрыт. |
| `implemented` → `partial` | Регрессия принята как «временно». Поставить `xfail` на упавшие тесты со ссылкой на причину. |
| любой → `rejected` | Решено что UC не нужен. Удалить тесты и план, оставить запись в каталоге для истории. |

---

## Связанные документы

- `USE_CASES.md` — каталог UC.
- `uc_index.yaml` + `validate_uc_index.py` — машинный индекс.
- `propose_uc.py` (UC-J-01) и `generate_test.py` (UC-J-02) — meta-инфра.
- `docs/explanation/test_architecture.md` — почему этот процесс.
- `tests/README.md` — структура каталогов.
