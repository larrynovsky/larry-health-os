"""Гаситель отложенного решения о партнёрском кране литературы (15.09).

Класс, ради которого датчик существует: обещание «вернёмся, когда приём станет
ненулевым» без носителя живёт ложью столько, сколько его никто не перечитывает.
В этом проекте рекорд — 49 дней.
"""
import health_db  # noqa: F401  ПЕРВЫМ: круговой импорт config_db↔health_db

from integrity_tests import literature_partner_gate as gate


def test_silent_while_owner_acceptance_is_zero():
    """Без принятого предложения гейт молчит."""
    assert gate(0, False) is None


def test_speaks_once_the_first_proposal_is_approved():
    r = gate(1, False)
    assert r and "завести можно" in r and "ПРЯМАЯ" in r


def test_silent_once_the_partner_tap_already_exists():
    """Второе гашение: кран заведён — поводу больше неоткуда взяться."""
    assert gate(5, True) is None
