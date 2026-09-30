"""Простое описание проблемы: пишется только у не закрытых и только при изменении (27.09)."""
import pytest

pytestmark = pytest.mark.unit


def test_dashboard_shows_plain_summary(dashboard_client):
    client, db = dashboard_client
    with db.conn() as c:
        c.execute("INSERT INTO problem_list (problem_id, title, description, status, first_seen, "
                  "last_updated, plain_summary) VALUES ('p1','Т','мед-текст','active','2026-01-01',"
                  "'2026-01-01','простыми словами')")
    r = client.get("/problems")
    assert r.status_code == 200 and "простыми словами" in r.text and "для врача" in r.text


def test_sensor_names_open_problem_without_plain(monkeypatch):
    """Датчик: открытая проблема без простого описания видна; закрытая и описанная — нет."""
    import integrity_tests as I
    monkeypatch.setattr(I.db, "get_problem_list", lambda *a, **k: [
        {"problem_id": "a", "status": "active", "plain_summary": None},
        {"problem_id": "b", "status": "resolved", "plain_summary": None},
        {"problem_id": "c", "status": "watchful_waiting", "plain_summary": "есть"}])
    said = []
    monkeypatch.setattr(I, "warn", lambda label, detail="": said.append(detail))
    assert I.check_problem_plain_summary() == 1
    assert said and "a" in said[0] and "b" not in said[0]
