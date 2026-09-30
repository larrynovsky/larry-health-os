"""
Сторож промпт-инструкции вплетания блоков брифа (Bug 1, 2026-07-14).

Класс бага: гейт кладёт карту в user_content, но промпт не инструктирует LLM
её вплести → Sonnet роняет блок в тексте. Так было со СРЕДА (починено раньше),
потом обнаружено то же для ПЛАН и ЕДА. Тест держит инвариант: под MORNING_BRIEF_GATE=1
промпт обязывает вплести ВСЕ три контентных блока, не только СРЕДА.

Тест ПРОВОДКИ (RST: test the wiring), не функции: падает, если кто-то добавит
новый гейт-блок в render, но забудет инструкцию вплетания. Бьём по чистому
хелперу `_gate_weave_instruction` — без БД, изоляция намеренная.
"""
from __future__ import annotations

import pytest

import gp_agent

pytestmark = pytest.mark.unit

# Блоки, которые render кладёт в user_content под гейтом (gp_agent.generate_daily_report).
# Каждый ДОЛЖЕН иметь инструкцию вплетания в промпте, иначе LLM роняет его непоследовательно.
GATED_BLOCKS = ["СРЕДА", "ПЛАН", "ЕДА"]


def test_prompt_weaves_all_gated_blocks(monkeypatch):
    monkeypatch.setenv("MORNING_BRIEF_GATE", "1")
    instr = gp_agent._gate_weave_instruction()
    for block in GATED_BLOCKS:
        assert f"«{block}»" in instr, (
            f"Промпт не инструктирует вплетать блок «{block}» — "
            f"LLM уронит его в тексте (класс СРЕДА-бага)."
        )
    # Инструкция требует ДЕЙСТВИЕ (вплести/назови), не просто упоминает блок.
    assert "вплети" in instr


def test_gate_off_no_weave_no_prime(monkeypatch):
    # Гейт OFF → блоков в контексте нет → инструкций вплетания тоже быть не должно (без шума).
    monkeypatch.delenv("MORNING_BRIEF_GATE", raising=False)
    instr = gp_agent._gate_weave_instruction()
    assert instr == ""


def test_helper_wired_into_daily_prompt():
    # Проводка: builder реально зовёт хелпер (не осталась мёртвая копия инструкции).
    import inspect
    src = inspect.getsource(gp_agent._build_gp_daily_prompt)
    assert "_gate_weave_instruction()" in src
    assert "_TONE_INSTRUCTION" in src  # тон-инструкция вплетена в промпт


def test_anti_confabulation_gene_rule(monkeypatch):
    # Под гейтом промпт ЯВНО запрещает называть гены/rs без блока ГЕНОМНЫЙ КОНТЕКСТ.
    monkeypatch.setenv("MORNING_BRIEF_GATE", "1")
    instr = gp_agent._gate_weave_instruction()
    assert "НИ ОДНОГО гена" in instr and "rs-номера" in instr


def test_tone_instruction_equal_register():
    # Тон-инструкция запрещает императив и требует «на равных».
    t = gp_agent._TONE_INSTRUCTION
    assert "на равных" in t
    assert "императив" in t.lower() or "командуй" in t.lower()


def test_format_instruction_paragraphs():
    # ФОРМАТ: абзацы через пустую строку, не кирпич (запрос владельца 2026-07-14).
    t = gp_agent._TONE_INSTRUCTION
    assert "ФОРМАТ" in t and "пуст" in t.lower() and "абзац" in t.lower()


def test_weave_requires_each_item(monkeypatch):
    # Вплетать КАЖДЫЙ пункт блока (еда/тропа/море не роняются из многострочного блока).
    monkeypatch.setenv("MORNING_BRIEF_GATE", "1")
    instr = gp_agent._gate_weave_instruction()
    assert "КАЖДЫЙ" in instr
    assert "ЕДА" in instr and "теряется" in instr  # явная защита самого слабого блока
