"""R2 (multitenancy Phase 0, 2026-06-29): единый источник пути к health.db.

vcf_import_pipeline исторически считал DB_PATH сам — Path(HEALTH_DATA_DIR)/"health.db",
без /data/ — что при ОДНОЙ И ТОЙ ЖЕ переменной HEALTH_DATA_DIR давало ДРУГОЙ файл,
чем канонический health_db.DB_PATH (split-brain hazard, см. ADR multitenancy §R2).

Этот тест — датчик: модули, пишущие в health.db, обязаны брать путь из health_db,
а не вычислять свою ветку. Уровень: consistency. Канонический прогон — на Studio
(где доступны тяжёлые зависимости vcf_import).
"""
import pytest

pytestmark = pytest.mark.consistency


def test_vcf_import_uses_canonical_db_path():
    """vcf_import_pipeline.DB_PATH идентичен health_db.DB_PATH, а не своя ветка."""
    pytest.importorskip("requests")  # vcf_import тянет requests + научный стек
    import health_db
    import vcf_import_pipeline

    assert vcf_import_pipeline.DB_PATH == health_db.DB_PATH, (
        f"vcf_import.DB_PATH={vcf_import_pipeline.DB_PATH} != "
        f"health_db.DB_PATH={health_db.DB_PATH} — расхождение пути = split-brain"
    )
