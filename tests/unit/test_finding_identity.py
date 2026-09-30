"""Характеризация имени и класса находки (finding_identity).

Независимо придуманные метки меняют счётчик, возраст и дату. Имя находки
обязано пережить эти изменения, сохраняя различия предметов и тенантов.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import pytest

import finding_identity as fi

# Независимо придуманные метки; исходные логи не используются.
SAME_THING_DIFFERENT_DAYS = [
    ("staging[fixture_a]: 26 строк не классифицированы 17д (>7д)",
     "staging[fixture_a]: 32 строк не классифицированы 18д (>7д)"),
    ("[fixture_a] промоут стоит: 5 строк ждут 34д (порог 7д)",
     "[fixture_a] промоут стоит: 5 строк ждут 36д (порог 7д)"),
    ("survivorship: 6 constitution_conflicts старше 32д",
     "survivorship: 6 constitution_conflicts старше 42д"),
    ("[fixture_b] порогов без единой строки данных: 8 из 19",
     "[fixture_b] порогов без единой строки данных: 7 из 19"),
]

DIFFERENT_THINGS = [
    "survivorship: 6 constitution_conflicts старше 32д",
    "survivorship: 9 pending proposals старше 17д",
    "survivorship: assessments просрочены",
    "staging[fixture_a]: 26 строк не классифицированы 17д (>7д)",
    "staging[fixture_b]: 12 строк не классифицированы 16д (>7д)",
]


def test_same_finding_keeps_one_name_across_days():
    """Позитив: смена любых чисел в метке не рождает новую находку."""
    for a, b in SAME_THING_DIFFERENT_DAYS:
        assert fi.finding_key(a) == fi.finding_key(b), f"имя поехало: {a!r} vs {b!r}"


def test_different_findings_keep_different_names():
    """Негативный контроль: без него правило «всё в одно имя» тоже прошло бы позитив.

    Ловит ложное разделение (conit слишком крупный): если бы имя резалось по первому
    двоеточию, три разные находки survivorship слились бы в одну, и закрытие конфликтов
    погасило бы вопрос про опросники."""
    keys = [fi.finding_key(x) for x in DIFFERENT_THINGS]
    assert len(set(keys)) == len(keys), f"разные находки слиплись: {keys}"


def test_emoji_and_case_do_not_change_name():
    assert fi.finding_key("⚠️ Producer_Registry: 1 задач") == fi.finding_key("producer_registry: 4 задач")


def test_explicit_key_beats_text():
    """Датчик, которому нужна устойчивость к переформулировке, задаёт имя сам."""
    assert fi.finding_key("какая угодно метка 42", explicit="lab:staging_unclassified") == "lab:staging_unclassified"


def test_unknown_label_goes_to_engineering_queue():
    """Решение владельца 30.09: незнакомое — в инженерную очередь, не на его стол.
    Метки, по которым 28–29.09 ему пришли две карточки, — дословно из integrity_tests."""
    assert fi.finding_class("нечто новое и странное") == "fix"
    assert fi.finding_class("launchd: джоб без копии в репозитории стало больше (25 > 23)") == "fix"
    assert fi.finding_class("деплой оставит джобы на СТАРОМ коде (хук их не перезапускает)") == "fix"


def test_owner_own_decision_returning_is_decide():
    """Условие пересмотра его решения — вопрос ему, явно, а не через умолчание."""
    assert fi.finding_class("партнёрский кран литературы: условие возврата наступило") == "decide"
    assert fi.finding_class("тенантов стало больше — решение «молчание не признак» устарело") == "decide"


def test_known_labels_get_their_class():
    assert fi.finding_class("security:pip_audit: pip 26.1.2") == "fix"
    assert fi.finding_class("Пробам судить не на чем (состояние данных)") == "standing"
    assert fi.finding_class("[fixture_b] порогов без единой строки данных: 8 из 19") == "standing"


def test_explicit_class_beats_substring_but_only_if_known():
    assert fi.finding_class("producer_registry: ...", explicit="decide") == "decide"
    # мусорное значение НЕ принимается молча: падаем обратно на подстроку, не на мусор
    assert fi.finding_class("producer_registry: ...", explicit="выдумка") == "fix"


@pytest.mark.parametrize("label", [
    # Дословно из integrity_tests (метки, по которым 14–22.09 владелец получал карточки).
    "на MacBook незакоммиченная работа: 1 файлов",
    "похоже на захват чужого файла в коммит: 1 коммит(ов)",
    "детектор захвата не смог судить 2 из 5 коммит(ов)",
    "вера построена ДО последнего изменения данных",
    "устаревание веры не проверено",
])
def test_engineering_labels_are_fix_not_owner_questions(label):
    """Решение владельца 23.09 («да»): оракул у этих находок — сессия, а не он."""
    assert fi.finding_class(label) == "fix"
