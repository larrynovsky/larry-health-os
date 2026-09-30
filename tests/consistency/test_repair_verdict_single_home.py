"""Вердикт об авто-применении красного живёт в своде; дома — указывают, не пересказывают.

ЧТО УМЕРЛО. Поправка владельца 2026-08-03 (CLAUDE.md §13) сузила запрет на
авто-применение красного до трёх условий. Замер 2026-09-14: за 42 дня она дошла
до ОДНОЙ страницы из пяти. Четыре дома продолжали печатать прежний вердикт как
действующий, и `test_failure_handler.py` в своём docstring ссылался именно на
самый устаревший из них (`TEST_ARCHITECTURE.md §12.3`, «не реализуем»). То есть
читатель кода попадал ровно в тот текст, который решением владельца уже отменён.

ЧЕГО НЕ ЛОВИТ, вслух. Ни один датчик не поймал бы саму непроходку поправки в
день её написания — правку сделали в своде, носителей не трогали, и
`affected_claims` (связь «тронул носитель → перечитай §») молчал бы законно.
Этот файл ловит ДРУГОЕ и более узкое: дом, переставший указывать на свод, и
возврат отменённой формулировки. Плюс метка ⟨carriers:⟩ на поправке, добавленная
той же правкой, даёт вторую половину: теперь правка любого дома поднимает §13 на
коммите.

ПОЧЕМУ СПИСОК ДОМОВ НЕ ЗДЕСЬ. Он читается из метки ⟨carriers:⟩ на строке
поправки в своде. Вписать его константой значило бы завести третий дом того же
знания — ровно того класса, против которого этот файл и стоит (§18).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.consistency

ROOT = Path(__file__).resolve().parents[2]
SVOD = ROOT / "CLAUDE.md"

_MARK = re.compile(r"⟨carriers:(?P<body>[^⟩]*)⟩")
POINTER = re.compile(r"CLAUDE\.md[^\n]{0,4}§\s*13\b")

# Формулировки ОТМЕНЁННОГО вердикта: «запрещено навсегда», без условий поправки.
# Возврат любой из них в дом = дом снова спорит со сводом.
_REVOKED = (
    "Не реализуем без human-in-loop",
    "Repair отвергнут",
    "Repair C отвергнут",
    "НЕ ДЕЛАТЬ СЕЙЧАС",
    "НЕ ДЕЛАЕТСЯ",
)


def _amendment_carriers() -> list[str]:
    """Дома поправки — из строки свода, помеченной датой решения владельца."""
    for line in SVOD.read_text(encoding="utf-8").splitlines():
        if "2026-08-03" not in line or "Поправка" not in line:
            continue
        m = _MARK.search(line)
        if m:
            return [p.strip() for p in m.group("body").split(",") if p.strip()]
    return []


def test_every_home_points_at_the_svod():
    homes = _amendment_carriers()
    silent = []
    for rel in homes:
        text = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
        if not POINTER.search(text):
            silent.append(rel)
    assert not silent, (
        "Дом нормы об авто-применении красного не ссылается на CLAUDE.md §13:\n  "
        + "\n  ".join(silent)
        + "\nЛибо вернуть ссылку, либо снять файл из ⟨carriers:⟩ поправки в своде."
    )


def test_no_home_reprints_the_revoked_verdict():
    homes = _amendment_carriers()
    back = []
    for rel in homes:
        text = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
        for phrase in _REVOKED:
            if phrase in text:
                back.append(f"{rel} → «{phrase}»")
    assert not back, (
        "Отменённая формулировка вернулась в дом (поправка владельца 2026-08-03 "
        "сузила запрет, а не подтвердила его):\n  " + "\n  ".join(back)
    )


def test_perimeter_is_not_empty():
    """Позитивный контроль: пустая метка дала бы вечно-зелёный прогон (§20)."""
    homes = _amendment_carriers()
    assert len(homes) >= 4, f"дома поправки не прочитаны из свода: {homes}"
    missing = [r for r in homes if not (ROOT / r).exists()]
    assert not missing, f"объявленный дом не существует: {missing}"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
