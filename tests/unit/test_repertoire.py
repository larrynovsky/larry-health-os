"""Репертуар и вкус-фильтр на независимо придуманных блюдах и предпочтениях."""
from __future__ import annotations

import json

import pytest

import repertoire
import taste

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def invented_repertoire(tmp_path, monkeypatch):
    """Подменяет файлы, сохраняя настоящий путь загрузка → вкус → подборка."""
    dishes = [{"title": f"Учебное блюдо {category}-{variant}" + (" глазурь" if variant == 5 else ""),
               "category": f"Учебная категория {category}"}
              for category in range(6) for variant in range(6)]
    menu = tmp_path / "menu.json"
    bridge = tmp_path / "bridge.json"
    menu.write_text(json.dumps({"dishes": dishes}, ensure_ascii=False))
    bridge.write_text(json.dumps({"bridge": [{"pattern": "глазурь", "status": "hard_avoid",
                                            "note": "Придуманное предпочтение"}]}, ensure_ascii=False))
    monkeypatch.setattr(repertoire, "_PATH", menu)
    monkeypatch.setattr(taste, "_BRIDGE_PATH", bridge)
    repertoire.load_repertoire.cache_clear()
    taste._bridge.cache_clear()
    yield
    repertoire.load_repertoire.cache_clear()
    taste._bridge.cache_clear()


def test_loads_full_rubricator():
    assert len(repertoire._dishes()) == 36


def test_suggest_returns_dish():
    s = repertoire.suggest(day_index=0)
    assert s and s["title"] and "репертуар" in s["why"]


def test_suggest_rotates():
    titles = {repertoire.suggest(day_index=i)["title"] for i in range(6)}
    assert len(titles) >= 4                           # ротация по дате даёт разное


def test_suggest_set_varied_across_categories():
    s = repertoire.suggest_set(seed=3, n=6)
    assert len(s) == 6
    assert len({d["title"] for d in s}) == 6          # без повторов
    assert len({d["category"] for d in s}) >= 5       # разнесено по категориям
    assert all("репертуар" in d["why"] for d in s)


def test_suggest_set_rotates_by_seed():
    # месяц к месяцу подборка меняется — не одни и те же 12 блюд («сервис, не проклятие»)
    seen: set[str] = set()
    for seed in range(12):
        seen |= {d["title"] for d in repertoire.suggest_set(seed=seed, n=6)}
    assert len(seen) >= 20                            # за год всплывает широкий срез, не горстка


def test_suggest_set_taste_filtered():
    # ни одно предложенное блюдо не пробивает жёсткий вкус-бан
    for seed in range(12):
        for d in repertoire.suggest_set(seed=seed, n=6):
            assert taste.verdict(d["title"])["status"] != "hard_avoid"


def test_taste_filter_cuts_invented_disallowed_dishes():
    # Придуманный запрет исключает часть меню; остальные блюда доступны.
    ds = repertoire._dishes()
    accepted = [d for d in ds if repertoire._acceptable(d)]
    assert len(accepted) >= len(ds) - 6               # режется единицы, не десятки
    assert len(accepted) < len(ds)                    # но что-то ДЕЙСТВИТЕЛЬНО режется
    excluded = [d for d in ds if "глазурь" in d["title"].lower()]
    assert excluded and all(not repertoire._acceptable(d) for d in excluded)


def test_acceptable_blocks_hard_avoid():
    assert repertoire._acceptable({"title": "Учебная глазурь", "protein": "", "veg": []}) is False
    assert repertoire._acceptable({"title": "Учебные клёцки", "protein": "", "veg": []}) is True
