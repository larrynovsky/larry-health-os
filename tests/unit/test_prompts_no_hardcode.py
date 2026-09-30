"""Guardrail (hardcode-migration Ф5): промпты специалистов не содержат
данных пациента и клинико-числовых литералов.

Сканирует ИСХОДНИКИ specialists/*.md, не рантайм-вывод: в рендере данные
пациента из БД легитимны, а в файле промпта — нет (О7 плана).
"""
from __future__ import annotations

import re
from pathlib import Path
import sys

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT))

import pytest

pytestmark = pytest.mark.unit

SPEC = ROOT / "specialists"

# Литералы пациента (имя, диагноз, генотип, терапия) — недопустимы в промптах.
# Фамилия — из словаря pii_census (приватная зона), не литералом в публичном тесте.
import pii_census as _pii
# Литералы пациента — из приватного словаря (класс owner_clinical): до 2026-09-26 часть их
# (диагноз, генотипы) стояла здесь в публичном файле — стоп-лист сам был досье.
PATIENT_LITERALS = _pii.literals(["surname", "owner_clinical"])

# Клинико-числовые пороги/нормы/диапазоны в прозе — случай (b), недопустимы.
# Прицельно на threshold-стиль, чтобы не ловить format-примеры (время «13:00»).
NUMERIC_PATTERNS = [
    r"HRV\s*[<>]",                          # HRV<N, HRV<N%
    r"ratio\s*[<>]\s*\d",                   # ratio>N
    r"readiness\s*[<>]\s*\d",               # readiness<N
    r"avg30",                                # HRV<N% avg30
    r"мс/год",                               # -N мс/год
    r"\d+[.,]?\d*\s*[–-]\s*\d+[.,]?\d*\s*мс",  # диапазон ВСР «N–M мс»
    r"p10\s*[–-]\s*p90",                    # хардкоженный перцентильный диапазон
]


def _md_files():
    return sorted(SPEC.glob("*.md"))


def test_specialists_dir_present():
    assert SPEC.is_dir() and _md_files(), "нет specialists/*.md в репозитории"


@pytest.mark.parametrize("f", _md_files(), ids=lambda p: p.name)
def test_no_patient_literals(f):
    text = f.read_text(encoding="utf-8")
    found = [w for w in PATIENT_LITERALS if w in text]
    assert not found, f"{f.name}: литералы пациента в промпте: {found}"


@pytest.mark.parametrize("f", _md_files(), ids=lambda p: p.name)
def test_no_clinical_numeric(f):
    text = f.read_text(encoding="utf-8")
    hits = []
    for pat in NUMERIC_PATTERNS:
        for m in re.finditer(pat, text):
            hits.append(m.group(0))
    assert not hits, f"{f.name}: клинико-числовые литералы в промпте: {hits}"


def test_lifestyle_prompts_have_placeholder():
    for name in ("lifestyle_sleep", "lifestyle_energy", "lifestyle_movement", "lifestyle_stress"):
        text = (SPEC / f"{name}.md").read_text(encoding="utf-8")
        assert "%%PATIENT_PROFILE%%" in text, f"{name}: нет %%PATIENT_PROFILE%% плейсхолдера"
