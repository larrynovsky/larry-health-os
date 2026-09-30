"""INV-DOC-GIT: свод не содержит маркеров устаревших git-моделей.

Класс бага (обзор 2026-07-02): разделы одного живого документа эволюционируют
с разной скоростью — шапка описывала модель 2026-05-23, диаграмма §2 модель
2026-05-09, раздел бэкапов третью. Читатель (в т.ч. AI-сессия, обязанная
читать свод перед изменением) получает split-brain правила.

Паттерны собраны конкатенацией, чтобы тест не триггерил сам себя.
Скоуп — только CLAUDE.md: в git_architecture.md исторические модели
описаны легитимно (explanation), в CHANGELOG — тем более.
"""
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.owner_data]   # скоуп — CLAUDE.md, закрытая часть (28.09)

# Скоуп переехал с BLUEPRINT.md на свод 2026-08-03: BLUEPRINT удалён, а живая
# git-модель и надгробия прежних моделей живут в CLAUDE.md § C.
SCOPE = Path(__file__).parents[2] / "CLAUDE.md"

# Маркеры моделей, вытесненных 2026-05-23 (git-клон на MacBook) и
# 2026-06-18 (e017929, Studio deploy-only). Конкатенация — не литералы.
STALE_MARKERS = [
    ("git живёт " + "ТОЛЬКО на Studio"),          # модель 2026-05-09 в диаграмме
    ("больше не имеет " + "`.git/`"),              # тот же слой в разделе бэкапов
    ("ssh-trigger " + "→ Studio git commit"),      # старый backup-flow
    ("Ssh-edit на Studio** — " + "допустим"),      # до e017929
    ("12" + " 700"),                          # ручная метрика строк (nbsp)
    ("12 " + "700"),                               # то же с обычным пробелом
]


def test_scope_has_no_stale_git_model_markers():
    text = SCOPE.read_text(encoding="utf-8")
    found = [m for m in STALE_MARKERS if m in text]
    assert not found, (
        f"CLAUDE.md содержит маркеры устаревшей git-модели: {found}. "
        "Канон топологии — docs/explanation/git_architecture.md; "
        "в своде остаётся одна (текущая) модель + надгробия со ссылкой.")


def test_scope_points_to_git_canon():
    """Дедуп правила: свод обязан ссылаться на канон git-топологии, а не пересказывать его."""
    text = SCOPE.read_text(encoding="utf-8")
    assert "docs/explanation/git_architecture.md" in text
