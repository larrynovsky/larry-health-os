"""
food_floor.py — ПОЛ БЕЗОПАСНОСТИ пищевого профиля (тир 1, нить data-in-code-9).

Нерушимые клинические инварианты, которым обязан удовлетворять любой пищевой профиль —
сегодняшний детерминированный и (позже) сгенерированный консилиумом. Источник — версионируемый
`methodology/food_rules.yaml` (оракул — владелец); §9 п.4: init сеет в таблицу `food_floor`, рантайм
читает таблицу (эталон absolute_thresholds), при незасеянной БД — читаемый fallback на YAML.

Пол сторожит СТРУКТУРНУЮ рамку (energy/constraints/micro), НЕ прозу рендера, и делает НЕЗАВИСИМЫЙ
чек на источник (problem_list), не доверяя строителю рамки. assert_floor НИКОГДА не должен ронять
бриф — он возвращает список нарушений, решение (лог/reject) принимает вызывающий.
"""
from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path

import yaml

_log = logging.getLogger(__name__)
_YAML_PATH = Path(__file__).resolve().parent / "methodology" / "food_rules.yaml"
_TABLE = "food_floor"


def _db():
    import health_db  # ленивый импорт — избегаем цикла health_db↔food_floor
    return health_db


@lru_cache(maxsize=1)
def _load_yaml_floor() -> tuple:
    """Инварианты из версионируемого YAML. Кэш на процесс. Возвращает tuple (хешируемо)."""
    data = yaml.safe_load(_YAML_PATH.read_text(encoding="utf-8")) or {}
    out = []
    for inv in data.get("floor", []) or []:
        out.append({"id": inv["id"], "why": inv.get("why", ""), "source": inv.get("source", ""),
                    "trigger": inv.get("trigger", {}) or {}, "assert": inv.get("assert", {}) or {}})
    return tuple(out)


def seed_floor(conn=None) -> int:
    """Сид YAML→таблица (§9 п.4). CREATE IF NOT EXISTS + INSERT OR REPLACE. Идемпотентно.
    Вызывать из init_db (как _seed_absolute_thresholds). Возвращает число инвариантов."""
    c = conn or _db().get_conn()
    c.execute(f"""
        CREATE TABLE IF NOT EXISTS {_TABLE} (
            id           TEXT PRIMARY KEY,
            why          TEXT NOT NULL DEFAULT '',
            source       TEXT NOT NULL DEFAULT '',
            trigger_json TEXT NOT NULL,
            assert_json  TEXT NOT NULL,
            updated_at   TEXT DEFAULT (datetime('now'))
        )
    """)
    invs = _load_yaml_floor()
    for inv in invs:
        c.execute(
            f"INSERT OR REPLACE INTO {_TABLE} (id, why, source, trigger_json, assert_json, updated_at) "
            f"VALUES (?,?,?,?,?, datetime('now'))",
            (inv["id"], inv["why"], inv["source"],
             json.dumps(inv["trigger"], ensure_ascii=False), json.dumps(inv["assert"], ensure_ascii=False)))
    try:
        c.commit()
    except Exception:  # silent-ok: conn без commit (уже в транзакции вызывающего)
        pass
    return len(invs)


def load_floor(conn=None) -> list[dict]:
    """Инварианты пола. Приоритет — таблица (прод, §9 контур); fallback — YAML (незасеянная БД/тесты)."""
    try:
        c = conn or _db().get_conn()
        rows = c.execute(
            f"SELECT id, why, source, trigger_json, assert_json FROM {_TABLE}").fetchall()
        if rows:
            return [{"id": r[0], "why": r[1], "source": r[2],
                     "trigger": json.loads(r[3]), "assert": json.loads(r[4])} for r in rows]
    except Exception:  # silent-ok: таблицы нет/БД недоступна → читаемый источник YAML
        pass
    return [dict(inv) for inv in _load_yaml_floor()]


def _med_text(conn, profile: dict | None) -> str:
    """НЕЗАВИСИМЫЙ ре-скан текста медкарты (как medical_frame, но пол не доверяет рамке).

    НЕ СВОДИТЬ в clinical_kb.patient_med_text: сведение домов 2026-09-01 намеренно обошло эту
    функцию (§17 — второй источник обязан обходить преобразование, которое могло исказить
    первый). Если пол начнёт читать тот же дом, что рамка, поломка дома станет невидимой для
    пола, и fail-closed перестанет быть вторым мнением."""
    txt = ""
    try:
        rows = conn.execute("SELECT title, description, notes FROM problem_list").fetchall()
        txt = " ".join(" ".join(str(x or "") for x in r) for r in rows)
    except Exception:  # silent-ok: нет problem_list → скан только по профилю
        pass
    txt += " " + " ".join(str(v) for v in ((profile or {}).get("medical", {}) or {}).values())
    return txt.lower()


def _triggered(trigger: dict, med_text: str, bmi) -> bool:
    if "problem_regex" in trigger:
        return bool(re.search(trigger["problem_regex"], med_text))
    if "bmi_below" in trigger:
        return bmi is not None and bmi < float(trigger["bmi_below"])
    return False


def _violations_of(assertion: dict, frame: dict) -> list[str]:
    out = []
    energy = frame.get("energy")
    constraints = set(frame.get("constraints", set()) or set())
    micro = set(frame.get("micro", set()) or set())
    if "energy_in" in assertion and energy not in assertion["energy_in"]:
        out.append(f"energy={energy!r} не в {assertion['energy_in']}")
    if "energy_exclude" in assertion and energy in assertion["energy_exclude"]:
        out.append(f"energy={energy!r} запрещён")
    if "constraints_exclude" in assertion:
        bad = constraints & set(assertion["constraints_exclude"])
        if bad:
            out.append(f"запрещённые constraints: {sorted(bad)}")
    if "micro_include" in assertion:
        missing = set(assertion["micro_include"]) - micro
        if missing:
            out.append(f"нет обязательных micro: {sorted(missing)}")
    return out


def assert_floor(frame: dict, conn=None, profile: dict | None = None,
                 med_text: str | None = None) -> list[dict]:
    """Проверить рамку против пола. Возвращает список нарушений [{id, why, detail}] (пусто = ок).
    НИКОГДА не бросает наружу — гвардиан не должен ронять бриф.

    med_text: если задан — берётся ВМЕСТО ре-скана problem_list. Нужно для критики
    СГЕНЕРИРОВАННОГО правила: подставляем текст его условия (напр. label «резекция…»),
    чтобы проверить, не пробивает ли предложенная рамка пол для этого условия."""
    try:
        # brief-neutralization Фаза 2 B2.2 (решение владельца): guard остаётся, но ЧИТАЕТ инварианты-пол
        # из единого clinical_kb (kind='floor'), а не из локальной food_floor-таблицы. Независимость
        # сохранена — active_entries сам матчит условия против ДАННЫХ тенанта (проблем-лист/ИМТ),
        # не доверяя строителю рамки. med_text override пробрасывается (критика сгенерированного
        # правила). Паритет со старым полом доказан 5/5. food_floor-таблица остаётся как §9-п.4
        # DB-контур + под drift-сторожем (test: clinical_kb floor == food_rules.yaml floor).
        import clinical_kb as _ckb
        c = conn if conn is not None else _db().get_conn()
        bmi = frame.get("bmi")
        mt = str(med_text).lower() if med_text is not None else None
        out = []
        for e in _ckb.active_entries(c, "food", med_text=mt, bmi=bmi, profile=profile):
            if e.get("kind") != "floor":
                continue
            assertion = (e.get("payload") or {}).get("assert", {}) or {}
            det = _violations_of(assertion, frame)
            if det:
                out.append({"id": e["id"], "why": (e.get("payload") or {}).get("why", ""),
                            "detail": "; ".join(det)})
        return out
    except Exception as e:  # silent-ok: пол — гвардиан, его сбой не должен ломать профиль
        _log.warning("assert_floor сбой (пол пропущен): %s", e)
        return []
