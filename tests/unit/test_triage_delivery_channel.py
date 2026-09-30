"""tests/unit/test_triage_delivery_channel.py — КАНАЛ доставки, а не факт вызова.

Отличие от `test_triage_delivery`: тот стережёт ПУТЬ до канала (что классификация
доходит до отправки). Здесь другой вопрос — отчитался ли канал о себе. Разные
утверждения, разные способы сломаться, поэтому отдельный дом.

Класс отказа, который закрывается. 2026-07-29: `_send_telegram` звал
`notify.notify(msg)` и ВЫБРАСЫВАЛ возврат, хотя notify всегда считает канал
('telegram' | 'fallback' | 'none'). Строка `USER QUESTIONS sent` писалась
безусловно — одинаково при удачной отправке и при двух легших каналах. Утренний
разбор 29.07 показал зелёную цепочку «маркер + лог + integrity» при том, что
доказательства доставки в ней не было ни одного: подтвердить мог только человек.
"""
from __future__ import annotations

import json

import pytest

import triage_agent

pytestmark = pytest.mark.unit


def _read(tmp_path):
    return json.loads((tmp_path / "logs" / "triage_delivery_latest.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("channel", ["telegram", "fallback", "none"])
def test_receipt_records_channel_verbatim(tmp_path, monkeypatch, channel):
    """Квитанция несёт канал как есть, включая 'none'. Записывать 'none' обязательно:
    квитанция, которая пишется только при успехе, — это снова отчёт о намерении."""
    monkeypatch.setattr(triage_agent, "SCRIPT_DIR", tmp_path)
    triage_agent._write_run_receipt(3, channel, 0, None, True)
    rec = _read(tmp_path)
    assert rec["via"] == channel
    assert rec["questions"] == 3
    assert rec["date"], "без даты квитанцию нельзя сопоставить с прогоном"


def test_quiet_run_still_writes_receipt(tmp_path, monkeypatch):
    """⭐ Дефект 2026-09-07: квитанция писалась только при needs_user, маркер — всегда.
    Тихий день разводил их, и наутро датчик рельсы кричал «квитанция отстала». Теперь
    прогон, которому нечего доставлять, ОБЯЗАН оставить квитанцию — иначе «нечего было
    слать» снова станет неотличимо от «канал не отчитался» (та же дисциплина, что у
    owner_nag: пульс пишется и когда колокол молчит)."""
    monkeypatch.setattr(triage_agent, "SCRIPT_DIR", tmp_path)
    triage_agent._write_run_receipt(0, None, 0, None, True)
    rec = _read(tmp_path)
    assert rec["date"] and rec["questions"] == 0 and rec["digest"] == 0
    assert rec["via"] is None and rec["via_digest"] is None


def test_digest_channel_is_recorded(tmp_path, monkeypatch):
    """Понедельник, в который уехал ТОЛЬКО дайджест, — состоявшаяся доставка. До
    2026-09-07 её канал не записывался никуда, и квитанция оставалась позади."""
    monkeypatch.setattr(triage_agent, "SCRIPT_DIR", tmp_path)
    triage_agent._write_run_receipt(0, None, 5, "telegram", True)
    rec = _read(tmp_path)
    assert rec["via_digest"] == "telegram" and rec["digest"] == 5
    assert rec["last_proven"] == rec["date"], "дайджест доказывает канал не хуже вопросов"


def test_last_proven_carries_forward_and_ages(tmp_path, monkeypatch):
    """`last_proven` — единственное поле, невыводимое из одного прогона. Доставки не
    было — значение переносится из прежней квитанции, а не обнуляется: иначе бюджет
    молчания канала считался бы заново каждый тихий день и не сработал бы никогда."""
    monkeypatch.setattr(triage_agent, "SCRIPT_DIR", tmp_path)
    triage_agent._write_run_receipt(1, "telegram", 0, None, True)
    proven = _read(tmp_path)["last_proven"]
    triage_agent._write_run_receipt(0, None, 0, None, True)      # тихий день
    assert _read(tmp_path)["last_proven"] == proven
    triage_agent._write_run_receipt(2, "none", 0, None, True)    # оба канала легли
    assert _read(tmp_path)["last_proven"] == proven, "'none' не доказывает доставку"


def test_dry_run_does_not_prove_channel(tmp_path, monkeypatch):
    """Прогон без --send ничего не отправлял. Он доказывает, что рельса жива, и НЕ
    доказывает канал: `last_proven` двигать не имеет права."""
    monkeypatch.setattr(triage_agent, "SCRIPT_DIR", tmp_path)
    triage_agent._write_run_receipt(3, "telegram", 0, None, False)
    rec = _read(tmp_path)
    assert rec["sent"] is False and rec["last_proven"] is None


def test_last_proven_of_reads_old_shape(tmp_path):
    """Обратная совместимость: у квитанций до 2026-09-07 поля нет, но писались они
    ТОЛЬКО при отправке. Без этой ветки первое утро после деплоя дало бы ложный FAIL
    «канал не подтверждён НИКОГДА»."""
    assert triage_agent.last_proven_of(
        {"date": "2026-09-05", "via": "telegram", "questions": 1}) == "2026-09-05"
    assert triage_agent.last_proven_of(
        {"date": "2026-09-05", "via": "none", "questions": 1}) is None
    assert triage_agent.last_proven_of(["не словарь"]) is None


def test_receipt_failure_is_logged_not_swallowed(tmp_path, monkeypatch):
    """Отказ записи квитанции не имеет права быть тихим (§7). Каталог-файл делает
    запись невозможной — сообщение обязано попасть в лог."""
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "triage_delivery_latest.json").mkdir()   # каталог вместо файла
    monkeypatch.setattr(triage_agent, "SCRIPT_DIR", tmp_path)
    log = tmp_path / "triage.log"
    monkeypatch.setattr(triage_agent, "LOG_FILE", log)
    triage_agent._write_run_receipt(1, "telegram", 0, None, True)
    assert "квитанция прогона не записана" in log.read_text(encoding="utf-8")


def test_receipt_path_follows_script_dir(tmp_path, monkeypatch):
    """НЕГАТИВНЫЙ КОНТРОЛЬ на класс «тест дотянулся до боевого артефакта» (§20).

    До 2026-08-07 путь квитанции был модульной константой, посчитанной при импорте:
    подмена SCRIPT_DIR её не уводила, и `tests/integration/test_uc_d_04_triage.py`
    писал в БОЕВУЮ logs/. Мутация «вернуть модульную константу вместо резолва при
    вызове» обязана уронить этот тест — иначе сторож последнего метра доставки снова
    станет судить квитанцию теста вместо квитанции рельсы."""
    real = triage_agent.SCRIPT_DIR / "logs" / "triage_delivery_latest.json"
    before = real.read_bytes() if real.exists() else None
    monkeypatch.setattr(triage_agent, "SCRIPT_DIR", tmp_path)
    assert triage_agent._receipt_path() == tmp_path / "logs" / "triage_delivery_latest.json"
    triage_agent._write_run_receipt(1, "telegram", 0, None, True)
    after = real.read_bytes() if real.exists() else None
    assert after == before, "запись ушла в боевую квитанцию — путь не следует за SCRIPT_DIR"


def test_notify_itself_still_distinguishes_channels(monkeypatch):
    """Позитивный контроль на источник: если notify перестанет различать каналы,
    все проверки выше станут зелёными и бессмысленными разом."""
    import notify
    monkeypatch.setattr(notify, "_telegram", lambda msg, secrets=None: False)
    monkeypatch.setattr(notify, "_healthcheck_fail", lambda msg, secrets=None: False)
    assert notify.notify("x") == "none"
    monkeypatch.setattr(notify, "_healthcheck_fail", lambda msg, secrets=None: True)
    assert notify.notify("x") == "fallback"
    monkeypatch.setattr(notify, "_telegram", lambda msg, secrets=None: True)
    assert notify.notify("x") == "telegram"


def _triage_on(day, tmp_path, monkeypatch, gates):
    """Прогон run_triage в заданный день: пустой вердикт, стол из `gates`, Telegram
    перехвачен. Возвращает (результат, отправленные сообщения)."""
    import parked_decisions as pd
    from _time_inject import set_test_clock
    (tmp_path / "logs").mkdir(exist_ok=True)
    monkeypatch.setattr(triage_agent, "SCRIPT_DIR", tmp_path)
    monkeypatch.setattr(triage_agent, "LOG_FILE", tmp_path / "logs" / "triage.log")
    monkeypatch.setattr(triage_agent, "latest_verdict",
                        lambda require_today=False: ({"failures": [], "warnings": []}, None))
    sent = []
    monkeypatch.setattr(triage_agent.notify, "notify", lambda msg, *a, **k: sent.append(msg) or "telegram")
    monkeypatch.setenv("HEALTH_PARKED_DB", str(tmp_path / "parked.json"))
    set_test_clock(day)
    for gid, kind in gates:
        pd.park(gid, kind, f"карточка {gid}")
    return triage_agent.run_triage(send_user_questions=True), sent


def test_monday_does_not_send_the_engineering_queue(tmp_path, monkeypatch):
    """28.09: инженерная очередь остаётся ночному циклу, без пересылки оператору."""
    from _time_inject import clear_test_clock
    from unittest.mock import Mock
    operator, fault = Mock(), Mock()
    monkeypatch.setattr(triage_agent.notify, "notify_operator", operator)
    monkeypatch.setattr(triage_agent.notify, "fault", fault)
    try:
        res, sent = _triage_on("2026-09-28T08:00", tmp_path, monkeypatch,
                               [("a", "dev_fix"), ("b", "owner_decision")])
    finally:
        clear_test_clock()
    assert res["digest"] == [] and sent == [] and res["needs_user"] == []
    operator.assert_not_called()
    fault.assert_not_called()
    receipt = _read(tmp_path)
    assert receipt["via_digest"] is None and receipt["digest"] == 0


def test_engineering_queue_waits_for_monday(tmp_path, monkeypatch):
    from _time_inject import clear_test_clock
    try:
        res, sent = _triage_on("2026-09-23T08:00", tmp_path, monkeypatch, [("a", "dev_fix")])
    finally:
        clear_test_clock()
    assert res.get("digest") == [] and sent == []


def test_person_question_goes_to_the_question_home_not_telegram(db, tmp_path, monkeypatch):
    """28.09 (нить triage-questions): вопрос человеку — задача-вопрос в доме вопросов с
    обратным адресом, а не строка в утреннем Telegram. Технический пункт человеку не идёт.
    Квитанция: questions=0 (в Telegram триаж ничего не слал), to_outbox=1."""
    from datetime import date
    from unittest.mock import Mock
    import health_db
    monkeypatch.setattr(triage_agent, "SCRIPT_DIR", tmp_path)
    monkeypatch.setattr(triage_agent, "LOG_FILE", tmp_path / "logs" / "triage.log")
    monkeypatch.setattr(triage_agent, "get_today", lambda: date(2000, 1, 3))
    monkeypatch.setattr(triage_agent, "warn_class", lambda *_: "standing")
    verdict = {"warnings": [["анализы требуют обновления: 200д", "последний: 1999-06-17"],
                            ["invented worker failure", "FAKE_529"]], "failures": []}
    monkeypatch.setattr(triage_agent, "latest_verdict", lambda **kw: (verdict, ""))
    person, operator, fault = Mock(return_value="telegram"), Mock(), Mock()
    monkeypatch.setattr(triage_agent.notify, "notify", person)
    monkeypatch.setattr(triage_agent.notify, "notify_operator", operator)
    monkeypatch.setattr(triage_agent.notify, "fault", fault)
    result = triage_agent.run_triage()
    person.assert_not_called()
    operator.assert_not_called()
    fault.assert_not_called()
    with health_db.get_conn() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT type, source, content FROM tasks WHERE source='triage'")]
    assert len(rows) == 1 and rows[0]["type"] == "question"
    assert "1999-06-17" in rows[0]["content"] and "FAKE_529" not in rows[0]["content"]
    assert len(result["needs_user"]) == 1
    receipt = _read(tmp_path)
    assert receipt["questions"] == 0 and receipt["to_outbox"] == 1


def test_tenant_triage_uses_its_own_logs_and_asks_only_its_own(db, tmp_path, monkeypatch):
    """28.09, решение владельца «партнёру — свой утренний разбор». run_checks.sh зовёт
    триаж тенанта с HEALTH_TRIAGE_LOGS=<данные тенанта>/logs: вердикт, маркер дня и
    квитанция — там, а не в logs/ владельца (иначе партнёрский прогон затёр бы маркер и
    квитанцию владельца, и датчик рельсы владельца судил бы чужой прогон). В артефакте
    тенанта бывают находки обоих — человеку задаётся только его собственная."""
    import health_db
    from pathlib import Path
    owner_logs = tmp_path / "owner" / "logs"
    owner_logs.mkdir(parents=True)
    tenant_logs = tmp_path / "tenant" / "logs"
    tenant_logs.mkdir(parents=True)
    monkeypatch.setattr(triage_agent, "SCRIPT_DIR", tmp_path / "owner")
    monkeypatch.setattr(triage_agent, "LOG_FILE", owner_logs / "triage.log")
    monkeypatch.setenv("HEALTH_TRIAGE_LOGS", str(tenant_logs))
    own = Path(health_db.DB_PATH).parent.parent.name
    (tenant_logs / "integrity_latest.json").write_text(json.dumps({
        "db_path": str(health_db.DB_PATH), "failures": [],
        "warnings": [[f"[{own}] анализы требуют обновления: 200д", "последний: 2030-01-15"],
                     ["[invented_other_tenant] анализы устарели: 400д (порог 270д)", ""]]}),
        encoding="utf-8")
    monkeypatch.setattr(triage_agent.notify, "notify", lambda *a, **k: "telegram")
    res = triage_agent.run_triage(send_user_questions=True)
    assert len(res["needs_user"]) == 1 and "2030-01-15" in res["needs_user"][0]
    assert (tenant_logs / "triage_delivery_latest.json").exists()
    assert list(tenant_logs.glob("triage_done_*.flag"))
    assert not list(owner_logs.iterdir()), "триаж тенанта тронул logs/ владельца"
    with health_db.get_conn() as conn:
        n = conn.execute("SELECT COUNT(*) FROM tasks WHERE source='triage'").fetchone()[0]
    assert n == 1
