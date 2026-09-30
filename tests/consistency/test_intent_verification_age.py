"""tests/consistency/test_intent_verification_age.py — возраст подтверждения (Р-3/Р-4)
и носитель проб (Р-1).

ЗАЧЕМ. До 2026-07-29 реестр делил мир надвое: статус есть или его нет. Молчание
читалось как «всё хорошо». Но `holds`, подтверждённый прогоном год назад на другом
коде, и `holds`, подтверждённый вчера, — разные утверждения, а выглядели одинаково.

Два правила, два разных вопроса:
  · Р-4 (календарь): сколько прошло с последнего прогона при НЕИЗМЕННОМ коде.
    Потолок 90 дней — не блок, а пометка «протухло» для владельца.
  · Р-1 (носитель): правка кода подсистемы обесценивает подтверждение НЕМЕДЛЕННО,
    потому что оно было про другой код. Это блок на коммите.

ЧЕСТНАЯ ГРАНИЦА. Правило Р-1 проверяет, что дата ПЕРЕСТАВЛЕНА, а не что прогон
действительно был: дату можно вписать руками. Тот же класс, что `legacy` в K1 и
«K2 доказывает синтаксис, не исполнение» — назван вслух, а не спрятан.

ОБЛАСТЬ. Сейчас правило спрашивается с `validation_gate` — подсистемы, которой
нить владеет и у которой подтверждение реально исполнено. В реестре 78 holds-
инвариантов в 18 подсистемах, дата есть у одного; проставлять остальным даты,
которых я не знаю, значило бы сочинить подтверждения. Расширение области — решение
владельца, не следствие этого файла.
"""
from __future__ import annotations

import datetime
import subprocess
from pathlib import Path

import pytest

import intent_registry as ir

pytestmark = pytest.mark.consistency

ROOT = Path(__file__).resolve().parents[2]
GUARDED = "validation_gate"
D = datetime.date


def _inv(iid, status="holds", va=None, check_file=None, probe=True):
    """probe=True — инвариант, снятый ПРОБОЙ: только с таких спрашивается дата.
    Снятые автоматической проверкой (check) переисполняются каждым прогоном."""
    d = {"id": iid, "claim": "c" + (" plans/probe_x_2026-01-01.py" if probe else ""),
         "status": status}
    if va is not None:
        d["verified_at"] = va
    if check_file:
        d["check"] = {"file": check_file}
    return d


def test_check_backed_invariants_are_not_asked_for_a_date():
    """Различие check vs test. `check: {present/absent}` переисполняется на каждом
    коммите и в каждом ночном прогоне — его «подтверждение» непрерывно, и дата у него
    смысла не имеет. Требовать её значило бы завести 6 фиктивных дат в одной только
    validation_gate."""
    e = {"invariants": [_inv("by_check", probe=False), _inv("by_probe")]}
    assert [i["id"] for i in ir.unverified_holds(e, D(2026, 7, 29))] == ["by_probe"]
    assert ir.probe_backed(_inv("by_probe")) is True
    assert ir.probe_backed(_inv("by_check", probe=False)) is False


# ── Чистые правила: позитивные контроли ──────────────────────────────────────────

def test_missing_date_counts_as_stale_not_as_fresh():
    """Отсутствие подтверждения не должно выглядеть лучше честной старой даты —
    иначе выгодно молчать."""
    assert ir.stale_verification(_inv("a"), D(2026, 7, 29)) is True
    assert ir.stale_verification(_inv("a", va="2026-07-29"), D(2026, 7, 29)) is False


def test_broken_date_behaves_as_absent():
    """Урок DG-05: битая дата, проходящая проверку, молча обнуляет счётчик."""
    assert ir.verified_at(_inv("a", va="на днях")) is None
    assert ir.stale_verification(_inv("a", va="на днях"), D(2026, 7, 29)) is True


def test_ttl_boundary_is_exactly_90_days():
    """Граница названа числом, а не «примерно квартал»: 90 держится, 91 протухло."""
    inv = _inv("a", va="2026-01-01")
    assert ir.stale_verification(inv, D(2026, 1, 1) + datetime.timedelta(days=90)) is False
    assert ir.stale_verification(inv, D(2026, 1, 1) + datetime.timedelta(days=91)) is True


def test_only_holds_are_asked_for_confirmation():
    """С `open` подтверждения не спрашивают: он и не обещает, что проверен."""
    e = {"invariants": [_inv("h"), _inv("o", status="open")]}
    assert [i["id"] for i in ir.unverified_holds(e, D(2026, 7, 29))] == ["h"]


# ── Р-1: правка кода обесценивает подтверждение ──────────────────────────────────

def test_untouched_subsystem_is_not_disturbed():
    e = {"code_anchors": ["correlation_gate.py::x"], "invariants": [_inv("h", va="2026-01-01")]}
    assert ir.demands_reconfirmation(e, ["README.md", "other.py"], D(2026, 7, 29)) == []


def test_touching_code_invalidates_yesterdays_confirmation():
    """Вчерашнее подтверждение было про другой код — сегодня оно не считается."""
    e = {"code_anchors": ["correlation_gate.py::x"], "invariants": [_inv("h", va="2026-07-28")]}
    assert ir.demands_reconfirmation(e, ["correlation_gate.py"], D(2026, 7, 29)) == \
        [("h", "2026-07-28")]


def test_reconfirmed_today_passes_and_lowered_status_passes():
    """Два законных выхода из блока: прогнать заново либо честно понизить статус."""
    anchors = {"code_anchors": ["correlation_gate.py::x"]}
    fresh = {**anchors, "invariants": [_inv("h", va="2026-07-29")]}
    lowered = {**anchors, "invariants": [_inv("h", status="open", va="2026-07-28")]}
    assert ir.demands_reconfirmation(fresh, ["correlation_gate.py"], D(2026, 7, 29)) == []
    assert ir.demands_reconfirmation(lowered, ["correlation_gate.py"], D(2026, 7, 29)) == []


def test_check_file_counts_as_code_of_the_subsystem():
    """Якоря и check.file наполняются разными людьми и расходятся — читаем оба."""
    e = {"invariants": [_inv("h", va="2026-01-01", check_file="quarantine_db.py")]}
    assert "quarantine_db.py" in ir.entry_code_files(e)
    assert ir.demands_reconfirmation(e, ["quarantine_db.py"], D(2026, 7, 29)) == \
        [("h", "2026-01-01")]


# ── Живой реестр ─────────────────────────────────────────────────────────────────

def test_place_of_verification_is_named_or_absent_never_guessed():
    """Р-9. Неизвестное значение читается как ОТСУТСТВИЕ, а не как «наверное канон»:
    умолчание в сторону более сильного утверждения — премия за молчание."""
    assert ir.verified_on({"verified_on": "staging"}) == "staging"
    assert ir.verified_on({"verified_on": "CANON"}) == "canon"      # регистр не важен
    assert ir.verified_on({"verified_on": "прод"}) is None          # мусор ≠ канон
    assert ir.verified_on({}) is None


def test_guarded_subsystem_holds_name_where_they_were_verified():
    """Дата без места после Р-7 неполна: «прогнали 29-го» не отличает боевую базу от
    её снимка, а это разные утверждения."""
    e = ir.get_entry(GUARDED)
    nameless = [i["id"] for i in e.get("invariants", [])
                if i.get("status") == "holds" and ir.probe_backed(i)
                and ir.verified_on(i) is None]
    assert not nameless, (
        f"{GUARDED}: holds на пробе не называет место подтверждения: {nameless}. "
        f"Допустимо: verified_on: canon | staging")


def test_guarded_subsystem_holds_carry_a_confirmation_date():
    e = ir.get_entry(GUARDED)
    missing = [i["id"] for i in e.get("invariants", [])
               if i.get("status") == "holds" and ir.probe_backed(i)
               and ir.verified_at(i) is None]
    assert not missing, (
        f"{GUARDED}: holds-инвариант опирается на пробу, но даты подтверждения нет: "
        f"{missing}. Либо прогнать пробу и проставить `verified_at`, либо честно "
        f"понизить статус")
    # Позитивный контроль: без него файл станет vacuous, если regexp пробы сломается
    # и probe_backed начнёт возвращать False для всех — «ничего не проверено» прочтётся
    # как «всё в порядке» (класс BL-017).
    backed = [i["id"] for i in e.get("invariants", []) if ir.probe_backed(i)]
    assert backed, (
        f"{GUARDED}: не найдено НИ ОДНОГО инварианта, опирающегося на пробу. Либо реестр "
        f"изменился, либо сломан признак probe_backed — в обоих случаях правило возраста "
        f"перестало кого-либо стеречь")


def test_staged_change_to_guarded_code_requires_reconfirmation():
    """Р-1 на настоящем индексе. Вне git-контекста молчим ЯВНО (skip с причиной),
    а не притворяемся зелёными: staging-дерево без .git даёт именно этот случай."""
    try:
        staged = subprocess.run(["git", "diff", "--cached", "--name-only"], cwd=str(ROOT),
                                capture_output=True, text=True, check=True).stdout.split()
    except (subprocess.CalledProcessError, FileNotFoundError):
        pytest.skip("нет git-контекста (staging-дерево) — правило Р-1 здесь НЕ проверено")
    pending = ir.demands_reconfirmation(ir.get_entry(GUARDED), staged, ir_today())
    assert not pending, (
        f"тронут код подсистемы {GUARDED}, а подтверждение осталось прежним: {pending}.\n"
        f"Подтверждение было про ДРУГОЙ код (Р-1). Два законных выхода:\n"
        f"  1) прогнать пробу и поставить `verified_at` сегодняшним числом;\n"
        f"  2) честно понизить статус на `open` — это норма, а не поражение.")


def ir_today() -> datetime.date:
    """Время берём через контракт проекта, а не через datetime.today(): тесты обязаны
    уметь подменить часы (`_time_inject`)."""
    from _time_inject import get_today
    t = get_today()
    return t if isinstance(t, datetime.date) else datetime.date.fromisoformat(str(t))
