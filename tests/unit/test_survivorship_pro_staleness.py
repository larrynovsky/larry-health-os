"""Оракул предела давности самоотчёта и якоря окна проксей (решение владельца 13.09).

Старый самоотчёт нельзя сопоставлять с показаниями приборов у даты прогона:
так различие двух моментов времени ошибочно превращается в конфликт источников.
Давность ограничивается периодичностью опросника, окно приборов — датой ответа.

Правка двойная и проверяется раздельно:
  (а) предел — самоотчёт старше cadence_days инструмента в сравнение не идёт;
  (б) якорь — окно проксей считается ОТ ДАТЫ САМООТЧЁТА, не от даты прогона.
Каждая из двух закрывает случай сама по себе; вместе они делают расхождение источников
во времени невозможным по построению.

Граница честно: доступ к БД здесь подменён на двух швах (_latest_pro_per_subscale,
_metric_window_mean). Проверяется РЕШЕНИЕ на заданных входах, не работа SQLite.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import survivorship_analyzer as sa

RUN_DAY = date(2043, 5, 16)
INSTRUMENT = {
    "id": "mfsi_sf",
    "cadence_days": 90,
    "shadow_rules": [{"item_or_subscale": "physical_fatigue",
                      "proxies": ["active_kcal", "steps"],
                      "min_window_days": 7,
                      "divergence_threshold": 0.4,
                      "note": "физическая фатига vs активность"}],
}


def _stub(monkeypatch, pro_date):
    """Самоотчёт указанной даты + прокси, помнящие, за какой день их спросили."""
    asked = {}
    monkeypatch.setattr(sa, "_latest_pro_per_subscale",
                        lambda _id, **kw: {"mfsi_sf_physical_fatigue": (0.0, pro_date)})

    def fake_mean(metric, end_date, window_days):
        asked[metric] = end_date
        return (100.0, 8)
    monkeypatch.setattr(sa, "_metric_window_mean", fake_mean)
    return asked


def test_stale_pro_does_not_produce_conflict(monkeypatch):
    """Придуманный самоотчёт старше допустимой периодичности не даёт конфликт."""
    _stub(monkeypatch, "2021-05-15")
    out = sa._analyse_instrument(INSTRUMENT, date(2021, 9, 10))
    kinds = [f["type"] for f in out]
    assert "pro_stale" in kinds, f"предел не сработал: {kinds}"
    assert "shadow_check" not in kinds, "конфликт родился на просроченном самоотчёте"
    stale = next(f for f in out if f["type"] == "pro_stale")
    assert stale["age_days"] == 118 and stale["cadence_days"] == 90


def test_fresh_pro_still_produces_conflict(monkeypatch):
    """Негативный контроль к (а): предел не должен глушить ВСЁ подряд.

    Без этой проверки первый тест зеленел бы и на правке «никогда не считать shadow_check»,
    то есть доказывал бы отсутствие механизма, а не его работу."""
    _stub(monkeypatch, "2043-04-27")           # придуманный ответ: за 19 дней до прогона, свежий
    out = sa._analyse_instrument(INSTRUMENT, RUN_DAY)
    kinds = [f["type"] for f in out]
    assert "shadow_check" in kinds and "pro_stale" not in kinds, kinds


def test_proxy_window_is_anchored_on_pro_date(monkeypatch):
    """(б) Позитив: прокси спрашиваются за дату САМООТЧЁТА, а не за день прогона."""
    asked = _stub(monkeypatch, "2043-04-27")
    out = sa._analyse_instrument(INSTRUMENT, RUN_DAY)
    f = next(x for x in out if x["type"] == "shadow_check")
    assert f["proxy_window_end"] == "2043-04-27"
    assert f["proxy_anchored_on_pro"] is True
    assert set(asked.values()) == {date(2043, 4, 27)}, (
        f"окно всё ещё считается от дня прогона: {asked}")


def test_unreadable_pro_date_is_not_treated_as_fresh(monkeypatch):
    """Граница: нечитаемая дата — «судить не на чем», а не «самоотчёт свежий».

    Предел пропускается (None), окно падает обратно на день прогона и ЧЕСТНО помечается
    флагом — чтобы читатель находки видел, что источники могли разъехаться."""
    assert sa._pro_age_days("не дата", RUN_DAY) is None
    _stub(monkeypatch, "не дата")
    f = next(x for x in sa._analyse_instrument(INSTRUMENT, RUN_DAY) if x["type"] == "shadow_check")
    assert f["proxy_anchored_on_pro"] is False
