"""
tests/unit/test_treatment.py — лечение как производное из документов.

Покрывает:
  L1: _normalize_regimen, upsert_medication (cumulative-MAX, идемпотентность, гейт),
      build_treatment_summary (группировка по эпизодам, chemo vs immuno, пустой случай).
  L2: process_treatment с мокнутым Anthropic → medications (proposed); фильтр _mentions_therapy.

Главный инвариант: снимки одного режима НЕ складываются (4 и 9 → 9, не 13).
"""
import json
import importlib
import pytest

import health_db
import treatment_summary
import treatment_extractor


def _ep(db, problem_id, title, start, end):
    db.execute(
        "INSERT INTO episodes_of_care (primary_problem_id, title, start_date, end_date, status) "
        "VALUES (?,?,?,?, 'finished')",
        (problem_id, title, start, end),
    )


# ── L1: нормализация ─────────────────────────────────────────────────────────



# ── L1: upsert cumulative-MAX (ядро) ─────────────────────────────────────────

def test_upsert_cumulative_max_not_sum(db):
    health_db.upsert_medication("REG-A", modality="chemo", cycles_completed=2,
                                indication_problem_id="p1", confirmation="proposed")
    health_db.upsert_medication("REG-A", modality="chemo", cycles_completed=6,
                                indication_problem_id="p1", confirmation="proposed")
    rows = health_db.get_medications(include_proposed=True)
    rega = [r for r in rows if r["name"] == "REG-A"]
    assert len(rega) == 1, "снимки одного режима не должны плодить строки"
    assert rega[0]["cycles_completed"] == 6, "должно быть MAX(2,6)=6, не сумма 8"


def test_upsert_lower_count_does_not_downgrade(db):
    health_db.upsert_medication("REG-A", cycles_completed=6, indication_problem_id="p1",
                                confirmation="proposed")
    health_db.upsert_medication("REG-A", cycles_completed=2, indication_problem_id="p1",
                                confirmation="proposed")
    rega = [r for r in health_db.get_medications(include_proposed=True) if r["name"] == "REG-A"]
    assert rega[0]["cycles_completed"] == 6


def test_upsert_distinct_episodes_separate_rows(db):
    health_db.upsert_medication("REG-A", cycles_completed=6, indication_problem_id="p1",
                                confirmation="proposed")
    health_db.upsert_medication("REG-A", cycles_completed=3, indication_problem_id="p2",
                                confirmation="proposed")
    rega = [r for r in health_db.get_medications(include_proposed=True) if r["name"] == "REG-A"]
    assert len(rega) == 2


def test_gate_confirmed_not_overwritten_by_extractor(db):
    health_db.upsert_medication("REG-A", cycles_completed=6, indication_problem_id="p1",
                                confirmation="confirmed", source="manual")
    # extractor пытается переписать подтверждённое — не должен
    health_db.upsert_medication("REG-A", cycles_completed=69, indication_problem_id="p1",
                                confirmation="proposed", source="extractor:5")
    rega = [r for r in health_db.get_medications() if r["name"] == "REG-A"]
    assert rega[0]["cycles_completed"] == 6


# ── L1: build_treatment_summary ──────────────────────────────────────────────

def test_summary_empty_when_no_meds(db):
    s = treatment_summary.build_treatment_summary()
    assert s["empty"] is True
    assert s["text"] == ""


def test_summary_proposed_not_counted(db):
    # proposed (не подтверждённые гейтом) не попадают в сводку
    health_db.upsert_medication("REG-A", modality="chemo", cycles_completed=6,
                                indication_problem_id="p1", confirmation="proposed")
    s = treatment_summary.build_treatment_summary()
    assert s["empty"] is True


def test_summary_history_and_totals(db):
    # Схема выдумана и нарочно не похожа ни на чью историю (29.09, чтение постороннего):
    # проверяется только механика — группировка по эпизодам, химиолучевая в счётчике
    # химии, иммунотерапия отдельно.
    _ep(db, "primary", "Первичный эпизод — курс лечения A", "2030-03-10", "2031-02-01")
    _ep(db, "second", "Второй эпизод — курс лечения B", "2031-06-15", "2032-01-20")
    health_db.upsert_medication("REG-A", modality="chemo", intent="adjuvant",
                                cycles_completed=4, indication_problem_id="primary",
                                start_date="2030-04", confirmation="confirmed")
    health_db.upsert_medication("REG-B", modality="chemoradiation", intent="definitive",
                                cycles_completed=2, indication_problem_id="second",
                                start_date="2031-06", confirmation="confirmed")
    health_db.upsert_medication("Examplimab", modality="immunotherapy",
                                indication_problem_id="second", start_date="2031-09",
                                confirmation="confirmed")
    s = treatment_summary.build_treatment_summary()
    assert s["empty"] is False
    # chemo: REG-A 4 + REG-B 2 = 6; экзамплимаб (иммуно) НЕ в счётчике химии
    assert s["chemo_courses_total"] == 6
    assert s["immunotherapy"] == ["Examplimab"]
    # хронология по эпизодам, первичный раньше второго
    assert s["text"].index("Первичный") < s["text"].index("Второй")
    assert "REG-B" in s["text"] and "REG-A" in s["text"]


# ── L2: process_treatment с мокнутым LLM ─────────────────────────────────────

def test_process_treatment_extracts_to_medications(db, anthropic_mock):
    _ep(db, "second", "Второй эпизод — курс лечения B", "2031-06-15", "2032-01-20")
    anthropic_mock.script(
        match="ДОКУМЕНТ",
        response=json.dumps([{
            "regimen": "REG-A", "agents": ["exampliplatin", "examplimide"],
            "modality": "chemo", "intent": "adjuvant", "cycles_completed": 6,
            "dose": None, "start_date": "2031-06", "end_date": "2031-12",
            "status": "completed",
        }], ensure_ascii=False),
    )
    n = treatment_extractor.process_treatment(
        event_id=42, text="Пациент получил 6 cycles of REG-A химиотерапии.",
        effective_date="2031-09-01",
    )
    assert n == 1
    assert len(anthropic_mock.calls) == 1
    rows = health_db.get_medications(include_proposed=True)
    rega = [r for r in rows if r["name"] == "REG-A"]
    assert rega and rega[0]["cycles_completed"] == 6
    assert rega[0]["confirmation"] == "proposed"          # человек-гейт
    assert rega[0]["indication_problem_id"] == "second"  # привязка к эпизоду по дате


def test_process_treatment_skips_non_therapy_text(db, anthropic_mock):
    n = treatment_extractor.process_treatment(
        event_id=1, text="Пациент чувствует себя хорошо, жалоб нет.",
        effective_date="2026-01-01",
    )
    assert n == 0
    assert anthropic_mock.calls == [], "LLM не должен дёргаться без упоминания терапии"


# ── Гейт: подтверждение/отклонение (Ф6) ──────────────────────────────────────

def test_gate_flow_unsent_then_confirm(db):
    health_db.upsert_medication("REG-A", modality="chemo", cycles_completed=6,
                                indication_problem_id="p1", confirmation="proposed")
    unsent = health_db.get_unsent_proposed_medications()
    assert len(unsent) == 1
    mid = unsent[0]["id"]
    # помечаем отправленной — повторно не шлём
    health_db.mark_medication_gate_sent(mid)
    assert health_db.get_unsent_proposed_medications() == []
    # до подтверждения — не в сводке
    assert treatment_summary.build_treatment_summary()["empty"] is True
    # подтверждаем гейтом → попадает в историю
    health_db.set_medication_confirmation(mid, "confirmed")
    s = treatment_summary.build_treatment_summary()
    assert s["empty"] is False and s["chemo_courses_total"] == 6


def test_gate_reject_keeps_out_of_summary(db):
    health_db.upsert_medication("REG-A", modality="chemo", cycles_completed=6,
                                indication_problem_id="p1", confirmation="proposed")
    mid = health_db.get_proposed_medications()[0]["id"]
    health_db.set_medication_confirmation(mid, "rejected")
    assert treatment_summary.build_treatment_summary()["empty"] is True


def test_extractor_robust_to_trailing_text(db, anthropic_mock):
    # LLM иногда добавляет пояснение после JSON — парсер должен вычленить массив
    _ep(db, "primary", "Первичный эпизод", "2030-03-10", "2031-02-01")
    anthropic_mock.script(
        match="ДОКУМЕНТ",
        response='[{"regimen":"REG-B","modality":"chemoradiation","intent":"definitive",'
                 '"cycles_completed":2,"agents":["exampliplatin","examplitaxel"]}]\n\n'
                 'Примечание: данные извлечены из текста выписки.',
    )
    n = treatment_extractor.process_treatment(
        event_id=1, text="Assessment after chemoradiation course X.",
        effective_date="2030-05-01",
    )
    assert n == 1
    rows = [r for r in health_db.get_medications(include_proposed=True) if r["name"] == "REG-B"]
    assert rows and rows[0]["modality"] == "chemoradiation" and rows[0]["cycles_completed"] == 2


def test_summary_renders_agents(db):
    _ep(db, "second", "Второй эпизод", "2031-06-15", "2032-01-20")
    health_db.upsert_medication("REG-A", modality="chemo", cycles_completed=6,
                                agents=["exampliplatin", "examplimide"],
                                indication_problem_id="second", start_date="2031-06",
                                confirmation="confirmed")
    s = treatment_summary.build_treatment_summary()
    assert "exampliplatin" in s["text"] and "examplimide" in s["text"]
