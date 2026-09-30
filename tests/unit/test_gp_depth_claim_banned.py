"""GP не имеет права называть глубину базы (решение владельца 2026-08-10).

Возраст базы не равен длине пересечения непустых рядов. Независимый пример
с разными датами начала ниже проверяет именно это различие.

Контроль слаб ЧЕСТНО и об этом сказано вслух: он проверяет, что требование
стоит в промпте, а не что модель ему следует. Второе доказывается только живым
отчётом и датчиком UC-B-09, который ловит следствие. Слабый контроль, названный
слабым, лучше сильного на словах.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def test_rule_text_exists_and_is_specific():
    """Правило существует и говорит ПРО ЧТО, а не «будь аккуратен»."""
    import gp_agent
    rule = gp_agent._DEPTH_RULE
    assert "ГЛУБИНА ДАННЫХ" in rule
    assert "подтверждено на N годах" in rule, "запрет обязан цитировать саму форму"
    assert "пересечению непустых" in rule, "правило обязано объяснять, чем глубина ЯВЛЯЕТСЯ"


def test_rule_reaches_both_gp_prompts():
    """Правило доезжает до ОБОИХ промптов — недельного и месячного.

    Половина покрытия здесь хуже, чем отсутствие: месячный отчёт как раз тот,
    где соблазн сказать «за N лет» максимален.
    """
    src = (ROOT / "gp_agent.py").read_text(encoding="utf-8")
    weekly = src.split("def _build_gp_system_prompt")[1].split("def _build_gp_monthly_prompt")[0]
    monthly = src.split("def _build_gp_monthly_prompt")[1].split("\ndef ")[0]
    assert "_DEPTH_RULE" in weekly, "недельный промпт без запрета"
    assert "_DEPTH_RULE" in monthly, "месячный промпт без запрета"


def test_the_measured_depth_is_what_the_rule_claims(db):
    """Независимые вымышленные строки: совместные наблюдения начинаются позже базы."""
    db.add_daily_metrics("2032-01-02", hrv=41.0)
    db.add_daily_metrics("2032-02-03", hrv=47.0, sleep_deep=1.3)
    row = db.execute(
        "SELECT (SELECT MIN(date) FROM daily_metrics), "
        "       (SELECT MIN(date) FROM daily_metrics "
        "        WHERE hrv IS NOT NULL AND sleep_deep IS NOT NULL)").fetchone()
    assert tuple(row) == ("2032-01-02", "2032-02-03")
    assert row[1] > row[0]


if __name__ == "__main__":
    for _n, _f in sorted(globals().items()):
        if _n.startswith("test_"):
            _f()
            print("ok", _n)
