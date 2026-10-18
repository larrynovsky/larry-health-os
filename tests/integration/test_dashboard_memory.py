"""Страница «Память» (07.10): владелец сам гасит записи и добавляет запреты «не предлагай мне X».

Каждый тест — одно обещание владельцу. Красный первый — запрет не дошёл до брифа, а значит ни
до бота, ни до Claude/ChatGPT. Красный второй — «убрать» стало удалением или не оставило следа.
Красный третий — чужая страница в браузере владельца может погасить его память.
"""
import sqlite3

import memory_facts_db as mf
import patient_context


def _row(db, fid):
    con = sqlite3.connect(db.path)
    con.row_factory = sqlite3.Row
    try:
        return con.execute("SELECT * FROM memory_facts WHERE id=?", (fid,)).fetchone()
    finally:
        con.close()


def test_ban_from_dashboard_reaches_the_brief(dashboard_client):
    client, _ = dashboard_client
    r = client.post("/api/memory/ban", data={"text": "  не предлагай мне   бег "})
    assert r.status_code == 200 and "не предлагай мне бег" in r.text
    (ban,) = mf.get_facts("preference")
    assert ban["key"].startswith("no_suggest:") and ban["source"] == "owner_dashboard"
    assert "НЕ ПРЕДЛАГАТЬ" in patient_context._bans_line() and "не предлагай мне бег" in patient_context._bans_line()
    assert "не предлагай мне бег" in client.get("/memory").text


def test_retire_extinguishes_not_deletes_and_leaves_a_trace(dashboard_client, db):
    client, _ = dashboard_client
    fid = mf.save_fact("state", "плохо спал", source="conversation")
    assert client.post(f"/api/memory/{fid}/retire").status_code == 200
    row = _row(db, fid)
    assert row is not None and row["active"] == 0 and row["retire_reason"]
    assert all(f["id"] != fid for f in mf.get_facts())
    assert client.post(f"/api/memory/{fid}/retire").status_code == 404     # второй раз — нечего гасить


def test_foreign_page_cannot_touch_memory(dashboard_client):
    client, _ = dashboard_client
    fid = mf.save_fact("state", "плохо спал", source="conversation")
    for h in ({"Origin": "https://evil.example"}, {"Sec-Fetch-Site": "cross-site"}):
        assert client.post(f"/api/memory/{fid}/retire", headers=h).status_code == 403
        assert client.post("/api/memory/ban", data={"text": "x"}, headers=h).status_code == 403
    assert any(f["id"] == fid for f in mf.get_facts()) and not mf.get_facts("preference")


def test_empty_or_huge_ban_refused(dashboard_client):
    client, _ = dashboard_client
    assert client.post("/api/memory/ban", data={"text": "   "}).status_code == 400
    assert client.post("/api/memory/ban", data={"text": "x" * 201}).status_code == 400
    assert not mf.get_facts("preference")


def test_handlers_called_directly(db):
    """Характеризация публичных имён модуля (гейт одноразовости, D5): без HTTP."""
    import pytest
    from fastapi import HTTPException
    from dashboard_routers import api_memory
    out = api_memory.api_memory_ban(text="не предлагай мне мелатонин")
    assert "мелатонин" in out.body.decode()
    (ban,) = mf.get_facts("preference")
    api_memory.api_memory_retire(ban["id"])
    assert not mf.get_facts("preference")
    with pytest.raises(HTTPException):
        api_memory.api_memory_retire(ban["id"])


def test_page_speaks_owner_language_and_folds_old(dashboard_client, db):
    """07.10: владелец назвал страницу мусором — коды базы вместо слов, 340 состояний одним списком."""
    client, _ = dashboard_client
    q = mf.save_fact("fact", "Как часто пьёте кофе? → 1-2 чашки утром", key="question:mem:875", source="task:195")
    old = mf.save_fact("state", "плохо спал в марте", source="arbiter_unverified")
    mf.save_fact("state", "сегодня бодр", source="conversation")
    con = sqlite3.connect(db.path)
    try:
        con.execute("UPDATE memory_facts SET valid_from='2026-01-02' WHERE id=?", (old,))
        con.commit()
    finally:
        con.close()
    html = client.get("/memory").text
    assert "question:mem:875" not in html and "1-2 чашки утром" in html          # служебный ключ скрыт
    assert "task:195" not in html and "arbiter_unverified" not in html            # источник — словами
    assert "твой ответ на задачу" in html and "из разговора, не проверено" in html
    assert "standing" not in html and "durable" not in html
    assert "02.01.2026" in html                                                  # дата — по-человечески
    fresh_part, old_part = html.split("старше 30 дней", 1)
    assert "сегодня бодр" in fresh_part and "плохо спал в марте" in old_part     # старое — под катом
    assert q
