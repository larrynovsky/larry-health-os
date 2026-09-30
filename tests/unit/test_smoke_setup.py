"""
Минимальный smoke-тест инфраструктуры: убеждаемся, что pytest вообще
запускается и видит модули проекта через sys.path в conftest.py.
"""
import pytest

pytestmark = pytest.mark.unit


def test_can_import_health_db():
    """sys.path setup в conftest.py работает: health_db импортируется."""
    import health_db  # noqa: F401


def test_can_import_time_inject():
    import _time_inject  # noqa: F401


def test_can_import_gp_agent():
    """gp_agent зависит от health_db и _time_inject — проверяем цепочку."""
    import gp_agent  # noqa: F401


def test_pytest_marker_unit_works():
    """Этот тест помечен @pytestmark unit — запустится при `pytest -m unit`."""
    assert True
