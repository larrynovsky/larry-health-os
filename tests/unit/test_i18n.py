"""Оракул слоя переводов и выбора языка (нить i18n, 28.09).

Решение владельца 28.09: выбор языка — первым вопросом знакомства. Ключевые моменты плана:
дефолт — русский (владелец и партнёр знакомство на английском не проходили), пропуск перевода
виден, а не тих, английское знакомство не показывает русского текста.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from string import Formatter

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
CYR = re.compile(r"[А-Яа-яЁё]")


def _yaml(lang):
    import yaml
    return yaml.safe_load((ROOT / "methodology" / "i18n" / f"{lang}.yaml").read_text(encoding="utf-8"))


def test_английский_покрывает_все_ключи_русского():
    ru, en = _yaml("ru"), _yaml("en")
    assert set(ru) == set(en), f"ключи разошлись: {sorted(set(ru) ^ set(en))}"
    for k in ru:
        ph_ru = {(field, spec, conversion) for _, field, spec, conversion in Formatter().parse(ru[k]) if field}
        ph_en = {(field, spec, conversion) for _, field, spec, conversion in Formatter().parse(en[k]) if field}
        assert ph_ru == ph_en, f"{k}: подстановки {ph_ru} ≠ {ph_en}"
        assert not CYR.search(en[k]), f"{k}: кириллица в английском"


def test_язык_по_умолчанию_русский():
    import i18n
    assert i18n.lang_of({}) == "ru"
    assert i18n.lang_of({"identity.language": "de"}) == "ru"
    assert i18n.lang_of({"identity.language": "en"}) == "en"


@pytest.mark.parametrize("n,expected", [(0, "0 дней"), (1, "1 день"), (2, "2 дня"),
    (3, "3 дня"), (5, "5 дней"), (11, "11 дней"), (14, "14 дней"),
    (21, "21 день"), (31, "31 день"), (112, "112 дней")])
def test_person_count_forms(n, expected):
    from _fmt_helpers import fmt_count
    assert fmt_count(n, "days", "ru") == expected
    assert fmt_count(2, "hypotheses", "ru") == "2 гипотезы"
    assert fmt_count(4, "nights", "ru") == "4 ночи"
    assert fmt_count(21, "days", "en") == "21 days"
    assert fmt_count(1, "days", "en") == "1 day"


def test_person_labels_explain_codes_and_do_not_invent_unknown_status():
    from _fmt_helpers import fmt_label
    assert fmt_label("active_monitoring", "problems.status", "ru") == "нужны регулярные проверки — учитываю в каждом недельном разборе"
    assert fmt_label("watchful_waiting", "problems.status", "ru") == "жду изменений — учитываю в каждом недельном разборе"
    assert fmt_label("invented_unknown", "hypotheses.status", "ru") == "статус не указан"
    assert fmt_label("past_week", "assessment.period", "ru") == "за последнюю неделю"


def test_person_copy_has_no_internal_vocabulary_or_formal_address():
    forbidden = re.compile(r"арбитр|МДТ|пропозал|active_monitoring|watchful_waiting|aliases|pending review|"
                           r"\bAPI\b|recall:|onboarding|\bdeep\b|\bscore\b|conf=|\bpartial\b|in-sample|"
                           r"\beval\b|Phase H|correlation_drift|\bGP\b|\bPRS\b|\bSNP\b|ClinVar|rsid|конституци|\bisi\b|Healz\.ai|кейс|"
                           r"task_id|\bID\b|реплаем|Ответьте|Обновите|вашим", re.I)
    for key, value in _yaml("ru").items():
        # dashboard.constitution.* — заголовки документа дашборда, который сам зовёт эти
        # документы «Конституции» (dashboard_templates/constitutions_index.html); в боте слова нет.
        # health_db.reason.* (K1, 29.09) — русский текст здесь ещё и КЛЮЧ сопоставления с уже
        # засеянными строками БД (health_db._translated_reason_templates): переформулировать его
        # можно только вместе с миграцией строк БД, иначе английский человек молча получит русское.
        # Лексика «Sleep score» в русском тексте — прежняя, не новая; долг назван в коммите нити cx-ru-batch1.
        if key.startswith(("owner.", "documents.operator.", "dashboard.constitution.", "health_db.reason.")):
            continue
        visible = re.sub(r"\{[^}]*\}", "", value)
        assert not forbidden.search(visible), (key, visible)
    assert "отвечу после" not in _yaml("ru")["onboarding.reply.question_deferred"]
    for lang, old_tone in (("ru", "без ответа"), ("en", "without a reply")):
        strings = _yaml(lang)
        assert not {k for k, v in strings.items() if old_tone in v.casefold()}
        consult_name, check_name, weekly_name = (
            ("консилиум", "проверка гипотезы", "еженедельный разбор") if lang == "ru"
            else ("AI consultation", "hypothesis check", "weekly review"))
        for key in ("consult.reply.starting", "consult.reply.report_heading",
                    "actions.consult.new", "actions.consult.end", "consult.reply.timed_out"):
            assert consult_name.casefold() in strings[key].casefold(), (lang, key)
        for key in ("hypotheses.reply.consilium_report", "jobs.hypotheses.checking_one",
                    "jobs.hypotheses.report", "jobs.outcomes.report"):
            assert check_name.casefold() in strings[key].casefold(), (lang, key)
        for key in ("jobs.weekly.heading", "reports.reply.weekly_heading"):
            assert weekly_name.casefold() in strings[key].casefold(), (lang, key)


def test_help_covers_registered_entrypoints_without_restoring_action_commands():
    import ast
    registered = set()
    for path in (ROOT / "handlers").glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "CommandHandler" and node.args
                    and isinstance(node.args[0], ast.Constant)):
                registered.add(node.args[0].value)
    buttons = {"stop", "end", "confirm", "hreject", "retire", "hyp", "eval_hypothesis",
               "done", "dismiss", "approve", "reject"}
    for lang in ("ru", "en"):
        help_text = _yaml(lang)["common.help.commands"].replace("\\_", "_")
        listed = set(re.findall(r"^/(\w+)", help_text, re.M))
        assert listed == registered - buttons
        assert not listed & buttons
        assert "08:00" not in help_text


def test_пропуск_перевода_виден_а_опечатка_падает(monkeypatch, caplog):
    import i18n
    monkeypatch.setattr(i18n, "_table", lambda lang: {"a.b": "Привет {x}"} if lang == "ru" else {})
    assert i18n.t("a.b", "en", x=1) == "Привет 1"
    assert "нет перевода a.b" in caplog.text, "русский показан вместо перевода молча"
    with pytest.raises(KeyError):
        i18n.t("нет.такого", "ru")


def test_опросник_знакомства_двуязычен():
    ins = json.loads((ROOT / "methodology/instruments/onboarding.json").read_text(encoding="utf-8"))
    first = ins["items"][0]
    assert first["field"] == "identity.language", "язык — первым вопросом (решение владельца 28.09)"
    for it in ins["items"][1:]:
        assert set(it["text"]) == {"ru", "en"}, it["id"]
        assert not CYR.search(it["text"]["en"]), it["id"]
        for o in it.get("options") or []:
            if isinstance(o["label"], dict):
                assert not CYR.search(o["label"]["en"]), (it["id"], o)


def _real(db, monkeypatch, tmp_path):
    import assessment_bot_handlers as abh
    import assessment_dialog as ad
    import secrets_paths
    empty = tmp_path / "secrets_empty"
    empty.mkdir()
    monkeypatch.setattr(secrets_paths, "secrets_dir", lambda: empty)
    monkeypatch.setattr(ad, "INSTRUMENTS_DIR", tmp_path / "tenant_instruments")
    return ad, abh.start_onboarding(7)


def test_английское_знакомство_без_русского_текста(db, monkeypatch, tmp_path):
    ad, (text, kb, sid) = _real(db, monkeypatch, tmp_path)
    assert "Language" in text and [b[0][0] for b in kb] == ["Русский", "English"]
    said = []
    reply, kb, _ = ad.answer(sid, "language", 1)
    said.append(reply)
    for iid, v in (("name", "Tester"), ("birth_date", "1975-01-01"), ("sex", 0), ("height", "178"),
                   ("weight", -1), ("home", -1), ("brief_time", 1), ("health", "no"),
                   ("allergies", "no"), ("meds", "no"), ("smoking", 0), ("fasting", 1), ("sources", 3)):
        reply, kb, done = ad.answer(sid, iid, v)
        said.append(reply)
        said += [b[0][0] for b in kb or []]
    assert done
    ru_left = [s for s in said if CYR.search(s)]
    assert not ru_left, f"в английском знакомстве русский текст: {ru_left[:3]}"
    assert "what I now know" in said[-1]


def test_без_выбора_языка_всё_по_русски(db, monkeypatch, tmp_path):
    """Владелец и партнёр поля языка не имеют — их знакомство (/about) остаётся русским."""
    ad, (text, kb, sid) = _real(db, monkeypatch, tmp_path)
    reply, _, _ = ad.answer(sid, "language", 0)
    assert "Записал: Русский" in reply and "Как к тебе обращаться?" in reply


def test_промах_закрытого_словаря_уходит_в_журнал_сбоев(monkeypatch):
    """fmt_label: неизвестный статус человек видит как «неизвестно» — без журнала
    расхождение словаря и данных молчало бы. Где словарь заведомо неполон — тихо."""
    import notify
    from _fmt_helpers import fmt_label
    faults = []
    monkeypatch.setattr(notify, "fault", lambda tech, person_key=None, **kw: faults.append(tech))
    assert fmt_label("active", "problems.status", "ru") == "требует внимания"
    assert faults == []
    fmt_label("invented_status", "problems.status", "ru")
    assert faults and "problems.status.invented_status" in faults[0]
    faults.clear()
    fmt_label("Invented condition", "genome.condition", "ru", unknown_expected=True)
    fmt_label("", "problems.status", "ru")
    assert faults == []
