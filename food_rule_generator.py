"""
food_rule_generator.py — критик+сохранение сгенерированных диет-правил (тир 2, data-in-code-9).

ЭТО безопасная детерминированная машинерия (инкремент 2b-core): разобрать предложения →
критик (схема + пол безопасности + провенанс) → прошедшие в shadow-склад, при провале ДЕРЖИМ
прошлый рулбук (решение владельца: не супер-седим прежнее на отвергнутом). НЕ содержит ни промпта,
ни вызова модели, ни правки консилиума — их приносит вызывающий (проводка 2b-wire) с промптом,
проверенным владельцем. LLM только ПРЕДЛАГАЕТ; здесь решает код.

Предложение = illness-script-рычаг (DESIGN §2): {id, condition{label,...}, fault, lever,
frame{energy, protein, micro[], constraints[]}, evidence{source, weight, why}}.
"""
# INTENT: generated_food_rules — диет-правила тира 2: LLM предлагает, код решает.
#          Замысел и инварианты — subsystem_intent.yaml, раздел generated_food_rules.
from __future__ import annotations

import llm_client
from _time_inject import get_today  # seam

import json
import re

import food_floor
import generated_food_rules as _store

_REQUIRED_PROV = ("generated_by", "model", "model_version_date")


def _balanced_span(txt: str) -> str | None:
    """Первый сбалансированный [..]/{..} в тексте (учёт строк и эскейпов). None, если нет.
    Ловит голый JSON-массив, обёрнутый прозой ('Вот правила:\\n[ ... ]' или коммент после)."""
    start = None
    for i, ch in enumerate(txt):
        if ch in "[{":
            start = i
            break
    if start is None:
        return None
    depth, in_str, esc = 0, False, False
    for j in range(start, len(txt)):
        c = txt[j]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c in "[{":
            depth += 1
        elif c in "]}":
            depth -= 1
            if depth == 0:
                return txt[start:j + 1]
    return None  # незакрытая скобка


def parse_proposals(raw) -> list[dict]:
    """Достать список предложений из ответа модели. Не бросает. Кандидаты по убыванию строгости:
    ```-забор → весь текст → первый сбалансированный span (модель иногда прифигачивает прозу к
    голому массиву — молчаливый [] раньше съедал ВЕСЬ прогон, data-in-code-9 датчик #3)."""
    if isinstance(raw, list):
        return [p for p in raw if isinstance(p, dict)]
    if not isinstance(raw, str):
        return []
    txt = raw.strip()
    candidates: list[str] = []
    m = re.search(r"```(?:json)?\s*(.+?)```", txt, re.DOTALL)
    if m:
        candidates.append(m.group(1).strip())
    candidates.append(txt)
    span = _balanced_span(txt)
    if span:
        candidates.append(span)
    for cand in candidates:
        try:
            data = json.loads(cand)
        except Exception:  # этот кандидат не JSON — пробуем следующий
            continue
        if isinstance(data, dict):
            data = data.get("rules") or data.get("proposals") or [data]
        if isinstance(data, list):
            out = [p for p in data if isinstance(p, dict)]
            if out:
                return out
    return []  # ни один кандидат не дал предложений (критик/датчик решат)


def _rule_key(p: dict) -> str:
    key = (p.get("id") or "").strip()
    if key:
        return key
    label = ((p.get("condition") or {}).get("label") or "").strip().lower()
    return re.sub(r"\s+", "_", label)[:60] or "unnamed"


def _schema_problems(p: dict) -> list[str]:
    out = []
    if not _rule_key(p) or _rule_key(p) == "unnamed":
        out.append("нет id/condition.label")
    frame = p.get("frame") or {}
    if not frame.get("energy"):
        out.append("нет frame.energy")
    if not (p.get("evidence") or {}).get("source"):
        out.append("нет evidence.source")
    return out


def _dead_problems(p: dict, med_text: str | None) -> list[str]:
    """Гейт «срабатывает сегодня» (2026-09-01). Замер 01.09: 9 из 14 active-правил никогда не
    срабатывали — целили в лабы/геном, а применитель матчит problem_list + profile.medical.
    Решение владельца (вопрос 1, «А»): лабы и геном — дом clinical_kb, сюда не расширяем;
    правило, которое не может сработать у применителя, в склад не идёт. Предикат ОДИН —
    food_profile.rule_fires. Пустой med_text (тест-БД, новый тенант) → судить не на чем,
    гейт пропускает и говорит об этом в логе."""
    if not med_text or not med_text.strip():
        import logging
        logging.getLogger(__name__).info("dead-гейт пропущен: med_text тенанта пуст")
        return []
    import food_profile
    fires = food_profile.rule_fires(p, med_text)
    if fires is None:
        return ["dead:regex не компилируется"]
    if not fires:
        return ["dead:не срабатывает на пациенте (problem_list + profile.medical)"]
    return []


def critique_proposal(p: dict, provenance: dict | None = None, conn=None,
                      med_text: str | None = None) -> list[str]:
    """Вернуть список причин отклонения (пусто = принято). Схема + провенанс + пол + dead-гейт.
    med_text — текст тенанта из дома clinical_kb.patient_med_text; None → считается здесь."""
    if med_text is None:
        import clinical_kb as _ckb
        med_text = _ckb.patient_med_text(conn) if conn is not None else ""
    reasons = _schema_problems(p)
    reasons += _dead_problems(p, med_text)
    prov = provenance or {}
    missing_prov = [k for k in _REQUIRED_PROV if not prov.get(k)]
    if missing_prov:
        reasons.append(f"неполный провенанс: {missing_prov}")
    # Пол безопасности: подставляем текст условия правила как med_text, проверяем его frame.
    frame = dict(p.get("frame") or {})
    label = (p.get("condition") or {}).get("label") or ""
    detect = ((p.get("condition") or {}).get("detect") or {}).get("regex") or ""
    med_text = f"{label} {detect}"
    viol = food_floor.assert_floor(frame, conn=conn, med_text=med_text)
    for v in viol:
        reasons.append(f"пол:{v['id']}({v['detail']})")
    return reasons


def process_proposals(proposals: list[dict], provenance: dict | None = None,
                      conn=None, status: str = "shadow") -> dict:
    """Критик по каждому; прошедшие → склад (новая версия). Отвергнутые — строкой status='rejected'
    (видимость в сводке), живые версии ключа НЕ трогаются (решение владельца: держим прошлый
    рулбук). Возвращает сводку."""
    saved, rejected = [], []
    import clinical_kb as _ckb
    med_text = _ckb.patient_med_text(conn) if conn is not None else ""
    for p in proposals or []:
        reasons = critique_proposal(p, provenance, conn, med_text=med_text)
        key = _rule_key(p)
        if reasons:
            rejected.append({"rule_key": key, "reasons": reasons})
            _store.save_rejected(key, p, reasons, provenance=provenance, conn=conn)
            continue
        _store.save_rule(key, p, provenance=provenance, floor_ok=True,
                         critique="pass", status=status, conn=conn)
        saved.append(key)
    return {"saved": saved, "rejected": rejected,
            "n_saved": len(saved), "n_rejected": len(rejected),
            "n_total": len(proposals or [])}


# ── 2b-wire: shadow-генерация через модель (промпт на ревью владельца) ─────────────
from pathlib import Path as _Path

_PROMPT_PATH = _Path(__file__).resolve().parent / "specialists" / "food_rule_generator.md"

def _load_prompt() -> str:
    try:
        return _PROMPT_PATH.read_text(encoding="utf-8")
    except Exception:  # silent-ok ниже перехватит и залогирует громко
        import logging
        logging.getLogger(__name__).error("ПРОМПТ генератора не найден: %s", _PROMPT_PATH)
        return ""


def _catalog_reference() -> str:
    """Выверенный каталог как ОПОРА генератору (продукт → польза). Из текущих food_*-модулей."""
    lines: list[str] = ["КАТАЛОГ-ОПОРА (продукт → польза, WCRF):"]
    try:
        import food_genome as _fg
        for food, (_tag, why) in list({**_fg.FOOD_BENEFITS, **_fg._SEAFOOD_BENEFITS}.items()):
            lines.append(f"- {food}: {why}")
        import food_staples as _fs
        for s in _fs.STAPLES:
            lines.append(f"- {s['item']}: {s['why']}")
        for m, src in _fs.MICRO_FOODS.items():
            lines.append(f"- микро {m}: {src}")
    except Exception:  # silent-ok: каталог опционален, генератор справится без него
        pass
    return "\n".join(lines)


def _model_name() -> str:
    try:
        import hai_core
        return hai_core.model_for("consilium_coordinator")
    except Exception:  # silent-ok: провенанс переживёт неизвестную модель
        return "unknown"


def _default_llm_call(system: str, user: str) -> str:
    import anthropic
    client = llm_client.guarded_client()
    resp = client.messages.create(task="food_rule_generator._default_llm_call", model=_model_name(), max_tokens=4000,
                                  system=system + __import__("hai_core").answer_language(), messages=[{"role": "user", "content": user}])
    return llm_client.answer_text(resp)


def generate_shadow(input_pkg: str, period_days: int = 30, *, llm_call=None,
                    prompt_text: str | None = None, conn=None) -> dict:
    """Отдельный shadow-проход: модель предлагает диет-правила → критик → shadow-склад.
    НЕ применяется брифом. llm_call инъектируется (тесты без API). Провенанс проставляется."""
    from datetime import date
    system = prompt_text if prompt_text is not None else _load_prompt()
    user = f"{input_pkg}\n\n{_catalog_reference()}\n\nСформулируй диет-правила (только JSON-массив)."
    call = llm_call or _default_llm_call
    raw = call(system, user)
    proposals = parse_proposals(raw)
    provenance = {"generated_by": "monthly_consilium", "model": _model_name(),
                  "model_version_date": get_today().isoformat(),
                  "data_sources": ["profile", "hypotheses", "labs", "genome", "catalog"]}
    return process_proposals(proposals, provenance, conn=conn, status="shadow")
