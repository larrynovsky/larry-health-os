"""Ключ Anthropic читается ровно из ОДНОГО дома — llm_client.api_key().

Дом завели 2026-08-03, и его докстринг объявил себя «единственным местом». Замер
2026-09-02 показал, что утверждение было ложным всё это время: 19 модулей собирали
путь сами, а в lab_extractor путь вёл в НЕСУЩЕСТВУЮЩИЙ файл
`~/health_scripts/.anthropic_key` — LLM-тракт был сломан, и это не всплыло,
потому что тесты мокают клиента. Строка в докстринге не является механизмом; механизм —
этот тест.

ЧТО СТЕРЕЖЁТ: не утечку (ключ общий для тенантов, это ключ проекта), а ЕДИНСТВЕННОСТЬ
дома. Двадцатый модуль со своей копией пути появится с красным тестом, а не тихо — и
не унесёт с собой второй сломанный путь.

ГРАНИЦА: судим по литералам исходников. Путь, собранный через переменную из другого
модуля, сюда не попадёт.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.consistency

ROOT = Path(__file__).resolve().parents[2]

# Дом (читает по определению) и генераторы справки, которые ПЕЧАТАЮТ путь как
# документацию, а не читают его. Список имён, а не число: baseline-счётчик заморозил
# бы долг как норму, а имя требует назвать, почему оно здесь.
ALLOW = {
    "llm_client.py": "дом: единственное место, где ключ читается",
    "gen_arch_blocks.py": "генератор справки: печатает таблицу внешних зависимостей",
    "gen_key_paths.py": "генератор справки: печатает карту путей",
    "check_contracts.py": "сторож: ищет это имя в чужих файлах",
    "secrets_paths.py": "реестр классов секретов",
    "smoke_tests.py": "проверяет НАЛИЧИЕ файла, не читает значение",
    "doc_agent.py": "текст ошибки для человека, чтения нет",
    "night_investigator.py": "текст ошибки для человека, чтения нет",
}

_READ = re.compile(
    r'(?:Path|_Path)\.home\(\)\s*/\s*"\.health_secrets(?:/anthropic_key"|"\s*/\s*"anthropic_key")'
    r'|anthropic_key"\s*\)?\s*\.read_text'
    r'|\.anthropic_key"\s*\)')


def _sources() -> dict[str, str]:
    out = {}
    for f in subprocess.run(["git", "ls-files", "*.py"], cwd=ROOT,
                            capture_output=True, text=True).stdout.split():
        if f.startswith(("tests/", "plans/", "scripts/")):
            continue
        out[f] = (ROOT / f).read_text(encoding="utf-8", errors="ignore")
    return out


def _own_path_builders(sources: dict[str, str], allow: set[str]) -> list[str]:
    """Чистая функция — её и проверяет позитивный контроль на синтетике."""
    return sorted(f for f, src in sources.items()
                  if f not in allow and _READ.search(src))


def test_positive_control_catches_a_new_copy():
    """Датчик обязан краснеть на нарочно заведённой копии (RST)."""
    evil = {"new_agent.py": 'KEY = Path.home() / ".health_secrets/anthropic_key"\n'
                            'k = KEY.read_text().strip()'}
    assert _own_path_builders(evil, allow=set()) == ["new_agent.py"]
    good = {"ok.py": "import llm_client\nk = llm_client.api_key()"}
    assert _own_path_builders(good, allow=set()) == []


@pytest.mark.host_only
def test_key_has_one_home():
    offenders = _own_path_builders(_sources(), set(ALLOW))
    assert not offenders, (
        f"модули собирают путь к ключу сами: {offenders}. Возьми llm_client.api_key() — "
        "иначе это вторая копия знания «где лежит секрет», и она разойдётся молча "
        "(02.09: одна из копий вела в несуществующий файл). Осознанное исключение — "
        "строка в ALLOW с причиной, не число в baseline.")


def test_allow_entries_still_exist():
    """Реестр исключений не переживает свои файлы (иначе он врёт про дерево)."""
    missing = sorted(f for f in ALLOW if not (ROOT / f).exists())
    assert not missing, f"в ALLOW есть несуществующие файлы: {missing}"
