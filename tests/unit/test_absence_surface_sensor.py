"""Датчик «поверхность отсутствия под сторожем или названа» — гаситель вердикта.

Вердикт аудита («у этого тракта поверхности нет») живёт вечно, пока его не гасит
механизм: §18. Датчик и есть гаситель — он пересчитывает периметр на каждом прогоне.
Здесь доказывается, что он КРАСНЕЕТ на новом тракте, а не просто существует.

Приманки живут в tmp, а не в репозитории: держать в проде файл, единственное
назначение которого — обмануть датчик, значило бы завести ту самую ложь, от которой
он стоит.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit

TRACT_WITH_SURFACE = '''
import anthropic
import labs_db

PROMPT = """Ты врач. Посмотри данные пациента и скажи, какие анализы стоит сдать.
Если чего-то нет в показанных данных — так и напиши, что анализ не сдавался.
Ответ на русском, без списков, максимум пять предложений, тон разговорный."""
'''

TRACT_GUARDED = '''
import anthropic
import labs_db
import gp_context

PROMPT = """Ты врач. Посмотри данные пациента и скажи, какие анализы стоит сдать.
Если чего-то нет в показанных данных — так и напиши, что анализ не сдавался.
Ответ на русском, без списков, максимум пять предложений, тон разговорный."""

def run(text):
    return gp_context.judge_absence_claims(text)
'''

TRACT_NO_SURFACE = '''
import anthropic
import labs_db

PROMPT = """Ты помощник. Опиши тренд сна за неделю одним абзацем, без советов
и без упоминания анализов. Тон разговорный, максимум пять предложений, по-русски."""
'''


def _scan(tmp_path, files: dict):
    import integrity_tests as it
    for name, body in files.items():
        (tmp_path / name).write_text(body, encoding="utf-8")
    return it.check_absence_surface_guarded(root=tmp_path)


def test_sensor_reddens_on_new_unguarded_tract(tmp_path):
    """Новый тракт с поверхностью и без сторожа = красный. Это и есть гаситель."""
    import integrity_tests as it
    with pytest.raises(AssertionError) as e:
        _scan(tmp_path, {"newtract.py": TRACT_WITH_SURFACE})
    assert "newtract" in str(e.value)


def test_sensor_silent_when_tract_calls_the_judge(tmp_path):
    """Тот же промпт, но выход проведён через судью → датчик молчит.

    Позитивный контроль к предыдущему: краснеет ПОВЕРХНОСТЬ БЕЗ сторожа, а не
    любой файл со словом «анализ».
    """
    res = _scan(tmp_path, {"guardedtract.py": TRACT_GUARDED})
    assert res["открытых"] == 0 and res["под сторожем"] == 1


def test_sensor_silent_when_no_surface(tmp_path):
    """Тракт в периметре, но его промпт не приглашает говорить об отсутствии."""
    res = _scan(tmp_path, {"quiettract.py": TRACT_NO_SURFACE})
    assert res["периметр"] == 1 and res["с поверхностью"] == 0


TRACT_INHERITED_SURFACE = '''
import anthropic
import labs_db
import hai_core

def run():
    system = hai_core.get_system_prompt()   # промпт ЧУЖОЙ, своих констант нет
    return system
'''


def test_inherited_surface_counts(tmp_path):
    """Тракт без своих промпт-констант, берущий чужой промпт, — это поверхность.

    Найдено испытанием 14.09: детектор объявил `lifestyle_agents` и `hai_hypotheses`
    чистыми, потому что их роли живут в `specialists/*.md` и в системном промпте бота,
    а живые вызовы показали ложь у обоих. Мутация: убрать INHERITS из детектора —
    тракт-приманка снова станет «чистым», и тест краснеет.
    """
    with pytest.raises(AssertionError) as e:
        _scan(tmp_path, {"borrowedtract.py": TRACT_INHERITED_SURFACE})
    assert "borrowedtract" in str(e.value)


def test_empty_perimeter_is_a_failure_not_a_pass(tmp_path):
    """Пустой периметр = детектор смотрит мимо дерева. Зелёный здесь был бы
    тривиальным (урок 29.07): датчик обязан упасть, а не отрапортовать «чисто»."""
    import integrity_tests as it
    with pytest.raises(AssertionError) as e:
        it.check_absence_surface_guarded(root=tmp_path)
    assert "периметр пуст" in str(e.value)


def test_real_repo_has_no_open_tracts():
    """Живой прогон по репозиторию: baseline держится, и периметр непуст.

    Замер 14.09: периметр 14 модулей, под сторожем 4, с поверхностью 6, открытых 0.
    """
    import integrity_tests as it
    res = it.check_absence_surface_guarded()
    assert res["периметр"] >= 10
    assert res["открытых"] == 0
