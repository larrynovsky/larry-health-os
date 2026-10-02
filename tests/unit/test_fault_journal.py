"""Routing oracle: execute production functions with all IO replaced, never import the bot.

Can also run with unittest to avoid the repository's database-initializing pytest conftest.
The strings and IDs below are invented; no tenant files or credentials are read.
"""
from __future__ import annotations

import ast
import asyncio
import json
import logging
import re
import sys
import unittest
from datetime import date, datetime, timedelta, timezone
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import i18n

ROOT = Path(__file__).resolve().parents[2]
LOG = logging.getLogger(__name__)


def _load(file, names, **env):
    """Load selected definitions, bypassing production imports and startup IO."""
    tree = ast.parse((ROOT / file).read_text(encoding="utf-8"))
    tree.body = [n for n in tree.body if getattr(n, "name", None) in names
                 or isinstance(n, ast.ImportFrom) and n.module == "__future__"
                 or isinstance(n, ast.Assign) and any(
                     isinstance(t, ast.Name) and t.id in names for t in n.targets)]
    exec(compile(tree, file, "exec"), env)
    return SimpleNamespace(**env)


# fault и то, на чём он стоит (нить first-contact, 02.10: выбор текста по живости ремонта).
_FAULT_NAMES = {"fault", "_faults_journal", "repairer_alive", "REPAIR_SEEN", "REPAIR_FRESH_S"}


def _notify(operator):
    return _load("notify.py", _FAULT_NAMES, Path=Path, os=os, __file__=str(ROOT / "notify.py"),
                 secrets_dir=lambda: Path("invented-tenant"), notify_operator=operator)


class FaultRoutingTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.journal = Path(temp.name) / "nested" / "faults.jsonl"
        # Установка с живым ночным ремонтом: человеку — «передал на починку». Без отметки —
        # другой текст, его судит tests/unit/test_first_contact.py.
        self.journal.parent.mkdir(parents=True, exist_ok=True)
        (self.journal.parent / "night_repair_seen").write_text(str(int(datetime.now().timestamp())))
        env = patch.dict(os.environ, HEALTH_FAULTS_JOURNAL=str(self.journal))
        env.start()
        self.addCleanup(env.stop)

    def records(self):
        return [json.loads(line) for line in self.journal.read_text().splitlines()]

    def test_fault_appends_one_record_and_preserves_person_return(self):
        operator = Mock()
        notify = _notify(operator)
        for tech, where in (("handlers/messages.py:handle_text: FAKE_529\nnext", "handlers/messages.py:handle_text"),
                            ("worker.run: " + "я" * 600, "worker.run"),
                            ("worker.py:run", "worker.py:run"), ("plain", "plain")):
            person = notify.fault(tech, lang="ru")
            self.assertEqual(person, i18n.t("common.error.our_side", "ru"))
            self.assertNotIn("FAKE_529", person)
            record = self.records()[-1]
            self.assertEqual(set(record), {"ts", "code", "tenant", "where", "text"})
            self.assertRegex(record["code"], r"^[0-9a-f]{6}$")   # код сбоя для человека (first-contact)
            self.assertEqual(record["where"], where)
            self.assertEqual(record["text"], tech[:500])
            self.assertEqual(record["tenant"], "invented-tenant")
            self.assertIsNotNone(datetime.fromisoformat(record["ts"]).utcoffset())
        self.assertIsNone(notify.fault("worker: background", person_key=None))
        self.assertEqual(len(self.records()), 5)
        operator.assert_not_called()

    def test_default_journal_path_is_relative_to_repo(self):
        root = self.journal.parent
        notify = _load("notify.py", _FAULT_NAMES, Path=Path, os=os,
                       __file__=str(root / "notify.py"), secrets_dir=lambda: Path("invented-tenant"))
        with patch.dict(os.environ):
            os.environ.pop("HEALTH_FAULTS_JOURNAL", None)
            notify.fault("worker: default", person_key=None)
        self.assertEqual(json.loads((root / "logs" / "faults.jsonl").read_text())["where"], "worker")

    def test_failed_journal_write_warns_and_keeps_person_line(self):
        self.journal.mkdir()  # каталог вместо файла
        operator = Mock()
        with self.assertLogs(level="WARNING") as logs:
            person = _notify(operator).fault("worker: FAKE_529", lang="ru")
        self.assertEqual(person, i18n.t("common.error.our_side", "ru"))
        self.assertIn("Журнал сбоев не записан", str(logs.output))
        operator.assert_not_called()

    def test_journal_rotation_keeps_last_2000_complete_lines(self):
        self.journal.parent.mkdir(parents=True, exist_ok=True)
        records = [{"ts": "2000-01-04T12:00:00+00:00", "tenant": "invented-tenant",
                    "where": "worker", "text": f"{i}: " + "x" * 400} for i in range(2300)]
        self.journal.write_text("".join(json.dumps(r) + "\n" for r in records))
        self.assertGreater(self.journal.stat().st_size, 1024 * 1024)
        _notify(Mock()).fault("worker: newest", person_key=None)
        actual = self.records()
        self.assertEqual(len(actual), 2000)
        self.assertEqual(actual[:-1], records[-1999:])
        self.assertEqual(actual[-1]["text"], "worker: newest")

    def integrity(self):
        warnings = []
        monitor = _load("integrity_tests.py", {"check_fault_journal"},
                        Path=Path, json=json, __file__=str(ROOT / "integrity_tests.py"),
                        get_now=lambda: datetime(2000, 1, 4, 12, tzinfo=timezone.utc),
                        warn=lambda title, detail: warnings.append((title, detail)))
        return monitor.check_fault_journal(), warnings

    def test_integrity_groups_by_tenant_and_where_with_24_hour_boundary(self):
        self.journal.parent.mkdir(parents=True, exist_ok=True)
        rows = [
            ("2000-01-04T11:00:00+00:00", "invented-a", "worker", "latest " + "x" * 210),
            ("2000-01-03T12:00:00+00:00", "invented-a", "worker", "boundary"),
            ("2000-01-04T13:00:00+03:00", "invented-a", "worker", "older out of order"),
            ("2000-01-03T11:59:59+00:00", "invented-a", "worker", "expired"),
            ("2000-01-04T12:00:01+00:00", "invented-a", "worker", "future"),
            ("2000-01-04T11:00:00+00:00", "invented-b", "worker", "other tenant"),
            ("2000-01-04T11:00:00+00:00", "invented-a", "other", "other source"),
        ]
        self.journal.write_text("".join(json.dumps(dict(zip(("ts", "tenant", "where", "text"), r)))
                                        + "\n" for r in rows))
        result, warnings = self.integrity()
        self.assertEqual(result, {"groups": 3, "faults": 5})
        self.assertCountEqual(warnings, [
            ("сбой: worker", "3 раз за сутки у invented-a; последний: " + rows[0][3][:200]),
            ("сбой: worker", "1 раз за сутки у invented-b; последний: other tenant"),
            ("сбой: other", "1 раз за сутки у invented-a; последний: other source"),
        ])

    def test_integrity_missing_empty_and_expired_journal_pass(self):
        self.assertEqual(self.integrity(), ({"groups": 0, "faults": 0}, []))
        self.journal.parent.mkdir(parents=True, exist_ok=True)
        self.journal.write_text("")
        self.assertEqual(self.integrity(), ({"groups": 0, "faults": 0}, []))
        self.journal.write_text(json.dumps({"ts": "1999-01-01T00:00:00+00:00",
                                           "tenant": "invented", "where": "worker", "text": "old"}) + "\n")
        self.assertEqual(self.integrity(), ({"groups": 0, "faults": 0}, []))

    def test_integrity_unreadable_or_corrupt_journal_warns(self):
        self.journal.mkdir(parents=True, exist_ok=True)
        _, warnings = self.integrity()
        self.assertEqual(len(warnings), 1)
        self.assertIn(str(self.journal), warnings[0][1])
        self.journal.rmdir()
        self.journal.write_text('broken\n[]\n{"ts": "bad"}\n' + json.dumps(
            {"ts": "2000-01-04T11:00:00+00:00", "tenant": "invented", "where": "worker", "text": "valid"}) + "\n")
        result, warnings = self.integrity()
        self.assertEqual(result, {"groups": 1, "faults": 1})
        self.assertEqual(len(warnings), 2)
        self.assertIn("повреждённых строк: 3", warnings[0][1])
        self.assertEqual(warnings[1][0], "сбой: worker")

    def test_integrity_registers_the_check(self):
        tree = ast.parse((ROOT / "integrity_tests.py").read_text())
        calls = [n for n in tree.body if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
                 and isinstance(n.value.func, ast.Name) and n.value.func.id == "check"
                 and len(n.value.args) >= 2 and isinstance(n.value.args[1], ast.Name)
                 and n.value.args[1].id == "check_fault_journal"]
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].value.args[0].value, "Сбои бота и служб за сутки")

    def test_fault_reaches_the_night_cycle_through_integrity_artifact(self):
        from _time_inject import set_test_clock, clear_test_clock
        set_test_clock("2000-01-04T11:00:00+00:00")
        try:
            _notify(Mock()).fault("worker: FAKE_529", person_key=None)
        finally:
            clear_test_clock()
        _, warnings = self.integrity()
        artifact = self.journal.with_name("integrity_latest.json")
        artifact.write_text(json.dumps({"warnings": warnings, "failures": []}))
        import finding_identity
        night = _load("night_cycle.py", {"_load_warnings", "_slug"}, json=json,
                      _integrity_path=lambda: artifact, finding_identity=finding_identity)
        findings = night._load_warnings()
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0][1], "сбой: worker")
        self.assertIn("1 раз за сутки у invented-tenant; последний: worker: FAKE_529", findings[0][2])

    def test_weekly_failure_separates_journal_and_person(self):
        for language in ("ru", "en"):
            with self.subTest(language=language), patch.object(i18n, "lang_of", return_value=language):
                operator = Mock(return_value="telegram")
                send = AsyncMock()
                scheduled = _load(
                    "jobs/scheduled.py", {"send_weekly_report"},
                    asyncio=asyncio, i18n=i18n, notify=_notify(operator), log=LOG,
                    get_chat_id=lambda: 123, get_today=lambda: date(2000, 1, 4),
                    refresh_data=Mock(), timedelta=timedelta, send_long=send,
                    gp=SimpleNamespace(generate_weekly_report=Mock(
                        side_effect=RuntimeError("FAKE_529 overloaded"))),
                    hai=SimpleNamespace(get_active_protocols=lambda: []))
                context = SimpleNamespace(bot=SimpleNamespace(send_chat_action=AsyncMock()))
                asyncio.run(scheduled.send_weekly_report(context))
                operator.assert_not_called()
                self.assertIn("RuntimeError: FAKE_529 overloaded", self.records()[-1]["text"])
                self.assertEqual(self.records()[-1]["where"], "scheduled.send_weekly_report")
                send.assert_awaited_once()
                person = send.call_args.args[2]
                self.assertEqual(person, i18n.t("jobs.weekly.failed", language))
                self.assertNotIn("FAKE_529", person)
                self.assertNotIn("RuntimeError", person)

    def test_triage_only_sends_decisions_to_the_person(self):
        for clinical in (False, True):
            with self.subTest(clinical=clinical), patch.object(i18n, "lang_of", return_value="ru"):
                operator = Mock(return_value="telegram")
                person = Mock(return_value="telegram")
                root = MagicMock()
                root.__truediv__.return_value.__truediv__.return_value.exists.return_value = False
                warnings = [["invented technical warning", "FAKE_PRIVATE_CONTENT"]]
                if clinical:
                    warnings.append(["анализы требуют обновления", "тестовый вопрос"])
                triage = _load(
                    "triage_agent.py", {"run_triage", "classify_warnings", "split_by_cadence",
                                        "warn_class", "MUTE_WARN_SUBSTRINGS", "person_questions",
                                        "_logs_dir"},
                    i18n=i18n, notify=_notify(operator), re=re, SCRIPT_DIR=root,
                    DIGEST_WEEKDAY=0, get_today=lambda: date(2000, 1, 4),
                    finding_identity=SimpleNamespace(class_of_warning=lambda *_: "decide"),
                    latest_verdict=lambda **kw: ({"failures": [["invented check", "FAKE_FAILURE"]],
                                                 "warnings": warnings}, ""),
                    _log=Mock(), _write_run_receipt=Mock(), _own_tag=lambda: "health",
                    _ask=lambda qs, log=None: (person("\n".join(q["content"] for q in qs)) and len(qs)) if qs else 0)
                result = triage.run_triage()
                operator.assert_not_called()
                self.assertFalse(self.journal.exists())
                self.assertEqual(person.call_count, int(clinical))
                self.assertEqual(len(result["needs_user"]), int(clinical))
                sent = str(person.call_args_list)
                self.assertNotIn("Провал проверки", sent)
                self.assertNotIn("FAKE_FAILURE", sent)
                self.assertNotIn("invented technical warning", sent)
                if clinical:
                    self.assertIn("Когда планируешь", sent)  # вопрос ушёл в дом вопросов (_ask), не в Telegram

    def test_stale_biometrics_names_only_stale_sources(self):
        for sources in (("oura",), ("apple_health",), ("oura", "apple_health")):
            with self.subTest(sources=sources), patch.object(i18n, "lang_of", return_value="ru"):
                operator, person = Mock(), Mock()
                stale = {source: {"age_hours": 60, "limit_hours": 26,
                                  "message": "FAKE: лимит 26 ч, проверь токен"}
                         for source in sources}
                sensor = _load(
                    "oura_freshness_check.py", {"run_check"},
                    ALERT_MULTIPLIER=2.0, BIOMETRIC_SOURCES=["oura", "apple_health"],
                    i18n=i18n, log=LOG, notifications=_notify(operator), _alert=person,
                    db=SimpleNamespace(check_data_freshness=lambda _: stale))
                self.assertEqual(sensor.run_check(notify=True), stale)
                operator.assert_not_called()
                person.assert_called_once()
                text = person.call_args.args[0]
                self.assertNotIn("лимит", text.lower())
                self.assertNotIn("токен", text.lower())
                self.assertIn("токен", self.records()[-1]["text"])
                self.assertEqual(self.records()[-1]["where"], "oura_freshness_check")
                self.assertEqual("Oura" in text, "oura" in sources)
                self.assertEqual("Здоровье" in text, "apple_health" in sources)
                self.assertIn("60", text)

    def test_filename_hides_only_inbox_suffixes(self):
        helper = _load("link_fetch.py", {"display_filename"}, re=re, Path=Path)
        for suffix in ("0123456789abcdef", "0123456789ab"):
            self.assertEqual(helper.display_filename(f"sample__{suffix}.pdf"), "sample.pdf")
        self.assertEqual(helper.display_filename("sample__notes.pdf"), "sample__notes.pdf")
        self.assertEqual(helper.display_filename("sample__abc.pdf"), "sample__abc.pdf")

    def test_link_known_reason_is_human_unknown_reason_is_private(self):
        for known in (False, True):
            with self.subTest(known=known), patch.object(i18n, "lang_of", return_value="en"):
                operator, person = Mock(), Mock()
                request = SimpleNamespace(
                    name="link_invented.request.json", write_text=Mock(),
                    read_text=lambda **kw: json.dumps({"kind": "url", "url": "invented", "status": "pending"}))
                inbox = SimpleNamespace(is_dir=lambda: True, glob=lambda _: [request])
                fetch = Mock()
                link = _load("link_fetch.py", {"process_requests", "_HumanError"},
                             json=json, Path=lambda p: p, REQ_SUFFIX=".request.json",
                             i18n=i18n, notify=_notify(operator), _tell=person, _fetch=fetch,
                             PROVIDERS_TEXT="intake.link.providers")
                fetch.side_effect = (link._HumanError(i18n.t("intake.link.page_instead", "en"))
                                     if known else ValueError("FAKE_INTERNAL_PARSER_FAILURE"))
                self.assertEqual(link.process_requests(inbox), 1)
                operator.assert_not_called()
                person.assert_called_once()
                self.assertEqual(self.records()[-1]["where"], "link_fetch.process_requests request_id=link_invented")
                self.assertNotIn("FAKE_INTERNAL_PARSER_FAILURE", person.call_args.args[0])
                if known:
                    self.assertIn(i18n.t("intake.link.page_instead", "en"), person.call_args.args[0])
                else:
                    self.assertEqual(person.call_args.args[0], i18n.t("intake.link.retry_later", "en"))
                    self.assertIn("FAKE_INTERNAL_PARSER_FAILURE", self.records()[-1]["text"])
                self.assertEqual(json.loads(request.write_text.call_args.args[0])["status"], "failed")

    def test_background_resolution_does_not_publish_reasoning(self):
        for key in (None, "common.error.our_side"):
            with self.subTest(key=key), patch.object(i18n, "lang_of", return_value="en"):
                operator = Mock()
                resolution = _load("hypothesis_resolution.py", {"resolve_hypothesis"},
                                   i18n=i18n, notify=_notify(operator))
                result = resolution.resolve_hypothesis(
                    123, {"verdict": "error", "reasoning": "FAKE_PRIVATE_CONTENT"}, person_key=key)
                self.assertEqual(result["message"], i18n.t(key, "en") if key else None)
                operator.assert_not_called()
                self.assertIn("memory_id=123", self.records()[-1]["text"])
                self.assertEqual(self.records()[-1]["where"], "hypothesis_resolution")
                self.assertNotIn("FAKE_PRIVATE_CONTENT", self.records()[-1]["text"])

    def test_report_section_failures_notify_once_and_keep_placeholders(self):
        operator = Mock()
        create = Mock(return_value=SimpleNamespace(content=[SimpleNamespace(text="invented report")]))
        database = SimpleNamespace(
            init_db=Mock(), get_last_consultation=lambda **kw: None,
            get_profile_context=lambda: {},
            render_all_metrics=Mock(side_effect=RuntimeError("FAKE_VITALS_FAILURE")),
            get_recent_labs=Mock(side_effect=RuntimeError("FAKE_LABS_FAILURE")))
        prep = _load(
            "consult_prep.py", {"prepare_visit_report", "_build_vitals_block", "_build_labs_block", "_fmt_date"},
            db=database, date=date, timedelta=timedelta, get_today=lambda: date(2000, 1, 4), log=LOG,
            _pc_frame=lambda _: [], _pc_unset=lambda _: True,
            build_genetic_context_block=lambda **kw: "", _build_specialist_questions_block=lambda: "",
            _get_client=lambda: SimpleNamespace(messages=SimpleNamespace(create=create)),
            hai_core=SimpleNamespace(get_model=lambda _: "invented-model", answer_language=lambda *a: ""), REPORTS_DIR=MagicMock(),
            # K8 (29.09): заголовок отчёта — из словаря; словарь тут не предмет суда
            i18n=SimpleNamespace(t=lambda key, *a, **kw: key))
        with patch.dict(sys.modules, {
            "notify": _notify(operator),
            "treatment_summary": SimpleNamespace(treatment_text=lambda **kw: ""),
            "patient_context": SimpleNamespace(reasoning_block=lambda: ""),
        }):
            self.assertEqual(prep.prepare_visit_report("2000-01-04", "invented-specialty"), "invented report")
        operator.assert_not_called()
        self.assertEqual(self.records()[-1]["where"], "consult_prep.prepare_visit_report")
        self.assertIn("FAKE_VITALS_FAILURE", self.records()[-1]["text"])
        self.assertIn("FAKE_LABS_FAILURE", self.records()[-1]["text"])
        prompt = create.call_args.kwargs["messages"][0]["content"]
        self.assertIn("Не удалось получить витальные.", prompt)
        self.assertIn("Не удалось получить анализы.", prompt)


if __name__ == "__main__":
    unittest.main()
