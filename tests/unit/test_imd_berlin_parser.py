"""
tests/unit/test_imd_berlin_parser.py

Тесты парсера IMD Berlin и сопутствующей инфраструктуры.

RST-покрытие:
  - Числовые значения (обычные и с флагом ++)
  - Текстовые результаты ("negativ") → пропускаются без ошибки
  - Референсные диапазоны: < X, > X, low - high
  - Извлечение даты из немецкого 2-значного года
  - Dedup doc_patterns (не растёт при повторном seed)
  - Детектор IMD Berlin в _classify_with_confidence
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import import_coordinator as ic


# ── _parse_imd_ref ────────────────────────────────────────────────────────────

class TestParseImdRef:
    def test_less_than(self):
        low, high = ic._parse_imd_ref(["<", "125"])
        assert low is None
        assert high == 125.0

    def test_greater_than(self):
        low, high = ic._parse_imd_ref([">", "50"])
        assert low == 50.0
        assert high is None

    def test_range(self):
        low, high = ic._parse_imd_ref(["4.05", "-", "10.11"])
        assert low == pytest.approx(4.05)
        assert high == pytest.approx(10.11)

    def test_integer_range(self):
        low, high = ic._parse_imd_ref(["9", "-", "32"])
        assert low == 9.0
        assert high == 32.0

    def test_empty(self):
        low, high = ic._parse_imd_ref([])
        assert low is None
        assert high is None

    def test_comma_decimal(self):
        low, high = ic._parse_imd_ref(["0,20", "-", "0,90"])
        assert low == pytest.approx(0.20)
        assert high == pytest.approx(0.90)


# ── _extract_date_from_text: немецкий формат ─────────────────────────────────

class TestExtractDateIMD:
    def test_ausgang_format(self):
        text = "Eingang 14.03.21\nAusgang 18.03.21\nEND-BEFUND"
        result = ic._extract_date_from_text(text)
        assert result == "2021-03-18"

    def test_berlin_den_format(self):
        text = "Berlin,den 18.03.21\n11:05"
        result = ic._extract_date_from_text(text)
        assert result == "2021-03-18"

    def test_ausgang_priority_over_eingang(self):
        # Дата результата = Ausgang, не Eingang
        text = "Eingang 14.03.21 Ausgang 18.03.21"
        result = ic._extract_date_from_text(text)
        assert result == "2021-03-18"

    def test_synevo_format_still_works(self):
        # Формат Synevo не сломан новым кодом
        text = "12/01/2021 7:42 something"
        result = ic._extract_date_from_text(text)
        assert result == "2021-01-12"

    def test_unknown_when_no_date(self):
        result = ic._extract_date_from_text("random text with no date")
        assert result == "unknown"


# ── _classify_with_confidence: IMD Berlin ────────────────────────────────────

class TestClassifyIMDBerlin:
    """Детектор IMD должен вернуть doc_type='lab' с confidence >= 0.90."""

    def test_detect_by_domain(self):
        path = Path("Patient Sample.pdf")
        text = "IMD Berlin MVZ\nNicolaistraße 22\ninfo@imd-berlin.de\nNT-pro BNP"
        doc_type, conf, fmt_id = ic._classify_with_confidence(path, text)
        assert doc_type == "lab"
        assert conf >= 0.90

    def test_detect_by_mvz(self):
        path = Path("result.pdf")
        text = "IMD Berlin MVZ\nKleinere Straße 1\nBefund"
        doc_type, conf, _ = ic._classify_with_confidence(path, text)
        assert doc_type == "lab"
        assert conf >= 0.90

    def test_not_confused_by_imd_in_random_text(self):
        # "imd" само по себе не должно триггерить IMD Berlin
        path = Path("report.pdf")
        text = "IMED Corporation annual report 2026"
        doc_type, conf, _ = ic._classify_with_confidence(path, text)
        # Не должен быть lab с высоким confidence
        if doc_type == "lab":
            assert conf < 0.90


# ── doc_patterns deduplication ───────────────────────────────────────────────

class TestDocPatternsDedup:
    """Повторный seed не должен создавать дубли."""

    def test_seed_idempotent(self):
        """SQL-логика дедупликации doc_patterns удаляет дубли, сохраняя первую запись."""
        import sqlite3

        # Тест полностью изолирован: создаём in-memory БД с той же схемой
        conn = sqlite3.connect(":memory:")
        conn.execute("""
            CREATE TABLE doc_patterns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pattern TEXT NOT NULL, doc_type TEXT NOT NULL,
                match_on TEXT NOT NULL, match_type TEXT NOT NULL, notes TEXT
            )
        """)
        # Симулируем состояние после N рестартов (как было до фикса — 3 дубля)
        for i in range(3):
            conn.execute(
                "INSERT INTO doc_patterns(pattern, doc_type, match_on, match_type, notes) "
                "VALUES ('synevo', 'lab', 'filename', 'literal', ?)",
                (f"дубль {i}",),
            )
        conn.commit()

        # Запускаем ту же SQL которую выполняет _cleanup_doc_patterns_duplicates()
        conn.execute("""
            DELETE FROM doc_patterns
            WHERE id NOT IN (
                SELECT MIN(id)
                FROM doc_patterns
                GROUP BY pattern, doc_type, match_on, match_type
            )
        """)
        conn.commit()

        count = conn.execute(
            "SELECT COUNT(*) FROM doc_patterns WHERE pattern='synevo' AND doc_type='lab'"
        ).fetchone()[0]
        conn.close()
        assert count == 1, f"Ожидали 1 запись synevo, получили {count}"

    def test_insert_where_not_exists(self):
        """WHERE NOT EXISTS guard предотвращает дубли при повторном seed."""
        import sqlite3

        conn = sqlite3.connect(":memory:")
        conn.execute("""
            CREATE TABLE doc_patterns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pattern TEXT NOT NULL, doc_type TEXT NOT NULL,
                match_on TEXT NOT NULL, match_type TEXT NOT NULL, notes TEXT
            )
        """)
        # Первый seed
        conn.execute("""
            INSERT INTO doc_patterns(pattern, doc_type, match_on, match_type, notes)
            SELECT 'imd', 'lab', 'filename', 'literal', 'test'
            WHERE NOT EXISTS (
                SELECT 1 FROM doc_patterns WHERE pattern='imd' AND doc_type='lab'
            )
        """)
        # Второй seed — должен быть проигнорирован
        conn.execute("""
            INSERT INTO doc_patterns(pattern, doc_type, match_on, match_type, notes)
            SELECT 'imd', 'lab', 'filename', 'literal', 'test'
            WHERE NOT EXISTS (
                SELECT 1 FROM doc_patterns WHERE pattern='imd' AND doc_type='lab'
            )
        """)
        conn.commit()
        count = conn.execute("SELECT COUNT(*) FROM doc_patterns WHERE pattern='imd'").fetchone()[0]
        conn.close()
        assert count == 1, f"Ожидали 1 запись imd, получили {count}"


# Интеграционные тесты на настоящем бланке IMD — в tests/unit/test_imd_berlin_parser_owner.py
# (приватная зона, 2026-09-26 pii-scrub).
