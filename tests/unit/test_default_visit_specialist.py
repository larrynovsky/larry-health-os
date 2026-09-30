"""A1 (нить diagnosis-hardcode): дефолт /visit — из данных тенанта, не зашитый 'oncologist'.

Позит-контроль обоих направлений: ключ задан → его значение; ключ пуст → нейтральный 'general'.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import health_db  # noqa: F401 — импорт ПЕРВЫМ (config_db↔health_db циркуляр разрешается порядком)
import config_db


def test_tenant_with_key_gets_its_value(monkeypatch):
    monkeypatch.setattr(config_db, "get_config",
                        lambda k, d=None: "oncologist" if k == "default_visit_specialist" else d)
    assert config_db.default_visit_specialist() == "oncologist"


def test_tenant_without_key_falls_back_to_general(monkeypatch):
    # get_config возвращает дефолт (ключа нет) → резолвер обязан дать нейтральный 'general', не 'oncologist'.
    monkeypatch.setattr(config_db, "get_config", lambda k, d=None: d)
    got = config_db.default_visit_specialist()
    assert got == "general", f"тенант без ключа получил '{got}' вместо нейтрального general"
    assert got != "oncologist", "дефолт НЕ должен быть зашитым онкологом"
