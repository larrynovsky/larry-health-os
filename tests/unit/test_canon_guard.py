"""tests/unit/test_canon_guard.py — предохранитель «не трогай канон» (Р-8, 2026-07-29).

ЗАЧЕМ. Решение владельца Р-7: живые фазы проб исполняются на снимке канона в
`~/health_staging`, а не на боевой БД. До предохранителя это держалось на памяти
запускающего — проба не спрашивала разрешения и не печатала, куда пишет. За один
день 2026-07-29 пробы трижды запускались по канону через PYTHONPATH; работало
потому, что так и хотели. Правило без механизма — ровно тот класс, ради которого
начата нить `validation-gate-repair`.

ГРАНИЦА, ПЕРЕСМОТРЕННАЯ 2026-07-29 (замер У-2). Здесь стояло: «гард сравнивает путь и
не отличит боевую БД под другим путём; закрыть значило бы сверять содержимое, то есть
читать боевую базу ради проверки, что мы её не читаем». **Это было неверно** и стоило
полдня открытой дыры: жёсткая ссылка на боевой файл проходила гард, а запись шла в
канонический inode. Достаточно `stat` — устройство и inode, метаданные, байты не
читаются. Граница была названа честно и оценена дороже, чем стоит; «названо в limits» не
равно «взвешено правильно».

Тождество файла теперь проверяется и покрыто `tests/unit/test_canon_guard_hardlink.py`.
Что остаётся вне охвата НАМЕРЕННО: копия канона под чужим именем — это и есть штатный
снимок (Р-7), запись в него канона не касается.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

ENV = "HEALTH_ALLOW_PROBE_ON_CANON"


def test_staging_path_passes(monkeypatch):
    """На снимке гард молчит — иначе он заблокировал бы штатный путь."""
    import health_db
    monkeypatch.setattr(health_db, "DB_PATH", Path.home() / "health_staging/data/health.db")
    assert health_db.on_canonical_db() is False
    health_db.assert_not_canonical("проба", allow_env=ENV)      # не бросает


def test_canonical_path_is_refused(monkeypatch):
    """⭐ Тот самый случай: процесс смотрит в боевую БД → отказ, а не предупреждение.
    Мутация «поменять raise на log.warning» обязана уронить этот тест."""
    import health_db
    monkeypatch.setattr(health_db, "DB_PATH", health_db.CANONICAL_DB_PATH)
    monkeypatch.delenv(ENV, raising=False)
    assert health_db.on_canonical_db() is True
    with pytest.raises(RuntimeError, match="ОТКАЗ"):
        health_db.assert_not_canonical("проба", allow_env=ENV)


def test_refusal_names_the_way_out(monkeypatch):
    """Отказ без рецепта — это стена. Сообщение обязано назвать и путь запуска,
    и имя переменной обхода: иначе следующий человек начнёт искать обход сам."""
    import health_db
    monkeypatch.setattr(health_db, "DB_PATH", health_db.CANONICAL_DB_PATH)
    monkeypatch.delenv(ENV, raising=False)
    with pytest.raises(RuntimeError) as e:
        health_db.assert_not_canonical("проба", allow_env=ENV)
    msg = str(e.value)
    assert "health_staging" in msg and ENV in msg and "test_on_studio" in msg


def test_explicit_env_opens_the_door(monkeypatch):
    """Обход существует и он ЯВНЫЙ. Без него владелец не сможет санкционировать
    канон-прогон, и правило превратится в запрет, который обходят правкой кода."""
    import health_db
    monkeypatch.setattr(health_db, "DB_PATH", health_db.CANONICAL_DB_PATH)
    monkeypatch.setenv(ENV, "1")
    health_db.assert_not_canonical("проба", allow_env=ENV)      # не бросает


@pytest.mark.owner_data
def test_probes_actually_call_the_guard():
    """Позитивный контроль на ПРОВОДКУ. Сам по себе гард ничего не стережёт, пока
    его не зовут; тесты выше проверяют функцию, а не то, что пробы её используют.
    Без этой проверки можно было бы удалить вызов из пробы и остаться зелёным."""
    probes = {
        "plans/probe_labs_scope_enforced_2026-07-28.py": "labs_scope_enforced, фаза live",
        "plans/probe_failure_never_publishes_2026-07-27.py": "failure_never_publishes",
    }
    for rel, marker in probes.items():
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert "assert_not_canonical" in src, f"{rel}: предохранитель не подключён"
        assert marker in src, f"{rel}: вызов есть, но не называет, что именно защищает"
        assert "on_canonical_db" in src, f"{rel}: не печатает, где находится"
