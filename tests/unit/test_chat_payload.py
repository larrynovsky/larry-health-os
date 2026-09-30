"""tests/unit/test_chat_payload.py — M1 F-testability.

Собранный вход модели инспектируем ОДНОЙ функцией (build_chat_payload), и chat()
идёт тем же путём. Инвариант против «тест на срезе/копии» — тест бьёт в точку
потребления целиком (system + все ходы), не в отдельный слой.
"""
from __future__ import annotations
import pytest

pytestmark = pytest.mark.unit


def test_payload_single_inspectable_assembler(db):
    import hai_chat
    sp, msgs = hai_chat.build_chat_payload("как я спал сегодня?", include_data=False)
    assert isinstance(sp, str) and sp, "system_prompt — непустая строка"
    assert isinstance(msgs, list) and msgs, "messages — непустой список"
    assert msgs[-1]["role"] == "user"
    assert msgs[-1]["content"] == "как я спал сегодня?", "include_data=False → сырое сообщение"


def test_assembled_context_text_covers_whole_input(db):
    """Инспектируемый артефакт содержит ВЕСЬ вход (system + user-ход), не срез."""
    import hai_chat
    txt = hai_chat.assembled_context_text("уникальный_маркер_XYZ", include_data=False)
    assert "[SYSTEM]" in txt
    assert "[USER]" in txt
    assert "уникальный_маркер_XYZ" in txt


def test_chat_uses_build_chat_payload_no_parallel_copy():
    """Инвариант: chat() зовёт build_chat_payload, а не собирает инлайн (иначе тест на копии)."""
    import inspect, hai_chat
    src = inspect.getsource(hai_chat.chat)
    assert "build_chat_payload(" in src, "chat() должен звать общий ассемблер"
    # старой инлайн-сборки в chat() быть не должно
    assert "get_history(" not in src, "chat() не собирает историю инлайн — только через payload"
