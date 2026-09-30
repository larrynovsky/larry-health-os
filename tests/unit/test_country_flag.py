"""Флаг локации строится из кода страны, а не из списка стран (списка поездок)."""
from handlers.messages import country_flag


def test_flag_from_code_and_fallback():
    assert country_flag("jp") == "\U0001F1EF\U0001F1F5"
    assert country_flag("") == country_flag(None) == country_flag("X1") == country_flag("ÄB") == "📍"
