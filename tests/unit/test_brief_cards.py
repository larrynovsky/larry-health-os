"""
Ф2 brief_cards — детерминированные адаптеры провайдеров → Card. Pure unit (без БД).

RST: границы, которые happy-path не покрывает — детерминизм ключа, разделение полос,
pass-through гипотез (не декомпозируем мед-текст), cross-provider дедуп, отказ на
плохой lane (посаженный дефект обязан упасть).
"""
from __future__ import annotations

import pytest

import brief_cards as bc

pytestmark = pytest.mark.unit

_DRIFT = {"metric": "steps", "direction": "up", "streak_days": 3,
          "current_7d": 6840.0, "baseline_30d": 6000.0, "delta_pct": 14.0, "severity": "mild"}
_SAFETY = {"source": "lab", "level": "warn", "metric": "Ferritin", "value": 24.6,
           "unit": "ng/mL", "direction": "low", "note": "учебное отклонение"}
_GENOME = {"gene": "FIXTURE_Q", "genotype": "AT", "significance": "Pathogenic",
           "clinical_summary": "invented variant Q; no clinical interpretation",
           "effect_allele_status": "palindromic"}
_HYP = {"memory_id": 42, "observation": "Ферритин снизился с 38.4 до 24.6", "status": "open"}


def test_drift_key_deterministic():
    assert bc.from_drift(_DRIFT).semantic_key == bc.from_drift(_DRIFT).semantic_key == "drift:steps:up"


def test_drift_claims_carry_numbers():
    c = bc.from_drift(_DRIFT)
    assert c.lane == "routine"
    assert any("6840" in x and "steps" in x for x in c.allowed_claims)


def test_safety_lane_and_key():
    c = bc.from_safety(_SAFETY)
    assert c.lane == "safety"
    assert c.semantic_key == "safety:ferritin:low"
    assert any("24.6" in x for x in c.allowed_claims)


def test_genome_key_origin_and_unverified_forbidden():
    c = bc.from_genome(_GENOME, "sleep")
    assert c.semantic_key == "genome:sleep:fixture_q"
    assert c.origin == "personal_external"
    assert c.claim_scope == "structured"
    assert c.forbidden_claims, "palindromic → нельзя утверждать направление эффекта"


def test_hypothesis_passthrough_not_decomposed():
    c = bc.from_hypothesis(_HYP)
    assert c.semantic_key == "hypothesis:42"
    assert c.claim_scope == "passthrough"
    assert c.allowed_claims is None


def test_dedup_collapses_cross_provider_synonyms():
    a = bc.Card(provider="drift", semantic_key="drift:deep:down", lane="routine")
    b = bc.Card(provider="agent", semantic_key="sleep:deep:below_band", lane="routine")
    assert len(bc.dedup([a, b])) == 1  # один смысл (глубокий ниже полосы)


def test_dedup_keeps_distinct_findings():
    assert len(bc.dedup([bc.from_genome(_GENOME, "sleep"), bc.from_safety(_SAFETY)])) == 2


def test_bad_lane_raises():
    with pytest.raises(ValueError):
        bc.Card(provider="x", semantic_key="k", lane="weird")


def test_bad_claim_scope_raises():
    with pytest.raises(ValueError):
        bc.Card(provider="x", semantic_key="k", lane="routine", claim_scope="freeform")


def test_sleep_deep_card_below_band():
    band = {"band": "below", "z": -2.0, "p_low": 28, "p_high": 95}
    c = bc.from_sleep_deep(22, band)
    assert c.semantic_key == "sleep:deep:below_band"
    assert c.lane == "routine" and c.severity == 0.7
    assert any("22" in x for x in c.allowed_claims)


def test_sleep_deep_folds_genome_modifier():
    """Sleep-ген склеен в находку sleep:deep (один кулдаун), не отдельная карточка."""
    band = {"band": "below", "z": -2.0, "p_low": 25}
    c = bc.from_sleep_deep(22, band, genome_mods=["FIXTURE_Q AT Pathogenic"])
    assert c.semantic_key == "sleep:deep:below_band"
    assert "FIXTURE_Q" in c.evidence_summary
    assert any("FIXTURE_Q" in x for x in c.allowed_claims)


# ── from_recovery: композит ниже ЛИЧНОЙ полосы → карточка (promote profile-staleness) ──
def test_from_recovery_below_band_makes_card():
    """Ниже личной полосы → карточка pulse, severity дискретна 0.5."""
    card = bc.from_recovery(71.0, {"band": "below", "z": -1.0, "p_low": 72.0})
    assert card is not None
    assert card.provider == "recovery"
    assert card.semantic_key == "recovery:composite:below_band"
    assert card.lane == "routine" and card.severity == 0.5


def test_from_recovery_within_band_is_none():
    """Вымышленный композит внутри личной полосы не становится находкой
    из-за своего абсолютного уровня."""
    assert bc.from_recovery(74.0, {"band": "within", "z": 0.1, "p_low": 72.0}) is None


def test_from_recovery_unknown_band_is_none():
    """Разреженная история (<min_obs) → band 'unknown' → нет карточки, не врём на 3 точках."""
    assert bc.from_recovery(71.0, {"band": "unknown", "z": None, "p_low": None}) is None


def test_from_recovery_deep_dip_escalates_severity():
    """Глубоко ниже полосы (z ≤ −1.5) → severity 0.7, как у sleep-агента (дискретно, два ярлыка)."""
    card = bc.from_recovery(55.0, {"band": "below", "z": -2.0, "p_low": 72.0})
    assert card is not None and card.severity == 0.7


def test_recovery_series_skips_none_and_newest_first(monkeypatch):
    """recovery_series пропускает дни без данных (composite None) — иначе они бы засоряли
    личную полосу нулями — и хранит порядок новейший-первым. Механизм ряда, не формулы."""
    from datetime import date
    import hai_analysis as hai

    class _Dummy:
        def close(self): pass
    seq = {0: 70.0, 1: None, 2: 65.0, 3: 68.0}   # i=1 — день без данных
    monkeypatch.setattr(hai.db, "get_conn", lambda: _Dummy())
    monkeypatch.setattr(hai, "compute_recovery_index",
                        lambda target, window_days=7, conn=None:
                        {"composite": seq.get((date(2026, 8, 5) - target).days)})
    out = hai.recovery_series(date(2026, 8, 5), days=4)
    assert out == [70.0, 65.0, 68.0]
