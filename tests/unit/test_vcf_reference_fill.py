"""Фаза R: «позиции нет в VCF полного генома = гомозигота по референсу» в рамках (28.09).

Решение владельца: вывод только для полного генома с вызывальщиком в заголовке (и GRCh37);
CYP2D6 и HLA-B — никогда; панель и экзом — без вывода; разошлось с чипом — откат всего
вывода. Референс по построению не несёт вариантного звёздного аллеля — даже если буква
референса совпала с зашитым аллелем эффекта (класс ложного «плохого метаболизатора» DPYD*13).
Все данные синтетические.
"""
import sqlite3

import pytest

import pharmaco_pipeline as pp
import vcf_import_pipeline as v

pytestmark = pytest.mark.unit

PANEL = {
    "rs4244285": {"gene": "CYP2C19", "chrom": "10", "pos": 96541616, "ref": "G"},
    "rs4986893": {"gene": "CYP2C19", "chrom": "10", "pos": 96540410, "ref": "G"},
    "rs12248560": {"gene": "CYP2C19", "chrom": "10", "pos": 96521657, "ref": "C"},
    "rs55886062": {"gene": "DPYD", "chrom": "1", "pos": 97981343, "ref": "A"},
}
HEAD_WGS = ["##fileformat=VCFv4.2", "##DeepVariant_version=1.5.0",
            "##contig=<ID=chr1,length=249250621>"]
COLS = "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSAMPLE"


def _vcf(tmp_path, header, rows, filler=0):
    lines = header + [COLS] + rows
    lines += [f"chr3\t{1000 + i}\t.\tA\tG\t30\tPASS\t.\tGT:GQ:DP\t0/1:40:30" for i in range(filler)]
    p = tmp_path / "s.vcf"
    p.write_text("\n".join(lines) + "\n")
    return p


def _db(chip=None):
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE raw_snps (rsid TEXT PRIMARY KEY, chromosome TEXT, position INTEGER, "
                "genotype TEXT, source TEXT)")
    for rsid, gt in (chip or {}).items():
        con.execute("INSERT INTO raw_snps VALUES (?,?,?,?, '23andme')", (rsid, "1", 1, gt))
    v.ensure_tables(con)
    return con


@pytest.fixture(autouse=True)
def _small_wgs(monkeypatch):
    monkeypatch.setattr(v, "WGS_MIN_RECORDS", 50)


def _src(con):
    return {r["rsid"]: (r["genotype"], r["source"]) for r in con.execute("SELECT * FROM raw_snps")}


def test_полный_геном_с_вызывальщиком_выводит_референс(tmp_path):
    con = _db()
    out = v.phase_r_reference_fill(con, _vcf(tmp_path, HEAD_WGS, [], filler=60), panel=PANEL)
    assert out["may_infer"]
    assert _src(con)["rs4244285"] == ("GG", v.SRC_INFERRED)
    assert len(_src(con)) == len(PANEL)


@pytest.mark.parametrize("header,filler,why", [
    (HEAD_WGS, 10, "экзом/панель: мало записей"),
    (["##fileformat=VCFv4.2", "##contig=<ID=chr1,length=249250621>"], 60, "нет вызывальщика"),
    (["##fileformat=VCFv4.2", "##DeepVariant_version=1.5.0", "##contig=<ID=chr1,length=248956422>"], 60, "GRCh38"),
])
def test_вне_рамок_вывода_нет(tmp_path, header, filler, why):
    con = _db()
    out = v.phase_r_reference_fill(con, _vcf(tmp_path, header, [], filler=filler), panel=PANEL)
    assert not out["may_infer"], why
    assert _src(con) == {}


def test_refcall_это_свидетельство_даже_в_экзоме(tmp_path):
    rows = ["chr10\t96541616\t.\tG\tA\t30\tRefCall\t.\tGT:GQ:DP\t0/0:50:30"]
    con = _db()
    v.phase_r_reference_fill(con, _vcf(tmp_path, HEAD_WGS, rows, filler=5), panel=PANEL)
    assert _src(con) == {"rs4244285": ("GG", v.SRC_REFCALL)}


def test_расхождение_с_чипом_откатывает_весь_вывод(tmp_path):
    con = _db(chip={"rs4244285": "GA"})                       # чип: гетерозигота *2
    out = v.phase_r_reference_fill(con, _vcf(tmp_path, HEAD_WGS, [], filler=60), panel=PANEL)
    assert out["rolled_back"] == ["rs4244285"]
    assert _src(con) == {"rs4244285": ("GA", "23andme")}       # чип цел, выведенного нет


def test_cyp2d6_и_hla_b_вне_панели_вывода():
    panel = v.load_panel()
    assert not {p["gene"] for p in panel.values()} & {"CYP2D6", "HLA-B"}
    assert "rs2395029" not in panel and "rs1065852" not in panel


def _pharmaco_db():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript("""
        CREATE TABLE raw_snps (rsid TEXT PRIMARY KEY, chromosome TEXT, position INTEGER,
                               genotype TEXT, source TEXT);
        CREATE TABLE genome_imports (id INTEGER PRIMARY KEY, vcf_source TEXT, phase_e_pharmaco_at TEXT);
        CREATE TABLE pharmaco_phenotypes (id INTEGER PRIMARY KEY AUTOINCREMENT, gene TEXT NOT NULL,
            star_allele_1 TEXT, star_allele_2 TEXT, phenotype TEXT NOT NULL,
            confidence TEXT NOT NULL DEFAULT 'indeterminate', coverage_snp_count INTEGER DEFAULT 0,
            genome_import_id INTEGER, computed_at TEXT NOT NULL DEFAULT (datetime('now')));
        CREATE UNIQUE INDEX uq ON pharmaco_phenotypes(gene);
        INSERT INTO genome_imports (id) VALUES (1);
    """)
    return con


def test_выведенный_референс_норма_с_пониженной_уверенностью():
    con = _pharmaco_db()
    panel = v.load_panel()
    for rsid, p in panel.items():
        con.execute("INSERT INTO raw_snps VALUES (?,?,?,?,?)",
                    (rsid, p["chrom"], p["pos"], p["ref"] * 2, pp.INFERRED_SOURCE))
    res = pp.run(con, 1)["results"]
    assert res["CYP2C19"]["star_allele_1"] == "*1" and res["CYP2C19"]["star_allele_2"] == "*1"
    assert res["CYP2C19"]["confidence"] == "medium"             # вывод — не наблюдение
    assert "Poor" not in res["DPYD"]["phenotype"]              # «A» референса ≠ аллель эффекта *13
    assert res["DPYD"]["star_allele_1"] == res["DPYD"]["star_allele_2"]
    assert res["CYP2D6"]["confidence"] == "indeterminate"       # CYP2D6 не выводится никогда
    assert res["HLA-B"]["confidence"] == "indeterminate"
