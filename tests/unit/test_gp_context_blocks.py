"""Sprint 4b unit-тесты для 13 секций _build_gp_context (F-092 / Sprint 4).

Каждая функция тестируется в изоляции:
  - empty case: нет данных → возвращает [] (или минимальный шаблон)
  - happy case: данные есть → возвращает строки с ожидаемым содержимым

specialist_review_block уже покрыт tests/unit/test_gp_specialist_review_block.py.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

pytestmark = pytest.mark.unit


# ── _build_location_header ─────────────────────────────────────────────────

def test_location_header_default_unknown(db, clock):
    clock.set("2026-05-22")
    import gp_agent
    lines = gp_agent._build_location_header(date(2026, 5, 22), 7)
    assert lines[0].startswith("=== ДАННЫЕ ДЛЯ GP")
    assert "до 2026-05-22" in lines[0]
    assert "7 дней" in lines[0]
    assert "не указана" in lines[1]  # fallback: без страны владельца


def test_location_header_from_profile(db, clock):
    clock.set("2026-05-22")
    db.add_profile("current_location", value_json='{"city":"Берлин","country":"Germany"}')
    import gp_agent
    lines = gp_agent._build_location_header(date(2026, 5, 22), 7)
    assert "Берлин" in lines[1]


# ── _build_lifestyle_days_rows ─────────────────────────────────────────────

def test_lifestyle_days_rows_empty(db, clock):
    clock.set("2026-05-22")
    import gp_agent
    rows = gp_agent._build_lifestyle_days_rows(date(2026, 5, 22), 3)
    assert len(rows) == 3
    # При отсутствии данных все значения "—"
    assert all("—" in r for r in rows)


def test_lifestyle_days_rows_with_data(db, clock):
    """_build_lifestyle_days_rows читает через db.get_day() → raw JSON.
    Чтобы данные появились в выводе, нужно использовать upsert_metrics_from_json
    (правильный путь) или прямо записать raw."""
    clock.set("2026-05-22")
    import json
    for offset in range(1, 4):
        d = str(date(2026, 5, 22) - timedelta(days=offset))
        raw = json.dumps({
            "sleep": {"totalSleep": 7.5, "deep": 0.7, "sleep_score": 80},
            "hrv": {"avg": 25.0},
            "steps": 8000,
        })
        with db.conn() as c:
            c.execute("INSERT OR REPLACE INTO daily_metrics (date, raw) VALUES (?, ?)",
                      (d, raw))
    import gp_agent
    rows = gp_agent._build_lifestyle_days_rows(date(2026, 5, 22), 3)
    assert len(rows) == 3
    assert all("сон 7.5ч" in r for r in rows)


# ── _build_trends_block ────────────────────────────────────────────────────

def test_trends_block_empty_stats():
    import gp_agent
    lines = gp_agent._build_trends_block({}, {}, {}, {})
    assert lines[0].startswith("ТРЕНДЫ")
    assert len(lines) == 7  # header + 6 metric rows (REM добавлен 28.09)
    assert all("—" in l for l in lines[1:])


def test_trends_block_with_data():
    import gp_agent
    s = {"avg_sleep": 7.5, "avg_deep": 0.7, "avg_hrv": 30, "avg_readiness": 75, "avg_sleep_score": 80}
    lines = gp_agent._build_trends_block(s, s, s, s)
    assert "7.5 / 7.5 / 7.5 / 7.5" in lines[1]


def test_trends_block_carries_rem():
    """28.09: автозадача «REM ниже нормы» снята (тренды — в разбор, не в список человека);
    единственный путь недельного тренда REM в разбор — эта строка. Без неё сигнал по REM
    в еженедельный разбор не доезжает."""
    import gp_agent
    lines = gp_agent._build_trends_block({"avg_rem": 1.5}, {"avg_rem": 1.25}, {}, {"avg_rem": 1.75})
    rem = [l for l in lines if l.strip().startswith("REM")]
    assert rem and "90" in rem[0] and "75" in rem[0] and "105" in rem[0], lines


# ── _build_labs_block ──────────────────────────────────────────────────────

def test_labs_block_empty(db, clock):
    clock.set("2026-05-22")
    import gp_agent
    lab_lines, recent_labs = gp_agent._build_labs_block()
    assert lab_lines == []
    assert recent_labs == []


def test_labs_block_with_data(db, clock):
    clock.set("2026-05-22")
    db.add_lab_result("2026-04-30", "CEA", 1.3, unit="ng/mL")
    db.add_lab_result("2026-04-30", "HGB", 15.2, unit="g/dL")
    import gp_agent
    lab_lines, recent_labs = gp_agent._build_labs_block()
    assert len(recent_labs) == 2
    assert any("CEA" in l for l in lab_lines)
    assert any("HGB" in l for l in lab_lines)


# ── _build_freshness_block ─────────────────────────────────────────────────

def test_freshness_block_no_overdue(db, clock):
    """Если все labs свежие — возвращает []."""
    clock.set("2026-05-22")
    fresh_date = str(date(2026, 5, 22) - timedelta(days=5))
    db.add_lab_result(fresh_date, "CEA", 1.3)
    db.add_lab_result(fresh_date, "HGB", 15.2)
    db.add_lab_result(fresh_date, "MCV", 87)
    db.add_lab_result(fresh_date, "WBC", 5.5)
    db.add_lab_result(fresh_date, "PLT", 200)
    # All other "high/critical" tests — отсутствуют, что вызывает missing block.
    # Поэтому это «happy path» с минимумом — но overdue=[] ⇒ возможно "ОТСУТСТВУЮТ"
    import gp_agent, health_db
    recent = health_db.get_recent_labs(730)
    lines = gp_agent._build_freshness_block(recent, date(2026, 5, 22))
    # Либо [] (все свежие, ничего не отсутствует), либо со словом ОТСУТСТВУЮТ
    if lines:
        assert lines[0] == ""
        assert "АКТУАЛЬНОСТЬ" in lines[1]


def test_freshness_block_overdue(db, clock):
    """Если labs просрочены — возвращает строки про АКТУАЛЬНОСТЬ ДАННЫХ."""
    clock.set("2026-05-22")
    old_date = str(date(2026, 5, 22) - timedelta(days=200))
    db.add_lab_result(old_date, "CEA", 1.3)
    import gp_agent, health_db
    recent = health_db.get_recent_labs(730)
    lines = gp_agent._build_freshness_block(recent, date(2026, 5, 22))
    assert any("АКТУАЛЬНОСТЬ" in l for l in lines)


# ── _build_mdt_block ───────────────────────────────────────────────────────

def test_mdt_block_empty(db, clock):
    clock.set("2026-05-22")
    import gp_agent
    lines = gp_agent._build_mdt_block(date(2026, 5, 22))
    assert lines == []


def test_mdt_block_with_report(db, clock):
    clock.set("2026-05-22")
    with db.conn() as c:
        c.execute(
            """INSERT INTO agent_reports (date, agent_type, agent_name, has_findings, findings)
               VALUES (?, 'mdt', 'mdt_weekly', 1, ?)""",
            ("2026-05-18", "MDT synthesis: stress↑ recovery↓"),
        )
    import gp_agent
    lines = gp_agent._build_mdt_block(date(2026, 5, 22))
    assert any("ПОСЛЕДНЯЯ МДТ" in l for l in lines)


def test_mdt_block_takes_the_newest_report(db, clock):
    """Последний МДТ выбирается по дате из вымышленной последовательности.
    Мутация: ORDER BY agent_type, agent_name вместо даты возвращает старый отчёт."""
    clock.set("2032-04-22")
    with db.conn() as c:
        for d, txt in [("2032-04-13", "СТАРАЯ: учебная заметка"),
                       ("2032-04-16", "СРЕДНЯЯ"),
                       ("2032-04-20", "СВЕЖАЯ")]:
            c.execute(
                """INSERT INTO agent_reports (date, agent_type, agent_name, has_findings, findings)
                   VALUES (?, 'mdt', 'mdt_weekly', 1, ?)""", (d, txt))
    import gp_agent
    text = "\n".join(gp_agent._build_mdt_block(date(2032, 4, 22)))
    assert "ПОСЛЕДНЯЯ МДТ (2032-04-20)" in text and "СВЕЖАЯ" in text
    assert "СТАРАЯ" not in text


# ── _build_active_periods_block ────────────────────────────────────────────

def test_active_periods_empty(db, clock):
    clock.set("2026-05-22")
    import gp_agent
    lines = gp_agent._build_active_periods_block(date(2026, 5, 22))
    assert lines == []


def test_active_periods_with_data(db, clock):
    clock.set("2026-05-22")
    db.add_period("ремиссия", type_="treatment", start_date="2026-01-01")
    import gp_agent
    lines = gp_agent._build_active_periods_block(date(2026, 5, 22))
    assert any("АКТИВНЫЕ КЛИНИЧЕСКИЕ ПЕРИОДЫ" in l for l in lines)
    assert any("ремиссия" in l for l in lines)


# ── _build_med_events_block ────────────────────────────────────────────────

def test_med_events_block_empty(db, clock):
    clock.set("2026-05-22")
    import gp_agent
    lines, events = gp_agent._build_med_events_block(date(2026, 5, 22))
    assert lines == []
    assert events == []


def test_med_events_block_with_data(db, clock):
    clock.set("2026-05-22")
    evt_id = db.add_event(event_type="encounter", effective_date="2026-04-15",
                          performer="Ivanov", location="Clinic")
    db.add_encounter(event_id=evt_id, specialty="oncology", assessment="Стабильная ремиссия")
    import gp_agent
    lines, events = gp_agent._build_med_events_block(date(2026, 5, 22))
    assert len(events) == 1
    assert any("МЕДИЦИНСКИЕ СОБЫТИЯ" in l for l in lines)
    assert any("Ivanov" in l for l in lines)


# ── _build_consultations_block — dedup vs med_events ───────────────────────

def test_consultations_block_empty(db):
    import gp_agent
    lines = gp_agent._build_consultations_block(med_events=[])
    assert lines == []


def test_consultations_block_dedup_against_med_events(db, clock):
    """Если консультация совпадает по дате с med_event — исключить из блока."""
    clock.set("2026-05-22")
    db.add_consultation("2026-04-15", "oncologist", key_findings="Test")
    db.add_consultation("2026-04-20", "cardiologist", key_findings="Other")
    import gp_agent
    # med_events содержит событие на 2026-04-15 — должно убрать первую консультацию
    med_events = [{"effective_date": "2026-04-15"}]
    lines = gp_agent._build_consultations_block(med_events=med_events)
    text = "\n".join(lines)
    assert "2026-04-20" in text  # эта осталась
    assert "2026-04-15" not in text  # эта была убрана через dedup


def test_consultations_block_no_dedup_when_empty_med_events(db, clock):
    """Если med_events=[] — все консультации показываются."""
    clock.set("2026-05-22")
    db.add_consultation("2026-04-15", "oncologist", key_findings="Test1")
    db.add_consultation("2026-04-20", "cardiologist", key_findings="Test2")
    import gp_agent
    lines = gp_agent._build_consultations_block(med_events=[])
    text = "\n".join(lines)
    assert "2026-04-15" in text
    assert "2026-04-20" in text


# ── _build_lifestyle_patterns_block ────────────────────────────────────────

def test_lifestyle_patterns_block_empty_returns_empty(db, clock):
    """Нет данных → нет флагов → пустой результат."""
    clock.set("2026-05-22")
    import gp_agent
    lines = gp_agent._build_lifestyle_patterns_block(date(2026, 5, 22), 3)
    # Без данных flags={}, return []
    assert lines == []


# ── _build_context_events_block ────────────────────────────────────────────

def test_context_events_block_empty(db, clock):
    clock.set("2026-05-22")
    import gp_agent
    lines = gp_agent._build_context_events_block(date(2026, 5, 22), 7)
    assert lines == []


def test_context_events_block_excludes_checkins(db, clock):
    """Source='checkin' должны быть отфильтрованы."""
    clock.set("2026-05-22")
    import health_db
    health_db.save_context_event("2026-05-20", source="checkin", category="mood",
                                 value_text="ok")
    health_db.save_context_event("2026-05-20", source="manual", category="travel",
                                 value_text="trip")
    import gp_agent
    lines = gp_agent._build_context_events_block(date(2026, 5, 22), 7)
    text = "\n".join(lines)
    assert "trip" in text
    assert "ok" not in text  # checkin отфильтрован


# ── _build_longitudinal_correlations_block ─────────────────────────────────

def test_longitudinal_correlations_empty(db, clock):
    clock.set("2026-05-22")
    import gp_agent
    lines = gp_agent._build_longitudinal_correlations_block()
    assert lines == []


def test_longitudinal_correlations_with_data(db, clock):
    clock.set("2026-05-22")
    import json
    findings = json.dumps({
        "top_correlations": [{"a": "hrv", "b": "sleep_deep", "r": 0.78}],
        "lab_metric_correlations": [{"lab": "Creatinine", "metric": "hrv", "r": 0.83}],
        # С 2026-07-26 блок принимает только гейтованную веру (belief_contract).
        "gate": {"gate_applied": True, "status": "applied"},
    }, ensure_ascii=False)
    with db.conn() as c:
        c.execute(
            """INSERT INTO agent_reports (date, agent_type, agent_name, has_findings, findings)
               VALUES (?, 'longitudinal_analysis', 'longitudinal_analysis', 1, ?)""",
            ("2026-05-21", findings),
        )
    import gp_agent
    lines = gp_agent._build_longitudinal_correlations_block()
    text = "\n".join(lines)
    assert "ДОЛГОСРОЧНЫЕ КОРРЕЛЯЦИИ" in text
    assert "hrv" in text
    assert "Creatinine" in text


def test_longitudinal_correlations_refused_without_gate(db, clock):
    """Мутация схемы: та же вера без ключа `gate` → блок ОТКАЗЫВАЕТ, а не рендерит.
    Негативный контроль к тесту выше: без него «блок собрался» ничего не доказывает."""
    clock.set("2026-05-22")
    import json
    findings = json.dumps({
        "top_correlations": [{"a": "hrv", "b": "sleep_deep", "r": 0.78}],
        "lab_metric_correlations": [{"lab": "Creatinine", "metric": "hrv", "r": 0.83}],
    }, ensure_ascii=False)
    with db.conn() as c:
        c.execute(
            """INSERT INTO agent_reports (date, agent_type, agent_name, has_findings, findings)
               VALUES (?, 'longitudinal_analysis', 'longitudinal_analysis', 1, ?)""",
            ("2026-05-21", findings),
        )
    import gp_agent
    text = "\n".join(gp_agent._build_longitudinal_correlations_block())
    assert "не подаются" in text, "отказ обязан быть видимым, не молчаливо пустым"
    assert "Creatinine" not in text and "sleep_deep" not in text, \
        "негейтованная корреляция не должна попасть в промпт врачебного контекста"


def test_longitudinal_empty_belief_is_declared_not_silent(db, clock):
    """Пустая принятая вера должна явно сообщать, что проверка выполнена.
    Отсутствие связей отличается от отсутствия блока."""
    clock.set("2026-05-22")
    import json
    findings = json.dumps({"top_correlations": [], "lab_metric_correlations": [],
                           "gate": {"gate_applied": True, "status": "applied", "daily_family_m": 251}},
                          ensure_ascii=False)
    with db.conn() as c:
        c.execute(
            """INSERT INTO agent_reports (date, agent_type, agent_name, has_findings, findings)
               VALUES (?, 'longitudinal_analysis', 'longitudinal_analysis', 1, ?)""",
            ("2026-05-21", findings),
        )
    import gp_agent
    text = "\n".join(gp_agent._build_longitudinal_correlations_block())
    assert "подтверждённых связей между показателями нет" in text and "251" in text


# ── _build_genome_block — внешний модуль, проверим только что [] на failure ──

def test_genome_block_falls_back_on_error(db, clock):
    """Без real genome data — должно вернуть [] без exception."""
    clock.set("2026-05-22")
    import gp_agent
    lines = gp_agent._build_genome_block()
    assert isinstance(lines, list)  # тип валиден
    # Содержимое может быть [] (нет данных) или с блоком — оба ок.


# ── _build_stress_workouts_section — компактный smoke ─────────────────────

def test_stress_workouts_empty(db, clock):
    """Нет stress/workout данных → возвращает [] или минимальный шаблон."""
    clock.set("2026-05-22")
    import gp_agent
    lines = gp_agent._build_stress_workouts_section(date(2026, 5, 22), 7)
    # При отсутствии данных Тренировки: нет данных — return list[str]
    assert isinstance(lines, list)


def test_stress_workouts_with_data(db, clock):
    clock.set("2026-05-22")
    for offset in range(1, 8):
        d = str(date(2026, 5, 22) - timedelta(days=offset))
        db.add_daily_metrics(d, stress_high_min=120, recovery_high_min=180,
                              stress_summary="normal", resilience_level="solid")
    import gp_agent
    lines = gp_agent._build_stress_workouts_section(date(2026, 5, 22), 7)
    text = "\n".join(lines)
    assert "СТРЕСС И НАГРУЗКА" in text


# ── 2026-08-30: allowlist аналитов снят; сторож «не сдавался» ─────────────────

def test_labs_block_shows_every_canon_row_not_allowlist(db, clock):
    """Каждая строка вымышленного набора за окно должна попасть в лаб-блок.
    Ручной allowlist не должен скрывать присутствующие аналиты."""
    clock.set("2032-04-12")
    db.add_lab_result("2032-03-09", "TSH", 2.75, unit="mImmol/L")
    db.add_lab_result("2032-04-12", "Ferritin", 63.0, unit="ng/mL")
    db.add_lab_result("2032-04-12", "Magnesium", 0.91, unit="mmol/L")
    import gp_agent
    lab_lines, recent = gp_agent._build_labs_block()
    for name in ("TSH", "Ferritin", "Magnesium"):
        assert any(name in l for l in lab_lines), name
    assert {r["test_name"] for r in recent} == {"TSH", "Ferritin", "Magnesium"}


def test_freshness_missing_medium_is_visible(db, clock):
    """Тест без строки и с priority medium раньше молча пропускался — ни ✓, ни ❌."""
    clock.set("2026-08-24")
    import gp_agent, health_db
    lines = gp_agent._build_freshness_block(health_db.get_recent_labs(730), date(2026, 8, 24))
    block = "\n".join(lines)
    assert "⚠ ALT" in block and "нет данных" in block and "[medium]" in block


def test_absent_claims_contradicted_matches_report_phrases():
    """Независимый вымышленный отчёт: пять ложных отсутствий и четыре контроля."""
    import gp_context as g
    txt = ("Учебный пример: TSH — не сдавался вообще; "
           "ферритин и витамин D — никогда не сдавались; "
           "магний и цинк — не было ни разу; "
           "ALT измерен; AST измерен; глюкоза просрочена; альбумин сдан.")
    last = {name: "2032-04-12" for name in
            ("TSH", "Ferritin", "Vitamin_D", "Magnesium", "Zinc",
             "ALT", "AST", "Glucose", "Albumin")}
    hits = g.absent_claims_contradicted(txt, last)
    assert {h["test"] for h in hits} == {"TSH", "Ferritin", "Vitamin_D", "Magnesium", "Zinc"}
    assert not {"ALT", "AST", "Glucose", "Albumin"} & {h["test"] for h in hits}


def test_absent_claims_silent_when_analyte_truly_absent():
    import gp_context as g
    assert g.absent_claims_contradicted("липаза — никогда не сдавалась", {"Amylase": "2026-08-24"}) == []


# ── 2026-09-13: граница окна объявляется, сторож различает датированное ───────

def test_labs_block_declares_window_boundary(db, clock):
    """Вымышленная строка вне окна объявляется датой, но не именем.
    Это позволяет отличить отсутствие в срезе от отсутствия во всём каноне."""
    clock.set("2032-04-12")
    db.add_lab_result("2032-04-02", "HbA1c", 5.3, unit="%")
    db.add_lab_result("2027-02-09", "TSH", 2.35, unit="mIU/L")
    import gp_agent
    block = "\n".join(gp_agent._build_labs_block()[0])
    assert "HbA1c" in block                      # внутри окна — строкой со значением
    assert "ГРАНИЦА ОКНА" in block
    assert "2027-02-09" in block                 # дата забора за окном названа
    assert "TSH" not in block                # а имя — нет
    assert "нет данных за 730" in block          # предписанная правдивая формулировка


def test_canon_window_note_says_all_when_nothing_older(db, clock):
    """Нет строк старше окна → блок и есть весь канон, и это сказано прямо."""
    clock.set("2026-09-13")
    db.add_lab_result("2026-08-24", "HbA1c", 5.5, unit="%")
    import labs_db
    note = labs_db.canon_window_note(730)
    assert "ВЕСЬ канон" in note and "не сдавался" in note


def test_absent_claim_with_last_date_year_is_not_a_hit():
    """«Инсулин 2022 года, с тех пор не сдавался» — правда, и сторож обязан молчать.

    Без этого исключения поправка валидатора («ссылайся на дату») вела бы прямо во
    второй отказ: модель выполняет требование, и её за это отклоняют."""
    import gp_context as g
    last = {"Insulin": "2022-03-14"}
    assert g.absent_claims_contradicted("инсулин — 6.0 (2022-03-14), с тех пор не сдавался", last) == []
    assert {h["test"] for h in g.absent_claims_contradicted("инсулин не сдавался вообще", last)} == {"Insulin"}


def test_absent_claim_catches_otsutstvuet_but_not_reference():
    """«Отсутствует полностью» — та же ложь (13.09 сторож её пропускал).
    «Референс отсутствует» — про бланк, не про наличие анализа."""
    import gp_context as g
    last = {"Insulin": "2022-03-14", "Lipase": "2026-08-24"}
    hits = g.absent_claims_contradicted("инсулин отсутствует полностью", last)
    assert {h["test"] for h in hits} == {"Insulin"}
    assert g.absent_claims_contradicted("липаза 35.0, референс отсутствует", last) == []


def test_guard_accumulates_facts_between_attempts(db, clock):
    """Вторая поправка обязана нести факты И первой попытки.

    13.09: попытка 1 возражала про PSA, попытка 2 — про HOMA_IR и Insulin; вторая
    ничего не знала о первой, и отказ пришёл на НОВОМ наборе. Клиент — мок: §20,
    зелёный причинён тестом, а не сетью."""
    clock.set("2026-09-13")
    db.add_lab_result("2026-08-24", "PSA", 0.8, unit="ng/ml")
    db.add_lab_result("2022-03-14", "Insulin", 6.0, unit="мкЕд/мл")
    import gp_agent

    seen_prompts = []
    replies = ["инсулин не сдавался вообще", "всё в порядке, претензий нет"]

    class _Msgs:
        def create(self, **kw):
            seen_prompts.append(kw["messages"][0]["content"])
            text = replies.pop(0)
            return type("R", (), {"content": [type("C", (), {"text": text})()]})()

    client = type("Cl", (), {"messages": _Msgs()})()
    out = gp_agent._guard_absent_claims("psa не измерялся", "КОНТЕКСТ", "SYS",
                                        client, "m", 100, "weekly")
    assert out == "всё в порядке, претензий нет"
    assert "PSA" in seen_prompts[0]
    # вторая поправка помнит PSA и знает про инсулин
    assert "PSA" in seen_prompts[1] and "Insulin" in seen_prompts[1]


def test_guard_rejects_with_readable_reason(db, clock):
    """Отказ обязан объяснять: что утверждалось и чем опровергнуто (решение владельца)."""
    clock.set("2026-09-13")
    db.add_lab_result("2022-03-14", "HOMA_IR", 1.2)
    import gp_agent, pytest as _pt

    class _Msgs:
        def create(self, **kw):
            return type("R", (), {"content": [type("C", (), {"text": "homa-ir не сдавался"})()]})()

    client = type("Cl", (), {"messages": _Msgs()})()
    with _pt.raises(gp_agent._AbsentClaimRejected) as e:
        gp_agent._guard_absent_claims("homa-ir не сдавался", "К", "S", client, "m", 100, "weekly")
    msg = str(e.value)
    assert "`HOMA_IR`" in msg          # code-span: подчёркивание не ломает Markdown Telegram
    assert "2022-03-14" in msg and "в отчёте:" in msg


def test_no_data_in_window_is_a_lie_when_the_row_is_in_the_window(db, clock):
    """Вымышленная свежая строка опровергает отсутствие в окне.
    Для старой строки такое же ограниченное утверждение остаётся честным."""
    clock.set("2032-04-12")
    import gp_context as g
    last = {"Ferritin": "2032-04-02", "TSH": "2027-02-09"}
    hits = g.absent_claims_contradicted("ферритин — нет данных в окне", last, window_days=730)
    assert {h["test"] for h in hits} == {"Ferritin"}
    assert hits[0]["kind"] == "в окне"
    # про аналит СТАРШЕ окна та же фраза — правда, и сторож молчит
    assert g.absent_claims_contradicted("ТТГ — нет данных за 730 дней", last, window_days=730) == []
    # без границы сторож её не знает и не судит
    assert g.absent_claims_contradicted("ферритин — нет данных в окне", last) == []


def test_window_lie_and_never_lie_do_not_double_count(db, clock):
    """Одна клауза — одна претензия на аналит, даже если совпали обе формулировки."""
    clock.set("2026-09-13")
    import gp_context as g
    last = {"PSA": "2026-08-24"}
    hits = g.absent_claims_contradicted("psa не сдавался, нет данных", last, window_days=730)
    assert len(hits) == 1


def test_window_qualified_claim_about_old_row_is_truth_not_a_hit(db, clock):
    """Вымышленные старые записи не опровергают отсутствие в объявленном окне.
    Такое же утверждение о свежей записи должно дать находку."""
    clock.set("2032-04-12")
    import gp_context as g
    last = {"TSH": "2027-02-09", "Ferritin": "2027-02-09", "Albumin": "2032-04-02"}
    assert g.absent_claims_contradicted(
        "ТТГ и ферритин по-прежнему отсутствуют в окне", last, window_days=730) == []
    # а про аналит со строкой ВНУТРИ окна та же ограниченная фраза — ложь
    hits = g.absent_claims_contradicted(
        "альбумин — нет данных за последние 730 дней", last, window_days=730)
    assert {h["test"] for h in hits} == {"Albumin"} and hits[0]["kind"] == "в окне"


def test_unqualified_no_data_is_judged_against_the_whole_canon(db, clock):
    """Безграничное «нет данных» — утверждение о каноне, и строка вне окна его опровергает."""
    clock.set("2032-04-12")
    import gp_context as g
    last = {"TSH": "2027-02-09"}
    hits = g.absent_claims_contradicted("по ТТГ нет данных", last, window_days=730)
    assert {h["test"] for h in hits} == {"TSH"} and hits[0]["kind"] == "никогда"


# ── _check_lab_freshness: «нет данных» только когда строк нет вообще (21.09) ──

def test_freshness_matches_schedule_name_to_canon(db, clock):
    """Имя расписания и синоним в каноне обозначают один вымышленный анализ.
    Мутация: буквальное сравнение имён даёт ложное отсутствие."""
    clock.set("2032-04-12")
    db.add_lab_result("2031-04-17", "TSH", 2.35)
    import labs_db
    labs_db.upsert_monitoring_rule("ТТГ", 180, priority="medium", source="manual")
    import gp_context
    block, _ = gp_context._check_lab_freshness(labs_db.get_recent_labs(730), date(2032, 4, 12))
    # остальные аналиты дефолтного расписания в фикстуре законно без строк — судим одну
    line = [l for l in block.split("\n") if "ТТГ" in l]
    assert not any("нет данных" in l.lower() for l in line), line


def test_freshness_names_old_row_by_date_not_absence(db, clock):
    """Строка старше среза — просрочка с числом дней, а не «НЕТ ДАННЫХ»."""
    clock.set("2032-04-12")
    db.add_lab_result("2027-02-09", "TSH", 2.35)
    import labs_db
    labs_db.upsert_monitoring_rule("TSH", 365, priority="high", source="manual")
    import gp_context
    block, _ = gp_context._check_lab_freshness(labs_db.get_recent_labs(730), date(2032, 4, 12))
    line = [l for l in block.split("\n") if " TSH " in l]
    assert line and "просрочен" in line[0] and "НЕТ ДАННЫХ" not in line[0], line
    critical = [l for l in block.split("\n") if l.startswith("ОТСУТСТВУЮТ")]
    assert not any("TSH" in l for l in critical), critical
