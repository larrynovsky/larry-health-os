"""По собственному критерию агент обещание не закрывает (CLAUDE.md §13, клауза условия закрытия).

ЗАЧЕМ ЭТОТ ФАЙЛ. Норму записали текстом 21.09 (f68e984) — и в тот же день она была
нарушена дважды: 4fbbe31 (сессия, которая норму и принесла) и f83845a (соседняя, ещё не
дочитавшая свод). Обе поимки — чтением ПОСЛЕ коммита. Норма, которая держится на том,
что сессия дочитает свод до нужного абзаца, держится на везении.

ТРИ РАЗНЫХ ВОПРОСА, три группы тестов:
  1. судья — `intent_registry.unapproved_promotions` отличает подъём по критерию
     владельца от подъёма по своему (и негативные контроли к каждому различию);
  2. история — каждый коммит реестра ПОСЛЕ ввода нормы судится тем же судьёй. Это ловит
     обход хука (`--no-verify`) и смерть его проводки: хук — датчик, и у него самого
     должен быть кто-то, кто заметит его молчание (§14);
  3. проводка — pre-commit действительно зовёт судью.

ЧЕГО НЕ ДЕЛАЕТ. Не проверяет, что отметка `closes_when_by: owner` правдива: агент может
поставить её сам. Защита не в невозможности, а в том, что это отдельный называемый
коммит с автором, а не побочный эффект подъёма.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import intent_registry as ir

pytestmark = pytest.mark.consistency

ROOT = Path(__file__).resolve().parents[2]

# Коммит, которым норма введена. Судится всё, что ПОСЛЕ него: до нормы нарушить её нельзя.
NORM_COMMIT = "f68e984"

# Нарушения ПОСЛЕ нормы, которые уже разобраны. Данные, а не комментарий: исключение без
# причины сторож отвергает, а список не имеет права расти молча (ратчет ниже).
EXCUSED = {
    "f83845a": "соседняя сессия подняла context_declares_its_boundary до того, как "
               "прочитала норму; откатано ею же в 85c4736, критерий утверждён в 5745cf1",
}


def _reg(status, by="claude", inv="i1", sub="s1", with_criterion=True):
    i = {"id": inv, "status": status}
    if with_criterion and status != "holds":
        i["closes_when"], i["closes_when_by"] = "что-то стало правдой", by
    return [{"id": sub, "invariants": [i]}]


# ── 1. Судья ─────────────────────────────────────────────────────────────────

def test_promotion_by_agents_own_criterion_is_flagged():
    v = ir.unapproved_promotions(_reg("open", "claude"), _reg("holds"))
    assert len(v) == 1 and "s1::i1" in v[0] and "claude" in v[0]


def test_promotion_by_owner_criterion_passes():
    assert ir.unapproved_promotions(_reg("open", "owner"), _reg("holds")) == []


@pytest.mark.parametrize("status", ["open", "doc_drift", "designed_not_built"])
def test_every_non_holds_status_is_judged(status):
    assert ir.unapproved_promotions(_reg(status, "claude"), _reg("holds"))


def test_missing_author_is_not_an_approval():
    """Молчание — не утверждение: критерия нет вовсе → подъём тоже отбивается."""
    v = ir.unapproved_promotions(_reg("open", with_criterion=False), _reg("holds"))
    assert v and "НЕ НАЗВАН" in v[0]


def test_what_is_not_a_promotion_is_not_flagged():
    assert ir.unapproved_promotions(_reg("holds"), _reg("holds")) == []          # без перехода
    assert ir.unapproved_promotions(_reg("holds"), _reg("open", "claude")) == []  # вниз — можно
    assert ir.unapproved_promotions([], _reg("holds")) == []                      # родился holds
    assert ir.unapproved_promotions(None, _reg("holds")) == []                    # реестра не было


def test_same_invariant_id_in_another_subsystem_is_not_borrowed():
    old = _reg("open", "owner", sub="a") + _reg("open", "claude", sub="b")
    new = _reg("open", "owner", sub="a") + _reg("holds", sub="b")
    v = ir.unapproved_promotions(old, new)
    assert len(v) == 1 and v[0].startswith("b::i1")


# ── 2. История ───────────────────────────────────────────────────────────────

def _git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)


def _commits_after_norm():
    if _git("cat-file", "-e", f"{NORM_COMMIT}^{{commit}}").returncode != 0:
        pytest.skip(f"истории git здесь нет ({NORM_COMMIT} недостижим) — судить не на чем; "
                    f"настоящий прогон — ночной, в репозитории на Studio")
    out = _git("log", "--format=%h", f"{NORM_COMMIT}..HEAD", "--", "subsystem_intent.yaml")
    return [c for c in out.stdout.split() if c]


@pytest.mark.host_only
def test_no_commit_after_the_norm_promotes_by_own_criterion():
    bad = {}
    for c in _commits_after_norm():
        v = ir.unapproved_promotions(ir.registry_at(f"{c}^", ROOT), ir.registry_at(c, ROOT))
        if v and not any(c.startswith(k) or k.startswith(c) for k in EXCUSED):
            bad[c] = v
    assert not bad, (
        f"подъём до holds по чужому/своему критерию прошёл МИМО хука (обход --no-verify "
        f"или проводка в pre-commit умерла): {bad}")


@pytest.mark.host_only
def test_the_walker_actually_judges_something():
    """Сторож, сообщающий «нарушений 0», неотличим от сторожа, осмотревшего 0 коммитов.

    Плюс позитивный контроль на живой истории: известное нарушение f83845a судья ОБЯЗАН
    видеть — иначе зелёный теста выше ничего не значит."""
    commits = _commits_after_norm()
    assert len(commits) >= 5, f"осмотрено коммитов реестра после нормы: {len(commits)}"
    known = next(iter(EXCUSED))
    assert ir.unapproved_promotions(ir.registry_at(f"{known}^", ROOT),
                                    ir.registry_at(known, ROOT)), (
        f"судья не видит известное нарушение {known} — он ослеп")


def test_excuses_carry_a_cause_and_do_not_grow_quietly():
    assert all(len(why.strip()) > 20 for why in EXCUSED.values())
    assert len(EXCUSED) <= 1, "новое исключение — решение владельца и правка этого числа"


# ── 3. Проводка ──────────────────────────────────────────────────────────────

def test_pre_commit_calls_the_judge():
    src = (ROOT / "scripts" / "git-hooks" / "pre-commit").read_text(encoding="utf-8")
    assert "ir.unapproved_promotions(ir.registry_at(\"HEAD\"), ir.registry_at(\":\"))" in src
    assert "exit 9" in src
