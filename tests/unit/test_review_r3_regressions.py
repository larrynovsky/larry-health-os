"""Регрессии на шесть находок третьего внешнего ревью (2026-07-26, якорь 533af69).

Каждая находка была воспроизведена автором независимо ПЕРЕД правкой; ложных не было.
Здесь — негативные контроли: откат соответствующего фикса обязан красить тест.

Общий класс пяти находок из шести: **claim сильнее механизма**. Комментарий обещал отказ
неизвестной схеме — читатель её не смотрел. Докстринг обещал liveness-датчик — его не было.
Реестр объявлял «ни одна лаб-пара не войдёт в веру» — при готовом кадре входила. Поэтому
тесты здесь проверяют не наличие кода, а невозможность обойти обещание.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

import pandas as pd
import pytest

pytestmark = pytest.mark.unit
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import belief_contract as bc
import correlation_gate as G
import longitudinal_analysis as la
import signal_family as sf

_META = {"gate_applied": True, "status": "applied", "stratified": {"lagged": []}}
_YEARLY = pd.DataFrame([{"year": 2026}])


def _corr():
    return pd.DataFrame([{"metric_a": "sleep_total", "metric_b": "hrv", "spearman_r": 0.7,
                          "p_value": 1e-8, "p_perm": 0.002, "gate_pass": True,
                          "derived": False, "coverage": 0.9}])


# ── VG-R3-01: носитель события карантина ─────────────────────────────────────

@pytest.mark.parametrize("payload,label", [("{не json", "битый"),
                                           ('{"a": 1}', "не-список"),
                                           ("[]", "пустой")])
def test_unreadable_quarantine_carrier_refuses_instead_of_empty(tmp_path, payload, label):
    """Нечитаемый носитель = отказ, а не «прибытий не было».

    До правки `_entered_pairs` возвращала [] на всех трёх входах, карантин выходил пустым, и
    прошедшая пара уходила в веру БЕЗ `pending_adjudication` — мимо человеческого гейта.
    Соседний датчик кричал о порче в 07:50, через пять часов после публикации веры."""
    art = tmp_path / f"ps_{label}.json"
    art.write_text(payload, encoding="utf-8")
    with pytest.raises(la.QuarantineCarrierUnreadable):
        la._entered_pairs(artifact=art)


def test_missing_carrier_is_legitimate(tmp_path):
    """Позитивный контроль: файла нет — это первый прогон вообще, а не порча."""
    assert la._entered_pairs(artifact=tmp_path / "нет.json") == []


def test_carrier_failure_blocks_publication(tmp_path):
    """Отказ носителя ведёт в ту же ветку, что отказ гейта: вера не публикуется."""
    meta = dict(_META)
    meta.update(status="failed", failure="quarantine_carrier",
                error="носитель карантина нечитаем — ps.json: снимок пуст или неформатен")
    assert la._publication_decision(meta) is False
    with pytest.raises(ValueError, match="гейт"):
        la.save_to_agent_reports({"gate": meta, "top_correlations": []})


# ── VG-R3-02: квитанция не должна опережать публикацию ───────────────────────

def test_receipt_published_flag_is_about_actual_write(tmp_path):
    """`published` означает «вера ЗАПИСАНА». Раньше квитанция писалась за 45 строк до записи
    в БД, и падение любого шага публикации оставляло `published=true` без строки веры."""
    art = tmp_path / "r.json"
    la._write_gate_run_receipt(_META, False, art, error="публикация упала: RuntimeError('boom')")
    rec = json.loads(art.read_text(encoding="utf-8"))
    assert rec["status"] == "failed" and rec["published"] is False and "boom" in rec["error"]


def test_dry_run_receipt_does_not_claim_publication(tmp_path):
    """`--no-db`: прогон успешен, но веры не пишет — `published=true` было бы ложью."""
    art = tmp_path / "r.json"
    la._write_gate_run_receipt(_META, False, art, dry_run=True)
    rec = json.loads(art.read_text(encoding="utf-8"))
    assert rec["status"] == "applied" and rec["published"] is False and rec["dry_run"] is True
    assert "error" not in rec


def test_receipt_never_claims_publication_that_did_not_happen(tmp_path, monkeypatch):
    """Замена тесту по ИСХОДНИКУ (2026-07-26, ревью R4 — тест снят мной осознанно).

    Прежняя версия искала подстроки `save_to_agent_reports(summary)` и
    `_write_gate_run_receipt(gate_meta, bool(save_db)` в тексте `run()` и сравнивала их
    позиции. Такой оракул проверяет ФОРМУ КОДА, а не поведение: он оставался зелёным на
    сломанном пути (R4 нашёл четыре выхода из `run()`, и квитанция стояла не на всех — а
    подстроки лежали в правильном порядке) и краснел на исправленном (публикация уехала в
    `_run_body`, квитанция — в `finally`). Ровно то, за что ревью и ругало: claim про
    порядок был сильнее механизма.

    Здесь оракул поведенческий: тело не дошло до записи веры ⇒ на диске `published: false`.
    Порядок при этом не описывается вовсе — он проверяется следствием, а не буквой.
    """
    receipt = tmp_path / "gate_run_receipt.json"
    monkeypatch.setattr(la, "GATE_RUN_RECEIPT", receipt)
    monkeypatch.setattr(la, "_run_body", lambda *a, **k: None)   # веры не записал
    la.run(out_path=tmp_path / "o.xlsx", save_db=True)
    assert json.loads(receipt.read_text(encoding="utf-8"))["published"] is False


# ── VG-R3-04: scope на последней точке перед верой ───────────────────────────

def test_prepared_lab_frame_cannot_bypass_descoped_scope(monkeypatch):
    """Кадр с уже проставленным `gate_pass=True` не проходит мимо запрета.

    Контракт объявлен сквозным («ни одна лаб-пара не может войти в веру»), а держался на одном
    вызывающем: `build_ai_summary` смотрел только на готовую колонку."""
    monkeypatch.setattr(sf, "LABS_ACTIVE", False)
    lab = pd.DataFrame([{"lab": "HGB", "metric": "hrv", "spearman_r": 0.8, "p_value": 1e-9,
                         "p_perm": 1e-9, "r_detrended": 0.8, "gate_pass": True}])
    with pytest.raises(ValueError, match="scope"):
        la.build_ai_summary(_YEARLY, pd.DataFrame(), {}, _corr(), lab, [], _META)


def test_descoped_lab_frame_without_passes_is_simply_empty(monkeypatch):
    """Штатный кадр при descoped (всё False) — не ошибка, просто пустой лаб-список."""
    monkeypatch.setattr(sf, "LABS_ACTIVE", False)
    lab = pd.DataFrame([{"lab": "HGB", "metric": "hrv", "spearman_r": 0.3, "p_value": 0.4,
                         "p_perm": None, "r_detrended": None, "gate_pass": False}])
    s = la.build_ai_summary(_YEARLY, pd.DataFrame(), {}, _corr(), lab, [], _META)
    assert s["lab_metric_correlations"] == []


def test_active_scope_still_publishes(monkeypatch):
    """Позитивный контроль: при active путь работает как раньше."""
    monkeypatch.setattr(sf, "LABS_ACTIVE", True)
    lab = pd.DataFrame([{"lab": "HGB", "metric": "hrv", "spearman_r": 0.8, "p_value": 1e-9,
                         "p_perm": 1e-9, "r_detrended": 0.8, "gate_pass": True}])
    s = la.build_ai_summary(_YEARLY, pd.DataFrame(), {}, _corr(), lab, [], _META)
    assert len(s["lab_metric_correlations"]) == 1


# ── VG-R3-05: неизвестная версия схемы ───────────────────────────────────────

def _belief_conn(payload):
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE agent_reports (id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT,"
              " agent_type TEXT, findings TEXT)")
    c.execute("INSERT INTO agent_reports (date, agent_type, findings) VALUES (?,?,?)",
              ("2026-07-26", "longitudinal_analysis", json.dumps(payload)))
    return c


_GOOD_GATE = {"gate_applied": True, "status": "applied"}
_NOWHERE = Path(tempfile.gettempdir()) / "нет-такой-квитанции.json"


def test_future_schema_is_refused():
    """Комментарий обещал отказ неизвестной схеме; читатель её не смотрел вовсе."""
    b = bc.read_belief(_belief_conn({"schema_version": 999, "gate": _GOOD_GATE,
                                     "top_correlations": []}), receipt_path=_NOWHERE)
    assert b["accepted"] is False and "schema_too_new" in b["reason"]


def test_unreadable_schema_is_refused():
    b = bc.read_belief(_belief_conn({"schema_version": "новая", "gate": _GOOD_GATE,
                                     "top_correlations": []}), receipt_path=_NOWHERE)
    assert b["accepted"] is False and "schema_unreadable" in b["reason"]


def test_current_and_legacy_schema_accepted():
    """Позитивный контроль с обеих сторон: текущая версия и строка v1 (без поля) — годны."""
    cur = bc.read_belief(_belief_conn({"schema_version": bc.BELIEF_SCHEMA_VERSION,
                                       "gate": _GOOD_GATE, "top_correlations": []}),
                         receipt_path=_NOWHERE)
    legacy = bc.read_belief(_belief_conn({"gate": {"gate_applied": True},
                                          "top_correlations": []}), receipt_path=_NOWHERE)
    assert cur["accepted"] is True and legacy["accepted"] is True


# ── VG-R3-06: провенанс выборки при выключенном пути ─────────────────────────

def test_sample_provenance_available_while_scope_is_off():
    """Оракул паритета обязан работать в том режиме, в котором система живёт.

    Раньше `lab_obs_n` считался только при `active`, и премисса «паритет измерим без включения
    scope» была неверна: измерение требовало временно поднять флаг."""
    labs = pd.DataFrame([{"date": "2026-01-01", "test_name": "HGB", "value": 120},
                         {"date": "2026-02-01", "test_name": "Hemoglobin", "value": 130},
                         {"date": "2026-03-01", "test_name": "HGB", "value": 125}])
    lc = pd.DataFrame([{"lab": "HGB", "metric": "hrv", "spearman_r": 0.5, "p_value": 0.01,
                        "n": 3, "significant": True, "strong": True}])
    lg = G._labs_not_evaluated(lc, labs)
    assert lg.attrs["lab_obs_n"]["HGB"] == 3, "варианты имени обязаны складываться по канону"
    assert bool(lg["gate_pass"].any()) is False, "провенанс не должен включать путь"
