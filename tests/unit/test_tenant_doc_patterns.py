"""A3 (нить diagnosis-hardcode): per-tenant PII-паттерны классификатора вне общего кода.

Общий сид не содержит owner-PII (имена врачей/фамилия); загрузчик читает per-tenant yaml.
Импортит health_db → гоняется на Studio-снапшоте (§8-гард пути на MacBook).
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import health_db as db


@pytest.mark.owner_data
def test_generic_seed_has_no_owner_pii():
    pats = {(r[0] or "").lower() for r in db._GENERIC_DOC_PATTERNS}
    # Личные слова — из словаря pii_census (приватная зона), не литералами в тесте.
    import pii_census
    probes = pii_census.literals(["doctor", "surname"])
    assert probes, "словарь pii_census пуст — проверка была бы пустой"
    for pii in probes:
        assert not any(pii.lower() in p for p in pats), "owner-PII из словаря вернулась в общий сид"
    # у общих вендоров нет specialist_name (имя врача = PII)
    assert all(r[4] is None for r in db._GENERIC_DOC_PATTERNS), "specialist_name в общем сиде = PII-утечка"


def test_load_tenant_patterns_parses(tmp_path, monkeypatch):
    import yaml
    p = tmp_path / "tenant_doc_patterns.yaml"
    p.write_text(yaml.safe_dump(
        [{"pattern": "drwho", "doc_type": "visit", "specialist_name": "Who", "notes": "x"}],
        allow_unicode=True), encoding="utf-8")
    monkeypatch.setattr(db, "TENANT_DOC_PATTERNS_PATH", p)
    rows = db._load_tenant_doc_patterns()
    assert rows and rows[0][0] == "drwho" and rows[0][4] == "Who"


def test_load_tenant_patterns_absent_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "TENANT_DOC_PATTERNS_PATH", tmp_path / "nope.yaml")
    assert db._load_tenant_doc_patterns() == []


def test_generic_seed_is_empty():
    """Решение владельца 29.09 (вариант «б»): лаборатории и тесты установки — данные тенанта
    (tenant_doc_patterns.yaml), не общий сид в публичном коде. Непустой общий сид снова
    выдал бы постороннему географию и вид лечения владельца."""
    assert db._GENERIC_DOC_PATTERNS == []
