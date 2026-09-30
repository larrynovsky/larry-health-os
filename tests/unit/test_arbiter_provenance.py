"""tests/unit/test_arbiter_provenance.py — E: структурный провенанс-гвард арбитра.

Промпт-правило «observations только от пользователя» не удержало 612/618 (LLM).
Структурный чек: наблюдение с уликами события/числа, которых НЕТ в словах юзера, —
конфабуляция ассистента → source=arbiter_unverified → B демотит (не confirmed).
"""
from __future__ import annotations
import pytest

pytestmark = pytest.mark.unit


def test_grounded_no_salient(db):
    import hai_chat
    assert hai_chat._grounded_in_user("хорошо спал, бодрый", "Я хорошо поспал") is True


def test_confabulation_ungrounded(db):
    """Анти-баг: «5:10 на вылет» при юзере «хорошо поспал» → НЕ заземлено."""
    import hai_chat
    assert hai_chat._grounded_in_user(
        "2021-05-12: ранний подъём в 5:10 на вылет", "Я хорошо поспал") is False


def test_grounded_when_user_said_it(db):
    import hai_chat
    assert hai_chat._grounded_in_user("проснулся в 5:10 на рейс", "встал в 5:10 на рейс") is True


def test_mirror_tags_ungrounded_as_unverified(db):
    """Ungrounded наблюдение пишется с source=arbiter_unverified (не conversation)."""
    import hai_chat, memory_facts_db as mf
    hai_chat._mirror_to_facts(
        {"observations": ["2021-05-12: ранний подъём в 5:10 на вылет"]},
        user_message="Я хорошо поспал")
    conf = [s for s in mf.get_facts("state") if "5:10" in s["value"]]
    assert conf and conf[0]["source"] == "arbiter_unverified"


def test_mirror_keeps_grounded_as_conversation(db):
    import hai_chat, memory_facts_db as mf
    hai_chat._mirror_to_facts(
        {"observations": ["хорошо спал"]}, user_message="Я хорошо поспал")
    s = [x for x in mf.get_facts("state") if "хорошо спал" in x["value"]]
    assert s and s[0]["source"] == "conversation"


# Незаземлённые fact/question/recommendation остаются предположениями модели.
# Независимо придуманные числа не получают статус слов пользователя.

def test_ungrounded_profile_update_is_quarantined(db):
    """Главный контроль ветки: LDL 127, которого нет в словах юзера, — не факт от него."""
    import hai_chat, memory_facts_db as mf
    hai_chat._mirror_to_facts(
        {"profile_updates": {"ldl_cholesterol": "127 mg/dL"}},
        user_message="Разбери учебный пример.")
    f = [x for x in mf.get_facts("fact") if x["key"] == "ldl_cholesterol"]
    assert f and f[0]["source"] == "arbiter_unverified"


def test_ungrounded_question_and_recommendation_quarantined(db):
    import hai_chat, memory_facts_db as mf
    hai_chat._mirror_to_facts(
        {"open_questions": ["Почему тромбоциты выросли с 214 до 347?"],
         "assistant_recommendations": [{"type": "protocol", "name": "учебная проверка источника",
                                        "rationale": "рост тромбоцитов, 347"}]},
        user_message="Поясни учебный пример.")
    q = [x for x in mf.get_facts("question") if "214" in x["value"]]
    r = [x for x in mf.get_facts("recommendation") if "источника" in x["value"]]
    assert q and q[0]["source"] == "arbiter_unverified"
    assert r and r[0]["source"] == "arbiter_unverified"


def test_grounded_profile_update_still_conversation(db):
    """Регресс-страж: сказанное человеком не должно уехать в карантин заодно."""
    import hai_chat, memory_facts_db as mf
    hai_chat._mirror_to_facts(
        {"profile_updates": {"smoking_status": "бросил курить"}},
        user_message="Я бросил курить")
    f = [x for x in mf.get_facts("fact") if x["key"] == "smoking_status"]
    assert f and f[0]["source"] == "conversation"


def test_unverified_flag_overrides_heuristic(db):
    """Материал с картинки — карантин по построению, даже если эвристика сказала
    «заземлено» (текст без цифр она пропускает: у неё нет улик, значит нет претензии)."""
    import hai_chat, memory_facts_db as mf
    assert hai_chat._grounded_in_user("Central City Hospital", "") is True
    hai_chat._mirror_to_facts(
        {"profile_updates": {"lab_source": "Central City Hospital"}},
        user_message="", unverified=True)
    f = [x for x in mf.get_facts("fact") if x["key"] == "lab_source"]
    assert f and f[0]["source"] == "arbiter_unverified"


def test_provenance_three_ways():
    import hai_chat
    assert hai_chat._provenance("LDL 127", "я бросил курить") == "arbiter_unverified"
    assert hai_chat._provenance("LDL 127", "у меня LDL 127") == "conversation"
    assert hai_chat._provenance("LDL 127", "у меня LDL 127", unverified=True) == "arbiter_unverified"


def test_quarantined_fact_is_never_confirmed():
    """Смычка с оракулом: карантин обязан демотироваться резолвером, иначе метка
    декоративна. Без этого контроля правка проверяла бы саму себя."""
    import beliefs
    assert beliefs._is_confirmed(
        {"source": "arbiter_unverified", "confirmations": 5}) is False
    assert beliefs._is_confirmed({"source": "conversation", "confirmations": 1}) is True
