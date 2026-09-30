"""Дайджест на языке получателя (нить digest-lang, 28.09)."""
import json

import weekly_digest as wd

RU = "*Что изменилось 21.09–27.09.2026*\n\n*Сон*\nТеперь бот видит 2 фазы.\n\n*Ещё починили:* мелочи."
EN = "*What changed 21.09–27.09.2026*\n\n*Sleep*\nThe bot now sees 2 phases.\n\n*Also fixed:* small things."


def test_faithful_translation_passes():
    assert wd.translation_problems(RU, EN) == []


def test_translation_that_adds_a_number_or_drops_a_paragraph_is_caught():
    assert "numbers" in wd.translation_problems(RU, EN.replace("2 phases", "3 phases"))
    assert "paragraphs" in wd.translation_problems(RU, EN.replace("\n\n*Also fixed:* small things.", ""))
    assert "cyrillic" in wd.translation_problems(RU, EN.replace("Sleep", "Сон"))
    assert "header" in wd.translation_problems(RU, EN.replace("*What changed", "*This week"))


def test_english_tail_is_not_a_sujet():
    assert wd._sujets(EN) == ["*Sleep*\nThe bot now sees 2 phases."]


def test_english_jargon_is_banned_without_false_positives():
    assert wd.gate_text("*What changed*\nWe fixed the pipeline.", frozenset()).verdict == "blocked"
    assert wd.gate_text("*What changed*\nYour report is clearer; the logic is simpler.", frozenset()).verdict == "pass"


def test_text_for_never_substitutes_russian():
    d = {"text": RU, "text_en": None}
    assert wd.text_for(d, "ru") == RU and wd.text_for(d, "en") is None


def test_english_tenant_without_translation_blocks(tmp_path, monkeypatch):
    import i18n
    monkeypatch.setattr(wd, "OUT_DIR", tmp_path)
    monkeypatch.setattr(wd, "tenant_lexicon", lambda: frozenset())
    monkeypatch.setattr(i18n, "lang_of", lambda profile=None: "en")
    (tmp_path / "2026-W39.json").write_text(json.dumps(
        {"week": "2026-W39", "text": RU, "text_en": None, "en_problems": ["numbers"]}), encoding="utf-8")
    v = wd.write_tenant_verdict("2026-W39", tag="stranger")
    assert v.verdict == "blocked" and v.kind == "translation" and v.hits_class == ["numbers"]


def test_english_tenant_is_judged_on_the_english_text(tmp_path, monkeypatch):
    import i18n
    monkeypatch.setattr(wd, "OUT_DIR", tmp_path)
    monkeypatch.setattr(wd, "tenant_lexicon", lambda: frozenset({"phases"}))
    monkeypatch.setattr(i18n, "lang_of", lambda profile=None: "en")
    (tmp_path / "2026-W39.json").write_text(json.dumps(
        {"week": "2026-W39", "text": RU, "text_en": EN}), encoding="utf-8")
    assert wd.write_tenant_verdict("2026-W39", tag="stranger").verdict == "blocked"


def test_english_keeps_only_a_clean_translation(monkeypatch):
    monkeypatch.setattr(wd, "_translate", lambda t: EN)
    assert wd._english(RU, frozenset(), raw="2 21 27 09 2026") == (EN, [])
    monkeypatch.setattr(wd, "_translate", lambda t: EN.replace("2 phases", "3 phases"))
    en, probs = wd._english(RU, frozenset(), raw="2 21 27 09 2026")
    assert en is None and probs == ["numbers"]
    def boom(t):
        raise RuntimeError("api down")
    monkeypatch.setattr(wd, "_translate", boom)
    assert wd._english(RU, frozenset(), raw="") == (None, ["RuntimeError"])
