"""
test_food_rule_generator.py — критик+сохранение сгенерированных диет-правил (data-in-code-9 #2b-core).

Ключевое: пол безопасности отвергает пробивающее правило; прошедшее ложится в shadow; при
отклонении прежняя версия ключа ОСТАЁТСЯ (держим прошлый рулбук).
"""
from __future__ import annotations

import pytest

import health_db
import food_rule_generator as gen
import generated_food_rules as gfr

pytestmark = pytest.mark.unit

_PROV = {"generated_by": "monthly_consilium", "model": "claude-sonnet",
         "model_version_date": "2026-07-15", "data_sources": ["profile"]}

_GOOD = {"id": "condition_x",
         "condition": {"label": "операция X", "detect": {"regex": "операц"}},
         "fault": {"category": "iatrogenic"}, "lever": "management",
         "frame": {"energy": "gain", "protein": "high", "micro": ["B12", "iron"], "constraints": []},
         "evidence": {"source": "WCRF", "weight": "strong", "why": "операция X"}}

_BAD_FLOOR = {**_GOOD, "frame": {"energy": "standard", "micro": [], "constraints": ["sat_fat_limit"]}}


_ONE = ('{"id":"x","frame":{"energy":"gain"},'
        '"condition":{"label":"y"},"evidence":{"source":"z"}}')


def test_parse_proposals_variants():
    assert gen.parse_proposals([_GOOD])[0]["id"] == "condition_x"
    assert gen.parse_proposals('```json\n[' + _ONE + ']\n```')
    assert gen.parse_proposals("не json") == []
    assert gen.parse_proposals('{"rules": [' + _ONE + ']}')


def test_parse_proposals_prose_wrapped_bare_array():
    """Регрессия data-in-code-9: модель прифигачивает прозу к голому массиву (без ```-забора).
    Раньше json.loads падал → молчаливый [] → ВЕСЬ прогон в ноль. Теперь span-фолбэк ловит."""
    # проза ПЕРЕД массивом (реальный режим отказа, съевший боевой прогон 2026-07-15)
    assert len(gen.parse_proposals("Вот сформулированные правила:\n[" + _ONE + "]")) == 1
    # комментарий ПОСЛЕ массива
    assert len(gen.parse_proposals("[" + _ONE + "]\n\nНадеюсь, это поможет!")) == 1
    # массив внутри ```-забора, но с текстом вокруг забора
    assert len(gen.parse_proposals("Пожалуйста:\n```json\n[" + _ONE + "]\n```\nГотово.")) == 1
    # genuine мусор без JSON остаётся пустым (датчик #3 должен сработать выше по стеку)
    assert gen.parse_proposals("извините, не могу сформулировать правила") == []
    # незакрытая скобка не зависает и не парсится
    assert gen.parse_proposals("[" + _ONE) == []


def test_critique_accepts_good(db):
    conn = health_db.get_conn()
    assert gen.critique_proposal(_GOOD, _PROV, conn) == []




def test_critique_rejects_missing_provenance(db):
    conn = health_db.get_conn()
    reasons = gen.critique_proposal(_GOOD, {"generated_by": "x"}, conn)
    assert any("провенанс" in r for r in reasons)


def test_process_saves_good_to_shadow(db):
    conn = health_db.get_conn()
    summary = gen.process_proposals([_GOOD], _PROV, conn=conn)
    assert summary["n_saved"] == 1 and summary["n_rejected"] == 0
    shadow = gfr.get_rules(status="shadow", conn=conn)
    assert len(shadow) == 1 and shadow[0]["rule_key"] == "condition_x"






def test_generate_shadow_prose_wrapped_still_saves(db):
    """Сквозной регресс: даже если модель обернула массив прозой, генерация НЕ пустеет."""
    conn = health_db.get_conn()

    def prose_llm(system, user):
        import json as _j
        return "Конечно, вот правила:\n" + _j.dumps([_GOOD], ensure_ascii=False) + "\nУдачи!"

    summary = gen.generate_shadow("PKG", period_days=30, llm_call=prose_llm,
                                  prompt_text="p", conn=conn)
    assert summary["n_saved"] == 1 and summary["n_total"] == 1, summary


def test_food_generation_gap_alert_delivers(monkeypatch):
    """Датчик #3: пустой прогон ДОСТАВЛЯЕТСЯ оператору, не тонет в логе."""
    import monthly_consilium as mc
    import notify
    sent = []
    monkeypatch.setattr(notify, "fault", lambda text, person_key=None: sent.append((text, person_key)))
    mc._alert_food_generation_gap("тест: 0 распарсилось")
    assert len(sent) == 1
    assert "generation returned no rules" in sent[0][0] and sent[0][1] is None


# ── dead-гейт (2026-09-01): правило, не срабатывающее на пациенте, в склад не идёт ─────────

def test_critique_dead_when_regex_never_fires(db):
    """problem_list есть, но про ферритин там нет → dead. На старом коде принято — красный."""
    db.add_problem("p1", "Операция X")
    conn = health_db.get_conn()
    ferritin = {**_GOOD, "id": "ferritin_low",
                "condition": {"label": "Низкий ферритин", "detect": {"regex": "(?i)ферритин"}}}
    reasons = gen.critique_proposal(ferritin, _PROV, conn)
    assert any(r.startswith("dead:") for r in reasons), reasons
    assert gen.critique_proposal(_GOOD, _PROV, conn) == []          # операция X в problem_list → живое


def test_critique_dead_when_regex_does_not_compile(db):
    db.add_problem("p1", "Операция X")
    conn = health_db.get_conn()
    broken = {**_GOOD, "condition": {"label": "x", "detect": {"regex": "(операц"}}}
    reasons = gen.critique_proposal(broken, _PROV, conn)
    assert any("не компилируется" in r for r in reasons), reasons


def test_critique_skips_dead_gate_when_med_text_empty(db):
    """Пустой med_text (тест-БД без problem_list) → судить не на чем, гейт пропускает."""
    conn = health_db.get_conn()
    ferritin = {**_GOOD, "condition": {"label": "Низкий ферритин", "detect": {"regex": "ферритин"}}}
    assert gen.critique_proposal(ferritin, _PROV, conn) == []


def test_critic_and_applier_share_fire_predicate(db):
    """Паритет: критик пропустил ⇔ применитель сработает. Один предикат — food_profile.rule_fires."""
    import clinical_kb, food_profile
    db.add_problem("p1", "Операция X")
    conn = health_db.get_conn()
    med = clinical_kb.patient_med_text(conn)
    for p in (_GOOD, {**_GOOD, "condition": {"label": "ферритин", "detect": {"regex": "ферритин"}}}):
        critic_ok = not any(r.startswith("dead:") for r in gen.critique_proposal(p, _PROV, conn))
        assert critic_ok == bool(food_profile.rule_fires(p, med))

