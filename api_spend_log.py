"""api_spend_log — логгер LLM-вызовов в logs/agent_api_spend.log (SX-20).

Универсальная обёртка, которую можно вызвать после каждого client.messages.create().
Формат записи — одна JSON-строка на вызов, аналогично logs/test_api_spend.log.

Использование:

    from api_spend_log import log_call
    resp = client.messages.create(model="...", ...)
    log_call(agent="literature_curator", model=resp.model or "haiku",
             tokens_in=resp.usage.input_tokens,
             tokens_out=resp.usage.output_tokens)
"""
from __future__ import annotations
from _time_inject import get_utcnow  # seam

import json
from datetime import datetime
from pathlib import Path

LOG_PATH = Path(__file__).parent / "logs" / "agent_api_spend.log"


def log_call(agent: str, model: str, tokens_in: int = 0, tokens_out: int = 0,
             cache_hit: bool = False, extra: dict | None = None) -> None:
    """Append one JSON-line per call. Fail-silent (логгер не должен ломать пайплайн)."""
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        rec = {
            "ts": get_utcnow().isoformat(timespec="seconds"),
            "agent": agent,
            "model": model,
            "tokens_in": int(tokens_in),
            "tokens_out": int(tokens_out),
            "cache_hit": bool(cache_hit),
        }
        if extra:
            rec.update(extra)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:  # silent-ok: log должен fail-silent
        pass
