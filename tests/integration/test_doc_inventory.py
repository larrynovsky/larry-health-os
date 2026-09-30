"""
P3-1 — Coverage-тест: каждый .md в проекте классифицирован в doc_inventory.yaml.

Источник: ROADMAP Wave 3-DOC v2 P3-1.

Проверяет:
- Каждый .md (не в ignored_paths) присутствует в `live` ИЛИ `archive`.
- Никакой файл не в обеих категориях одновременно.
- doc_inventory.yaml парсится без ошибок.

Это первый слой drift-инфраструктуры: новый .md без явной классификации
ловится тестом → разработчик обязан принять решение «live или archive».
"""
from __future__ import annotations

import fnmatch
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
INVENTORY = ROOT / "doc_inventory.yaml"


def _load_inventory() -> dict:
    """Загрузка doc_inventory.yaml с проверкой синтаксиса."""
    assert INVENTORY.exists(), f"doc_inventory.yaml не найден в {ROOT}"
    with INVENTORY.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    assert isinstance(data, dict), "yaml должен быть mapping"
    for key in ("live", "archive", "ignored_paths"):
        assert key in data, f"yaml не содержит ключ {key!r}"
        assert isinstance(data[key], list), f"{key} должен быть list"
    return data


def _expand_patterns(patterns: list[str]) -> set[str]:
    """Раскрываем glob-паттерны в реальные относительные пути файлов."""
    result: set[str] = set()
    for pat in patterns:
        # Glob — ищем по реальной файловой системе
        if "*" in pat or "?" in pat:
            for p in ROOT.glob(pat):
                if p.is_file():
                    result.add(str(p.relative_to(ROOT)))
        else:
            # Explicit path
            full = ROOT / pat
            if full.is_file():
                result.add(pat)
    return result


def _is_ignored(rel_path: str, ignored: list[str]) -> bool:
    """Путь начинается с одного из ignored prefix'ов."""
    return any(rel_path.startswith(ig.rstrip("/") + "/") or rel_path.startswith(ig) for ig in ignored)


def _all_md_in_project() -> set[str]:
    """Список всех .md файлов в проекте (relative to ROOT)."""
    md_files = set()
    for p in ROOT.rglob("*.md"):
        rel = p.relative_to(ROOT)
        md_files.add(str(rel))
    return md_files


def test_yaml_loads_and_has_required_keys():
    """doc_inventory.yaml парсится и содержит обязательные ключи."""
    data = _load_inventory()
    assert "history_section_headers" in data
    assert "stop_words_in_live" in data
    assert "silent_except_baseline" in data
    assert isinstance(data["silent_except_baseline"], int)


def test_every_md_classified():
    """Каждый .md (вне ignored_paths) присутствует в live или archive."""
    data = _load_inventory()
    live_set = _expand_patterns(data["live"])
    archive_set = _expand_patterns(data["archive"])
    classified = live_set | archive_set
    ignored = data["ignored_paths"]

    all_md = _all_md_in_project()
    unclassified = []
    for md_path in all_md:
        if _is_ignored(md_path, ignored):
            continue
        if md_path not in classified:
            unclassified.append(md_path)

    assert not unclassified, (
        f"Найдены .md без классификации в doc_inventory.yaml:\n  "
        + "\n  ".join(unclassified)
        + "\n\nДобавь в `live` (drift-тесты проверяют) или `archive` (история, пропускается),\n"
        + "или включи путь в `ignored_paths` если файл — артефакт/мусор."
    )


def test_no_file_in_both_categories():
    """Файл не должен быть одновременно в live и archive."""
    data = _load_inventory()
    live_set = _expand_patterns(data["live"])
    archive_set = _expand_patterns(data["archive"])
    overlap = live_set & archive_set
    assert not overlap, f"Эти .md одновременно в live И archive: {sorted(overlap)}"


@pytest.mark.owner_data
def test_live_files_exist():
    """Все указанные в `live` явные пути (не glob) — реально существуют."""
    data = _load_inventory()
    missing = []
    for pat in data["live"]:
        if "*" in pat or "?" in pat:
            continue  # glob проверим отдельно
        full = ROOT / pat
        if not full.is_file():
            missing.append(pat)
    assert not missing, f"live: эти явные пути не существуют: {missing}"


@pytest.mark.owner_data
def test_archive_files_exist():
    """Все указанные в `archive` файлы существуют."""
    data = _load_inventory()
    missing = []
    for pat in data["archive"]:
        if "*" in pat or "?" in pat:
            continue
        full = ROOT / pat
        if not full.is_file():
            missing.append(pat)
    assert not missing, f"archive: эти файлы не существуют: {missing}"


def test_baseline_silent_except_is_positive_int():
    """silent_except_baseline — sane значение (положительное число)."""
    data = _load_inventory()
    baseline = data["silent_except_baseline"]
    assert isinstance(baseline, int) and baseline >= 0, \
        f"silent_except_baseline должно быть >= 0, получили {baseline!r}"


def test_plans_md_classified_as_archive():
    """C6 (audit 2026-06-17): plans/*.md классифицированы как archive (история),
    а не live/unclassified. Ловит: будущий plans/-файл, выпавший из инвентаря,
    и ошибочную пометку live."""
    data = _load_inventory()
    live_set = _expand_patterns(data["live"])
    archive_set = _expand_patterns(data["archive"])
    plans = {m for m in _all_md_in_project() if m.startswith("plans/")}
    if not plans:
        pytest.skip("нет plans/*.md в проекте")
    not_archived = plans - archive_set
    assert not not_archived, f"plans/*.md вне archive: {sorted(not_archived)}"
    in_live = plans & live_set
    assert not in_live, f"plans/*.md ошибочно помечены live: {sorted(in_live)}"
