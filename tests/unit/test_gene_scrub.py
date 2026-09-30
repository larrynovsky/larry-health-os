"""
Хирургический скраб генов (Bug: досочинение генов, 2026-07-14).

Промпт-запрет не держит: Sonnet называет «знаменитые» гены по находке, даже когда их нет
в контексте. Гарантия — _scrub_unapproved_genes удаляет предложения с генами/rs,
не одобренными гейтом. Тест: неодобренный ген → предложение вон; одобренный → живёт;
не-геномные латинские токены (HRV/UV/REM) не трогаются.
"""
from __future__ import annotations

import pytest

import gp_agent

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _fixed_universe(monkeypatch):
    monkeypatch.setattr(gp_agent, "_gene_universe",
                        lambda: {"CRY1", "CLOCK", "FTO", "BDNF", "COMT"})


def test_unapproved_gene_sentence_removed(db):
    rep = ("Ночь короткая, HRV упал до 17. В геноме вариант CRY1 делает сон уязвимее. "
           "Шагов вчера 6000.")
    out, removed = gp_agent._scrub_unapproved_genes(rep, {"genome:energy:fto"})
    assert "CRY1" not in out
    assert "HRV" in out and "Шагов вчера 6000" in out  # не-геном и обычный текст целы
    assert removed and "CRY1" in removed[0]


def test_approved_gene_sentence_kept(db):
    rep = "Вариант FTO влияет на аппетит сегодня. Погода ясная."
    out, removed = gp_agent._scrub_unapproved_genes(rep, {"genome:energy:fto"})
    assert "FTO" in out
    assert removed == []


def test_rs_id_not_allowed_removed(db):
    rep = "Вариант rs1234567 намекает на гликемию. Вода — 2 литра."
    out, _ = gp_agent._scrub_unapproved_genes(rep, {"genome:energy:fto"})
    assert "rs1234567" not in out
    assert "Вода" in out


def test_non_gene_latin_tokens_survive(db):
    # UV, REM, SPF, AQI — не гены, не должны триггерить скраб.
    rep = "UV до 8.6 сегодня, REM в норме, SPF 50 снимает риск."
    out, removed = gp_agent._scrub_unapproved_genes(rep, set())
    assert removed == []
    assert "UV" in out and "REM" in out and "SPF" in out


def test_scrub_preserves_paragraphs(db):
    # Скраб чистит ВНУТРИ абзаца, но сохраняет разбивку \n\n (иначе бриф-кирпич).
    rep = "Тело устало.\n\nUV высокий, море тёплое.\n\nБобы полезны сегодня."
    out, removed = gp_agent._scrub_unapproved_genes(rep, set())
    assert removed == []
    assert out.count("\n\n") == 2
    assert "Бобы" in out
