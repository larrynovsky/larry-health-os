#!/usr/bin/env python3.11
"""
fix_palindromic_het.py — ОДНОРАЗОВЫЙ БЭКФИЛЛ (выполнен 2026-06-26).

⚠️  Этот скрипт исторический. Системная логика перенесена в
    backfill_effect_alleles.py v4 (функция compute_for_variant +
    _het_palindromic_alt). Все новые варианты из Phase B и последующих
    WGS-импортов обрабатываются там автоматически.

    Этот файл оставлен как документация алгоритма и для возможного
    ручного запуска при нестандартных сценариях (например, ре-импорт
    данных с принудительным сбросом статусов).

Проблема: resolve_effect_allele() правильно ставит status='palindromic' для SNP
где ref/alt взаимодополняют (A/T, C/G). Но для ГЕТЕРОЗИГОТНЫХ носителей
(genotype IN ('AT','TA','CG','GC')) это ложный пропуск: оба аллеля присутствуют,
carrier detection через `effect_allele in genotype` сработал бы если effect_allele
был проставлен.

Два пути резолюции (в порядке предпочтения):
  1. HGVS из clinical_summary: c.187C>G → alt=G. Явная нотация, надёжнее.
  2. ref_allele из БД: palindromic A/T → alt=complement(ref). Используется
     когда clinical_summary не содержит X>Y паттерн (напр. описательные аннотации
     вроде "FTO intron variant; associated with...").

Безопасность для HET-генотипов: при genotype='CG' или 'TA' оба аллеля присутствуют
в строке. `effect_allele in genotype` = True независимо от strand → ложных
carrier-срабатываний не создаём.

НЕ трогаем гомозиготные палиндромные (GG, CC, TT, AA) — strand неразрешима
без external lookup, status='palindromic' остаётся правильным.

Защита от перезаписи: backfill_effect_alleles.py скипает
effect_allele_status='palindromic_het_resolved' (WHERE добавлен там же).

Идемпотентно: повторный прогон не меняет уже обработанные строки (фильтр по
status='palindromic').
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import health_db as db

# Матч ref>alt в HGVS: c.187C>G, g.12345A>T, c.371+11642G>C, c.-13+590T>A
_HGVS_RE = re.compile(r'[ACGT]>([ACGT])', re.IGNORECASE)

_COMP = {'A': 'T', 'T': 'A', 'C': 'G', 'G': 'C'}

HET_GENOTYPES = frozenset(('AT', 'TA', 'CG', 'GC'))


def _parse_alt(clinical_summary: str) -> str | None:
    """Возвращает alt-аллель из первого HGVS ref>alt вхождения. None если не нашли."""
    m = _HGVS_RE.search(clinical_summary)
    return m.group(1).upper() if m else None


def _alt_from_ref(ref_allele: str | None, genotype: str) -> str | None:
    """Fallback: для palindromic het alt = complement(ref).

    Для A/T SNP: ref=T → alt=A. ref=A → alt=T.
    Для C/G SNP: ref=C → alt=G. ref=G → alt=C.
    Работает только если complement(ref) присутствует в генотипе (sanity check).
    """
    if not ref_allele:
        return None
    comp = _COMP.get(ref_allele.upper())
    if comp and comp in genotype.upper():
        return comp
    return None


def run(*, _conn=None) -> dict:
    """Основная функция. _conn инъектируется в тестах."""
    use_db = _conn is None
    if use_db and not db._is_primary():
        raise RuntimeError("fix_palindromic_het: только на primary (Studio)")

    def _get_conn():
        return db.get_conn() if use_db else _FakeCtx(_conn)

    with _get_conn() as conn:
        rows = conn.execute("""
            SELECT id, rsid, genotype, clinical_summary, ref_allele
            FROM genetic_variants
            WHERE effect_allele_status = 'palindromic'
              AND genotype IN ('AT', 'TA', 'CG', 'GC')
              AND clinical_summary IS NOT NULL
        """).fetchall()

    updates = []
    skipped: list[str] = []

    for row in rows:
        id_, rsid, genotype, summary, ref_allele = row
        gt = genotype.upper()

        # Путь 1: HGVS из clinical_summary
        alt = _parse_alt(summary)
        if alt is not None and alt not in gt:
            comp = _COMP.get(alt)
            alt = comp if (comp and comp in gt) else None

        # Путь 2: complement(ref_allele) если HGVS не дал результата
        if alt is None:
            alt = _alt_from_ref(ref_allele, gt)

        if alt is None:
            skipped.append(rsid)
            continue

        updates.append((alt, 'palindromic_het_resolved', id_))

    if updates:
        with _get_conn() as conn:
            conn.executemany(
                "UPDATE genetic_variants "
                "SET effect_allele=?, effect_allele_status=? WHERE id=?",
                updates,
            )

    return {
        'updated': len(updates),
        'skipped': skipped,
        'rows': [{'rsid': u[2], 'effect_allele': u[0]} for u in updates],
    }


class _FakeCtx:
    """Контекст-менеджер вокруг голого sqlite3.Connection для тестов."""
    def __init__(self, conn): self._c = conn
    def __enter__(self): return self._c
    def __exit__(self, *_): pass


if __name__ == "__main__":
    r = run()
    print(f"Обновлено: {r['updated']}")
    if r['skipped']:
        print(f"Пропущено (нет HGVS и ref_allele): {r['skipped']}")
