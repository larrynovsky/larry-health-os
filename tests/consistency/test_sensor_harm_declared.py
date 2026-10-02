"""У каждого датчика integrity объявлено, кого задевает его поломка (нить repair-order, 02.10).

Читатель — night_repair.pick_cards: первое место ночи — самой вредной карточке. Датчик без пометки
молча стал бы «системой» и терял бы очередь; поэтому новый датчик обязан прийти с пометкой.
"""
import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _registered() -> list[str]:
    tree = ast.parse((ROOT / "integrity_tests.py").read_text(encoding="utf-8"))
    return [ast.unparse(n.value.args[1]) for n in tree.body
            if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
            and getattr(n.value.func, "id", None) == "check" and len(n.value.args) > 1]


def test_every_sensor_has_exactly_one_harm_tier():
    harm = json.loads((ROOT / "project_context" / "integrity_sensors.json").read_text(encoding="utf-8"))["harm"]
    assert set(harm) == {"person", "data", "system"}
    tiers = {}
    for tier, fns in harm.items():
        for fn in fns:
            assert fn not in tiers, f"{fn}: два уровня — {tiers[fn]} и {tier}"
            tiers[fn] = tier
    missing = sorted(set(_registered()) - set(tiers))
    assert not missing, f"датчики без пометки вреда (project_context/integrity_sensors.json, harm): {missing}"
    stale = sorted(set(tiers) - set(_registered()))
    assert not stale, f"пометка у датчика, которого нет: {stale}"
