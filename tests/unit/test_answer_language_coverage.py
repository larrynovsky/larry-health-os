"""Язык ответа модели = язык человека (нить model-lang, 28.09).

Карта всех файлов, где код зовёт модель (`messages.create(`): у каждого — класс.
COVERED — в сборке промпта стоит hai_core.answer_language(); VIA_SHARED — промпт берётся из
hai_core.get_system_prompt(); NOT_YET — человек видит текст, но инструкции ещё нет (храповик:
только убывает); INTERNAL — текст читает модель/код/оператор, а не человек. Новый файл с
вызовом модели без класса — красный: генератор не проскочит мимо языка молча.
Инвентарь, откуда классы: plans/LLM_INVENTORY_2026-09-28.md (Codex X3)."""
import re
from pathlib import Path

import pytest

import hai_core
import i18n

ROOT = Path(__file__).resolve().parents[2]

COVERED = {
    "gp_agent.py", "checkin_agent.py", "wellally_consult.py", "hypothesis_consilium_eval.py",
    "cbcr_hypothesis.py", "monthly_consilium.py", "symptom_intake.py", "genome_context.py",
    "genome_update_agent.py", "consult_prep.py", "hai_hypotheses.py", "hai_core.py",
    # волна 2 (смешанные JSON: читаемые поля — на языке человека, ключи/enum — как есть)
    "problems_db.py", "task_agent.py", "literature_curator.py", "survivorship_curator.py",
    "memory_consolidation.py", "import_medical_events.py", "food_rule_generator.py",
    "generate_constitutions.py",   # тело конституции; ревью алертов — оператору, по-русски
}
VIA_SHARED = {"hai_chat.py", "hai_reports.py"}
NOT_YET: set[str] = set()   # храповик пуст с 28.09; новый генератор без языка — сюда, с причиной
OWN_LANGUAGE = {  # язык решён иначе, чем инструкцией в промпте
    "weekly_digest.py",        # русский текст + проверенный перевод text_en (нить digest-lang)
    "desc_translation.py",     # всегда английский: переводит русские пояснения для английских
                               # справочников ARCH/TESTING_CONTRACTS (arch-en-gen, 29.09), не человеку
    "fill_description_ru.py",  # русское производное поле; английский путь читает исходное
                               # название из ClinVar (handlers/genome.py, ветка lang != ru)
}
INTERNAL = {
    "lifestyle_agents.py", "hypothesis_semantic_check.py", "publication_reader.py",
    "lab_extractor.py", "lab_recognizer.py", "lab_schedule_extractor.py",
    "treatment_extractor.py", "doc_triage.py", "handlers/symptom.py", "model_health_check.py",
    "lab_drytest.py", "scripts/replay_beliefs.py", "scripts/replay_staleness.py",
    "doc_agent.py", "night_investigator.py", "test_failure_handler.py",
}


def _callers() -> set[str]:
    out = set()
    for p in ROOT.rglob("*.py"):
        rel = p.relative_to(ROOT).as_posix()
        if rel.startswith(("tests/", ".venv/")) or "/site-packages/" in rel:
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        code = re.sub(r'"""[\s\S]*?"""', "", text)          # вызовы в докстрингах — не вызовы
        if re.search(r"\bmessages\.create\(", code):
            out.add(rel)
    return out


def test_every_model_caller_is_classified():
    known = COVERED | VIA_SHARED | NOT_YET | OWN_LANGUAGE | INTERNAL
    missing = _callers() - known
    assert not missing, f"вызов модели без класса языка: {sorted(missing)} — впиши в карту"


def test_covered_files_really_insert_the_instruction():
    for rel in COVERED:
        assert "answer_language" in (ROOT / rel).read_text(encoding="utf-8"), rel


def test_not_yet_is_a_ratchet():
    done = sorted(r for r in NOT_YET if "answer_language" in (ROOT / r).read_text(encoding="utf-8"))
    assert not done, f"{done} уже вставляют инструкцию — перенеси в COVERED"


WRAPPED = {  # сборщики, через которые идут и повторные (ремонтные) вызовы — декоратор, не вставка
    "gp_agent.py": {"_build_gp_system_prompt", "_build_gp_monthly_prompt", "_build_gp_daily_prompt",
                    "_build_problem_list_reviewer_prompt"},
    "checkin_agent.py": {"_build_checkin_system", "_build_checkin_opener_system"},
}


def test_prompt_builders_carry_the_decorator():
    import ast
    for rel, names in WRAPPED.items():
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        deco = {f.name for f in tree.body if isinstance(f, ast.FunctionDef)
                and any(ast.unparse(d) == "hai_core.with_answer_language" for d in f.decorator_list)}
        assert names <= deco, f"{rel}: без языка ответа {sorted(names - deco)}"


def test_russian_person_gets_byte_identical_prompts():
    assert hai_core.answer_language("ru") == ""


def test_english_person_gets_an_overriding_instruction():
    s = hai_core.answer_language("en")
    assert s.startswith("\n\n") and "English" in s and "overrides" in s


@pytest.fixture
def english(monkeypatch):
    monkeypatch.setattr(i18n, "lang_of", lambda profile=None: "en")


def test_shared_chat_prompt_ends_with_instruction(english, monkeypatch):
    monkeypatch.setattr(hai_core, "_build_system_prompt", lambda: "BASE")
    assert hai_core.get_system_prompt() == "BASE" + hai_core.answer_language("en")


def test_checkin_builders_are_wrapped(english, monkeypatch):
    import checkin_agent
    monkeypatch.setattr(checkin_agent, "_get_checkin_patient_desc", lambda: "P")
    monkeypatch.setattr(checkin_agent, "_data_truth_note", lambda: "")
    for build in (checkin_agent._build_checkin_system, checkin_agent._build_checkin_opener_system):
        assert build().endswith(hai_core.answer_language("en"))


def test_symptom_payload_ends_with_instruction(english, monkeypatch):
    import symptom_intake
    monkeypatch.setattr(symptom_intake, "_load_system_prompt", lambda: ("BASE", "v1"))
    system, _ = symptom_intake.build_elicitation_payload(symptom_intake.new_state(1), "hi")
    assert system == "BASE" + hai_core.answer_language("en")


def test_constitution_structure_follows_the_person_language(english):
    import generate_constitutions as gc
    assert gc._heading("sleep") == "# Constitution: Sleep"
    sec = gc._changed_head_first("## Что изменилось\n\n- shorter wind-down")
    assert sec == "## What changed\n\n- shorter wind-down"


def test_constitution_structure_is_unchanged_for_russian(monkeypatch):
    import generate_constitutions as gc
    monkeypatch.setattr(i18n, "lang_of", lambda profile=None: "ru")
    assert gc._heading("sleep") == "# Конституция: Сон"
    assert gc._changed_head_first("## Что изменилось\n\n- x") == "## Что изменилось\n\n- x"
    assert set(gc._CHANGED_HEADS) >= {"## Что изменилось", "## What changed"}
