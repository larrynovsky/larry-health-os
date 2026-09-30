"""Характеризация owner_gate — LIVE-часть §13-клаузы. Оракул краснеет, если
классификатор перестанет паковать домен владельца ИЛИ начнёт паковать чистые
dev-действия (over-escalation, что ломает §13). Чистая функция, БД не трогает."""
import pytest

from owner_gate import requires_owner


def test_unknown_category_parks_failclosed():
    assert requires_owner({})[0] is True
    assert requires_owner({"category": "promote_canon"})[0] is True


def test_write_to_owner_domain_parks_even_if_auto_category():
    for tbl in ("lab_results", "lab_domain_verdicts", "health.db:lab_results_staging"):
        parked, why = requires_owner({"category": "dev_fix", "writes": [tbl]})
        assert parked is True, f"запись в {tbl} обязана париться"
        assert "домен владельца" in why


def test_irreversible_always_parks():
    assert requires_owner({"category": "dev_fix", "irreversible": True})[0] is True


def test_clean_dev_fix_is_auto():
    parked, why = requires_owner(
        {"category": "dev_fix", "writes": ["tests/unit/test_x.py", "foo.py"]})
    assert parked is False and why is None


def test_reading_canon_is_not_parking():
    """§13 over-escalation guard: диагностическое ДЕЙСТВИЕ без записи в домен
    (только чтение канона) НЕ паркуется — иначе тренируем banner-blindness."""
    parked, _ = requires_owner({"category": "dev_fix", "writes": []})
    assert parked is False, "чтение канона без записи не должно париться"


@pytest.mark.parametrize("cat", ["dev_fix", "test_fix", "ops_config"])
def test_auto_categories_pass_when_clean(cat):
    assert requires_owner({"category": cat, "writes": ["x.py"]})[0] is False


def test_agent_cannot_upgrade_only_downgrade():
    """Даже 'чистая' авто-категория с записью в канон уходит в парк: агент
    структурно НЕ может повысить до авто то, что трогает домен владельца."""
    assert requires_owner({"category": "ops_config", "writes": ["lab_results"]})[0] is True
