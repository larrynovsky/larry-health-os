"""Оракул ДОСТАВКИ вердикта детектора захвата — второй половины механизма.

ПОЧЕМУ ОТДЕЛЬНО ОТ test_captured_files.py. Тот судит ПРИЗНАК: правильно ли
`git_facts.captured_files` отличает захват от своей работы. Этот судит, что
найденное ДОХОДИТ и что ненайденное не выдаётся за чистоту. Половины ломаются
независимо: 14.09 признак был построен и покрыт пятью тестами, а доставка —
`check_captured_files` — не имела ни одного, и именно в ней жил дефект.

ДЕФЕКТ, РАДИ КОТОРОГО ЭТО НАПИСАНО (найден 15.09). `captured_files` различает
три исхода: список имён (находка), `[]` (чисто) и `None` (судить не на чем —
у снимка нет причинного предка). Доставка писала `if поймано:` — и `[]`, и
`None` ложны, то есть «я не смотрел» выдавалось за «я посмотрел, чисто».
Замер того же дня показывает, насколько это не теория: в окне
`refs/backups/wip^..HEAD` лежит 0 из 63 суточных коммитов, то есть «не знаю» —
ОБЫЧНОЕ состояние этого датчика, а не край.

Второй дефект того же класса: `[:20]` резал список молча.

ГРАНИЦА ЧЕСТНО. Здесь не проверяется, верен ли сам признак — это дом соседнего
файла. Здесь проверяется только, что три исхода признака доезжают до человека
тремя разными сообщениями.
"""
from __future__ import annotations

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.test_meta(status="implemented", confirmation="confirmed"),
]


def _прогнать(monkeypatch, коммиты, вердикты):
    """Гоняет доставку на подставных ответах признака.

    `вердикты` — dict {сокращённый sha: список|[]|None}. Подставляем именно
    `captured_files`, потому что предмет теста — как доставка обходится с его
    тремя исходами, а не как он их получает.
    """
    import integrity_tests as it
    import git_facts as gf

    monkeypatch.setattr(gf, "_git",
                        lambda args, **kw: "\n".join(коммиты) if args[0] == "log" else None)
    monkeypatch.setattr(gf, "captured_files", lambda c, *a, **k: вердикты[c])
    it._warnings.clear()
    итог = it.check_captured_files()
    return итог, [w[0] for w in it._warnings]


def test_находка_доезжает(monkeypatch):
    итог, преду = _прогнать(monkeypatch, ["aaa1"], {"aaa1": ["чужой.py"]})
    assert итог["suspects"] == {"aaa1": ["чужой.py"]}
    assert any("захват" in w for w in преду), преду
    assert "чужой.py" in " ".join(str(w) for w in преду) or итог["suspects"]


def test_чисто_молчит(monkeypatch):
    """`[]` — это вердикт «чисто», и он обязан быть тихим."""
    итог, преду = _прогнать(monkeypatch, ["bbb1", "bbb2"], {"bbb1": [], "bbb2": []})
    assert итог["suspects"] == {}
    assert итог["unjudged"] == []
    assert преду == [], f"чистый прогон не должен ничего говорить: {преду}"


def test_не_знаю_не_выдаётся_за_чисто(monkeypatch):
    """ГЛАВНЫЙ ТЕСТ ФАЙЛА. `None` ≠ `[]`.

    Мутация «вернуть `if поймано:`» краснеет здесь: несудимые коммиты уедут в
    тишину, которую человек прочитает как «захвата нет».
    """
    итог, преду = _прогнать(monkeypatch, ["ccc1", "ccc2"], {"ccc1": None, "ccc2": []})
    assert итог["unjudged"] == ["ccc1"], итог
    assert any("не смог судить" in w for w in преду), (
        f"датчик промолчал о том, что он НЕ СУДИЛ коммит — это читается как "
        f"«чисто»: {преду}")


def test_все_коммиты_несудимы_громче_всего(monkeypatch):
    """Замеренное состояние этого датчика в бою — 0 судимых из 63."""
    коммиты = [f"d{i:03d}" for i in range(5)]
    итог, преду = _прогнать(monkeypatch, коммиты, {c: None for c in коммиты})
    assert итог["suspects"] == {}
    assert len(итог["unjudged"]) == 5
    assert any("не смог судить" in w for w in преду), преду


def test_срез_называется_вслух(monkeypatch):
    """Потолок в 20 коммитов законен, его молчание — нет."""
    коммиты = [f"e{i:03d}" for i in range(25)]
    итог, преду = _прогнать(monkeypatch, коммиты, {c: [] for c in коммиты})
    assert итог["commits_checked"] == 20
    assert итог["commits_total"] == 25
    assert итог["truncated"] is True
    assert any("из 25" in w for w in преду), (
        f"хвост в 5 коммитов не судился и об этом не сказано: {преду}")


def test_без_ленты_снимков_вердикта_нет_вообще(monkeypatch):
    """Ленты нет — функция возвращает None, а не пустой отчёт.

    Пустой отчёт в артефакте выглядел бы как проведённая проверка.
    """
    import integrity_tests as it
    import git_facts as gf
    monkeypatch.setattr(gf, "_git", lambda *a, **kw: None)
    it._warnings.clear()
    assert it.check_captured_files() is None
