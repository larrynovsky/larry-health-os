"""Прогноз гипотезы в числах и вердикт кода (hypothesis_verdict, Э1–Э2 плана plsi, 11.11).

Синтетическая БД через продакшн-миграции; модель подменена записывающим клиентом — сеть не вызывается.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

import health_db
import hypothesis_verdict as hv

DAY0 = date(2026, 9, 1)          # дата версии прогноза (синтетика)


class FakeClient:
    def __init__(self, answer: str):
        self.answer, self.prompts = answer, []
        self.messages = self

    def create(self, **kw):
        self.prompts.append(kw["messages"][0]["content"])
        return SimpleNamespace(content=[SimpleNamespace(text=self.answer)], stop_reason="end_turn")


@pytest.fixture
def hdb(db):
    health_db._migrate_protocol_outcomes()
    health_db._migrate_hypothesis_predictions()
    from tests.conftest import canon_schema
    canon_schema(health_db)
    with health_db.get_conn() as c:
        for i in range(1, 31):     # до даты прогноза: сон 6,0 ± 0,5
            c.execute("INSERT INTO daily_metrics(date, sleep_total) VALUES (?, ?)",
                      (str(DAY0 - timedelta(days=i)), 6.0 + (0.5 if i % 2 else -0.5)))
        c.execute("INSERT INTO lab_results(date, test_name, value, unit, source) VALUES ('2026-08-20','Ferritin',18.6,'ng/mL','manual:synthetic')")
        c.execute("INSERT INTO lab_results(date, test_name, value, unit, source) VALUES ('2026-10-15','Ferritin',99.9,'ng/mL','manual:synthetic')")
    return health_db


def _hyp(mid=41, prediction="Сон вырастет на час за 28 дней", version=1, history=None):
    payload = {"prediction": prediction, "version": version, "created_date": "2026-07-01",
               "history": history if history is not None else [{"date": str(DAY0), "version": 0}]}
    with health_db.get_conn() as c:
        c.execute("INSERT INTO memory(id, category, key, value, active) VALUES (?,?,?,?,1)",
                  (mid, "hypothesis", f"hyp_test_{mid}", json.dumps(payload, ensure_ascii=False)))
    return mid


SLEEP = json.dumps({"predictions": [{"metric": "sleep_total", "change": 1.0, "low": 0.3, "high": 1.5,
                                     "horizon_days": 28}]})


def test_дата_прогноза_это_дата_его_версии():
    assert hv.prediction_date({"created_date": "2026-07-01", "history": [{"date": "2026-08-29"}]}) == date(2026, 8, 29)
    assert hv.prediction_date({"created_date": "2026-07-01"}) == date(2026, 7, 1)


def test_структуратор_не_видит_значений_после_даты_и_анализов(hdb):
    _hyp()
    client = FakeClient(SLEEP)
    hv.structure(41, client=client)
    prompt = client.prompts[0]
    assert "Сон вырастет на час" in prompt and "sleep_total" in prompt and "Ferritin" in prompt
    assert "99.9" not in prompt and "18.6" not in prompt      # значения анализов модели не показываются


def test_цель_до_значения_переводится_кодом_от_базы(hdb):
    _hyp(prediction="Ферритин поднимется до 50–70")
    out = hv.structure(41, client=FakeClient(json.dumps({"predictions": [
        {"metric": "Ферритин", "target_low": 50, "target_high": 70, "horizon_days": 60}]})))
    p = out["saved"][0]
    assert p["metric"] == "Ferritin" and p["baseline"] == 18.6
    assert (round(p["low"], 1), round(p["high"], 1), round(p["change"], 1)) == (31.4, 51.4, 41.4)


def test_повтор_той_же_версии_без_вызова_модели(hdb):
    _hyp()
    client = FakeClient(SLEEP)
    hv.structure(41, client=client)
    hv.structure(41, client=client)
    assert len(client.prompts) == 2      # один разбор = два прочтения; повтор версии — без вызовов


def test_не_числовой_и_неразобранный_записываются(hdb):
    _hyp(41)
    _hyp(42, prediction="Опросник PHQ-9")
    assert hv.structure(42, client=FakeClient('{"predictions": []}'))["outcome"] == "not_numeric"
    assert hv.structure(41, client=FakeClient("я не знаю"))["outcome"] == "unparsed"
    assert hv.code_verdict(42)["status"] == "not_numeric"


def test_разбор_берёт_итоговый_объект_а_не_черновик():
    """Живой ответ 11.11 (#796): черновик, пояснение, исправленный итог — два объекта в одном ответе."""
    draft = '```json\n{"predictions": [{"metric": "LDL", "low": 1}, {"metric": "VLDL", "threshold": null}]}\n```'
    final = '```json\n{"predictions": [{"metric": "LDL", "low_pct": -10, "high_pct": -5}]}\n```'
    assert hv._parse(f"{draft}\nПримечания: {{не объект}} четвёртый пункт лишний.\n{final}\nЗамечание.") == [
        {"metric": "LDL", "low_pct": -10, "high_pct": -5}]
    assert hv._parse('Прогнозов нет.\n{"predictions": []}') == []
    assert hv._parse('{"metric": "LDL"}') is None and hv._parse("я не знаю") is None


def _after(value: float):
    with health_db.get_conn() as c:
        for i in range(1, 29):
            c.execute("INSERT INTO daily_metrics(date, sleep_total) VALUES (?, ?)", (str(DAY0 + timedelta(days=i)), value))


def test_сбывшийся_прогноз_подтверждает_тем_же_судом(hdb):
    _hyp()
    hv.structure(41, client=FakeClient(SLEEP))
    assert hv.code_verdict(41)["status"] == "undecided"           # срок не наступил
    _after(7.0)
    hv.score_hypothesis_predictions(today=DAY0 + timedelta(days=30))
    assert hv.code_verdict(41)["status"] == "confirmed"


def test_ушедший_в_другую_сторону_опровергает(hdb):
    _hyp()
    hv.structure(41, client=FakeClient(SLEEP))
    _after(5.0)
    hv.score_hypothesis_predictions(today=DAY0 + timedelta(days=30))
    assert hv.code_verdict(41)["status"] == "refuted"


def test_инерция_не_подтверждает(hdb):
    _hyp()
    hv.structure(41, client=FakeClient(SLEEP))
    _after(6.0)                                               # ничего не изменилось
    hv.score_hypothesis_predictions(today=DAY0 + timedelta(days=30))
    assert hv.code_verdict(41)["status"] == "undecided"


def test_переписанная_версия_делает_старый_прогноз_void(hdb):
    _hyp()
    hv.structure(41, client=FakeClient(SLEEP))
    with health_db.get_conn() as c:
        c.execute("UPDATE memory SET value=? WHERE id=41", (json.dumps(
            {"prediction": "другой", "version": 2, "history": [{"date": "2026-09-10"}]}),))
    hv.score_hypothesis_predictions(today=DAY0 + timedelta(days=30))
    with health_db.get_conn() as c:
        assert c.execute("SELECT status FROM hypothesis_predictions").fetchone()[0] == "void"
    assert hv.code_verdict(41)["status"] == "not_numeric"            # новая версия ещё не разобрана


def test_проход_разбирает_неразобранные_с_пределом(hdb, monkeypatch):
    for mid in (41, 42, 43):
        _hyp(mid)
    import config_db
    config_db.upsert_config("hypotheses.structure_per_pass", value_num=2)
    client = FakeClient(SLEEP)
    assert len(hv.structure_pending(client=client)) == 2
    assert len(hv.structure_pending(client=client)) == 1 and len(client.prompts) == 6


def test_консилиум_видит_вердикт_кода(hdb):
    import hypothesis_consilium_eval as hce
    _hyp()
    hv.structure(41, client=FakeClient(SLEEP))
    block = "\n".join(hce._verdict_block(41))
    assert "sleep_total" in block and "не решено" in block


def test_попадание_без_победы_над_инерцией_не_подтверждает(hdb):
    _hyp()
    hv.structure(41, client=FakeClient(SLEEP))
    _after(6.4)                       # +0,4 — в интервале 0,3…1,5, но ближе к «без изменений», чем к +1
    hv.score_hypothesis_predictions(today=DAY0 + timedelta(days=30))
    p = hv.code_verdict(41)
    assert p["predictions"][0]["within"] == 1 and p["status"] == "undecided"


def test_вердикт_кода_доходит_до_пакета_консилиума(hdb):
    import hypothesis_consilium_eval as hce
    _hyp()
    hv.structure(41, client=FakeClient(SLEEP))
    package = hce._build_eval_data_package({"memory_id": 41, "observation": "о", "mechanism": "м",
                                            "prediction": "Сон вырастет на час за 28 дней", "test": "т",
                                            "created_date": "2026-07-01"})
    assert "Вердикт кода: не решено" in package


# ── порог, проценты, условия (forecast-core, 11.11) ──

def _thr(threshold, bound="at_least", days=28):
    return json.dumps({"predictions": [{"metric": "sleep_total", "threshold": threshold, "bound": bound,
                                        "horizon_days": days}]})


def test_порог_достигнут_с_другой_стороны_подтверждает(hdb):
    _hyp()
    hv.structure(41, client=FakeClient(_thr(6.8)))            # база 6,0 — порог ещё не выполнен
    _after(7.0)
    hv.score_hypothesis_predictions(today=DAY0 + timedelta(days=30))
    assert hv.code_verdict(41)["status"] == "confirmed"
    assert "не меньше 6.8" in "\n".join(hv.verdict_lines(41))


def test_удержится_при_уже_выполненном_пороге_не_подтверждает_но_опровергается(hdb):
    _hyp(41); _hyp(42)
    hv.structure(41, client=FakeClient(_thr(5.5)))            # база 6,0 уже ≥ 5,5
    hv.structure(42, client=FakeClient(_thr(5.5)))
    _after(6.2)
    hv.score_hypothesis_predictions(today=DAY0 + timedelta(days=30))
    assert hv.code_verdict(41)["status"] == "undecided"       # инерция предсказывала то же
    with health_db.get_conn() as c:
        c.execute("UPDATE hypothesis_predictions SET status='open', observed=NULL, within=NULL WHERE memory_id=42")
        c.execute("UPDATE daily_metrics SET sleep_total=5.0 WHERE date>?", (str(DAY0),))
    hv.score_hypothesis_predictions(today=DAY0 + timedelta(days=30))
    assert hv.code_verdict(42)["status"] == "refuted"         # порог перестал выполняться


def test_проценты_переводит_код_от_базы(hdb):
    _hyp(prediction="Ферритин вырастет на 50–100%")
    out = hv.structure(41, client=FakeClient(json.dumps({"predictions": [
        {"metric": "Ferritin", "change_pct": 75, "low_pct": 50, "high_pct": 100, "horizon_days": 60}]})))
    p = out["saved"][0]
    assert (round(p["low"], 2), round(p["change"], 2), round(p["high"], 2)) == (9.3, 13.95, 18.6)


def test_промпт_запрещает_условия_и_планы_и_знает_порог(hdb):
    _hyp()
    client = FakeClient(SLEEP)
    hv.structure(41, client=client)
    prompt = client.prompts[0]
    assert "условие для решения" in prompt and "план обследования" in prompt and "at_least" in prompt
    assert "в условии или в средстве" in prompt and "РЕЗУЛЬТАТ" in prompt
    assert "верхнюю границу" in prompt and "только когда срока в тексте нет" in prompt    # срок — из текста


def test_порог_у_протокола_не_принимается(hdb):
    import prediction_core as pc
    with health_db.get_conn() as c, pytest.raises(pc.PredictionRejected):
        pc.accept_prediction(c, {"metric": "sleep_total", "threshold": 7, "bound": "at_least",
                                 "horizon_days": 28}, DAY0)


def test_порог_не_больше_судится_в_свою_сторону(hdb):
    _hyp()
    hv.structure(41, client=FakeClient(_thr(5.5, bound="at_most")))   # база 6,0 — ещё выше порога
    _after(5.2)
    hv.score_hypothesis_predictions(today=DAY0 + timedelta(days=30))
    assert hv.code_verdict(41)["status"] == "confirmed"


def test_анализ_в_день_прогноза_и_есть_база(hdb):
    """Замер 11.11, #795: версия от 29.08 написана с анализом от 29.08; «строго до» брало прежний забор."""
    with health_db.get_conn() as c:
        c.execute("INSERT INTO lab_results(date, test_name, value, unit, source) VALUES (?,'Ferritin',25.0,'ng/mL','manual:synthetic')",
                  (str(DAY0),))
    _hyp(prediction="Ферритин вырастет до 30–35")
    saved = hv.structure(41, client=FakeClient(json.dumps({"predictions": [
        {"metric": "Ferritin", "horizon_days": 30, "target_low": 30, "target_high": 35}]})))["saved"]
    assert (saved[0]["baseline"], saved[0]["low"], saved[0]["high"]) == (25.0, 5.0, 10.0)


def test_назван_только_интервал_точка_его_середина(hdb):
    """Живые ответы 11.11 (#796): «на 5–10%» приходит без точки — раньше такой прогноз отклонялся."""
    _hyp(prediction="Сон вырастет на 0,3–1,5 ч; ферритин на 10–30%")
    saved = hv.structure(41, client=FakeClient(json.dumps({"predictions": [
        {"metric": "sleep_total", "horizon_days": 28, "low": 1.5, "high": 0.3},      # границы в любом порядке
        {"metric": "Ferritin", "horizon_days": 30, "low_pct": 10, "high_pct": 30,      # чужие формы — null
         "change": None, "target_low": None, "threshold": None, "bound": None}]})))["saved"]
    assert [round(p["change"], 2) for p in saved] == [0.9, 3.72]


def _hyp_with(mid, **extra):
    _hyp(mid)
    with health_db.get_conn() as c:
        v = json.loads(c.execute("SELECT value FROM memory WHERE id=?", (mid,)).fetchone()[0])
        c.execute("UPDATE memory SET value=? WHERE id=?", (json.dumps({**v, **extra}, ensure_ascii=False), mid))


def test_прогноз_от_рождения_принимается_без_модели_тем_же_приёмом(hdb):
    """Этап В: консилиум назвал числа сам — переводчик прозы не зовётся; кривое отклоняет код."""
    _hyp_with(41, forecast=[{"metric": "sleep_total", "horizon_days": 28, "low": 0.3, "high": 1.5},
                            {"metric": "нет_такого", "horizon_days": 28, "low": 1, "high": 2}, "мусор"])
    client = FakeClient("не должен вызываться")
    r = hv.structure(41, client=client)
    assert client.prompts == [] and r["outcome"] == "numeric"
    assert [p["metric"] for p in r["saved"]] == ["sleep_total"] and len(r["rejected"]) == 1


def test_пустой_прогноз_от_рождения_с_причиной_это_не_числовая(hdb):
    _hyp_with(41, forecast=[], no_forecast_reason="тема — ноготь, измеримого показателя нет")
    client = FakeClient(SLEEP)
    assert hv.structure(41, client=client)["outcome"] == "not_numeric" and client.prompts == []
    with health_db.get_conn() as c:
        assert "ноготь" in c.execute("SELECT note FROM hypothesis_prediction_runs WHERE memory_id=41").fetchone()[0]


def test_рождение_гипотезы_кривой_прогноз_не_отменяет_и_старые_вызовы_целы(hdb):
    import hai_hypotheses as hh
    args = dict(observation="о", mechanism="м", prediction="п", test="т")
    def payload(mid):
        with health_db.get_conn() as c:
            return json.loads(c.execute("SELECT value FROM memory WHERE id=?", (mid,)).fetchone()[0])
    assert "forecast" not in payload(hh.save_hypothesis(**args))                       # старый вызов
    assert "forecast" not in payload(hh.save_hypothesis(**{**args, "observation": "о2"}, forecast="строка"))
    p = payload(hh.save_hypothesis(**{**args, "observation": "о3"}, forecast=[{"metric": "hrv"}, 7], no_forecast_reason="x"))
    assert p["forecast"] == [{"metric": "hrv"}] and "no_forecast_reason" not in p and p["prediction"] == "п"
    p = payload(hh.save_hypothesis(**{**args, "observation": "о4"}, forecast=[], no_forecast_reason="нет показателя"))
    assert p["forecast"] == [] and p["no_forecast_reason"] == "нет показателя"


def test_два_прочтения_разошлись_прогноз_не_сохраняется(hdb):
    """Замер 12.11: модель расходится сама с собой на двусмысленном тексте — такое код не судит."""
    _hyp()
    other = json.dumps({"predictions": [{"metric": "sleep_total", "change": 0.2, "low": 0.1, "high": 0.3, "horizon_days": 28}]})
    client = FakeClient(SLEEP)
    answers = iter([SLEEP, other])
    client.create = lambda **kw: SimpleNamespace(content=[SimpleNamespace(text=next(answers))], stop_reason="end_turn")
    r = hv.structure(41, client=client)
    assert r == dict(outcome="unparsed", saved=[], rejected=[])
    with health_db.get_conn() as c:
        assert c.execute("SELECT note FROM hypothesis_prediction_runs WHERE memory_id=41").fetchone()[0] == hv.UNSTABLE
        assert c.execute("SELECT count(*) FROM hypothesis_predictions").fetchone()[0] == 0
    assert hv.code_verdict(41)["status"] == "not_numeric"


def test_расхождение_в_отклонённом_мусоре_не_мешает(hdb):
    """Сравнивается то, что код принял бы, а не сырой ответ: лишний кривой пункт во втором прочтении не в счёт."""
    _hyp()
    noisy = json.dumps({"predictions": json.loads(SLEEP)["predictions"] + [{"metric": "нет_такого", "horizon_days": 5}]})
    client = FakeClient(SLEEP)
    answers = iter([SLEEP, noisy])
    client.create = lambda **kw: SimpleNamespace(content=[SimpleNamespace(text=next(answers))], stop_reason="end_turn")
    assert hv.structure(41, client=client)["outcome"] == "numeric"


def test_проценты_у_показателя_в_процентах_это_пункты(hdb):
    """HbA1c 6,0%: «снизится на 0,1–0,2%» — это до 5,8–5,9, а не на 0,006–0,012."""
    with health_db.get_conn() as c:
        c.execute("INSERT INTO lab_results(date, test_name, value, unit, source) VALUES ('2026-08-20','HbA1c',6.0,'%','manual:synthetic')")
    _hyp(prediction="HbA1c снизится на 0,1–0,2%")
    saved = hv.structure(41, client=FakeClient(json.dumps({"predictions": [
        {"metric": "HbA1c", "horizon_days": 30, "low_pct": -0.2, "high_pct": -0.1}]})))["saved"]
    assert (round(saved[0]["low"], 3), round(saved[0]["high"], 3)) == (-0.2, -0.1)
