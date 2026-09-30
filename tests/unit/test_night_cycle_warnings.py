"""Ночной цикл читает ПРЕДУПРЕЖДЕНИЯ, а не только падения (2026-09-13).

Почему это отдельный оракул. Машина против курьерства (цикл → парк → колокол) была
построена 02.08 и простаивала: она читала только `failures`, а весь поток, который
доходил до владельца, живёт в `warnings` — на 13.09 в артефакте 0 падений и 5
предупреждений, за 75 дней владельцу ушло 369 строк. Проверяем маршрут по классу и
то, что standing НЕ занимает стол владельца.

investigate замокан: маршрут судим детерминированно, без сети и без денег.
"""
from __future__ import annotations

import json

import pytest

import night_cycle as nc
import night_investigator
import parked_decisions as pd
from _time_inject import set_test_clock, clear_test_clock

# Независимо придуманные предупреждения сохраняют синтаксис маршрутизатора.
WARNINGS = [
    # 30.09: класс decide теперь только явный — берём метку владельца (канон анализов).
    ["[health] промоут стоит: 5 строк ждут 44д (порог 30д)", "самая старая с 2026-08-17"],
    ["survivorship: 2 constitution_conflicts старше 45д", "#4101 (61д); #4102 (63д)"],
    ["producer_registry: 1 scheduled-задач без датчика", "com.larry.health.weekly-digest"],
]


@pytest.fixture
def env(tmp_path, monkeypatch):
    integ = tmp_path / "integrity_latest.json"
    integ.write_text(json.dumps({"failures": [], "code_failures": [],
                                 "warnings": WARNINGS}), encoding="utf-8")
    monkeypatch.setenv("HEALTH_INTEGRITY_LATEST", str(integ))
    # Третий источник цикла (нити без движения) уводится в пустой указатель:
    # иначе run() читал бы настоящий docs/handoff и историю git, и вердикт теста
    # зависел бы от состояния проекта в день прогона (§20).
    monkeypatch.setenv("HEALTH_HANDOFF_INDEX", str(tmp_path / "нет-указателя.md"))
    monkeypatch.setenv("HEALTH_PARKED_DB", str(tmp_path / "parked.json"))
    monkeypatch.setenv("HEALTH_NIGHT_CYCLE_RECEIPT", str(tmp_path / "hb.json"))
    # Четвёртый источник (возврат красного потока) уводится в пустой список отчётов.
    # Иначе run() читал бы НАСТОЯЩУЮ таблицу agent_reports, и две красные ночи в проде
    # завели бы здесь лишнюю карточку — вердикт теста менялся бы от состояния базы (§20).
    monkeypatch.setattr(__import__("agent_reports_db"), "get_agent_report",
                        lambda name, date_str=None, n=1: [])
    set_test_clock("2026-09-13T08:00")
    # Судья всегда говорит «инженерный фикс» — чтобы было видно, что понижение до
    # владельца делает КЛАСС находки, а не мнение судьи.
    # owner_ask заполнен: иначе сработало бы правило трёх полей и мы бы проверяли не то.
    # Его собственный оракул — tests/unit/test_owner_card_three_fields.py.
    monkeypatch.setattr(night_investigator, "investigate",
                        lambda f: {"class": "dev_fix", "diagnosis": "диагноз " + f["summary"][:20],
                                   "owner_ask": {"subject": "учебная находка", "question": "что делаем?", "options": [
                                       {"label": "а", "cost": "одна цена"},
                                       {"label": "б", "cost": "другая цена"}]}})
    try:
        yield tmp_path
    finally:
        clear_test_clock()


def test_warnings_are_seen_at_all(env):
    """Позитив: цикл перестал быть слепым к предупреждениям."""
    s = nc.run()
    assert s["warn_seen"] == 3, s
    assert s["seen"] == 0, "падений нет — и это нормальный день"


def test_standing_does_not_occupy_the_table(env):
    """constitution_conflicts — standing (решение владельца 31.08): стол не занимает."""
    s = nc.run()
    assert s["warn_standing"] == 1 and s["warn_parked"] == 2, s
    ids = [g["id"] for g in pd.list_open()]
    assert not any("constitution_conflicts" in i for i in ids), ids


def test_decide_class_cannot_be_downgraded_by_the_judge(env):
    """Негативный контроль к §13-клаузе: судья сказал dev_fix по ВСЕМ трём, но находка
    класса decide обязана остаться делом владельца. Без этой проверки маршрут прошёл бы
    и при «верим судье», то есть при повышении прав автономным актором."""
    nc.run()
    kinds = {g["id"]: g["kind"] for g in pd.list_open()}
    decide = [i for i in kinds if "промоут стоит" in i]
    assert decide and kinds[decide[0]] == "owner_decision", kinds
    fix = [i for i in kinds if "producer_registry" in i]
    assert fix and kinds[fix[0]] == "dev_fix", kinds


def test_id_survives_changing_counters(env, tmp_path):
    """Идемпотентность по СМЫСЛУ, а не по тексту: назавтра счётчики другие, находка та же."""
    nc.run()
    before = sorted(g["id"] for g in pd.list_open())
    changed = [[w[0].replace("45д", "55д").replace("1 scheduled", "4 scheduled"), w[1] + "; ещё"]
               for w in WARNINGS]
    (tmp_path / "integrity_latest.json").write_text(
        json.dumps({"failures": [], "warnings": changed}), encoding="utf-8")
    nc.run()
    assert sorted(g["id"] for g in pd.list_open()) == before, "смена счётчиков породила дубли"


# ── Инженерная находка не попадает на стол по слову судьи (23.09) ──────────────
# Решение владельца «да». Для класса fix судья отправляет находку владельцу только по
# СТРУКТУРНОЙ причине (необратимо или пишет в его домен); «категория не из трёх
# разрешённых» больше не причина. Класс decide по-прежнему не понижается (тест выше).

def _judge_says_owner(monkeypatch, action):
    monkeypatch.setattr(night_investigator, "investigate",
                        lambda f: {"class": "owner_decision", "diagnosis": "д " + f["summary"][:20],
                                   "park_reason": "вне allowlist", "action": action,
                                   "owner_ask": {"subject": "учебная находка", "question": "что делаем?", "options": [
                                       {"label": "а", "cost": "одна цена"},
                                       {"label": "б", "cost": "другая цена"}]}})


def _kind_of(substr):
    kinds = {g["id"]: g["kind"] for g in pd.list_open()}
    hit = [i for i in kinds if substr in i]
    assert hit, kinds
    return kinds[hit[0]]


def test_fix_class_stays_engineering_when_judge_only_names_odd_category(env, monkeypatch):
    _judge_says_owner(monkeypatch, {"category": "commit_or_discard", "writes": [],
                                    "irreversible": False})
    nc.run()
    assert _kind_of("producer_registry") == "dev_fix"


def test_fix_class_goes_to_owner_when_action_is_irreversible(env, monkeypatch):
    """Негативный контроль: структурная причина по-прежнему поднимает к владельцу."""
    _judge_says_owner(monkeypatch, {"category": "commit_or_discard", "writes": [],
                                    "irreversible": True})
    nc.run()
    assert _kind_of("producer_registry") == "owner_decision"


# ── desk-guard (30.09): «здесь не судимо» и потерянная память стола ────────────

def test_not_judged_here_is_standing_whatever_the_label():
    """30.09 контейнер отказался судить хуки и счётчик тенантов (предмета не видно), а
    цикл сделал из отказа вопросы владельцу. Отказ судить — standing при ЛЮБОЙ метке,
    даже помеченной decide; та же метка с настоящей находкой остаётся decide."""
    import finding_identity as fi
    lbl = "тенантов стало больше — решение «молчание не признак» устарело"
    assert fi.class_of_warning(lbl, f"{fi.NOT_JUDGED_HERE}: предмет — launchd хоста") == "standing"
    assert fi.class_of_warning(lbl, "было 1, стало 2") == "decide"
    import triage_agent as ta
    daily, _digest, _deferred = ta.split_by_cadence(
        [[lbl, f"{fi.NOT_JUDGED_HERE}: предмет — launchd хоста"]], __import__("datetime").date(2026, 9, 30))
    assert daily == [], "утренний триаж не шлёт владельцу отказ судить"


def test_cycle_parks_nothing_when_desk_forgot(env, monkeypatch):
    """Стол забыл карточки (переезд без logs/, 30.09) — цикл не паркует ничего: иначе
    решённое вернулось бы на стол новым."""
    monkeypatch.setattr(pd, "_remembered", lambda: 39)
    s = nc.run()
    assert s.get("desk_memory_lost") == [0, 39]
    assert pd.list_open() == []
