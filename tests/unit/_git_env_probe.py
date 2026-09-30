"""Приманка для оракула изоляции git-окружения. В обычный прогон НЕ попадает.

Имя без `test_`: `pytest.ini` не задаёт `python_files`, значит действует дефолт
`test_*.py` / `*_test.py`, и этот файл не собирается ничем, кроме явного пути.
Запускает его `tests/unit/test_git_env_isolation.py`, передав путь отчёта в
`PROBE_REPORT_PATH`.

Почему приманка постоянная, а не пишется во время прогона. 12.09 соседняя нить
сделала `git add -A` посреди моей работы и унесла мои файлы в свой коммит.
Тест, который создаёт файлы внутри дерева репозитория, эту лотерею повторяет;
файл, лежащий в git постоянно, — нет.

Приманка обязана жить ВНУТРИ `tests/`: она меряет, доезжает ли до теста
`tests/conftest.py`. Проба вне дерева (приём из
`test_data_ingestion_expected_red.py`) для этого не годится — там conftest
намеренно не подключается.
"""
import json
import os
import subprocess

GIT_VARS_PREFIX = "GIT_"


def test_probe_reports_git_env():
    out = os.environ.get("PROBE_REPORT_PATH")
    assert out, "приманку запустили без PROBE_REPORT_PATH — отчёт некуда писать"

    # Имя переменной-отчёта НАРОЧНО без префикса GIT_: первая редакция назвала её
    # GIT_ENV_PROBE_OUT, и приманка поймала собственный управляющий вход как
    # утечку. Оракул покраснел на себе — дёшево и вовремя, но повторять незачем.
    seen = {k: v for k, v in os.environ.items() if k.startswith(GIT_VARS_PREFIX)}
    # Без cwd: наследуем рабочий каталог прогона. Именно так git и видит любой
    # тест, который зовёт git «просто так», — и именно так родился a251b93.
    r = subprocess.run(["git", "rev-parse", "--absolute-git-dir"],
                       capture_output=True, text=True, timeout=30)

    with open(out, "w", encoding="utf-8") as f:
        json.dump({"env": seen, "git_dir": r.stdout.strip(),
                   "rc": r.returncode}, f, ensure_ascii=False)
