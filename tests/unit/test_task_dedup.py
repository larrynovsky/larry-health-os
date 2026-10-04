"""Судья дублей задач из отчёта врача (нить task-dedup, 02.10).

Судится МЕХАНИЗМ применения вердиктов на временной базе, модель подменена (§20).
Примеры выдуманы: содержимое задач живых людей в репозиторий не едет (§23).

Почему это есть. Ключ fingerprint придумывала модель, и разные недели давали разные
ключи одной просьбы: у партнёра 51 снятая задача — 51 разный ключ. Сверка по ключу
не сработала ни разу, снятое человеком рождалось заново на следующем обзоре.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import task_agent as ta

pytestmark = pytest.mark.unit


# ── разбор ответа модели: пропуск разрешён только проверяемым суждением ──────────

def test_covered_needs_evidence_for_every_request():
    """«Покрыто» выносит код: каждая просьба обязана найтись в старой задаче по номеру.
    Замер 02.10: когда «покрыто» ставила модель, новые анализы панели терялись."""
    rows = [
        {"id": "0", "items": [{"request": "А", "in": [7]}, {"request": "Б", "in": [8]}]},
        {"id": "1", "items": [{"request": "А", "in": [7]}, {"request": "новое", "in": []}]},
        {"id": "2", "items": [{"request": "А", "in": [999]}]},        # номера не было во входе
        {"id": "3", "items": []},                                     # просьб не названо
        {"id": "4", "verdict": "covered", "ids": [7]},               # старый формат без улик
        {"id": "5", "items": [{"request": "А", "in": ["7", "x"]}, "мусор"]},
        {"id": "9", "items": [{"request": "А", "in": [7]}]},          # id не из новых
        "мусор",
    ]
    out = ta._dedup_verdicts_from_rows(rows, {"0", "1", "2", "3", "4", "5"}, {7, 8})
    assert out["0"]["verdict"] == "covered" and out["0"]["ids"] == [7, 8]
    assert [out[k]["verdict"] for k in "12345"] == ["new"] * 5
    assert "9" not in out


# ── множество «уже известно человеку» ─────────────────────────────────────────────

def test_known_tasks_include_open_snoozed_and_recently_dismissed(db):
    import tasks_db
    keep = {
        db.add_task("открытая"),
        db.add_task("отложенная", status="snoozed"),
        db.add_task("снята вчера", status="dismissed"),
    }
    db.execute("UPDATE tasks SET resolved_at=datetime('now','-1 day') WHERE content='снята вчера'")
    old = db.add_task("снята давно", status="dismissed")
    db.execute("UPDATE tasks SET resolved_at=datetime('now','-120 day') WHERE id=?", (old,))
    done = db.add_task("выполнена", status="completed")
    db.execute("UPDATE tasks SET resolved_at=datetime('now','-1 day') WHERE id=?", (done,))

    got = {r["id"] for r in tasks_db.get_tasks_known_to_person(90)}
    assert got == keep


def test_recall_window_lives_in_config(db):
    """Окно — решение владельца, число в данных: пустая база получает seed, правка читается."""
    import config_db
    assert ta.dismissed_recall_days() == ta.RECALL_SEED_DAYS
    config_db.upsert_config(ta.RECALL_KEY, value_num=30.0, category="tasks", source="test")
    assert ta.dismissed_recall_days() == 30


# ── экстрактор + судья на временной базе ──────────────────────────────────────────

class _FakeClient:
    """Отвечает экстрактору списком задач, судье дублей — заданными вердиктами."""

    def __init__(self, tasks, verdicts=None, judge_raises=False):
        self.tasks, self.verdicts, self.judge_raises = tasks, verdicts, judge_raises
        self.messages = SimpleNamespace(create=self._create)
        self.judge_calls = 0
        self.judge_inputs = []

    def _create(self, **kw):
        if kw["system"].startswith(ta.TASK_DEDUP_PROMPT):
            self.judge_calls += 1
            self.judge_inputs.append(kw["messages"][0]["content"])
            if self.judge_raises:
                raise RuntimeError("модель недоступна")
            body = self.verdicts
        else:
            body = self.tasks
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(body, ensure_ascii=False))],
                               stop_reason="end_turn")


@pytest.fixture
def harness(db, monkeypatch):
    closed, faults = [], []
    monkeypatch.setattr(ta, "complete_macos_reminder", lambda tid: closed.append(tid) or True)
    import notify
    monkeypatch.setattr(notify, "fault", lambda tech, **kw: faults.append(tech))
    monkeypatch.setattr(ta, "_notify_specialist", lambda text: None)
    return SimpleNamespace(db=db, closed=closed, faults=faults, mp=monkeypatch)


NEW = [
    {"type": "lab_test", "content": "Сдать показатель А", "fingerprint": "lab:A2"},
    {"type": "lab_test", "content": "Сдать показатели Б и В", "fingerprint": "lab:BV"},
    {"type": "action", "content": "Сделать новое дело", "fingerprint": "action:new"},
]


def _run(h, client):
    h.mp.setattr(ta, "_get_client", lambda: client)
    return ta.extract_tasks_from_report("отчёт", source="gp_weekly", report_date=None)


def test_covered_is_skipped_partial_and_new_are_created(harness):
    h = harness
    a = h.db.add_task("Сдать А", type="lab_test", source="gp_weekly")
    b = h.db.add_task("Сдать Б", type="lab_test", source="gp_weekly")
    client = _FakeClient(NEW, [
        {"id": "0", "items": [{"request": "А", "in": [a]}]},
        {"id": "1", "items": [{"request": "Б", "in": [b]}, {"request": "В", "in": []}]},
        {"id": "2", "items": [{"request": "дело", "in": []}]},
    ])
    saved = _run(h, client)

    contents = [s["content"] for s in saved]
    assert not any("показатель А" in c for c in contents)          # целиком есть — не создана
    assert any("Б и В" in c for c in contents)                      # есть новое — создана
    assert any("новое дело" in c for c in contents)
    # старые не тронуты: сверка ничего не снимает
    assert {r["status"] for r in h.db.fetchall(
        "SELECT status FROM tasks WHERE id IN (?, ?)", (a, b))} == {"open"}
    assert h.closed == []
    # След пропуска — в базе (04.10: у владельца пропуск не оставил следа, приёмка не смогла
    # проверить судью). Строка невидима человеку и не входит в «уже известно».
    trace = h.db.fetchall("SELECT content, resolved_text, judge_verdict FROM tasks "
                          "WHERE status='duplicate'")
    assert len(trace) == 1 and "показатель А" in trace[0]["content"]
    assert f"#{a}" in trace[0]["resolved_text"] and trace[0]["judge_verdict"] == "covered"
    import tasks_db
    assert all(r["status"] != "duplicate" for r in tasks_db.get_tasks_known_to_person(90))


def test_dismissed_by_person_is_not_reborn(harness):
    """Главный случай нити: человек снял — следующий обзор не возвращает."""
    h = harness
    gone = h.db.add_task("Сдать А", type="lab_test", status="dismissed", source="gp_weekly")
    h.db.execute("UPDATE tasks SET resolved_at=datetime('now','-1 day') WHERE id=?", (gone,))
    client = _FakeClient(NEW[:1], [{"id": "0", "items": [{"request": "А", "in": [gone]}]}])
    assert _run(h, client) == []
    assert client.judge_calls == 1


def test_judge_failure_creates_everything_and_is_journaled(harness):
    """FAIL-OPEN: упавший судья стоит дублей, а не недели без задач — и это видно в журнале."""
    h = harness
    h.db.add_task("Сдать А", type="lab_test", source="gp_weekly")
    client = _FakeClient(NEW, judge_raises=True)
    saved = _run(h, client)
    assert len(saved) == 3
    assert len(h.faults) == 1 and "судья дублей" in h.faults[0]


def test_no_known_tasks_no_judge_call(harness):
    h = harness
    client = _FakeClient(NEW, [])
    assert len(_run(h, client)) == 3
    assert client.judge_calls == 0


def test_only_lab_tasks_go_to_the_judge(harness):
    """Решение владельца 02.10 по замеру: на действиях и вопросах судья терял новые
    просьбы врача (9 существенных на 168), на анализах — 1 спорную на 83. Действие
    судье не показывается и создаётся всегда."""
    h = harness
    h.db.add_task("Сделать новое дело", type="action", source="gp_weekly")
    client = _FakeClient(NEW, [{"id": "2", "items": [{"request": "дело", "in": [1]}]}])
    saved = _run(h, client)
    assert any("новое дело" in s["content"] for s in saved)
    assert client.judge_calls >= 1          # без вердиктов на анализы — один переспрос
    for given in client.judge_inputs:
        new_part = given.split("УЖЕ ЕСТЬ:")[0]
        assert "новое дело" not in new_part and "показатель А" in new_part
