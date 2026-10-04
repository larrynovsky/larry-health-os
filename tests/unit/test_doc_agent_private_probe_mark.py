"""delta-facts на публичной странице замысла помечает закрытый путь пробы (урок C-143, 04.10).

До правки генератор писал путь plans/… дословно, и публичная страница краснела только на
полном прогоне закрытия (test_public_doc_refs) — уже после ручной правки страницы."""
from __future__ import annotations

import pytest

import doc_agent

pytestmark = pytest.mark.unit

PROBE = "plans/probe_task_dedup_2026-10-02.py"   # настоящий файл в закрытой зоне репозитория


def _entry():
    return {"invariants": [{"id": "x", "probe": PROBE}]}


def test_private_probe_in_delta_gets_the_mark():
    import pii_census as pc
    if pc.is_public_export():
        pytest.skip("в открытой выгрузке закрытой зоны нет")
    mark = doc_agent._private_probe_mark(_entry(), [f"x: носитель → ['{PROBE}']"])
    assert "закрытой части" in mark


def test_no_probe_in_delta_no_mark():
    assert doc_agent._private_probe_mark(_entry(), ["+ инвариант x (status=holds)"]) == ""
