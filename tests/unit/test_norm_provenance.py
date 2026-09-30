"""Провенанс нормы: вид · срок годности · внешний свидетель · покрытие (нить norm-provenance, 2026-09-02).

Замер, породивший нить: колонки провенанса построены (clinical_kb.review_date, source_date),
содержимого 0/81 и 0/75 — ни одной даты пересмотра нормы во всей базе. Медицина обновляет
норму именованным документом с датой пересмотра; здесь этого объекта не было.

Стражи (fixture-БД, §20 — не боевая):
1. Схема: norm_kind под CHECK, default unclassified; next_review/stratum.
2. Классификация по провенансу: однозначное → kind; остальное unclassified; вердикт человека
   не перетирается.
3. Просрочка: next_review < сегодня → красное; без даты — не красное.
4. Свидетель: reference_interval, расходящийся с модальным интервалом лаборатории (≥min_docs
   документов) → красное; unclassified → предложение, не красное; один документ — не свидетель.
5. Покрытие: аналит ×N без нормы нигде → в списке; с порогом или в lab_refs — нет.
6. Coherence: _NORM_FALLBACK ≡ сид system_config.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

import health_db
import integrity_tests as I

pytestmark = pytest.mark.unit


@pytest.fixture
def sdb(db):
    health_db._seed_safety_lab_thresholds()
    # личный порог — данные тенанта, не сид кода (27.09, BL-PUB-16 а): заводим строкой
    with db.conn() as c:
        c.execute("INSERT INTO absolute_thresholds (metric,direction,value,reason_template,source,"
                  "band_label,variant,kind) VALUES ('Amylase','ceiling',150.0,'Amylase {val:g} U/L — личная',"
                  "'safety_net_owner_personal_example','warn','safety_net','absolute')")
    health_db._classify_norm_kinds()
    # тестовая АБСОЛЮТНАЯ строка вида «референс» — свидетель судит только absolute warn
    with db.conn() as c:
        c.execute("INSERT INTO absolute_thresholds (metric,direction,value,reason_template,source,"
                  "band_label,variant,kind) VALUES ('CRP','ceiling',10.0,'CRP {val:g} mg/L — test',"
                  "'test_doc','warn','safety_net','absolute')")
    return db


def _lab(db, name, value, src="doc:a", lo=None, hi=None, unit="mg/dL", d="2026-08-01"):
    with db.conn() as c:
        c.execute("INSERT INTO lab_results (date, source, test_name, value, unit, ref_low, ref_high) "
                  "VALUES (?,?,?,?,?,?,?)", (d, src, name, value, unit, lo, hi))


def _ro(db):
    con = sqlite3.connect(f"file:{db.path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


# ── 1. Схема ──────────────────────────────────────────────────────────────────

def test_columns_exist_with_default_unclassified(sdb):
    cols = {r[1] for r in sdb.fetchall("PRAGMA table_info(absolute_thresholds)")}
    assert {"norm_kind", "next_review", "stratum"} <= cols
    assert sdb.count("absolute_thresholds", "norm_kind IS NULL") == 0


@pytest.mark.owner_data
def test_norm_kind_check_constraint(sdb):
    with pytest.raises(sqlite3.IntegrityError):
        with sdb.conn() as c:
            c.execute("UPDATE absolute_thresholds SET norm_kind='vibes' WHERE metric='HGB'")


# ── 2. Классификация ─────────────────────────────────────────────────────────

@pytest.mark.owner_data
def test_classify_by_provenance(sdb):
    kinds = {r[0]: r[1] for r in sdb.fetchall(
        "SELECT source, norm_kind FROM absolute_thresholds GROUP BY source")}
    assert kinds.get("ESC_AHA_2023") == "decision_threshold"
    assert kinds.get("safety_net_owner_personal_example") == "personal"
    assert kinds.get("CTCAE_v6.0") == "decision_threshold", "строки из документа — порог решения"
    assert kinds.get("test_doc") == "unclassified", "неизвестный source — не угадывается"


@pytest.mark.owner_data
def test_classify_keeps_human_verdict(sdb):
    with sdb.conn() as c:
        c.execute("UPDATE absolute_thresholds SET norm_kind='reference_interval' WHERE metric='HGB'")
        c.execute("UPDATE absolute_thresholds SET source='p10_personal' WHERE metric='HGB'")
    health_db._classify_norm_kinds()
    assert sdb.count("absolute_thresholds", "metric='HGB' AND norm_kind='reference_interval'") > 0


# ── 3. Просрочка ─────────────────────────────────────────────────────────────

@pytest.mark.owner_data
def test_overdue_red_only_when_date_passed(sdb):
    with sdb.conn() as c:
        c.execute("UPDATE absolute_thresholds SET next_review=NULL")
    assert I._norm_scan_overdue(_ro(sdb), "2026-09-02") == []
    with sdb.conn() as c:
        c.execute("UPDATE absolute_thresholds SET next_review='2026-01-01' "
                  "WHERE metric='HGB' AND band_label='urgent'")
    hits = I._norm_scan_overdue(_ro(sdb), "2026-09-02")
    assert len(hits) == 1 and hits[0].startswith("HGB/floor/urgent")
    with sdb.conn() as c:
        c.execute("UPDATE absolute_thresholds SET active=0 WHERE metric='HGB' AND band_label='urgent'")
    assert I._norm_scan_overdue(_ro(sdb), "2026-09-02") == [], "неактивная норма не просрочена"


# ── 4. Свидетель ─────────────────────────────────────────────────────────────

def _witness_docs(db, name, lo, hi, n=3, unit="mg/L"):
    """unit — в шкале порога: свидетель приводится к conventional (to_conventional_range),
    и mg/dL для CRP ушёл бы ×10 — это не баг датчика, а то, ради чего пересчёт есть."""
    for i in range(n):
        _lab(db, name, 1.0, src=f"doc:{i}", lo=lo, hi=hi, unit=unit)


def test_witness_red_for_reference_interval_mismatch(sdb):
    # CRP warn ceiling 10.0; лаборатория печатает 0–5 → 100% расхождение
    _witness_docs(sdb, "CRP", 0.0, 5.0)
    with sdb.conn() as c:
        c.execute("UPDATE absolute_thresholds SET norm_kind='reference_interval' WHERE metric='CRP'")
    red, props = I._norm_scan_witness(_ro(sdb), 0.25, 3)
    assert red and red[0].startswith("CRP ceiling warn 10")
    assert not props


def test_witness_green_when_reference_agrees(sdb):
    _witness_docs(sdb, "CRP", 0.0, 10.0)
    with sdb.conn() as c:
        c.execute("UPDATE absolute_thresholds SET norm_kind='reference_interval' WHERE metric='CRP'")
    red, _ = I._norm_scan_witness(_ro(sdb), 0.25, 3)
    assert red == []


def test_witness_proposes_for_unclassified_not_red(sdb):
    _witness_docs(sdb, "CRP", 0.0, 5.0)
    red, props = I._norm_scan_witness(_ro(sdb), 0.25, 3)
    assert red == []
    assert any(p.startswith("CRP") and "расходится" in p for p in props)


def test_witness_needs_min_docs(sdb):
    """Один бланк — не свидетель (§17)."""
    _witness_docs(sdb, "CRP", 0.0, 5.0, n=1)
    with sdb.conn() as c:
        c.execute("UPDATE absolute_thresholds SET norm_kind='reference_interval' WHERE metric='CRP'")
    red, props = I._norm_scan_witness(_ro(sdb), 0.25, 3)
    assert red == [] and props == []


# ── 5. Покрытие ──────────────────────────────────────────────────────────────

@pytest.mark.owner_data
def test_coverage_lists_measured_analyte_without_norm(sdb):
    for i in range(5):
        _lab(sdb, "LDL", 100.0 + i, src=f"doc:{i}")
    for i in range(5):
        _lab(sdb, "ALT", 20.0, src=f"doc:{i}")         # есть порог (CTCAE) → покрыт
    for i in range(5):
        _lab(sdb, "Sodium", 140.0, src=f"doc:{i}")     # в lab_refs → покрыт
    for i in range(5):
        _lab(sdb, "HDL", 55.0, src=f"doc:{i}", lo=40.0, hi=None)   # референс на бланке → вид 1 → покрыт
    _lab(sdb, "RDW", 13.0)                              # 1 измерение < N
    gaps = I._norm_scan_coverage(_ro(sdb), 5, {"Sodium": (135, 145, "mmol/L")})
    assert gaps == ["LDL×5"], gaps


def test_coverage_respects_live_verdict_and_forgets_expired(sdb):
    """Вердикт человека (2026-09-03): живой (review_at ≥ today) — аналит не слепое пятно;
    истёкший — читатель его не отдаёт и аналит возвращается в gaps сам (§18)."""
    import labs_db
    for i in range(11):
        _lab(sdb, "Chol_HDL_ratio", 2.6, src=f"doc:{i}", unit="")
    ro = _ro(sdb)
    assert I._norm_scan_coverage(ro, 5, {}, labs_db.analyte_norm_verdicts(conn=ro, today="2026-09-03")) \
        == ["Chol_HDL_ratio×11"]
    labs_db.set_analyte_norm_verdict("CHOL/dHDLC", "deferred", "бланк без референса; врачу",
                                     "owner", review_at="2027-01-05", decided_on="2026-09-03")
    ro = _ro(sdb)
    live = labs_db.analyte_norm_verdicts(conn=ro, today="2026-09-03")
    assert live == {"Chol_HDL_ratio": "deferred"}, "имя нормализовано через lab_canon"
    assert I._norm_scan_coverage(ro, 5, {}, live) == []
    expired = labs_db.analyte_norm_verdicts(conn=ro, today="2027-01-06")
    assert expired == {} and I._norm_scan_coverage(ro, 5, {}, expired) == ["Chol_HDL_ratio×11"]


def test_verdict_constraints_are_native(sdb):
    """Вид, непустота и review_at > decided_on — CHECK в SQLite, не проверка в Python."""
    import labs_db
    with pytest.raises(sqlite3.IntegrityError):
        labs_db.set_analyte_norm_verdict("NLR", "maybe", "x", "owner", "2027-01-01", "2026-09-03")
    with pytest.raises(sqlite3.IntegrityError):
        labs_db.set_analyte_norm_verdict("NLR", "no_norm", "", "owner", "2027-01-01", "2026-09-03")
    with pytest.raises(sqlite3.IntegrityError):
        labs_db.set_analyte_norm_verdict("NLR", "no_norm", "x", "owner", "2026-09-03", "2026-09-03")


# ── 6. Coherence ─────────────────────────────────────────────────────────────

def test_norm_config_seed_matches_fallback(sdb):
    health_db._migrate_patient_profile_and_config()
    rows = dict(sdb.fetchall("SELECT key, value_num FROM system_config WHERE key LIKE 'norm.%'"))
    assert rows, "сид norm.* не засеян"
    for k, v in I._NORM_FALLBACK.items():
        assert rows.get(k) == v, f"{k}: сид {rows.get(k)} ≠ резерв {v}"


# ── 7. Источник порога — документ (инвариант threshold_derived_from_document) ─────

@pytest.mark.owner_data
def test_threshold_source_green_after_seed(sdb):
    """Сид из документа + личный Amylase → чисто; тестовая строка 'test_doc' — набранное число."""
    with sdb.conn() as c:
        c.execute("DELETE FROM absolute_thresholds WHERE source='test_doc'")
    health_db._migrate_norm_documents()
    bad, checked = I._threshold_source_scan(_ro(sdb))
    assert bad == [] and checked >= 75


def test_threshold_source_negative_control_typed_number_is_red(sdb):
    """НЕГАТИВНЫЙ КОНТРОЛЬ: строка с source не из реестра (ровно апрельский класс) → красное."""
    health_db._migrate_norm_documents()
    bad, _ = I._threshold_source_scan(_ro(sdb))
    assert any(b.startswith("CRP/ceiling/warn") and "test_doc" in b for b in bad), bad


def test_threshold_source_clinician_and_personal_allowed(sdb):
    with sdb.conn() as c:
        c.execute("DELETE FROM absolute_thresholds WHERE source='test_doc'")
        c.execute("INSERT INTO absolute_thresholds (metric,direction,value,reason_template,source,band_label,variant,kind) "
                  "VALUES ('CA19-9','ceiling',100,'x','clinician:consult_7','urgent','safety_net','absolute')")
    health_db._migrate_norm_documents()
    bad, _ = I._threshold_source_scan(_ro(sdb))
    assert bad == [], bad


@pytest.mark.owner_data
def test_seed_supersedes_previous_ctcae_version(sdb):
    """Строки v5 (если были) уходят в variant='superseded:CTCAE_v5.0', active=0; активны — v6."""
    with sdb.conn() as c:
        c.execute("INSERT OR IGNORE INTO absolute_thresholds (metric,direction,value,reason_template,source,"
                  "source_date,kind,baseline,band_label,variant,norm_kind) VALUES "
                  "('CPK','ceiling',2.5,'x','CTCAE_v5.0','2017-11-27','relative','ULN','urgent','safety_net','decision_threshold')")
    health_db._seed_safety_lab_thresholds()
    assert sdb.count("absolute_thresholds", "source='CTCAE_v5.0' AND active=1") == 0
    assert sdb.count("absolute_thresholds", "variant='superseded:CTCAE_v5.0' AND metric='CPK'") == 1
    assert sdb.count("absolute_thresholds", "source='CTCAE_v6.0' AND active=1 AND variant='safety_net'") >= 70


# ── 8. lab_refs = мода бланков, не литерал ───────────────────────────────────────

def test_bank_refs_need_min_docs(db):
    import labs_db
    for i in range(3):
        _lab(db, "ALT", 20.0, src=f"doc:{i}", lo=0.0, hi=41.0, unit="U/L")
    for i in range(2):
        _lab(db, "GGT", 20.0, src=f"doc:{i}", lo=0.0, hi=60.0, unit="U/L")
    refs = labs_db.compute_bank_refs(3)
    assert refs["ALT"][:3] == [0.0, 41.0, "U/L"] and refs["ALT"][3] == 3
    assert "GGT" not in refs, "два документа — не мода"


def test_refresh_writes_cache_and_get_lab_refs_reads_it(db):
    import labs_db
    health_db._migrate_patient_profile_and_config()
    for i in range(3):
        _lab(db, "ALT", 20.0, src=f"doc:{i}", lo=0.0, hi=41.0, unit="U/L")
    health_db._refresh_lab_refs()
    refs = labs_db.get_lab_refs()
    assert refs["ALT"] == (0.0, 41.0, "U/L")
    assert labs_db.get_lab_refs_meta()["n_docs"]["ALT"] == 3
    assert not hasattr(health_db, "LAB_REFS_CANONICAL"), "литерал удалён"


def test_lab_refs_without_witnesses_is_red(sdb):
    """НЕГАТИВНЫЙ КОНТРОЛЬ: интервал в кэше без документов (как прежний литерал) → красное."""
    health_db._migrate_patient_profile_and_config()
    health_db._migrate_norm_documents()
    with sdb.conn() as c:
        c.execute("DELETE FROM absolute_thresholds WHERE source='test_doc'")
        c.execute("INSERT OR REPLACE INTO system_config (key, value_json, category, source) "
                  "VALUES ('lab_refs', ?, 'lab', 'migration')",
                  (json.dumps({"HGB": [13.5, 17.5, "g/dL"]}),))
    bad, _ = I._threshold_source_scan(_ro(sdb))
    assert any(b.startswith("lab_refs.HGB") for b in bad), bad


@pytest.mark.owner_data
def test_glucose_ceiling_follows_fasting_profile(sdb):
    health_db._migrate_patient_profile_and_config()
    with sdb.conn() as c:
        c.execute("INSERT OR REPLACE INTO patient_profile (key, value_text, category) VALUES ('routine.fasting_labs', 'false', 'routine')")
    health_db._seed_safety_lab_thresholds()
    assert sdb.count("absolute_thresholds", "metric='Glucose' AND direction='ceiling' AND variant='safety_net' AND active=1") == 0
    with sdb.conn() as c:
        c.execute("UPDATE patient_profile SET value_text='true' WHERE key='routine.fasting_labs'")
    health_db._seed_safety_lab_thresholds()
    assert sdb.count("absolute_thresholds", "metric='Glucose' AND direction='ceiling' AND variant='safety_net' AND active=1") == 3
