#!/usr/bin/env python3.11
"""
test_trend_alerts — оконная логика _breached (без БД).

Покрывает consecutive/avg × floor/ceiling + недобор данных. Регресс-гард для
BL-ALERT-TREND-1 (sleep_score, sleep_deep 3-ночи и др.).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import trend_alerts as ta


def test_consecutive_floor_all_breach():
    # 3 ночи подряд <0.6 → сработало (свежие первыми)
    hit, mn = ta._breached([0.58, 0.46, 0.51], 3, "floor", 0.6, "consecutive")
    assert hit and abs(mn - 0.46) < 1e-9


def test_consecutive_floor_one_ok_no_fire():
    # третья ночь = 0.70 (не <0.6) → не сработало
    hit, _ = ta._breached([0.58, 0.46, 0.70], 3, "floor", 0.6, "consecutive")
    assert not hit


def test_insufficient_data_no_fire():
    # только 2 значения при окне 3 → консервативно не срабатывает
    hit, _ = ta._breached([0.4, 0.4], 3, "floor", 0.6, "consecutive")
    assert not hit


def test_avg_floor_mean_breaches():
    # среднее 14 дней <70 → сработало
    vals = [68, 69, 65, 70, 66] + [69] * 9
    hit, m = ta._breached(vals, 14, "floor", 70.0, "avg")
    assert hit and m < 70


def test_avg_floor_mean_ok():
    # среднее 72.7 (>70) → не сработало (синтетика формы случая 2026-07-04)
    vals = [78, 71, 67, 73, 80, 66, 70, 75, 68, 72, 74, 69, 76, 79]
    hit, m = ta._breached(vals, 14, "floor", 70.0, "avg")
    assert not hit and 72 < m < 73


def test_consecutive_ceiling():
    # 2 дня подряд >12000 → сработало
    hit, mx = ta._breached([12500, 13000], 2, "ceiling", 12000.0, "consecutive")
    assert hit and mx == 13000


if __name__ == "__main__":
    for fn in [test_consecutive_floor_all_breach, test_consecutive_floor_one_ok_no_fire,
               test_insufficient_data_no_fire, test_avg_floor_mean_breaches,
               test_avg_floor_mean_ok, test_consecutive_ceiling]:
        fn()
    print("TEST PASS")
