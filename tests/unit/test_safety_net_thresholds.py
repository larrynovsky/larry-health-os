"""Сенсоры выноса порогов safety_net в БД (safety-net-thresholds E1, этап 3).

Четыре независимых стража:
1. БД-путь — loader реально читает absolute_thresholds/lab_trend_thresholds (не только
   резерв). Позитивный контроль: сдвиг БД-порога отражается в loader.
2. Coherence — резерв _FALLBACK ≡ сид БД (ловит дрейф резерва, split-brain).
3. §9-линтер — check-функции не обращаются к старым порог-константам (только loader/_FALLBACK).
4. Fallback-эмит — пустой источник → резерв + громкий алерт (предохранитель не онемел).

sdb-фикстура сеет safety-пороги ЛОКАЛЬНО (не в общей db-фикстуре: тесты db_config_api
считают точный brief-контент absolute_thresholds).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

import safety_net as sn
import rules_db

pytestmark = pytest.mark.unit


@pytest.fixture
def sdb(db):
    """db + safety_net-пороги засеяны как init_db в бою (lab_trend + абс. + lifestyle)."""
    import health_db
    health_db._seed_lab_trend_thresholds()
    health_db._seed_safety_lab_thresholds()
    health_db._seed_safety_lifestyle_thresholds()
    return db


# ── 1. БД-путь ────────────────────────────────────────────────────────────────

def _row(rows, direction, band):
    return next(r for r in rows if r["direction"] == direction and r["band"] == band)


@pytest.mark.owner_data
def test_lab_loader_reads_db_not_only_fallback(sdb):
    """_load_lab_thresholds читает absolute_thresholds. Позитивный контроль: сдвиг
    БД-порога HGB urgent 10→9 отражается в loader (докажет БД-путь, не резерв).
    С 2026-09-02 строки — из CTCAE v5.0 (HGB grade 2 = <10 g/dL), не литерал."""
    lab = sn._load_lab_thresholds()
    assert _row(lab["HGB"], "floor", "urgent")["value"] == 10.0
    assert _row(lab["HGB"], "floor", "warn")["kind"] == "relative" and _row(lab["HGB"], "floor", "warn")["baseline"] == "LLN"
    with sdb.conn() as c:
        c.execute("UPDATE absolute_thresholds SET value=9 "
                  "WHERE metric='HGB' AND band_label='urgent' AND variant='safety_net'")
    lab2 = sn._load_lab_thresholds()
    assert _row(lab2["HGB"], "floor", "urgent")["value"] == 9.0, "loader читает резерв, а не absolute_thresholds"


@pytest.mark.owner_data
def test_lab_trend_loader_reads_db(sdb):
    import norm_documents
    lt = sn._load_lab_trend_thresholds()
    assert lt["CEA"]["pct_change"] == norm_documents.rcv_pct("CEA") and lt["CEA"]["level"] == "urgent"
    assert lt["HGB"]["direction"] == "down" and lt["CEA"]["n_readings"] == 2


@pytest.mark.owner_data
def test_lab_trend_near_boundary_share_seeded_with_owner_provenance(sdb):
    """Пол по величине — данные строки порога с провенансом слова владельца (03.09), сид не
    перетирает значение, уже заданное в БД (вердикт человека)."""
    import health_db
    lt = sn._load_lab_trend_thresholds()
    assert lt["CA19-9"]["near_boundary_share"] == health_db.LAB_TREND_NEAR_BOUNDARY_SHARE == 0.2
    src = sdb.fetchone("SELECT near_boundary_source FROM lab_trend_thresholds WHERE metric='CA19-9'")[0]
    assert src.startswith("owner_word:")
    sdb.execute("UPDATE lab_trend_thresholds SET near_boundary_share=0.3, near_boundary_source='owner_word:test' WHERE metric='CA19-9'")
    health_db._seed_lab_trend_thresholds()
    assert sn._load_lab_trend_thresholds()["CA19-9"]["near_boundary_share"] == 0.3


def test_lifestyle_loaders_read_db(sdb):
    a = sn._load_lifestyle_abs()
    assert a["spo2_avg"]["low_critical"] == 90.0 and a["spo2_avg"]["low_warn"] == 94.0
    assert a["readiness_score"]["low_urgent"] == 30.0
    r = sn._load_lifestyle_rel()
    assert r["hrv"]["drop_warn"] == 0.20 and r["hrv"]["drop_critical"] == 0.50
    assert r["resting_hr"]["rise_urgent"] == 15.0


# ── 2. Coherence: резерв ≡ сид ────────────────────────────────────────────────

def _keyset(rows_by_metric):
    return {(m, r["direction"], r["band"], r["kind"], r["baseline"], round(float(r["value"]), 6))
            for m, rows in rows_by_metric.items() for r in rows}


def test_fallback_lab_equals_seed(sdb):
    """Резерв _FALLBACK_LAB (снимок документа) ≡ строки absolute_thresholds из того же
    документа. Дрейф = split-brain. Amylase (личный, DB-only) в резерве нет — намеренно."""
    loaded = sn._load_lab_thresholds()
    loaded = {m: rows for m, rows in loaded.items() if m != "Amylase"}
    assert _keyset(loaded) == _keyset(sn._FALLBACK_LAB)


@pytest.mark.owner_data
def test_snapshot_equals_import_of_document():
    """Снимок json ≡ импорт(xlsx) — coherence документа и его снимка."""
    import norm_documents as nd
    fresh = nd.import_ctcae()
    snap = nd.load_ctcae_rows()["rows"]
    key = lambda r: (r["metric"], r["direction"], r["band"], r["kind"], r["baseline"], round(r["value"], 6))
    assert sorted(map(key, fresh)) == sorted(map(key, snap)), "снимок отстал от документа: norm_documents.snapshot_ctcae()"


def test_fallback_lab_trend_equals_seed(sdb):
    loaded = sn._load_lab_trend_thresholds()
    for metric, fb in sn._FALLBACK_LAB_TREND.items():
        assert loaded[metric]["pct_change"] == fb["pct_change"]
        assert loaded[metric]["n_readings"] == fb["n_readings"]
        assert loaded[metric]["direction"] == fb["direction"]
        assert loaded[metric]["level"] == fb["level"]


def test_fallback_lifestyle_equals_seed(sdb):
    a = sn._load_lifestyle_abs()
    for k, fb in sn._FALLBACK_LIFESTYLE_ABS.items():
        for bk in ("low_critical", "low_urgent", "low_warn"):
            if bk in fb:
                assert a[k].get(bk) == fb[bk], f"{k}/{bk}: БД ≠ резерв"
    r = sn._load_lifestyle_rel()
    for k in ("hrv", "resting_hr"):   # readiness_score rel — мёртвая конфигурация (reader не зовёт)
        fb = sn._FALLBACK_LIFESTYLE_REL[k]
        for bk, v in fb.items():
            if bk in ("label", "unit"):
                continue
            assert r[k].get(bk) == v, f"{k}/{bk}: БД ≠ резерв"


# ── 3. §9-линтер: нет обращений к старым порог-константам ──────────────────────

def test_no_bare_threshold_constants_in_code():
    """check-функции обращаются к порогам только через loader/_FALLBACK, не через
    старые голые LAB_THRESHOLDS/LIFESTYLE_ABS/... (иначе вынос обойдён литералом)."""
    src = Path(__file__).parents[2].joinpath("safety_net.py").read_text(encoding="utf-8")
    for name in ("LAB_THRESHOLDS", "LIFESTYLE_ABS", "LIFESTYLE_REL", "LAB_TREND_THRESHOLDS"):
        bare = re.findall(rf'(?<![_A-Za-z]){name}\b', src)
        assert not bare, f"голое {name} в safety_net.py ({len(bare)}×) — обход выноса, используй loader"


# ── 4. Fallback-эмит: пустой источник → резерв + алерт ────────────────────────

def test_lab_fallback_on_empty_source(monkeypatch):
    """Пустая absolute_thresholds → _load_lab_thresholds возвращает _FALLBACK_LAB + эмит.
    Позитивный контроль предохранителя: убери fallback-ветку → loader упадёт/онемеет."""
    monkeypatch.setattr(rules_db, "get_absolute_thresholds", lambda *a, **k: [])
    sn._FALLBACK_ALERTED.clear()
    called = []
    monkeypatch.setattr(sn, "_emit_fallback_alert", lambda scope, err: called.append(scope))
    lab = sn._load_lab_thresholds()
    assert lab == sn._FALLBACK_LAB, "loader не деградировал к резерву на пустой БД"
    assert "lab_abs" in called, "не эмитил громкий fallback-алерт"


def test_lab_trend_fallback_on_empty_source(monkeypatch):
    monkeypatch.setattr(rules_db, "get_lab_trend_thresholds", lambda *a, **k: [])
    sn._FALLBACK_ALERTED.clear()
    called = []
    monkeypatch.setattr(sn, "_emit_fallback_alert", lambda scope, err: called.append(scope))
    lt = sn._load_lab_trend_thresholds()
    assert lt == sn._FALLBACK_LAB_TREND
    assert "lab_trend" in called


def test_lifestyle_fallback_on_empty_source(monkeypatch):
    monkeypatch.setattr(rules_db, "get_absolute_thresholds", lambda *a, **k: [])
    sn._FALLBACK_ALERTED.clear()
    called = []
    monkeypatch.setattr(sn, "_emit_fallback_alert", lambda scope, err: called.append(scope))
    assert sn._load_lifestyle_abs() == sn._FALLBACK_LIFESTYLE_ABS
    assert sn._load_lifestyle_rel() == sn._FALLBACK_LIFESTYLE_REL
    assert "lifestyle_abs" in called and "lifestyle_rel" in called
