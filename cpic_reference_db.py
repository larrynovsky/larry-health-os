"""cpic_reference_db.py — единый канон CPIC → 3 read-проекции (нить diagnosis-hardcode, A2-full).

Северная звезда нити: диагноз/медистория ТЕНАНТА — в данных; в коде и промпте
НИКОГДА не зашит конкретный фенотип конкретного человека. До A2 дашборд-константа
`_CPIC_DRUG_GENE` зашивала фенотип ОДНОГО человека (диплотип по одному гену и вывод по препарату) и
отдавала его ВСЕМ тенантам под ярлыком «твой фенотип» — кросс-тенант утечка генотипа
(WSTG-ATHZ-04, multi-tenant scoping).

Форма A2-full (одобрено владельцем 2026-07-17): один канон → один SEED_VERSION → три
проекции; дашборд и консилиум читают ИЗ БД, а конкретный фенотип берётся из
`pharmaco_phenotypes` СМОТРЯЩЕГО тенанта (per-process, per HEALTH_DATA_DIR).

Проекции (общая мед-справка CPIC — НЕ PII, git-seedable, одинакова у всех тенантов):
  cpic_drug_catalog     — нейтральный справочник: препарат ↔ ген ↔ поисковые синонимы.
  cpic_drug_risk        — (ген, фенотип-КЛАСС, препарат) → риск + рекомендация. Строится из
                          снимка CPIC (data/norm_docs/cpic_recommendations_*.json) по ВСЕМ
                          классам, для которых у CPIC есть рекомендация; поверх — приватный
                          слой русских кураторских текстов установки. Модель тексты не
                          дописывает. Недостача → fallback на импликацию.
  cpic_gene_implication — (ген, фенотип-подстрока) → ген-уровневый текст. МНОГОфенотипный
                          (Poor/Intermediate/Ultrarapid/…). Источник консилиума
                          (замена pharmaco_context._IMPLICATIONS) + fallback дашборда.

Утечка снята КЛЮЧОМ: drug_risk/gene_implication адресуются фенотип-КЛАССОМ (диплотип
диплотип владельца снят). Строка всплывает только когда СОБСТВЕННЫЙ фенотип смотрящего равен
этому классу — это общее CPIC-знание при условии его генома, не личность владельца.

phenotype_class ТОЧНО совпадает со строкой, которую пишет pharmaco_pipeline в
pharmaco_phenotypes.phenotype: "Normal Metabolizer", "Intermediate Metabolizer",
"Poor Metabolizer", "Ultrarapid Metabolizer", "Normal Function", "Decreased Function",
"Poor Function", "HLA-B*57:01 Carrier (het)", "indeterminate". Диплотип-суффикс
после класса — это была утечка, снят. У HLA-B "(het)" — часть класса (стоит в БД),
НЕ снимается.

Публичный вход (one-module-one-purpose):
  seed(conn=None)                              — идемпотентно засеять 3 проекции.
  build_drug_interactions(tenant_phenotypes)   — список для дашборда, скоуплен тенантом.
  get_gene_implications(gene, phenotype)        — ген-уровневые строки (консилиум), substring.
  drug_catalog() / drug_risk_for()              — низкоуровневые читатели.
"""

from __future__ import annotations

import hashlib
import json
import logging

import i18n
from pathlib import Path

log = logging.getLogger(__name__)

# Бампни при ЛЮБОМ изменении канона ниже → seed() пересоздаст все 3 проекции. Фактическая
# версия засева = SEED_VERSION + отпечатки снимка CPIC и приватного слоя (seed_version):
# правка любого из файлов пересевает проекции, а не остаётся незамеченной.
SEED_VERSION = "2026-09-27-b1"

_HERE = Path(__file__).resolve().parent
# Снимок публичного API CPIC (документ с датой; сборщик — plans/build_cpic_snapshot_20260927.py).
CPIC_SNAPSHOT = _HERE / "data" / "norm_docs" / "cpic_recommendations_2026-09-27.json"
# Кураторские русские тексты под классы тенантов установки — приватная зона (publication_zones).
CURATED_OVERLAY = _HERE / "private" / "cpic_curated_ru.json"
_NORMAL_CLASSES = {"Normal Metabolizer", "Normal Function", "HLA-B*57:01 Negative"}

# Sentinel: справочник не засеян (отличать от «у тенанта нет генома»).
SENTINEL_UNSEEDED = "[CPIC REFERENCE UNSEEDED: run cpic_reference_db.seed()]"


# ---------------------------------------------------------------------------
# КАНОН 1 — нейтральный справочник препаратов (препарат ↔ ген ↔ поисковые синонимы)
# ---------------------------------------------------------------------------
# Рекомендации под фенотип-класс здесь НЕ живут (BL-PUB-16 в).
# Вместо персонального набора cpic_drug_risk использует полный снимок CPIC
# (все классы, документ с датой) + приватный слой кураторских текстов установки.

_DRUGS: list[dict] = [
    # ── CYP2C9 ────────────────────────────────────────────────────────────
    {
        "drug_id": "warfarin",
        'drug_display_key': 'cpic.drug.warfarin',
        "drug_search": "варфарин warfarin coumadin кумадин антикоагулянт anticoagulant",
        "gene": "CYP2C9",
    },
    {
        "drug_id": "celecoxib",
        'drug_display_key': 'cpic.drug.celecoxib',
        "drug_search": "целекоксиб celecoxib celebrex целебрекс НПВС NSAID",
        "gene": "CYP2C9",
    },
    {
        "drug_id": "phenytoin",
        'drug_display_key': 'cpic.drug.phenytoin',
        "drug_search": "фенитоин phenytoin дифенин diphenylhydantoin противосудорожный anticonvulsant",
        "gene": "CYP2C9",
    },
    {
        "drug_id": "ibuprofen",
        'drug_display_key': 'cpic.drug.ibuprofen',
        "drug_search": "ибупрофен ibuprofen нурофен nurofen advil адвил НПВС",
        "gene": "CYP2C9",
    },
    # ── CYP2C19 ───────────────────────────────────────────────────────────
    {
        "drug_id": "clopidogrel",
        'drug_display_key': 'cpic.drug.clopidogrel',
        "drug_search": "клопидогрел clopidogrel plavix плавикс антитромбоцитарный antiplatelet",
        "gene": "CYP2C19",
    },
    {
        "drug_id": "omeprazole",
        'drug_display_key': 'cpic.drug.omeprazole',
        "drug_search": "омепразол omeprazole лансопразол lansoprazole эзомепразол esomeprazole пантопразол pantoprazole ИПП PPI",
        "gene": "CYP2C19",
    },
    {
        "drug_id": "escitalopram",
        'drug_display_key': 'cpic.drug.escitalopram',
        "drug_search": "эсциталопрам escitalopram ципралекс cipralex циталопрам citalopram СИОЗС SSRI антидепрессант",
        "gene": "CYP2C19",
    },
    {
        "drug_id": "voriconazole",
        'drug_display_key': 'cpic.drug.voriconazole',
        "drug_search": "вориконазол voriconazole vfend противогрибковый antifungal",
        "gene": "CYP2C19",
    },
    # ── CYP2D6 ────────────────────────────────────────────────────────────
    {
        "drug_id": "codeine",
        'drug_display_key': 'cpic.drug.codeine',
        "drug_search": "кодеин codeine трамадол tramadol опиоид opioid анальгетик analgesic",
        "gene": "CYP2D6",
    },
    {
        "drug_id": "tamoxifen",
        'drug_display_key': 'cpic.drug.tamoxifen',
        "drug_search": "тамоксифен tamoxifen nolvadex нолвадекс рак груди breast cancer",
        "gene": "CYP2D6",
    },
    {
        "drug_id": "metoprolol",
        'drug_display_key': 'cpic.drug.metoprolol',
        "drug_search": "метопролол metoprolol беталок betaloc бета-блокатор beta-blocker кардиоселективный",
        "gene": "CYP2D6",
    },
    # ── DPYD ──────────────────────────────────────────────────────────────
    {
        "drug_id": "fluorouracil",
        'drug_display_key': 'cpic.drug.fluorouracil',
        "drug_search": "фторурацил fluorouracil 5-FU химиотерапия chemotherapy капецитабин capecitabine",
        "gene": "DPYD",
    },
    # ── TPMT ──────────────────────────────────────────────────────────────
    {
        "drug_id": "azathioprine",
        'drug_display_key': 'cpic.drug.azathioprine',
        "drug_search": "азатиоприн azathioprine imuran имуран 6-меркаптопурин mercaptopurine тиогуанин thioguanine иммуносупрессант",
        "gene": "TPMT",
    },
    # ── UGT1A1 ────────────────────────────────────────────────────────────
    {
        "drug_id": "irinotecan",
        'drug_display_key': 'cpic.drug.irinotecan',
        "drug_search": "иринотекан irinotecan кампостар camptosar химиотерапия colorectal cancer колоректальный",
        "gene": "UGT1A1",
    },
    {
        "drug_id": "atazanavir",
        'drug_display_key': 'cpic.drug.atazanavir',
        "drug_search": "атазанавир atazanavir reyataz реятаз ВИЧ HIV антиретровирусный",
        "gene": "UGT1A1",
    },
    # ── SLCO1B1 ───────────────────────────────────────────────────────────
    {
        "drug_id": "simvastatin",
        'drug_display_key': 'cpic.drug.simvastatin',
        "drug_search": "симвастатин simvastatin zocor зокор статин statin холестерин cholesterol",
        "gene": "SLCO1B1",
    },
    {
        "drug_id": "atorvastatin",
        'drug_display_key': 'cpic.drug.atorvastatin',
        "drug_search": "аторвастатин atorvastatin lipitor липитор статин statin",
        "gene": "SLCO1B1",
    },
    {
        "drug_id": "rosuvastatin",
        'drug_display_key': 'cpic.drug.rosuvastatin',
        "drug_search": "розувастатин rosuvastatin crestor крестор статин statin",
        "gene": "SLCO1B1",
    },
    {
        "drug_id": "pravastatin",
        'drug_display_key': 'cpic.drug.pravastatin',
        "drug_search": "правастатин pravastatin статин statin",
        "gene": "SLCO1B1",
    },
    # ── HLA-B ─────────────────────────────────────────────────────────────
    {
        "drug_id": "abacavir",
        'drug_display_key': 'cpic.drug.abacavir',
        "drug_search": "абакавир abacavir ziagen зиаген ABC ВИЧ HIV антиретровирусный kivexa triumeq epzicom",
        "gene": "HLA-B",
    },
    {
        "drug_id": "flucloxacillin",
        'drug_display_key': 'cpic.drug.flucloxacillin',
        "drug_search": "флуклоксациллин flucloxacillin floxapen антибиотик antibiotic пенициллин penicillin",
        "gene": "HLA-B",
    },
]


# ---------------------------------------------------------------------------
# КАНОН 2 — ген-уровневые импликации (перенос pharmaco_context._IMPLICATIONS)
# ---------------------------------------------------------------------------
# (gene, phenotype_substring, implication_key). Семантика ПОДСТРОКИ сохранена:
# читатель матчит `phenotype_substring.lower() in tenant_phenotype.lower()`.
# Ключи текста; seed сохраняет прежний русский текст, person-view переводит при чтении.
# Маркер в начале строки → серьёзность: 🚨 critical, ⚠️ warning, ⚡ warning, ℹ️ ok.

_GENE_IMPLICATIONS: list[tuple[str, str, str]] = [
    # CYP2C9
    ("CYP2C9", "Poor Metabolizer",
     'cpic.implication.cyp2c9.poor_metabolizer'),
    ("CYP2C9", "Intermediate Metabolizer",
     'cpic.implication.cyp2c9.intermediate_metabolizer'),
    # CYP2C19
    ("CYP2C19", "Poor Metabolizer",
     'cpic.implication.cyp2c19.poor_metabolizer'),
    ("CYP2C19", "Intermediate Metabolizer",
     'cpic.implication.cyp2c19.intermediate_metabolizer'),
    ("CYP2C19", "Ultrarapid Metabolizer",
     'cpic.implication.cyp2c19.ultrarapid_metabolizer'),
    ("CYP2C19", "Rapid Metabolizer",
     'cpic.implication.cyp2c19.rapid_metabolizer'),
    # CYP2D6
    ("CYP2D6", "Poor Metabolizer",
     'cpic.implication.cyp2d6.poor_metabolizer'),
    ("CYP2D6", "Ultrarapid Metabolizer",
     'cpic.implication.cyp2d6.ultrarapid_metabolizer'),
    ("CYP2D6", "Intermediate Metabolizer",
     'cpic.implication.cyp2d6.intermediate_metabolizer'),
    # SLCO1B1
    ("SLCO1B1", "Poor Function",
     'cpic.implication.slco1b1.poor_function'),
    ("SLCO1B1", "Decreased Function",
     'cpic.implication.slco1b1.decreased_function'),
    # DPYD
    ("DPYD", "Poor Metabolizer",
     'cpic.implication.dpyd.poor_metabolizer'),
    ("DPYD", "Intermediate Metabolizer",
     'cpic.implication.dpyd.intermediate_metabolizer'),
    # TPMT
    ("TPMT", "Poor Metabolizer",
     'cpic.implication.tpmt.poor_metabolizer'),
    ("TPMT", "Intermediate Metabolizer",
     'cpic.implication.tpmt.intermediate_metabolizer'),
    # UGT1A1
    ("UGT1A1", "Poor Metabolizer",
     'cpic.implication.ugt1a1.poor_metabolizer'),
    ("UGT1A1", "Intermediate Metabolizer",
     'cpic.implication.ugt1a1.intermediate_metabolizer'),
    # HLA-B
    ("HLA-B", "HLA-B*57:01 Carrier",
     'cpic.implication.hla_b.hla_b_57_01_carrier'),
]


# ---------------------------------------------------------------------------
# Схема + идемпотентный seed
# ---------------------------------------------------------------------------

def _create_schema(conn) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS cpic_drug_catalog (
            drug_id      TEXT PRIMARY KEY,
            drug_display TEXT NOT NULL,
            drug_search  TEXT NOT NULL,
            gene         TEXT NOT NULL,
            seed_version TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS cpic_drug_risk (
            gene            TEXT NOT NULL,
            phenotype_class TEXT NOT NULL,
            drug_id         TEXT NOT NULL,
            risk_level      TEXT NOT NULL,
            recommendation  TEXT NOT NULL,
            seed_version    TEXT NOT NULL,
            PRIMARY KEY (drug_id, phenotype_class)
        );
        CREATE TABLE IF NOT EXISTS cpic_gene_implication (
            gene             TEXT NOT NULL,
            phenotype_match  TEXT NOT NULL,
            implication_text TEXT NOT NULL,
            seed_version     TEXT NOT NULL,
            PRIMARY KEY (gene, phenotype_match)
        );
        """
    )


def _current_seed_version(conn) -> str | None:
    try:
        row = conn.execute(
            "SELECT seed_version FROM cpic_drug_catalog LIMIT 1"
        ).fetchone()
        return row[0] if row else None
    except Exception:
        return None


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def seed_version() -> str:
    parts = [SEED_VERSION]
    for p in (CPIC_SNAPSHOT, CURATED_OVERLAY):
        parts.append(hashlib.sha256(p.read_bytes()).hexdigest()[:8] if p.exists() else "-")
    return "+".join(parts)


_CRITICAL_START = ("avoid", "prescribe an alternative", "use an alternative",
                   "choose an alternative", "select an alternative")
_CRITICAL_ANY = ("contraindicated", "not recommended")
_STANDARD_PHRASES = ("standard dosing", "desired starting dose", "recommended starting dose",
                     "label-recommended", "per standard", "standard of care dosing",
                     "at standard dose")
# Оговорки, при которых «стандартная доза» уже не стандарт: снижение, минимум, темп, предел.
_ADJUST_MARKERS = ("%", "lowest", "reduc", "≤", "<", "slower", "limit")
_NO_REC_START = ("no recommendation", "n/a")


def _first_sentence(text: str) -> str:
    return text.strip().split(". ")[0].lower()


def _item_critical(text: str) -> bool:
    """Запрет или замена САМОГО препарата — по первой фразе рекомендации CPIC. «Avoid … inhibitors»
    (совет о сопутствующих препаратах) и «no need to avoid» запретом не считаются."""
    s = _first_sentence(text)
    if any(w in s for w in _CRITICAL_ANY):
        return True
    return s.startswith(_CRITICAL_START) and "inhibitor" not in s


def _risk_level(phenotype_class: str, items: list[dict]) -> str:
    """Уровень строки снимка CPIC — прозрачным правилом по тексту CPIC, не моделью:
    неопределённый класс → unknown; запрет/замена препарата (хоть в одной популяции) →
    critical; нормальный класс или стандартная доза → ok; остальное → warning."""
    texts = [it["recommendation"] for it in items]
    if phenotype_class == "indeterminate" or all(
            _first_sentence(t).startswith(_NO_REC_START) for t in texts):
        return "unknown"
    if any(_item_critical(t) for t in texts):
        return "critical"
    if phenotype_class in _NORMAL_CLASSES or all(
            any(w in t.lower() for w in _STANDARD_PHRASES)
            and not any(m in t.lower() for m in _ADJUST_MARKERS) for t in texts):
        return "ok"
    return "warning"


def _snapshot_rows() -> list[tuple]:
    """(gene, class, drug_id, risk_level, text) из снимка CPIC; популяции — префиксом."""
    out = []
    for r in _read_json(CPIC_SNAPSHOT).get("rows", []):
        items = r["items"]
        text = " ".join(
            it["recommendation"] if it["population"] == "general" and len(items) == 1
            else f"[{it['population'].strip()}] {it['recommendation']}"
            for it in items)
        out.append((r["gene"], r["phenotype_class"], r["drug_id"],
                    _risk_level(r["phenotype_class"], items), f"CPIC: {text}"))
    return out


def seed(conn=None) -> None:
    """Идемпотентно засеять 3 проекции. No-op если уже стоит текущая версия засева.

    cpic_drug_risk = снимок CPIC (все фенотип-классы) + приватный слой кураторских текстов
    поверх (INSERT OR REPLACE). Нет снимка — строк риска нет, дашборд уходит в ген-импликацию
    или «свериться с врачом» (не «чисто»)."""
    _own = conn is None
    if _own:
        from health_db import get_conn
        conn = get_conn()
    try:
        _create_schema(conn)
        version = seed_version()
        if _current_seed_version(conn) == version:
            return  # уже засеяно этой версией
        conn.execute("DELETE FROM cpic_drug_catalog")
        conn.execute("DELETE FROM cpic_drug_risk")
        conn.execute("DELETE FROM cpic_gene_implication")
        gene_of = {d["drug_id"]: d["gene"] for d in _DRUGS}
        for d in _DRUGS:
            conn.execute(
                "INSERT INTO cpic_drug_catalog (drug_id, drug_display, drug_search, gene, seed_version) "
                "VALUES (?, ?, ?, ?, ?)",
                (d["drug_id"], i18n.t(d["drug_display_key"], "ru"), d["drug_search"], d["gene"], version),
            )
        risk = [r for r in _snapshot_rows() if r[2] in gene_of]
        risk += [(gene_of[o["drug_id"]], o["phenotype_class"], o["drug_id"], o["risk_level"],
                  o["recommendation"])
                 for o in _read_json(CURATED_OVERLAY).get("rows", []) if o["drug_id"] in gene_of]
        for (gene, cls, drug_id, level, text) in risk:
            conn.execute(
                "INSERT OR REPLACE INTO cpic_drug_risk (gene, phenotype_class, drug_id, risk_level, "
                "recommendation, seed_version) VALUES (?, ?, ?, ?, ?, ?)",
                (gene, cls, drug_id, level, text, version),
            )
        for (gene, pheno_match, key) in _GENE_IMPLICATIONS:
            conn.execute(
                "INSERT INTO cpic_gene_implication (gene, phenotype_match, implication_text, seed_version) "
                "VALUES (?, ?, ?, ?)",
                (gene, pheno_match, i18n.t(key, "ru"), version),
            )
        conn.commit()
        log.info("cpic_reference_db: seeded %d drugs, %d risk rows, %d implications @ %s",
                 len(_DRUGS), len(risk), len(_GENE_IMPLICATIONS), version)
    finally:
        if _own:
            conn.close()


# ---------------------------------------------------------------------------
# Читатели
# ---------------------------------------------------------------------------

def drug_catalog(conn=None) -> list[dict]:
    """Нейтральный справочник: [{drug_id, drug_display, drug_search, gene}]. Без фенотипа."""
    _own = conn is None
    if _own:
        from health_db import get_conn
        conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT drug_id, drug_display, drug_search, gene FROM cpic_drug_catalog "
            "ORDER BY rowid"
        ).fetchall()
        return [dict(r) if isinstance(r, dict) else
                {"drug_id": r[0], "drug_display": r[1], "drug_search": r[2], "gene": r[3]}
                for r in rows]
    finally:
        if _own:
            conn.close()


def drug_risk_for(conn, drug_id: str, phenotype_class: str) -> dict | None:
    """Curated (risk_level, recommendation) для точного (препарат, фенотип-класс) или None."""
    row = conn.execute(
        "SELECT risk_level, recommendation FROM cpic_drug_risk "
        "WHERE drug_id = ? AND phenotype_class = ?",
        (drug_id, phenotype_class),
    ).fetchone()
    if not row:
        return None
    return {"risk_level": row[0], "recommendation": row[1]}


def get_gene_implications(gene: str, phenotype: str, conn=None) -> list[str]:
    """Ген-уровневые строки, где phenotype_match — ПОДСТРОКА фенотипа тенанта.

    Полная замена pharmaco_context._get_implications: та же substring-семантика,
    порядок строк — как в каноне (ORDER BY rowid)."""
    _own = conn is None
    if _own:
        from health_db import get_conn
        conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT phenotype_match, implication_text FROM cpic_gene_implication "
            "WHERE gene = ? ORDER BY rowid",
            (gene,),
        ).fetchall()
        pheno_low = (phenotype or "").lower()
        return [text for (pm, text) in rows if pm.lower() in pheno_low]
    finally:
        if _own:
            conn.close()


def cpic_implications_for_person(gene: str, phenotype: str, conn=None,
                                 *, lang: str | None = None) -> list[str]:
    """Translate recognised seeded implications on read; keep custom text and storage intact."""
    lang = lang or i18n.lang_of()
    translations = {i18n.t(key, "ru"): i18n.t(key, lang)
                    for g, _, key in _GENE_IMPLICATIONS if g == gene}
    return [translations.get(text, text)
            for text in get_gene_implications(gene, phenotype, conn)]


# risk_level по маркеру ген-уровневой импликации (fallback дашборда).
def _risk_from_markers(implications: list[str]) -> str:
    joined = " ".join(implications)
    if "🚨" in joined:
        return "critical"
    if "⚠️" in joined or "⚡" in joined:
        return "warning"
    if "ℹ️" in joined:
        return "ok"
    return "unknown"


def build_drug_interactions(tenant_phenotypes: dict[str, str], conn=None, *, lang: str | None = None) -> list[dict]:
    """Список строк для дашборда, СКОУПЛЕННЫЙ фенотипами СМОТРЯЩЕГО тенанта.

    tenant_phenotypes: {gene -> phenotype_string} из pharmaco_phenotypes тенанта
                       (фенотипы с confidence='indeterminate' сюда лучше не класть
                       или класть как 'indeterminate' — см. вызывающий код).

    Возвращает по одной записи на препарат из нейтрального справочника, форма как
    ожидает шаблон: {drug, drug_search, gene, phenotype_context, risk_level, recommendation}.

    Слоёный fallback (по убыванию точности):
      1) есть фенотип по гену + curated drug_risk строка → точный риск/рекомендация;
      2) есть фенотип + ген-импликация (многофенотипная) → ген-уровневая заметка;
      3) есть фенотип, но нет ни того ни другого → unknown, «свериться с врачом»;
      4) нет фенотипа по гену у тенанта → «не определён» (нейтральный справочник).
    НИКОГДА не показывает чужой фенотип: всё выводится из tenant_phenotypes."""
    lang = lang or i18n.lang_of()
    display_keys = {d["drug_id"]: d["drug_display_key"] for d in _DRUGS}
    _own = conn is None
    if _own:
        from health_db import get_conn
        conn = get_conn()
    try:
        out: list[dict] = []
        for cat in drug_catalog(conn):
            gene = cat["gene"]
            pheno = tenant_phenotypes.get(gene)
            display_key = display_keys.get(cat["drug_id"])
            base = {
                "drug": (i18n.t(display_key, lang)
                         if display_key and cat["drug_display"] == i18n.t(display_key, "ru")
                         else cat["drug_display"]),
                "drug_search": cat["drug_search"],
                "gene": gene,
            }
            if not pheno:
                # (4) тенант без генотипа по гену — нейтрально, без чужих данных
                base.update({
                    "phenotype_context": i18n.t("cpic.phenotype.undetermined", lang),
                    "risk_level": "unknown",
                    "recommendation": i18n.t("cpic.advice.no_genotype", lang),
                })
                out.append(base)
                continue
            curated = drug_risk_for(conn, cat["drug_id"], pheno)
            if curated:
                # (1) точная curated CPIC-строка под фенотип тенанта
                base.update({
                    "phenotype_context": pheno,
                    "risk_level": curated["risk_level"],
                    "recommendation": curated["recommendation"],
                })
                out.append(base)
                continue
            impls = cpic_implications_for_person(gene, pheno, conn, lang=lang)
            if impls:
                # (2) fallback: ген-уровневая заметка (многофенотипная)
                base.update({
                    "phenotype_context": pheno,
                    "risk_level": _risk_from_markers(impls),
                    "recommendation": " ".join(impls),
                })
                out.append(base)
                continue
            # (3) фенотип есть, но CPIC-данных по этому классу нет
            base.update({
                "phenotype_context": pheno,
                "risk_level": "unknown",
                "recommendation": i18n.t("cpic.advice.no_recommendation", lang),
            })
            out.append(base)
        return out
    finally:
        if _own:
            conn.close()
