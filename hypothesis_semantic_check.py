#!/usr/bin/env python3.11
"""hypothesis_semantic_check — семантическая дедупликация гипотез.

Вызывается ПЕРЕД save_hypothesis из любого источника (literature_curator,
survivorship_curator, monthly_consilium, manual). Использует Haiku для
сравнения candidate-observation с открытыми + недавно отвергнутыми
гипотезами в окне window_days.

Зависимости: health_db, hai_core (get_client).
Конфиг: ~/health/data/survivorship_config.yaml :: semantic_dedup.

Контракт:
    check(candidate_observation: str) -> tuple[bool, int | None, str]
        Возвращает (is_dup, existing_memory_id, reason).
        - (False, None, '') — гипотеза новая, можно сохранять.
        - (True, mid, reason) — дубль, save_hypothesis должен пропустить.
"""
from __future__ import annotations
import hai_core

import json
import logging
import yaml
from datetime import date, timedelta
from _time_inject import get_today  # единый источник времени (seam)
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))
import health_db as db
from hai_core import get_client

log = logging.getLogger(__name__)

from assessment_scheduler import tenant_data_path
CONFIG_PATH = tenant_data_path("survivorship_config.yaml")  # конфиг ЭТОГО человека (28.09, этап Б)
# Модель берётся при ВЫЗОВЕ через hai_core.get_model — до 2026-10-01 здесь стояла
# константа из MODEL_DEFAULTS, и настройка/цепочка модели в БД этот модуль не достигала
# (нить llm-provider: шесть таких модулей). Роль: haiku.


def _load_config() -> dict:
    """Читает survivorship_config.yaml. Безопасные дефолты если нет файла."""
    defaults = {"enabled": True, "window_days": 90, "check_rejected": True}
    if not CONFIG_PATH.exists():
        return defaults
    try:
        cfg = yaml.safe_load(CONFIG_PATH.read_text()) or {}
        sd = (cfg.get("semantic_dedup") or {})
        return {
            "enabled": sd.get("enabled", defaults["enabled"]),
            "window_days": int(sd.get("window_days", defaults["window_days"])),
            "check_rejected": bool(sd.get("check_rejected", defaults["check_rejected"])),
        }
    except Exception as e:
        log.warning(f"semantic_dedup config read failed: {e}; using defaults")
        return defaults


def _collect_candidates(window_days: int, check_rejected: bool) -> list[dict]:
    """Собирает гипотезы для сравнения: открытые + опционально rejected за окно."""
    cutoff = (get_today() - timedelta(days=window_days)).isoformat()
    rows = db.get_memory(category="hypothesis", n=200, active_only=False)
    candidates = []
    for row in rows:
        try:
            payload = json.loads(row["value"])
            status = payload.get("status", "open")
            updated = row.get("updated_at") or row.get("created_at") or ""
            if updated < cutoff:
                continue
            if status in ("confirmed",):
                continue  # confirmed уже стали протоколами, не дублируем
            if status == "rejected" and not check_rejected:
                continue
            candidates.append({
                "memory_id": row["id"],
                "status": status,
                "observation": payload.get("observation", "")[:300],
            })
        except Exception:  # silent-ok: broken row/JSON или нет данных — пропуск
            continue
    return candidates


def _haiku_compare(candidate_obs: str, existing: list[dict]) -> tuple[bool, int | None, str]:
    """Один Haiku-вызов: сравнить candidate с list of existing."""
    if not existing:
        return (False, None, "no existing hypotheses to compare against")

    existing_text = "\n".join(
        f"  #{e['memory_id']} [{e['status']}]: {e['observation']}"
        for e in existing
    )
    prompt = (
        "Ты — дедупликатор медицинских гипотез. Сравни КАНДИДАТА с СУЩЕСТВУЮЩИМИ "
        "и реши, описывает ли он СЕМАНТИЧЕСКИ ту же тему. Не строковое совпадение, "
        "а суть наблюдения и механизм.\n\n"
        f"КАНДИДАТ:\n{candidate_obs[:500]}\n\n"
        f"СУЩЕСТВУЮЩИЕ:\n{existing_text}\n\n"
        "Ответь СТРОГО валидным JSON: {\"is_duplicate\": bool, \"existing_id\": int|null, \"reason\": str}.\n"
        "is_duplicate=true только если КАНДИДАТ говорит о том же явлении что один из EXISTING. "
        "Если разные домены (sleep vs cardio) или разные паттерны (decline vs improvement) — false."
    )

    try:
        client = get_client()
        resp = client.messages.create(
            model=hai_core.get_model("haiku"),
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip()
        # Грубый JSON-extract
        import re
        m = re.search(r"\{[^{}]*\}", text, re.DOTALL)
        if not m:
            return (False, None, f"haiku parse failed: {text[:120]}")
        parsed = json.loads(m.group(0))
        is_dup = bool(parsed.get("is_duplicate"))
        existing_id = parsed.get("existing_id")
        reason = parsed.get("reason", "")[:200]
        if is_dup and existing_id:
            return (True, int(existing_id), reason)
        return (False, None, reason or "haiku says not duplicate")
    except Exception as e:
        log.warning(f"_haiku_compare error: {e}")
        return (False, None, f"compare error: {e}")


def check(candidate_observation: str) -> tuple[bool, int | None, str]:
    """Главная точка. См. модульный docstring.

    Если semantic_dedup отключён в config — всегда (False, None, 'disabled').
    """
    cfg = _load_config()
    if not cfg["enabled"]:
        return (False, None, "semantic_dedup disabled in config")

    candidates = _collect_candidates(cfg["window_days"], cfg["check_rejected"])
    is_dup, existing_id, reason = _haiku_compare(candidate_observation, candidates)

    if is_dup and existing_id is not None:
        # Audit: фиксируем skip в memory с active=0 (чтобы не лезло в утренний контекст)
        try:
            audit_value = json.dumps({
                "candidate_observation": candidate_observation[:300],
                "existing_id": existing_id,
                "reason": reason,
                "checked_at": get_today().isoformat(),
            }, ensure_ascii=False)
            db.save_memory(
                category="dedup_skipped",
                key=f"dedup_{get_today()}_{existing_id}",
                value=audit_value,
                confidence=0.9,
                source="hypothesis_semantic_check",
            )
            # Деактивируем чтобы не попало в _build_gp_context
            with db.get_conn() as conn:
                conn.execute(
                    "UPDATE memory SET active=0 WHERE category='dedup_skipped' "
                    "AND key=?",
                    (f"dedup_{get_today()}_{existing_id}",)
                )
        except Exception as e:
            log.warning(f"dedup audit save failed: {e}")

    return (is_dup, existing_id, reason)


if __name__ == "__main__":
    # CLI smoke: проверка одного observation против БД
    if len(sys.argv) < 2:
        print("usage: python3.11 hypothesis_semantic_check.py '<observation text>'")
        sys.exit(1)
    obs = sys.argv[1]
    is_dup, existing_id, reason = check(obs)
    print(f"is_duplicate: {is_dup}")
    print(f"existing_id: {existing_id}")
    print(f"reason: {reason}")
