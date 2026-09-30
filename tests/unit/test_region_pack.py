"""region_pack: единственный читатель пакета региона (private/region.yaml, pub-prep 2026-09-23)."""
import pytest

import region_pack

pytestmark = pytest.mark.unit


@pytest.fixture
def at(tmp_path, monkeypatch):
    def _point(text):
        p = tmp_path / "region.yaml"
        if text is not None:
            p.write_text(text, encoding="utf-8")
        monkeypatch.setattr(region_pack, "PATH", p)
        region_pack._load.cache_clear()
    yield _point
    region_pack._load.cache_clear()


def test_value_from_pack(at):
    at("timezone: Atlantic/Reykjavik\ntemp_norm_c: {7: 14}\n")
    assert region_pack.value("timezone") == "Atlantic/Reykjavik"
    assert region_pack.value("temp_norm_c") == {7: 14}


def test_no_pack_gives_default_not_foreign_region(at):
    at(None)
    assert region_pack.value("timezone", "UTC") == "UTC"
    assert region_pack.value("trails") is None


def test_dust_card_name_comes_from_region_not_code(at):
    """Имя пылевого события — из пакета региона; без имени в пакете — нейтральное (BL-PUB-16 б, 27.09)."""
    import env_context
    at("dust_label: пыль из пустыни\n")
    card = [c for c in env_context.from_weather({"dust": 50}, 7) if "dust" in c.semantic_key][0]
    assert card.evidence_summary.startswith("пыль из пустыни 50")
    at("timezone: UTC\n")   # пакет без имени пылевого события
    card = [c for c in env_context.from_weather({"dust": 50}, 7) if "dust" in c.semantic_key][0]
    assert card.evidence_summary.startswith("пыль в воздухе 50")
