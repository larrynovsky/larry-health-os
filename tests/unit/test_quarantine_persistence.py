"""Карантин переживает стабильные недели и снимается только явным вердиктом (P1-03).

Ключевой тест — `test_quarantine_persists_across_stable_weeks_until_resolution`. До 2026-07-26
он был бы КРАСНЫМ: `_quarantined_pairs` возвращала `entered` последнего снимка, и на второй
неделе пара исчезала из карантина сама. Один стабильный прогон работал как вердикт, которого
никто не выносил.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import quarantine_db as Q

_DDL = """
CREATE TABLE passset_quarantine (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pair TEXT NOT NULL,
    family TEXT NOT NULL,
    method_epoch TEXT NOT NULL DEFAULT '',
    entered_at TEXT NOT NULL DEFAULT (datetime('now')),
    status TEXT NOT NULL DEFAULT 'pending',
    resolved_at TEXT,
    resolution TEXT,
    resolved_by TEXT,
    UNIQUE(pair, method_epoch)
);
"""


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.executescript(_DDL)
    yield c
    c.close()


_E = [{"pair": "sleep_total×hrv", "family": "D"}]


def test_quarantine_persists_across_stable_weeks_until_resolution(conn):
    """Пара вошла на неделе 1. На неделях 2 и 3 состав стабилен — событий прибытия НЕТ.
    Карантин обязан остаться: `stable next run` решением не является."""
    assert Q.queue_quarantine(_E, method_epoch="v7", conn=conn) == 1
    assert Q.pending_quarantine_pairs("v7", conn=conn) == {"sleep_total×hrv"}

    for _ in range(2):                       # две стабильные недели: прибытий нет
        Q.queue_quarantine([], method_epoch="v7", conn=conn)
        assert Q.pending_quarantine_pairs("v7", conn=conn) == {"sleep_total×hrv"}, \
            "карантин снялся сам — вернулась P1-03"

    assert Q.resolve_quarantine("sleep_total×hrv", "admitted", "держится 3 прогона",
                                method_epoch="v7", conn=conn) == 1
    assert Q.pending_quarantine_pairs("v7", conn=conn) == set()


def test_requeue_does_not_resurrect_resolved_pair(conn):
    """Идемпотентность: повторное прибытие той же пары в ту же эпоху не воскрешает вердикт.
    Иначе решение владельца отменялось бы следующим же мерцанием."""
    Q.queue_quarantine(_E, method_epoch="v7", conn=conn)
    Q.resolve_quarantine("sleep_total×hrv", "rejected", "артефакт смены периода",
                         method_epoch="v7", conn=conn)
    assert Q.queue_quarantine(_E, method_epoch="v7", conn=conn) == 0
    assert Q.pending_quarantine_pairs("v7", conn=conn) == set()


def test_method_epoch_reopens_the_question(conn):
    """Смена метода — новый вопрос о той же паре: прошлый вердикт к нему не относится."""
    Q.queue_quarantine(_E, method_epoch="v7", conn=conn)
    Q.resolve_quarantine("sleep_total×hrv", "admitted", "ok", method_epoch="v7", conn=conn)
    assert Q.queue_quarantine(_E, method_epoch="v8", conn=conn) == 1
    assert Q.pending_quarantine_pairs("v8", conn=conn) == {"sleep_total×hrv"}
    assert Q.pending_quarantine_pairs("v7", conn=conn) == set()


def test_verdict_without_reason_is_refused(conn):
    """Вердикт без обоснования — щелчок, а не решение: его прочитает будущий читатель."""
    Q.queue_quarantine(_E, method_epoch="v7", conn=conn)
    with pytest.raises(ValueError, match="обоснования"):
        Q.resolve_quarantine("sleep_total×hrv", "admitted", "   ", method_epoch="v7", conn=conn)
    with pytest.raises(ValueError, match="admitted|rejected"):
        Q.resolve_quarantine("sleep_total×hrv", "maybe", "ну ладно", method_epoch="v7", conn=conn)
    assert Q.pending_quarantine_pairs("v7", conn=conn) == {"sleep_total×hrv"}


def test_resolving_unknown_pair_changes_nothing(conn):
    Q.queue_quarantine(_E, method_epoch="v7", conn=conn)
    assert Q.resolve_quarantine("нет×такой", "admitted", "ok", method_epoch="v7", conn=conn) == 0
    assert Q.pending_quarantine_pairs("v7", conn=conn) == {"sleep_total×hrv"}


def test_rows_carry_age_and_resolution(conn):
    Q.queue_quarantine(_E + [{"pair": "steps×kcal", "family": "D"}], method_epoch="v7", conn=conn)
    Q.resolve_quarantine("steps×kcal", "rejected", "тривиальная пара", method_epoch="v7", conn=conn)
    pend = Q.quarantine_rows("pending", conn=conn)
    assert [r["pair"] for r in pend] == ["sleep_total×hrv"]
    assert pend[0]["age_days"] == 0
    all_rows = Q.quarantine_rows(None, conn=conn)
    res = [r for r in all_rows if r["pair"] == "steps×kcal"][0]
    assert res["status"] == "rejected" and res["resolution"] == "тривиальная пара"


# ── VG-R4-08: «БД занята» и «пары нет» — разные состояния, разные действия оператора ──
# Единственная из восьми находок R4, оставшаяся без стерегущего теста: 2026-07-26
# `grep -rl "R4-08" tests/` был пуст, а починка (коды выхода + ретрай) в CLI уже стояла.
# Авторская проба `scripts/_verify_r4_fixes.py` оракулом не является — её не зовёт ничто.
# Тесты бьют по ПУТИ: настоящий файл БД и настоящая блокировка SQLite, не подменённые
# функции. R3/R4 показали цену обратного: проверка слоя проходит на сломанном пути.
import importlib          # noqa: E402
import threading          # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
adj = importlib.import_module("adjudicate_quarantine")


@pytest.fixture()
def dbfile(tmp_path, monkeypatch):
    """Файловая БД + CLI, подключённый к ней. timeout=0 — ждать блокировку не наша задача,
    ретраем управляет сам CLI, и именно его мы проверяем."""
    path = tmp_path / "health.db"
    c = sqlite3.connect(path)
    c.executescript(_DDL)
    c.execute("INSERT INTO passset_quarantine (pair, family, method_epoch, entered_at) "
              "VALUES ('sleep_total×hrv','D','v7',datetime('now'))")
    c.commit()
    c.close()
    monkeypatch.setattr(Q, "_conn", lambda: sqlite3.connect(path, timeout=0))
    monkeypatch.setattr(adj, "_BACKOFF", 0.01)      # ждать по-настоящему незачем
    return path


def _run(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["adjudicate_quarantine.py", *argv])
    return adj.main()


_VERDICT = ("--pair", "sleep_total×hrv", "--verdict", "admit",
            "--reason", "держится 4 прогона", "--epoch", "v7")


def test_locked_db_is_not_reported_as_missing_pair(dbfile, monkeypatch, capsys):
    """Занятая БД → EXIT_DB и указание, что делать. Не EXIT_NOT_FOUND: иначе оператор идёт
    искать пару, которая на месте, и в итоге учится игнорировать вывод инструмента."""
    lock = sqlite3.connect(dbfile, check_same_thread=False)
    lock.execute("BEGIN EXCLUSIVE")
    try:
        rc = _run(monkeypatch, *_VERDICT)
    finally:
        lock.rollback()
        lock.close()
    err = capsys.readouterr().err
    assert rc == adj.EXIT_DB, f"занятая БД дала код {rc}, а не EXIT_DB"
    assert rc != adj.EXIT_NOT_FOUND
    assert "база недоступна" in err and "подожди и повтори" in err, err


def test_transient_lock_is_survived_not_just_reported(dbfile, monkeypatch, capsys):
    """Ретрай обязан ДОВОДИТЬ вердикт до конца, а не только красиво печатать ожидание.
    Ночной longitudinal держит запись секунды — это и есть штатный случай."""
    lock = sqlite3.connect(dbfile, check_same_thread=False)
    lock.execute("BEGIN EXCLUSIVE")
    threading.Timer(0.15, lambda: (lock.rollback(), lock.close())).start()
    rc = _run(monkeypatch, *_VERDICT)
    assert rc == adj.EXIT_OK, capsys.readouterr().err
    c = sqlite3.connect(dbfile)
    status, resolution = c.execute(
        "SELECT status, resolution FROM passset_quarantine WHERE pair='sleep_total×hrv'"
    ).fetchone()
    c.close()
    assert status == "admitted" and resolution == "держится 4 прогона"


def test_permanent_db_error_is_not_retried(tmp_path, monkeypatch, capsys):
    """«Нет таблицы» повторами не лечится. Молчаливая задержка на пять попыток здесь хуже
    отказа: оператор ждёт, а причина не изменится."""
    empty = tmp_path / "empty.db"
    sqlite3.connect(empty).close()
    monkeypatch.setattr(Q, "_conn", lambda: sqlite3.connect(empty, timeout=0))
    monkeypatch.setattr(adj, "_BACKOFF", 0.01)
    rc = _run(monkeypatch, *_VERDICT)
    err = capsys.readouterr().err
    assert rc == adj.EXIT_DB
    assert "⏳" not in err, f"постоянная ошибка ушла в ретрай: {err}"
    assert "init_db" in err, err


def test_unknown_pair_and_wrong_epoch_both_say_not_found(dbfile, monkeypatch, capsys):
    """Второй конец различения. Чужая эпоха — тот самый вход, на котором CLI был мёртв
    (rowcount=0 всегда): код обязан отличаться от «БД занята», а текст — называть эпоху."""
    assert _run(monkeypatch, "--pair", "нет×такой", "--verdict", "admit",
                "--reason", "x", "--epoch", "v7") == adj.EXIT_NOT_FOUND
    assert _run(monkeypatch, *_VERDICT[:-1], "v6") == adj.EXIT_NOT_FOUND
    assert "эпохи v6" in capsys.readouterr().out
