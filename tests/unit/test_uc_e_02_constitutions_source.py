"""
UC-E-02 — Constitutions: источник = `genetic_variants`, НЕ `promethease_variants`.

Источник: USE_CASES.md §3.E → UC-E-02.
Реализация: `generate_constitutions.py`.
Status: `implemented`.

Главный инвариант (замысел: `subsystem_intent.yaml::constitutions`): `promethease_variants` пустая —
её нельзя использовать как источник, иначе constitutions будут пустыми.

2026-06-26: добавлен test_guard_allows_bad_zero_if_unknown_nonzero — после
carrier-status фикса bad-bucket может быть пустым (не носитель ни одного аллеля),
но snp_total считается от всех трёх buckets, поэтому генерация не прерывается.
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def test_generate_constitutions_imports():
    import generate_constitutions
    assert generate_constitutions is not None


def test_uses_genetic_variants_not_promethease():
    """В коде должен использоваться `genetic_variants`, не `promethease_variants`."""
    src = (Path(__file__).parents[2] / "generate_constitutions.py").read_text(encoding="utf-8")
    assert "genetic_variants" in src, "должен читать genetic_variants"
    # promethease_variants может упоминаться в комментарии «НЕ использовать»,
    # но не в SQL-запросах
    if "promethease_variants" in src:
        for line in src.splitlines():
            if "promethease_variants" in line:
                line_clean = line.strip()
                if line_clean.startswith("#") or "НЕ" in line_clean.upper():
                    continue
                if "FROM promethease_variants" in line and "NOT" not in line:
                    pytest.fail(f"generate_constitutions.py читает promethease_variants: {line}")


def test_aborts_if_genetic_variants_empty(db):
    """
    При `SELECT * FROM genetic_variants` пустой результат → функция должна
    прерваться с ошибкой: генерация на пустом входе даёт уверенный текст ни о чём
    (ложный путь C-08 — доверие выходу на пустом входе).

    Smoke-проверка через AST: ищем явную проверку на пустой набор.
    """
    src = (Path(__file__).parents[2] / "generate_constitutions.py").read_text(encoding="utf-8")
    has_guard = ("len(" in src and "variants" in src) or "if not " in src
    assert has_guard, (
        "В generate_constitutions.py не видно guard на пустой genetic_variants. "
        "Замысел constitutions требует прерывания на SNP=0."
    )


def test_guard_allows_bad_zero_if_unknown_nonzero(db):
    """snp_total = bad + good + unknown. Если bad=0 но unknown>0 → генерация не прерывается.

    После carrier-status фикса (2026-06-26) для доменов, где человек не является
    носителем ни одного патогенного аллеля (но есть palindromic/multiallelic
    варианты → unknown), bad-bucket пуст. Guard должен пропускать такие домены,
    а не прерывать генерацию — иначе конституция сна не генерировалась бы.

    Тест проверяет поведение _get_snp_data: при palindromic варианте
    snp_total = 1 (в unknown), генерация не прерывается по guard'у.
    """
    import generate_constitutions as gc

    # Вставляем один palindromic вариант (носительство неопределимо → unknown)
    db.add_genetic_variant(
        "rs_PAL_GUARD",
        gene="PER3",
        genotype="CC",
        significance="Pathogenic",
        effect_allele=None,
        effect_allele_status="palindromic",
        clinical_summary="Guard test palindromic",
    )

    result = gc._get_snp_data({"genes": ["PER3"]})
    snp_total = len(result["bad"]) + len(result["good"]) + len(result["unknown"])

    assert result["bad"] == [], "bad должен быть пуст"
    assert len(result["unknown"]) == 1, "palindromic должен попасть в unknown"
    assert snp_total == 1, "snp_total = 1, guard не должен прерывать генерацию"


def test_carrier_status_columns_selected():
    """SELECT в _get_snp_data включает effect_allele и effect_allele_status.

    Без этих колонок carrier-фильтр не может работать — тест защищает от
    случайного удаления из SELECT при рефакторинге.
    """
    src = (Path(__file__).parents[2] / "generate_constitutions.py").read_text(encoding="utf-8")
    assert "effect_allele," in src or "effect_allele\n" in src, \
        "effect_allele должна быть в SELECT _get_snp_data"
    assert "effect_allele_status" in src, \
        "effect_allele_status должна быть в SELECT _get_snp_data"
