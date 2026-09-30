"""X4: invented examples only; no application bootstrap, DB, network or fixtures.

Run directly: python3 -B tests/unit/test_en_output_guards.py
The AST loader executes the actual guard definitions, not copies of their logic.
This deliberately bypasses application imports and tests/conftest.py. It does not
prove application wiring. The mutation script removes each English alternative.
"""
from __future__ import annotations

import ast
import logging
from pathlib import Path
import re
import sys
from datetime import date, timedelta
from string import Formatter
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import i18n  # explicit language below; never reads a profile
import lab_canon  # pure dictionary / matching module; the real shared synonyms

TODAY = date(2040, 9, 28)  # fictional clock, not a patient date
LAST = "2040-09-25"


def _load(filename, names, **extra):
    wanted = set(names.split())
    selected, found = [], set()
    for node in ast.parse((ROOT / filename).read_text(encoding="utf-8")).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            defined = {node.name}
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            defined = {t.id for t in targets if isinstance(t, ast.Name)}
        else:
            continue
        if defined & wanted:
            selected.append(node)
            found |= defined & wanted
    assert found == wanted, (filename, wanted - found)
    ns = dict(re=re, _re_backref=re, date=date, timedelta=timedelta,
              get_today=lambda: TODAY, i18n=i18n, log=logging.getLogger(__name__))
    ns.update(extra)
    exec(compile(ast.Module(body=selected, type_ignores=[]), filename, "exec"), ns)
    return SimpleNamespace(**ns)


def _context():
    return _load("gp_context.py", """
        _ABSENCE_RE _REF_WORD _WINDOW_QUALIFIER_RE _CLAUSE_SPLIT _DELIM _ADJ_TAIL
        _analyte_patterns _is_adjective_match _subjects_of absent_claims_contradicted
        _RECO_RE _SENTENCE_SPLIT_RE recommendations_without_evidence annotate_lab_recency
        """, _ldb=SimpleNamespace(
            get_recent_labs=lambda days: [{"test_name": "HGB", "date": LAST}],
            get_effective_lab_schedule=lambda: {"HGB": {"interval_days": 30}}))


def test_absence_english_claims_and_russian_control():
    g = _context()
    for claim in ("Hemoglobin was never tested.", "HGB was never measured.",
                  "HGB was never done.", "HGB has not been tested.",
                  "HGB has never been checked.", "There is no data for HGB.",
                  "HGB is missing.", "No record of a hemoglobin test exists.",
                  "Гемоглобин никогда не сдавался.", "HGB — нет данных."):
        hits = g.absent_claims_contradicted(claim.upper(), {"HGB": LAST})
        assert [h["test"] for h in hits] == ["HGB"], claim
    for honest in (f"HGB was last tested on {LAST}; no newer measurement is reported.",
                   "I never said that HGB was low.", "HGB is noteworthy.",
                   f"Гемоглобин сдан {LAST}."):
        assert not g.absent_claims_contradicted(honest, {"HGB": LAST}), honest


def test_reference_word_exempts_ranges_not_absent_measurements():
    g = _context()
    assert g.absent_claims_contradicted("HGB has not been tested.", {"HGB": LAST})
    for honest in ("HGB REFERENCE RANGE: no data.", "HGB reference interval is missing.",
                   "HGB — референс отсутствует."):
        assert not g.absent_claims_contradicted(honest, {"HGB": LAST}), honest
    assert g.absent_claims_contradicted("HGB is missing; preferences are recorded.", {"HGB": LAST})
    assert not g._REF_WORD.search("preferences")


def test_window_qualifiers_judge_the_stated_window():
    g = _context()
    for qualifier in ("in the window", "within the reported window", "during the window",
                      "over the last 14 days", "in the past 14 days", "for 14 days",
                      "over 14 days", "in 14 days", "в окне", "за последние 14 дней"):
        claim = f"HGB — no data {qualifier}."
        hits = g.absent_claims_contradicted(claim, {"HGB": LAST}, window_days=14)
        assert len(hits) == 1 and hits[0]["kind"] == "в окне", qualifier
        assert not g.absent_claims_contradicted(claim, {"HGB": "2040-07-01"}, window_days=14)
    # Explicit days override the caller's wider window, in both languages.
    for honest in ("HGB — no data in the last 14 days.", "HGB — нет данных за 14 дней."):
        assert not g.absent_claims_contradicted(honest, {"HGB": "2040-07-01"}, window_days=730)
    ru = g.absent_claims_contradicted("HGB — нет данных в окне.", {"HGB": LAST}, 14)
    assert len(ru) == 1 and ru[0]["kind"] == "в окне"
    assert not g._WINDOW_QUALIFIER_RE.search("windowless; surpassing 14 days")


def test_recommendations_require_acknowledging_fresh_evidence():
    g = _context()
    schedule = {"HGB": {"interval_days": 30}}
    for claim in ("Retest HGB.", "RECHECK hemoglobin.", "Repeat HGB.", "Re-measure HGB.",
                  "Order a hemoglobin test.", "I recommend HGB.", "Check HGB.",
                  "Get a hemoglobin test.", "Have HGB tested.", "Take a hemoglobin test.",
                  "Perform a hemoglobin test.", "Schedule a hemoglobin test.",
                  "Сдать гемоглобин.", "Перепроверить HGB."):
        hits = g.recommendations_without_evidence(claim, {"HGB": LAST}, schedule)
        assert len(hits) == 1 and hits[0]["test"] == "HGB", claim
    for honest in (f"Repeat HGB; the last test was on {LAST}.",
                   "Check whether HGB was measured.", "Check if HGB was measured.",
                   "The HGB measurement is recorded.", "HGB disorder is not asserted.",
                   "In order to support HGB, walk daily.", "Measure your steps; HGB is fine.",
                   f"Сдать HGB повторно, предыдущий анализ {LAST}."):
        assert not g.recommendations_without_evidence(honest, {"HGB": LAST}, schedule), honest
    assert not g.recommendations_without_evidence("Retest HGB.", {"HGB": "2030-01-01"}, schedule)


def test_recency_suffix_uses_i18n_and_keeps_russian_bytes():
    g = _context()
    for lang, text, note in (("en", "Retest HGB.", "(last tested: HGB — 25.09.2040)"),
                             ("ru", "Сдать HGB.", "(последний раз сдано: HGB — 25.09.2040)")):
        with patch.object(i18n, "lang_of", return_value=lang):
            assert g.annotate_lab_recency(text) == text + "\n" + note
            bounded = text + f" Last measured: {LAST}."
            assert g.annotate_lab_recency(bounded) == bounded
    placeholders = [{field for _, field, _, _ in Formatter().parse(
        i18n.t("gp.task.lab_recency", lang=lang)) if field} for lang in ("ru", "en")]
    assert placeholders == [{"parts"}, {"parts"}]


def test_analyte_names_come_from_existing_canon():
    g = _context()
    # Canonical abbreviation, English synonym and Russian synonym denote one invented row.
    assert "hemoglobin" in lab_canon.synonyms_for("HGB")
    for name in ("HGB", "hemoglobin", "гемоглобин"):
        assert g.absent_claims_contradicted(f"{name} has not been tested.", {"HGB": LAST})
    assert not g.absent_claims_contradicted("HGBX has not been tested.", {"HGB": LAST})


def test_backrefs_remove_orphans_but_keep_independent_sentences():
    g = _load("gp_agent.py", "_BACKREF _cut_sentences")
    bad = lambda sentence: {"invented"} if sentence.startswith("DEMO") else set()
    for ref in ("THIS", "These", "Those", "Such", "Therefore", "Thus", "Hence",
                "Consequently", "Accordingly",
                "As a result", "Because of this", "In this case", "Это", "Поэтому"):
        out, removed = g._cut_sentences(f"DEMO claim. {ref} explains the claim. Walk if you wish.", bad)
        assert out == "Walk if you wish." and len(removed) == 2, ref
    for honest in ("Thistle is a fictional label.", "Walking remains optional.", "Эталон условный.",
                   "It is a good day for a walk.", "So far the ring data look complete."):
        out, removed = g._cut_sentences("DEMO claim. " + honest, bad)
        assert out == honest and len(removed) == 1
    assert g._cut_sentences("This is independently supported.", bad)[0] == "This is independently supported."
    assert g._cut_sentences("DEMO claim.\n\nThis is a new paragraph.", bad)[0] == "This is a new paragraph."


def test_sleep_terms_remove_invented_measurements_only_without_data():
    scrub = _load("gp_agent.py", "_scrub_fabricated_sleep")._scrub_fabricated_sleep
    for term in ("sleep", "sleeping", "slept", "asleep", "deep", "bedtime", "wake",
                 "wakes", "waking", "woke", "awake", "awakening", "awakenings"):
        claim = f"DEMO {term.upper()} duration: 7 h."
        assert scrub(claim, False)[0] == "", term
        assert scrub(claim, True) == (claim, [])
    for honest in ("Sleep data is unavailable.", "The deepening shade lasted 7 h.",
                   "The oversleeping label spans 7 h.", "Данных сна нет."):
        assert scrub(honest, False) == (honest, [])
    assert scrub("Сон длился 7 часов.", False)[0] == ""


def test_sleep_word_units_are_not_unit_prefixes():
    scrub = _load("gp_agent.py", "_scrub_fabricated_sleep")._scrub_fabricated_sleep
    # REM was already recognised: these controls depend on the new units alone.
    for unit in ("hour", "hours", "hr", "hrs", "minute", "minutes", "min", "mins",
                 "point", "points", "percent"):
        assert scrub(f"DEMO REM: 7 {unit.upper()}.", False)[0] == "", unit
    for honest in ("REM data is unavailable.", "REM appeared in 7 minuscule drawings."):
        assert scrub(honest, False) == (honest, [])
    assert scrub("REM — 7 минут.", False)[0] == ""


def _extra(provider, key, evidence):
    return SimpleNamespace(provider=provider, semantic_key=key, evidence_summary=evidence)


def test_food_extra_uses_the_localized_anchor():
    ensure = _load("gp_agent.py", "_ensure_shown_extras")._ensure_shown_extras
    c = _extra("food", "food:demo", "в сезоне и полезно: учебный плод — условный пример")
    with patch.object(i18n, "lang_of", return_value="en"):
        claim = "The demo report contains everything."
        assert ensure(claim, [c], {c.semantic_key}) == claim + "\n\n" + i18n.t(
            "gp.brief.seasonal", lang="en", food="учебный плод", detail="условный пример")
        honest = "In season now — a fictional fruit: a demonstration only."
        assert ensure(honest, [c], {c.semantic_key}) == honest
        assert ensure("Begin season now.", [c], {c.semantic_key}) != "Begin season now."
        assert ensure(claim, [c], set()) == claim
        ru = "Учебный плод уже упомянут."
        assert ensure(ru, [c], {c.semantic_key}) == ru
        # Translation, not a second hardcoded vocabulary, supplies the anchor.
        original = i18n.t
        with patch.object(i18n, "t", side_effect=lambda key, **kw:
                          "Seasonal demo — {food}: {detail}." if key == "gp.brief.seasonal"
                          else original(key, **kw)):
            assert ensure("Seasonal demo — a fictional fruit.", [c], {c.semantic_key}) == "Seasonal demo — a fictional fruit."


def test_trail_extra_uses_the_localized_anchor():
    ensure = _load("gp_agent.py", "_ensure_shown_extras")._ensure_shown_extras
    c = _extra("trail", "trail:demo", "выходной — тропа: Учебная")
    with patch.object(i18n, "lang_of", return_value="en"):
        claim = "The demo report contains everything."
        # ожидание — из словаря, не копией строки: живой английский (X1) правит формулировку
        assert ensure(claim, [c], {c.semantic_key}) == claim + "\n\n" + i18n.t("gp.brief.trail", "en", name="Учебная")
        for honest in ("A TRAIL is available if you wish.", "Тропа уже упомянута."):
            assert ensure(honest, [c], {c.semantic_key}) == honest
        assert ensure("A trailer is parked.", [c], {c.semantic_key}) != "A trailer is parked."
        original = i18n.t
        with patch.object(i18n, "t", side_effect=lambda key, **kw:
                          "For the weekend — the {name} footpath." if key == "gp.brief.trail"
                          else original(key, **kw)):
            assert ensure("A footpath is available.", [c], {c.semantic_key}) == "A footpath is available."


def test_sea_extra_matches_words_not_substrings():
    ensure = _load("gp_agent.py", "_ensure_shown_extras")._ensure_shown_extras
    c = _extra("env", "sea:demo", "море — учебное описание")
    claim = "Research is mentioned."
    assert ensure(claim, [c], {c.semantic_key}) == claim + "\n\nМоре — учебное описание."
    for honest in ("The SEA is mentioned without measurements.", "A seaside walk is optional.",
                   "The ocean is mentioned.", "Море уже упомянуто."):
        assert ensure(honest, [c], {c.semantic_key}) == honest


def test_unsupported_numeric_word_units_with_observation_whitelist():
    check = _load("cbcr_hypothesis.py", """
        _NUM_WITH_UNIT_RE _PMID_RE _collect_observation_numbers _check_unsupported_numerics
        """)._check_unsupported_numerics
    for unit in ("month", "months", "days", "hours", "hrs", "years", "weeks", "minutes",
                 "mins", "milliseconds", "mIU/L", "ng/mL", "ug/mL", "mcg/mL", "ms",
                 "mg/dL", "mmol/L", "дней", "часов", "месяцев"):
        hyp = {"evidence_for": [{"fact": f"DEMO: 13 {unit}, 17 {unit}, 19 {unit}."}]}
        assert len(check(hyp, {"summary": "Invented observation without numbers."})) == 3, unit
        assert not check(hyp, {"summary": "Invented observation: 13, 17, 19."}), unit
    honest = {"evidence_for": [{"fact": "13 daylight sketches, 17 yearly sketches, 19 minuscule sketches."}]}
    assert not check(honest, {"summary": "No measured values in this invented observation."})


def test_jargon_counterparts_share_one_list():
    g = _load("cbcr_hypothesis.py", "_JARGON_BLACKLIST _PATIENT_VIEW_REQUIRED_KEYS _check_patient_view")
    base = dict.fromkeys(g._PATIENT_VIEW_REQUIRED_KEYS, "This is an invented example, with no conclusion.")
    for term in ("dissociation", "dysfunction", "neurotoxicity", "neurotoxic", "pathogenesis",
                 "iatrogenic", "anticorrelation", "anti-correlation", "диссоциация", "дисфункция"):
        hyp = {"patient_view": {**base, "might_mean": f"DEMO claims {term.upper()}."}}
        assert g._check_patient_view(hyp), term
    assert not g._check_patient_view({"patient_view": base})
    assert not g._check_patient_view({"patient_view": {
        **base, "noticed": "The invented labels are predysfunction and neurotoxicityish."}})
    food = _load("food_rule_review.py", "check_jargon", _jargon_blacklist=lambda: g._JARGON_BLACKLIST)
    assert "neurotoxicity" in food.check_jargon("DEMO claims NEUROTOXICITY.")
    assert not food.check_jargon("This is an invented example without a conclusion.")


def test_symptom_fallback_is_bilingual_and_word_bounded():
    g = _load("memory_salience.py", "SYMPTOM_TERMS symptom_terms is_symptom",
              _mc=SimpleNamespace(get_lexicon=lambda key, fallback: fallback))
    for symptom in ("heartburn", "insomnia", "weakness", "diarrhea", "diarrhoea", "constipation",
                    "bloating", "appetite", "common cold", "caught a cold", "have a cold",
                    "had a cold", "flu", "influenza", "изжога", "бессонница", "слабость"):
        assert g.is_symptom(f"DEMO mentions {symptom.upper()}."), symptom
    for honest in ("The flute lesson was influential.", "Cold weather followed a cold drink.",
                   "The fictional entry describes a walk.", "Учебный текст про прогулку.", None):
        assert not g.is_symptom(honest), honest


def test_month_names_preserve_month_number_and_russian_stems():
    g = _load("generate_constitutions.py", "_RU_MONTHS _RECENT_RE recent_mentions")
    months = ("January", "February", "March", "April", "May", "June", "July", "August",
              "September", "October", "November", "December")
    for month in months:
        assert g.recent_mentions(f"DEMO changed in {month.upper()} 2040.", TODAY, 365), month
        assert not g.recent_mentions(f"Long history: {month} 2030.", TODAY, 365), month
        assert not g.recent_mentions(f"Demo label {month}ish 2040.", TODAY, 365), month
    assert g.recent_mentions("Учебный эпизод в сентябре 2040.", TODAY, 365)
    assert not g.recent_mentions("Учебный эпизод в сентябре 2030.", TODAY, 365)
    # With a narrow horizon, September is recent and January is not.
    assert g.recent_mentions("September 2040", TODAY, 30)
    assert not g.recent_mentions("January 2040", TODAY, 30)


def test_recent_phrases_exclude_durable_horizons():
    g = _load("generate_constitutions.py", "_RU_MONTHS _RECENT_RE recent_mentions")
    for phrase in ("last week", "past 3 days", "last 2 months", "this month", "next week",
                   "coming month", "upcoming week", "yesterday", "the day before yesterday",
                   "recently", "lately", "на прошлой неделе", "вчера", "за последние 3 дня"):
        assert g.recent_mentions(f"DEMO changed {phrase.upper()}.", TODAY, 365), phrase
    for honest in ("The pattern spans the last 5 years.", "Long history: January 2030.",
                   "The label says yesterdayish.", "История за последние 5 лет."):
        assert not g.recent_mentions(honest, TODAY, 365), honest


if __name__ == "__main__":
    import traceback
    passed = failed = errors = 0
    for name, test in sorted(list(globals().items())):
        if name.startswith("test_") and callable(test):
            try:
                test()
                passed += 1
            except AssertionError:
                failed += 1
                print("FAIL", name)
                traceback.print_exc()
            except Exception:
                errors += 1
                print("ERROR", name)
                traceback.print_exc()
    print(f"{passed} passed, {failed} failed, {errors} errors")
    sys.exit(2 if errors else 1 if failed else 0)
