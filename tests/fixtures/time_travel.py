"""
tests/fixtures/time_travel.py — pytest fixture для контроля времени.

Обёртка над `_time_inject` с удобным API. После теста время автоматически
размораживается (благодаря autouse в `tests/conftest.py`).

Использование:

    def test_stale_data_warning(clock):
        clock.set("2026-05-08")
        # код видит "2026-05-08" как сегодня
        clock.advance(days=1)
        # теперь видит "2026-05-09"
        assert clock.today() == date(2026, 5, 9)

Альтернативно — без fixture, через прямые вызовы:

    from _time_inject import set_test_clock, get_today
    set_test_clock("2026-05-08")
    assert get_today() == date(2026, 5, 8)
    # autoreset снимает после теста

Fixture удобнее для серий шагов (advance, jump_to) и читаемее в Then-блоках.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, tzinfo
from typing import Optional, Union

import pytest

from _time_inject import (
    clear_test_clock,
    get_now,
    get_today,
    is_frozen,
    set_test_clock,
)


class Clock:
    """Pytest-friendly wrapper над `_time_inject`."""

    def set(self, when: Union[str, date, datetime]) -> None:
        """Установить «сейчас». Поддерживает str ISO, date, datetime."""
        set_test_clock(when)

    def freeze_at(self, when: Union[str, date, datetime]) -> None:
        """Алиас для `set` — для читаемости в Given-блоках."""
        set_test_clock(when)

    def advance(self, *, days: int = 0, hours: int = 0, minutes: int = 0,
                seconds: int = 0) -> None:
        """Прокрутить замороженное время вперёд (или назад с минусом).

        Если время не было заморожено — берёт реальное «сейчас» как старт.
        """
        delta = timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)
        current = get_now()
        if not is_frozen():
            # Если ещё не заморожено — замораживаем от now()
            set_test_clock(current + delta)
            return
        set_test_clock(current + delta)

    def jump_to(self, when: Union[str, date, datetime]) -> None:
        """Алиас для set, выразительнее для прыжка во времени."""
        set_test_clock(when)

    def today(self) -> date:
        return get_today()

    def now(self, tz: Optional[tzinfo] = None) -> datetime:
        return get_now(tz)

    def yesterday(self) -> date:
        return get_today() - timedelta(days=1)

    def days_ago(self, n: int) -> date:
        return get_today() - timedelta(days=n)

    def days_ahead(self, n: int) -> date:
        return get_today() + timedelta(days=n)

    def thaw(self) -> None:
        """Снять подмену вручную (autoreset делает это после теста)."""
        clear_test_clock()

    @property
    def is_frozen(self) -> bool:
        return is_frozen()


@pytest.fixture
def clock() -> Clock:
    """Pytest fixture: возвращает Clock-объект для контроля времени.

    Не замораживает время автоматически — тест должен вызвать `clock.set(...)`
    или `clock.freeze_at(...)`. После теста — размораживается через autouse.
    """
    return Clock()
