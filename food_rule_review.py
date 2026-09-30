"""
food_rule_review.py — месячная сводка сгенерированного рулбука (тир 2, инкремент 4, data-in-code-9).

Генерация раз в месяц кладёт правила в shadow-склад. Прежде чем что-то флипнуть в active, ЧЕЛОВЕК
(владелец) видит месячную сводку-diff: что генератор предложил, что изменилось. Это ВИДИМОСТЬ перед
флипом, не гейт и не медицина.

Решение владельца (2026-07-15): питание — НЕ вопрос медицины, врача не трогаем; лекарств в утреннем
брифе нет, значит и лекарство-взаимодействий генератор породить не может. Медицинскую безопасность
еды держит ПОЛ (food_floor: резекция→не постное и т.п.), а не эскалация врачу. Поэтому здесь —
только сводка человеку. Правило, не прошедшее пол, помечается и НЕ применяется (fail-closed выше).
"""
from __future__ import annotations

import re
import json
import logging
import i18n

import generated_food_rules as _store

_log = logging.getLogger(__name__)


def _jargon_blacklist() -> list[str]:
    try:
        from cbcr_hypothesis import _JARGON_BLACKLIST
        return list(_JARGON_BLACKLIST)
    except Exception as e:  # noqa: BLE001
        _log.warning("jargon blacklist import failed: %s", e)
        return []


def _rule_label(rule: dict) -> str:
    """Короткая человеко-читаемая метка правила.

    Схема генератора (food_rule_generator, читатели строк 92/117): метка живёт в
    payload.condition.label; `condition` — dict, `lever` — enum рычага
    (management / modify_enabling_condition), не текст. Дефект 2026-08-31: цикл
    по ключам брал первое строковое поле → `lever`, и месячный отчёт печатал
    двадцать строк «modify_enabling_condition» (ещё и с курсивом от `_` в Markdown).
    """
    p = rule.get("payload") or {}
    cond = p.get("condition")
    candidates = [
        p.get("label"),
        cond.get("label") if isinstance(cond, dict) else cond,
    ]
    for v in candidates:
        if isinstance(v, str) and v.strip():
            return v.strip()[:80]
    # Фолбэк — ключ, но без `_`: Telegram Markdown делает из них курсив.
    return str(rule.get("rule_key") or p.get("id") or "unnamed").replace("_", " ")


def _latest_by_key(rules: list[dict]) -> dict:
    """rule_key → правило максимальной версии (get_rules отдаёт по возрастанию версии)."""
    out: dict = {}
    for r in rules:
        out[r["rule_key"]] = r  # последняя перезапишет = максимальная версия
    return out


def _item(rule: dict) -> dict:
    return {"rule_key": rule["rule_key"], "version": rule.get("version"),
            "label": _rule_label(rule), "floor_ok": bool(rule.get("floor_ok"))}


def build_diff(conn=None) -> dict:
    """Diff применяемого рулбука (active) против предложенного (shadow). Детерминированно."""
    active = _latest_by_key(_store.get_rules(status="active", conn=conn))
    shadow = _latest_by_key(_store.get_rules(status="shadow", conn=conn))
    proposed, changed, removed = [], [], []
    for key, srule in shadow.items():
        if key not in active:
            proposed.append(_item(srule))
        elif json.dumps(srule["payload"], sort_keys=True, ensure_ascii=False) != \
                json.dumps(active[key]["payload"], sort_keys=True, ensure_ascii=False):
            changed.append(_item(srule))
    for key, arule in active.items():
        if key not in shadow:
            removed.append(_item(arule))
    # Отклонённое критиком в ТОМ ЖЕ прогоне, что и shadow (дата новейшего shadow): чего
    # генератор хотел и почему не прошло — материал для строки в clinical_kb руками.
    rejected = []
    if shadow:
        run_day = max((r.get("created_at") or "")[:10] for r in shadow.values())
        for r in _store.get_rules(status="rejected", conn=conn):
            if (r.get("created_at") or "")[:10] >= run_day:
                rejected.append({**_item(r), "reasons": r.get("critique") or ""})
    return {"proposed": proposed, "changed": changed, "removed": removed, "rejected": rejected,
            "n_active": len(active), "n_shadow": len(shadow)}


def check_jargon(text: str) -> list[str]:
    """Термины из мед-жаргон-blacklist, попавшие в текст. Пусто = чисто."""
    low = (text or "").lower()
    # английские термины — целым словом (ревью X4, 28.09): тот же предикат, что у CBCR
    return [j for j in _jargon_blacklist()
            if (re.search(r"\b" + re.escape(j.lower()) + r"\b", low) if j.isascii() else j.lower() in low)]


def render_summary(diff: dict) -> str:
    """Месячная сводка ДЛЯ ВЛАДЕЛЬЦА. Обзор перед флипом, не гейт. Пол безопасности всегда перебивает."""
    lang = i18n.lang_of()
    L = [i18n.t("food_rule_review.summary.heading", lang),
         i18n.t("food_rule_review.summary.note", lang)]
    if not (diff["proposed"] or diff["changed"] or diff["removed"] or diff.get("rejected")):
        L.append("")
        L.append(i18n.t("food_rule_review.summary.unchanged", lang,
                        active=diff["n_active"], proposed=diff["n_shadow"]))
        return "\n".join(L)
    if diff["proposed"]:
        L.append("")
        L.append(i18n.t("food_rule_review.summary.proposed", lang))
        L += [f"- {x['label']}" + ("" if x["floor_ok"] else i18n.t("food_rule_review.summary.floor_failed", lang))
              for x in diff["proposed"]]
    if diff["changed"]:
        L.append("")
        L.append(i18n.t("food_rule_review.summary.changed", lang))
        L += [f"- {x['label']}" for x in diff["changed"]]
    if diff["removed"]:
        L.append("")
        L.append(i18n.t("food_rule_review.summary.removed", lang))
        L += [f"- {x['label']}" for x in diff["removed"]]
    if diff.get("rejected"):
        L.append("")
        L.append(i18n.t("food_rule_review.summary.rejected", lang))
        L += [f"- {x['label']} · {x['reasons']}" for x in diff["rejected"]]
    jl = check_jargon("\n".join(L))
    if jl:
        L.append("")
        L.append(i18n.t("food_rule_review.summary.jargon", lang, terms=jl[:5]))
    return "\n".join(L)


def monthly_review(conn=None) -> dict:
    """Сводка рулбука для месячного GP-отчёта. Только видимость — без эскалаций и медицины."""
    diff = build_diff(conn=conn)
    return {"summary": render_summary(diff), "diff": diff}
