#!/usr/bin/env python3
"""
WellAlly-Health: полный рекурсивный импорт всей папки health/
Медицинские документы → структурированный JSON в data/
Финансовые / страховые документы → пропускаются
"""

import logging
import os, sys, json, re, subprocess, shutil
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _time_inject import get_now
log = logging.getLogger(__name__)
try:
    import health_db as db
    _DB_AVAILABLE = True
except Exception:
    _DB_AVAILABLE = False

import infra_config
HEALTH   = infra_config.cloud_dir()   # дом пути — infra_config (BL-PUB-12)
# DATA_DIR: выходные данные (biochemical JSONs и др.) пишем в канонический путь health_db.
# На основной машине → ~/health/data (локальный, читается health_db).
# На MacBook → iCloud/health/data (совпадает с HEALTH, как было раньше).
# Это устраняет расхождение: import_all.py писал в iCloud, import_all_biochemical читал из local.
DATA_DIR = (db._HEALTH_DIR / "data") if _DB_AVAILABLE else (HEALTH / "data")
# страницы PDF → PNG: временные файлы — во временном каталоге системы, а не мусором в ~
# (приёмка урока 24.09, BL-PUB-12)
import tempfile
TMP_DIR  = Path(tempfile.gettempdir()) / "health_cr_tmp"
TMP_DIR.mkdir(exist_ok=True)

# Типы, которые не требуют подтверждения от пользователя
DOC_REVIEW_AUTO_TYPES = {"lab"}

# ── Финансовые паттерны — пропускаем ─────────────────────────────────────────
SKIP_PATTERNS = [
    r"claim_form", r"transaction.statement", r"payment",
    r"invoice", r"f&d", r"поручение",
    r"surgicare\s+quotation", r"receipt", r"чек",
    r"import_cr", r"import_all",
]

def is_financial(path: Path) -> bool:
    name = path.name.lower()
    for p in SKIP_PATTERNS:
        if re.search(p, name):
            return True
    # Payments папки — только выборочно
    parts = [x.lower() for x in path.parts]
    if "payments" in parts:
        # Внутри Payments берём только явно медицинские файлы
        medical_in_payments = [
            "pathology", "biopsy", "gastroscopy", "colonoscopy",
            "discharge", "lab", "pet", "port",
            "blood", "report", "drugs", "therapy"
        ]
        return not any(kw in name for kw in medical_in_payments)
    return False

# ── OCR ───────────────────────────────────────────────────────────────────────
def pdf_to_images(pdf_path: Path):
    import fitz
    doc = fitz.open(str(pdf_path))
    images = []
    for i, page in enumerate(doc):
        pix = page.get_pixmap(dpi=150)
        out = TMP_DIR / f"page_{i}.png"
        pix.save(str(out))
        images.append(out)
    return images

def _tesseract() -> str:
    """Путь к tesseract. Голое имя зависит от PATH процесса; launchd может
    запускаться без Homebrew в PATH. Явный запасной путь предотвращает
    сохранение ошибки запуска OCR вместо текста документа."""
    return shutil.which("tesseract") or "/opt/homebrew/bin/tesseract"


def ocr_image(img_path: Path) -> str:
    import config_db
    lang = config_db.ocr_languages()   # языки — данные тенанта (system_config), не код

    r = subprocess.run(
        [_tesseract(), img_path.name, "stdout", "-l", lang],
        capture_output=True, cwd=str(img_path.parent)
    )
    return r.stdout.decode("utf-8", errors="replace") if r.returncode == 0 else ""

def ocr_pdf(pdf_path: Path) -> str:
    try:
        images = pdf_to_images(pdf_path)
        return "\n".join(ocr_image(img) for img in images)
    except Exception as e:
        return f"[OCR error: {e}]"

def extract_text(pdf_path: Path) -> str:
    """Извлекает текст из PDF. Для digital PDF использует fitz (быстро, точно).
    Для scanned PDF падает на tesseract OCR. Критично для не-латинских шрифтов —
    tesseract без нужных lang-пакетов даёт мусор."""
    try:
        import fitz
        doc = fitz.open(str(pdf_path))
        text = "\n".join(page.get_text() for page in doc)
        if len(text.strip()) > 200:  # digital PDF — текст извлечён
            return text
    except Exception as e:
        log.warning(f"fitz failed for {pdf_path.name}: {e}")
    # Fallback: OCR для scanned PDF
    return ocr_pdf(pdf_path)

# ── Классификация ─────────────────────────────────────────────────────────────

# Кэш паттернов из БД, загружается при старте модуля
_DOC_PATTERNS: list = []


def _load_doc_patterns() -> list:
    """Загружает паттерны из doc_patterns. GUARD: пустая таблица = RuntimeError.
    DB недоступна → [] + log.error (не падаем при импорте модуля)."""
    global _DOC_PATTERNS
    if not _DB_AVAILABLE:
        log.error("classify: DB недоступна, doc_patterns не загружены")
        _DOC_PATTERNS = []
        return _DOC_PATTERNS
    try:
        patterns = db.get_doc_patterns()
        if len(patterns) == 0:
            log.critical("classify: doc_patterns пустая — migrate не запускалась?")
            raise RuntimeError(
                "doc_patterns пустая — запустите db.init_db() перед импортом"
            )
        _DOC_PATTERNS = patterns
        return _DOC_PATTERNS
    except RuntimeError:
        raise
    except Exception as e:
        log.error(f"classify: ошибка загрузки doc_patterns: {e}")
        _DOC_PATTERNS = []
        return _DOC_PATTERNS


# Загружаем паттерны при старте модуля
_load_doc_patterns()


def classify(path: Path, text: str) -> str:
    name = path.name.lower()
    # ОКНО. Слабые однословные сигналы («pathol», «discharge») читаются по шапке:
    # встретившись в середине выписки, они означают упоминание, а не тип документа.
    # СТРУКТУРНЫЕ сигналы (триада CBC, шапка таблицы результатов) — по ВСЕМУ тексту:
    # Ограниченное окно может закончиться внутри длинной шапки с реквизитами.
    # Тогда структурные маркеры таблицы за шапкой останутся невидимыми,
    # и лабораторный документ получит неверный тип.
    t    = text.lower()[:800]
    full = text.lower()

    # 1. DB-паттерны по filename — приоритет (Rule #9: персональные справочники в БД)
    for pat in _DOC_PATTERNS:
        if pat["match_on"] == "filename":
            p = pat["pattern"]
            if pat["match_type"] == "regex":
                if re.search(p, name):
                    return pat["doc_type"]
            else:  # literal
                if p.lower() in name:
                    return pat["doc_type"]

    # 2. Структурные сигналы по имени файла (тип документа, не идентичность провайдера)
    if any(x in name for x in ["lab eng", "lab test", "blood-test", "lab ", "полная панель", "panel"]):
        return "lab"
    if "pet" in name:              return "imaging_pet"
    if "pathology" in name:        return "pathology"
    if "biopsy" in name or "биопсия" in name: return "biopsy"
    import config_db
    _mk = config_db.doc_type_markers()       # слова имён файлов тенанта (docs.type_markers)
    _by_name = config_db.marker_doc_type(_mk, "name", name)
    if _by_name:                   return _by_name
    if "gastroscop" in name or "гастроскопия" in name: return "endoscopy"
    if "colonoscop" in name or "колоноскопия" in name: return "endoscopy"
    if "discharge" in name or "выписка" in name:       return "discharge"
    if "port" in name and "install" in t: return "procedure"
    if "therapy" in name or "drugs" in name or "drug" in name:  return "medication"
    if "питание" in name or "nutrition" in name:       return "nutrition_guide"

    # 3. Структурные сигналы по содержимому (семантика типа документа, не имена)
    if "laboratory tests" in t:     return "lab"
    _by_text = config_db.marker_doc_type(_mk, "text", text)
    if _by_text:                    return _by_text   # слова бланков тенанта (docs.type_markers)
    if "pet" in t and "scan" in t:  return "imaging_pet"
    if "pathol" in t:               return "pathology"
    if "endoscop" in t:             return "endoscopy"
    if "discharge" in t:            return "discharge"

    # CBC-триада → анализ крови (работает для фото/скана без имени файла).
    # По ВСЕМУ тексту: три из пяти аббревиатур рядом — структура бланка, а не
    # упоминание; в прозе выписки такая тройка не встречается.
    cbc_signals = sum(1 for m in ["wbc", "rbc", "hgb", "hct", "plt"] if m in full)
    if cbc_signals >= 3:            return "lab"

    # Synevo / грузинские бланки — digital PDF с Georgian текстом + биохимические маркеры
    georgian = sum(1 for c in text[:3000] if 'ა' <= c <= 'ჿ')
    bio_markers = sum(1 for m in [
        "alt", "ast", "crp", "glucose", "ferritin", "tsh",
        "troponin", "amylase", "lipase", "magnesium", " mg ",
        "b12", "folate", "ldh", "haptoglobin",
    ] if m in t)
    if georgian > 20 and bio_markers >= 2:
        return "lab"

    return "general_medical"

# ── Парсеры ───────────────────────────────────────────────────────────────────
_BIRTH_CONTEXT = ("birth", "dob", "рожд")
_FILENAME_FULL = [
    (r"(\d{2})\.(\d{2})\.(\d{4})", "{2}-{1}-{0}"),
    (r"(\d{2})\.(\d{2})\.(\d{2})\b", "20{2}-{1}-{0}"),
    (r"(\d{2})[_-](\d{2})[_-](\d{4})", "{2}-{1}-{0}"),
    (r"(\d{4})[_-](\d{2})[_-](\d{2})", "{0}-{1}-{2}"),
]


def parse_date(text: str, filename: str) -> str:
    """Дата документа — от надёжного признака к угадыванию:
    подписанная «Date: DD.MM.YYYY» → полная дата в имени файла → подписанная дата
    исследования → дата забора со временем (Synevo) → первая дата текста, кроме
    даты рождения → месяц из имени файла. Иначе дата из реквизитов пациента
    может стать датой приёма, а повторный импорт — создать дубль."""
    m = re.search(r"Date:\s*(\d{2})\.(\d{2})\.(\d{4})", text)
    if m: return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    for pat, fmt in _FILENAME_FULL:
        mo = re.search(pat, filename)
        if mo:
            return fmt.format(*mo.groups())
    m = re.search(r"(?:examination|performed on|date of (?:visit|test))\D{0,20}(\d{1,2})/(\d{1,2})/(\d{4})",
                  text, re.I)
    if m: return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})\s+\d{1,2}:\d{2}", text)
    if m: return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    for m in re.finditer(r"(\d{2})/(\d{2})/(\d{4})", text):
        if any(k in text[max(0, m.start() - 30):m.start()].lower() for k in _BIRTH_CONTEXT):
            continue
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    mo = re.search(r"(\d{2})\.(\d{4})\b", filename)   # MM.YYYY → YYYY-MM-01
    if mo:
        return f"{mo.group(2)}-{mo.group(1)}-01"
    return "unknown"

LAB_MARKERS = {
    "WBC": "WBC", "RBC": "RBC", "HGB": "HGB", "HCT": "HCT",
    "MCV": "MCV", "MCH": "MCH", "MCHC": "MCHC", "RDW": "RDW",
    "PLT": "PLT", "CRP": "CRP",
    "Glucose-B": "Glucose", "UREA-B": "Urea",
    "Creatinine-B": "Creatinine", "Creatinme-B": "Creatinine",
    "Sodium-B": "Sodium", "Potassium-B": "Potassium",
    "Chloride-B": "Chloride", "Calcium-B": "Calcium",
    "Albumin-B": "Albumin", "Protein, total-B": "Total_Protein",
    "ALT(GPT)-B": "ALT", "AST(GOT)-B": "AST",
    "LDH": "LDH", "Amylase-B": "Amylase",
    "Ferritine": "Ferritin", "Iron-B": "Iron",
    "Cholesterol, total-B": "Cholesterol",
    "Triglycerides-B": "Triglycerides",
    "LDL calculated-B": "LDL",
    "CEA": "CEA", "Carcinoembryonic": "CEA",
    "Ca9.9": "CA19-9", "Ca 19": "CA19-9", "Ca 19.9": "CA19-9", "Ca 19-9": "CA19-9",
    "NEUTRO%": "Neutrophils_pct", "LYMPHO%": "Lymphocytes_pct",
    "MONO%": "Monocytes_pct",
}

def parse_lab_values(text: str) -> dict:
    results = {}
    for line in text.split("\n"):
        for key, label in LAB_MARKERS.items():
            if key in line:
                nums = re.findall(r"\b(\d+\.?\d*)\b", line)
                if nums:
                    results[label] = {
                        "value": float(nums[0]),
                        "flagged": bool(re.search(r"\bH\b|\bL\b", line))
                    }
                break
    return results

# ── Synevo (Georgian/multilingual digital PDF) ────────────────────────────────
_GEORGIAN_RANGE = ('ა', 'ჿ')

def is_synevo_format(text: str) -> bool:
    """Детектирует многострочный формат Synevo по грузинским символам в тексте."""
    georgian = sum(1 for c in text if _GEORGIAN_RANGE[0] <= c <= _GEORGIAN_RANGE[1])
    return georgian > 20

# Маркеры Synevo — имена строго как они появляются в тексте (первая строка теста)
# Маппинг Synevo-имён (как появляются в блоке) → canonical label
SYNEVO_NAME_MAP = {
    "CRP":                  "CRP",
    "AST":                  "AST",
    "ALT":                  "ALT",
    "GGT":                  "GGT",
    "Total Bilirubin":      "Bilirubin_total",
    "Glucose":              "Glucose",
    "Hemoglobin A1C":       "HbA1c",
    "Lipase":               "Lipase",
    "TSH":                  "TSH",
    "CA-125 II":            "CA125",
    "CA-125":               "CA125",
    "Triglycerides":        "Triglycerides",
    "Total Cholesterol":    "Cholesterol_Total",
    "HDL Cholesterol":      "HDL",
    "LDL Cholesterol":      "LDL",
    "Atherogenic index":    "Atherogenic_index",
    "VLDL":                 "VLDL",
    "Ferritin":             "Ferritin",
    "Transferrin":          "Transferrin",
    "Iron":                 "Iron",
    "Leucocytes":           "WBC",
    "Erythrocytes":         "RBC",
    "Hemoglobin":           "HGB",
    "Hematocrit":           "HCT",
    "MCV":                  "MCV",
    "MCH":                  "MCH",
    "MCHC":                 "MCHC",
    "MPV":                  "MPV",
    "RDW":                  "RDW",
    "PLT":                  "PLT",
    "Platelets":            "PLT",
    "Vitamin D":            "Vitamin_D",
    "Vitamin B12":          "Vitamin_B12",
    "Folate":               "Folate",
    "ESR":                  "ESR",
    "Creatinine":           "Creatinine",
    "Urea":                 "Urea",
    # ── Synevo additions ─────────────────────────────────────────────────────
    "Mg":                   "Magnesium",        # Synevo: "Mg \\ მაგნიუმი"
    "P-amylase":            "Amylase_pancreatic",  # Synevo: "P-amylase \\ P ამილაზა"
    "Troponin T hs":        "Troponin",         # Synevo high-sensitivity
    "Troponin T":           "Troponin",
    # Differential CBC — Abs entries BEFORE prefix entries
    # (startswith-match would wrongly catch "Neutrophils Abs" as "Neutrophils" otherwise)
    # 2026-07-29: вели в ГОЛОЕ имя, теперь в «_abs» — это и создавало у аналита два
    # канонических имени (`Neutrophils` от этого пути, `Neutrophils_abs` от lab_canon).
    "Neutrophils Abs":      "Neutrophils_abs",
    "Lymphocytes Abs":      "Lymphocytes_abs",
    "Monocytes Abs":        "Monocytes_abs",
    "Eosinophils Abs":      "Eosinophils_abs",
    "Basophils Abs":        "Basophils_abs",
    # Differential CBC — percentages (after Abs variants).
    # ⚠️ МИНА, НЕ ТРОГАТЬ БЕЗ ЗАМЕРА: здесь голое имя означает ПРОЦЕНТ, и это верно
    # ТОЛЬКО для этого модуля. Причина — `_synevo_match_name` режет всё после «/» или
    # «\\», то есть вместе с грузинским хвостом теряет и суффикс «%» из подписи бланка
    # («Neutrophils /ნეიტროფილები %» → «Neutrophils»). В `lab_canon._key` суффикс «%»
    # СОХРАНЯЕТСЯ (правка 29.07), поэтому там голое имя означает обрезанный «Abs».
    # Два верных прочтения одного бланка, потому что режут по-разному. Если кто-то
    # научит `_synevo_match_name` сохранять «%», эти пять строк станут неверными.
    "Thrombocytes":         "PLT",              # Synevo uses "Thrombocytes" not "Platelets"
    "Neutrophils":          "Neutrophils_pct",
    "Lymphocytes":          "Lymphocytes_pct",
    "Monocytes":            "Monocytes_pct",
    "Basophilis":           "Basophils_pct",    # Synevo typo (Basophilis → Basophils)
    "Basophils":            "Basophils_pct",
    "Eosinophils":          "Eosinophils_pct",
}

def _synevo_match_name(raw: str) -> str | None:
    """Возвращает canonical label для Synevo-имени или None."""
    # Отрезаем Georgian-суффикс (после \\ или /)
    clean = raw.split("\\")[0].split("/")[0].strip().rstrip()
    for key, label in SYNEVO_NAME_MAP.items():
        if clean == key or clean.startswith(key + " ") or clean.startswith(key + "\t"):
            return label
    return None

def parse_synevo_values(pdf_path: Path) -> dict:
    """Парсер Synevo-PDF через fitz blocks.
    Synevo кладёт value+unit+ref+name в ОДИН блок, разделённый '|'.
    Два паттерна:
      A (Biochemistry/Immunology): value | unit | ref | name [Georgian]
      B (CBC): name [/Georgian] | value | unit | ref
    """
    import fitz
    results = {}
    try:
        doc = fitz.open(str(pdf_path))
    except Exception as e:
        log.error(f"parse_synevo_values: fitz open failed: {e}")
        return results

    for page in doc:
        blocks = page.get_text("blocks")
        # Сортируем по строке (y с округлением до 10px), затем по x
        blocks.sort(key=lambda b: (round(b[1] / 10) * 10, b[0]))
        for b in blocks:
            text = b[4].strip()
            parts = [p.strip() for p in text.split("\n") if p.strip()]
            if len(parts) < 3:
                continue

            # Паттерн A: первая часть — число (value \n unit \n ref \n name)
            try:
                val = float(parts[0].replace(",", "."))
                raw_name = parts[-1]
                label = _synevo_match_name(raw_name)
                if label:
                    results[label] = {"value": val, "flagged": False}
                continue
            except ValueError:
                pass

            # Паттерн B: первая часть — имя (name/Georgian \n value \n unit \n ref)
            raw_name = parts[0]
            label = _synevo_match_name(raw_name)
            if label:
                try:
                    val = float(parts[1].replace(",", "."))
                    results[label] = {"value": val, "flagged": False}
                except (ValueError, IndexError):
                    pass

    return results

# ── Сохранение ────────────────────────────────────────────────────────────────
def save(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    # Каноническое имя {date}_{type}.json делает повторный импорт идемпотентным.
    # Добавление суффикса при каждом сохранении создавало бы новые source
    # для одного документа и write-write конфликты в каноне.
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path

def make_record(pdf_path: Path, text: str, date: str, doc_type: str, extra: dict = None) -> dict:
    base = {
        "date": date,
        "type": doc_type,
        "source_file": str(pdf_path.relative_to(HEALTH)),
        "imported_at": get_now().isoformat(),
        "summary_text": text[:6000],   # вывод документа бывает к концу (заключение на ~3,3 тыс. знаков)
    }
    if extra: base.update(extra)
    return base

# ── Заключение документа приёма (27.09) ──────────────────────────────────────
# Раньше «заключением» в медкарту шли первые 500 знаков текста — шапка документа: клиника,
# адрес, имя, номер удостоверения, дата рождения. «Врачи» читали шапку вместо вывода, а
# идентификаторы человека уходили в промпты. Теперь — текст от раздела вывода, без строк
# с идентификаторами. Разделы — по порядку предпочтения: вывод, затем рекомендации/диагноз.
_CONCLUSION_HEAD = re.compile(
    r"(?im)^\W*(summary and recommendations?|summary|impression|conclusions?|заключение)\b")
_FALLBACK_HEAD = re.compile(r"(?im)^\W*(recommendations?|diagnos[ie]s|рекомендации|диагноз)\b")
_IDENTITY_LINE = re.compile(
    r"(?i)(\bID\b|\bDOB\b|date of birth|passport|patient'?s? (name|number)|^\W*(name|subject|patient)\s*:|"
    r"дата рождения|паспорт|пациент\s*:|ф\.?\s*и\.?\s*о|phone|fax|e-?mail|\btel\b|\d{7,})")


def consult_conclusion(text: str, limit: int = 1200) -> str:
    """Вывод документа приёма для медкарты: от раздела «Summary/Impression/Conclusion/Заключение»
    (иначе — «Recommendations/Diagnosis»), иначе весь текст; строки с идентификаторами человека
    (номер удостоверения, дата рождения, телефон…) выбрасываются всегда."""
    m = _CONCLUSION_HEAD.search(text) or _FALLBACK_HEAD.search(text)
    body = text[m.start():] if m else text
    lines = [ln.strip() for ln in body.splitlines()]
    kept = [ln for ln in lines if ln and not _IDENTITY_LINE.search(ln)]
    return " ".join(" ".join(kept).split())[:limit]


# ── Сохранение клинических событий в БД ──────────────────────────────────────
CLINICAL_TYPES = {
    "imaging_pet":     ("imaging",      "PET-CT"),
    "oncology_visit":  ("oncology",     "Oncologist"),
    "endoscopy":       ("endoscopy",    "Gastroenterologist"),
    "pathology":       ("pathology",    "Pathology"),
    "biopsy":          ("pathology",    "Pathology"),
    "discharge":       ("discharge",    "Hospital"),
}

def save_clinical_to_db(doc_type: str, date: str, pdf_path: Path, rec: dict):
    """Сохраняет клинически значимые документы приёмом в медкарту (events + encounters)."""
    if not _DB_AVAILABLE:
        return
    if doc_type not in CLINICAL_TYPES:
        return
    if date == "unknown":
        return
    specialist_type, specialist_name = CLINICAL_TYPES[doc_type]
    try:
        # Проверяем дубликат по дате + типу
        if db.consultation_exists(date, specialist_type):
            return
        summary = rec.get("summary_text", "")
        if not summary.startswith("[OCR error"):
            summary = consult_conclusion(summary)
        if summary.startswith("[OCR error"):
            # Текст ошибки — не находка врача: в медкарте и в промптах он читался как заключение.
            log.error(f"OCR не удался для {pdf_path.name}: {summary[:120]} — приём без текста")
            summary = ""
        db.save_consultation(
            date_str=date,
            specialist_type=specialist_type,
            specialist_name=specialist_name,
            platform="Imported from PDF",
            key_findings=summary,
            source_file=str(pdf_path.relative_to(HEALTH)),
        )
        print(f"  → DB: приём в медкарте ({specialist_type} {date})")
    except Exception as e:
        print(f"  ⚠ DB save failed: {e}")



# ── Применение подтверждённого типа (из Telegram doc review) ─────────────────

_REVIEW_TYPE_MAP = {
    "oncology":     "oncology_visit",
    "consultation": "consultation",
    "discharge":    "discharge",
    "other":        "general_medical",
}


def apply_confirmed_type(source_file: str, doc_type: str) -> None:
    """Применяет подтверждённый тип документа из Telegram doc-review.

    Для lab → запускает coordinate_import (извлекает маркеры в lab_results).
    Для клинических → читает PDF, сохраняет в consultations.

    source_file: относительный путь (от HEALTH) или абсолютный.
    doc_type: ключ из _REVIEW_TYPE_MAP или "lab".
    """
    import fitz
    from import_coordinator import coordinate_import

    path_obj = Path(source_file)
    if not path_obj.is_absolute():
        path_obj = HEALTH / source_file

    if not path_obj.exists():
        print(f"  ⚠ apply_confirmed_type: файл не найден: {path_obj}")
        return

    try:
        fitz_doc = fitz.open(str(path_obj))
        text = "\n".join(page.get_text() for page in fitz_doc)
    except Exception as e:
        print(f"  ⚠ apply_confirmed_type: fitz open failed: {e}")
        return

    if doc_type == "lab":
        # Раньше здесь звался coordinate_import — и это делало кнопку фиктивной:
        # он ПЕРЕКЛАССИФИЦИРОВАЛ документ тем же классификатором, что уже промахнулся,
        # и на строке `if doc_type != "lab": return` молча выходил. Подтверждение
        # человека переигрывалось машиной ровно в том случае, ради которого кнопка
        # и существует. Вдобавок тот путь писал прямо в lab_results, минуя staging.
        #
        # Теперь кнопка сообщает вотчеру единственное, что человек знает лучше
        # машины — «это бланк», — и распознавание идёт единственным маршрутом.
        # Не зовём распознавание прямо здесь намеренно: оно длится минуты, а это
        # телеграм-колбэк; вотчер сделает это следующим опросом и пришлёт ссылку.
        import lab_intake_watcher
        lab_intake_watcher.force_lab(path_obj)
        print(f"  ✓ {path_obj.name}: помечен как лабораторный, вотчер возьмёт его в разбор")
        return

    clinical_type = _REVIEW_TYPE_MAP.get(doc_type, "general_medical")
    date = parse_date(text, path_obj.name)
    rec = make_record(path_obj, text, date, clinical_type)
    save_clinical_to_db(clinical_type, date, path_obj, rec)
    print(f"  ✓ {clinical_type} {date} → consultations")


# ── Главный цикл ──────────────────────────────────────────────────────────────
def main():
    # Собираем все PDF рекурсивно (кроме служебных папок)
    all_pdfs = []
    for pdf in HEALTH.rglob("*"):
        if pdf.suffix.lower() != ".pdf":
            continue
        parts = [p.lower() for p in pdf.parts]
        if any(x in parts for x in ["data", ".claude", ".git"]): continue
        if pdf.name.startswith("import_"): continue
        all_pdfs.append(pdf)
    all_pdfs.sort()

    # Фильтруем финансовые
    medical = [p for p in all_pdfs if not is_financial(p)]
    skipped_fin = [p for p in all_pdfs if is_financial(p)]

    print(f"\n📂 Всего PDF: {len(all_pdfs)}")
    print(f"🏥 Медицинских: {len(medical)}")
    print(f"💰 Финансовых (пропускаем): {len(skipped_fin)}\n")

    imported, errors = [], []

    # Полные относительные пути уже импортированных файлов (фиксирует баг с дублями)
    already_imported: set = db.get_imported_sources() if _DB_AVAILABLE else set()

    for pdf in medical:
        short = str(pdf.relative_to(HEALTH))
        print(f"📄 {short}")

        # Пропускаем уже импортированные (полный путь, не только имя)
        if short in already_imported:
            print(f"  ↷ уже импортирован")
            continue

        try:
            text = extract_text(pdf)
            date = parse_date(text, pdf.name)
            doc_type = classify(pdf, text)
            print(f"  тип: {doc_type} | дата: {date}")

            if doc_type == "lab":
                # МАРШРУТ ОДИН (решение владельца 2026-07-29). Лабораторный документ
                # не разбирается здесь: разбор — дело lab_intake_watcher → lab_backfill →
                # lab_results_staging → ревью → lab_promote. Прежняя ветка звала
                # coordinate_import, который писал в lab_results НАПРЯМУЮ, минуя staging.
                # Мы лишь сообщаем вотчеру вердикт «это бланк» — тем же входом, что
                # и кнопка подтверждения, чтобы путей запуска распознавания было не два.
                import lab_intake_watcher as _liw
                _liw.force_lab(pdf)
                print(f"  → передан вотчеру на распознавание (маршрут staging→ревью)")
                imported.append(short)
                if _DB_AVAILABLE:
                    try:
                        db.mark_imported(str(pdf.relative_to(HEALTH)), doc_type)
                    except Exception as _me:
                        log.error(f"mark_imported failed: {_me}")
                continue

            # Лабораторная ветка ушла выше по `continue` (маршрут один), поэтому
            # цепочка не-лабораторных типов начинается здесь заново.
            if doc_type == "oncology_visit":
                # Specialist name from DB pattern (Rule #9 — not hardcoded)
                _name_lower = pdf.name.lower()
                doc_name = next(
                    (p["specialist_name"] for p in _DOC_PATTERNS
                     if p["match_on"] == "filename"
                     and p["doc_type"] == "oncology_visit"
                     and p["specialist_name"]
                     and p["pattern"].lower() in _name_lower),
                    "Unknown"
                )
                out = DATA_DIR / "oncology_visits" / f"{date}_{doc_name}.json"
                rec = make_record(pdf, text, date, doc_type,
                                  {"physician": f"Dr. {doc_name}"})

            elif doc_type == "imaging_pet":
                out = DATA_DIR / "imaging" / f"{date}_PET-CT.json"
                rec = make_record(pdf, text, date, doc_type,
                                  {"modality": "PET-CT"})

            elif doc_type == "pathology":
                out = DATA_DIR / "pathology" / f"{date}_pathology.json"
                rec = make_record(pdf, text, date, doc_type)

            elif doc_type == "biopsy":
                out = DATA_DIR / "pathology" / f"{date}_biopsy.json"
                rec = make_record(pdf, text, date, doc_type)

            elif doc_type == "endoscopy":
                modality = "gastroscopy" if any(x in pdf.name.lower() for x in ["gastro", "гастро"]) else "colonoscopy"
                out = DATA_DIR / "imaging" / f"{date}_{modality}.json"
                rec = make_record(pdf, text, date, doc_type,
                                  {"modality": modality})

            elif doc_type == "discharge":
                out = DATA_DIR / "discharge" / f"{date}_discharge.json"
                rec = make_record(pdf, text, date, doc_type)

            elif doc_type == "genetic":
                out = DATA_DIR / "genetic" / f"{date}_{pdf.stem[:40]}.json"
                rec = make_record(pdf, text, date, doc_type)

            elif doc_type == "procedure":
                out = DATA_DIR / "procedures" / f"{date}_{pdf.stem[:40]}.json"
                rec = make_record(pdf, text, date, doc_type)

            elif doc_type == "medication":
                out = DATA_DIR / "medications_history" / f"{date}_{pdf.stem[:40]}.json"
                rec = make_record(pdf, text, date, doc_type)

            elif doc_type in ("hospital_bill", "nutrition_guide"):
                out = DATA_DIR / "documents" / f"{date}_{doc_type}_{pdf.stem[:30]}.json"
                rec = make_record(pdf, text, date, doc_type)

            else:
                out = DATA_DIR / "documents" / f"{date}_{pdf.stem[:50]}.json"
                rec = make_record(pdf, text, date, doc_type)

            saved = save(out, rec)
            try:
                _rel = saved.relative_to(HEALTH)
            except ValueError:
                _rel = saved
            print(f"  ✓ {_rel}")
            save_clinical_to_db(doc_type, date, pdf, rec)
            imported.append(short)

            # Не-лаб документы → очередь на подтверждение типа
            if _DB_AVAILABLE and doc_type not in DOC_REVIEW_AUTO_TYPES:
                try:
                    db.save_pending_doc_review(
                        source_file=str(pdf.relative_to(HEALTH)),
                        proposed_type=doc_type,
                    )
                    print(f"  📋 ожидает подтверждения типа")
                except Exception as _pe:
                    print(f"  ⚠ pending_doc_review save failed: {_pe}")

            # Помечаем как импортированный — защита от повторного импорта
            if _DB_AVAILABLE:
                try:
                    db.mark_imported(str(pdf.relative_to(HEALTH)), doc_type)
                except Exception as _me:
                    print(f"  ⚠ mark_imported failed: {_me}")

            # HV-2: связываем лабные данные с гипотезами в testing
            if _DB_AVAILABLE and doc_type == "lab" and date != "unknown":
                try:
                    from hypothesis_lab_linker import link_labs_to_hypotheses
                    linked = link_labs_to_hypotheses(pdf.name, date)
                    if linked:
                        print(f"  🔬 Гипотезы связаны с лабами: {len(linked)}")
                except Exception as _le:
                    print(f"  ⚠ hypothesis_lab_linker: {_le}")

        except Exception as e:
            print(f"  ✗ ОШИБКА: {e}")
            errors.append(short)

    # index.json удалён (2026-05-24): вызывал EDEADLK из launchd на iCloud-пути.
    # imported_docs в health.db — единственный источник истины о дедупликации.

    print(f"\n{'='*50}")
    print(f"✅ Импортировано: {len(imported)}")
    print(f"↷  Финансовые пропущены: {len(skipped_fin)}")
    print(f"✗  Ошибки: {len(errors)}")
    if errors:
        for e in errors: print(f"   - {e}")

if __name__ == "__main__":
    main()
