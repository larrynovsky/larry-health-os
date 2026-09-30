"""Фильтр деривативов не имеет права стать мёртвым списком имён (2026-09-07).

`git_facts.MACHINE_REGENERATED_FILES` — то, что датчик незакоммиченной работы
ВЫЧИТАЕТ из diff'а снимка. Ошибка в обе стороны тихая и разная:
  · имя лишнее (писатель исчез) → настоящая работа маскируется, датчик молчит;
  · имя пропущено (появился новый дериватив) → датчик кричит каждый день, и его
    перестают читать (§13).

Форма сторожа взята у `test_warn_classes_match_live_labels`: каждое имя обязано
встречаться в исходнике того, кто его РЕАЛЬНО перевыпускает.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.consistency
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import git_facts as gf  # noqa: E402

# Кто объявлен писателем каждой группы — здесь же и проверяется.
WRITERS = {
    gf.STAGED_BY_DOC_AGENT: "doc_agent.py",
    gf.STAGED_BY_SECURITY: "doc_agent.py",
    gf.REGENERATED_BY_ARCH_GUARD: "arch_guard.py",
}


@pytest.mark.parametrize("group,writer", list(WRITERS.items()))
def test_every_name_has_a_live_writer(group, writer):
    """Имя без живого писателя — мёртвый фильтр, который молча маскирует работу."""
    src = (ROOT / writer).read_text(encoding="utf-8")
    for name in group:
        assert name in src, (
            f"{name} объявлен деривативом, но {writer} его не упоминает — "
            f"писатель исчез, а фильтр остался и теперь прячет настоящую работу")


def test_doc_agent_stages_exactly_from_the_constant():
    """Мутация «дописать имя литералом в git add мимо константы» обязана краснеть:
    иначе список в git_facts и то, что реально стейджится, разъедутся молча."""
    src = (ROOT / "doc_agent.py").read_text(encoding="utf-8")
    adds = [ln.strip() for ln in src.splitlines()
            if '"git", "add"' in ln or "'git', 'add'" in ln]
    assert adds, "в doc_agent не нашлось ни одного git add — тест смотрит не туда"
    for ln in adds:
        assert "STAGED_BY_" in ln, (
            f"git add с литеральными именами мимо единственного дома: {ln}")


def test_groups_do_not_overlap_and_cover_the_union():
    """Склейка — это и есть весь список; лишнего в MACHINE_REGENERATED_FILES нет."""
    union = gf.STAGED_BY_DOC_AGENT + gf.STAGED_BY_SECURITY + gf.REGENERATED_BY_ARCH_GUARD
    assert set(union) == set(gf.MACHINE_REGENERATED_FILES)
    assert len(union) == len(set(union)), "имя дважды — склейка собрана неверно"


def test_filter_does_not_swallow_hand_written_norms():
    """Позитивный контроль на переусердствование: файлы, которые пишет ЧЕЛОВЕК,
    в фильтр попасть не должны никогда. CLAUDE.md здесь не случаен — 04.08 он лежал
    незакоммиченным, и это ровно то попадание, ради которого датчик существует."""
    for hand_written in ("CLAUDE.md", "subsystem_intent.yaml", "BACKLOG.md", "ROADMAP.md"):
        assert hand_written not in gf.MACHINE_REGENERATED_FILES
