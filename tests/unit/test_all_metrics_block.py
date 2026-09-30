"""BL-DATA-PARITY-1: каждый врач видит каждый собранный показатель.

До 24.09 у каждого врачебного контекста был свой ручной список колонок daily_metrics
(8+ списков), и около 20 колонок писались, но не доходили ни до одного промпта: ходьба,
VO2max, время стоя, дыхание во сне, состав тела… Решение владельца: «все данные у всех
должны использоваться одинаково», «должны видеть все эти метрики все врачи».

Оракул: строка, где заполнена КАЖДАЯ колонка таблицы (список из PRAGMA, не литерал),
и сборщик каждого врачебного контекста обязан показать подпись каждой колонки.
Новая колонка без подписи или врач без блока — красное. Негативный контроль ниже
доказывает, что проверка краснеет, когда колонка из блока выпадает.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

pytestmark = pytest.mark.unit

END = date(2026, 9, 20)


def _fill_every_column(db):
    import metrics_db
    with db.conn() as c:
        info = {r[1]: r[2] for r in c.execute("PRAGMA table_info(daily_metrics)")}
    for i in range(3):
        d = END - timedelta(days=i)
        row = {}
        for col in metrics_db.metric_columns():
            if col in metrics_db._CLOCK_COLUMNS:
                row[col] = f"{d}T23:1{i}:00+03:00"
            elif info[col].upper() == "TEXT":
                row[col] = "solid"
            else:
                row[col] = 2.5 + i
        db.add_daily_metrics(str(d), **row)


def _missing(text: str) -> list[str]:
    import metrics_db
    return [c for c in metrics_db.metric_columns()
            if metrics_db.METRIC_LABELS[c][0] not in text]


def test_every_column_has_a_label_and_no_orphans():
    """Без фикстуры db: судим по схеме, которую строит настоящий init_db (per-run БД
    conftest), а не по снимку схемы тестов — снимок до 24.09 отставал на 16 колонок."""
    import metrics_db
    cols = set(metrics_db.metric_columns())
    labels = set(metrics_db.METRIC_LABELS)
    assert cols - labels == set(), (
        f"колонки без подписи: {sorted(cols - labels)} — добавь в metrics_db.METRIC_LABELS, "
        "иначе врачи увидят сырое имя колонки")
    assert labels - cols == set(), f"подписи без колонки: {sorted(labels - cols)}"


def test_every_numeric_column_is_in_the_signal_family():
    """«…и связи тоже» (владелец, 24.09): каждая числовая колонка — член семьи гейта
    корреляций (signal_family v8). Новая числовая колонка без ре-объявления семьи — красное:
    пусть решение «входит или нет» принимается явно, а не молчанием."""
    import metrics_db
    import signal_family as sf
    import health_db
    with health_db.get_conn() as c:
        types = {r[1]: r[2] for r in c.execute("PRAGMA table_info(daily_metrics)")}
    numeric = {k for k in metrics_db.metric_columns() if types[k].upper() != "TEXT"}
    assert numeric - set(sf.DAILY_METRICS) == set(), (
        f"числовые колонки вне семьи: {sorted(numeric - set(sf.DAILY_METRICS))} — "
        "ре-объяви семью (signal_family.yaml, version↑) ДО прогона гейта")


def test_gate05_control_as_pair_member_never_blocks():
    """v8: sleep_inbed — и член семьи, и контроль Z Gate 0.5. Пара с самим контролем даёт
    вырожденную частную; она обязана остаться NaN (не блок), а не числом-шумом."""
    import numpy as np
    import correlation_gate as cg
    for s in range(200):
        r = np.random.default_rng(s)
        x = r.normal(8, 1, 90)
        y = 0.5 * x + r.normal(0, 1, 90)
        raw, par = cg._partial_corr_pearson(x, y, x)
        assert not cg._gate05_suppressed(raw, par), f"сид {s}: частная {par}"


def test_fixture_schema_has_every_real_column(tmp_path):
    """Снимок схемы тестов (tests/fixtures/health_schema.sql) обязан нести все колонки
    daily_metrics, что строит init_db: иначе тесты на фикстуре db слепы к колонке,
    и проверка «врач видит всё» зеленеет на неполной таблице."""
    import sqlite3
    import metrics_db
    from tests.fixtures.db import _build_schema
    p = tmp_path / "s.db"
    _build_schema(p)
    with sqlite3.connect(p) as c:
        snap = set(metrics_db.metric_columns(c))
    real = set(metrics_db.metric_columns())
    assert real - snap == set(), f"в снимке схемы тестов нет колонок: {sorted(real - snap)}"


def _gp_weekly():
    import gp_context
    return gp_context._build_gp_context(END, 7)


def _mdt_and_consult():
    import wellally_consult
    return wellally_consult._build_data_package(end_date=END, period_days=7)


def _consilium():
    import monthly_consilium
    return monthly_consilium._build_consilium_input(END, 30)


def _chat():
    import hai_context
    return hai_context.build_context_block_compact(END)


def _chat_tool_all():
    """Инструмент чата «запросить показатели» без списка = все колонки."""
    import hai_context
    import metrics_db
    out = hai_context._execute_tool("query_metrics", {"date_from": str(END - timedelta(days=5)),
                                                      "date_to": str(END)})
    # инструмент отдаёт имена колонок, не подписи — переводим, чтобы судить одной меркой
    return " ".join(metrics_db.METRIC_LABELS[c][0] for c in metrics_db.metric_columns()
                    if c in out.split("\n")[1].split(" | "))


def _legacy_report():
    import hai_reports
    return hai_reports.build_context_block(END)


def _consult_prep():
    import consult_prep
    return consult_prep._build_vitals_block(str(END - timedelta(days=30)), str(END))


def _checkin():
    """Вечерний чекин: промпт называет себя врачом; у человека без Oura строки кольца пусты."""
    import checkin_agent
    return checkin_agent._build_day_context()


DOCTORS = {
    "evening_checkin": _checkin,
    "gp_weekly_monthly": _gp_weekly,
    "mdt_and_consult": _mdt_and_consult,
    "monthly_consilium": _consilium,
    "chat_context": _chat,
    "chat_query_metrics": _chat_tool_all,
    "hai_reports": _legacy_report,
    "consult_prep": _consult_prep,
}


@pytest.mark.parametrize("name", sorted(DOCTORS))
def test_every_doctor_sees_every_collected_column(db, clock, name):
    clock.set(str(END + timedelta(days=1)))
    db.add_profile("identity.birth_date", value_text="1975-01-01", category="identity")
    _fill_every_column(db)
    text = DOCTORS[name]()
    assert _missing(text) == [], f"{name} не видит: {_missing(text)}"


def _shift_every_numeric_column(db, days=40, shifted=5):
    """База 10 → последние дни 20: устойчивый сдвиг +100% у каждой числовой колонки."""
    import metrics_db
    with db.conn() as c:
        info = {r[1]: r[2] for r in c.execute("PRAGMA table_info(daily_metrics)")}
    num = [c for c in metrics_db.metric_columns() if info[c].upper() != "TEXT"]
    for i in range(days):
        d = END - timedelta(days=days - 1 - i)
        v = 20.0 if i >= days - shifted else 10.0
        db.add_daily_metrics(str(d), **{c: v for c in num})
    return num


def test_daily_brief_watches_every_numeric_column(db, clock):
    """Утренний бриф по замыслу (morning_brief: llm_renders_only, анти-повтор) говорит только
    о сдвигах, отобранных кодом, — сырой блок всех чисел туда не кладётся. Поэтому «врач
    брифа видит всё» = детектор дрейфа следит за каждой числовой колонкой, и сдвиг любой
    из них становится карточкой под общим гейтом."""
    import hai_analysis
    import metrics_db
    clock.set(str(END + timedelta(days=1)))
    num = _shift_every_numeric_column(db)
    legacy = dict(zip(hai_analysis._DRIFT_LEGACY_COLS, hai_analysis.DRIFT_LEGACY_METRICS))
    got = {d["metric"] for d in hai_analysis.detect_metric_drift(target=END)}
    want = {legacy.get(c, c) for c in num}
    assert want - got == set(), f"дрейф не следит за: {sorted(want - got)}"
    text = hai_analysis.format_drift_report(hai_analysis.detect_metric_drift(target=END))
    new = [c for c in num if c not in legacy]
    unlabeled = [c for c in new if metrics_db.METRIC_LABELS[c][0] not in text]
    assert unlabeled == [], f"сдвиг без человеческой подписи: {unlabeled}"


def test_drift_card_carries_label_legacy_unchanged():
    import brief_cards
    new = brief_cards.from_drift({"metric": "walking_speed_avg", "direction": "down",
                                  "delta_pct": -20, "streak_days": 4})
    old = brief_cards.from_drift({"metric": "deep_min", "direction": "down",
                                  "delta_pct": -20, "streak_days": 4})
    assert new.semantic_key == "drift:walking_speed_avg:down"
    assert new.allowed_claims[0].startswith("Скорость ходьбы down")
    assert old.allowed_claims[0].startswith("deep_min down")   # ключи/тексты брифа не сдвинуты


def test_negative_control_dropped_column_is_caught(db, clock, monkeypatch):
    """Колонка, выпавшая из блока, обязана краснить проверку (§20: зелёный без
    исполненного контроля неотличим от зелёного по совпадению)."""
    import metrics_db
    clock.set(str(END + timedelta(days=1)))
    _fill_every_column(db)
    full = metrics_db.render_all_metrics(30, END)
    assert _missing(full) == []
    monkeypatch.setattr(metrics_db, "_NOT_METRICS", ("date", "raw", "vo2max"))
    broken = metrics_db.render_all_metrics(30, END)
    monkeypatch.setattr(metrics_db, "_NOT_METRICS", ("date", "raw"))
    assert _missing(broken) == ["vo2max"]


def test_empty_columns_are_not_printed(db, clock):
    """У тенанта ровно то, что пришло: пустая колонка — не строка «—» в промпте."""
    import metrics_db
    clock.set(str(END + timedelta(days=1)))
    db.add_daily_metrics(str(END), hrv=30.0)
    block = metrics_db.render_all_metrics(30, END)
    assert "ВСР ночью, мс: 30 (20.09)" in block
    assert "VO2max" not in block
    assert metrics_db.render_all_metrics(30, END - timedelta(days=60)) == ""


def test_bedtime_average_does_not_break_at_midnight():
    import metrics_db
    a = metrics_db._clock_minutes("2026-09-01T23:50:00+03:00")
    b = metrics_db._clock_minutes("2026-09-02 00:10:00 +0300")
    assert metrics_db._fmt_clock((a + b) / 2) == "00:00"
