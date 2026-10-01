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


def test_container_sees_the_tap_through_the_repo_copy(tmp_path):
    """01.10: в контейнере владельца плистов партнёра нет — кран, включённый 27.09, читался
    как не заведённый, и владельцу снова пришло «включить?»."""
    from integrity_tests import partner_tap_present as present
    agents, repo = tmp_path / "agents", tmp_path / "launchd"
    agents.mkdir(); repo.mkdir()
    (repo / "com.larry.health.literature-search.partner.plist").write_text("x")
    assert present(agents, repo, in_container=True) is True
    assert present(agents, repo, in_container=False) is False   # на хосте судит установленный
