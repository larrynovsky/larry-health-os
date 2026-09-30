"""Публичный модуль не импортирует приватную зону (27.09).

В открытой выгрузке приватной зоны нет: такой модуль падает на импорте, а тест — на сборке,
роняя весь прогон. Правило «тест разового скрипта plans/ — в приватную зону» держалось на
памяти и за три дня было забыто пять раз; на коммите его судит pii_census --staged, здесь —
всё дерево целиком.
"""
import pytest

pytestmark = pytest.mark.unit


def test_no_public_module_imports_private_zone():
    import pii_census
    leaks = pii_census.private_imports()
    assert not leaks, "публичные модули импортируют приватную зону: " + "; ".join(
        f"{p} → {', '.join(h)}" for p, h in sorted(leaks.items()))


def test_detector_sees_a_planted_import(tmp_path):
    """Негативный контроль в самом тесте: подложенный импорт plans/ обязан быть найден."""
    import pii_census
    if pii_census.is_public_export():
        pytest.skip("в открытой выгрузке приватной зоны нет — подкладывать нечего")
    tracked = pii_census._tracked(pii_census.ROOT)
    plan = next(p for p in sorted(tracked) if p.startswith("plans/") and p.endswith(".py")
                and p[len("plans/"):-3].isidentifier())
    mod = plan[:-3].replace("/", ".")
    src = {"tests/unit/test_x_public.py": f"from {mod.rsplit('.', 1)[0]} import {mod.rsplit('.', 1)[1]}\n"}
    found = pii_census.private_imports(paths=list(src), read=src.get)
    assert found == {"tests/unit/test_x_public.py": [plan]}
