#!/usr/bin/env python3.11
"""
effect_allele.py — strand-aware резолюция effect_allele для генома.

Единственная публичная функция:
    resolve_effect_allele(genotype, ref, alts) -> (allele | None, status)

Зачем: 23andMe-генотип и ClinVar/VCF ref/alt часто записаны на разных strand.
Прямое посимвольное сравнение (как в genome_context._zygosity) при таком
рассогласовании даёт ложный wildtype (носитель исчезает) или ложного носителя.
Эта функция приводит alt к strand генотипа, сверяя ПАРУ генотипа с парой
{ref, alt}. При любой неоднозначности возвращает (None, <status>) —
НИКОГДА не угадывает.

Контракт:
  - resolved          — однозначно: вернули effect_allele на strand генотипа;
  - palindromic       — ref/alt комплементарны (A/T или C/G): strand из одного
                        генотипа неразрешим → None (НЕ угадываем по частоте);
  - multiallelic_ambiguous — >1 alt: строгая консервативность → None;
  - genotype_mismatch — генотип не из пары {ref,alt} ни на одном strand → None;
  - no_call           — генотип не два валидных нуклеотида (инделы II/DD, '--');
  - no_data           — нет ref или нет валидного alt.

Инвариант (strand-симметрия): resolve(complement(gt), ref, alt) даёт тот же
status, а для resolved — комплементарный аллель. Покрыто property-тестом.
"""
# INTENT: genome_effect_allele — геном: strand-безопасная резолюция аллеля (отказ безопасно).
#          Замысел и инварианты — subsystem_intent.yaml, раздел genome_effect_allele.
from __future__ import annotations

_COMP = {"A": "T", "T": "A", "C": "G", "G": "C"}
_BASES = frozenset("ACGT")


def _comp(base: str) -> str | None:
    return _COMP.get(base)


def _norm_genotype(genotype: str | None) -> str | None:
    """Верхний регистр, без разделителей. None если не ровно 2 нуклеотида ACGT."""
    if not genotype:
        return None
    gt = (genotype.upper()
          .replace("/", "").replace("|", "").replace(" ", ""))
    if len(gt) != 2 or any(c not in _BASES for c in gt):
        return None
    return gt


def resolve_effect_allele(genotype, ref, alts) -> tuple[str | None, str]:
    """Резолвит effect_allele на strand генотипа. См. docstring модуля.

    genotype : str   — пара нуклеотидов из raw_snps (напр. 'AG', 'CC', '--').
    ref      : str   — референсный аллель из dbSNP/VCF (hg19), один нуклеотид.
    alts     : list  — alt-аллели из источника (множество; >1 → ambiguous).
    """
    gt = _norm_genotype(genotype)
    if gt is None:
        return None, "no_call"

    r = (ref or "").upper()
    if len(r) != 1 or r not in _BASES:
        return None, "no_data"

    if isinstance(alts, str):
        alts = [alts]
    clean = sorted({
        a.upper() for a in (alts or [])
        if a and len(str(a)) == 1 and str(a).upper() in _BASES and str(a).upper() != r
    })
    if not clean:
        return None, "no_data"
    if len(clean) > 1:
        return None, "multiallelic_ambiguous"
    alt = clean[0]

    # Палиндром: ref/alt комплементарны → strand из одного генотипа неразрешим.
    if _comp(r) == alt:
        return None, "palindromic"

    pair = {r, alt}
    gset = set(gt)

    # Тот же strand, что и источник.
    if gset <= pair:
        return alt, "resolved"

    # Обратный strand: переводим alt в алфавит генотипа.
    if {_comp(c) for c in gset} <= pair:
        return _comp(alt), "resolved"

    # Генотип не принадлежит этому SNP ни на одном strand.
    return None, "genotype_mismatch"
