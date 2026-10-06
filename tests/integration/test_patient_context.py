"""L1/L2: patient_context.build_patient_brief (hardcode-migration Ф1).

Профиль собирается ТОЛЬКО из БД; нет генома и порогов; DB-fail → нейтральный
маркер без литералов пациента.
"""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT))

import pytest

import health_db
import patient_context as pc

pytestmark = pytest.mark.integration


def _seed_profile():
    health_db.upsert_profile("identity.name", value_text="Тест Пациентов", category="identity")
    health_db.upsert_profile("identity.birth_date", value_text="1972-01-01", category="identity")
    health_db.upsert_profile("identity.location", value_text="Исландия", category="identity")
    health_db.upsert_profile("medical.diagnosis", value_text="Диагноз X", category="medical")
    health_db.upsert_profile("medical.treatment_status", value_text="observation_only", category="medical")
    health_db.upsert_profile("medical.oncologist", value_text="Доктор Y", category="medical")
    health_db.upsert_profile("routine.nurosym", value_text="20 мин/день", category="routine")


def test_brief_from_db(db):
    _seed_profile()
    brief = pc.build_patient_brief()
    assert "Тест Пациентов" not in brief       # имя в модель не уходит (нить identity-out-of-llm)
    assert "лет" in brief                      # возраст вычислен из birth_date
    assert "Исландия" in brief
    assert "Диагноз X" in brief
    assert "Доктор Y" in brief
    # строка routine-устройства в брифе больше не показывается — строка в профиле не читается
    assert "Вагусная стимуляция" not in brief


def test_brief_reflects_db_change_read_your_writes(db):
    _seed_profile()
    assert "Диагноз X" in pc.build_patient_brief()
    health_db.upsert_profile("medical.diagnosis", value_text="Диагноз Z", category="medical")
    assert "Диагноз Z" in pc.build_patient_brief()  # без рестарта/кэша


def test_no_genome_no_thresholds_in_brief(db):
    """Бриф НЕ содержит генома и клинических порогов (они инжектятся/живут отдельно)."""
    _seed_profile()
    brief = pc.build_patient_brief()
    for forbidden in ("APOE", "CYP2C19", "rs", "HRV<", "ratio>", "readiness<", "Pathogenic"):
        assert forbidden not in brief, forbidden


def test_degraded_mode_neutral_marker(db, monkeypatch):
    """DB недоступна → нейтральный маркер, без raise, без литералов пациента."""
    def boom():
        raise RuntimeError("DB down")
    monkeypatch.setattr(health_db, "get_profile_context", boom)
    brief = pc.build_patient_brief()
    assert "недоступ" in brief.lower()
    import pii_census
    for forbidden in pii_census.literals(["owner_clinical", "surname"]):   # досье — из приватного словаря
        assert forbidden not in brief


def test_missing_keys_no_literals(db):
    """Пустой профиль (минимум ключей) → нейтрально, без хардкод-дефолтов владельца.

    brief-neutralization Фаза 0: изменение поведения — раньше пустые поля давали заглушку '—';
    теперь рамка Диагноз/Онкостатус/PET-CT/Онколог ОПУСКАЕТСЯ целиком (не рендерится не-онко
    тенанту). Это строже, чем '—': болезнь-рамка вообще не навязывается."""
    health_db.upsert_profile("identity.name", value_text="Кто-то", category="identity")
    brief = pc.build_patient_brief()
    assert "Кто-то" not in brief      # имя в модель не уходит (нить identity-out-of-llm)
    # онко/диагноз-рамка отсутствует при пустых полях (движок нейтрален)
    assert "Онкостатус" not in brief
    assert "PET-CT" not in brief
    assert "Онколог" not in brief
    import pii_census
    for forbidden in pii_census.literals(["owner_clinical"]):
        assert forbidden not in brief
