"""tests/unit/test_diagnosis_hardcode_B.py — этап B нити diagnosis-hardcode.

Литература (PHI-egress) + labs per-tenant. Проверяет ОБА направления:
  - сырой диагноз/лечение НЕ уходит в NCBI (egress-guard, RED-first);
  - тенант без онко-класса не получает онко-контекст в запросе и онкомаркеры в лаб-каденции;
  - тенант с онко-классом сохраняет свой контекст/мониторинг.
"""
from __future__ import annotations

import pytest

import pubmed_client as pm


class TestEgressGuard:
    def test_blocks_raw_drug_names(self):
        for raw in ("carboplatin chemotherapy", "doxorubicin metabolic effects",
                    "paclitaxel toxicity", "docetaxel neutropenia"):
            assert pm.egress_safe(raw) is False, raw

    def test_blocks_raw_histology_surgery(self):
        for raw in ("lung adenocarcinoma",
                    "nephrectomy recovery"):
            assert pm.egress_safe(raw) is False, raw

    @pytest.mark.owner_data
    def test_blocks_digits_and_surname(self):
        assert pm.egress_safe("PSA surveillance 2019") is False
        # Личные слова — из словаря pii_census (приватная зона), не литералами в тесте.
        import pii_census
        probes = pii_census.literals(["surname", "doctor", "clinical"])
        assert probes, "словарь pii_census пуст — проверка личных слов была бы пустой"
        for t in probes:
            assert pm.egress_safe(f"{t} case report") is False, "личное слово словаря прошло egress"

    def test_allows_class_level_terms(self):
        for ok in ("heart rate variability autonomic recovery",
                   "cancer survivorship sleep quality",
                   "tumor marker surveillance remission",
                   "iron deficiency ferritin repletion"):
            assert pm.egress_safe(ok) is True, ok


class TestComposeQuery:
    def test_neutral_without_condition(self):
        q = pm._compose_query("low_hrv", set())
        assert q == "heart rate variability autonomic recovery"
        assert "cancer" not in q.lower()

    def test_condition_context_appended(self):
        q = pm._compose_query("low_hrv", {"oncology"})
        assert q == "heart rate variability autonomic recovery cancer survivorship"
        assert pm.egress_safe(q)

    def test_unknown_pattern_returns_none(self):
        assert pm._compose_query("nonexistent_pattern", set()) is None


class TestPatternGating:
    _STATS7 = {"avg_hrv": 18, "avg_deep": 0.8, "avg_readiness": 60}
    _LABS = [{"test_name": "CEA", "value": 3.0}]

    @pytest.fixture(autouse=True)
    def _floors(self, monkeypatch):
        # личные полы тенанта (в жизни — absolute_thresholds, p10 его ряда)
        monkeypatch.setattr(pm, "_tenant_floors", lambda: {"hrv": 20.0, "sleep_deep": 0.9, "readiness": 65.0})

    def test_floor_comes_from_tenant_not_code(self, monkeypatch):
        """BL-PUB-16 в: порог паттерна — личный пол тенанта. Тот же ряд при другом поле — другой ответ."""
        monkeypatch.setattr(pm, "_tenant_floors", lambda: {"hrv": 15.0, "sleep_deep": 0.5, "readiness": 55.0})
        pats = pm.detect_patterns_from_stats(self._STATS7, {}, {"avg_hrv": 18}, [], set())
        assert "low_hrv" not in pats and "poor_deep_sleep" not in pats and "readiness_low" not in pats
        monkeypatch.setattr(pm, "_tenant_floors", lambda: {})
        assert pm.detect_patterns_from_stats(self._STATS7, {}, {"avg_hrv": 18}, [], set()) == []

    def test_unclassified_fixture_no_onco_patterns(self):
        pats = pm.detect_patterns_from_stats(self._STATS7, {}, {"avg_hrv": 25}, self._LABS, set())
        assert "cipn" not in pats
        assert "cea_trend" not in pats
        assert "low_deep_sleep_after_chemo" not in pats
        # физиологические паттерны всё равно детектятся
        assert "low_hrv" in pats and "poor_deep_sleep" in pats

    def test_classified_fixture_gets_onco_patterns(self):
        pats = pm.detect_patterns_from_stats(self._STATS7, {}, {"avg_hrv": 25}, self._LABS, {"oncology"})
        assert "cipn" in pats
        assert "cea_trend" in pats
        assert "low_deep_sleep_after_chemo" in pats


class TestLabsFreshnessPerTenant:
    """labs_db.effective_freshness — импортит health_db (циклический фасад), поэтому
    гоняется на Studio (HEALTH_DATA_DIR задан); на MacBook без env import упадёт."""

    def _labs(self):
        import health_db  # noqa: F401 — раньше labs_db (порядок фасадного цикла)
        import labs_db
        return labs_db

    def test_unclassified_fixture_no_tumor_markers(self):
        L = self._labs()
        neutral = L.effective_freshness(set())
        for m in ("CEA", "CA19-9", "CA125", "Vitamin_B12", "Folate"):
            assert m not in neutral, f"{m} навязан тенанту без класса"
        assert "HGB" in neutral and "Creatinine" in neutral  # база на месте

    def test_iron_deficiency_no_tumor_markers(self):
        L = self._labs()
        s = L.effective_freshness({"iron_deficiency"})
        assert "CEA" not in s  # другой класс не тянет онкомаркеры

    def test_data_freshness_full_catalog_backcompat(self):
        L = self._labs()
        assert "CEA" in L.DATA_FRESHNESS and "HGB" in L.DATA_FRESHNESS


class TestLabFreshnessPrecedence:
    """gp_context: условный boost из effective_freshness главнее СТАРОГО code_seed-дефолта,
    но реальная encounter/manual правка — главнее всего. RED-first: без фикса code_seed
    перекрывал бы boost (ALT остался бы medium у онко-тенанта)."""

    def test_condition_boost_beats_code_seed(self, monkeypatch):
        import gp_context, health_db, clinical_kb
        from datetime import date
        monkeypatch.setattr(clinical_kb, "active_conditions", lambda *a, **k: {"oncology"})
        monkeypatch.setattr(health_db, "get_effective_lab_schedule",
                            lambda *a, **k: {"ALT": {"interval_days": 90, "priority": "medium", "source": "code_seed"}})
        recent = [{"test_name": "ALT", "date": "2026-01-01"}]
        _block, overdue = gp_context._check_lab_freshness(recent, date(2026, 7, 1))
        alt = [o for o in overdue if o["test"] == "ALT"]
        assert alt and alt[0]["priority"] == "high", f"code_seed перекрыл онко-boost: {alt}"

    def test_manual_override_still_wins(self, monkeypatch):
        import gp_context, health_db, clinical_kb
        from datetime import date
        monkeypatch.setattr(clinical_kb, "active_conditions", lambda *a, **k: {"oncology"})
        monkeypatch.setattr(health_db, "get_effective_lab_schedule",
                            lambda *a, **k: {"ALT": {"interval_days": 30, "priority": "critical", "source": "manual"}})
        recent = [{"test_name": "ALT", "date": "2026-01-01"}]
        _block, overdue = gp_context._check_lab_freshness(recent, date(2026, 7, 1))
        alt = [o for o in overdue if o["test"] == "ALT"]
        assert alt and alt[0]["priority"] == "critical", f"manual-правка проиграла дефолту: {alt}"


class TestLiteratureSweepTopics:
    """diagnosis-hardcode (гибрид floor+enrichment): темы еженедельного sweep =
    floor(классы из active_conditions СМОТРЯЩЕГО тенанта) ∪ enrichment(per-tenant yaml).
    Вымышленный профиль A без yaml получает только floor; профиль B — floor ∪ yaml.
    Каждая floor-тема egress-safe по построению (класс-термин).

    Studio-only: pubmed_searcher импортит health_db (отказ на cloud-sync пути MacBook)."""

    def _patch(self, monkeypatch, tmp_path, conds, yaml_text=None):
        import pubmed_searcher as ps
        import clinical_kb
        monkeypatch.setattr(clinical_kb, "active_conditions", lambda *a, **k: set(conds))
        if yaml_text is None:
            monkeypatch.setattr(ps, "TOPICS_PATH", tmp_path / "nonexistent.yaml")
        else:
            p = tmp_path / "survivorship_topics.yaml"
            p.write_text(yaml_text)
            monkeypatch.setattr(ps, "TOPICS_PATH", p)
        return ps

    def test_fixture_floor_only_from_data(self, monkeypatch, tmp_path):
        monkeypatch.setattr(pm, "CONDITION_FLOOR_TERMS",
                            {"fixture_class_q": "sleep duration",
                             "fixture_class_r": "physical activity"})
        ps = self._patch(monkeypatch, tmp_path, {"fixture_class_q", "fixture_class_r"})
        topics = ps._load_topics()
        assert {t["id"] for t in topics} == {"floor_fixture_class_q", "floor_fixture_class_r"}
        assert {t["pubmed_query"] for t in topics} == {"sleep duration", "physical activity"}

    def test_no_conditions_no_yaml_empty(self, monkeypatch, tmp_path):
        ps = self._patch(monkeypatch, tmp_path, set())
        assert ps._load_topics() == []

    def test_fixture_floor_union_enrichment(self, monkeypatch, tmp_path):
        y = "topics:\n- id: fixture_enrichment\n  pubmed_query: exercise recovery\n  priority: high\n"
        ps = self._patch(monkeypatch, tmp_path, {"oncology"}, y)
        topics = ps._load_topics()
        ids = {t["id"] for t in topics}
        assert "floor_oncology" in ids       # пол из данных
        assert "fixture_enrichment" in ids        # обогащение из yaml (аддитивно, union)
        floor = next(t for t in topics if t["id"] == "floor_oncology")
        assert floor["pubmed_query"] == "cancer survivorship"  # нейтральный класс-термин

    def test_all_floor_topics_egress_safe(self, monkeypatch, tmp_path):
        # floor берёт из CONDITION_FLOOR_TERMS (клинические ∪ геномные); КАЖДЫЙ проходит egress_safe
        all_conds = set(pm.CONDITION_FLOOR_TERMS.keys())
        ps = self._patch(monkeypatch, tmp_path, all_conds)
        topics = ps._load_topics()
        assert len(topics) == len(all_conds)
        for t in topics:
            assert pm.egress_safe(t["pubmed_query"]), t

    def test_fixture_genomic_conditions_light_up(self, monkeypatch, tmp_path):
        # Независимые придуманные классы: проверяется передача класса в floor.
        fixture_profile = {"fixture_gene_q", "fixture_gene_r"}
        monkeypatch.setattr(pm, "CONDITION_FLOOR_TERMS",
                            {"fixture_gene_q": "circadian rhythm",
                             "fixture_gene_r": "exercise response"})
        ps = self._patch(monkeypatch, tmp_path, fixture_profile)
        topics = ps._load_topics()
        assert {t["id"] for t in topics} == {f"floor_{c}" for c in fixture_profile}
        for t in topics:
            assert pm.egress_safe(t["pubmed_query"]), t
