"""K9 food pipeline: dictionary snapshot, tenant language and input/display separation.

Standalone: PYTHONDONTWRITEBYTECODE=1 python3 tests/unit/test_i18n_batch4.py
No conftest, database, private data, network or delivery. All I/O boundaries are doubles.
The pending maps are overlaid in memory; production i18n files are never changed.
"""
from __future__ import annotations

import ast
import datetime
import hashlib
import json
import re
import sys
from contextlib import contextmanager
from pathlib import Path
from string import Formatter
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import yaml

if __name__ != "__main__":
    import pytest
    pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
_K9_MODULES = ("clinical_kb", "food_genome", "food_staples", "repertoire", "food_profile", "food_quarterly")
_K9_CYR = re.compile(r"[А-Яа-яЁё]")
# Снимок публичных строк после нейтрализации каталога; региональные подписи — в пакете.
_K9_RU_SHA256 = "668c6ceb30205f8bd9197fdd23f3f2be889e0b02e1839a6719ed486a4ba2e2f1"
_K9_DOCUMENT_SHA256 = {
    "standard": "c09fe4f3192f0053ca993ad68ea9d235cde9aea0c2f1e3d7d07b944b49287c06",
    "maintain": "4bd0312e818802c395f14f2a889f667f8613ac65e86ff0e13023bdac55f54f4e",
    "gain": "1466d06b620cc976471d5fda9bf432a0caa05d2b65524e94fb3752c10b862493",
}  # Снимок меню с нейтральным публичным каталогом.


def _k9_tables():
    tables = {lang: yaml.safe_load((ROOT / "methodology/i18n" / f"{lang}.yaml").read_text())
              for lang in ("ru", "en")}
    pending = ROOT / "plans/K9_NEW_KEYS_2026-09-29.yaml"
    if pending.exists():
        additions = yaml.safe_load(pending.read_text())
        assert set(additions) == {"ru", "en"}
        for lang in tables:
            for key, value in additions[lang].items():
                assert key not in tables[lang] or tables[lang][key] == value, (lang, key)
            tables[lang].update(additions[lang])
    return tables


@contextmanager
def _k9_environment(energy="gain", tenant_language="en", owner_language="ru"):
    """Load only the six task modules, with all external boundaries replaced before import."""
    import i18n

    tables = _k9_tables()
    state = SimpleNamespace(db_language=tenant_language)
    profile = {"identity": {"language": tenant_language, "name": "Example"}}
    constraints = ["lactose_free", "sat_fat_limit", "portion_aware", "low_GI", "purine_limit", "dumping_aware"]
    entries = [{"id": "invented_frame", "condition_key": "invented_medical", "kind": "frame_rule",
                "payload": {"energy": energy, "protein": "high", "micro": ["B12", "iron", "folate", "D", "Ca"],
                            "constraints": constraints, "why": "Practice rule"}}]

    def _k9_execute(sql, args=()):
        if "patient_profile" in sql:
            return SimpleNamespace(fetchone=lambda: (state.db_language, None))
        if "clinical_kb_conditions" in sql:
            rows = [("invented_medical", '{"problem_regex":"practice"}'),
                    ("invented_genome", '{"genome":{}}'), ("invented_profile", '{"bmi_below":1}')]
        elif "genetic_variants" in sql:
            rows = [{"gene": "MTHFR", "significance": "Risk"}]
        elif "problem_list" in sql:
            rows = []
        else:
            raise AssertionError(f"Unexpected query to test double: {sql}")
        return SimpleNamespace(fetchall=lambda: rows)

    conn = SimpleNamespace(execute=_k9_execute)
    boundaries = {
        "health_db": SimpleNamespace(get_conn=Mock(return_value=conn),
                                     get_profile_context=Mock(return_value=profile)),
        "profile_db": SimpleNamespace(get_patient_profile=Mock(return_value={"identity.language": owner_language})),
        "seasonal_produce": SimpleNamespace(in_season=Mock(return_value={})),
        "region_pack": SimpleNamespace(value=lambda key, default=None: default),
        "generated_food_rules": SimpleNamespace(get_active_rules=Mock(return_value=[])),
        "food_floor": SimpleNamespace(assert_floor=Mock(return_value=[])),
        "taste": SimpleNamespace(verdict=Mock(return_value={"status": "neutral"}),
                                 acceptable=Mock(return_value=True), rank_key=Mock(return_value=0),
                                 taste_note=Mock(return_value="")),
        "secrets_paths": SimpleNamespace(secrets_dir=Mock(side_effect=AssertionError("real secrets access"))),
        "_time_inject": SimpleNamespace(get_today=lambda: datetime.date(2030, 1, 1)),
    }
    modules = {name: ModuleType(name) for name in _K9_MODULES}
    with patch.dict(sys.modules, {**boundaries, **modules}), patch.object(i18n, "_table", tables.__getitem__):
        for name, module in modules.items():
            path = ROOT / f"{name}.py"
            module.__file__ = str(path)
            exec(compile(path.read_text(), str(path), "exec"), module.__dict__)
        ckb, fg, fs, rep, fp, fq = (modules[name] for name in _K9_MODULES)
        ckb.active_entries = Mock(return_value=entries)
        # Cover every seasonal label and every reason, independently of the private region pack.
        produce = list(fg.FOOD_BENEFITS)
        season = {"fruits": produce[:27], "vegetables": produce[27:], "seafood": list(fg._SEAFOOD_BENEFITS)}
        boundaries["seasonal_produce"].in_season.return_value = season
        rep._dishes = Mock(return_value=[{"title": "Practice dish", "category": "Practice category", "url": ""},
                                       {"title": "Second practice dish", "category": "", "url": ""}])
        yield SimpleNamespace(**modules, conn=conn, profile=profile, state=state, tables=tables,
                              boundaries=boundaries, entries=entries, season=season)


def test_k9_dictionary_snapshot_placeholders_and_vocabulary():
    tables = _k9_tables()
    ru = {k: v for k, v in tables["ru"].items() if k.startswith("food.")}
    en = {k: v for k, v in tables["en"].items() if k.startswith("food.")}
    assert set(ru) == set(en) and len(ru) == 176
    assert hashlib.sha256(json.dumps(ru, ensure_ascii=False, sort_keys=True).encode()).hexdigest() == _K9_RU_SHA256
    for key in ru:
        fields = [{(f, spec, conversion) for _, f, spec, conversion in Formatter().parse(table[key]) if f}
                  for table in (ru, en)]
        assert fields[0] == fields[1], key
        assert not _K9_CYR.search(en[key]), (key, en[key])
    # Execute the actual existing vocabulary oracle against the overlay: no second regex.
    source = (ROOT / "tests/unit/test_i18n.py").read_text()
    node = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef)
                and n.name == "test_person_copy_has_no_internal_vocabulary_or_formal_address")
    namespace = {"re": re, "_yaml": tables.__getitem__}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "vocabulary_oracle", "exec"), namespace)
    namespace[node.name]()


def test_k9_russian_documents_match_neutral_catalog_renderer():
    for energy, expected in _K9_DOCUMENT_SHA256.items():
        with _k9_environment(energy, tenant_language="ru", owner_language="en") as e:
            model = e.food_profile.build_food_profile(e.conn, month=7, profile=e.profile)
            doc = e.food_profile.render_food_document("Example", model)
            assert hashlib.sha256(doc.encode()).hexdigest() == expected, energy
    assert set(_K9_DOCUMENT_SHA256) == {"standard", "maintain", "gain"}


def test_k9_language_switch_covers_whole_document_and_all_catalog_items():
    with _k9_environment() as e:
        fp = e.food_profile
        for energy in ("standard", "maintain", "gain"):
            e.entries[0]["payload"]["energy"] = energy
            for lang in ("en", "ru", "en"):
                profile = {"identity": {"language": lang}}
                model = fp.build_food_profile(e.conn, month=7, profile=profile)
                assert model["language"] == lang
                model["frame"]["bmi"] = 123.45  # deliberately invented test value
                doc = fp.render_food_document("Example", model)
                assert ("BMI 123.45" if lang == "en" else "ИМТ 123.45") in doc
                assert bool(_K9_CYR.search(doc)) == (lang == "ru"), doc
                assert sum(map(len, model["categories"].values())) == 48 + 18
                assert len(model["micro"]) == 5 and len(model["limit"]) == 6
                assert len(model["from_repertoire"]) == 2
                assert model["frame"]["energy"] == energy
                assert bool(model["frame"]["constraints"] & {"sat_fat_limit", "portion_aware"}) == (energy == "standard")
                assert model["frame"]["reasons"]
        e.boundaries["profile_db"].get_patient_profile.assert_not_called()


def test_k9_language_is_from_supplied_tenant_even_when_profile_is_empty():
    with _k9_environment(tenant_language="en", owner_language="en") as e:
        resolve = e.clinical_kb.clinical_kb_language
        assert resolve(e.conn) == "en"
        assert resolve(e.conn, {}) == "ru"
        assert resolve(e.conn, {"identity": {"language": "de"}}) == "ru"
        assert resolve(e.conn, {"identity.language": "ru"}) == "ru"
        assert resolve(e.conn, {"identity": {"language": "en"}}) == "en"
        e.state.db_language = "ru"
        assert resolve(e.conn) == "ru"
        e.boundaries["profile_db"].get_patient_profile.assert_not_called()
        # With no explicit profile, an explicit connection still determines document language.
        assert e.food_profile.build_food_profile(e.conn)["language"] == "ru"
        with patch.object(e.conn, "execute", side_effect=RuntimeError("missing profile table")):
            assert resolve(e.conn) == "ru"
        assert resolve() == "en"  # only this process-local case reads profile_db


def test_k9_aliases_drive_selection_boosts_and_taste_in_both_languages():
    with _k9_environment() as e:
        genes = [{"gene": "MTHFR", "significance": "Risk"}]
        focus = {"id": "invented_focus", "kind": "benefit_focus", "condition_key": "oncology",
                 "payload": {"foods": ["помидоры"]}}
        e.clinical_kb.active_entries.return_value = e.entries + [focus]
        ru = e.food_genome.beneficial_this_month(7, genes, e.conn, lang="ru")
        en = e.food_genome.beneficial_this_month(7, genes, e.conn, lang="en")
        assert len(ru) == len(en) == 48
        for r, translated in zip(ru, en):
            assert r["food"] == translated["food_alias"]
            assert (r["tag"], r["kind"], r["boosted"]) == (translated["tag"], translated["kind"], translated["boosted"])
            assert not _K9_CYR.search(translated["food"] + translated["why"])
        assert {r["food_alias"] for r in en if r["boosted"]} == e.food_genome._FOLATE_FOODS | {"помидоры"}
        taste = e.boundaries["taste"]
        # Reject one source alias. Translating it before taste matching would miss this ban.
        taste.acceptable.side_effect = lambda name: name != "яблоки"
        taste.taste_note.return_value = {"ru": "Учебная заметка", "en": "Practice note"}
        for day in range(12):
            item = e.food_profile.pick_food_of_day(e.conn, 7, day, e.profile)
            assert item and item["food_alias"] != "яблоки"
            assert not _K9_CYR.search(item["food"] + item["why"])
        assert all(_K9_CYR.search(call.args[0]) for call in taste.acceptable.call_args_list)
        assert e.food_genome.food_of_day(7, genes, 0, lang="en")["food"]


def test_k9_known_frame_reasons_and_provenance_switch_without_changing_rules():
    with _k9_environment(energy="standard") as e:
        ckb, fp = e.clinical_kb, e.food_profile
        for lang in ("ru", "en"):
            labels = ckb.cond_trigger_type(e.conn, lang=lang)
            assert len(labels) == 3
            assert all(bool(_K9_CYR.search(v)) == (lang == "ru") for v in labels.values())
        for rule_id, key in fp._FRAME_REASON_KEYS.items():
            payload = {"why": e.tables["ru"][key], "constraints": ["lactose_free"]}
            ckb.active_entries.return_value = [{"id": rule_id, "condition_key": "invented_medical",
                                                "kind": "frame_rule", "payload": payload}]
            frame = fp.medical_frame(e.conn, e.profile, lang="en")
            assert frame["reasons"] == ["medical record: " + e.tables["en"][key]]
            assert frame["constraints"] == {"lactose_free"}
            assert payload["why"] == e.tables["ru"][key], "source data was mutated"
        # Tenant-edited data must not silently turn into the older dictionary message.
        payload["why"] = {"ru": "Учебная новая причина", "en": "A changed practice reason"}
        frame = fp.medical_frame(e.conn, e.profile, lang="en")
        assert frame["reasons"] == ["medical record: A changed practice reason"]
        # An unknown monolingual source is preserved, not mistranslated or dropped.
        payload["why"] = "Учебная причина без перевода"
        assert fp.medical_frame(e.conn, e.profile, lang="en")["reasons"] == ["medical record: Учебная причина без перевода"]


def test_k9_repertoire_bilingual_data_preserves_input_matching_and_rotation():
    with _k9_environment() as e:
        rep = e.repertoire
        dishes = [{"title": {"ru": "Учебное блюдо", "en": "Practice dish"},
                   "category": {"ru": "Учебная категория", "en": "Practice category"}},
                  {"title": {"ru": "Другое учебное блюдо", "en": "Another practice dish"}, "category": ""}]
        rep._dishes.return_value = dishes
        for lang in ("en", "ru", "en"):
            selected = rep.suggest_set(seed=0, n=6, lang=lang)
            assert len(selected) == 2
            assert all(bool(_K9_CYR.search(d["title"] + d["why"])) == (lang == "ru") for d in selected)
            assert rep.suggest(0, lang=lang)["title"] == dishes[0]["title"][lang]
        assert all("Practice" not in call.args[0] for call in e.boundaries["taste"].verdict.call_args_list)
        rep._dishes.return_value = []
        assert rep.suggest(lang="en") is None and rep.suggest_set(lang="en") == []


def test_k9_quarterly_caption_document_and_fallback_use_tenant_language():
    for lang in ("en", "ru"):
        with _k9_environment(tenant_language=lang, owner_language="ru" if lang == "en" else "en") as e:
            fq = e.food_quarterly
            e.profile["identity"]["name"] = ""  # no real personal data, including in file names
            secret_path = MagicMock()
            secret_path.__truediv__.return_value.read_text.side_effect = ["dummy-token", "dummy-chat"]
            doc_path, marker = MagicMock(), MagicMock()
            temp_path = MagicMock()
            temp_path.__truediv__.return_value = doc_path
            with patch.object(fq, "secrets_dir", return_value=secret_path), \
                    patch.object(fq, "Path", return_value=temp_path), \
                    patch.object(fq, "_marker", return_value=marker), \
                    patch.object(fq.subprocess, "run") as send:
                doc = fq.deliver(datetime.date(2030, 1, 1))
            assert bool(_K9_CYR.search(doc)) == (lang == "ru")
            name = e.tables[lang]["food.document.unnamed_person"]
            assert doc.startswith(e.tables[lang]["food.document.title"].format(name=name))
            assert "caption=" + e.tables[lang]["food.document.caption"] in send.call_args.args[0]
            doc_path.write_text.assert_called_once_with(doc)
            marker.write_text.assert_called_once_with("2030Q1")
            e.boundaries["profile_db"].get_patient_profile.assert_not_called()


def test_k9_literal_ratchet_only_exempts_input_aliases():
    source = (ROOT / "tests/unit/test_i18n_no_hardcoded_ru.py").read_text()
    tree = ast.parse(source)
    namespace = {"ast": ast, "re": re}
    for node in tree.body:
        if (isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in
                {"FILES", "ALLOWED_RU", "_CYR", "_LOG"} for t in node.targets)) or (
                isinstance(node, ast.FunctionDef) and node.name == "russian_literals"):
            exec(compile(ast.Module(body=[node], type_ignores=[]), "literal_oracle", "exec"), namespace)
    for module in _K9_MODULES:
        name = module + ".py"
        assert name in namespace["FILES"]
        allowed = namespace["ALLOWED_RU"].get(name, {})
        assert all(reason.startswith("INPUT:") for reason in allowed.values())
        hits = namespace["russian_literals"]((ROOT / name).read_text(), allowed)
        assert not hits, (name, hits)


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    checks = [v for k, v in list(globals().items()) if k.startswith("test_k9_")]
    for check in checks:
        check()
        print("PASS", check.__name__)
    print(f"{len(checks)} isolated checks passed; no database, network or real delivery")
