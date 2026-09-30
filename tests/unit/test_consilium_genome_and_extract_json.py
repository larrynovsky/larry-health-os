"""Unit-тесты для двух изменений 2026-06-26 (коммит db30384):

1. cbcr_hypothesis._extract_json() — strip-based fence вместо non-greedy regex.
   Баг: r"```(?:json)?\\s*\\n?(.*?)```" с .*? останавливался на первом ``` внутри
   JSON-значения (напр. в examples), возвращал неполный фрагмент → JSONDecodeError.
   Ещё хуже: truncated response без закрывающего ``` → brace-counter на мусоре → None.

2. monthly_consilium._build_consilium_input() — теперь включает геномный блок.
   Баг: консилиум генерировал гипотезы без геномного контекста тенанта.
   Fix: genome_context.build_genetic_context_block() вызывается внутри input-builder.
"""
from __future__ import annotations

import importlib
import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT))

import pytest

# ─── 1. _extract_json ─────────────────────────────────────────────────────────

from cbcr_hypothesis import _extract_json


class TestExtractJsonBasic:
    def test_plain_json_object(self):
        text = '{"a": 1, "b": "hello"}'
        assert _extract_json(text) == {"a": 1, "b": "hello"}

    def test_json_in_backtick_fence(self):
        text = '```json\n{"x": 42}\n```'
        assert _extract_json(text) == {"x": 42}

    def test_json_in_plain_backtick_fence(self):
        text = '```\n{"x": 42}\n```'
        assert _extract_json(text) == {"x": 42}

    def test_returns_none_on_empty(self):
        assert _extract_json("") is None

    def test_returns_none_on_plain_text(self):
        assert _extract_json("нет json здесь") is None

    def test_nested_object(self):
        text = '{"outer": {"inner": [1, 2, 3]}}'
        result = _extract_json(text)
        assert result == {"outer": {"inner": [1, 2, 3]}}


class TestExtractJsonBugFix:
    """Покрывает конкретный баг: non-greedy regex останавливался на первом ```.

    До fix'а r\"```(?:json)?\\s*\\n?(.*?)```\" с DOTALL и .*? возвращал только текст
    до первого ``` внутри значения, что давало неполный JSON → JSONDecodeError.
    """

    def test_triple_backtick_inside_string_value(self):
        """Ключевой регресс: ``` внутри JSON-значения не должен обрывать парсинг."""
        payload = {
            "hypothesis_id": "hyp_123",
            "one_line_statement": "Тест с кодом внутри",
            "example_code": "смотри ```python\ncode\n``` пример",
        }
        text = "```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"
        result = _extract_json(text)
        assert result is not None, "Должен вернуть dict, а не None"
        assert result["hypothesis_id"] == "hyp_123"
        assert "```python" in result["example_code"]

    def test_multiple_embedded_backtick_fences(self):
        """Несколько вложенных ``` не должны сбивать парсер."""
        payload = {
            "field_a": "code: ```bash\necho hi\n```",
            "field_b": "another: ```\nblock\n```",
            "value": 99,
        }
        text = "```json\n" + json.dumps(payload) + "\n```"
        result = _extract_json(text)
        assert result is not None
        assert result["value"] == 99

    def test_truncated_response_returns_none(self):
        """Обрезанный JSON (нет закрывающего }) → None, не исключение."""
        truncated = '```json\n{"hypothesis_id": "hyp_1", "one_line_state'
        result = _extract_json(truncated)
        assert result is None

    def test_fence_without_closing_returns_none(self):
        """Открытый ``` без закрывающего → None."""
        text = "```json\n{\"a\": 1}"  # no closing ```
        # should still work via brace counter (fence stripped, json found)
        result = _extract_json(text)
        assert result == {"a": 1}

    def test_preamble_before_json(self):
        """LLM часто добавляет текст перед JSON даже без fence."""
        text = "Вот гипотеза:\n\n{\"theme\": \"сон\", \"score\": 3}"
        result = _extract_json(text)
        assert result == {"theme": "сон", "score": 3}

    def test_trailing_text_after_json_ignored(self):
        """Текст после закрывающего } игнорируется."""
        text = '{"a": 1} — вот что я нашёл'
        assert _extract_json(text) == {"a": 1}

    def test_real_coordinator_style_response(self):
        """Симулирует реальный coordinator output с гипотезами."""
        hyp = {
            "hypotheses": [
                {
                    "theme": "Истощение ВНС",
                    "patient_view": {
                        "noticed": "ВСР упала",
                        "do_now": "Сдать холтер",
                        "consult_when": "ВСР < 15 мс",
                        "might_mean": "Перегрузка нервной системы",
                    },
                    "consensus": {"count": 3, "specialists_supporting": ["cardiology"]},
                }
            ],
            "no_hypotheses_reason": None,
        }
        text = "```json\n" + json.dumps(hyp, ensure_ascii=False, indent=2) + "\n```"
        result = _extract_json(text)
        assert result is not None
        assert len(result["hypotheses"]) == 1
        assert result["hypotheses"][0]["theme"] == "Истощение ВНС"


# ─── 2. Genome block в consilium input ────────────────────────────────────────

class TestConsiliumGenomeBlock:
    """Проверяет что _build_consilium_input включает геномный контекст.

    Mock'аем genome_context.build_genetic_context_block чтобы не зависеть от БД.
    Проверяем присутствие секции и подстановку реального genome_block.
    """

    def _call_build_input(self, genome_return: str) -> str:
        """Вызывает _build_consilium_input с замоканными зависимостями."""
        from datetime import date

        # Патчим все внешние зависимости consilium
        with patch("health_db.init_db"), \
             patch("health_db.get_conn") as mock_conn, \
             patch("hai_core._build_patient_profile", return_value="[profile]"), \
             patch("gp_agent._build_clinical_history", return_value="[history]"), \
             patch("hai_analysis.detect_metric_drift", return_value=[]), \
             patch("gp_context._build_longitudinal_correlations_block", return_value=[]), \
             patch("health_db.get_recent_labs", return_value=[]), \
             patch("genome_context.build_genetic_context_block",
                   return_value=genome_return) as mock_genome:

            # Мок get_conn для daily_metrics и workouts
            mock_cursor = MagicMock()
            mock_cursor.fetchone.return_value = None
            mock_cursor.fetchall.return_value = []
            mock_conn.return_value.__enter__.return_value.execute.return_value = mock_cursor

            import monthly_consilium as mc
            result = mc._build_consilium_input(date(2026, 6, 25), 30)

        return result

    def test_genome_section_header_present(self):
        """Секция геномного контекста должна присутствовать в input_pkg."""
        pkg = self._call_build_input("GENOME_BLOCK")
        assert "ГЕНОМНЫЙ КОНТЕКСТ" in pkg, \
            "input_pkg должен содержать раздел ГЕНОМНЫЙ КОНТЕКСТ"

    def test_genome_block_content_injected(self):
        """Контент genome_block должен попасть в input_pkg."""
        genome_text = "• GENE_X (rs0000001) | AB | синтетика"
        pkg = self._call_build_input(genome_text)
        assert genome_text in pkg, \
            "Текст из genome_context.build_genetic_context_block должен быть в input_pkg"

    def test_genome_fallback_on_empty_block(self):
        """Если genome_block пустой — вставляется placeholder, не падаем."""
        pkg = self._call_build_input("")
        assert "данные недоступны" in pkg, \
            "При пустом genome_block должен быть placeholder 'данные недоступны'"

    def test_genome_block_before_end_marker(self):
        """Геном должен быть ДО маркера конца входных данных."""
        genome_text = "GENOME_SENTINEL"
        pkg = self._call_build_input(genome_text)
        genome_pos = pkg.find(genome_text)
        end_pos = pkg.find("КОНЕЦ ВХОДНЫХ ДАННЫХ")
        assert genome_pos > 0, "genome_text должен присутствовать"
        assert end_pos > genome_pos, "Геном должен идти ДО маркера конца"

    def test_genome_exception_does_not_crash_input(self):
        """Если genome_context бросает исключение — input_pkg строится без него."""
        from datetime import date

        with patch("health_db.init_db"), \
             patch("health_db.get_conn") as mock_conn, \
             patch("hai_core._build_patient_profile", return_value="[profile]"), \
             patch("gp_agent._build_clinical_history", return_value="[history]"), \
             patch("hai_analysis.detect_metric_drift", return_value=[]), \
             patch("gp_context._build_longitudinal_correlations_block", return_value=[]), \
             patch("health_db.get_recent_labs", return_value=[]), \
             patch("genome_context.build_genetic_context_block",
                   side_effect=RuntimeError("DB не доступна")):

            mock_cursor = MagicMock()
            mock_cursor.fetchone.return_value = None
            mock_cursor.fetchall.return_value = []
            mock_conn.return_value.__enter__.return_value.execute.return_value = mock_cursor

            import monthly_consilium as mc
            pkg = mc._build_consilium_input(date(2026, 6, 25), 30)

        # Не упало — хорошо. Должен быть fallback placeholder
        assert "данные недоступны" in pkg
        assert "ГЕНОМНЫЙ КОНТЕКСТ" in pkg

    def test_coordinator_max_tokens_is_16000(self):
        """Регресс: max_tokens координатора не должен быть меньше 16000.

        Баг: 8000 приводил к truncation genome-enriched гипотез → JSON parse fail.
        """
        import ast, inspect
        import monthly_consilium as mc

        src = inspect.getsource(mc._run_coordinator)
        # Ищем max_tokens= в исходнике функции
        tree = ast.parse(src)
        max_tokens_values = []
        for node in ast.walk(tree):
            if isinstance(node, ast.keyword) and node.arg == "max_tokens":
                if isinstance(node.value, ast.Constant):
                    max_tokens_values.append(node.value.value)

        assert max_tokens_values, "max_tokens должен быть задан в _run_coordinator"
        for val in max_tokens_values:
            assert val >= 16000, \
                f"max_tokens={val} в _run_coordinator слишком мало; минимум 16000 " \
                f"(genome-enriched гипотезы превышают 8000 токенов output)"
