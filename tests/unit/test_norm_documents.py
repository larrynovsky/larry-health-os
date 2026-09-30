"""norm_documents: документ → снимок → строки нормы (нить norm-from-documents, 2026-09-02).

Характеризация против САМОГО документа (data/norm_docs/ctcae_v5.0_2017-11-27.xlsx в репо):
1. Разбор xlsx без зависимостей даёт таблицу CTCAE (838 строк, заголовок с грейдами).
2. Грейды, которые модель в апреле переврала, читаются из документа верно:
   PLT grade 2 = 75 (было 100), ALT grade 3 = 5×ULN (было «7x»), HGB grade 2 = 10.
3. Grade 1 у терма с ULN/LLN — относительный ×1.0 (порог = референс бланка).
4. Симптом-зависимый грейд с тем же числом (Hypokalemia G2) не даёт второго порога.
5. НЕГАТИВНЫЙ КОНТРОЛЬ (исполнен): порча ячейки грейда → импорт падает громко, не молчит;
   терм из карты, которого нет в документе → ошибка.
6. Снимок ≡ импорт (в test_safety_net_thresholds), RCV считается из снимка EFLM.
"""
from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

import pytest

import norm_documents as nd

pytestmark = pytest.mark.unit


@pytest.mark.owner_data
def test_xlsx_reader_sees_ctcae_table():
    rows = nd._read_xlsx_sheet1(nd.CTCAE_FILES["CTCAE_v5.0"]["xlsx"])
    assert rows[0][2].strip() == "CTCAE Term" and rows[0][3].startswith("Grade 1")
    assert len(rows) > 800
    rows6 = nd._read_xlsx_sheet1(nd.CTCAE_FILES["CTCAE_v6.0"]["xlsx"])
    assert "CTCAE Preferred Term" in [str(h).strip() for h in rows6[0]] and len(rows6) > 4000


@pytest.mark.owner_data
def test_grades_read_from_document_not_memory():
    idx = {(r["metric"], r["direction"], r["band"]): r for r in nd.import_ctcae()}
    assert idx[("PLT", "floor", "urgent")]["value"] == 75.0          # апрель: 100
    assert idx[("PLT", "floor", "critical")]["value"] == 50.0
    assert idx[("ALT", "ceiling", "critical")]["value"] == 5.0        # апрель: «7x»
    assert idx[("ALT", "ceiling", "urgent")]["value"] == 3.0
    assert idx[("HGB", "floor", "urgent")]["value"] == 10.0
    assert idx[("HGB", "floor", "critical")]["value"] == 8.0
    assert idx[("Creatinine", "ceiling", "urgent")]["value"] == 1.5 and idx[("Creatinine", "ceiling", "urgent")]["baseline"] == "ULN"


@pytest.mark.owner_data
def test_grade1_is_reference_multiple():
    idx = {(r["metric"], r["direction"], r["band"]): r for r in nd.import_ctcae()}
    for m, d in (("HGB", "floor"), ("PLT", "floor"), ("ALT", "ceiling"), ("Potassium", "ceiling")):
        r = idx[(m, d, "warn")]
        assert r["kind"] == "relative" and r["value"] == 1.0
        assert r["baseline"] == ("ULN" if d == "ceiling" else "LLN")


@pytest.mark.owner_data
def test_symptom_gated_grade_dedup():
    rows = [r for r in nd.import_ctcae() if r["metric"] == "Potassium" and r["direction"] == "floor"]
    bands = {r["band"] for r in rows}
    assert bands == {"warn", "critical"}, rows     # G2 «Symptomatic with <LLN - 3.0» = G1 число → нет


@pytest.mark.owner_data
def test_units_scaled_to_canon():
    idx = {(r["metric"], r["direction"], r["band"]): r for r in nd.import_ctcae()}
    assert idx[("Neutrophils_abs", "floor", "urgent")]["value"] == 1.0     # v6: 1000/mm3 → 10^3/µL
    assert idx[("Neutrophils_abs", "floor", "urgent")]["unit"] == "10^3/µL"


@pytest.mark.owner_data
def test_v6_is_current_and_differs_from_v5_where_document_changed():
    """Смена версии видна дифом снимков, а не памятью: нейтрофилы сдвинуты на грейд,
    липаза G3 2→3×ULN, гипергликемия стала числовой, ALP/CPK/лимфопения выпали."""
    assert nd.CTCAE_CURRENT == "CTCAE_v6.0"
    d = set(nd.diff_ctcae("CTCAE_v5.0", "CTCAE_v6.0"))
    assert "Neutrophils_abs/floor/urgent: absolute:1.5 → absolute:1.0" in d
    assert "Lipase/ceiling/critical: relative:2.0 → relative:3.0" in d
    assert "Glucose/ceiling/urgent: — → absolute:160.0" in d
    assert "CPK/ceiling/warn: relative:1.0 → —" in d
    # v5 по-прежнему разбирается своей картой (широкий формат)
    f5 = nd.CTCAE_FILES["CTCAE_v5.0"]
    idx5 = {(r["metric"], r["direction"], r["band"]): r for r in nd.import_ctcae(f5["xlsx"], f5["terms"])}
    assert idx5[("Neutrophils_abs", "floor", "urgent")]["value"] == 1.5


@pytest.mark.owner_data
def test_long_layout_reader_builds_wide_table():
    header, body = nd._wide_table(nd._read_xlsx_sheet1(nd.CTCAE_FILES["CTCAE_v6.0"]["xlsx"]))
    assert header[2] == "CTCAE Term" and header[3] == "Grade 1"
    row = next(r for r in body if r[2] == "Thrombocytopenia")
    assert row[3].startswith("<LLN - 75,000/mm3") and row[0] == "10043554"


def _corrupt_copy(tmp_path: Path, term: str, grade_col: int, new_text: str) -> Path:
    """Копия xlsx v5 (широкая раскладка) с одной испорченной ячейкой (sheet1.xml, inlineStr)."""
    src = nd.CTCAE_FILES["CTCAE_v5.0"]["xlsx"]
    dst = tmp_path / "corrupt.xlsx"
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "xl/worksheets/sheet1.xml":
                rows = nd._read_xlsx_sheet1(src)
                r_i = next(i for i, r in enumerate(rows) if len(r) > 2 and str(r[2]).strip() == term)
                # найти ячейку строки r_i+1 в колонке grade_col (A=1): заменить на inlineStr
                import re
                col = chr(64 + grade_col)
                ref = f"{col}{r_i + 1}"
                xml = data.decode("utf-8")
                pat = re.compile(rf'<c r="{ref}"[^>]*>.*?</c>', re.S)
                assert pat.search(xml), ref
                xml = pat.sub(f'<c r="{ref}" t="inlineStr"><is><t>{new_text}</t></is></c>', xml, count=1)
                data = xml.encode("utf-8")
            zout.writestr(item, data)
    return dst


@pytest.mark.owner_data
def test_negative_control_corrupted_cell_is_loud(tmp_path):
    bad = _corrupt_copy(tmp_path, "Anemia", 5, "Hgb low-ish, see clinician")   # Grade 2 → текст без числа
    with pytest.raises(ValueError, match="Anemia.*grade 2"):
        nd.import_ctcae(bad, nd.CTCAE_FILES["CTCAE_v5.0"]["terms"])


@pytest.mark.owner_data
def test_negative_control_missing_term_is_loud(tmp_path):
    terms = json.loads(nd.TERMS_PATH.read_text(encoding="utf-8"))
    terms["terms"].append({"term": "Blood unobtainium increased", "metric": "X", "direction": "ceiling",
                           "family": "multiple", "unit": "U/L"})
    tp = tmp_path / "terms.json"
    tp.write_text(json.dumps(terms), encoding="utf-8")
    with pytest.raises(ValueError, match="не найден"):
        nd.import_ctcae(terms_path=tp)


@pytest.mark.owner_data
def test_rcv_from_eflm_snapshot():
    snap = nd.load_eflm()
    assert snap["analytes"]["CEA"]["cvi_median"] < 10, "медиана EFLM для CEA ~6.8%, не выброс 30%"
    r = nd.rcv_pct("CEA", snapshot=snap)
    assert 15 < r < 25
    # подмена CV_I меняет RCV — формула живая, не константа
    snap2 = json.loads(json.dumps(snap)); snap2["analytes"]["CEA"]["cvi_median"] *= 2
    assert nd.rcv_pct("CEA", snapshot=snap2) > r
    with pytest.raises(KeyError):
        nd.rcv_pct("Unobtainium", snapshot=snap)


@pytest.mark.owner_data
def test_documents_registry_has_checksums():
    docs = {d["id"]: d for d in nd.documents()}
    assert docs["CTCAE_v5.0"]["sha256"] and len(docs["CTCAE_v5.0"]["sha256"]) == 64
    assert docs["CTCAE_v6.0"]["sha256"] and docs["CTCAE_v6.0"]["local"].endswith("ctcae_v6.0_2026-01-26.xlsx")
    assert docs["EFLM_BV"]["sha256"]



def _patched_files(monkeypatch, tmp_path):
    import norm_documents as nd
    doc = nd.CTCAE_CURRENT
    f = dict(nd.CTCAE_FILES[doc])
    monkeypatch.setitem(nd.CTCAE_FILES, doc, {"xlsx": tmp_path / "ctcae.xlsx", "terms": f["terms"],
                                              "snapshot": tmp_path / "snap.json"})
    return nd, doc, f


class _Resp:
    def __init__(self, data): self.data = data
    def read(self): return self.data
    def __enter__(self): return self
    def __exit__(self, *a): return False


@pytest.mark.owner_data      # «скачанное» здесь — документ владельца из репо (в публичной выгрузке его нет)
def test_fetch_ctcae_builds_snapshot_equal_to_import(monkeypatch, tmp_path):
    """Публичная установка качает документ у NCI (BL-PUB-10): снимок из скачанного ≡ разбор документа."""
    nd, doc, f = _patched_files(monkeypatch, tmp_path)
    body = f["xlsx"].read_bytes()
    snap = nd.fetch_ctcae(doc, _open=lambda url: _Resp(body))
    assert nd.load_ctcae_rows(snap)["rows"] == nd.import_ctcae(f["xlsx"], f["terms"])
    assert (tmp_path / "ctcae.xlsx").read_bytes() == body


def test_fetch_ctcae_refuses_non_xlsx_and_leaves_nothing(monkeypatch, tmp_path):
    """Отрицательный контроль: сайт отдал страницу вместо файла → громкий отказ, документа и снимка нет."""
    import pytest
    nd, doc, _ = _patched_files(monkeypatch, tmp_path)
    with pytest.raises(ValueError, match="не xlsx"):
        nd.fetch_ctcae(doc, _open=lambda url: _Resp(b"<html>maintenance</html>"))
    assert not (tmp_path / "ctcae.xlsx").exists() and not (tmp_path / "snap.json").exists()
    assert not list(tmp_path.glob("*.part"))
