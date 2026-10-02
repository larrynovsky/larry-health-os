"""
tests/unit/test_patient_context_contract.py — fitness function (C-2, 2026-07-05).

Контракт: КАЖДЫЙ LLM-потребитель, рассуждающий о пациенте, обязан тянуть память
через единый источник patient_context. Дрейф (новый консилиум/отчёт без памяти)
становится структурно невозможным — тест валит суит (hard FAIL, решение владельца).

Способ 2 (точность до функции): для многофункциональных модулей (gp_agent: дневной/
недельный/месячный) проверяем КАЖДУЮ функцию отдельно — ловит «дневной читает, а
недельный слеп». Для остальных — файловый backstop (ловит новый непокрытый модуль).
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]

# Санкционированные способы дотянуться до памяти пациента (провайдеры контекста)
PROVIDERS = (
    "patient_context", "build_patient_brief", "recent_notes", "reasoning_block",
    "chat_facts", "_build_gp_context", "get_system_prompt",
)

# Модули с messages.create, которые НЕ рассуждают о состоянии пациента (осознанно).
ALLOWLIST = {
    # llm_admission (2026-10-02, нить llm-provider): СУД ДОПУСКА модели на синтетике и
    # эталонах, а не рассуждение о пациенте. Ответ модели читает код-судья; память пациента
    # в промпте исказила бы эталон, а не улучшила его.
    "llm_admission",
    # llm_translate (2026-10-02): перевод формата запроса/ответа, промптов не строит.
    "llm_translate",
    # llm_client (2026-08-03): ТРАНСПОРТ, а не рассуждатель. Он не строит промпт
    # и ничего не знает о пациенте — только отдаёт клиента с гардом на исходящем
    # тексте. Память тянет тот, кто рассуждает; требовать её от транспорта значит
    # затащить контекст пациента в слой, которому он не нужен.
    "llm_client",
    # problems_db (2026-09-27): ПЕРЕСКАЗ одной формулировки проблемы простым языком
    # (_plain_llm), а не рассуждение о пациенте: на входе только заголовок и описание,
    # модели запрещено добавлять что-либо сверх текста; память пациента тут исказила бы
    # пересказ, а не улучшила его.
    "problems_db",
    # night_investigator (2026-08-04): судит ПАДЕНИЕ ТЕСТА, а не пациента. Улики
    # собирает харнесс (логи, вывод прогона), на вход идёт текст улик, на выход —
    # {class, diagnosis, action}. Ни patient_*, ни health_db, ни memory в модуле нет.
    # Тот же класс, что doc_triage: рассуждатель есть, предмет — не человек.
    "night_investigator",
    # weekly_digest (2026-09-05): рассуждает о КОММИТАХ, не о пациенте, и обязан НЕ знать
    # пациента — текст один на всех тенантов, гейт режет любой факт о человеке. Память
    # пациента здесь была бы не пробелом покрытия, а утечкой.
    "weekly_digest",
    # desc_translation (2026-09-29): переводит ПОЯСНЕНИЯ КОДА (докстринги, комментарии) для
    # английских справочников; пациента не знает и знать не должен — справочник публичный.
    "desc_translation",
    # извлечение / утилиты
    "doc_agent", "doc_triage", "lab_extractor", "lab_recognizer", "lab_schedule_extractor",
    "treatment_extractor", "import_medical_events", "publication_reader",
    "genome_update_agent", "genome_context", "model_health_check",
    "hypothesis_semantic_check", "cbcr_lookup", "fill_description_ru", "api_spend_log",
    # doc_triage (2026-07-28): судит, ЧТО изображено на кадре, а не что с пациентом.
    # Память тут не пропущена по недосмотру, а запрещена по замыслу: контекст «у него
    # были анализы» смещал бы классификацию в сторону lab на любом фото, а безопасная
    # сторона и так lab. Плюс незачем тащить анамнез в промпт, которому нужна картинка.
    # аналитики (решение: не читают жалобы из чата)
    "survivorship_curator", "literature_curator", "task_agent",
    # тонкий вызов — контекст собран upstream и передан вызывающим
    "hai_chat",   # контекст собирает hai_core (get_system_prompt→build_patient_brief)
    "food_rule_generator",   # monthly_consilium._build_consilium_input собирает контекст пациента, передан как input_pkg (data-in-code-9)
    # легаси — заменён gp_agent, в проде не зовётся
    "hai_reports",
    # dev/тест/патч-скрипты
    "lab_drytest", "test_failure_handler", "_patch_blueprint",
    # сам движок памяти
    "memory_consolidation",
    # visual/symptom-intake: мотор элиситации, memory-free ПО ЗАМЫСЛУ. Knowledge-base
    # детекция опасного сознательно отвергнута (docs/explanation/symptom_intake_hypothesis.md,
    # §«Почему безопасность — это НЕ детекция опасного»). Безопасность = запрет успокоения +
    # неколлапс дифференциала + needs_specialist-handoff, НЕ память. Память заякорила бы
    # анти-якорный мотор. Инвариант обеспечен: tests/unit/test_symptom_intake_memory_free.py.
    "symptom_intake",
}

# Способ 2: функции-потребители, проверяемые ПОФУНКЦИОННО (риск частичного покрытия)
CONSUMER_FNS = {
    "gp_agent.py": {"generate_daily_report", "generate_weekly_report", "generate_monthly_report"},
}


def _fn_covered(src: str, tree: ast.AST, fname: str) -> list[str]:
    lines = src.splitlines()
    gaps = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in CONSUMER_FNS.get(fname, ()):
            body = "\n".join(lines[node.lineno - 1: (node.end_lineno or node.lineno)])
            if not any(p in body for p in PROVIDERS):
                gaps.append(f"{fname}::{node.name}")
    return gaps


def _coverage_gaps() -> list[str]:
    gaps: list[str] = []
    for p in sorted(ROOT.glob("*.py")):
        src = p.read_text(errors="ignore")
        if "messages.create" not in src:
            continue
        # function-level (способ 2)
        if p.name in CONSUMER_FNS:
            gaps += _fn_covered(src, ast.parse(src), p.name)
            continue
        # file-level backstop
        if p.stem in ALLOWLIST:
            continue
        if any(pr in src for pr in PROVIDERS):
            continue
        gaps.append(f"{p.name} (новый непокрытый LLM-потребитель)")
    return gaps


def test_all_patient_reasoners_use_context():
    gaps = _coverage_gaps()
    assert not gaps, (
        "LLM-потребители рассуждают о пациенте, но НЕ тянут память через patient_context: "
        f"{gaps}. Подключи через patient_context.reasoning_block()/build_patient_brief(), "
        "либо (если это извлечение/аналитика) добавь модуль в ALLOWLIST с причиной."
    )
