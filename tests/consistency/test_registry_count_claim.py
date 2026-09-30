"""Число записей реестра, заявленное в своде, обязано совпадать с реестром.

Свод (`CLAUDE.md`) говорит «`subsystem_intent.yaml` (24 записи, замер <дата>)». Это
утверждение о ДАННЫХ, которые меняются, и до сих пор у него не было сторожа: 12.09
обнаружилось, что там стояло 18 при фактических 24 — расхождение прожило незамеченным
и было найдено человеком в чужой задаче, а не механизмом.

Класс — §18 (у утверждения в коде есть временной класс): «18 записей» это не константа,
а замер, и событие-гаситель у него есть — добавление записи в реестр. Событие есть,
гашения не было.

Тест судит СВЯЗЬ числа с реестром, а не его величину: добавил запись — поправь свод
(и дату замера), иначе красный. Дата здесь не проверяется на свежесть намеренно: её
смысл в том, чтобы читатель видел, КОГДА мерили, а не в том, чтобы мерить часто.
"""
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

_CLAIM_RE = re.compile(r"subsystem_intent\.yaml`?\s*\((\d+)\s+запис")


def _claimed_count() -> int | None:
    text = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    m = _CLAIM_RE.search(text)
    return int(m.group(1)) if m else None


def _actual_count() -> int:
    data = yaml.safe_load((ROOT / "subsystem_intent.yaml").read_text(encoding="utf-8"))
    subs = data["subsystems"] if isinstance(data, dict) and "subsystems" in data else data
    return len(subs)


def test_claim_exists_at_all():
    """Отсутствие утверждения тоже красное: молча убрать число — способ обойти сторож."""
    assert _claimed_count() is not None, (
        "в CLAUDE.md нет утверждения «subsystem_intent.yaml (N записей)» — "
        "либо верни его, либо сними этот тест осознанно")


def test_claimed_count_matches_registry():
    claimed, actual = _claimed_count(), _actual_count()
    assert claimed == actual, (
        f"свод заявляет {claimed} записей реестра, в subsystem_intent.yaml их {actual}. "
        f"Поправь строку в CLAUDE.md вместе с датой замера.")


if __name__ == "__main__":
    assert _claimed_count() == _actual_count(), "свод и реестр разошлись"
    print(f"ok: {_actual_count()} записей, свод согласен")
