"""Golden-паритет safety_net (safety-net-thresholds E1, этап 2/3).

Страж механического выноса порогов из литералов в БД. Фиксирует ТОЧНЫЙ выхлоп
трёх check-функций на контролируемых данных. Зелёный до рефактора (литералы) и
ОБЯЗАН остаться зелёным после (reader читает absolute_thresholds/lab_trend_thresholds
через fallback→_FALLBACK). Значения = прежние → набор алертов идентичен.

Это РЕЗЕРВ-путь (mock DB-слоя данных, get_conn не зовётся на MacBook → reader
деградирует к _FALLBACK). Доказывает: резерв даёт правильные пороги. БД-путь
(reader реально читает засеянную absolute_thresholds) проверяет отдельный тест
на фикстуре db (test_safety_net_db_path).

Позитивный контроль: сдвинь _FALLBACK_LAB['CEA'] high_urgent 10→99 → CEA-URGENT
исчезнет, тест покраснеет.

ПЕРЕСМОТР 2026-09-02 (нить norm-from-documents). Golden фиксировал выхлоп ПРИДУМАННЫХ
порогов (CEA 5/10/20 — в CTCAE онкомаркеров нет, число было из памяти модели). Теперь:
HGB 9 → URGENT — грейд 2 анемии CTCAE (<10–8 g/dL), число из документа; CEA 16 без
референса на бланке — абсолютного порога НЕТ (документа нет), с референсом 0–5 → WARN
(вид 1, бланк); CEA 10→16 (+60%) → URGENT по тренду: RCV из EFLM ≈ 18%.
Второе исправление: мок get_recent_labs отдавал 3 строки CEA, а реальная функция —
одну на тест (GROUP BY): golden был зелёным на лжи мока, тренды в бою не срабатывали.
Теперь тренд читает серию ПО ВЕЩЕСТВУ (labs_db.get_lab_trend_by_component) и мокается именно он; без отображения — WARN «не отображён», не тишина.
"""
from __future__ import annotations

from datetime import date

import pytest

import safety_net as sn

pytestmark = pytest.mark.unit

TARGET = date(2026, 7, 16)

# Лаб-данные: CEA рост 10→10→16 (+60%, trend URGENT) + latest 16 (abs URGENT);
# HGB 9 (floor URGENT low).
_LABS = {
    "CEA": [
        {"test_name": "CEA", "value": 10.0, "date": "2026-01-10", "ref_low": 0.0, "ref_high": 5.0, "unit": "ng/mL"},
        {"test_name": "CEA", "value": 10.0, "date": "2026-03-10", "ref_low": 0.0, "ref_high": 5.0, "unit": "ng/mL"},
        {"test_name": "CEA", "value": 16.0, "date": "2026-06-10", "ref_low": 0.0, "ref_high": 5.0, "unit": "ng/mL"},
    ],
    "HGB": [{"test_name": "HGB", "value": 9.0, "date": "2026-06-10", "ref_low": 13.5, "ref_high": 17.5, "unit": "g/dL"}],
}


def _fake_get_recent_labs(days, names=None):
    """Как настоящая: ОДНА (последняя) строка на тест."""
    rows = [sorted(v, key=lambda r: r["date"])[-1] for v in _LABS.values()]
    if names:
        rows = [r for r in rows if r["test_name"] in names]
    return rows


def _fake_get_lab_series(name, days=730, exclude_pro=True):
    return sorted(_LABS.get(name, []), key=lambda r: r["date"])


def _patch_all(monkeypatch, day, stats30):
    monkeypatch.setattr(sn.db, "init_db", lambda *a, **k: None)
    # РЕЗЕРВ-путь явно: на машине с dev-копией БД rules_db читал бы её (и её старые строки) —
    # golden обязан судить снимок документа, а не состояние чьей-то локальной базы
    import rules_db
    monkeypatch.setattr(rules_db, "get_absolute_thresholds", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("golden: БД выключена")))
    monkeypatch.setattr(rules_db, "get_lab_trend_thresholds", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("golden: БД выключена")))
    sn._FALLBACK_ALERTED.clear()
    monkeypatch.setattr(sn, "_emit_fallback_alert", lambda *a, **k: None)
    monkeypatch.setattr(sn.db, "get_recent_labs", _fake_get_recent_labs)
    import labs_db
    # путь «по веществу»: мок отдаёт точки серии как get_lab_trend_by_component
    monkeypatch.setattr(labs_db, "get_lab_trend_by_component",
                        lambda name, *a, **k: {"points": _fake_get_lab_series(name) or None, "unresolved": []})
    monkeypatch.setattr(sn.db, "get_day", lambda _s: day)
    monkeypatch.setattr(sn.db, "get_stats", lambda *_a, **_k: stats30)
    monkeypatch.setattr(sn, "_personal_floor", lambda *a, **k: None)


@pytest.mark.owner_data
def test_golden_lab_abs(monkeypatch):
    _patch_all(monkeypatch, {}, {})
    alerts = sn.check_lab_alerts(TARGET)
    by = {a["metric"]: a for a in alerts}
    assert by["CEA"]["level"] == sn.WARN and by["CEA"]["direction"] == "high"   # вид 1: выше референса бланка
    assert by["HGB"]["level"] == sn.URGENT and by["HGB"]["direction"] == "low"  # CTCAE grade 2


@pytest.mark.owner_data
def test_golden_lab_trend(monkeypatch):
    """CEA 10→10→16: рост есть (+60 %), но только по ОДНОМУ забору относительно 10 —
    EGTM 2014 (schedules.json::rules): подтвердить повторным → WARN, не URGENT (2026-09-03)."""
    _patch_all(monkeypatch, {}, {})
    alerts = sn.check_lab_trends(TARGET)
    cea = [a for a in alerts if a["metric"] == "CEA"]
    assert cea and cea[0]["level"] == sn.WARN and "подтвердить повторным" in cea[0]["note"]
    assert cea[0]["pct_change"] == pytest.approx(60.0)


@pytest.mark.owner_data
def test_golden_lab_trend_confirmed_rise_keeps_level(monkeypatch):
    """CEA 10→14→18: последняя пара 14→18 (+29 %) выше RCV 17.8 %, и обе последние точки
    (+40 %, +80 %) выше порога относительно исходных 10 — рост подтверждён вторым
    забором → уровень порога (URGENT)."""
    _patch_all(monkeypatch, {}, {})
    import labs_db
    pts = [{"date": "2026-01-10", "value": 10.0, "unit": "ng/mL", "ref_low": 0.0, "ref_high": 5.0},
           {"date": "2026-03-10", "value": 14.0, "unit": "ng/mL", "ref_low": 0.0, "ref_high": 5.0},
           {"date": "2026-06-10", "value": 18.0, "unit": "ng/mL", "ref_low": 0.0, "ref_high": 5.0}]
    monkeypatch.setattr(labs_db, "get_lab_trend_by_component",
                        lambda name, *a, **k: {"points": pts if name == "CEA" else None, "unresolved": []})
    cea = [a for a in sn.check_lab_trends(TARGET) if a["metric"] == "CEA"]
    assert cea and cea[0]["level"] == sn.URGENT and "подтвердить повторным" not in cea[0]["note"]


@pytest.mark.owner_data
def test_confirmation_rule_is_data_not_code(monkeypatch):
    """Правило читается из документа: пустой набор метрик → CEA 10→10→16 снова URGENT
    (негативный контроль — поведение до 03.09), и наоборот, метрика вне списка (HGB
    падение по одной паре) уровень не теряет."""
    _patch_all(monkeypatch, {}, {})
    import norm_documents
    live = norm_documents.confirmation_metrics()
    assert {"CEA", "CA19-9"} <= live and "HGB" not in live, "правило EGTM — про онкомаркеры"
    monkeypatch.setattr(norm_documents, "confirmation_metrics", lambda: set())
    cea = [a for a in sn.check_lab_trends(TARGET) if a["metric"] == "CEA"]
    assert cea and cea[0]["level"] == sn.URGENT, "без правила — как до 03.09: одна пара решает"


def test_golden_lifestyle(monkeypatch):
    day = {"spo2": {"avg": 91.0}, "readiness_score": 25,
           "sleep": {"sleep_score": 38}, "hrv": {"avg": 36.0}}
    _patch_all(monkeypatch, day, {"avg_hrv": 60.0})
    alerts = sn.check_lifestyle_alerts(TARGET)
    by = {a["metric"]: a["level"] for a in alerts}
    assert by.get("SpO2") == sn.URGENT
    assert by.get("Readiness") == sn.URGENT
    assert by.get("Sleep score") == sn.URGENT
    assert by.get("HRV") == sn.URGENT


def test_golden_healthy_silent(monkeypatch):
    """Негат-контроль: здоровый день + нет тревожных лабов → пусто (кроме, возможно,
    CEA-тренда — данные _LABS всегда содержат рост; здесь мокаем лабы пустыми)."""
    monkeypatch.setattr(sn.db, "get_recent_labs", lambda *a, **k: [])
    import labs_db
    monkeypatch.setattr(labs_db, "get_lab_trend_by_component", lambda *a, **k: {"points": None, "unresolved": []})
    monkeypatch.setattr(sn.db, "init_db", lambda *a, **k: None)
    monkeypatch.setattr(sn, "_personal_floor", lambda *a, **k: None)
    day = {"spo2": {"avg": 98.0}, "readiness_score": 82,
           "sleep": {"sleep_score": 85}, "hrv": {"avg": 60.0}}
    monkeypatch.setattr(sn.db, "get_day", lambda _s: day)
    monkeypatch.setattr(sn.db, "get_stats", lambda *a, **k: {"avg_hrv": 62.0, "avg_rhr": 55.0})
    assert sn.check_lab_alerts(TARGET) == []
    assert sn.check_lifestyle_alerts(TARGET) == []


# ── Придуманный сценарий смены лаборатории ─────────────────────────────────────

@pytest.mark.owner_data
def test_trend_across_labs_is_warn_by_uln_share(monkeypatch):
    """Две точки из РАЗНЫХ лабораторий (референс 39 → 34): RCV неприменим, сравнение по доле
    от ULN и не выше warn. Синтетика той же формы: референс 0–39 → 0–34."""
    _patch_all(monkeypatch, {}, {})
    import labs_db
    series = [
        {"test_name": "CA19-9", "value": 19.0, "date": "2026-04-12", "ref_low": 0.0, "ref_high": 39.0, "unit": "U/mL"},
        {"test_name": "CA19-9", "value": 28.0, "date": "2026-07-03", "ref_low": 0.0, "ref_high": 34.0, "unit": "U/mL"},
    ]
    monkeypatch.setattr(labs_db, "get_lab_trend_by_component", lambda name, *a, **k: {"points": series if name == "CA19-9" else None, "unresolved": []})
    alerts = [a for a in sn.check_lab_trends(TARGET) if a["metric"] == "CA19-9"]
    assert alerts and alerts[0]["level"] == sn.WARN and alerts[0]["same_lab"] is False
    assert "РАЗНЫЕ лаборатории" in alerts[0]["note"]
    # печатаются ОБА: сырые +47 %, по доле от ULN 19/39 → 28/34 = +69 %; pct_change — сырые
    assert alerts[0]["pct_change"] == pytest.approx(47.4, abs=0.5)
    assert "+47 %" in alerts[0]["note"] and "+69 %" in alerts[0]["note"]


@pytest.mark.owner_data
def test_trend_same_lab_keeps_level(monkeypatch):
    _patch_all(monkeypatch, {}, {})
    import labs_db
    series = [
        {"test_name": "CA19-9", "value": 25.0, "date": "2026-01-12", "ref_low": 0.0, "ref_high": 39.0, "unit": "U/mL"},
        {"test_name": "CA19-9", "value": 32.0, "date": "2026-04-12", "ref_low": 0.0, "ref_high": 39.0, "unit": "U/mL"},
        {"test_name": "CA19-9", "value": 36.0, "date": "2026-07-03", "ref_low": 0.0, "ref_high": 39.0, "unit": "U/mL"},
    ]
    monkeypatch.setattr(labs_db, "get_lab_trend_by_component", lambda name, *a, **k: {"points": series if name == "CA19-9" else None, "unresolved": []})
    alerts = [a for a in sn.check_lab_trends(TARGET) if a["metric"] == "CA19-9"]
    assert alerts and alerts[0]["level"] == sn.URGENT and alerts[0]["same_lab"] is True
    # та же лаборатория, но рост виден лишь по одному забору → подтвердить (EGTM 2014)
    monkeypatch.setattr(labs_db, "get_lab_trend_by_component", lambda name, *a, **k: {"points": series[1:] if name == "CA19-9" else None, "unresolved": []})
    alerts = [a for a in sn.check_lab_trends(TARGET) if a["metric"] == "CA19-9"]
    assert alerts and alerts[0]["level"] == sn.WARN and "подтвердить повторным" in alerts[0]["note"]


@pytest.mark.owner_data
def test_trend_across_labs_triggers_on_either_pct(monkeypatch):
    """Сырые +5 % (ниже RCV 11 %), по доле от ULN +20 % (выше) → срабатывает: любой из двух.
    Значения у границы (≥ 0.8·ULN) — пол по величине пройден."""
    _patch_all(monkeypatch, {}, {})
    import labs_db
    series = [
        {"test_name": "CA19-9", "value": 30.0, "date": "2026-04-12", "ref_low": 0.0, "ref_high": 39.0, "unit": "U/mL"},
        {"test_name": "CA19-9", "value": 31.5, "date": "2026-07-03", "ref_low": 0.0, "ref_high": 34.0, "unit": "U/mL"},
    ]
    monkeypatch.setattr(labs_db, "get_lab_trend_by_component", lambda name, *a, **k: {"points": series if name == "CA19-9" else None, "unresolved": []})
    alerts = [a for a in sn.check_lab_trends(TARGET) if a["metric"] == "CA19-9"]
    assert alerts and alerts[0]["level"] == sn.WARN


@pytest.mark.owner_data
def test_trend_unknown_lab_is_warn(monkeypatch):
    """Референс не напечатан → лаборатория неизвестна → консервативно warn."""
    _patch_all(monkeypatch, {}, {})
    import labs_db
    series = [
        {"test_name": "CEA", "value": 2.0, "date": "2026-01-10", "unit": "ng/mL"},
        {"test_name": "CEA", "value": 3.0, "date": "2026-06-10", "unit": "ng/mL"},
    ]
    monkeypatch.setattr(labs_db, "get_lab_trend_by_component", lambda name, *a, **k: {"points": series if name == "CEA" else None, "unresolved": []})
    alerts = [a for a in sn.check_lab_trends(TARGET) if a["metric"] == "CEA"]
    assert alerts and alerts[0]["level"] == sn.WARN and "не определена" in alerts[0]["note"]


@pytest.mark.owner_data
def test_trend_uses_component_points_when_mapped(monkeypatch):
    """Отображение есть → серия по веществу (единицы сведены), референс едет с точкой."""
    _patch_all(monkeypatch, {}, {})
    import labs_db
    pts = [
        {"date": "2026-04-12", "value": 28.0, "unit": "U/mL", "ref_low": 0.0, "ref_high": 39.0},
        {"date": "2026-07-03", "value": 33.0, "unit": "U/mL", "ref_low": 0.0, "ref_high": 34.0},
    ]
    monkeypatch.setattr(labs_db, "get_lab_trend_by_component",
                        lambda name, *a, **k: {"points": pts if name == "CA19-9" else None, "unresolved": []})
    alerts = [a for a in sn.check_lab_trends(TARGET) if a["metric"] == "CA19-9"]
    assert alerts and alerts[0]["same_lab"] is False and alerts[0]["level"] == sn.WARN
    unmapped = [a for a in sn.check_lab_trends(TARGET) if a["metric"] == "CEA"]
    assert unmapped and "не отображён" in unmapped[0]["note"], "пустая карта обязана быть видна, не молчать"


def _ca(v1, v2, hi=39.0, lo=0.0):
    return [{"date": "2025-05-14", "value": v1, "unit": "U/mL", "ref_low": lo, "ref_high": hi},
            {"date": "2026-07-14", "value": v2, "unit": "U/mL", "ref_low": lo, "ref_high": hi}]


@pytest.mark.owner_data
def test_near_boundary_floor_silences_bottom_of_range(monkeypatch):
    """Слово владельца 03.09: «колебания у границы нормы в пределах 20 % от неё должны приходить»,
    ниже — нет. Пример: 1.5→2.7 при ULN 39 (+80 %) — тишина; 30→36 (≥ 0.8·ULN) — сигнал."""
    _patch_all(monkeypatch, {}, {})
    import labs_db
    monkeypatch.setattr(labs_db, "get_lab_trend_by_component",
                        lambda name, *a, **k: {"points": _ca(1.5, 2.7) if name == "CA19-9" else None, "unresolved": []})
    assert not [a for a in sn.check_lab_trends(TARGET) if a["metric"] == "CA19-9"]
    monkeypatch.setattr(labs_db, "get_lab_trend_by_component",
                        lambda name, *a, **k: {"points": _ca(30.0, 36.0) if name == "CA19-9" else None, "unresolved": []})
    hit = [a for a in sn.check_lab_trends(TARGET) if a["metric"] == "CA19-9"]
    assert hit and hit[0]["pct_change"] == pytest.approx(20.0)


@pytest.mark.owner_data
def test_near_boundary_floor_only_for_zero_based_ranges(monkeypatch):
    """Прочтение (а): пол действует лишь при нижней границе 0/нет. Двусторонний интервал —
    как прежде (тот же 1.5→2.7 с ref_low 0.5 → сигнал есть). Без референса — громко, как было."""
    _patch_all(monkeypatch, {}, {})
    import labs_db
    monkeypatch.setattr(labs_db, "get_lab_trend_by_component",
                        lambda name, *a, **k: {"points": _ca(1.5, 2.7, lo=0.5) if name == "CA19-9" else None, "unresolved": []})
    assert [a for a in sn.check_lab_trends(TARGET) if a["metric"] == "CA19-9"]
    monkeypatch.setattr(labs_db, "get_lab_trend_by_component",
                        lambda name, *a, **k: {"points": _ca(1.5, 2.7, hi=None, lo=None) if name == "CA19-9" else None, "unresolved": []})
    loud = [a for a in sn.check_lab_trends(TARGET) if a["metric"] == "CA19-9"]
    assert loud and "не определена" in loud[0]["note"]


@pytest.mark.owner_data
def test_near_boundary_share_is_data(monkeypatch):
    """Число живёт в строке порога (near_boundary_share), не в коде: без него — прежнее поведение."""
    _patch_all(monkeypatch, {}, {})
    import labs_db
    stripped = {m: {k: v for k, v in cfg.items() if k != "near_boundary_share"} for m, cfg in sn._FALLBACK_LAB_TREND.items()}
    monkeypatch.setattr(sn, "_load_lab_trend_thresholds", lambda: stripped)
    monkeypatch.setattr(labs_db, "get_lab_trend_by_component",
                        lambda name, *a, **k: {"points": _ca(1.5, 2.7) if name == "CA19-9" else None, "unresolved": []})
    assert [a for a in sn.check_lab_trends(TARGET) if a["metric"] == "CA19-9"], "без пола — как до 03.09"


@pytest.mark.owner_data
def test_unmeasured_analyte_is_silent_but_unmapped_is_loud(monkeypatch):
    """Два «нет тренда» различаются (2026-09-03): n_rows=0 — у тенанта аналит не мерялся,
    молчим; строки есть, отображения нет — WARN «не отображён». Мок без n_rows — громко."""
    _patch_all(monkeypatch, {}, {})
    import labs_db
    def _comp(name, *a, **k):
        if name == "Albumin":
            return {"points": None, "unresolved": [], "n_rows": 0}
        if name == "CEA":
            return {"points": None, "unresolved": [], "n_rows": 2}
        return {"points": None, "unresolved": []}
    monkeypatch.setattr(labs_db, "get_lab_trend_by_component", _comp)
    by = {a["metric"]: a for a in sn.check_lab_trends(TARGET)}
    assert "Albumin" not in by, "не мерялся — не находка"
    assert "не отображён" in by["CEA"]["note"]
    assert all("не отображён" in a["note"] for m, a in by.items() if m not in ("CEA",)), "без n_rows — как раньше"
