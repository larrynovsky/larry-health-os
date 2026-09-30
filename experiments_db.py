"""experiments_db.py — РЕТАЙР (BL-EXP-1, 2026-07-10).

Конвейер «эксперименты» (N-of-1, бра́лись «из головы») свёрнут: методика ушла
к data-born гипотезам консилиума; сходимость — через версионирование самой
гипотезы (resolve_hypothesis: version++/history), не через эксперименты.
Подробнее — docs/explanation/hypothesis_engine.md.

Функции оставлены как **no-op с прежними сигнатурами**: health_db ре-экспортирует
их вниз (≈54 импортёра), и check_contracts (§5) не ломается, а таблицы
`experiments`/`experiment_log` сняты — ни одна из этих функций к ним больше
не обращается. Потребители (`get_active_experiments()` → `[]`) сами отдают
пусто. Финальная зачистка (удалить функции + ре-экспорт + обновить контракт) —
отдельным шагом, когда убедимся, что пусто-путь всех потребителей стабилен.
"""
from __future__ import annotations

from _time_inject import get_today  # noqa: F401


def get_active_experiments() -> list[dict]:
    return []


def get_experiment_stats(exp_id: int) -> dict:
    return {}


def complete_experiment(exp_id: int, result: str, notes: str = None):
    return None


def log_experiment_day(exp_id: int, adhered: bool, notes: str = None, day: str = None):
    return None


def start_experiment(name: str, hypothesis: str, intervention: str, start_date: str = None) -> int:
    return 0


def update_experiment_check_results(exp_id: int, check_results_json: str):
    return None


# health_db — В КОНЦЕ модуля (BL-TEST-COLLECT-ALONE-1, 2026-09-24): он ре-экспортирует функции
# этого модуля, и импорт наверху давал цикл, если модуль импортировали первым (28 из 29 доменных
# модулей). Имя _hdb нужно только внутри функций — к их вызову health_db уже загружен.
import health_db as _hdb  # noqa: E402,F401 — сохранён ради стабильности arch-графа
