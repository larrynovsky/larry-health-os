"""K5: isolated generator contracts. Run this file directly; no project conftest.

RU fixtures were captured from the unmodified generators on these invented inputs,
before adding language support. Never regenerate them from the code under test.
"""
import contextlib
import datetime
import importlib
import io
import json
from pathlib import Path
import plistlib
import re
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


_DESC_FUNCS = {"gen_blueprint": "blueprint_descriptions", "arch_guard": "arch_graph_descriptions",
               "gen_key_paths": "key_paths_descriptions", "gen_schedule": "schedule_descriptions",
               "gen_arch_blocks": "arch_blocks_descriptions",
               "gen_testing_contracts": "testing_contracts_descriptions"}


def _descriptions(mod, **kwargs):
    """Сборщик пояснений генератора — у каждого своё имя (дубль-гейт, 29.09)."""
    name = mod.__name__.rsplit(".", 1)[-1].removeprefix("k5_import_")
    return getattr(mod, _DESC_FUNCS[name])(**kwargs)

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
GOLDEN = ROOT / "tests/fixtures/arch_en_generators_ru.json"
NAMES = ("gen_blueprint", "arch_guard", "gen_key_paths", "gen_schedule",
         "gen_arch_blocks", "gen_testing_contracts")
DAY = "2040-01-02"
MODULE_DOC = "Описание учебного модуля " + "я" * 90
FUNCTION_DOC = "Описание учебной функции " + "ю" * 80
REASON = "Учебная причина " + "ж" * 125


class FixedDate(datetime.date):
    @classmethod
    def today(cls):
        return cls(2040, 1, 2)


@contextlib.contextmanager
def inputs():
    """All file inputs belong to a disposable directory inside this worktree."""
    with tempfile.TemporaryDirectory(prefix=".k5-test-", dir=ROOT) as directory:
        root = Path(directory)
        (root / "example.py").write_text(
            f'"""{MODULE_DOC}\nSecond line."""\n'
            'class Example:\n'
            f'    def read(self, a, b, c, d, e):\n        """{FUNCTION_DOC}"""\n'
            'async def fetch(x):\n    """Без перевода"""\n'
            'def _hidden(): pass\n', encoding="utf-8")
        (root / "integrity_tests.py").write_text(
            'LIMIT = 3  # Учебный порог | комментарий\n'
            'EMPTY = 0\n'
            'def check_example():\n    """Учебная проверка | описание\nignored"""\n'
            'check("Учебный датчик", check_example)\n'
            'check(dynamic_label, unknown)\n', encoding="utf-8")
        (root / "tests").mkdir()
        (root / "tests/test_example.py").write_text(
            f'@pytest.mark.xfail(reason="{REASON}\\nignored")\n'
            'def test_example(): pass\n'
            '@pytest.mark.xfail\nclass TestEmpty: pass\n', encoding="utf-8")
        launchd = root / "launchd"
        launchd.mkdir()
        cases = [
            {"StartCalendarInterval": [{"Weekday": i, "Hour": i, "Minute": 5}
                                       for i in range(7)]},
            {"StartCalendarInterval": [{"Day": 4, "Minute": 20}, {}, {"Weekday": 9}]},
            {"StartInterval": 10800}, {"StartInterval": 120}, {"StartInterval": 7},
            {"WatchPaths": ["/invented/watch"], "KeepAlive": True},
            {"KeepAlive": {"SuccessfulExit": False}}, {"RunAtLoad": True}, {},
        ]
        for i, data in enumerate(cases):
            data.update(Label=f"example.{i}", ProgramArguments=["python", "/invented/example.py"])
            (launchd / f"com.larry.health.{i}.plist").write_bytes(plistlib.dumps(data))
        report = root / "tests/reports" / DAY / "summary.json"
        report.parent.mkdir(parents=True)
        summary = {"date": DAY, "overall_exit": 1,
                   "total": {"tests": 7, "failures": 1, "errors": 2, "skipped": 3},
                   "per_layer": {"unit": {"tests": 7, "failures": 1, "errors": 2, "skipped": 3}}}
        report.write_text(json.dumps(summary), encoding="utf-8")
        yield root, launchd, report


@contextlib.contextmanager
def generators(root, launchd, report):
    # These stand-ins support importing the original (pre-K5) code too.
    infra = types.SimpleNamespace(is_primary=lambda hostname: True,
                                  CLOUD_HEALTH_DIR=Path("/invented/cloud"))
    db = types.SimpleNamespace(get_domain_signals=lambda domain: [
        {"metric": "example_metric", "label": "Учебный сигнал", "threshold_pct": 42,
         "r": 0.123}] if domain == "example_domain" else [])
    with contextlib.ExitStack() as stack:
        stack.enter_context(patch.dict(sys.modules, {"infra_config": infra, "health_db": db}))
        stack.enter_context(patch("subprocess.run", return_value=types.SimpleNamespace(stdout="example.py\n")))
        mods = {name: importlib.import_module(name) for name in NAMES}
        for mod in mods.values():
            for attr in ("ROOT", "REPO"):
                if hasattr(mod, attr):
                    stack.enter_context(patch.object(mod, attr, root))
        bp, ag, kp, sc, ab, tc = (mods[n] for n in NAMES)
        stack.enter_context(patch.object(bp, "date", FixedDate))
        stack.enter_context(patch.object(bp, "_ARCH_LAYERS", [("СЛОЙ ДАННЫХ", ["example.py", "missing.py"])]))
        stack.enter_context(patch.object(ag, "_log"))
        stack.enter_context(patch.object(ag, "DRY_RUN", False))
        stack.enter_context(patch.object(bp, "DRY_RUN", False))
        stack.enter_context(patch.object(sc, "LAUNCHD_DIR", launchd))
        stack.enter_context(patch.object(ab, "REPORTS_DIR", report.parent.parent))
        stack.enter_context(patch.object(ab, "DOMAINS_INVENTORY", ["example_domain", "empty_domain"]))
        stack.enter_context(patch.object(tc, "SRC", root / "integrity_tests.py"))
        for mod in (bp, ag):
            stack.enter_context(patch.object(mod, "ARCH_SNAPSHOT", root / "ARCH_SNAPSHOT.md"))
        for mod in (kp, sc, ab):
            stack.enter_context(patch.object(mod, "ARCH", root / "ARCH_SNAPSHOT.md"))
        stack.enter_context(patch.object(tc, "DOC", root / "TESTING_CONTRACTS.md"))
        stack.enter_context(patch.object(ag, "GRAPH_CACHE", root / ".arch_graph.json"))
        yield mods


def snapshots(mods, root, lang=None):
    kw = {} if lang is None else {"lang": lang}
    bp, ag, kp, sc, ab, tc = (mods[n] for n in NAMES)
    graph = {"example": ["alpha", "beta"], "alpha": ["beta"]}
    changes = ag.diff_graphs({"gone": [], "example": ["old"]}, graph, **kw)
    doc = root / "ARCH_SNAPSHOT.md"
    doc.write_text("**Версия:** synthetic\n" + ag._GRAPH_START + "\nold\n" + ag._GRAPH_END
                   + "\n" + ag._ARCH_LOG_START + "\n- old history\n" + ag._ARCH_LOG_END + "\n")
    ag.update_arch_snapshot(graph, "; ".join(changes), DAY, **kw)
    out = {"gen_blueprint": bp._build_arch_registry(**kw),
           "arch_guard": doc.read_text(), "gen_schedule": sc.build_section(**kw),
           "gen_testing_contracts": tc.render(**kw)}
    with patch.object(Path, "home", return_value=Path("/example-home")), \
            patch.object(Path, "exists", return_value=False):
        out["gen_key_paths"] = kp.build_section(**kw)
        integrations = ab.build_external_integrations(**kw)
    out["gen_arch_blocks"] = "\n".join([
        ab.build_test_coverage(**kw), ab.build_xfail_list(**kw), integrations,
        ab.build_domain_signals(**kw)])
    with patch.object(ab, "_latest_summary", return_value=None), \
            patch.object(ab, "_scan_xfails", return_value=[]):
        out["gen_arch_blocks"] += "\n" + ab.build_test_coverage(**kw) + "\n" + ab.build_xfail_list(**kw)
    return out


class RussianContract(unittest.TestCase):
    def compare(self, name):
        expected = json.loads(GOLDEN.read_text(encoding="utf-8"))[name].encode("utf-8")
        with inputs() as args, generators(*args) as mods:
            self.assertEqual(expected, snapshots(mods, args[0])[name].encode("utf-8"))
            self.assertEqual(expected, snapshots(mods, args[0], "ru")[name].encode("utf-8"))

    def test_gen_blueprint_ru_bytes(self): self.compare("gen_blueprint")
    def test_arch_guard_ru_bytes(self): self.compare("arch_guard")
    def test_gen_key_paths_ru_bytes(self): self.compare("gen_key_paths")
    def test_gen_schedule_ru_bytes(self): self.compare("gen_schedule")
    def test_gen_arch_blocks_ru_bytes(self): self.compare("gen_arch_blocks")
    def test_gen_testing_contracts_ru_bytes(self): self.compare("gen_testing_contracts")


def memory_miss(source):
    return None if re.search(r"[А-Яа-яЁё]", source) else source


def skeleton(mods):
    bp, ag, kp, sc, ab, tc = (mods[n] for n in NAMES)
    pairs = [(bp._ARCH_START, bp._ARCH_END), (ag._GRAPH_START, ag._GRAPH_END),
             (ag._ARCH_LOG_START, ag._ARCH_LOG_END), (kp.START, kp.END), (sc.START, sc.END)]
    pairs += [(start, end) for start, end, _ in ab.GENERATORS.values()]
    return "**Версия:** synthetic, leave this label\n" + "\n".join(
        start + "\n" + end for start, end in pairs) + "\nTAIL\n"


class EnglishContract(unittest.TestCase):
    def test_translation_precedes_truncation(self):
        with inputs() as args, generators(*args) as mods:
            bp, ab = mods["gen_blueprint"], mods["gen_arch_blocks"]
            cases = [
                (bp, MODULE_DOC, 80, "example.py  # {}"),
                (bp, FUNCTION_DOC, 65, "  read(a, b, c, d) — {}"),
                (bp, "Без перевода", 65, "  async fetch(x) — {}"),
                (ab, REASON, 120, "| `tests/test_example.py:2` | {} |"),
            ]
            for mod, source, limit, row in cases:
                render = bp._build_arch_registry if mod is bp else ab.build_xfail_list
                collect_args = {} if mod is bp else {"only": "xfail_list"}
                with self.subTest(generator=mod.__name__, source=source), \
                        patch("desc_translation.english") as lookup:
                    self.assertIn(row.format(source[:limit]), render("ru").splitlines())
                    self.assertIn(source, _descriptions(mod, **collect_args))
                    lookup.assert_not_called()
                for translated in ("Short English", "E" * limit, "Invented | English " + "e" * 140):
                    with self.subTest(generator=mod.__name__, source=source, translated=translated), \
                            patch("desc_translation.english", return_value=translated) as lookup:
                        output = render("en")
                        lookup.assert_any_call(source)
                        expected = translated[:limit]
                        if mod is ab:
                            expected = expected.replace("|", "\\|")
                        self.assertIn(row.format(expected), output.splitlines())

    def test_translation_failure_cannot_silently_remove_a_function(self):
        with inputs() as args, generators(*args) as mods:
            def translate(source):
                if source == FUNCTION_DOC:
                    raise ValueError("synthetic malformed memory")
                return source
            with patch("desc_translation.english", side_effect=translate):
                with self.assertRaisesRegex(ValueError, "synthetic malformed memory"):
                    mods["gen_blueprint"]._build_arch_registry("en")

    def test_domain_labels_use_the_displayed_string_and_never_export_correlation(self):
        with inputs() as args, generators(*args) as mods:
            ab = mods["gen_arch_blocks"]
            data = {"example_domain": [{"metric": "example_metric", "label": None, "r": 0.123}]}
            with patch.dict(sys.modules, {"health_db": None}), \
                    patch("desc_translation.english", side_effect=memory_miss) as lookup:
                block = ab.build_domain_signals("en", signals_by_domain=data)
            lookup.assert_called_once_with("None")
            self.assertIn("| None |", block)
            self.assertNotIn("0.123", block)

    def test_table_escaping_does_not_change_lookup_keys_and_backslashes_survive_writing(self):
        with inputs() as args, generators(*args) as mods:
            bp, ag, kp, sc, ab, tc = (mods[n] for n in NAMES)
            with patch("desc_translation.english", return_value=r"One | two \| three") as lookup:
                rendered = tc.render("en")
                self.assertIn(r"One \| two \| three", rendered)
                self.assertIn(unittest.mock.call("Учебная проверка \\| описание"), lookup.call_args_list)
            doc = args[0] / "ARCH_SNAPSHOT.en.md"
            doc.write_text(skeleton(mods))
            with patch("desc_translation.english", return_value=r"Description \1 \g<2>"), \
                    contextlib.redirect_stdout(io.StringIO()):
                bp.main(["--lang", "en"])
            self.assertIn(r"Description \1 \g<2>", doc.read_text())

    def test_all_blueprint_layers_have_english_names(self):
        with inputs() as args, generators(*args) as mods:
            bp = mods["gen_blueprint"]
            layers = [(layer, []) for layer in bp._TEXT["ru"]["layers"]]
            with patch.object(bp, "_ARCH_LAYERS", layers):
                output = bp._build_arch_registry("en")
            for layer in bp._TEXT["en"]["layers"]:
                self.assertIn("### " + layer, output)
            self.assertNotRegex(output, r"[А-Яа-яЁё]")

    def test_translation_keys_collectors_and_missing_counts(self):
        with inputs() as args, generators(*args) as mods:
            bp, ag, kp, sc, ab, tc = (mods[n] for n in NAMES)
            expected = {
                bp: [MODULE_DOC, FUNCTION_DOC, "Без перевода"],
                tc: ["Учебный датчик", "Учебная проверка \\| описание", "Учебный порог \\| комментарий"],
                ab: [REASON, "Учебный сигнал"],
            }
            for mod, sources in expected.items():
                with self.subTest(generator=mod.__name__), \
                        patch("desc_translation.english", side_effect=memory_miss) as lookup:
                    collected = _descriptions(mod)
                    self.assertCountEqual(collected, sources)
                    lookup.assert_not_called()
                    ctx = bp.DescriptionText("en")
                    if mod is bp:
                        out = bp._build_arch_registry("en", descriptions=ctx)
                    elif mod is tc:
                        out = tc.render("en", descriptions=ctx)
                    else:
                        out = ab.build_xfail_list("en", descriptions=ctx) + ab.build_domain_signals("en", descriptions=ctx)
                    called = [call.args[0] for call in lookup.call_args_list]
                    self.assertCountEqual([s for s in called if re.search(r"[А-Яа-яЁё]", s)], sources)
                    self.assertEqual(ctx.untranslated, len(sources))
                    self.assertEqual(out.count(" ⟨untranslated⟩"), len(sources))
                    for source in sources:
                        limit = {MODULE_DOC: 80, FUNCTION_DOC: 65, REASON: 120}.get(source)
                        self.assertIn(source[:limit] + " ⟨untranslated⟩", out)
            for mod in (ag, kp, sc):
                self.assertEqual(_descriptions(mod), [])

    def test_memory_hits_render_english_and_ru_never_looks_up_memory(self):
        with inputs() as args, generators(*args) as mods:
            bp, ag, kp, sc, ab, tc = (mods[n] for n in NAMES)
            sources = _descriptions(bp) + _descriptions(tc) + _descriptions(ab)
            mapping = {s: f"Translated description {i}" for i, s in enumerate(sources)}
            with patch("desc_translation.english", side_effect=lambda s: mapping.get(s, s)):
                outputs = [bp._build_arch_registry("en"), tc.render("en"),
                           ab.build_xfail_list("en"), ab.build_domain_signals("en")]
            combined = "\n".join(outputs)
            for translated in mapping.values():
                self.assertIn(translated, combined)
            self.assertNotRegex(combined, r"[А-Яа-яЁё]")
            self.assertNotIn("⟨untranslated⟩", combined)
            with patch("desc_translation.english", side_effect=AssertionError("RU must not use memory")):
                snapshots(mods, args[0], "ru")

    def test_all_owned_prose_is_english_including_empty_fallbacks(self):
        with inputs() as args, generators(*args) as mods, \
                patch("desc_translation.english", side_effect=lambda s: "Description" if memory_miss(s) is None else s):
            bp, ag, kp, sc, ab, tc = (mods[n] for n in NAMES)
            outputs = [bp._build_arch_registry("en"), tc.render("en"), sc.build_section("en"),
                       ab.build_test_coverage("en"), ab.build_xfail_list("en"),
                       ab.build_domain_signals("en"), ag.format_graph({"example": ["other"]}, "en"),
                       "\n".join(ag.diff_graphs({"gone": [], "same": ["old"]},
                                               {"new": [], "same": ["fresh"]}, "en"))]
            with patch.object(Path, "exists", return_value=False), \
                    patch.object(sys.modules["infra_config"], "CLOUD_HEALTH_DIR", None):
                outputs += [kp.build_section("en"), ab.build_external_integrations("en")]
            with patch.object(ab, "_latest_summary", return_value=None), \
                    patch.object(ab, "_scan_xfails", return_value=[]):
                outputs += [ab.build_test_coverage("en"), ab.build_xfail_list("en")]
            with patch.object(ab, "DOMAINS_INVENTORY", ["empty_domain"]):
                outputs.append(ab.build_domain_signals("en", signals_by_domain={}))
            for output in outputs:
                self.assertNotRegex(output, r"[А-Яа-яЁё]")
            schedule = sc.build_section("en")
            for value in ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "day 4",
                          "every 3h", "every 2min", "every 7s", "on event (WatchPaths)"):
                self.assertIn(value, schedule)
            # Every generated block, including empty blocks, carries the provenance note.
            for output in outputs:
                if "<!-- GEN:" in output or tc.BEGIN in output or "## MODULE REGISTRY" in output:
                    self.assertIn("descriptions are machine-translated from the Russian source", output)

    def test_cli_routes_to_english_and_reports_counts_preserving_ru_and_header(self):
        real_exists = Path.exists
        with inputs() as args, generators(*args) as mods, \
                patch("desc_translation.english", side_effect=memory_miss):
            root = args[0]
            arch = skeleton(mods)
            tc = mods["gen_testing_contracts"]
            testing = "**Версия:** synthetic\n" + tc.BEGIN + "\n" + tc.END + "\nTAIL\n"
            for filename, content in (("ARCH_SNAPSHOT", arch), ("TESTING_CONTRACTS", testing)):
                (root / (filename + ".md")).write_text("RUSSIAN ORIGINAL: do not write\n")
                (root / (filename + ".en.md")).write_text(content)
            with patch.object(Path, "exists", lambda p: real_exists(p) if p.is_relative_to(root) else False), \
                    patch.object(mods["arch_guard"], "build_graph", return_value={"example": ["other"]}):
                for name, count in zip(NAMES, (3, 0, 0, 0, 2, 3)):
                    output = io.StringIO()
                    with self.subTest(generator=name), contextlib.redirect_stdout(output):
                        self.assertEqual(mods[name].main(["--lang", "en"]), 0)
                        self.assertEqual(output.getvalue().splitlines()[-1], f"untranslated: {count}")
                        filename = "TESTING_CONTRACTS.en.md" if name == "gen_testing_contracts" else "ARCH_SNAPSHOT.en.md"
                        before = (root / filename).read_bytes()
                        self.assertEqual(mods[name].main(["--lang", "en"]), 0)
                        self.assertEqual(output.getvalue().splitlines()[-1], f"untranslated: {count}")
                        self.assertEqual((root / filename).read_bytes(), before)
            for filename in ("ARCH_SNAPSHOT", "TESTING_CONTRACTS"):
                self.assertEqual((root / (filename + ".md")).read_text(), "RUSSIAN ORIGINAL: do not write\n")
                text = (root / (filename + ".en.md")).read_text()
                self.assertTrue(text.startswith("**Версия:** synthetic"))
                self.assertTrue(text.endswith("TAIL\n"))
            english = (root / "ARCH_SNAPSHOT.en.md").read_text()
            self.assertEqual(english.count("descriptions are machine-translated"), 9)
            self.assertFalse((root / ".arch_graph.json").exists())

    def test_en_log_starts_empty_and_ru_does_not_consume_en_events(self):
        with inputs() as args, generators(*args) as mods:
            root = args[0]
            ag = mods["arch_guard"]
            for suffix in (".md", ".en.md"):
                (root / ("ARCH_SNAPSHOT" + suffix)).write_text(skeleton(mods))
            original = (root / "ARCH_SNAPSHOT.md").read_text().replace(
                ag._ARCH_LOG_START, ag._ARCH_LOG_START + "\nСтарая русская история")
            (root / "ARCH_SNAPSHOT.md").write_text(original)
            old = {"example": ["alpha"]}
            new = {"example": ["beta"], "added": ["alpha"]}
            with patch.object(ag, "build_graph", return_value=old), contextlib.redirect_stdout(io.StringIO()):
                ag.main(["--lang", "en"])
            doc = root / "ARCH_SNAPSHOT.en.md"
            first = doc.read_text()
            log = first.split(ag._ARCH_LOG_START)[1].split(ag._ARCH_LOG_END)[0]
            self.assertIn("History before ", log)
            self.assertIn("[ARCH_SNAPSHOT.md](ARCH_SNAPSHOT.md)", log)
            self.assertNotIn("- `", log)
            ag.GRAPH_CACHE.write_text(json.dumps(old))
            with patch.object(ag, "build_graph", return_value=new), contextlib.redirect_stdout(io.StringIO()):
                ag.main(["--lang", "ru"])
                ag.main(["--lang", "en"])
                second = doc.read_bytes()
                ag.main(["--lang", "en"])
            self.assertEqual(second, doc.read_bytes())
            text = second.decode()
            self.assertNotIn("Старая русская история", text)
            self.assertIn("new module `added` (depends on: alpha)", text)
            self.assertIn("`example` + dependency: beta", text)
            self.assertIn("`example` − dependency: alpha", text)
            self.assertEqual(text.count("History before "), 1)

    def test_missing_english_document_does_not_consume_log_events(self):
        with inputs() as args, generators(*args) as mods, contextlib.redirect_stdout(io.StringIO()):
            ag = mods["arch_guard"]
            cache = args[0] / ".arch_graph.en.json"
            cache.write_text('{"example": ["old"]}')
            before = cache.read_bytes()
            with patch.object(ag, "build_graph", return_value={"example": ["new"]}):
                self.assertEqual(ag.main(["--lang", "en"]), 2)
            self.assertEqual(cache.read_bytes(), before)

    def test_en_check_detects_corruption_and_does_not_write(self):
        with inputs() as args, generators(*args) as mods, \
                patch("desc_translation.english", side_effect=memory_miss), \
                contextlib.redirect_stdout(io.StringIO()):
            tc = mods["gen_testing_contracts"]
            doc = args[0] / "TESTING_CONTRACTS.en.md"
            doc.write_text(tc.BEGIN + "\n" + tc.END)
            self.assertEqual(tc.main(["--lang", "en"]), 0)
            self.assertEqual(tc.main(["--lang", "en", "--check"]), 0)
            doc.write_text(doc.read_text().replace("Integrity sensor registry", "BROKEN"))
            broken = doc.read_bytes()
            self.assertEqual(tc.main(["--lang", "en", "--check"]), 1)
            self.assertEqual(doc.read_bytes(), broken)

    def test_studio_modules_import_without_config_and_cli_still_rejects_nonprimary(self):
        for name in ("gen_key_paths", "gen_schedule", "gen_arch_blocks"):
            with self.subTest(generator=name), patch.dict(sys.modules, {"infra_config": None, "health_db": None}):
                spec = importlib.util.spec_from_file_location("k5_import_" + name, ROOT / (name + ".py"))
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                with patch.dict(sys.modules, {"infra_config": types.SimpleNamespace(is_primary=lambda _: False)}):
                    with self.assertRaises(SystemExit) as error:
                        mod.main(["--lang", "en"])
                    self.assertIn("§8", str(error.exception))

    def test_ru_golden_oracle_rejects_a_changed_literal_for_each_generator(self):
        expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
        keys = ("heading", "all_modules", "heading", "heading", "coverage", "sensors")
        with inputs() as args, generators(*args) as mods:
            for name, key in zip(NAMES, keys):
                with self.subTest(generator=name), patch.dict(mods[name]._TEXT["ru"], {key: "BROKEN"}):
                    self.assertNotEqual(snapshots(mods, args[0], "ru")[name].encode(), expected[name].encode())


if __name__ == "__main__":
    unittest.main()
