"""
_time_inject.py — единая точка инъекции времени для тестируемости.

Все вызовы `datetime.now()` / `date.today()` в **логике агентов** должны идти
через `get_now()` / `get_today()` этого модуля. Тест подменяет внутреннее
состояние через `set_test_clock(when)`; продакшен видит реальное время.

CLI-блоки `if __name__ == "__main__":` не обязаны использовать этот модуль —
тест вызывает функции напрямую с фикс-датами, минуя CLI.

Ссылки:
- USE_CASES.md UC-D-05 (устаревшие labs/genome → пометка даты)
- TESTING_CONTRACTS.md §1 (свежесть данных, conit-контракты)
- TEST_ARCHITECTURE.md §4.2 (fixture time_travel)

Использование в коде:
    from _time_inject import get_now, get_today
    today = get_today()
    now = get_now(TZ)

Использование в тесте (pytest fixture):
    from _time_inject import set_test_clock, clear_test_clock
    set_test_clock("2026-05-08")
    try:
        ...  # код видит "2026-05-08" как сегодня
    finally:
        clear_test_clock()

КОНТРАКТ (enforced, 2026-07-21):
    Вся recency-логика агентов читает время ТОЛЬКО через этот модуль:
      datetime.now(tz) → get_now(tz);  date.today() → get_today();
      datetime.utcnow() → get_utcnow()  (НЕ get_now() — то локальное, сдвиг на tz).
    Прямой вызов вне seam ловит time_contract_sensor.py (чек в integrity_tests):
    новое → FAIL, унаследованное (baseline) → WARN. Осознанное исключение —
    маркер `# time-inject: ok` в строке. Почему: docs/explanation/time-inject-contract.md.
"""
from __future__ import annotations

from datetime import datetime, date, tzinfo
from typing import Optional, Union


# Внутреннее замороженное «сейчас». None = использовать реальное время.
_TEST_CLOCK: Optional[datetime] = None


def set_test_clock(when: Union[datetime, date, str, None]) -> None:
    """Установить фейковое 'сейчас' для теста.

    Args:
        when: точка времени:
            - datetime — точное значение (с tz или без)
            - date — начало этого дня (00:00, naive)
            - str — ISO формат: "2026-05-08" (день) или "2026-05-08T14:30" (datetime)
            - None — снять подмену (эквивалент clear_test_clock)
    """
    global _TEST_CLOCK
    if when is None:
        _TEST_CLOCK = None
        return
    if isinstance(when, str):
        try:
            _TEST_CLOCK = datetime.fromisoformat(when)
        except ValueError:
            d = date.fromisoformat(when)
            _TEST_CLOCK = datetime.combine(d, datetime.min.time())
        return
    if isinstance(when, datetime):
        _TEST_CLOCK = when
        return
    if isinstance(when, date):
        _TEST_CLOCK = datetime.combine(when, datetime.min.time())
        return
    raise TypeError(f"set_test_clock: unsupported type {type(when).__name__}")


def clear_test_clock() -> None:
    """Снять подмену; вернуть real time."""
    global _TEST_CLOCK
    _TEST_CLOCK = None


def is_frozen() -> bool:
    """True если активна подмена (для отладочных проверок в тестах)."""
    return _TEST_CLOCK is not None


def get_now(tz: Optional[tzinfo] = None) -> datetime:
    """Эквивалент datetime.now(tz) с возможностью подмены.

    Если активна подмена через set_test_clock:
      - если frozen-значение naive и tz передан — добавляем tz без преобразования;
      - если frozen-значение aware и tz не передан — возвращаем без tzinfo;
      - иначе возвращаем как есть (предполагая совместимость).

    Если подмены нет — обычный datetime.now(tz).
    """
    if _TEST_CLOCK is not None:
        if tz is not None and _TEST_CLOCK.tzinfo is None:
            return _TEST_CLOCK.replace(tzinfo=tz)
        if tz is None and _TEST_CLOCK.tzinfo is not None:
            return _TEST_CLOCK.replace(tzinfo=None)
        return _TEST_CLOCK
    return datetime.now(tz)


def get_today() -> date:
    """Эквивалент date.today() с возможностью подмены."""
    if _TEST_CLOCK is not None:
        return _TEST_CLOCK.date()
    return date.today()


def get_utcnow() -> datetime:
    """Эквивалент datetime.utcnow() с возможностью подмены.

    Note: datetime.utcnow() deprecated в Python 3.12. Если код использует —
    оборачиваем; новый код должен использовать `get_now(timezone.utc)`.
    """
    if _TEST_CLOCK is not None:
        if _TEST_CLOCK.tzinfo is not None:
            from datetime import timezone
            return _TEST_CLOCK.astimezone(timezone.utc).replace(tzinfo=None)
        return _TEST_CLOCK
    return datetime.utcnow()
