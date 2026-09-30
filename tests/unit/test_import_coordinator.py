"""
tests/unit/test_import_coordinator.py

Контрактные тесты import_coordinator.py:
  1. ParseResult.coverage считается правильно
  2. classify_with_confidence никогда не бросает исключение
  3. parse_with_coverage на мусоре возвращает пустой ParseResult, не падает
  4. fuzzy_suggest_canonical — пороговая логика
  5. _clean_synevo_name убирает Georgian-суффикс и скобки
  6. _similarity симметрична и нормализована
"""
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from import_coordinator import (
    ParseResult, ParsedField, UnknownField, ParseError,
    _classify_with_confidence, _parse_with_coverage,
    _clean_synevo_name,
    CLASSIFY_LOW, CLASSIFY_HIGH, COVERAGE_LOW,
)
from lab_fuzzy import top_candidates, _best_match, _bigram_similarity


# ── 1. ParseResult.coverage ───────────────────────────────────────────────────

class TestParseResultCoverage:
    def _field(self, raw="A", canonical="B"):
        return ParsedField(raw_name=raw, canonical=canonical, value=1.0)

    def test_empty(self):
        assert ParseResult().coverage == 0.0

    def test_all_known(self):
        r = ParseResult(known=[self._field("A"), self._field("B")])
        assert r.coverage == 1.0

    def test_half_known(self):
        r = ParseResult(
            known=[self._field("A")],
            unknown=[UnknownField("X", 1.0)],
        )
        assert r.coverage == pytest.approx(0.5)

    def test_with_errors(self):
        r = ParseResult(
            known=[self._field("A")],
            unknown=[UnknownField("X", 1.0)],
            errors=[ParseError("raw", "reason")],
        )
        # 1 known / (1 + 1 + 1) = 0.333...
        assert r.coverage == pytest.approx(1/3)

    def test_only_errors(self):
        r = ParseResult(errors=[ParseError("r", "e")])
        assert r.coverage == 0.0

    def test_total_extracted(self):
        r = ParseResult(
            known=[self._field()],
            unknown=[UnknownField("X", 1.0)],
            errors=[ParseError("r", "e")],
        )
        assert r.total_extracted == 3


# ── 2. classify_with_confidence — никогда не падает ──────────────────────────

class TestClassifyContract:
    """Contract: всегда возвращает (str, float, int|None), никогда не исключение.
    Тест вызывает приватную _classify_with_confidence напрямую — это OK,
    внутренняя логика тестируется через её публичный интерфейс coordinate_import."""

    @pytest.mark.parametrize("name,content", [
        ("random.pdf", ""),
        ("random.pdf", "some random text"),
        ("lab eng 01.01.26.pdf", "WBC 5.0\nRBC 4.5\nHGB 14"),
        ("10000000001.PDF", "ა" * 30 + "\nglucose\nferritin"),
        ("", ""),
        ("a" * 300 + ".pdf", "x" * 5000),
    ])
    def test_returns_tuple(self, name, content):
        path = Path(name)
        result = _classify_with_confidence(path, content)
        assert isinstance(result, tuple)
        assert len(result) == 3
        doc_type, confidence, fmt_id = result
        assert isinstance(doc_type, str)
        assert isinstance(confidence, float)
        assert 0.0 <= confidence <= 1.0
        assert fmt_id is None or isinstance(fmt_id, int)

    def test_garbage_bytes_content(self):
        path = Path("garbage.pdf")
        content = "".join(chr(i) for i in range(256))
        doc_type, confidence, fmt_id = _classify_with_confidence(path, content)
        assert isinstance(doc_type, str)

    def test_synevo_detected_via_georgian(self):
        georgian = "ა" * 25
        content = georgian + "\nglucose\nferritin\nast"
        path = Path("test.PDF")
        doc_type, confidence, fmt_id = _classify_with_confidence(path, content)
        assert doc_type == "lab"
        assert confidence >= 0.60

    def test_lab_detected_via_bio_markers_alone(self):
        # Без Georgian — но 6 bio_markers → lab (language-agnostic path)
        content = "glucose\nferritin\nhemoglobin\nleucocytes\ntroponin\nmagnesium"
        path = Path("results.pdf")
        doc_type, confidence, fmt_id = _classify_with_confidence(path, content)
        assert doc_type == "lab"
        assert confidence >= 0.55


# ── 3. parse_with_coverage — не падает на мусоре ─────────────────────────────

class TestParseContract:
    def test_nonexistent_file_returns_empty(self):
        path = Path("/nonexistent/file.pdf")
        result = _parse_with_coverage(path, None, "some text")
        assert isinstance(result, ParseResult)
        assert result.coverage == 0.0

    def test_empty_text_generic(self):
        path = Path("test.pdf")
        result = _parse_with_coverage(path, None, "")
        assert isinstance(result, ParseResult)

    def test_result_fields_types(self):
        path = Path("/nonexistent.pdf")
        result = _parse_with_coverage(path, None, "random content")
        assert isinstance(result.known, list)
        assert isinstance(result.unknown, list)
        assert isinstance(result.errors, list)
        assert isinstance(result.coverage, float)


# ── 4. _clean_synevo_name ─────────────────────────────────────────────────────

class TestCleanSynevoName:
    @pytest.mark.parametrize("raw,expected", [
        ("Mg \\ მაგნიუმი",           "Mg"),
        # "/" отсекает Georgian-суффикс вместе с "%": "Neutrophils " → "Neutrophils"
        # Это корректно — alias-ключ в карте именно "Neutrophils", не "Neutrophils %"
        ("Neutrophils /ნეიტ % (^77)", "Neutrophils"),
        ("Troponin T hs\\ ტრ",       "Troponin T hs"),
        ("Glucose",                   "Glucose"),
        ("  ALT  ",                   "ALT"),
        # Референсные диапазоны → пустая строка
        ("59 - 158",                  ""),
        ("1.13 - 4.52",               ""),
        ("28 - 33",                   ""),
    ])
    def test_cleaning(self, raw, expected):
        assert _clean_synevo_name(raw) == expected


# ── 5. _similarity ────────────────────────────────────────────────────────────

class TestSimilarity:
    # _similarity переехал в lab_fuzzy._bigram_similarity — тест обновлён
    def test_identical(self):
        assert _bigram_similarity("troponin", "troponin") == 1.0

    def test_symmetric(self):
        assert _bigram_similarity("abc", "bcd") == _bigram_similarity("bcd", "abc")

    def test_empty(self):
        assert _bigram_similarity("", "") == 1.0
        assert _bigram_similarity("abc", "") == 0.0

    def test_bounded(self):
        for a, b in [("troponin", "Troponin"), ("Mg", "Magnesium"), ("x", "xyz")]:
            s = _bigram_similarity(a.lower(), b.lower())
            assert 0.0 <= s <= 1.0

    def test_similar_names_score(self):
        s = _bigram_similarity("troponin", "troponin t hs")
        assert s > 0.5


# ── 6. fuzzy_suggest_canonical — пороги ──────────────────────────────────────

class TestFuzzySuggest:
    def test_no_db_returns_none(self):
        with patch("lab_fuzzy._DB_AVAILABLE", False):
            assert _best_match("Troponin") is None

    def test_exact_match(self):
        with patch("lab_fuzzy._DB_AVAILABLE", True), \
             patch("lab_fuzzy.db") as mock_db:
            mock_db.get_all_canonical_names.return_value = ["Troponin", "HGB", "WBC"]
            result = _best_match("Troponin", threshold=0.85)
            assert result == "Troponin"

    def test_below_threshold_returns_none(self):
        with patch("lab_fuzzy._DB_AVAILABLE", True), \
             patch("lab_fuzzy.db") as mock_db:
            mock_db.get_all_canonical_names.return_value = ["Cholesterol"]
            result = _best_match("Mg", threshold=0.85)
            assert result is None


class TestFuzzyTopCandidates:
    def test_no_db_returns_empty(self):
        with patch("lab_fuzzy._DB_AVAILABLE", False):
            assert top_candidates("Troponin") == []

    def test_returns_sorted_by_score(self):
        with patch("lab_fuzzy._DB_AVAILABLE", True), \
             patch("lab_fuzzy.db") as mock_db:
            mock_db.get_all_canonical_names.return_value = [
                "Troponin", "TSH", "Glucose", "HGB"
            ]
            results = top_candidates("Troponin_hs", n=3, min_score=0.0)
            assert len(results) <= 3
            assert results[0][0] == "Troponin"
            scores = [s for _, s in results]
            assert scores == sorted(scores, reverse=True)

    def test_min_score_filter(self):
        with patch("lab_fuzzy._DB_AVAILABLE", True), \
             patch("lab_fuzzy.db") as mock_db:
            mock_db.get_all_canonical_names.return_value = ["Cholesterol", "TSH"]
            results = top_candidates("XYZABC", n=3, min_score=0.5)
            assert results == []

    def test_n_limit(self):
        with patch("lab_fuzzy._DB_AVAILABLE", True), \
             patch("lab_fuzzy.db") as mock_db:
            mock_db.get_all_canonical_names.return_value = [
                "A", "AB", "ABC", "ABCD", "ABCDE"
            ]
            results = top_candidates("AB", n=2, min_score=0.0)
            assert len(results) == 2
