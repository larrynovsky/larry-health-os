#!/usr/bin/env python3.11
"""
import_coordinator.py — универсальный координатор импорта медицинских документов.

Архитектура: три слоя с confidence-метриками.
  Слой 1: classify_with_confidence() → (doc_type, confidence, format_id)
  Слой 2: parse_with_coverage()      → ParseResult {known, unknown, errors, coverage}
  Слой 3: canonicalize()             → known → lab_results немедленно
                                        unknown → field_review queue

Заменяет прямой вызов parse_synevo_values() в import_all.py.
Обратная совместимость: import_all.py продолжает работать через старый путь.
"""

from __future__ import annotations

import hashlib
import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
log = logging.getLogger(__name__)

try:
    import health_db as db
    _DB_AVAILABLE = True
except Exception:
    _DB_AVAILABLE = False


# ── Классы результата ─────────────────────────────────────────────────────────

@dataclass
class ParsedField:
    """Одно успешно распознанное поле лабораторного анализа."""
    raw_name:      str
    canonical:     str
    value:         float
    unit:          str | None = None
    ref_low:       float | None = None
    ref_high:      float | None = None
    flagged:       bool = False


@dataclass
class UnknownField:
    """Поле с известным значением, но неизвестным canonical-именем."""
    raw_name:  str
    value:     float
    unit:      str | None = None


@dataclass
class ParseError:
    """Блок который выглядит как тест, но value не удалось извлечь."""
    raw_text: str
    reason:   str


@dataclass
class ParseResult:
    """
    Результат парсинга одного лабораторного PDF.

    known    — поля с известным canonical-именем, готовы к записи в lab_results
    unknown  — поля с распознанным значением, но неизвестным именем → queue
    errors   — блоки похожие на тест, value не извлечено
    coverage — known / (known + unknown + errors)  ∈ [0, 1]
               НЕ считает блоки не-тест (хедеры, футеры)
    format_id — id из lab_formats
    source_file — путь к PDF
    """
    known:       list[ParsedField] = field(default_factory=list)
    unknown:     list[UnknownField] = field(default_factory=list)
    errors:      list[ParseError]   = field(default_factory=list)
    format_id:   int | None = None
    source_file: str = ""

    @property
    def coverage(self) -> float:
        total = len(self.known) + len(self.unknown) + len(self.errors)
        return len(self.known) / total if total > 0 else 0.0

    @property
    def total_extracted(self) -> int:
        return len(self.known) + len(self.unknown) + len(self.errors)


# ── Слой 1: классификация с confidence ───────────────────────────────────────

# Confidence-пороги
CLASSIFY_HIGH   = 0.85   # молча принимаем
CLASSIFY_LOW    = 0.40   # ниже → Telegram "что это?"

_GEORGIAN_RANGE = ('ა', 'ჿ')

def _count_georgian(text: str) -> int:
    return sum(1 for c in text if _GEORGIAN_RANGE[0] <= c <= _GEORGIAN_RANGE[1])


def _classify_with_confidence(path: Path, text: str) -> tuple[str, float, int | None]:
    """
    Возвращает (doc_type, confidence, format_id).

    confidence: 0.0–1.0. Ниже CLASSIFY_LOW → нужно Telegram-ревью.
    format_id: id из lab_formats или None для не-лаб документов.
    """
    name = path.name.lower()
    # Длинная шапка может скрыть аналиты за пределами окна.
    # Слабые сигналы остаются в шапке, структурные (био-маркеры)
    # считаются по всему тексту — см. import_all.classify.
    t    = text.lower()[:1500]
    full = text.lower()

    # ── 1. DB-паттерны по filename ──────────────────────────────────────────
    if _DB_AVAILABLE:
        try:
            for pat in db.get_doc_patterns():
                p = pat["pattern"]
                if pat["match_on"] == "filename":
                    matched = False
                    if pat["match_type"] == "regex":
                        matched = bool(re.search(p, name))
                    else:
                        matched = p.lower() in name
                    if matched:
                        doc_type = pat["doc_type"]
                        # Для lab-документов — пробуем найти format_id
                        fmt_id = _infer_format_id(name, text) if doc_type == "lab" else None
                        return doc_type, 0.95, fmt_id
        except Exception as e:
            log.warning(f"classify_with_confidence: doc_patterns error: {e}")

    # ── 2. Bio-markers detection (language-agnostic) ─────────────────────────
    bio_markers = sum(1 for m in [
        "alt", "ast", "crp", "glucose", "ferritin", "tsh",
        "troponin", "amylase", "lipase", "magnesium", "mg",
        "b12", "folate", "ldh", "haptoglobin", "hemoglobin",
        "erythrocytes", "leucocytes", "hematocrit",
    ] if m in t)

    # ── IMD Berlin: немецкая лаборатория ───────────────────────────────────────
    t_full = text.lower()
    if "imd-berlin.de" in t_full or "imd berlin mvz" in t_full or "imd institut" in t_full:
        fmt_id = _get_format_id("imd_berlin") if _DB_AVAILABLE else None
        return "lab", 0.95, fmt_id

    georgian = _count_georgian(text[:3000])
    if georgian > 20 and bio_markers >= 2:
        # Synevo: Georgian text + bio markers
        fmt_id = _get_format_id("synevo") if _DB_AVAILABLE else None
        confidence = min(0.60 + bio_markers * 0.05, 0.97)
        return "lab", confidence, fmt_id

    # Триада CBC по ВСЕМУ тексту — структурная подпись бланка, а не упоминание.
    # Считается ОТДЕЛЬНО от bio_markers и НЕ расширяет их окно: в том списке есть
    # короткие подстроки («ast», «mg», «alt»), которые по полному тексту поймают
    # «gastroscopy» и «alternative» и превратят классификатор в генератор мусора.
    # Токены триады такой двусмысленности не имеют.
    if sum(1 for m in ["wbc", "rbc", "hgb", "hct", "plt"] if m in full) >= 3:
        return "lab", 0.90, None

    if bio_markers >= 5:
        # Strong bio signal in any language (non-Synevo lab)
        fmt_id = _get_format_id("synevo") if _DB_AVAILABLE else None  # best match
        confidence = min(0.55 + bio_markers * 0.04, 0.88)
        return "lab", confidence, fmt_id

    # ── 3. Структурные сигналы (имя файла) ─────────────────────────────────
    if any(x in name for x in ["lab eng", "lab test", "blood-test", "lab ", "полная панель", "panel"]):
        return "lab", 0.88, None
    if "pet" in name:            return "imaging_pet", 0.90, None
    if "pathology" in name:      return "pathology",   0.90, None
    if "biopsy" in name:         return "biopsy",      0.90, None
    if "gastroscop" in name:     return "endoscopy",   0.90, None
    if "colonoscop" in name:     return "endoscopy",   0.90, None
    if "discharge" in name:      return "discharge",   0.85, None
    if "therapy" in name or "drugs" in name: return "medication", 0.85, None

    # ── 4. Структурные сигналы (содержимое) ────────────────────────────────
    if "laboratory tests" in t:  return "lab", 0.88, None
    import config_db
    _by_text = config_db.marker_doc_type(config_db.doc_type_markers(), "text", text)
    if _by_text:                 return _by_text, 0.88, None  # слова бланков тенанта
    if "pet" in t and "scan" in t: return "imaging_pet", 0.82, None
    if "pathol" in t:            return "pathology", 0.75, None

    cbc = sum(1 for m in ["wbc", "rbc", "hgb", "hct", "plt"] if m in t)
    if cbc >= 3:                 return "lab", 0.82, None

    # ── 5. Не классифицировано ─────────────────────────────────────────────
    return "general_medical", 0.30, None


def _get_format_id(name: str) -> int | None:
    """Возвращает format_id из lab_formats по имени."""
    if not _DB_AVAILABLE:
        return None
    try:
        row = db.get_lab_format_by_name(name)
        return row["id"] if row else None
    except Exception:
        return None


def _infer_format_id(filename: str, text: str) -> int | None:
    """
    Определяет format_id для лабораторного документа по контентным сигналам.
    Вызывается когда doc_patterns вернул doc_type='lab' без явного format_id.
    Приоритет: Georgian → Synevo; иначе None (generic regex path).
    """
    geo = _count_georgian(text[:3000])
    if geo > 10 or "synevo" in filename.lower():
        return _get_format_id("synevo")
    return None


# ── Слой 2: парсинг с coverage ────────────────────────────────────────────────

# Порог coverage ниже которого пробуем LLM-fallback
COVERAGE_LOW = 0.50

def _parse_with_coverage(path: Path, format_id: int | None, text: str) -> ParseResult:
    """
    Диспетчер: выбирает парсер по format_id и возвращает ParseResult.
    Если format_id == Synevo → _parse_synevo().
    Иначе → _parse_generic_regex().
    """
    result = ParseResult(format_id=format_id, source_file=str(path))

    format_name = _resolve_format_name(format_id)

    if format_name == "synevo":
        try:
            result = _parse_synevo(path, format_id)
            result.source_file = str(path)
        except Exception as e:
            log.error(f"parse_with_coverage Synevo failed: {e}")
            result.errors.append(ParseError(raw_text=str(path), reason=str(e)))
    elif format_name == "imd_berlin":
        try:
            result = _parse_imd_berlin(path, format_id)
            result.source_file = str(path)
        except Exception as e:
            log.error(f"parse_with_coverage IMD Berlin failed: {e}")
            result.errors.append(ParseError(raw_text=str(path), reason=str(e)))
    else:
        result = _parse_generic_regex(text, format_id)
        result.source_file = str(path)

    return result


def _resolve_format_name(format_id: int | None) -> str:
    if format_id is None:
        return "unknown"
    if not _DB_AVAILABLE:
        return "unknown"
    try:
        row = db.get_lab_format_by_id(format_id)
        return row["name"] if row else "unknown"
    except Exception:
        return "unknown"  # silent-ok: DB недоступна → fallback на generic parser

# ── Synevo parser (4-класс) ───────────────────────────────────────────────────

def _parse_synevo(path: Path, format_id: int | None) -> ParseResult:
    """
    Парсит Synevo Digital PDF через fitz blocks.
    Возвращает ParseResult с 4 категориями блоков.
    """
    import fitz  # PyMuPDF
    result = ParseResult(format_id=format_id)

    # Загружаем alias-карту из БД (confirmed only)
    alias_map: dict[str, str] = {}
    if _DB_AVAILABLE and format_id is not None:
        try:
            alias_map = db.get_confirmed_aliases(format_id)
        except Exception as e:
            log.warning(f"_parse_synevo: alias_map load failed: {e}")

    try:
        doc = fitz.open(str(path))
    except Exception as e:
        result.errors.append(ParseError(raw_text=str(path), reason=f"fitz open: {e}"))
        return result

    for page in doc:
        blocks = page.get_text("blocks")
        blocks.sort(key=lambda b: (round(b[1] / 10) * 10, b[0]))

        for b in blocks:
            raw_block = b[4].strip()
            parts = [p.strip() for p in raw_block.split("\n") if p.strip()]

            # Блок < 3 частей — не тест (хедер/футер/служебное)
            if len(parts) < 3:
                continue  # класс 4: not_a_test

            raw_name: str | None = None
            val: float | None = None
            unit: str | None = None
            ref_low: float | None = None
            ref_high: float | None = None

            # Паттерн A: первая часть — число (value \n unit \n ref \n name)
            try:
                val = float(parts[0].replace(",", "."))
                raw_name = parts[-1]
                # Попытка извлечь unit и ref из средних частей
                if len(parts) >= 4:
                    unit = parts[1] if parts[1] else None
                    ref_str = parts[2] if len(parts) > 2 else ""
                    ref_low, ref_high = _parse_ref_range(ref_str)
            except ValueError:
                val = None  # silent-ok: не float → пробуем Паттерн B

            # Паттерн B: первая часть — имя (name \n value \n unit \n ref)
            if val is None:
                raw_name = parts[0]
                try:
                    val = float(parts[1].replace(",", "."))
                    unit = parts[2] if len(parts) > 2 else None
                    ref_str = parts[3] if len(parts) > 3 else ""
                    ref_low, ref_high = _parse_ref_range(ref_str)
                except (ValueError, IndexError):
                    val = None  # silent-ok: блок не является тестом

            # Значение не извлечено — класс 3: parse_error
            if val is None or raw_name is None:
                if any(c.isdigit() for c in raw_block):
                    result.errors.append(ParseError(
                        raw_text=raw_block[:120],
                        reason="value_not_extracted"
                    ))
                continue  # else: not_a_test

            # Канонизация имени
            clean_name = _clean_synevo_name(raw_name)
            if not clean_name:
                continue  # ref_range или мусор — не тест
            canonical = alias_map.get(clean_name)

            if canonical:
                # Класс 1: known
                flagged = (val < ref_low if ref_low is not None else False) or \
                          (val > ref_high if ref_high is not None else False)
                result.known.append(ParsedField(
                    raw_name=clean_name, canonical=canonical,
                    value=val, unit=unit,
                    ref_low=ref_low, ref_high=ref_high,
                    flagged=flagged,
                ))
            else:
                # Класс 2: unknown name
                result.unknown.append(UnknownField(
                    raw_name=clean_name, value=val, unit=unit
                ))

    return result


_REF_RANGE_RE = re.compile(r'^\d[\d\s\.\,]*[-–]\s*\d')  # "59 - 158", "1.13 - 4.52"

def _clean_synevo_name(raw: str) -> str:
    """
    Убирает Georgian-суффикс и нормализует имя теста.
    Возвращает пустую строку если raw выглядит как референсный диапазон (не имя).
    """
    clean = raw.split("\\")[0].split("/")[0].strip()
    # Убрать % и скобки в конце
    clean = re.sub(r'\s*\(.*?\)\s*$', '', clean).strip()
    # Фильтр: "59 - 158" — это ref_range, а не имя теста
    if _REF_RANGE_RE.match(clean):
        return ""
    return clean


def _parse_ref_range(s: str) -> tuple[float | None, float | None]:
    """Извлекает (low, high) из строки вида '1.6  2.6' или '4.5 - 11.0'."""
    nums = re.findall(r'\d+[\.,]?\d*', s)
    if len(nums) >= 2:
        try:
            return float(nums[0].replace(",", ".")), float(nums[1].replace(",", "."))
        except ValueError:
            pass
    return None, None



# ── IMD Berlin parser (word-column layout) ───────────────────────────────────

def _parse_imd_berlin(path: "Path", format_id: "int | None") -> "ParseResult":
    """
    Парсит IMD Berlin PDF через fitz words с группировкой по y-рядам.

    PDF имеет табличную структуру с фиксированными x-колонками:
      x < 240       : имя теста (несколько слов, без метода в скобках)
      x ≈ 240-295   : метод "(CMIA)", "(Neph.)" и т.п. — игнорируем
      x ≈ 300-325   : флаг "++" / "+" (out-of-range)
      x ≈ 322-345   : числовое значение
      x ≈ 380-420   : единица измерения (g/l, pg/ml, pmol/l…)
      x > 460       : референсный диапазон ("< 125", "4.05 - 10.11", "> 50")

    Текстовые результаты ("negativ") — пропускаются без errors.
    Заголовки секций (нет числа в value-колонке) — пропускаются.
    """
    import fitz  # PyMuPDF
    result = ParseResult(format_id=format_id)

    alias_map: dict = {}
    if _DB_AVAILABLE and format_id is not None:
        try:
            alias_map = db.get_confirmed_aliases(format_id)
        except Exception as e:
            log.warning(f"_parse_imd_berlin: alias_map load failed: {e}")

    try:
        doc = fitz.open(str(path))
    except Exception as e:
        result.errors.append(ParseError(raw_text=str(path), reason=f"fitz open: {e}"))
        return result

    # Пороги x-колонок (калиброваны по образцу бланка формата IMD)
    X_NAME_MAX  = 260   # правая граница имени теста (i.S. при x=248 входит в имя)
    X_FLAG_MIN  = 300   # начало зоны флага
    X_FLAG_MAX  = 325   # конец зоны флага
    X_VAL_MIN   = 322   # начало числового значения
    X_VAL_MAX   = 348   # конец числового значения
    X_UNIT_MIN  = 375   # начало единицы
    X_UNIT_MAX  = 425   # конец единицы
    X_REF_START = 460   # начало референсного диапазона

    for page in doc:
        words = page.get_text("words")

        # Группируем слова по y-ряду (квантуем по 4px → ±2px точность)
        rows: dict = {}
        for w in words:
            x0, y0 = w[0], w[1]
            text_w = w[4]
            y_key = round(y0 / 4) * 4
            rows.setdefault(y_key, []).append((x0, text_w))

        for y_key in sorted(rows):
            row_words = sorted(rows[y_key], key=lambda w: w[0])

            name_parts: list = []
            flag = False
            value_str = None
            unit = None
            ref_parts: list = []

            for x, tok in row_words:
                if x < X_NAME_MAX:
                    # Метод в скобках "(CMIA)" — пропускаем полностью
                    if tok.startswith("(") and tok.endswith(")"):
                        continue
                    name_parts.append(tok)
                elif X_FLAG_MIN <= x <= X_FLAG_MAX and tok in ("++", "+", "+/-", "+++"):
                    flag = True
                elif X_VAL_MIN <= x <= X_VAL_MAX:
                    value_str = tok
                elif X_UNIT_MIN <= x <= X_UNIT_MAX:
                    unit = tok
                elif x >= X_REF_START:
                    ref_parts.append(tok)
                # else: x между 240 и 300 — метод или мусор, пропускаем

            if not name_parts or value_str is None:
                continue  # заголовок секции или пустая строка

            raw_name = " ".join(name_parts).strip()
            if not raw_name:
                continue

            # Числовое значение: float после optional strip "+" / "++"
            val_clean = value_str.lstrip("+").replace(",", ".").strip()
            try:
                value = float(val_clean)
            except ValueError:
                # Текстовый результат ("negativ", "positiv") — не ошибка, просто пропуск
                continue

            ref_low, ref_high = _parse_imd_ref(ref_parts)

            # Авто-флаг по диапазону если явный флаг не стоит
            if not flag:
                if ref_low is not None and ref_high is not None:
                    flag = value < ref_low or value > ref_high
                elif ref_high is not None:   # "< X"
                    flag = value > ref_high
                elif ref_low is not None:    # "> X"
                    flag = value < ref_low

            canonical = alias_map.get(raw_name)
            if canonical:
                result.known.append(ParsedField(
                    raw_name=raw_name, canonical=canonical,
                    value=value, unit=unit,
                    ref_low=ref_low, ref_high=ref_high,
                    flagged=flag,
                ))
            else:
                result.unknown.append(UnknownField(
                    raw_name=raw_name, value=value, unit=unit
                ))

    return result


def _parse_imd_ref(ref_parts: list) -> "tuple[float | None, float | None]":
    """
    Парсит референсный диапазон из списка токенов IMD Berlin.

    Форматы:
      ["<", "125"]         → (None, 125.0)
      [">", "50"]          → (50.0, None)
      ["4.05", "-", "10.11"] → (4.05, 10.11)
      ["9", "-", "32"]     → (9.0, 32.0)
    """
    if not ref_parts:
        return None, None

    has_lt = False
    has_gt = False
    nums: list = []

    for tok in ref_parts:
        if tok == "<":
            has_lt = True
        elif tok == ">":
            has_gt = True
        elif tok == "-":
            pass  # разделитель диапазона
        else:
            try:
                nums.append(float(tok.replace(",", ".")))
            except ValueError:
                pass

    if not nums:
        return None, None
    if has_lt:
        return None, nums[0]
    if has_gt:
        return nums[0], None
    if len(nums) >= 2:
        return nums[0], nums[1]
    return None, None

# ── Generic regex fallback parser ─────────────────────────────────────────────

def _parse_generic_regex(text: str, format_id: int | None) -> ParseResult:
    """
    Базовый regex-парсер для не-Synevo форматов.
    Использует LAB_MARKERS из import_all.py как alias-источник.
    """
    result = ParseResult(format_id=format_id)
    alias_map: dict[str, str] = {}
    if _DB_AVAILABLE and format_id is not None:
        try:
            alias_map = db.get_confirmed_aliases(format_id)
        except Exception as e:
            log.warning(f"_parse_generic_regex: alias_map load failed: {e}")

    for line in text.split("\n"):
        nums = re.findall(r'\b(\d+\.?\d*)\b', line)
        if not nums:
            continue
        for raw_name, canonical in alias_map.items():
            if raw_name in line:
                try:
                    result.known.append(ParsedField(
                        raw_name=raw_name, canonical=canonical,
                        value=float(nums[0]),
                        flagged=bool(re.search(r'\bH\b|\bL\b', line))
                    ))
                except (ValueError, IndexError):
                    result.errors.append(ParseError(raw_text=line[:80], reason="value_cast"))
                break
    return result

# ── Слой 3: координатор ───────────────────────────────────────────────────────

def coordinate_import(path: Path, text: str) -> dict:
    """
    Полный цикл импорта одного файла.

    1. classify_with_confidence → (doc_type, confidence, format_id)
    2. Если doc_type != lab → возвращаем классификацию (import_all.py обрабатывает дальше)
    3. Если lab:
       a. parse_with_coverage → ParseResult
       b. known[] → lab_results через write_known_to_db() немедленно
       c. unknown[] → queue_field_reviews() (не блокирует запись known)
    4. Возвращает summary dict

    Summary:
      {
        "doc_type": str, "confidence": float, "format_id": int|None,
        "needs_review": bool,          # confidence < CLASSIFY_LOW
        "lab_imported": bool,
        "known_count": int,
        "unknown_count": int,
        "coverage": float,
        "reviews_queued": int,
      }
    """
    doc_type, confidence, format_id = _classify_with_confidence(path, text)

    summary = {
        "doc_type": doc_type,
        "confidence": confidence,
        "format_id": format_id,
        "needs_review": confidence < CLASSIFY_LOW,
        "lab_imported": False,
        "known_count": 0,
        "unknown_count": 0,
        "coverage": 0.0,
        "reviews_queued": 0,
        "low_coverage": False,
    }

    if doc_type != "lab":
        return summary

    result = _parse_with_coverage(path, format_id, text)
    summary["coverage"]      = result.coverage
    summary["known_count"]   = len(result.known)
    summary["unknown_count"] = len(result.unknown)
    summary["low_coverage"]  = result.coverage < COVERAGE_LOW and result.total_extracted > 0

    # Шаг 3b: записываем known в DB немедленно
    # Дата: сначала из текста (надёжнее для файлов без даты в имени)
    date_from_text = _extract_date_from_text(text)
    if result.known and _DB_AVAILABLE:
        try:
            _write_known_to_db(result, path, date_str=date_from_text)
            summary["lab_imported"] = True
        except Exception as e:
            log.error(f"coordinate_import: write_known failed: {e}")

    # Шаг 3c: неизвестные поля → очередь ревью (не блокирует)
    if result.unknown and _DB_AVAILABLE:
        try:
            n = _queue_field_reviews(result, path)
            summary["reviews_queued"] = n
        except Exception as e:
            log.error(f"coordinate_import: queue_reviews failed: {e}")

    return summary


def _write_known_to_db(result: ParseResult, path: Path, date_str: str | None = None) -> None:
    """РЕТАЙРНУТ 2026-07-29 (решение владельца: маршрут один, старый демонтируется).

    Писал в `lab_results` НАПРЯМУЮ (`DELETE`+`INSERT` по source), минуя
    `lab_results_staging` и человеческое ревью — открытый F-01. Пока путь был жив,
    объявленная safety boundary («распознанное сначала только в staging») держалась
    не на всех путях, то есть была декоративной.

    Фенс жёсткий и без env-хатча намеренно: §11 предупреждает, что escape-хатч
    «чтобы вдруг не сломать» — обычно экземпляр ровно того, от чего правило защищает
    (прецедент BL-LAB-CANON-1: `HEALTH_ALLOW_BIOCHEMICAL_LABWRITE` сам был
    реактиватором старого писателя). Единственный писатель канона — `lab_promote`.

    Документы не теряются: вызывающий делегирует их новому маршруту через
    `lab_intake_watcher.force_lab`.
    """
    log.error(
        f"_write_known_to_db РЕТАЙРНУТ (фенс 2026-07-29): {path.name} НЕ записан в "
        f"lab_results. Канон принадлежит единственному писателю lab_promote; путь "
        f"документа — lab_intake_watcher → lab_backfill → lab_results_staging → ревью."
    )
    return


def _write_known_to_db_RETIRED_BODY(result: ParseResult, path: Path, date_str: str | None = None) -> None:
    """Тело ретайрнутой функции. Не зовётся; оставлено читаемым один цикл,
    чтобы при разборе инцидента было видно, ЧТО именно делал старый путь."""
    import json as _json
    if not date_str or date_str == "unknown":
        date_str = _extract_date_from_path(path)
    source   = path.name  # PDF filename — используется как source в lab_results

    data = {
        "date":        date_str,
        "type":        "lab",
        "source_file": str(path),
        "source":      source,   # явно передаём чтобы import_biochemical_json использовал PDF-имя
        "results": {
            f.canonical: {
                "value":    f.value,
                "unit":     f.unit,
                "ref_low":  f.ref_low,
                "ref_high": f.ref_high,
                "flagged":  f.flagged,
            }
            for f in result.known
        }
    }
    # import_biochemical_json берёт source из Path(filepath).name (temp-имя).
    # Обходим это прямой записью в DB, не через temp-файл.
    db._ensure_lab_table()
    with db.get_conn() as conn:
        # Удаляем предыдущие записи от coordinator для этого же PDF
        conn.execute("DELETE FROM lab_results WHERE source=?", (source,))
        # Удаляем дубли из biochemical JSON для той же даты —
        # coordinator является авторитетным источником для Synevo
        if date_str and date_str != "unknown":
            conn.execute(
                "DELETE FROM lab_results WHERE date=? AND source LIKE '%.json' "
                "AND source NOT LIKE 'instrument:%'",
                (date_str,)
            )
        for test_name, item in data["results"].items():
            val = item.get("value")
            try:
                val_float = float(val) if val is not None else None
            except (TypeError, ValueError):
                val_float = None
            conn.execute(
                "INSERT INTO lab_results"
                "(date, source, test_name, value, unit, ref_low, ref_high, status)"
                "VALUES (?,?,?,?,?,?,?,?)",
                (date_str, source, test_name,
                 val_float, item.get("unit"),
                 item.get("ref_low"), item.get("ref_high"),
                 "flagged" if item.get("flagged") else "normal"),
            )


def _queue_field_reviews(result: ParseResult, path: Path) -> int:
    """Ставит unknown-поля в очередь ревью. Возвращает кол-во новых записей."""
    unknown_list = [
        {"raw_name": u.raw_name, "value": u.value, "unit": u.unit}
        for u in result.unknown
    ]
    return db.queue_field_reviews(
        format_id=result.format_id,
        source_file=str(path),
        unknown_fields=unknown_list,
    )


def _extract_date_from_text(text: str) -> str:
    """
    Извлекает дату из содержимого PDF (надёжнее для файлов без даты в имени).
    Synevo: "DD/MM/YYYY   H:MM" (с временем — дата сбора, не рождения пациента).
    Общий: DD/MM/YYYY или DD.MM.YYYY.
    """
    # IMD Berlin: "Ausgang DD.MM.YY" или "Berlin,den DD.MM.YY" (2-значный год)
    m = re.search(r'(?:Ausgang|Berlin,den)\s+(\d{2})\.(\d{2})\.(\d{2})\b', text)
    if m:
        day, mon, yr = m.group(1), m.group(2), m.group(3)
        year = f"20{yr}"  # 2-digit year → 21st century
        return f"{year}-{mon}-{day}"

    # Synevo: дата сбора идёт со временем — ищем раньше чтобы не захватить дату рождения
    m = re.search(r'(\d{2})/(\d{2})/(\d{4})\s+\d{1,2}:\d{2}', text)
    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    m = re.search(r'(\d{2})\.(\d{2})\.(\d{4})', text)
    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    m = re.search(r'(\d{2})/(\d{2})/(\d{4})', text)
    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    return "unknown"


def _extract_date_from_path(path: Path) -> str:
    """Пробует извлечь YYYY-MM-DD из имени или пути файла."""
    name = path.name
    for pat, fmt in [
        (r'(\d{2})[./\-](\d{2})[./\-](\d{4})', '{2}-{1}-{0}'),
        (r'(\d{4})[./\-](\d{2})[./\-](\d{2})', '{0}-{1}-{2}'),
    ]:
        m = re.search(pat, name)
        if m:
            g = m.groups()
            try:
                return fmt.format(*g)
            except Exception:
                pass  # silent-ok: date format string мисматч — пробуем следующий паттерн
    return "unknown"


# Fuzzy-matching перенесён в lab_fuzzy.py (один модуль — одна функция).
