"""Медицинская рамка человека одинакова во ВСЕХ врачебных контекстах.

В вымышленном примере профиль без поля диагноза содержит активную проблему.
Читатель только поля диагноза теряет непустой список проблем, хотя GP его видит.
Проблемы, названные при знакомстве с системой, должны попадать во все контексты:
консилиум, подготовку к визиту, профиль пациента и GP.
У пустого профиля вместо объявления отсутствия данных появлялись заглушки,
а консилиум без даты рождения падал. Данные тестов ниже вымышлены.

Оракул: один и тот же человек → каждый контекст содержит его проблему, не содержит заглушек,
а пустота объявлена словами, а не спрятана.
"""
from __future__ import annotations

import re
from datetime import date

import pytest

pytestmark = pytest.mark.unit

END = date(2026, 9, 12)
PLACEHOLDER = re.compile(r"\[[^\]\n]{0,40}не задан[аоы]?\]|None[чм ]")


class _Stub:
    captured: list = []

    class messages:
        @staticmethod
        def create(**kw):
            _Stub.captured.append("\n".join(str(m["content"]) for m in kw.get("messages", [])))
            raise RuntimeError("stub: модель в тесте не вызывается")


def _contexts(monkeypatch) -> dict[str, str]:
    import patient_context, wellally_consult, consult_prep, gp_context
    monkeypatch.setattr(consult_prep, "_get_client", lambda: _Stub())
    _Stub.captured = []
    try:
        consult_prep.prepare_visit_report("2026-10-01", "терапевт")
    except Exception:
        pass
    return {
        "бриф (patient_context)": patient_context.build_patient_brief(),
        "консилиум (wellally)": wellally_consult._build_data_package(end_date=END, period_days=7),
        "визит (consult_prep)": _Stub.captured[-1] if _Stub.captured else "",
        "GP (контекст)": gp_context._build_gp_context(END),
        # системные промпты GP шьют эти два куска; сами промпты требуют periods (миграция Studio)
        "GP (шапка и визит)": gp_context._patient_header() + " | " + gp_context._next_appointment(),
    }


def test_problem_reaches_every_doctor(db, clock, monkeypatch):
    clock.set("2026-09-13")
    db.add_profile("identity.birth_date", value_text="1980-05-05", category="identity")
    db.add_problem("P-T1", "проблема-Икс")
    ctx = _contexts(monkeypatch)
    for name in ("бриф (patient_context)", "консилиум (wellally)", "визит (consult_prep)", "GP (контекст)"):
        assert "проблема-Икс" in ctx[name], f"{name} не видит активную проблему человека"
    assert "проблема-Икс" not in patient_context_resolved_frame(db)


def patient_context_resolved_frame(db) -> str:
    """Решённая проблема в рамку не едет — рамка про текущее."""
    db.add_problem("P-T2", "проблема-Игрек", status="resolved")
    import patient_context
    return " ".join(l for l in patient_context.medical_frame_lines({}) if "Игрек" in l)


def test_empty_profile_declares_absence_and_prints_no_placeholders(db, clock, monkeypatch):
    clock.set("2026-09-13")   # нет даты рождения, диагноза, проблем — консилиум обязан собраться
    ctx = _contexts(monkeypatch)
    for name, text in ctx.items():
        assert text, f"{name}: контекст не собрался"
        hits = PLACEHOLDER.findall(text)
        assert not hits, f"{name}: заглушки в промпте модели: {hits}"
    for name in ("бриф (patient_context)", "консилиум (wellally)", "визит (consult_prep)"):
        assert "не сообщены" in ctx[name], f"{name}: пустота рамки не объявлена"
    assert "возраст не указан" in ctx["консилиум (wellally)"]


def test_allergies_line_declared_both_ways():
    """27.09: рамка врача говорит об аллергиях всегда — сообщено или «не сообщены»."""
    from patient_context import medical_frame_lines
    assert any("не сообщены" in l and "Аллерги" in l for l in medical_frame_lines({}))
    assert any("пенициллин" in l for l in medical_frame_lines({"allergies": "пенициллин"}))
