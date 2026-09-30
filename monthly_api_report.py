#!/usr/bin/env python3.11
"""
monthly_api_report.py — месячный отчёт API-трат на тестовые diagnosis-вызовы.

Читает `logs/test_api_spend.log` (одна JSON-запись на строку), агрегирует
за прошедший месяц, шлёт в TG.

Каждая запись из test_failure_handler:
    {ts, test_id, model, tokens_in, tokens_out, cache_hit}

Запускается через launchd `com.larry.health.test-api-report` 1-го числа в 09:30.
См. ROADMAP T-0.17.

CLI:
    python3.11 monthly_api_report.py [--month YYYY-MM]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))

from _time_inject import get_today

LOGS_DIR = SCRIPT_DIR / "logs"
SPEND_LOG = LOGS_DIR / "test_api_spend.log"
AGENT_SPEND_LOG = LOGS_DIR / "agent_api_spend.log"  # SX-20: survivorship agents


# ── Цены (USD per 1M tokens) — обновляйте при изменении прайса Anthropic ──
PRICES = {
    "claude-haiku-4-5": {"input": 1.0, "output": 5.0},
    "claude-haiku-4-5-20251001": {"input": 1.0, "output": 5.0},
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0},
    "claude-opus-4-7": {"input": 15.0, "output": 75.0},
}


def _cost(model: str, tokens_in: int, tokens_out: int) -> float:
    p = PRICES.get(model, {"input": 1.0, "output": 5.0})
    return (tokens_in * p["input"] + tokens_out * p["output"]) / 1_000_000


def _read_log_lines(path):
    """SX-20: read JSON-lines из любого spend log, безопасно."""
    if not path.exists():
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out


def aggregate(month: str) -> dict:
    """month: 'YYYY-MM'. Возвращает dict с total + per_test + cache_stats."""
    if not SPEND_LOG.exists():
        return {"month": month, "total_usd": 0, "calls": 0,
                "cache_hit_ratio": None, "per_test": {}, "note": "log not found"}

    per_test: dict[str, dict] = defaultdict(
        lambda: {"calls": 0, "tokens_in": 0, "tokens_out": 0,
                 "cache_hits": 0, "cost_usd": 0.0})
    total = {"calls": 0, "cache_hits": 0,
             "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0}

    sources = _read_log_lines(SPEND_LOG) + _read_log_lines(AGENT_SPEND_LOG)
    for rec in sources:
        if not rec.get("ts", "").startswith(month):
            continue

        tid = rec.get("test_id") or rec.get("agent") or "?"
        cache_hit = bool(rec.get("cache_hit"))
        tin = int(rec.get("tokens_in", 0))
        tout = int(rec.get("tokens_out", 0))
        model = rec.get("model", "")
        cost = 0.0 if cache_hit else _cost(model, tin, tout)

        per_test[tid]["calls"] += 1
        per_test[tid]["tokens_in"] += tin
        per_test[tid]["tokens_out"] += tout
        per_test[tid]["cache_hits"] += int(cache_hit)
        per_test[tid]["cost_usd"] += cost

        total["calls"] += 1
        total["cache_hits"] += int(cache_hit)
        total["tokens_in"] += tin
        total["tokens_out"] += tout
        total["cost_usd"] += cost

    cache_hit_ratio = (total["cache_hits"] / total["calls"]
                       if total["calls"] else None)
    # Топ-5 «дорогих» по cost
    top = sorted(per_test.items(), key=lambda x: x[1]["cost_usd"], reverse=True)[:5]
    return {
        "month": month,
        "total_usd": round(total["cost_usd"], 4),
        "calls": total["calls"],
        "cache_hits": total["cache_hits"],
        "cache_hit_ratio": cache_hit_ratio,
        "tokens_in": total["tokens_in"],
        "tokens_out": total["tokens_out"],
        "top_tests": [{"test_id": tid, **stats} for tid, stats in top],
    }


def format_for_tg(report: dict) -> str:
    if report.get("note"):
        return f"🧪 Тесты — траты за {report['month']}\nЛог трат пуст."
    cache_pct = (f"{int(report['cache_hit_ratio']*100)}%"
                 if report["cache_hit_ratio"] is not None else "—")
    lines = [
        f"🧪 Тесты — траты за {report['month']}",
        f"Всего: ${report['total_usd']:.4f}",
        f"Вызовов: {report['calls']} (cache hit ratio: {cache_pct})",
        f"Токены: in={report['tokens_in']:,} · out={report['tokens_out']:,}",
    ]
    if report["top_tests"]:
        lines.append("")
        lines.append("Топ «дорогих»:")
        for t in report["top_tests"]:
            lines.append(f"  {t['test_id']}  ${t['cost_usd']:.4f} ({t['calls']} вызова)")
    return "\n".join(lines)


def send_to_tg(text: str) -> None:
    """Совместимый вход: отложить краткую строку расходов до понедельника."""
    import notify
    notify.weekly(text)


def _previous_month(today_str: str) -> str:
    """'2026-05-08' → '2026-04'."""
    from datetime import date
    d = date.fromisoformat(today_str)
    if d.month == 1:
        return f"{d.year - 1}-12"
    return f"{d.year}-{d.month - 1:02d}"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--month", default=None,
                    help="YYYY-MM; по умолчанию прошлый месяц")
    p.add_argument("--no-send", action="store_true",
                    help="не отправлять в TG (для тестов)")
    args = p.parse_args()

    month = args.month or _previous_month(str(get_today()))
    report = aggregate(month)
    text = format_for_tg(report)
    print(text)
    print()
    print(json.dumps(report, ensure_ascii=False, indent=2), file=sys.stderr)

    if not args.no_send:
        import i18n
        send_to_tg(i18n.t("owner.weekly.cost", month=month, cost=f"{report['total_usd']:.2f}"))
        # Датчик жизненного цикла LLM-моделей: пинг MODEL_DEFAULTS, громкий
        # алерт при отзыве модели (см. model_health_check.py).
        try:
            import model_health_check
            model_health_check.run_check(notify=True)
        except Exception as e:  # silent-ok: вторичный датчик не должен ронять отчёт
            print(f"model_health_check failed: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
