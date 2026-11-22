"""Что стало с протоколом: пересказ выполнения и прогноз, оценённый кодом (assistant-loop, 10.11).

Синтетическая БД через продакшн-миграции; модели, сеть и почта не вызываются.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

import health_db
import protocol_outcomes as po

DAY0 = date(2026, 10, 1)


@pytest.fixture
def pdb(db, monkeypatch):
    health_db._migrate_protocol_outcomes()
    from tests.conftest import canon_schema
    canon_schema(health_db)
    import agent_reply
    monkeypatch.setattr(agent_reply, "allowed_peers", lambda: {"berrimor"})
    with health_db.get_conn() as c:
        c.execute("INSERT INTO protocols(id,title,behavior,status,created_at) "
                  "VALUES (7,'Ходьба','30 минут после обеда','active',?)", (f"{DAY0}T09:00:00",))
        for i in range(1, 31):   # база: steps = 6000 за 30 дней до протокола
            c.execute("INSERT INTO daily_metrics(date, steps) VALUES (?, 6000)", (str(DAY0 - timedelta(days=i)),))
    return health_db


def _days(values: dict[int, int]):
    with health_db.get_conn() as c:
        for offset, v in values.items():
            c.execute("INSERT OR REPLACE INTO daily_metrics(date, steps) VALUES (?, ?)",
                      (str(DAY0 + timedelta(days=offset)), v))


PRED = {"metric": "steps", "change": 2000, "low": 500, "high": 3500, "horizon_days": 28}


# ── выполнение ──

def test_строка_ассистента_хозяина_пишется_с_происхождением(pdb):
    letter = {"peer": "berrimor", "key": "k1",
              "wrapped": "Привет. протокол #7: 5 из 7 с 03.11 по 09.11. Остальное позже."}
    saved = po.record_from_letter(letter, today=date(2026, 11, 10))
    assert saved == [dict(protocol_id=7, done=5, planned=7, period_from="2026-11-03", period_to="2026-11-09")]
    a = po.adherence(7)
    assert (a["done"], a["planned"], a["reports"], a["source"]) == (5, 7, 1, "assistant_report")
    assert health_db.get_active_protocols()[0]["id"] == 7      # статус строкой не меняется


def test_чужой_адрес_и_кривые_числа_не_пишутся(pdb):
    assert po.record_from_letter({"peer": "stranger", "wrapped": "протокол #7: 1 из 2 с 01.11 по 02.11"}) == []
    assert po.record_from_letter({"peer": "berrimor", "wrapped": "протокол #7: 9 из 7 с 01.11 по 07.11"},
                                 today=date(2026, 11, 10)) == []
    assert po.record_from_letter({"peer": "berrimor", "wrapped": "протокол #99: 1 из 1 с 01.11 по 01.11"},
                                 today=date(2026, 11, 10)) == []
    assert po.adherence(7)["reports"] == 0


def test_период_через_новый_год():
    rows = po.parse_adherence("протокол #7: 3 из 7 с 28.12 по 03.01", today=date(2027, 1, 5))
    assert (rows[0]["period_from"], rows[0]["period_to"]) == ("2026-12-28", "2027-01-03")


def test_дом_принимает_только_пересказ_ассистента(pdb):
    with pytest.raises(Exception):
        with health_db.get_conn() as c:
            c.execute("INSERT INTO protocol_adherence(protocol_id,period_from,period_to,done,planned,source) "
                      "VALUES (7,'2026-11-01','2026-11-07',3,7,'oura')")


# ── прогноз ──

def test_базу_считает_код_а_не_модель(pdb):
    row = po.attach_prediction(7, {**PRED, "baseline": 1}, created=DAY0)
    assert row["baseline"] == 6000 and row["horizon"] == str(DAY0 + timedelta(days=28))


@pytest.mark.parametrize("bad", [{**PRED, "metric": "mood_magic"}, {**PRED, "change": 4000},
                                 {**PRED, "horizon_days": 3}, {**PRED, "low": "много"}])
def test_кривой_прогноз_отвергается(pdb, bad):
    with pytest.raises(po.PredictionRejected):
        po.attach_prediction(7, bad, created=DAY0)


# ── оценка ──

def _status():
    with health_db.get_conn() as c:
        return dict(c.execute("SELECT * FROM protocol_predictions WHERE protocol_id=7").fetchone())


def test_сбывшийся_прогноз_бьёт_прогноз_без_изменений(pdb):
    po.attach_prediction(7, PRED, created=DAY0)
    _days({d: 8000 for d in range(14, 29)})
    assert po.score_due(DAY0 + timedelta(days=20)) == []          # окно daily ждёт горизонта
    po.score_due(DAY0 + timedelta(days=29))
    s = _status()
    assert (s["status"], s["observed"], s["within"], s["beat_persistence"], s["direction_correct"]) == \
        ("scored", 8000, 1, 1, 1)


def test_ничего_не_изменилось_значит_прогноз_проиграл(pdb):
    po.attach_prediction(7, PRED, created=DAY0)
    _days({d: 6000 for d in range(14, 29)})
    po.score_due(DAY0 + timedelta(days=29))
    s = _status()
    assert (s["within"], s["beat_persistence"]) == (0, 0)


def test_нет_измерений_это_промах_и_он_в_калибровке(pdb):
    po.attach_prediction(7, PRED, created=DAY0)
    _days({20: 9000})                                             # одна точка < минимума
    po.score_due(DAY0 + timedelta(days=29))
    assert _status()["status"] == "missed"
    assert po.calibration()["n_missed"] == 1


def test_снятый_до_горизонта_протокол_не_оценивается(pdb):
    po.attach_prediction(7, PRED, created=DAY0)
    with health_db.get_conn() as c:
        c.execute("UPDATE protocols SET status='retired', retired_at=? WHERE id=7", (str(DAY0 + timedelta(days=5)),))
    po.score_due(DAY0 + timedelta(days=29))
    assert _status()["status"] == "void"
    assert po.calibration()["n_scored"] == po.calibration()["n_missed"] == 0


def test_анализ_до_протокола_не_засчитывается(pdb):
    with health_db.get_conn() as c:
        c.execute("INSERT INTO lab_results(date,source,test_name,value) VALUES (?,?,?,?)",
                  (str(DAY0 - timedelta(days=40)), "manual:test", "Ferritin", 18.0))
        c.execute("INSERT INTO lab_results(date,source,test_name,value) VALUES (?,?,?,?)",
                  (str(DAY0), "manual:test", "Ferritin", 40.0))             # в день рождения — это ещё «до»
    po.attach_prediction(7, {"metric": "Ферритин", "change": 15, "low": 5, "high": 30,
                             "horizon_days": 30}, created=DAY0)
    po.score_due(DAY0 + timedelta(days=80))
    assert _status()["status"] == "missed"


def test_анализ_в_другой_единице_не_сравнивается_с_базой(pdb):
    with health_db.get_conn() as c:
        c.execute("INSERT INTO lab_results(date,source,test_name,value,unit) VALUES (?,?,?,?,?)",
                  (str(DAY0 - timedelta(days=40)), "manual:test", "Glucose", 100.0, "mg/dL"))
        c.execute("INSERT INTO lab_results(date,source,test_name,value,unit) VALUES (?,?,?,?,?)",
                  (str(DAY0 + timedelta(days=30)), "manual:test", "Glucose", 5.2, "mmol/L"))
    po.attach_prediction(7, {"metric": "Glucose", "change": -5, "low": -15, "high": 0,
                             "horizon_days": 30}, created=DAY0)
    po.score_due(DAY0 + timedelta(days=80))
    assert _status()["status"] == "missed"                         # 5.2 ммоль/л не «упал на 95 мг/дл»
    with health_db.get_conn() as c:
        c.execute("UPDATE protocol_predictions SET status='open'")
        c.execute("INSERT INTO lab_results(date,source,test_name,value,unit) VALUES (?,?,?,?,?)",
                  (str(DAY0 + timedelta(days=31)), "manual:test", "Glucose", 92.0, "mg/dL"))
    po.score_due(DAY0 + timedelta(days=80))
    s = _status()
    assert (s["status"], s["observed"], s["within"]) == ("scored", 92.0, 1)


def test_датчик_на_базе_без_миграции_не_падает(db):
    """Снимок или монитор раньше перезапуска бота: таблицы ещё нет — не ошибка кода.
    Таблицу убираем явно: с 22.11 она есть в фикстуре (нить fixture-schema), а раньше тест
    держался на её отсутствии там — то есть на отставании фикстуры от боевой схемы."""
    with health_db.get_conn() as c:
        c.execute("DROP TABLE IF EXISTS protocol_predictions")
    assert po.scoring_liveness(DAY0) == (False, 0, None)


def test_датчик_оценщика():
    today = date(2026, 11, 20)
    assert po.scoring_stale("2026-11-10", today, 1, 8) is True
    assert po.scoring_stale("2026-11-15", today, 1, 8) is False
    assert po.scoring_stale(None, today, 0, 8) is False


# ── читатели ──

def test_консилиум_видит_оценку_кода_и_пересказ(pdb, monkeypatch):
    import hypothesis_consilium_eval as hce
    with health_db.get_conn() as c:
        c.execute("UPDATE protocols SET linked_hypothesis_id=42 WHERE id=7")
    po.attach_prediction(7, PRED, created=DAY0)
    _days({d: 8000 for d in range(14, 29)})
    po.score_due(DAY0 + timedelta(days=29))
    po.record_from_letter({"peer": "berrimor", "wrapped": "протокол #7: 20 из 28 с 02.10 по 29.10"},
                          today=DAY0 + timedelta(days=29))
    text = "\n".join(hce._protocol_block(42))
    assert "оценку прогноза ставит код" in text and "в интервал да" in text
    assert "20 из 28" in text and "со слов ассистента" in text


def test_протокол_сохраняется_вместе_с_прогнозом(pdb, monkeypatch):
    import hai_hypotheses
    monkeypatch.setattr(po, "get_today", lambda: DAY0)
    pid = hai_hypotheses.save_protocol({"title": "T", "behavior": "идти", "prediction": PRED})
    assert po.for_protocol(pid)["prediction"]["baseline"] == 6000
    pid2 = hai_hypotheses.save_protocol({"title": "T2", "behavior": "идти", "prediction": {"metric": "x"}})
    assert po.for_protocol(pid2)["prediction"] is None             # кривой прогноз не роняет протокол


# ── масштаб интервала (prediction-scale, 11.11) ──

def _sleep(pdb):
    """30 ночей до протокола: уровень ~5,85 ч, разброс ~0,6 ч — как у владельца 11.11 (синтетика)."""
    with health_db.get_conn() as c:
        for i in range(1, 31):
            c.execute("UPDATE daily_metrics SET sleep_total=? WHERE date=?",
                      (5.85 + (0.6 if i % 2 else -0.6), str(DAY0 - timedelta(days=i))))


def test_интервал_не_в_тех_единицах_отклоняется(pdb):
    _sleep(pdb)
    with pytest.raises(po.PredictionRejected, match="обычных разбросов"):   # минуты при уровне в часах
        po.attach_prediction(7, {"metric": "sleep_total", "change": 45, "low": 15, "high": 90,
                                 "horizon_days": 28}, created=DAY0)
    row = po.attach_prediction(7, {"metric": "sleep_total", "change": 1.0, "low": 0.3, "high": 1.5,
                                   "horizon_days": 28}, created=DAY0)
    assert row["metric"] == "sleep_total" and row["change"] == 1.0


def test_промпт_видит_уровень_и_разброс(pdb):
    _sleep(pdb)
    base, sd = po.metric_scales(DAY0)["sleep_total"]
    assert round(base, 2) == 5.85 and 0.5 < sd < 0.7
    assert po.metric_scales(DAY0)["steps"][1] is None     # постоянный ряд — разброса нет, предела нет


def test_масштаб_по_разбросу_а_не_по_уровню(pdb):
    """Уровень около нуля (как отклонение температуры владельца: 0,017 при разбросе 0,215): предел от
    уровня отбросил бы честный прогноз −0,2; предел от разброса — пропускает."""
    with health_db.get_conn() as c:
        for i in range(1, 31):
            c.execute("UPDATE daily_metrics SET temp_deviation=? WHERE date=?",
                      (0.02 + (0.2 if i % 2 else -0.2), str(DAY0 - timedelta(days=i))))
    row = po.attach_prediction(7, {"metric": "temp_deviation", "change": -0.2, "low": -0.4, "high": 0.0,
                                   "horizon_days": 28}, created=DAY0)
    assert row["metric"] == "temp_deviation"
