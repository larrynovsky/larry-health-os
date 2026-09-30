#!/usr/bin/env python3
"""
hypothesis_lab_linker — связывает импортированные лабные данные с гипотезами в testing.

Единственная задача: при импорте лабного PDF создать эксперимент в experiments
и обновить поле linked_experiment_id в payload гипотезы.

Вызывается из import_all.py синхронно, после успешного импорта.
Зависимости: health_db.
"""

import json as _json
import logging
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent))
import health_db as db

log = logging.getLogger(__name__)


def link_labs_to_hypotheses(lab_source: str, lab_date: str) -> list[tuple[int, int]]:
    """
    Создаёт эксперимент для каждой гипотезы в status='testing' без experiment.
    Обновляет linked_experiment_id в payload гипотезы.

    Args:
        lab_source: имя PDF-файла (например '10000000002.PDF')
        lab_date:   дата анализов 'YYYY-MM-DD'

    Returns:
        Список (memory_id, experiment_id) для созданных связей.
    """
    # BL-EXP-1 (2026-07-10): конвейер экспериментов ретайрится. Создание
    # экспериментов из лаб-импорта отключено. Функция сохранена как no-op,
    # чтобы не менять контракт вызова в import_all.py. Оценка гипотез после
    # прихода анализов теперь идёт через consilium (кнопка «eval» → resolve).
    _ = (lab_source, lab_date, db, _json, log)
    return []
