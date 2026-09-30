"""tests/consistency/test_intent_doc_home_refs.py — сторож doc-домов claim'а.

Правило [[feedback_closing_open_invariant]]: claim подсистемы живёт в нескольких
домах; независимый пересказ в LIVE-доме (BLUEPRINT/BACKLOG) без ссылки на инвариант
реестра = split-brain в прозе — дом стухает МОЛЧА (intent-сторож видит лишь реестр↔
код↔страницу, BLUEPRINT/BACKLOG — нет; кейс §253).

Здесь: если инвариант несёт `bug_ids`, то КАЖДОЕ упоминание bug-id в BLUEPRINT.md /
BACKLOG.md обязано со-упоминать ссылку на инвариант реестра (его id ИЛИ subsystem_intent).
CHANGELOG.md и *_ARCHIVE.md — append-only лог/архив (снимок во времени, НЕ живой claim),
исключены.

Стережёт НАЛИЧИЕ ссылки (читатель дойдёт до авторитетного статуса в реестре), НЕ
отсутствие пересказа — «no restatement» не машинно, держится дисциплиной reference-формы.
Это check (связь), не test (правоту статуса доказывает ночной pytest). Детерминирован.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import intent_registry as ir

pytestmark = pytest.mark.consistency

ROOT = Path(__file__).resolve().parents[2]
LIVE_HOMES = ["BACKLOG.md", "CLAUDE.md"]   # BLUEPRINT.md удалён 2026-08-03; свод — новый живой дом  # НЕ CHANGELOG/*_ARCHIVE (append-only)


def _entries_with_bug_ids() -> list[tuple[str, str, str]]:
    out = []
    for e in ir.load_registry():
        for inv in e.get("invariants", []):
            for bid in (inv.get("bug_ids") or []):
                out.append((e["id"], inv["id"], bid))
    return out


def ref_violations(text: str, bug_id: str, markers: list[str]) -> list[str]:
    """Строки, где упомянут bug_id, но нет ни одного ref-маркера. Чистая функция."""
    bad = []
    for i, line in enumerate(text.splitlines(), 1):
        if bug_id in line and not any(m in line for m in markers):
            bad.append(f"L{i}: {line.strip()[:80]}")
    return bad


_CASES = [
    pytest.param(eid, iid, bid, id=f"{iid}:{bid}")
    for eid, iid, bid in _entries_with_bug_ids()
]


@pytest.mark.parametrize("entry_id,inv_id,bug_id", _CASES)
def test_bug_id_mention_references_registry(entry_id, inv_id, bug_id):
    markers = [inv_id, "subsystem_intent", "реестр замысла"]
    for home in LIVE_HOMES:
        f = ROOT / home
        if not f.exists():
            continue
        viol = ref_violations(f.read_text(encoding="utf-8"), bug_id, markers)
        assert not viol, (
            f"[{entry_id}::{inv_id}] bug-id {bug_id!r} упомянут в {home} без ссылки на "
            f"инвариант реестра ({inv_id} / subsystem_intent) — restated status рискует "
            f"стухнуть молча (split-brain в прозе). Ставь ссылку на реестр рядом. Строки:\n  "
            + "\n  ".join(viol)
        )


# ── Позитивные контроли (RST): сенсор ловит bare-mention, молчит на reference ──
def test_control_flags_bare_mention():
    assert ref_violations("BUG-X сломан, пороги не проверяются.", "BUG-X",
                          ["fires_regardless_of_signals", "subsystem_intent"])


def test_control_clean_on_reference():
    assert ref_violations("BUG-X — статус в реестре fires_regardless_of_signals.",
                          "BUG-X", ["fires_regardless_of_signals", "subsystem_intent"]) == []
