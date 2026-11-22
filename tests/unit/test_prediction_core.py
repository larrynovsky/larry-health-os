"""Ядро прогноза, который оценивает код (prediction_core, вынесено 11.11). Чистый суд — без базы и модели;
приём и окно наблюдения закреплены через протокол в tests/unit/test_protocol_outcomes.py."""
import pytest

import prediction_core as pc

P = {"baseline": 20.0, "change": 3.0, "low": 1.0, "high": 5.0}


def test_суд_сбывшегося_прогноза():
    r = pc.judge_prediction(P, observed=23.0)
    assert (r["within"], r["beat_persistence"], r["direction_correct"]) == (1, 1, 1)


def test_ничего_не_изменилось_прогноз_проигрывает_инерции():
    r = pc.judge_prediction(P, observed=20.0)
    assert (r["within"], r["beat_persistence"]) == (0, 0)


def test_ушло_в_другую_сторону():
    r = pc.judge_prediction(P, observed=17.0)
    assert (r["within"], r["direction_correct"]) == (0, 0)


def test_прогноз_без_чисел_не_принимается():
    with pytest.raises(pc.PredictionRejected):
        pc.accept_prediction(None, "не словарь", created=None)
