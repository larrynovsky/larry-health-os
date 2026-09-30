"""Консилиум из дашборда — фоном, не в HTTP-запросе (2026-08-29), в ОТДЕЛЬНОМ
процессе (2026-08-30).

Дефект 29.08: хендлер eval ждал консилиум 105–170 с; клиент рвал соединение,
uvicorn при disconnected не пишет ответ — 9 консилиумов за день «в пустоту»,
повторные клики плодили лишние прогоны. Дефект 30.08: задача в event loop
гибла при рестарте дашборда post-commit хуком (каждый коммит).

Здесь _spawn_eval подменён потоком, гоняющим hypothesis_resolution.
run_consilium_and_resolve — тот же код, что исполняет subprocess; сам Popen
проверяется отдельно (test_spawn_eval_detaches_process).

Оракул механизма (test_eval_returns_before_consilium_finishes): фейковый
консилиум блокируется на asyncio.Event; POST обязан вернуться, пока событие
НЕ установлено. На старом коде POST висит до таймаута → красный.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time

import pytest

pytestmark = pytest.mark.integration


class _Consilium:
    """Подменяет hypothesis_consilium_eval.evaluate_hypothesis_via_consilium."""

    def __init__(self, verdict: dict | None = None, raise_exc: Exception | None = None):
        self.calls = 0
        self.gate = threading.Event()       # тест открывает из своего потока
        self.verdict = verdict
        self.raise_exc = raise_exc

    async def __call__(self, memory_id: int) -> dict:
        self.calls += 1
        while not self.gate.is_set():       # ждём разрешения теста, не блокируя loop
            await asyncio.sleep(0.02)
        if self.raise_exc:
            raise self.raise_exc
        return dict(self.verdict, memory_id=memory_id)


def _payload(db, hyp_id: int) -> dict:
    return json.loads(db.fetchone("SELECT value FROM memory WHERE id=?", (hyp_id,))["value"])


def _wait_until(pred, timeout=5.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if pred():
            return True
        time.sleep(0.05)
    return False


def _thread_spawn(monkeypatch):
    """subprocess → daemon-поток с тем же run_consilium_and_resolve."""
    import dashboard_routers.api_status_actions as asa
    import hypothesis_resolution as hr
    monkeypatch.setattr(asa, "_spawn_eval", lambda hid: threading.Thread(
        target=hr.run_consilium_and_resolve, args=(hid,), daemon=True).start())


@pytest.fixture
def consilium(monkeypatch):
    import hypothesis_consilium_eval as hce
    fake = _Consilium(verdict={
        "verdict": "partial", "confidence": 0.7, "reasoning": "r",
        "revised_hypothesis": {"observation": "obs v2", "test": "сдать ферритин"},
    })
    monkeypatch.setattr(hce, "evaluate_hypothesis_via_consilium", fake)
    _thread_spawn(monkeypatch)
    return fake


def test_eval_returns_before_consilium_finishes(dashboard_client, consilium):
    client, db = dashboard_client
    hyp_id = db.add_hypothesis({"observation": "obs v1", "status": "open"})

    r = client.post(f"/api/hypotheses/{hyp_id}/eval")     # консилиум ещё заперт
    assert r.status_code == 200
    assert consilium.gate.is_set() is False
    assert f'hx-get="/api/hypotheses/{hyp_id}/card"' in r.text
    assert 'hx-trigger="every 5s"' in r.text
    p = _payload(db, hyp_id)
    assert p["status"] == "testing" and p["eval_started_at"]

    consilium.gate.set()
    assert _wait_until(lambda: _payload(db, hyp_id).get("version") == 1), "фон не применил вердикт"
    card = client.get(f"/api/hypotheses/{hyp_id}/card")
    assert card.status_code == 200
    assert "obs v2" in card.text and "partial" in card.text
    assert "hx-trigger" not in card.text                     # опрос погашен


def test_second_click_while_running_does_not_start_second_consilium(dashboard_client, consilium):
    client, db = dashboard_client
    hyp_id = db.add_hypothesis({"observation": "obs", "status": "open"})
    client.post(f"/api/hypotheses/{hyp_id}/eval")
    r2 = client.post(f"/api/hypotheses/{hyp_id}/eval")
    assert r2.status_code == 200 and "уже идёт" in r2.text
    consilium.gate.set()
    _wait_until(lambda: _payload(db, hyp_id).get("version") == 1)
    assert consilium.calls == 1


def test_stale_lock_allows_restart(dashboard_client, consilium):
    """eval_started_at старше TTL → замок протух, новый запуск разрешён."""
    client, db = dashboard_client
    hyp_id = db.add_hypothesis({"observation": "obs", "status": "testing",
                                "eval_started_at": "2020-01-01T00:00:00"})
    r = client.post(f"/api/hypotheses/{hyp_id}/eval")
    assert "уже идёт" not in r.text
    consilium.gate.set()
    assert _wait_until(lambda: consilium.calls == 1)


def test_background_failure_sets_eval_error_and_keeps_hypothesis(dashboard_client, monkeypatch):
    import hypothesis_consilium_eval as hce
    fake = _Consilium(raise_exc=RuntimeError("anthropic down"))
    fake.gate.set()
    monkeypatch.setattr(hce, "evaluate_hypothesis_via_consilium", fake)
    _thread_spawn(monkeypatch)
    client, db = dashboard_client
    hyp_id = db.add_hypothesis({"observation": "obs v1", "status": "open", "version": 3})

    client.post(f"/api/hypotheses/{hyp_id}/eval")
    assert _wait_until(lambda: "eval_error" in _payload(db, hyp_id))
    p = _payload(db, hyp_id)
    assert "anthropic down" in p["eval_error"]
    assert p["observation"] == "obs v1" and p["version"] == 3     # error_no_mutation
    assert "eval_started_at" not in p                              # замок снят
    card = client.get(f"/api/hypotheses/{hyp_id}/card")
    assert "повторить" in card.text and "hx-trigger" not in card.text


def test_spawn_eval_detaches_process(dashboard_client, monkeypatch):
    """Оракул механизма 30.08: eval уходит в ОТДЕЛЬНУЮ сессию процесса
    (start_new_session) с argv hypothesis_resolution.py --eval <id>; на коде
    29.08 (create_task) Popen не зовётся вовсе — красный."""
    import subprocess
    import dashboard_routers.api_status_actions as asa
    calls = []

    class _P:
        def __init__(self, argv, **kw):
            calls.append((argv, kw))
    monkeypatch.setattr(subprocess, "Popen", _P)
    client, db = dashboard_client
    hyp_id = db.add_hypothesis({"observation": "obs", "status": "open"})
    r = client.post(f"/api/hypotheses/{hyp_id}/eval")
    assert r.status_code == 200 and "Идёт консилиум" in r.text
    assert len(calls) == 1
    argv, kw = calls[0]
    assert argv[1].endswith("hypothesis_resolution.py") and argv[2:] == ["--eval", str(hyp_id)]
    assert kw.get("start_new_session") is True
