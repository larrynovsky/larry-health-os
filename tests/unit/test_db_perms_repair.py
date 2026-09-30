"""Авто-ремонт прав на .db (§13 ступень 1, введён 2026-08-07).

Почему отдельный дом, а не строка в test_security_sensors: там судят ЧТЕНИЕ прав
(нашёл ли сканер нарушителя), здесь — ДЕЙСТВИЕ (починил ли и оставил ли след).
Разные утверждения и разные способы сломаться.

Контекст находки: 12 файлов с 644 ехали владельцу warn'ом каждую ночь. Гипотеза
«чинить создателя» опровергнута чтением источника — `backup_studio.sh` уже делает
chmod 600 на каждый свой бэкап; нарушители это ad-hoc снапшоты §3, у которых
единого создателя нет. Поэтому сторож стоит на чтении прав, а не на записи.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import security_sensors as S

pytestmark = pytest.mark.unit


def _mk(home: Path, name: str, mode: int) -> Path:
    d = home / "health" / "backups"
    d.mkdir(parents=True, exist_ok=True)
    f = d / name
    f.write_bytes(b"x")
    f.chmod(mode)
    return f


def test_loose_perms_are_repaired_to_600(tmp_path):
    """Позитив: 644 → 600, файл назван в возврате (след рецидива)."""
    f = _mk(tmp_path, "snap.db", 0o644)
    repaired = S.repair_db_perms(tmp_path)
    assert str(f) in repaired, f"файл не починен: {repaired}"
    assert f.stat().st_mode & 0o777 == 0o600
    assert S._db_perms(tmp_path) == [], "после ремонта сканер обязан молчать"


def test_already_tight_is_not_touched(tmp_path):
    """Негативный контроль: 600 не попадает в «починено».

    Без него тест был бы зелёным и у реализации «chmod всем подряд и вернуть всех» —
    то есть счётчик рецидива печатал бы шум и перестал что-либо значить."""
    _mk(tmp_path, "tight.db", 0o600)
    assert S.repair_db_perms(tmp_path) == []


def test_non_db_files_are_not_touched(tmp_path):
    """Периметр ремонта — только .db. Мутация «чинить всё в backups/» обязана
    уронить этот тест: safe-предикат §13(a) обязан быть узким, а не удобным."""
    d = tmp_path / "health" / "backups"
    d.mkdir(parents=True)
    other = d / "notes.txt"
    other.write_text("x")
    other.chmod(0o644)
    S.repair_db_perms(tmp_path)
    assert other.stat().st_mode & 0o777 == 0o644


def test_repair_leaves_trace(tmp_path):
    """Конверт §13(d): ремонт обязан оставить след. Молчаливый авто-ремонт — это
    то же самое, что молчаливый отказ: и то и другое нельзя посчитать."""
    _mk(tmp_path, "snap.db", 0o666)
    seen: list[str] = []
    S.repair_db_perms(tmp_path, log=seen.append)
    assert seen and "snap.db" in seen[0], f"следа ремонта нет: {seen}"
