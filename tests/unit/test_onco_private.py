"""Онко-контур тенанта — данные, не код (решение владельца 2026-09-23).
Краснеет, если импликации класса перестанут приходить из свода или сторож egress
снова будет знать препараты только списком (а не по суффиксу МНН)."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def test_класс_тенанта_приносит_лабы_и_термин_из_свода(tmp_path, monkeypatch):
    import clinical_kb
    idx = tmp_path / "_index.yaml"
    idx.write_text(
        "conditions:\n"
        "  - id: zz_class\n"
        "    trigger: {problem_regex: 'zz'}\n"
        "    lit_term: 'zz recovery'\n"
        "    labs:\n"
        "      Zinc: {days: 120, priority: high}\n", encoding="utf-8")
    monkeypatch.setattr(clinical_kb, "_INDEX_YAML", idx)
    clinical_kb._condition_attrs_raw.cache_clear()
    try:
        assert clinical_kb.condition_attr("lit_term") == {"zz_class": "zz recovery"}
        import labs_db
        assert labs_db.effective_freshness({"zz_class"})["Zinc"]["priority"] == "high"
        assert "Zinc" not in labs_db.effective_freshness(set())
    finally:
        clinical_kb._condition_attrs_raw.cache_clear()


def test_нет_свода_нет_импликаций(tmp_path, monkeypatch):
    import clinical_kb
    monkeypatch.setattr(clinical_kb, "_INDEX_YAML", tmp_path / "absent.yaml")
    clinical_kb._condition_attrs_raw.cache_clear()
    try:
        assert clinical_kb.condition_attr("labs") == {}
    finally:
        clinical_kb._condition_attrs_raw.cache_clear()


@pytest.mark.parametrize("raw", ["pembrolizumab response", "docetaxel neuropathy",
                                 "gemcitabine outcomes", "after pancreatectomy"])
def test_egress_режет_препарат_и_операцию_по_классу(raw):
    import pubmed_client
    assert not pubmed_client.egress_safe(raw)


def test_egress_пропускает_класс_уровень():
    import pubmed_client
    assert pubmed_client.egress_safe("cancer survivorship sleep quality")
