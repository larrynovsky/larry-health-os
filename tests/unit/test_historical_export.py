"""BL-PUB-16 (д): полная выгрузка Apple Health ищется правилом, а не именем файла владельца."""
import pytest

pytestmark = pytest.mark.unit


def test_largest_export_wins_and_absence_is_none(tmp_path, monkeypatch):
    import infra_config
    import import_apple_health as iah
    monkeypatch.setattr(infra_config, "cloud_dir", lambda *parts: tmp_path.joinpath(*parts))
    assert iah.historical_export() is None
    small = tmp_path / "HealthAutoExport_1" / "HealthAutoExport-a.json"
    big = tmp_path / "HealthAutoExport_2" / "HealthAutoExport-b.json"
    for p, n in ((small, 10), (big, 1000)):
        p.parent.mkdir()
        p.write_text("x" * n)
    assert iah.historical_export() == big


def test_no_region_timezone_literal_in_live_code():
    """(б): таймзона дома — только из пакета региона; литерал «Континент/Город» в живом коде
    выдаёт место жительства и расходится с пакетом молча."""
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    pat = re.compile(r'ZoneInfo\(\s*["\'][A-Z][a-z]+/[A-Za-z_]+')
    hits = []
    for p in [*root.glob("*.py"), *root.glob("*/*.py")]:
        rel = str(p.relative_to(root))
        if rel.startswith(("tests/", "plans/")):
            continue
        if pat.search(p.read_text(encoding="utf-8", errors="ignore")):
            hits.append(rel)
    assert hits == []


def test_constitution_span_from_data():
    """(г): «N лет (ГГГГ–ГГГГ)» — по датам ряда тенанта, не литерал."""
    from constitution_analysis import _span_label
    assert _span_label(("2019-03-01", "2026-09-27")) == "7 ЛЕТ (2019–2026)"
    assert _span_label((None, None)) == "ДАННЫХ НЕТ"
