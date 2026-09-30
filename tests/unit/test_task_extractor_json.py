"""
task_agent._parse_tasks_json — устойчивый парс JSON задач от LLM.

Ограничение длины ответа может оборвать JSON внутри строки.
Без восстановления усечённого массива теряются и предшествующие полные задачи.
Фикс: лимит поднят + salvage усечённого массива (спасти полные объекты).
"""
from __future__ import annotations

import pytest

import task_agent as ta

pytestmark = pytest.mark.unit


def test_clean_json():
    assert ta._parse_tasks_json('[{"content":"a"},{"content":"b"}]') == \
        [{"content": "a"}, {"content": "b"}]


def test_fenced_json():
    assert ta._parse_tasks_json('```json\n[{"content":"a"}]\n```') == [{"content": "a"}]


def test_truncated_salvage_keeps_complete_objects():
    # оборвано mid-string во втором объекте — первый должен спастись
    raw = '[{"content":"task1","why":"x"},{"content":"task2 недопис'
    assert ta._parse_tasks_json(raw) == [{"content": "task1", "why": "x"}]


def test_truncated_multiple_salvaged():
    raw = ('[{"content":"t1"},{"content":"t2"},{"content":"t3"},'
           '{"content":"t4 обор')
    out = ta._parse_tasks_json(raw)
    assert [t["content"] for t in out] == ["t1", "t2", "t3"]


def test_garbage_raises():
    with pytest.raises(Exception):
        ta._parse_tasks_json("сорри, не могу")
