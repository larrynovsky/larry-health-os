"""Вера — кэш из `daily_metrics`, и у него не было инвалидации.

После изменения `daily_metrics` ранее построенная вера должна устареть.
Оракул проверяет порядок «вера → правка», а не наличие строк в журнале.

Каждый тест ниже назван мутацией, на которой он обязан покраснеть. Две из них —
не гипотетические, а те самые, что чуть не проехали в конструкции:

  М1 · односторонний предикат (только незакаченные записи) — пропускает
       последовательность репарация → вера → откат: живых записей не осталось,
       а вера посчитана на данных, которых больше нет.
  М2 · сравнение сырых строк без приведения часового пояса — `repaired_at` пишется
       в UTC (`datetime('now')`), `generated_at` веры в локальном (`get_now()`),
       расхождение 3 часа, и ошибка идёт в сторону ЛОЖНО-ЗЕЛЁНОГО.
  М3 · слияние «не проверено» и «изменений не было» в один ответ — тогда ослепший
       датчик неотличим от чистых данных.

Оракул часового пояса берётся из ПИТОНОВСКОГО `astimezone()`, а не из того же
`datetime(ts,'localtime')`, которым пользуется код: иначе тест проверял бы согласие
автора с самим собой (независимый оракул).
"""
from __future__ import annotations

import datetime as dt
import json

import pytest

pytestmark = pytest.mark.unit

_UTC_INSTANT = dt.datetime(2032, 4, 12, 9, 0, 0, tzinfo=dt.timezone.utc)


def _utc(when: dt.datetime) -> str:
    return when.astimezone(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _local(when: dt.datetime) -> str:
    return when.astimezone().strftime("%Y-%m-%d %H:%M:%S")


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """Изолированная БД. `data_repair_log` заводится НАСТОЯЩИМ DDL из health_db,
    а не переписанным руками: фикстура, выдуманная по памяти, уже один раз
    подтвердила репарацию, покрывшую половину полей."""
    import health_db as hdb

    monkeypatch.setattr(hdb, "DB_PATH", tmp_path / "t.db")
    with hdb.get_conn() as conn:
        conn.executescript(
            "CREATE TABLE agent_reports (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "date TEXT, agent_type TEXT, findings TEXT);"
        )
    hdb._ensure_data_repair_log()
    return hdb


def _belief(conn, generated_local: str):
    body = {"schema_version": 2, "generated_at": generated_local,
            "top_correlations": [], "gate": {"gate_applied": True, "status": "applied"}}
    conn.execute("INSERT INTO agent_reports (date, agent_type, findings) VALUES (?,?,?)",
                 (generated_local[:10], "longitudinal_analysis",
                  json.dumps(body, ensure_ascii=False)))


def _repair(conn, *, repaired_utc: str, reverted_utc: "str | None" = None):
    conn.execute(
        "INSERT INTO data_repair_log(run_id, repaired_at, home, entity, field, "
        "old_value, new_value, reason, reverted_at) VALUES (?,?,?,?,?,?,?,?,?)",
        ("t", repaired_utc, "sqlite:daily_metrics", "2032-04-01", "sleep_deep",
         json.dumps(0.0), json.dumps(None), "оракул", reverted_utc),
    )


def _read(hdb, receipt=None):
    from belief_contract import read_belief
    with hdb.get_conn() as conn:
        return read_belief(conn, receipt_path=receipt or (hdb.DB_PATH.parent / "nope.json"))


# ── Позитивный контроль: пустой журнал есть ПРОВЕРЕНО, а не «не знаю» (М3) ──

def test_empty_journal_reads_as_checked_and_fresh(db):
    with db.get_conn() as c:
        _belief(c, _local(_UTC_INSTANT))
    b = _read(db)
    assert b["stale_vs_data"] is False, (
        "журнал прочитан и пуст — это ПРОВЕРЕНО. None здесь слил бы «датчик ослеп» "
        "и «данные чистые» в один ответ (М3)"
    )
    assert b["data_changed_at"] is None


def test_missing_journal_reads_as_unknown(db, monkeypatch):
    with db.get_conn() as c:
        _belief(c, _local(_UTC_INSTANT))
        c.execute("DROP TABLE data_repair_log")
    b = _read(db)
    assert b["stale_vs_data"] is None, (
        "журнала нет — сведений нет. False здесь означал бы «данные не менялись», "
        "то есть ложную безопасность (М3)"
    )


# ── Предмет: вера старше правки ────────────────────────────────────────────

def test_repair_before_belief_leaves_belief_fresh(db):
    with db.get_conn() as c:
        _repair(c, repaired_utc=_utc(_UTC_INSTANT - dt.timedelta(days=2)))
        _belief(c, _local(_UTC_INSTANT))
    assert _read(db)["stale_vs_data"] is False


def test_repair_after_belief_marks_stale(db):
    with db.get_conn() as c:
        _belief(c, _local(_UTC_INSTANT - dt.timedelta(days=2)))
        _repair(c, repaired_utc=_utc(_UTC_INSTANT))
    b = _read(db)
    assert b["stale_vs_data"] is True
    assert b["data_changed_at"] == _local(_UTC_INSTANT)


# ── М1: репарация → вера → откат. Живых записей ноль, вера всё равно устарела ──

def test_revert_after_belief_marks_stale_though_nothing_is_live(db):
    repaired = _UTC_INSTANT - dt.timedelta(hours=6)
    belief = _UTC_INSTANT - dt.timedelta(hours=3)
    with db.get_conn() as c:
        _repair(c, repaired_utc=_utc(repaired), reverted_utc=_utc(_UTC_INSTANT))
        _belief(c, _local(belief))
        live = c.execute(
            "SELECT COUNT(*) FROM data_repair_log WHERE reverted_at IS NULL").fetchone()[0]
    assert live == 0, "предпосылка теста: незакаченных записей нет"
    b = _read(db)
    assert b["stale_vs_data"] is True, (
        "М1: односторонний предикат (только незакаченные) здесь скажет «свежая». "
        "Но откат тоже изменил данные — вера посчитана на снимке между двумя правками"
    )
    assert b["data_changed_at"] == _local(_UTC_INSTANT)


# ── М2: расхождение UTC и локального времени не должно прятать правку ──

def test_utc_local_skew_does_not_hide_a_later_repair(db):
    belief_at = _UTC_INSTANT - dt.timedelta(hours=1)
    with db.get_conn() as c:
        _belief(c, _local(belief_at))
        _repair(c, repaired_utc=_utc(_UTC_INSTANT))
    b = _read(db)
    assert b["stale_vs_data"] is True, (
        "М2: правка на час ПОЗЖЕ веры. Сравнение сырых строк видит UTC-метку правки "
        "как более раннюю на величину смещения пояса и молчит — ложно-зелёное"
    )
    if _utc(_UTC_INSTANT) != _local(_UTC_INSTANT):
        assert _utc(_UTC_INSTANT) < _local(belief_at), (
            "предпосылка мутации: именно так и выглядит наивное сравнение — "
            "сырая UTC-метка правки лексикографически МЕНЬШЕ локальной метки веры"
        )


# ── Вера старого формата: только дата, день-в-день считается устаревшей ──

def test_date_only_belief_treats_same_day_change_as_stale(db):
    with db.get_conn() as c:
        _belief(c, _local(_UTC_INSTANT)[:10])
        _repair(c, repaired_utc=_utc(_UTC_INSTANT))
    assert _read(db)["stale_vs_data"] is True, (
        "у веры только дата — час правки внутри дня неизвестен. Направление ошибки "
        "выбрано в сторону лишней перепроверки, а не молчания"
    )


# ── Датчик: устаревание обязано ДОЕХАТЬ до человека, а не осесть в поле dict ──
# Предикат без читателя — это обнаружение без доставки; класс уже стоил проекту
# рельсы warn 13.07. Поэтому ветки датчика проверяются отдельно от предиката.

def _warn_labels(hdb):
    import integrity_tests as it
    it._warnings.clear()
    with hdb.get_conn() as conn:
        it.check_belief_fresh_vs_data(conn)
    return [w[0] for w in it._warnings]


def test_sensor_speaks_when_belief_is_stale(db):
    with db.get_conn() as c:
        _belief(c, _local(_UTC_INSTANT - dt.timedelta(days=2)))
        _repair(c, repaired_utc=_utc(_UTC_INSTANT))
    assert any("вера построена ДО" in lbl for lbl in _warn_labels(db))


def test_sensor_is_silent_when_belief_is_fresh(db):
    with db.get_conn() as c:
        _belief(c, _local(_UTC_INSTANT))
    assert not any("вера" in lbl for lbl in _warn_labels(db)), \
        "негативный контроль: датчик, который варнит всегда, не датчик"


def test_sensor_says_unknown_rather_than_fine(db):
    with db.get_conn() as c:
        _belief(c, _local(_UTC_INSTANT))
        c.execute("DROP TABLE data_repair_log")
    assert any("не проверено" in lbl for lbl in _warn_labels(db)), \
        "журнала нет — датчик обязан сказать «не знаю», а не промолчать как при чистых данных"
