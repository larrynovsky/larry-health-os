"""owner_chat_id — единый резолвер (anti-split-brain) + F2 fail-closed.

Контекст (2026-07-17, нить owner-chatid-lazy): резолв OWNER_CHAT_ID стал
ленивым (bot.filters.owner_chat_id). Два инварианта под сторожем:

  #3 anti-split-brain: единственный держатель значения owner chat_id —
     bot.filters.owner_chat_id(). Ни один прод-модуль не заводит второй путь
     (локальную константу OWNER_CHAT_ID = ...) — дубль = split-brain
     маршрутизации (карты владельца ушли бы не туда).

  #4 F2 fail-CLOSED: inline owner-check в assessment_bot_handlers.cb_router
     при недоступном резолве БЛОКИРУЕТ всех. Прежний код
     `if OWNER_CHAT_ID is not None and id != OWNER_CHAT_ID` при None
     короткозамыкался в fail-OPEN (пускал не-владельца).
"""
import asyncio
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.consistency

_REPO = Path(__file__).resolve().parents[2]


def test_no_second_owner_chat_id_constant():
    """#3: единственный резолвер значения — bot.filters.owner_chat_id().
    Ни один прод-модуль не держит локальную константу OWNER_CHAT_ID = ..."""
    offenders = []
    for py in _REPO.rglob("*.py"):
        rel = str(py.relative_to(_REPO))
        if ("__pycache__" in rel or rel.startswith("tests/")
                or rel == "bot/filters.py"):
            continue
        src = py.read_text(encoding="utf-8", errors="ignore")
        if re.search(r"^\s*OWNER_CHAT_ID\s*[:=]", src, re.M):
            offenders.append(rel)
    assert not offenders, (
        f"Второй путь к owner chat_id (split-brain): {offenders}. "
        "Значение резолвит только bot.filters.owner_chat_id().")


# ── #4 F2: assessment cb_router fail-closed ─────────────────────────────────

class _FakeQuery:
    def __init__(self):
        self.data = "cb_aa:test"
        self.message = None
        self.answered = False

    async def answer(self):
        self.answered = True


def _callback_update(chat_id: int):
    q = _FakeQuery()
    upd = SimpleNamespace(
        effective_chat=SimpleNamespace(id=chat_id),
        callback_query=q,
    )
    return upd, q


def test_assessment_router_blocks_stranger(monkeypatch):
    """Owner резолвится → чужой chat_id заблокирован до ACK."""
    import bot.filters as bf
    import assessment_bot_handlers as abh
    monkeypatch.setattr(bf, "owner_chat_id", lambda: 12345)
    upd, q = _callback_update(99999)  # чужой
    asyncio.run(abh.cb_router(upd, None))
    assert q.answered is False, "чужой прошёл owner-check — fail-open"


def test_assessment_router_fail_closed_when_resolve_unavailable(monkeypatch):
    """F2: резолв недоступен (raise) → БЛОКИРУЕМ всех (fail-CLOSED),
    а не пускаем (прежний баг fail-OPEN при None)."""
    import bot.filters as bf
    import assessment_bot_handlers as abh

    def _boom():
        raise RuntimeError("секрет недоступен")

    monkeypatch.setattr(bf, "owner_chat_id", _boom)
    upd, q = _callback_update(99999)
    asyncio.run(abh.cb_router(upd, None))
    assert q.answered is False, "при недоступном резолве пустили chat — fail-open (F2)"
