"""Решения владельца 29.09: языки OCR, слова бланков конкретной страны и пороги морской
карточки — данные тенанта (system_config), не литералы публичного кода."""
import pathlib
import re
import subprocess
import sys
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def test_fallbacks_mirror_the_seeds(db, monkeypatch):
    """Резервы в коде — зеркала сидов, не вторая норма."""
    import config_db
    import env_context
    import health_db
    monkeypatch.setenv("ALLOW_WRITE_NONPRIMARY", "1")
    health_db.init_db()
    with health_db.get_conn() as c:
        rows = {r[0]: r[1:] for r in c.execute(
            "SELECT key, value_num, value_text, value_json FROM system_config "
            "WHERE key LIKE 'env.sea_%' OR key IN ('ocr.languages','docs.type_markers')")}
    assert {k: rows[k][0] for k in env_context._MARINE_FALLBACK} == env_context._MARINE_FALLBACK
    assert rows["ocr.languages"][1] == config_db._OCR_LANGUAGES_FALLBACK
    assert rows["docs.type_markers"][2] == "{}" and config_db._DOC_TYPE_MARKERS_FALLBACK == {}


def test_marine_card_follows_tenant_config(monkeypatch):
    import config_db
    import env_context as ec
    cfg = {"env.sea_waves_notable_m": 5.0, "env.sea_waves_safety_m": 9.0, "env.sea_swim_temp_c": 30.0}
    monkeypatch.setattr(config_db, "get_config", lambda k, d=None, conn=None: cfg.get(k, d))
    assert ec.from_marine({"sea_temp": 28.0, "wave_max": 1.6}) == []   # по резерву было бы «волны»


def test_doc_type_markers_come_from_tenant_config(monkeypatch):
    import config_db
    mk = {"text": {"lab": ["LABOR-BEFUND"]}, "name": {"biopsy": ["probe-x"]}}
    assert config_db.marker_doc_type(mk, "text", "… labor-befund …") == "lab"
    assert config_db.marker_doc_type(mk, "name", "scan_PROBE-X.pdf") == "biopsy"
    assert config_db.marker_doc_type({}, "text", "labor-befund") is None


@pytest.mark.host_only
def test_public_code_names_no_country_language():
    """Ни письменности, ни слов страны документов владельца в публичном коде и образе (чтение
    29.09). Слова страны — из приватного словаря (класс geo), не литералами здесь (урок C-58):
    сторож, перечисляющий скрываемое, сам его раскрывает."""
    import pii_census
    words = [w.lower() for w in pii_census.literals(["geo"]) if w.isalpha()]
    assert words, "словарь pii_census пуст — проверка была бы пустой"
    script = re.compile("[\\u0590-\\u05FF]")
    files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout.split()
    zones = pii_census._zones(ROOT)
    hits = []
    for f in files:
        if pii_census._is_private(f, zones) or not (f.endswith(".py") or f.startswith("docker/")):
            continue
        for i, ln in enumerate((ROOT / f).read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            low = ln.lower()
            if script.search(ln) or any(w in low for w in words):
                hits.append(f"{f}:{i}")
    assert not hits, hits


def test_image_ocr_default_matches_the_seed():
    """Языки образа по умолчанию = сид ocr.languages: образ не ставит «своих» языков установки."""
    import config_db
    df = (ROOT / "docker" / "Dockerfile").read_text(encoding="utf-8")
    m = re.search(r'^ARG OCR_LANGS="([^"]*)"', df, re.M)
    assert m and m.group(1).split() == config_db._OCR_LANGUAGES_FALLBACK.split("+")
