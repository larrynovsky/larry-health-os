"""Вес в профиле берётся из живого источника и несёт дату замера.

Синтетический сценарий: сохранённый профиль расходится с последним
измерением устройства. Давность источника проверяется отдельно от расхождения.

Штамп даты обязателен именно поэтому: без него «устаревшее значение» и «свежее
значение» выглядят одинаково — как число (§18). Профиль читают девять модулей через
`patient_context.build_patient_brief`, включая консилиум.
"""
from __future__ import annotations

import sqlite3

import pytest

import profile_reconciler as pr

pytestmark = pytest.mark.unit


def _db_with(rows):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE daily_metrics (date TEXT PRIMARY KEY, weight REAL)")
    conn.executemany("INSERT INTO daily_metrics (date, weight) VALUES (?,?)", rows)
    return conn


def test_takes_latest_measurement_with_date_stamp():
    """Берётся ПОСЛЕДНИЙ замер, и дата едет вместе с числом."""
    conn = _db_with([("2021-02-02", 68.9), ("2021-03-09", 68.4), ("2021-01-01", 70.0)])
    key, val = pr._reconcile_weight(conn)
    assert key == "identity.weight_kg"
    assert val == "68.4 (замер 2021-03-09)"


def test_stale_source_is_visible_not_silent():
    """Весы молчат месяц — значение то же, но дата это показывает.
    Без даты «свежие 68.4» и «68.4 месячной давности» неразличимы."""
    conn = _db_with([("2020-11-15", 68.4)])
    _key, val = pr._reconcile_weight(conn)
    assert "2020-11-15" in val, "дата пропала — протухание стало невидимым"


def test_no_data_means_silence_not_a_guess():
    """Нет живых замеров — ничего не пишем. Пустой профиль честнее выдуманного."""
    assert pr._reconcile_weight(_db_with([])) is None
    assert pr._reconcile_weight(_db_with([("2021-03-09", None)])) is None
    assert pr._reconcile_weight(_db_with([("2021-03-09", 0)])) is None


def test_weight_lands_in_identity_not_medical():
    """Категория идёт от сверки, а не захардкожена «medical» — иначе вес уехал бы
    в медицинскую категорию, а список сверок раньше жил в ДВУХ ветках reconcile()."""
    by_fn = {fn.__name__: cat for fn, cat in pr._RECONCILERS}
    assert by_fn["_reconcile_weight"] == "identity"
    assert by_fn["_reconcile_port"] == "medical"
    assert len(pr._RECONCILERS) == 3


def test_negative_control_date_stamp_is_load_bearing(monkeypatch):
    """ИСПОЛНЕННЫЙ негативный контроль: возвращаем формат БЕЗ даты — и проверка
    протухания обязана покраснеть. Без этого её зелёный неотличим от зелёного на
    коде, который дату не пишет вовсе (§20).
    """
    real = pr._reconcile_weight
    monkeypatch.setattr(
        pr, "_reconcile_weight",
        lambda conn: ("identity.weight_kg", "68.4"))          # ← поведение до правки
    _key, val = pr._reconcile_weight(_db_with([("2020-11-15", 68.4)]))
    assert "2020-11-15" not in val, "контроль негоден: дата берётся не отсюда"

    monkeypatch.setattr(pr, "_reconcile_weight", real)
    _key2, val2 = pr._reconcile_weight(_db_with([("2020-11-15", 68.4)]))
    assert "2020-11-15" in val2, "признак не восстановился — позитив зеленел не им"


def test_reconcile_writes_weight_end_to_end(tmp_path):
    """Путь целиком: reconcile() кладёт вес в patient_profile с категорией identity.
    Проверяем ЗАПИСЬ, а не только чистую функцию — раньше третья сверка требовала
    правки двух веток и могла молча не доехать до одной из них."""
    db = tmp_path / "t.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE daily_metrics (date TEXT PRIMARY KEY, weight REAL)")
    conn.execute("INSERT INTO daily_metrics VALUES ('2021-03-09', 68.4)")
    conn.execute("CREATE TABLE events (event_type TEXT, effective_date TEXT, "
                 "location TEXT, notes TEXT)")
    conn.execute("CREATE TABLE patient_profile (key TEXT PRIMARY KEY, value_text TEXT, "
                 "value_json TEXT, category TEXT, updated_at TEXT, updated_by TEXT)")
    conn.commit()
    conn.close()

    changes = pr.reconcile(db_path=db)
    assert ("identity.weight_kg" in [k for _a, k, _v in changes]), f"вес не записан: {changes}"

    conn = sqlite3.connect(str(db))
    row = conn.execute("SELECT value_text, category, updated_by FROM patient_profile "
                       "WHERE key='identity.weight_kg'").fetchone()
    conn.close()
    assert row == ("68.4 (замер 2021-03-09)", "identity", "profile_reconciler")


# ── drift() — датчик расхождения профиля с живым источником (Э5) ─────────────
def _db_full(weight_rows, profile_rows):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE daily_metrics (date TEXT PRIMARY KEY, weight REAL)")
    conn.executemany("INSERT INTO daily_metrics (date, weight) VALUES (?,?)", weight_rows)
    conn.execute("CREATE TABLE events (event_type TEXT, effective_date TEXT, "
                 "location TEXT, notes TEXT)")
    conn.execute("CREATE TABLE patient_profile (key TEXT PRIMARY KEY, value_text TEXT, "
                 "value_json TEXT, category TEXT, updated_at TEXT, updated_by TEXT)")
    conn.executemany("INSERT INTO patient_profile (key, value_text) VALUES (?,?)", profile_rows)
    conn.commit()
    return conn


def test_drift_flags_stale_weight_key():
    """Живой источник даёт 68.4 от 2021-03-09, в профиле лежит устаревшее '71' — форма кейса нити.
    drift() обязан назвать ключ, иначе оракула у расхождения нет (строка «НЕТ» в снимке)."""
    conn = _db_full([("2021-03-09", 68.4)], [("identity.weight_kg", "71")])
    assert pr.drift(conn) == ["identity.weight_kg"]


def test_drift_silent_when_in_sync():
    """Хранимое == пересчитанному живому → расхождения нет (фон позитива)."""
    conn = _db_full([("2021-03-09", 68.4)],
                    [("identity.weight_kg", "68.4 (замер 2021-03-09)")])
    assert pr.drift(conn) == []


def test_drift_returns_keys_not_values():
    """§16/§19: едут ИМЕНА ключей, не health-значения. '71'/'68.4' в выдаче нет никогда."""
    conn = _db_full([("2021-03-09", 68.4)], [("identity.weight_kg", "71")])
    out = pr.drift(conn)
    assert out == ["identity.weight_kg"]
    assert not any("68" in x or "71" in x for x in out), "значение утекло в выдачу датчика"


def test_drift_silent_source_is_not_divergence():
    """Весы молчат (нет daily_metrics.weight) — ремонтник вернёт None, сверять не с чем.
    Молчащий источник — НЕ расхождение (его протухание стережёт штамп даты, не drift)."""
    conn = _db_full([], [("identity.weight_kg", "71")])
    assert "identity.weight_kg" not in pr.drift(conn)


def test_drift_negative_control_perimeter_is_reconcilers(monkeypatch):
    """ИСПОЛНЕННЫЙ негативный контроль: опустошаем _RECONCILERS — датчик обязан
    ОСЛЕПНУТЬ (вернуть []) даже при явном расхождении. Без этого его зелёный неотличим
    от зелёного на коде, где периметр захардкожен мимо _RECONCILERS (§17/§20)."""
    conn = _db_full([("2021-03-09", 68.4)], [("identity.weight_kg", "71")])
    assert pr.drift(conn) == ["identity.weight_kg"]        # признак жив
    monkeypatch.setattr(pr, "_RECONCILERS", ())
    assert pr.drift(conn) == [], "периметр берётся не из _RECONCILERS — контроль негоден"
    monkeypatch.undo()
    assert pr.drift(conn) == ["identity.weight_kg"], "признак не восстановился"
