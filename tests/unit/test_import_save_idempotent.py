"""save() идемпотентна по каноническому имени (BL-LAB-CANON-1 Ш4).

РАНЬШЕ при существующем файле добавлялся суффикс _N → повторные прогоны плодили
тысячи копий (13 555 файлов в biochemical/) → каждый импортировался как отдельный
source → write-write конфликты в каноне. Теперь перезапись: повторный импорт
идемпотентен, генератор дублей заглох.
"""
import json


def test_save_overwrites_not_proliferates(tmp_path):
    import import_all
    p = tmp_path / "2021-06-14_CBC_chemistry.json"
    import_all.save(p, {"v": 1})
    import_all.save(p, {"v": 2})   # повторный прогон той же даты/типа
    import_all.save(p, {"v": 3})
    files = sorted(x.name for x in tmp_path.glob("2021-06-14_CBC_chemistry*.json"))
    assert files == ["2021-06-14_CBC_chemistry.json"]   # НЕ _2/_3
    assert json.loads(p.read_text())["v"] == 3          # last-write-wins


def test_save_returns_canonical_path(tmp_path):
    import import_all
    p = tmp_path / "2026-02-09_chemistry.json"
    out = import_all.save(p, {"v": 1})
    assert out == p                                     # не мигрирует в _N
