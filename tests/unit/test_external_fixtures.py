"""Smoke-тесты на T-0.7 fixtures (ClinVar, Oura, HAE, PubMed) и T-0.8 (pending_labs)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


# ── ClinVar ──────────────────────────────────────────────────────────────────

def test_clinvar_default_has_reference_snps(clinvar_mock):
    rs4680 = clinvar_mock.get("rs4680")
    assert rs4680["gene"] == "COMT"
    assert rs4680["genotype"] == "GG"


def test_clinvar_set_overrides(clinvar_mock):
    clinvar_mock.set("rs6265", significance="Pathogenic")  # было Benign
    assert clinvar_mock.get("rs6265")["significance"] == "Pathogenic"


def test_clinvar_batch(clinvar_mock):
    res = clinvar_mock.batch_get(["rs4680", "rs6265", "rs_unknown"])
    assert res["rs4680"]
    assert res["rs6265"]
    assert res["rs_unknown"] == {}


def test_clinvar_fail_mode_timeout(clinvar_mock):
    clinvar_mock.fail_mode = "timeout"
    with pytest.raises(TimeoutError):
        clinvar_mock.get("rs4680")


def test_clinvar_fail_mode_schema_drift(clinvar_mock):
    """Возвращает невалидный формат — для UC-K-04."""
    clinvar_mock.fail_mode = "schema_drift"
    res = clinvar_mock.get("rs4680")
    assert "_unknown_format" in res


# ── Oura ─────────────────────────────────────────────────────────────────────

def test_oura_add_sleep(oura_mock):
    oura_mock.add_sleep("2026-05-08", total=7 * 3600 + 30 * 60, deep=45 * 60)
    res = oura_mock.get("sleep", "2026-05-08", "2026-05-08")
    assert len(res["data"]) == 1
    assert res["data"][0]["day"] == "2026-05-08"
    assert res["data"][0]["total_sleep_duration"] == 7 * 3600 + 30 * 60


def test_oura_filter_by_date_range(oura_mock):
    for d in ["2026-05-01", "2026-05-08", "2026-05-15"]:
        oura_mock.add_sleep(d)
    res = oura_mock.get("sleep", "2026-05-05", "2026-05-10")
    assert len(res["data"]) == 1
    assert res["data"][0]["day"] == "2026-05-08"


def test_oura_endpoints_separate(oura_mock):
    oura_mock.add_sleep("2026-05-08")
    oura_mock.add_activity("2026-05-08", steps=10000)
    assert oura_mock.get("sleep")["data"]
    assert oura_mock.get("daily_activity")["data"][0]["steps"] == 10000


# ── HAE ──────────────────────────────────────────────────────────────────────

def test_hae_creates_file(hae_mock):
    p = hae_mock.add_daily("2026-05-08", steps=8000, hrv=25)
    assert p.exists()
    payload = json.loads(p.read_text())
    metric_names = {m["name"] for m in payload["data"]["metrics"]}
    assert "step_count" in metric_names
    assert "heart_rate_variability" in metric_names


def test_hae_skips_none_fields(hae_mock):
    """None в add_daily не должен создавать metric — это симулирует «HAE не прислал поле»."""
    p = hae_mock.add_daily("2026-05-08", steps=8000, hrv=None)
    payload = json.loads(p.read_text())
    metric_names = {m["name"] for m in payload["data"]["metrics"]}
    assert "step_count" in metric_names
    assert "heart_rate_variability" not in metric_names


def test_hae_list_files(hae_mock):
    hae_mock.add_daily("2026-05-08")
    hae_mock.add_daily("2026-05-09")
    files = hae_mock.list_files()
    assert len(files) == 2


# ── PubMed ───────────────────────────────────────────────────────────────────

def test_pubmed_fetch(pubmed_mock):
    pubmed_mock.add(38123456, title="Adenocarcinoma management")
    res = pubmed_mock.fetch(38123456)
    assert res["PMID"] == "38123456"
    assert "Adenocarcinoma" in res["Article"]["ArticleTitle"]


def test_pubmed_404(pubmed_mock):
    pubmed_mock.fail_mode = "404"
    res = pubmed_mock.fetch(99999999)
    assert res == {}


def test_pubmed_search(pubmed_mock):
    pubmed_mock.add(1, title="HRV in chemotherapy")
    pubmed_mock.add(2, title="Sleep architecture")
    pmids = pubmed_mock.search("HRV")
    assert pmids == ["1"]


# ── pending_labs ─────────────────────────────────────────────────────────────

def test_pending_labs_dir_empty(pending_labs_dir: Path):
    assert pending_labs_dir.exists()
    assert pending_labs_dir.is_dir()
    assert list(pending_labs_dir.iterdir()) == []


def test_make_pending_json_helper(make_pending_json):
    payload = make_pending_json("2026-05-01_clinic")
    assert payload["visit_key"] == "2026-05-01_clinic"
    assert payload["items"][0]["name"] == "WBC"
    assert payload["items"][0]["confidence"] == "high"


def test_pending_labs_workflow(pending_labs_dir: Path, make_pending_json):
    """Симулируем tentative-запись: создаём файл, читаем, удаляем."""
    payload = make_pending_json("2026-05-01_clinic",
                                  items=[{"name": "AST", "value": 80,
                                          "ref_low": 10, "ref_high": 40,
                                          "flagged": True, "confidence": "low"}])
    path = pending_labs_dir / "2026-05-01_clinic.json"
    path.write_text(json.dumps(payload, ensure_ascii=False))

    assert path.exists()
    loaded = json.loads(path.read_text())
    assert loaded["items"][0]["confidence"] == "low"
