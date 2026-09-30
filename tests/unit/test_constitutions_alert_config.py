"""C4 (audit 2026-06-17): alert-config для ревью конституций берётся из БД,
а не парсингом telegram_bot.py (тихий обрыв после переезда конфига в БД).

Тестируем вынесенную функцию _build_alert_config_excerpt(). Качество/смысл
итоговой конституции НЕ тестируем (нет оракула).
"""
import pytest

import generate_constitutions as gc

pytestmark = pytest.mark.unit


def test_excerpt_nonempty_when_db_has_config(monkeypatch):
    """Засеяны domain_signals + thresholds → врезка непустая, с метками доменов и порогов.
    Оракул: вывод get_domain_signals/get_absolute_thresholds."""
    import health_db
    monkeypatch.setattr(health_db, "get_domain_signals",
                        lambda dom: [{"metric": "rmssd", "r": -0.5, "threshold_pct": 20}]
                        if dom == "stress" else [])
    monkeypatch.setattr(health_db, "get_absolute_thresholds",
                        lambda: [{"metric": "hrv", "direction": "<", "value": 17}])

    excerpt = gc._build_alert_config_excerpt()
    assert "DOMAIN_SIGNALS" in excerpt
    assert "stress" in excerpt and "rmssd" in excerpt
    assert "Абсолютные пороги" in excerpt and "hrv" in excerpt


def test_excerpt_empty_on_empty_db_no_exception(monkeypatch):
    """Пустая БД → пустая строка, без исключения (не падает на регене)."""
    import health_db
    monkeypatch.setattr(health_db, "get_domain_signals", lambda dom: [])
    monkeypatch.setattr(health_db, "get_absolute_thresholds", lambda: [])
    assert gc._build_alert_config_excerpt() == ""


def test_excerpt_diagnostic_on_db_error(monkeypatch):
    """Ошибка чтения → диагностическая строка, не падение."""
    import health_db
    def _boom(*a, **k):
        raise RuntimeError("db gone")
    monkeypatch.setattr(health_db, "get_domain_signals", _boom)
    out = gc._build_alert_config_excerpt()
    assert "не удалось" in out


def test_no_stale_telegram_bot_parse_regression():
    """Регрессия-сторож: generate_constitutions больше не парсит блоки текстом.
    Проверяем по однозначным маркерам старого парсинга (а не по упоминанию в
    комментариях). Ловит возврат тихого обрыва."""
    from pathlib import Path
    src = Path(gc.__file__).read_text(encoding="utf-8")
    assert "bot_path" not in src, "вернулась переменная bot_path (парсинг файла бота)"
    assert "DOMAIN_SIGNALS = {" not in src, "вернулся текстовый парсинг блока DOMAIN_SIGNALS"
    assert "ABSOLUTE_FLOORS = [" not in src, "вернулся текстовый парсинг блока ABSOLUTE_FLOORS"
