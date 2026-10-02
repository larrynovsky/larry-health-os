"""Главная «С чего начать»: состояние каждой возможности судится из данных, каталог полон.

Оракулы (каждый краснеет при поломке своего механизма):
- источник, молчащий дольше SOURCE_STALE_DAYS, — broken (мутация «убрать проверку свежести»);
- накопление до личной полосы — acc с долей n/BAND_MIN_OBS;
- токен без данных — wait, ни токена ни данных — not;
- ждущее решения человека поднимается в «следующий шаг» раньше нового источника;
- содержимое секрета не попадает в доску (§19);
- каждая возможность каталога имеет судью, каждый её intent есть в реестре, каждая запись
  реестра либо покрыта, либо названа в skip_intent с причиной.
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest
import yaml

import getting_started as gs

pytestmark = pytest.mark.unit

TODAY = date(2030, 1, 20)


@pytest.fixture
def env(db, tmp_path):
    """Настоящая схема базы (фикстура db), пустой каталог ключей, каталог данных тенанта."""
    import health_db
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    conn = sqlite3.connect(str(health_db.DB_PATH))
    yield conn, secrets, Path(health_db.DB_PATH).parent
    conn.close()


def _board(env):
    conn, secrets, data_dir = env
    return gs.board(conn=conn, secrets=secrets, data_dir=data_dir, today=TODAY, lang="ru")


def _cap(board, cid):
    return next(c for c in board["caps"] if c["id"] == cid)


def _oura_days(conn, last: date, n: int):
    for i in range(n):
        d = (last - timedelta(days=i)).isoformat()
        conn.execute("INSERT OR REPLACE INTO daily_metrics (date, readiness) VALUES (?, 80)", (d,))
    conn.commit()


def test_fresh_install_nothing_green_but_consult_and_next_is_introduction(env):
    conn, secrets, _ = env
    (secrets / "anthropic_key").write_text("x")
    b = _board(env)
    states = {c["id"]: c["state"] for c in b["caps"]}
    assert states["consult"] == "ok"
    assert [k for k, v in states.items() if v == "ok"] == ["consult"]
    assert b["next"]["id"] == "about"


def test_device_states_follow_data_not_flags(env):
    conn, secrets, _ = env
    assert _cap(_board(env), "oura")["state"] == "not"
    (secrets / "oura_token").write_text("tok")
    assert _cap(_board(env), "oura")["state"] == "wait"
    _oura_days(conn, TODAY, 9)
    c = _cap(_board(env), "oura")
    from brief_gate import BAND_MIN_OBS
    assert c["state"] == "acc" and c["progress"] == pytest.approx(9 / BAND_MIN_OBS)
    _oura_days(conn, TODAY, BAND_MIN_OBS)
    assert _cap(_board(env), "oura")["state"] == "ok"


def test_silent_source_is_broken_and_becomes_the_next_step(env):
    conn, secrets, _ = env
    from metrics_db import SOURCE_STALE_DAYS
    (secrets / "oura_token").write_text("tok")
    _oura_days(conn, TODAY - timedelta(days=SOURCE_STALE_DAYS + 1), 20)
    b = _board(env)
    c = _cap(b, "oura")
    assert c["state"] == "broken"
    assert b["next"]["id"] == "oura"
    assert c["primary"]["text"] == "Заменить токен Oura"
    assert c["primary"]["href"].endswith("how-to/connect_oura.md") and c["primary"]["kind"] == "warning"
    # граница: ровно порог — ещё не сломано
    _oura_days(conn, TODAY - timedelta(days=SOURCE_STALE_DAYS), 1)
    assert _cap(_board(env), "oura")["state"] != "broken"


def test_pending_decision_beats_new_source(env):
    conn, secrets, _ = env
    import problems_db   # настоящий писатель предложений, тот же, что у знакомства
    problems_db.save_problem_proposal("onboarding", [{
        "action": "add", "problem_id": None,
        "new_value": {"title": "Example_Condition_A", "status": "active", "description": "x"},
        "reason": "test"}])
    b = _board(env)
    rec = _cap(b, "record")
    assert rec["pending"] == 1 and rec["primary"]["href"] == "/proposals"
    assert b["next"]["id"] == "record"


def test_secret_content_never_reaches_the_board(env):
    conn, secrets, _ = env
    for n in ("oura_token", "hae_ingest_token", "anthropic_key", "google_calendar_token.json"):
        (secrets / n).write_text("SENTINEL-SECRET-7f3a")
    assert "SENTINEL-SECRET-7f3a" not in repr(_board(env))


def test_links_threshold_comes_from_signal_family(env):
    conn, secrets, _ = env
    from signal_family import MIN_OVERLAP_DAYS
    _oura_days(conn, TODAY, 20)
    c = _cap(_board(env), "links")
    assert c["state"] == "acc"
    assert str(MIN_OVERLAP_DAYS) in c["status"] and str(MIN_OVERLAP_DAYS) in c["need"]


def test_catalog_complete_against_registry():
    cat = gs.load_catalog()
    ids = [c["id"] for c in cat["capabilities"]]
    assert set(ids) == set(gs.JUDGES), "у каждой возможности — судья, у судьи — возможность"
    reg = {e["id"] for e in yaml.safe_load((Path(gs.__file__).parent / "subsystem_intent.yaml").read_text())}
    used = {i for c in cat["capabilities"] for i in c["intent"]}
    assert used <= reg, f"ссылки на несуществующие записи реестра: {used - reg}"
    skipped = set(cat["skip_intent"])
    assert skipped <= reg, f"skip_intent называет несуществующее: {skipped - reg}"
    missing = reg - used - skipped
    assert not missing, f"запись реестра без карточки и без причины: {sorted(missing)}"
    assert not (used & skipped), f"и покрыто, и пропущено: {used & skipped}"


def test_every_state_text_renders_in_both_languages(env):
    cat = gs.load_catalog()
    for lang in ("ru", "en"):
        for key, val in cat["texts"].items():
            assert val.get(lang), f"{key}: нет текста {lang}"
        for c in cat["capabilities"]:
            for f in ("title", "need"):
                assert c[f].get(lang), f"{c['id']}.{f}: нет {lang}"
    conn, secrets, data_dir = env
    gs.board(conn=conn, secrets=secrets, data_dir=data_dir, today=TODAY, lang="en")


def test_home_page_renders_every_capability(dashboard_client, monkeypatch, tmp_path):
    client, _db = dashboard_client
    monkeypatch.setenv("HEALTH_SECRETS_DIR", str(tmp_path))
    r = client.get("/")
    assert r.status_code == 200
    for cid in gs.JUDGES:
        assert f'data-cap="{cid}"' in r.text, cid


def test_home_page_survives_a_failing_judge_and_says_so(dashboard_client, monkeypatch, tmp_path):
    client, _db = dashboard_client
    monkeypatch.setenv("HEALTH_SECRETS_DIR", str(tmp_path))
    def boom(*a, **k):
        raise RuntimeError("judge down")
    monkeypatch.setattr(gs, "board", boom)
    r = client.get("/")
    assert r.status_code == 200
    import i18n
    assert i18n.t("dashboard.home.board_failed", "ru") in r.text


def test_brief_trace_is_the_gate_table_not_agent_reports(env):
    """Ежедневный бриф оставляет след в context_cards, а не в agent_reports (gp_daily там нет).
    Регрессия 02.10: живая главная владельца с ежедневным брифом показывала «ждёт данных»."""
    conn, secrets, _ = env
    assert _cap(_board(env), "brief")["state"] == "wait"
    conn.execute("INSERT INTO context_cards (date, provider, semantic_key, lane) "
                 "VALUES (?, 'sleep', 'sleep:deep', 'routine')", (TODAY.isoformat(),))
    conn.commit()
    c = _cap(_board(env), "brief")
    assert c["state"] == "ok" and c["receipt"] == TODAY.isoformat()


def test_answer_names_what_is_not_working_and_open_questions_lead(env):
    """Замечание владельца 02.10: «16 из 17» не говорит, какая не работает, а «вопросов ждут: 4»
    не говорит, как ответить. Ответ называет неработающее по имени; открытый вопрос — ждущее
    решение со ссылкой на how-to ответа, и он становится следующим шагом."""
    conn, secrets, _ = env
    import health_db
    tid = health_db.save_task(source="test", type_="question", content="Состоялся ли визит?")
    b = _board(env)
    q = _cap(b, "questions")
    assert q["pending"] == 1 and q["primary"]["href"].endswith("how-to/answer_a_question.md")
    assert "реплаем" in q["status"]
    assert b["next"]["id"] == "questions"
    assert "Вопросы системы" in b["answer"]


def test_bot_actions_are_text_and_links_lead_where_their_label_says(env):
    """Холодное чтение 02.10: «Отправить бланки боту» вело на очередь проверки, «Подробнее»
    календаря — на установку Докера, «Инструкция» среды — на «добавить человека». Действие,
    которое делается в боте, — текст без ссылки; ссылка на инструкцию идёт отдельной строкой со
    своей подписью; у возможности без инструкции ссылки нет вовсе."""
    b = _board(env)
    for cid in ("about", "record", "labs", "genome", "place"):
        p = _cap(b, cid)["primary"]
        assert p["kind"] == "text" and p["href"] is None, cid
    assert _cap(b, "labs")["secondary"]["text"] == "Как добавить анализы"
    assert _cap(b, "labs")["secondary"]["href"].endswith("how-to/add_labs.md")
    for cid in ("place", "calendar"):
        assert _cap(b, cid)["secondary"] is None, cid
    cat = gs.load_catalog()
    howtos = {c["howto"] for c in cat["capabilities"] if c.get("howto")}
    root = Path(gs.__file__).parent / "docs"
    assert all((root / h).exists() for h in howtos), "ссылка на несуществующую инструкцию"
