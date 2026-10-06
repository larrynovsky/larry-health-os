"""
tests/unit/test_brief_neutralization_phase0.py

Позитивные контроли нити brief-neutralization, Фаза 0 (заморозка межтенантных утечек).
RST: это CHECKS (детерминированные, на инвариант), не exploratory tests. Каждый фикс —
ДВА контроля: (нейтральность) не-онко тенант НЕ получает контента владельца; (безопасность)
детекция/значения владельца НЕ потеряны.

Северная звезда: код брифа — нейтральный движок; личные клинические факты — только в данных
тенанта. Ни один личный литерал не зашит в код/промпт.
"""
from __future__ import annotations

from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent

import pii_census as _pii

# Личные формулировки владельца (диагноз, препараты, даты, «восстановление после …») — из
# приватного словаря переписи (класс owner_clinical), не литералами в публичном тесте: прежний
# список сам по себе был досье (2026-09-25, нить regimen-scrub). Здесь — только нейтральные
# формулировки движка, которые не выдают, кто автор. У постороннего словаря нет — сторож
# проверяет только их.
_BANNED_PERSONAL_LITERALS = _pii.literals(["owner_clinical"]) + [
    "под ХТ",
    "исторический p10",
    "обсудить с онкологом",
    "дескриптор рецидива",
    "постлечебное восстановление",
]

_ENGINE_FILES = [
    "constitution_analysis.py",
    "_lifestyle_profile.py",
    "patient_context.py",
    "gp_context.py",
    "health_db.py",
    "publication_reader.py",   # A1 нейтрализован 2026-07-16: промпты per-tenant, без досье владельца
    "hai_hypotheses.py",       # A2 нейтрализован 2026-07-16: контекст протокола per-tenant
]


# ── СТОРОЖ-ГРЕП (plan §8.1a): личный литерал в коде-движке = fail ─────────────
@pytest.mark.parametrize("fname", _ENGINE_FILES)
def test_no_personal_literals_in_engine_source(fname):
    src = (_ROOT / fname).read_text(encoding="utf-8")
    hits = [lit for lit in _BANNED_PERSONAL_LITERALS if lit in src]
    assert not hits, f"{fname}: личный литерал(ы) владельца в коде-движке: {hits}"


# ── constitution_analysis: онко-секция гейтится по данным ────────────────────
def _domains(onco_has_data: bool):
    """Минимальный domains-dict для build_summary."""
    base_genome = {"genome_risk_count": 0, "genome_unverified_count": 0,
                   "genome_unverified": [], "genome_risk": []}
    return {
        "sleep": {"yearly_trend": {"2026": {"avg_sleep_h": 7.5, "n_days": 300,
                                            "deficit_h": 0.0, "avg_deep_min": 60}},
                  "worst_year": {"year": "2026", "avg_h": 7.5},
                  "note": "2026: средний сон 7.5ч/ночь", **base_genome,
                  "genome_risk_count": 0, "genome_unverified_count": 0},
        "movement": {"yearly_trend": {}, "rhr_change_total": 0.0,
                     "rhr_interpretation": "без изменений", "peak_activity_year": None,
                     **base_genome},
        "stress": {"hrv_monthly": {}, "rhr_by_treatment_period": {}, "note": "", **base_genome},
        "metabolism": {"marker_summary": {}, "key_flags": [], **base_genome},
        "oncology": {"has_data": onco_has_data,
                     "cea_timeline": ([{"date": "2026-01-01", "value": 3.0, "period": "baseline"}]
                                      if onco_has_data else []),
                     "ca199_timeline": [], "treatment_timeline": [],
                     "genome_oncology_risk_count": 0, "genome_unverified_count": 0,
                     "genome_unverified": [], "genome_oncology_risk": [],
                     "note": "CEA: 1 точек" if onco_has_data else "CEA: данных нет"},
    }


def test_summary_neutral_hides_onco_section_when_no_data():
    """Нейтральность: у не-онко тенанта нет онко-данных → секция и заголовок НЕ рендерятся."""
    import constitution_analysis as ca
    out = ca.build_summary(_domains(onco_has_data=False))
    assert "ОНКОЛОГИЯ" not in out
    assert "CEA" not in out
    assert "PET" not in out


def test_summary_renders_onco_section_when_data_present():
    """Безопасность: у онко-тенанта данные есть → секция рендерится (инвариант не потерян)."""
    import constitution_analysis as ca
    out = ca.build_summary(_domains(onco_has_data=True))
    assert "ОНКОЛОГИЯ" in out
    assert "CEA" in out


def test_analyze_oncology_note_is_data_derived(db):
    """Нейтральность+безопасность: note выводится из данных тенанта, без личного литерала."""
    import constitution_analysis as ca
    conn = db.conn()
    # Не-онко тенант: нет CEA → has_data False, note нейтрально «данных нет»
    res_empty = ca.analyze_oncology(conn)
    assert res_empty["has_data"] is False
    assert res_empty["note"] == "CEA: данных нет"
    # Онко-тенант: есть CEA → has_data True, note ссылается на ДАННЫЕ (не PET-литерал)
    db.add_lab_result("2026-05-01", "CEA", 3.2, status=None)
    res_data = ca.analyze_oncology(db.conn())
    assert res_data["has_data"] is True
    assert "3.2" in res_data["note"] and "PET" not in res_data["note"]


# ── _lifestyle_profile.build_profile_block ───────────────────────────────────
def test_profile_block_neutral_when_empty(db, monkeypatch):
    """Пустой профиль не наследует независимо придуманные маркеры предыдущего."""
    import health_db
    import _lifestyle_profile as lp
    markers = ("FIXTURE_GENE_Q", "fixture_condition_r", "fixture_device_s")
    monkeypatch.setattr(health_db, "get_profile_context",
                        lambda: {"medical": {"diagnosis": " ".join(markers)}})
    populated = lp.build_profile_block()
    assert all(marker in populated for marker in markers)
    monkeypatch.setattr(health_db, "get_profile_context", lambda: {})
    block = lp.build_profile_block()
    assert "профиль недоступен" not in block
    for banned in (*markers, *_pii.literals(["owner_clinical"])):
        assert banned not in block, "чужой маркер в пустом профиле"


def test_profile_block_shows_tenant_diagnosis(db):
    """Безопасность: диагноз тенанта задан → он в блоке (собственные данные не теряются)."""
    import _lifestyle_profile as lp
    db.add_profile("identity.name", value_text="Тест Тестов")
    db.add_profile("medical.diagnosis", value_text="Гипертония II ст.")
    block = lp.build_profile_block()
    assert "Гипертония II ст." in block
    assert "Тест Тестов" not in block      # имя в модель не уходит (нить identity-out-of-llm)


# ── patient_context.build_patient_brief ──────────────────────────────────────
def test_patient_brief_neutral_no_onco_frame(db):
    """Нейтральность: тенант без онко-данных → нет навязанной рамки Онкостатус/PET-CT/Онколог."""
    import patient_context as pc
    db.add_profile("identity.name", value_text="Тест Тестов")  # профиль непустой, но без онко
    brief = pc.build_patient_brief()
    assert "Онкостатус" not in brief
    assert "PET-CT" not in brief
    assert "Онколог" not in brief


def test_patient_brief_shows_onco_frame_when_data(db):
    """Безопасность: у тенанта есть онко-данные → рамка рендерится (инвариант не потерян)."""
    import patient_context as pc
    db.add_profile("identity.name", value_text="Тест Тестов")
    db.add_profile("medical.oncologist", value_text="Др. Иванов")
    db.add_profile("medical.last_pet_ct", value_text="2026-03-01")
    brief = pc.build_patient_brief()
    assert "Онколог" in brief


# ── health_db seed + scrub: значения целы, текст очищен ───────────────────────
def test_absolute_thresholds_value_preserved_text_neutral(db):
    """Безопасность: засеянный пол ВСР на месте, scrub его значение НЕ трогает, и
    get_threshold(band='') находит readiness тем же ключом. Нейтральность: текст без личного
    перцентиля. Значение пола в сиде — стартовое (не чужой ряд); личное выводит _personalize."""
    import health_db as hdb
    hdb._seed_absolute_thresholds()
    q = ("SELECT value, reason_template, band_label FROM absolute_thresholds "
         "WHERE metric='hrv' AND direction='floor' AND band_label='' AND variant=''")
    row = db.fetchone(q)
    assert row is not None
    assert row["value"] is not None and row["value"] >= 0     # пол засеян числом (0 = молчит до своих данных)
    v0 = row["value"]
    hdb._scrub_leaky_seed_reasons()
    row = db.fetchone(q)
    assert abs(row["value"] - v0) < 1e-9             # значение (пол) сохранено scrub'ом
    assert "p10" not in row["reason_template"]       # личный перцентиль убран из текста
    # band_label остаётся дефолтным (''), иначе get_threshold(band='') не найдёт порог
    assert row["band_label"] == ""
    # регрессионный якорь: readiness/floor находится тем же ключом, что зовёт morning_report
    assert abs(hdb.get_threshold("readiness", "floor") - 70.0) < 1e-9   # шкала прибора, не чужой ряд


def test_trend_threshold_detection_preserved_text_neutral(db):
    """Безопасность: детекция sleep_score<70 (14д) цела. Нейтральность: без онко-рамки."""
    import health_db as hdb
    hdb._seed_trend_thresholds()
    row = db.fetchone(
        "SELECT value, reason_template FROM trend_thresholds "
        "WHERE metric='sleep_score' AND window_days=14 AND mode='avg'")
    assert row is not None
    assert abs(row["value"] - 70.0) < 1e-9           # детекция сохранена
    assert "онкролог" not in row["reason_template"]
    assert "онколог" not in row["reason_template"]    # онко-рамка убрана
    assert "рецидив" not in row["reason_template"]


def test_scrub_fixes_already_seeded_stale_leaky_rows(db):
    """Ключевой контроль failure-mode «устаревшая реплика»: INSERT OR IGNORE НЕ перезапишет уже
    засеянный (старый, утекающий) текст → scrub-UPDATE обязан его починить в существующей БД."""
    import health_db as hdb
    # Симулируем СТАРУЮ засеянную БД с утекающим текстом (синтетика той же формы, что до фикса).
    # Фикстура db уже засеяла absolute_thresholds → чистим, чтобы вставить именно stale-версию
    # без UNIQUE-коллизии (metric,direction,band_label,variant).
    with db.conn() as c:
        c.execute("DELETE FROM absolute_thresholds WHERE metric='hrv' AND direction='floor'")
        c.execute("DELETE FROM trend_thresholds WHERE metric='sleep_score' AND window_days=14")
        c.execute(
            "INSERT INTO absolute_thresholds (metric, direction, value, reason_template, "
            "source, source_date, kind, baseline, band_label, variant) "
            "VALUES ('hrv','floor',35.0,'ВСР критически низкая: {val:.0f} мс (исторический p10=35 мс)',"
            "'threshold_analysis_2026-04-21','2026-04-21','absolute',NULL,'','')")
        c.execute(
            "INSERT INTO trend_thresholds (metric,direction,value,window_days,mode,level,domain,"
            "reason_template,source,source_date) VALUES ('sleep_score','floor',70.0,14,'avg',"
            "'medical','sleep','Sleep_score: 14-дн среднее {val:.0f} (<70) — онко-рамка: онколог, "
            "рецидив','constitution_sleep_2026-07-04','2026-07-04')")
    # Прогон scrub (как на init_db).
    hdb._scrub_leaky_seed_reasons()
    abs_row = db.fetchone(
        "SELECT reason_template, band_label FROM absolute_thresholds WHERE metric='hrv' AND direction='floor'")
    trend_row = db.fetchone(
        "SELECT reason_template FROM trend_thresholds WHERE metric='sleep_score' AND window_days=14")
    assert "исторический p10" not in abs_row["reason_template"]
    assert "p10" not in abs_row["reason_template"]
    assert abs_row["band_label"] == ""   # band_label не трогается scrub'ом (часть ключа)
    assert "онколог" not in trend_row["reason_template"]
    assert "рецидив" not in trend_row["reason_template"]
