#!/usr/bin/env python3.11
"""
lab_fuzzy.py — fuzzy-matching для канонических имён лабораторных тестов.

Один модуль, одна функция: top_candidates()

top_candidates(raw_name) возвращает топ-N canonical имён из lab_name_aliases
отсортированных по убыванию схожести. Используется для:
  - Telegram-алертов о неизвестных маркерах (jobs/scheduled.py)
  - hae_checker при проверке новых Apple Health метрик
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

try:
    import health_db as db
    _DB_AVAILABLE = True
except Exception:
    _DB_AVAILABLE = False


def top_candidates(raw_name: str, n: int = 3,
                   min_score: float = 0.45) -> list[tuple[str, float]]:
    """
    Возвращает топ-N canonical имён отсортированных по убыванию схожести.

    Два прохода:
    1. Substring: canonical содержится в raw_name или наоборот → score 0.90
    2. Биграммная схожесть с порогом min_score (default 0.45)

    Пустой список → нет хороших кандидатов, пользователь вводит вручную.
    """
    if not _DB_AVAILABLE:
        return []
    try:
        candidates = db.get_all_canonical_names()
    except Exception:
        return []
    if not candidates:
        return []

    raw_lo = raw_name.lower()
    scored: dict[str, float] = {}

    for c in candidates:
        c_lo = c.lower()
        if c_lo in raw_lo or raw_lo in c_lo:
            scored[c] = max(scored.get(c, 0), 0.90)
        s = _bigram_similarity(raw_lo, c_lo)
        if s >= min_score:
            scored[c] = max(scored.get(c, 0), s)

    result = sorted(scored.items(), key=lambda x: x[1], reverse=True)
    return result[:n]


# ── Приватные утилиты ─────────────────────────────────────────────────────────

def _bigram_similarity(a: str, b: str) -> float:
    """Коэффициент Сёренсена по биграммам."""
    def bigrams(s: str) -> set[str]:
        return {s[i:i+2] for i in range(len(s) - 1)}

    ba, bb = bigrams(a), bigrams(b)
    if not ba and not bb:
        return 1.0
    if not ba or not bb:
        return 0.0
    return 2 * len(ba & bb) / (len(ba) + len(bb))


def _best_match(raw_name: str, threshold: float = 0.85) -> str | None:
    """Возвращает лучший canonical если score >= threshold, иначе None."""
    candidates = top_candidates(raw_name, n=1, min_score=threshold)
    return candidates[0][0] if candidates else None
