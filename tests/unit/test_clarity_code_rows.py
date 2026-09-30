"""X7: real callers and translations, invented data, no live services or DB.

Run by the reviewer; this task deliberately does not execute tests.
"""
import asyncio
import sys
from datetime import date
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import i18n


def _load_caller(relative_path):
    """Execute the whole caller module, blocking service imports before they run."""
    path = Path(__file__).resolve().parents[2] / relative_path
    module = ModuleType(path.stem)
    module.__file__ = str(path)
    services = {name: Mock(name=name) for name in (
        "health_db", "gp_agent", "notify", "llm_client", "hai_core", "requests",
        "telegram", "telegram.ext", "bot.utils",
    )}
    with patch.dict(sys.modules, services), patch.object(sys, "path", sys.path[:]):
        exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), module.__dict__)
    return module


def _labs_text(lang, fields):
    reports = _load_caller("handlers/reports.py")
    # Entirely synthetic rows; CEA selects the existing key-result branch.
    key = {"test_name": "CEA", "date": "2000-01-01", "value": 0.0, **fields}
    other = {"test_name": "DEMO_OTHER", "date": "2000-01-02", "value": 0.0, **fields}
    reports.db.get_recent_labs.side_effect = [[key], [other]]
    reports.db.get_lab_refs.return_value = {}
    reports.get_today = Mock(return_value=date(2000, 1, 2))
    reports.send_md = AsyncMock()
    update = SimpleNamespace(message=SimpleNamespace(reply_text=AsyncMock()))
    with patch.object(i18n, "lang_of", return_value=lang):
        asyncio.run(reports.cmd_labs(update, None))
    reports.send_md.assert_awaited_once()
    return reports.send_md.await_args.kwargs["text"].splitlines()


# No conversion, inferred unit, trailing space, or change to already-rendered units.
LAB_CASES = (
    ({"unit": "demo/u", "ref_low": 1.0, "ref_high": 2.0}, "0.0 demo/u ⚠"),
    ({"unit": "%"}, "0.0 %"),
    ({"unit": None}, "0.0"),
    ({"unit": ""}, "0.0"),
    ({}, "0.0"),
    ({"value": "0.0 demo/u"}, "0.0 demo/u"),
)


def test_key_result_includes_the_row_unit_ru_and_en():
    # Old handlers/reports.py:148 passed v=v, assigned only lab.get('value') at :143.
    # The first case therefore lost demo/u despite the unit field being populated.
    for lang in ("ru", "en"):
        for fields, value in LAB_CASES:
            lines = _labs_text(lang, fields)
            actual = next(line for line in lines if line.startswith("`CEA "))
            expected = f"`{'CEA':16}` {value} _2000-01-01_"
            assert actual.encode("utf-8") == expected.encode("utf-8"), (lang, fields)


def test_other_result_includes_the_row_unit_ru_and_en():
    # Old handlers/reports.py:160 passed v=v, assigned only lab.get('value') at :157.
    # The first case therefore lost demo/u despite the unit field being populated.
    for lang in ("ru", "en"):
        for fields, value in LAB_CASES:
            lines = _labs_text(lang, fields)
            actual = next(line for line in lines if line.startswith("`DEMO_OTHER "))
            expected = f"`{'DEMO_OTHER':16}` {value}"
            assert actual.encode("utf-8") == expected.encode("utf-8"), (lang, fields)


def test_changed_variant_localizes_both_values_ru_and_en():
    # Old genome_update_agent.py:246 passed before=c['old_sig'], after=c['new_sig'].
    # The first case therefore printed Benign -> drug response in both languages.
    # Подписи берутся из словаря, а не литералом: их формулировку меняет владелец
    # (нить понятности 29.09), а тест судит подстановку, не текст.
    keys = ("genome.significance.benign", "genome.significance.drug_response",
            "genome.significance.unknown", "genome.significance.pathogenic")
    labels = {lang: tuple(i18n.t(k, lang) for k in keys) for lang in ("ru", "en")}
    assert labels["ru"] != labels["en"] and all(labels["ru"]) and all(labels["en"])
    for lang, (benign, drug, unknown, pathogenic) in labels.items():
        for old, new, before, after in (
            ("Benign", "drug response", benign, drug),
            ("Benign", "demo_unlisted", benign, unknown),
            ("demo_unlisted", "Pathogenic", unknown, pathogenic),
        ):
            agent = _load_caller("genome_update_agent.py")
            change = {"rsid": "rs_DEMO_ONLY", "gene": "DEMO_GENE",
                      "old_sig": old, "new_sig": new, "conditions": []}
            agent.db.get_significant_variants.return_value = [{"rsid": "rs_DEMO_ONLY"}]
            agent.db.get_conn.return_value.execute.return_value.fetchone.return_value = {
                "genotype": "DEMO", "conditions": "[]", "domain_tags": "[]",
            }
            agent.db.get_snps_batch.return_value = {}
            agent.fetch_clinvar_updates = Mock(return_value=[change])
            agent.generate_genome_narrative = Mock(side_effect=RuntimeError("synthetic failure"))
            agent.get_today = Mock(return_value=date(2000, 1, 2))
            with patch.object(i18n, "lang_of", return_value=lang):
                result = agent.run_monthly_update()
            expected = i18n.t("genome.reply.changed_variant", lang,
                              variant="rs_DEMO_ONLY", before=before, after=after)
            assert expected in result["narrative"], (lang, old, new)
            # Localize presentation only; persistence keeps the reference codes.
            assert agent.db.upsert_genetic_variant.call_args.args[1]["significance"] == new
            assert agent.db.save_genome_update_log.call_args.args[0]["changes"] == [change]

