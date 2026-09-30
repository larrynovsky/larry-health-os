"""check_quarantine_stuck — предохранитель не должен превращаться в тихий затор.

Ш3 сделал карантин персистентным. Цена: если вердикт не выносить, находки копятся в pending,
гейт перестаёт публиковать новое — и это выглядит как зрелость («находок нет»), а не как
затор. Датчик ровно против этого. Плюс delivery-гард: сообщение обязано пережить mute-list.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:
    import integrity_tests as it
except Exception as e:  # noqa: BLE001 — health_db-гвард/нет БД: не наша среда
    pytest.skip(f"integrity_tests не импортируется здесь: {e}", allow_module_level=True)

# DDL ЗЕРКАЛИТ боевой (health_db.py), включая CHECK'и. Раньше здесь стояла упрощённая копия
# без ограничений — и именно такое расхождение фикстуры с боевой схемой сделало VG-R4-05
# невидимым: тест жил в мире, где испорченной строки не бывает.
_DDL = """
CREATE TABLE passset_quarantine (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pair TEXT NOT NULL CHECK (trim(pair) <> ''),
    family TEXT NOT NULL CHECK (family IN ('D','A','q_lag')),
    method_epoch TEXT NOT NULL DEFAULT '',
    entered_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
           CHECK (status IN ('pending','admitted','rejected')),
    resolved_at TEXT, resolution TEXT, resolved_by TEXT,
    CHECK (status = 'pending' OR trim(coalesce(resolution,'')) <> ''),
    UNIQUE(pair, method_epoch)
);
"""

# Та же таблица БЕЗ ограничений — какой она лежит на боевой машине, созданная до ревью R4.
# SQLite не умеет ALTER ADD CONSTRAINT, так что этот вариант реален, а не гипотетичен.
_DDL_UNGUARDED = _DDL.replace("CHECK (trim(pair) <> '')", "").replace(
    "CHECK (family IN ('D','A','q_lag'))", "").replace(
    "CHECK (status IN ('pending','admitted','rejected'))", "").replace(
    "CHECK (status = 'pending' OR trim(coalesce(resolution,'')) <> ''),", "")


def _epoch() -> str:
    """ДЕЙСТВУЮЩАЯ эпоха, а не выдуманная 'v7'. Ревью R5 (VG-R5-06): датчик порчи теперь
    отличает pending под чужой эпохой — такая строка невидима читателю веры и публикацию не
    держит. Фикстура с фальшивой эпохой означала бы, что тест живёт в мире, где этого не бывает
    (ровно щель, сделавшая VG-R4-05 невидимым)."""
    import quarantine_db
    return quarantine_db.method_epoch()


def _conn(rows, ddl=_DDL, epoch=None):
    c = sqlite3.connect(":memory:")
    c.executescript(ddl)
    for pair, days_ago, status in rows:
        c.execute("INSERT INTO passset_quarantine "
                  "(pair, family, method_epoch, entered_at, status, resolution, resolved_by) "
                  f"VALUES (?,?,?,datetime('now','-{days_ago} days'),?,?,?)",
                  (pair, "D", epoch or _epoch(), status,
                   None if status == "pending" else "обоснование вердикта",
                   None if status == "pending" else "human"))
    return c


def _run(monkeypatch, conn):
    cap = []
    monkeypatch.setattr(it, "warn", lambda name, detail="": cap.append((name, detail)))
    return cap, it.check_quarantine_stuck(conn=conn)


_CLEAN = {"corrupt": 0, "schema_guarded": True}   # добавлено ревью R4 к возврату датчика


def test_fresh_pending_is_silent(monkeypatch):
    cap, res = _run(monkeypatch, _conn([("a×b", 3, "pending")]))
    assert cap == [] and res == {"pending": 1, "stuck": 0, **_CLEAN}


def test_stuck_pending_warns(monkeypatch):
    cap, res = _run(monkeypatch, _conn([("a×b", 40, "pending"), ("c×d", 2, "pending")]))
    assert len(cap) == 1 and "карантин без вердикта" in cap[0][0]
    assert "a×b" in cap[0][1] and "adjudicate_quarantine" in cap[0][1]
    assert res == {"pending": 2, "stuck": 1, **_CLEAN}


def test_resolved_pair_does_not_count(monkeypatch):
    """Разрешённая пара — не затор, даже если она старая. Иначе датчик кричал бы вечно
    и его научились бы игнорировать."""
    cap, res = _run(monkeypatch, _conn([("a×b", 90, "admitted"), ("c×d", 90, "rejected")]))
    assert cap == [] and res == {"pending": 0, "stuck": 0, **_CLEAN}


# ── VG-R4-05: «pending=0» больше не покрывает собой нечитаемое состояние ──

def test_corrupt_row_warns_even_when_pending_is_zero(monkeypatch):
    """Ключевой оракул находки. Испорченная строка выпадает из выборки читателя веры И из
    счёта застоя — раньше датчик рапортовал `{pending:0, stuck:0}`, то есть «всё чисто»,
    ровно тогда, когда состояние было нечитаемо."""
    c = _conn([("a×b", 3, "pending")], ddl=_DDL_UNGUARDED)
    c.execute("UPDATE passset_quarantine SET status='PENDING'")     # регистр «сломал» фильтр
    cap, res = _run(monkeypatch, c)
    assert res["pending"] == 0, "предпосылка: читателю строка невидима"
    assert res["corrupt"] == 1, "датчику она обязана быть видима"
    assert any("вне контракта" in n for n, _ in cap)


def test_unguarded_live_table_is_reported(monkeypatch):
    """Боевая таблица создана до CHECK'ов. Молчать об этом = «claim сильнее механизма»:
    ограничения объявлены в DDL, а живая таблица ими не защищена."""
    cap, res = _run(monkeypatch, _conn([("a×b", 3, "pending")], ddl=_DDL_UNGUARDED))
    assert res["schema_guarded"] is False
    assert any("без CHECK" in n for n, _ in cap)


def test_missing_table_is_loud_not_empty(monkeypatch):
    """Нет таблицы ≠ карантин пуст. Тихое [] означало бы «всё разрешено», хотя сведений нет."""
    c = sqlite3.connect(":memory:")            # без DDL
    cap, res = _run(monkeypatch, c)
    assert res is None and len(cap) == 1 and "недоступна" in cap[0][0]


def test_warn_is_deliverable():
    from triage_agent import classify_warnings
    out = classify_warnings([("карантин без вердикта: 2 пар > 21д", "детали")])
    assert out and "карантин" in out[0], "warn замьючен триажем — датчик детектирует в пустоту"


# ── VG-R5-06: pending под ЧУЖОЙ эпохой — строка есть, а публикацию она не держит ──
# Ревью R5, воспроизведено независимо: `corrupt_quarantine_rows` проверял эпоху только на NULL.
# Пара под `signal_family_v999` невидима читателю веры (он фильтрует точным совпадением), но
# датчик считал её валидной, а `pending=0` читалось как «карантин пуст». Снова «отсутствие как
# норма» — тот самый класс, ради которого весь этот ремонт.

def test_pending_under_foreign_epoch_is_named(monkeypatch):
    import quarantine_db as Q
    c = _conn([("a×b", 3, "pending")], epoch="signal_family_v999")
    rows = Q.corrupt_quarantine_rows(conn=c)
    assert len(rows) == 1, "строка под чужой эпохой не названа порчей"
    assert "чужой эпохой" in rows[0]["why"] and "signal_family_v999" in rows[0]["why"]
    assert Q.pending_quarantine_pairs(Q.method_epoch(), conn=c) == set(), \
        "предпосылка находки: читатель веры такую строку НЕ видит"
    cap, res = _run(monkeypatch, c)
    assert res["corrupt"] == 1 and cap, "датчик обязан кричать, а не рапортовать «пусто»"


def test_resolved_row_under_old_epoch_is_history_not_corruption(monkeypatch):
    """Позитивный контроль. Смена метода — новый вопрос о той же паре; ЗАКРЫТЫЙ вердикт
    прошлой эпохи остаётся историей и порчей не является. Без этого контроля правило кричало
    бы на каждую смену версии семьи, и его научились бы гасить."""
    import quarantine_db as Q
    c = _conn([("a×b", 90, "admitted")], epoch="signal_family_v6")
    assert Q.corrupt_quarantine_rows(conn=c) == []
    cap, res = _run(monkeypatch, c)
    assert cap == [] and res["corrupt"] == 0
