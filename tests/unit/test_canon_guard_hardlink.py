"""Р-8 против ПОДМЕНЫ ФАЙЛА, не только имени (замер У-2, 2026-07-29).

ЗАЧЕМ ОТДЕЛЬНЫЙ ФАЙЛ. `test_canon_guard.py` проверяет гард по ПУТИ и делает это верно.
Здесь другой предмет: тождество файла. Замер У-2 на Studio дал четыре ответа —
канон напрямую отказ · снимок пустил · симлинк отказ · **жёсткая ссылка ПУСТИЛА**.
Последнее означало, что запись пойдёт в боевой inode, пока гард уверяет, что это снимок.

Почему дыра прожила полдня: в докстринге прежнего теста я написал, что закрыть её можно
«только сверкой содержимого, то есть чтением канона ради проверки, что мы его не читаем».
Это было неверно — достаточно `stat`. Граница была названа честно и оценена дороже, чем
стоит, и потому осталась дырой вместо починки. Отдельный урок: «названо в limits» не
равно «взвешено правильно».
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

ENV = "HEALTH_ALLOW_PROBE_ON_CANON"


def _fake_canon(tmp_path: Path) -> Path:
    """Файл, играющий роль канона. Настоящий канон в тестах не трогаем."""
    p = tmp_path / "canon" / "data" / "health.db"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"SQLite format 3\x00" + b"boevoe sostoyanie")
    return p


def test_hardlink_to_canon_is_recognised(tmp_path, monkeypatch):
    """⭐ ЗАМЕР У-2. Другой путь, тот же inode → это КАНОН, и гард обязан отказать.
    Мутация «убрать сверку inode» роняет этот тест и возвращает дыру."""
    import health_db

    canon = _fake_canon(tmp_path)
    link = tmp_path / "looks_like_staging" / "data" / "health.db"
    link.parent.mkdir(parents=True, exist_ok=True)
    os.link(canon, link)
    assert link.stat().st_ino == canon.stat().st_ino, "жёсткая ссылка не создалась"

    monkeypatch.setattr(health_db, "CANONICAL_DB_PATH", canon)
    monkeypatch.setattr(health_db, "DB_PATH", link)
    monkeypatch.delenv(ENV, raising=False)

    assert health_db.on_canonical_db() is True, \
        "жёсткая ссылка на боевой файл не распознана — запись пойдёт в канон"
    with pytest.raises(RuntimeError, match="ОТКАЗ"):
        health_db.assert_not_canonical("проба", allow_env=ENV)


def test_symlink_to_canon_is_recognised(tmp_path, monkeypatch):
    """Позитивный контроль на прежний признак: симлинк ловился путём и обязан ловиться
    дальше. Без него починка inode могла бы незаметно сломать разворот пути."""
    import health_db

    canon = _fake_canon(tmp_path)
    link_dir = tmp_path / "via_symlink"
    link_dir.symlink_to(canon.parent.parent)

    monkeypatch.setattr(health_db, "CANONICAL_DB_PATH", canon)
    monkeypatch.setattr(health_db, "DB_PATH", link_dir / "data" / "health.db")
    assert health_db.on_canonical_db() is True


def test_independent_copy_is_not_canon(tmp_path, monkeypatch):
    """⭐ ГРАНИЦА, и она НАМЕРЕННАЯ. Копия с теми же байтами — это штатный снимок (Р-7),
    и гард обязан её ПУСТИТЬ. Если бы этот тест краснел, гард запрещал бы ровно тот путь,
    который решением владельца объявлен единственно правильным."""
    import health_db

    canon = _fake_canon(tmp_path)
    copy = tmp_path / "health_staging" / "data" / "health.db"
    copy.parent.mkdir(parents=True, exist_ok=True)
    copy.write_bytes(canon.read_bytes())
    assert copy.read_bytes() == canon.read_bytes(), "копия не идентична — тест бессмыслен"
    assert copy.stat().st_ino != canon.stat().st_ino, "копия оказалась ссылкой"

    monkeypatch.setattr(health_db, "CANONICAL_DB_PATH", canon)
    monkeypatch.setattr(health_db, "DB_PATH", copy)
    monkeypatch.delenv(ENV, raising=False)

    assert health_db.on_canonical_db() is False, \
        "снимок принят за канон — гард запретил бы штатный путь проб"
    health_db.assert_not_canonical("проба", allow_env=ENV)      # не бросает


def test_missing_db_file_is_not_canon(tmp_path, monkeypatch):
    """Свежий staging, где БД ещё не скопирована. `stat` бросает → это НЕ канон.
    Обратное поведение заблокировало бы подготовку снимка."""
    import health_db

    monkeypatch.setattr(health_db, "CANONICAL_DB_PATH", _fake_canon(tmp_path))
    monkeypatch.setattr(health_db, "DB_PATH", tmp_path / "пусто" / "health.db")
    assert health_db.on_canonical_db() is False


def test_missing_canon_does_not_make_everything_canon(tmp_path, monkeypatch):
    """MacBook: канона нет вовсе. Тогда `_file_identity` обоих может быть None, и
    наивное сравнение None == None объявило бы каноном ЛЮБОЙ путь — гард заблокировал бы
    всё. Это ровно тот класс, что ловит оракул охвата: сторож, срабатывающий на пустом."""
    import health_db

    real = tmp_path / "staging" / "data" / "health.db"
    real.parent.mkdir(parents=True, exist_ok=True)
    real.write_bytes(b"snapshot")

    monkeypatch.setattr(health_db, "CANONICAL_DB_PATH", tmp_path / "нет" / "health.db")
    monkeypatch.setattr(health_db, "DB_PATH", real)
    assert health_db.on_canonical_db() is False
