"""Поведенческий контракт доставки longitudinal-отчёта: read-your-writes через РЕАЛЬНЫЕ writer+readers.

Заземляет доставочный сторож R2 в реальности. Раньше сторожил `test_report_read_total_order.py`,
но он копировал SQL-строки литералами (`_READERS`) и гонял их против своей in-memory таблицы —
расцеплён с продом: снятие `, id DESC` в gp_context / generate_constitutions / integrity_tests
оставляло тот тест зелёным (антипаттерн «2-й assert == литерал», см. память duplicate_value_splitbrain).

Здесь пишем ДВЕ строки за одну дату НАСТОЯЩИМ writer'ом (`longitudinal_analysis.save_to_agent_reports`,
agent_type='longitudinal_analysis' — ровно то, по чему фильтруют читатели) и зовём ТРИ продакшен-функции-
читателя. Если кто-то снимет тай-брейк `id DESC` в любой из них — падает именно этот тест.

Позит.контроль (доказан вручную при внедрении): временно убрать `, id DESC` из любого читателя →
соответствующий assert краснеет. Сторож, зелёный на сломанном порядке, хуже отсутствующего.
"""
from __future__ import annotations

import json


_OLD = "OLDMARK_RYW"
_NEW = "NEWMARK_RYW"
# Одна секунда на обе строки: воспроизводит гонку двух прогонов в одну секунду, при которой
# generate_constitutions (ORDER BY created_at DESC) обязан опереться на id DESC, а не на время.
_TIED_CREATED_AT = "2026-07-12 07:50:00"


def _summary(mark: str, gate_applied: bool) -> dict:
    """JSON-саммари в форме, которую рендерят все три читателя: маркер попадает в вывод
    каждого (data_range.start → generate; top_correlations[0].a → gp_context; gate.marker → integrity)."""
    return {
        "data_range": {"start": mark, "end": "2026", "total_years": 1},
        "top_correlations": [{"a": mark, "b": "steps", "r": 0.5, "p": 0.001}],
        "lab_metric_correlations": [],
        "gate": {"gate_applied": gate_applied, "marker": mark},
    }


def _seed_two_runs_same_date(monkeypatch) -> None:
    """OLD, затем NEW — обе за сегодня, настоящим writer'ом. created_at принудительно равны."""
    import health_db
    import longitudinal_analysis as la

    # Writer захватывает DB_PATH при импорте; если модуль уже в кэше сессии — перенацелим на tmp.
    monkeypatch.setattr(la, "DB_PATH", health_db.DB_PATH)

    # Обе строки гейтованы: с 2026-07-26 негейтованную веру writer вообще не принимает
    # (см. test_writer_refuses_ungated_belief ниже) — тай-брейк проверяется маркером.
    la.save_to_agent_reports(_summary(_OLD, gate_applied=True))
    la.save_to_agent_reports(_summary(_NEW, gate_applied=True))

    # Ничья по created_at (без неё generate_constitutions мог бы выиграть по времени и не
    # проверить id DESC). Дата у обеих строк и так одна (date.today()).
    with health_db.get_conn() as conn:
        conn.execute(
            "UPDATE agent_reports SET created_at=? WHERE agent_type='longitudinal_analysis'",
            (_TIED_CREATED_AT,),
        )
        conn.commit()


def test_generate_constitutions_reads_last_written(db, monkeypatch):
    """generate_constitutions._get_longitudinal_context (ORDER BY created_at DESC, id DESC)."""
    _seed_two_runs_same_date(monkeypatch)
    import generate_constitutions as gc
    out = gc._get_longitudinal_context()
    assert _NEW in out, "конституции обязаны получить ПОСЛЕДНИЙ прогон longitudinal"
    assert _OLD not in out, "старый прогон не должен просачиваться (ничья по created_at → id DESC)"


def test_gp_context_reads_last_written(db, monkeypatch):
    """gp_context._build_longitudinal_correlations_block (ORDER BY date DESC, id DESC)."""
    _seed_two_runs_same_date(monkeypatch)
    import gp_context
    lines = gp_context._build_longitudinal_correlations_block()
    joined = "\n".join(lines)
    assert _NEW in joined, "GP-контекст обязан взять последнюю запись за дату"
    assert _OLD not in joined, "ничья по дате → без id DESC вернулась бы произвольная строка"


def test_integrity_gate_sensor_reads_last_written(db, monkeypatch):
    """integrity_tests.check_longitudinal_gate_applied (ORDER BY date DESC, id DESC).

    Сторож gate_applied обязан читать ПОСЛЕДНИЙ прогон: иначе старый gate_applied=False
    даст ложный алерт (или свежий False спрячется за старым True)."""
    _seed_two_runs_same_date(monkeypatch)
    import integrity_tests
    gate = integrity_tests.check_longitudinal_gate_applied()
    assert gate is not None, "свежий longitudinal есть → сторож обязан вернуть его gate"
    assert gate.get("marker") == _NEW, "сторож прочитал НЕ последнюю запись (сломан тай-брейк)"
    assert gate.get("gate_applied") is True, "последний прогон имеет gate_applied=True"


def test_writer_refuses_ungated_belief(db, monkeypatch):
    """Точка записи отказывает сама (2026-07-26). Негативный контроль к трём тестам выше:
    они доказывают, что читатели берут ПОСЛЕДНЮЮ строку, — этот доказывает, что негейтованная
    строка в БД вообще не появляется. Раньше появлялась и читалась как вера (P1-01)."""
    import health_db
    import longitudinal_analysis as la
    import pytest as _pytest

    monkeypatch.setattr(la, "DB_PATH", health_db.DB_PATH)
    with _pytest.raises(ValueError, match="гейт"):
        la.save_to_agent_reports(_summary("UNGATED", gate_applied=False))
    with health_db.get_conn() as conn:
        n = conn.execute("SELECT COUNT(*) c FROM agent_reports "
                         "WHERE agent_type='longitudinal_analysis'").fetchone()["c"]
    assert n == 0, "отказ обязан не оставлять следа в вере"
