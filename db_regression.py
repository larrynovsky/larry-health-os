#!/usr/bin/env python3.11
"""
db_regression.py — датчик регрессии числа строк.

Ловит тихое стирание данных: проверка «таблица пуста» видит только полный ноль.
Здесь сравниваем канон с ПИКОМ по последним бэкапам — если таблица
упала >50% против недавнего максимума, это warn → triage → Telegram.

Сравнение с пиком (а не «вчера»), чтобы поймать потерю, даже если она уже
просочилась в свежий бэкап (медианный случай: стёрли в активной сессии).

Один публичный entry point: check(). Чистый find_regressions вынесен для тестов.
"""
from __future__ import annotations

import glob
import logging
import infra_config  # основная машина — данные установки (private/infra.yaml)
import re
import sqlite3
from pathlib import Path

log = logging.getLogger(__name__)
BACKUP_DIR = Path.home() / "health/backups"
DROP_THRESHOLD = 0.5    # падение >50% против пика
MIN_ROWS = 3            # игнор крошечных таблиц (шум)
LOOKBACK_BACKUPS = 3    # пик по последним N daily-бэкапам

# Таблицы с ЛЕГИТИМНОЙ убылью (стейджинг/очереди/дедуп) — не алармим.
EXCLUDE = {
    "lab_results_staging", "vcf_staging", "vcf_discovery",
    "experiments", "experiment_log",   # эксперименты свёрнуты (BL-EXP-1)
    "pgs_catalog", "pgs_weights",       # вынесены в reference-БД 2026-07-02 (не потеря)
    # Регенерируемые/производные таблицы законно уменьшаются при пересборке.
    # Источники (memory, medications, lab_results, documents…) остаются под мониторингом.
    "tasks",                 # регенерируются вместе с гипотезами
    "hypotheses_cbcr",       # производные CBCR-скоры к memory-гипотезам
    "hypothesis_outcomes",   # производный трекинг исходов
}

_DATED = re.compile(r"health_\d{4}-\d{2}-\d{2}\.db$")


def _excluded(t: str) -> bool:
    return (t in EXCLUDE or "_fts" in t or t.startswith("sqlite_")
            or t.endswith("_staging"))


def _counts(path) -> dict:
    """{table: row_count} для невыключенных таблиц. Read-only."""
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    out = {}
    try:
        for name, typ in c.execute("SELECT name, type FROM sqlite_master").fetchall():
            if typ != "table" or _excluded(name):
                continue
            try:
                out[name] = c.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
            except sqlite3.Error:
                pass
    finally:
        c.close()
    return out


def find_regressions(current: dict, backup_peak: dict,
                     threshold: float = DROP_THRESHOLD,
                     min_rows: int = MIN_ROWS) -> list[str]:
    """Чистая функция: current{t:n} vs backup_peak{t:n} → строки про упавшие.

    Флагуем таблицу, если её пик по бэкапам >= min_rows и канон упал ниже
    peak*(1-threshold). Новые таблицы (нет в бэкапах) не флагуются.
    """
    out = []
    for t, peak in sorted(backup_peak.items()):
        if peak < min_rows:
            continue
        cur = current.get(t, 0)
        if cur < peak * (1 - threshold):
            pct = round(100 * (peak - cur) / peak)
            out.append(f"{t}: {peak}→{cur} (−{pct}%)")
    return out


def _recent_backups(n: int = LOOKBACK_BACKUPS) -> list[str]:
    files = [f for f in glob.glob(str(BACKUP_DIR / "health_*.db"))
             if _DATED.search(f) and Path(f).stat().st_size > 0]
    return sorted(files)[-n:]


def check() -> list[str]:
    """Studio-only. Список таблиц, упавших >50% против пика последних бэкапов."""
    import socket
    if not infra_config.is_primary():
        return []
    import health_db as db
    backups = _recent_backups()
    if not backups:
        return []
    current = _counts(db.DB_PATH)
    peak: dict = {}
    for b in backups:
        for t, n in _counts(b).items():
            peak[t] = max(peak.get(t, 0), n)
    drops = find_regressions(current, peak)
    if not drops:
        return drops
    explained = explained_by_snapshot(current, peak, _preop_snapshots(backups[0]))
    for t, snap in explained.items():
        log.info("убыль %s объяснена: данные сохранены в pre-op снимке %s", t, snap)
    return [d for d in drops if d.split(":", 1)[0] not in explained]


# ── Сознательная операция с pre-op снимком — не «тихое стирание» (26.09) ──────
# Очистка дублей с pre-op снимком может законно уменьшить таблицу. Повторять запрос
# решения без учёта такого снимка — лишний шум. Признак сохранности — ДАННЫЕ, не
# пометка: снимок, снятый ПОСЛЕ пикового бэкапа и ещё содержащий пиковое число строк, доказывает,
# что строки сохранены до убыли и восстановимы. Граница честно: «сохранено» ≠ «убыль была
# задумана» — ошибочное удаление после чужого снимка тоже замолчит; восстановимость при этом есть.

def _preop_snapshots(oldest_backup: str) -> list[str]:
    """Недатированные sqlite-снимки в BACKUP_DIR новее самого старого бэкапа окна."""
    since = Path(oldest_backup).stat().st_mtime
    out = []
    for f in BACKUP_DIR.iterdir():
        if (f.is_file() and ".db" in f.name and not f.name.endswith(("-shm", "-wal"))
                and not _DATED.search(f.name) and f.stat().st_size > 0
                and f.stat().st_mtime > since):
            out.append(str(f))
    return sorted(out)


def explained_by_snapshot(current: dict, peak: dict, snapshots: list[str],
                          threshold: float = DROP_THRESHOLD) -> dict:
    """{таблица: снимок} для упавших таблиц, чьи строки целиком сохранены в свежем снимке."""
    out = {}
    for t, p in peak.items():
        if current.get(t, 0) >= p * (1 - threshold):
            continue
        for s in snapshots:
            try:
                n = _counts(s).get(t, 0)
            except sqlite3.Error:
                continue
            if n >= p:
                out[t] = Path(s).name
                break
    return out


if __name__ == "__main__":
    drops = check()
    print("\n".join(drops) if drops else "регрессий нет")
