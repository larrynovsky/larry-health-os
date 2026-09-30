"""Клиент модели — только через llm_client (гард секретов по конструкции), 29.09."""
import pytest

pytestmark = pytest.mark.unit


def test_repository_has_no_direct_constructors():
    import integrity_tests as I
    assert I.check_llm_direct_constructors()["direct"] == 0


def test_a_direct_constructor_in_a_subdirectory_is_caught(tmp_path):
    import integrity_tests as I
    (tmp_path / "handlers").mkdir()
    (tmp_path / "handlers" / "x.py").write_text("import anthropic\nc = anthropic.Anthropic(api_key='k')\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "t.py").write_text("import anthropic\nanthropic.Anthropic()\n")
    (tmp_path / "llm_client.py").write_text("import anthropic\nanthropic.Anthropic()\n")
    with pytest.raises(AssertionError, match="handlers/x.py:2"):
        I.check_llm_direct_constructors(root=tmp_path)


def test_cbcr_client_builds_without_name_error(anthropic_mock):
    """a180e88 оставил в cbcr_hypothesis ссылку на удалённый ANTHROPIC_KEY_PATH — NameError."""
    import cbcr_hypothesis
    assert hasattr(cbcr_hypothesis._get_client(), "messages")
