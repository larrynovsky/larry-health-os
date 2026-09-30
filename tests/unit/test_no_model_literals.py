"""Сторож (audit 2026-06-17): в LIVE-коде нет литералов выбора модели.
Все вызовы должны идти через hai_core.get_model / MODEL_DEFAULTS.
Исключения: pricing-таблицы (ключи "claude-...": {...}), комбинированные
ярлыки ("model": "a + b"), комментарии — не матчатся паттерном `<name>=`.
Скрипты _*.py и tests/ — вне scope. Принцип #5: проверяем и чистоту, и детект."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
# Литерал ВЫБОРА модели: имя-присваивание = "claude-..."
SELECTOR = re.compile(r'(?:model|MODEL|HAIKU|SONNET|OPUS|HAIKU_MODEL|SONNET_MODEL)\s*=\s*["\']claude-')

LIVE_DIRS = ["", "bot", "handlers", "jobs", "services", "dashboard_routers"]


def _live_py_files():
    files = []
    for d in LIVE_DIRS:
        base = ROOT / d if d else ROOT
        if not base.exists():
            continue
        for p in base.glob("*.py"):
            if p.name.startswith("_"):
                continue
            if p.name == "hai_core.py":   # единый источник — допустимо
                continue
            files.append(p)
    return files


def _scan_text(text: str):
    return [ln for ln in text.splitlines() if SELECTOR.search(ln)]


def test_no_model_literals_in_live_code():
    offenders = {}
    for p in _live_py_files():
        hits = _scan_text(p.read_text(encoding="utf-8"))
        if hits:
            offenders[str(p.relative_to(ROOT))] = hits
    assert not offenders, f"Литералы выбора модели вне hai_core: {offenders}"


def test_detector_catches_violation():
    # Позитивный кейс: детектор обязан ловить инъекцию.
    bad = 'resp = client.messages.create(model="claude-haiku-4-5", max_tokens=10)'
    assert _scan_text(bad), "Детектор не поймал явное нарушение!"


def test_detector_ignores_pricing_and_labels():
    ok = '    "claude-haiku-4-5": {"input": 0.8}\n    "model": "claude-haiku-4-5 + claude-sonnet-4-6"'
    assert not _scan_text(ok), "Детектор ложно сработал на прайс/ярлык"


def test_detector_limitation_documented():
    """Документируем предел grep-детектора: модель через переменную или f-string
    он НЕ ловит (паттерн ждёт кавычку сразу после `=`). Сознательное ограничение
    (audit 2026-06-17). Если детектор усилят до AST — обновить этот тест."""
    indirect = "    client.messages.create(model=chosen_model, max_tokens=10)"
    fstring = '    client.messages.create(model=f"claude-{ver}", max_tokens=10)'
    assert not _scan_text(indirect), "переменная-модель не ловится — это известный предел"
    assert not _scan_text(fstring), "f-string-модель не ловится — это известный предел"
