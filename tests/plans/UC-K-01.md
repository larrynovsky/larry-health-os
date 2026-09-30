# Тест-план UC-K-01 — Specialist prompts upstream tracking

**Источник:** USE_CASES.md §4.K → UC-K-01.
**Status:** `partial` · **Confirmation:** `confirmed`
**Type:** `e2e_mock` · **Oracle:** `B + E`
**Owner:** `check_wellally_updates.py`

---

## Что проверяем

`check_wellally_updates.py` (уже существует, найдено при валидации T-1.12):
1. Опрашивает GitHub API для `huifer/WellAlly-health` и `huifer/Claude-Ally-Health`.
2. Сравнивает SHA локальных файлов в `.claude/specialists/*.md` с remote.
3. При расхождении — формирует diff-сообщение и (с `--notify`) шлёт в TG.
4. Сохраняет известный SHA в `~/health_scripts/.wellally_upstream_state.json`.

**NOT-Then (главный инвариант UC-K-01):** apply изменений **не происходит**.
Скрипт только показывает diff, человек применяет вручную (через rsync/git pull).

---

## Стратегия теста

**Уровень:** `unit` через mock `urllib.request.urlopen` + tmp `STATE_FILE`.

**Оракулы:**
- **B (negative):** скрипт не пишет файлы в `.claude/specialists/` сам.
- **B:** GitHub API timeout → graceful return, `last_check_ok` не обновляется
  лживо (state не сохраняется).
- **E (cross-check):** mock GitHub API с разными SHA → `changed_files` непуст;
  если SHA совпадают → `new_commits = False`.
- **E:** `local_file_sha` использует git-blob алгоритм (`sha1("blob {n}\0{content}")`).

---

## Структура теста

```python
def test_local_file_sha_matches_git_blob_format():
def test_check_repo_no_change_when_sha_unchanged():
def test_check_repo_detects_new_commits():
def test_check_repo_detects_changed_files():
def test_check_repo_detects_new_files():
def test_check_repo_does_not_modify_local_files():  # NOT-Then
def test_state_file_saved_after_update():
def test_404_repo_handled_gracefully():
```

---

## Acceptance

8 unit-тестов с моками. Один (NOT-Then) — главный страх UC-K-01.

## Risk если красный

`partial` → expected_gap (apply-команда `/upstream_apply` ещё не реализована).
REGRESSION — если скрипт начнёт применять файлы автоматически.
