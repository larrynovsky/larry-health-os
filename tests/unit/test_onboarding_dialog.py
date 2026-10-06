"""Оракул: движок опросников умеет вопросы знакомства и пишет ответы туда, откуда их читают.

Нить onboarding-dialog (Э2, 2026-09-23). До неё движок знал только шкалу: текст отбивался
«нажми кнопку», геопозиция уходила мимо опроса, а finalize импортировал ВСЕ ответы в
lab_results как баллы опросника — «рост 175» стал бы строкой анализа.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytestmark = pytest.mark.unit

_INSTR = {
    "id": "t_onb", "name": "Знакомство (тест)", "sink": "profile",
    "intro": "Несколько вопросов о тебе.", "done_text": "Готово.",
    "items": [
        {"id": "name", "kind": "text", "field": "identity.name", "text": "Как к тебе обращаться?"},
        {"id": "sex", "kind": "choice", "field": "identity.sex", "text": "Пол?",
         "options": [{"label": "Мужской", "value": "male"}, {"label": "Женский", "value": "female"}]},
        {"id": "height", "kind": "number", "field": "identity.height_cm", "text": "Рост, см?",
         "hint": "число от 100 до 230"},
        {"id": "home", "kind": "location", "optional": True, "text": "Где дом?"},
    ],
}


@pytest.fixture()
def onb(db, tmp_path, monkeypatch):
    import assessment_dialog as ad
    repo = tmp_path / "repo_instruments"
    repo.mkdir()
    (repo / "t_onb.json").write_text(json.dumps(_INSTR, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(ad, "REPO_INSTRUMENTS_DIR", repo)
    monkeypatch.setattr(ad, "INSTRUMENTS_DIR", tmp_path / "tenant_instruments")
    import health_db as hdb
    tid = hdb.save_task(source="onboarding", type_="assessment", content="знакомство", priority="medium")
    db.execute("UPDATE tasks SET fingerprint=? WHERE id=?", ("assessment:t_onb", tid))
    return ad, tid


def test_знакомство_целиком_пишет_в_профиль_а_не_в_анализы(onb, db):
    import health_db as hdb
    ad, tid = onb
    text, kb, sid = ad.start(chat_id=7, task_id=tid)
    assert "Несколько вопросов о тебе." in text and "Как к тебе обращаться?" in text
    reply, _, _ = ad.answer(sid, "name", "Тестер")
    assert reply.startswith("Записал: Тестер")
    assert hdb.get_patient_profile().get("identity.name") == "Тестер", "ответ не записан сразу"
    reply, kb, _ = ad.answer(sid, "sex", 0)
    assert "Записал: Мужской" in reply
    reply, _, done = ad.answer(sid, "height", "1750")
    assert reply.startswith("Не понял: число от 100 до 230") and not done
    assert "identity.height_cm" not in hdb.get_patient_profile(), "неразобранное записано"
    ad.answer(sid, "height", "175 см")
    reply, _, done = ad.answer(sid, "home", -1)
    assert done and "Вот что я теперь знаю" in reply
    prof = hdb.get_patient_profile()
    assert prof["identity.sex"] == "male" and prof["identity.height_cm"] == "175"
    assert db.execute("SELECT COUNT(*) FROM lab_results").fetchone()[0] == 0, \
        "ответы знакомства уехали в lab_results"
    import assessment_importer as ai
    assert not list(ai.assessments_dir().glob("*.json")), "знакомство записало JSON опросника"
    st = db.execute("SELECT status FROM assessment_sessions WHERE id=?", (sid,)).fetchone()[0]
    assert st == "completed"


def test_прерванное_продолжается_с_места(onb):
    ad, tid = onb
    _, _, sid = ad.start(chat_id=7, task_id=tid)
    ad.answer(sid, "name", "Тестер")
    text, _, sid2 = ad.start(chat_id=7, task_id=tid)
    assert sid2 == sid and "Продолжаем" in text and "Пол?" in text


def test_текст_во_время_опроса_становится_ответом(onb):
    import assessment_bot_handlers as abh
    ad, tid = onb
    _, _, sid = ad.start(chat_id=7, task_id=tid)
    chat = SimpleNamespace(send_message=AsyncMock())
    upd = SimpleNamespace(message=SimpleNamespace(text="Тестер"), effective_chat=chat)
    asyncio.run(abh.handle_text_in_assessment(upd, None, ad.get_active(7)))
    assert "Записал: Тестер" in chat.send_message.await_args.args[0]


def test_геопозиция_во_время_опроса_уходит_в_ответ(onb):
    import assessment_bot_handlers as abh
    ad, tid = onb
    _, _, sid = ad.start(chat_id=7, task_id=tid)
    for iid, v in (("name", "А"), ("sex", 1), ("height", "170")):
        ad.answer(sid, iid, v)
    chat = SimpleNamespace(send_message=AsyncMock())
    upd = SimpleNamespace(message=SimpleNamespace(location=SimpleNamespace(latitude=64.14, longitude=-21.94)),
                          effective_chat=chat)
    took = asyncio.run(abh.handle_location_in_assessment(upd, None, ad.get_active(7)))
    assert took, "точка не принята опросом"
    answers = json.loads(ad.db.get_assessment_session(sid)["answers_json"])
    assert answers["home"] == [64.14, -21.94]


def test_кнопка_геопозиции_рисуется_reply_клавиатурой(onb):
    import assessment_bot_handlers as abh
    ad, _ = onb
    ins = ad._load_instrument_by_source_id("t_onb")
    markup = abh._kb_to_markup(ad._build_keyboard(ins, "home"))
    assert type(markup).__name__ == "ReplyKeyboardMarkup"
    assert markup.keyboard[0][0].request_location is True


def test_опросники_проекта_не_видны_планировщику():
    """У опросника проекта нет ритма — попади он в каталог планировщика, ночной датчик
    свежести звал бы «знакомство просрочено» каждую ночь."""
    import assessment_dialog as ad
    import assessment_scheduler as asch
    assert Path(asch.INSTRUMENTS_DIR).resolve() != Path(ad.REPO_INSTRUMENTS_DIR).resolve()


# ── Э3–Э4: настоящий опросник знакомства (methodology/instruments/onboarding.json) ─────────

def _real(db, monkeypatch, tmp_path):
    import assessment_bot_handlers as abh
    import assessment_dialog as ad
    monkeypatch.setattr(ad, "INSTRUMENTS_DIR", tmp_path / "tenant_instruments")
    text, kb, sid = abh.start_onboarding(7)
    return ad, abh, sid, text


_ANSWERS = [("language", 0), ("name", "Тестер"), ("birth_date", "01.01.1975"), ("sex", 0), ("height", "178"),
            ("weight", "82,5"), ("home", [64.14, -21.94]), ("brief_time", 3),
            ("health", "Высокий холестерин\nИногда болит спина"), ("allergies", "пенициллин"), ("meds", "Витамин D 2000 МЕ\nнет"),
            ("smoking", 0), ("fasting", 0), ("sources", 2)]


def test_знакомство_раскладывает_ответы_по_местам(db, monkeypatch, tmp_path):
    import config_db
    import health_db as hdb
    import secrets_paths
    empty = tmp_path / "secrets_empty"
    empty.mkdir()
    # Подключено ли уже — судится по файлам секретов тенанта; у стенда их нет (на Studio
    # тестовый каталог секретов содержит oura_token — поймано полным прогоном).
    monkeypatch.setattr(secrets_paths, "secrets_dir", lambda: empty)
    ad, abh, sid, text = _real(db, monkeypatch, tmp_path)
    n_items = len(ad._load_instrument_by_source_id("onboarding")["items"])
    assert f"вопросов — {n_items}" in text, "первая реплика не говорит, куда идём (число — из items)"
    reply = ""
    for iid, v in _ANSWERS:
        reply, _, done = ad.answer(sid, iid, v)
    assert done and "Вот что я теперь знаю" in reply
    prof = hdb.get_patient_profile()
    assert prof["identity.name"] == "Тестер" and prof["identity.birth_date"] == "1975-01-01"
    assert prof["identity.height_cm"] == "178" and prof["identity.weight_kg"] == "82.5"
    assert prof["routine.smoking"] == "не курит" and prof["routine.fasting_labs"] == "true"
    assert prof["medical.allergies"] == "пенициллин"   # 27.09: аллергии — полем профиля
    assert config_db.get_config("location.home_lat") == 64.14
    assert config_db.get_config("schedule.morning_brief") == {"hour": 8, "minute": 0}
    props = db.execute("SELECT proposed FROM problem_list_proposals WHERE source='onboarding'").fetchall()
    assert len(props) == 2, "каждая проблема — своим предложением"
    assert db.execute("SELECT COUNT(*) FROM problem_list").fetchone()[0] == 0, \
        "проблема записана в медкарту без /approve (owner_gate_kept)"
    meds = [r[0] for r in db.execute("SELECT name FROM medications WHERE source='onboarding'")]
    assert meds == ["Витамин D 2000 МЕ"], meds
    acts = db.execute("SELECT COUNT(*) FROM tasks WHERE source='onboarding' AND type='action'").fetchone()[0]
    assert acts == 2, "«и то и другое» — две задачи подключения"
    assert db.execute("SELECT COUNT(*) FROM lab_results").fetchone()[0] == 0
    assert "Проблемы со здоровьем: 2" in reply and "Дом: задан" in reply


def test_известное_предлагается_кнопкой_верно(db, monkeypatch, tmp_path):
    import health_db as hdb
    hdb.upsert_profile("identity.sex", value_text="male", category="identity")
    ad, abh, sid, _ = _real(db, monkeypatch, tmp_path)
    ad.answer(sid, "language", 0)
    ad.answer(sid, "name", "Тестер")
    reply, kb, _ = ad.answer(sid, "birth_date", "1975-01-01")
    assert kb[0][0] == ("Верно: Мужской", "cb_aa:sex:-2"), kb
    reply, _, _ = ad.answer(sid, "sex", -2)
    assert "Оставил как было" in reply and hdb.get_patient_profile()["identity.sex"] == "male"


def test_вопрос_посреди_знакомства_не_записывается_ответом(db, monkeypatch, tmp_path):
    import health_db as hdb
    ad, abh, sid, _ = _real(db, monkeypatch, tmp_path)
    ad.answer(sid, "language", 0)
    chat = SimpleNamespace(send_message=AsyncMock())
    upd = SimpleNamespace(message=SimpleNamespace(text="а что с моим сном?"), effective_chat=chat)
    asyncio.run(abh.handle_text_in_assessment(upd, None, ad.get_active(7)))
    assert "Задай его ещё раз после знакомства" in chat.send_message.await_args.args[0]
    assert "identity.name" not in hdb.get_patient_profile()


def test_знакомство_не_приходит_второй_раз_через_outbox(db, monkeypatch, tmp_path):
    import health_db as hdb
    _real(db, monkeypatch, tmp_path)
    row = db.execute("SELECT sent_at FROM tasks WHERE fingerprint='assessment:onboarding'").fetchone()
    assert row[0], "задача знакомства не помечена доставленной — outbox опросников пришлёт её снова"


def test_прерванное_знакомство_продолжается_через_about(db, monkeypatch, tmp_path):
    ad, abh, sid, _ = _real(db, monkeypatch, tmp_path)
    ad.answer(sid, "name", "Тестер")
    text, _, sid2 = abh.start_onboarding(7)
    assert sid2 == sid and "Продолжаем" in text


def test_пропуск_в_карточке_не_читается_как_нет(db, monkeypatch, tmp_path):
    """Придуманный пропуск ответа отображается как пропуск, а не отрицание."""
    ad, abh, sid, _ = _real(db, monkeypatch, tmp_path)
    ins = ad._load_instrument_by_source_id("onboarding")
    card = ad.summary_text(ins, {"meds": "__skipped__", "health": "__skipped__"})
    assert "Лекарства и добавки: пропущено" in card and "Лекарства и добавки: нет" not in card
    assert "Проблемы со здоровьем: пропущено" in card


def test_итог_знакомства_просит_документы_и_геном(db, monkeypatch, tmp_path):
    """Решение владельца 24.09: просить геном, анализы и заключения врачей за 3 года. Перечень
    форматов генома — из дома разборщика (genome_intake.PROVIDERS), а не копией."""
    import genome_intake
    ad, abh, sid, _ = _real(db, monkeypatch, tmp_path)
    ins = ad._load_instrument_by_source_id("onboarding")
    card = ad.summary_text(ins, {})
    assert "заключения врачей" in card and "за последние 3 года" in card
    assert all(p in card for p in genome_intake.PROVIDERS) and "20 МБ" in card


def test_после_геопозиции_reply_клавиатура_снимается(onb):
    """Telegram держит reply-клавиатуру, пока её явно не снимут. До 25.09 её не снимал никто:
    у владельца с 23.09 висели «📍 Отправить место» и «Пропустить» — бот будто просил место.
    Оракул: после ответа на вопрос-геопозицию уходит служебное сообщение с ReplyKeyboardRemove
    и тут же удаляется, а ответ опроса идёт следом."""
    import assessment_bot_handlers as abh
    ad, tid = onb
    _, _, sid = ad.start(chat_id=7, task_id=tid)
    for iid, v in (("name", "А"), ("sex", 1), ("height", "170")):
        ad.answer(sid, iid, v)
    service = SimpleNamespace(delete=AsyncMock())
    chat = SimpleNamespace(send_message=AsyncMock(return_value=service))
    upd = SimpleNamespace(message=SimpleNamespace(location=SimpleNamespace(latitude=1.0, longitude=2.0)),
                          effective_chat=chat)
    asyncio.run(abh.handle_location_in_assessment(upd, None, ad.get_active(7)))
    first = chat.send_message.await_args_list[0]
    assert type(first.kwargs.get("reply_markup")).__name__ == "ReplyKeyboardRemove", \
        "reply-клавиатура геопозиции осталась висеть"
    service.delete.assert_awaited_once()
    assert "Вот что я теперь знаю" in chat.send_message.await_args_list[-1].args[0]


def test_ответ_на_обычный_вопрос_клавиатуру_не_трогает(onb):
    """Служебное сообщение — только после вопроса-геопозиции, не после каждого ответа."""
    import assessment_bot_handlers as abh
    ad, tid = onb
    ad.start(chat_id=7, task_id=tid)
    chat = SimpleNamespace(send_message=AsyncMock())
    upd = SimpleNamespace(message=SimpleNamespace(text="Тестер"), effective_chat=chat)
    asyncio.run(abh.handle_text_in_assessment(upd, None, ad.get_active(7)))
    assert chat.send_message.await_count == 1


def test_реплика_о_файле_не_становится_ответом(onb):
    """Нить lab-intake-retry (05.10): «это не дубль» в ответ боту про файл записалось проблемой со
    здоровьем. Мутации: убрать признак цитаты; убрать метку ответа на файл; не гасить метку
    (тогда и повторённый настоящий ответ отбивался бы)."""
    import time
    import assessment_bot_handlers as abh
    ad, tid = onb
    ad.start(chat_id=7, task_id=tid)
    chat = SimpleNamespace(send_message=AsyncMock())
    dup = SimpleNamespace(text="Этот файл уже получал — пропускаю дубль.")
    upd = SimpleNamespace(message=SimpleNamespace(text="это не дубль", reply_to_message=dup),
                          effective_chat=chat)
    asyncio.run(abh.handle_text_in_assessment(upd, None, ad.get_active(7)))
    assert "Записал" not in chat.send_message.await_args.args[0]
    ctx = SimpleNamespace(chat_data={"doc_reply_at": time.time()})
    upd = SimpleNamespace(message=SimpleNamespace(text="это не дубль"), effective_chat=chat)
    asyncio.run(abh.handle_text_in_assessment(upd, ctx, ad.get_active(7)))
    assert "Записал" not in chat.send_message.await_args.args[0]
    upd = SimpleNamespace(message=SimpleNamespace(text="Тестер"), effective_chat=chat)
    asyncio.run(abh.handle_text_in_assessment(upd, ctx, ad.get_active(7)))
    assert "Записал: Тестер" in chat.send_message.await_args.args[0]
