#!/usr/bin/env python3.11
"""treatment_summary.py — детерминированная история лечения из medications+episodes.

Лечение = производное из документов, не ручная строка профиля. Эта функция —
единственный источник строки «Лечение:» для брифов/запросов. Никакого LLM:
читает уже извлечённые и ПОДТВЕРЖДЁННЫЕ человеком (confirmation in confirmed/manual)
режимы и строит хронологию по линиям терапии (история болезни, не арифметика).

Public API:
    build_treatment_summary() -> dict
        {
          "lines": [ {episode, problem_id, regimen, modality, intent, cycles, ...} ],
          "text": "<хронология>",
          "chemo_courses_total": int,           # справочно (chemo+chemoradiation)
          "immunotherapy": [names],             # отдельно (иммунотерапия и т.п.)
          "empty": bool,                        # True → потребителю взять fallback
        }

Зависимость: health_db.
"""
import i18n
import json
import logging
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))
import health_db as db

log = logging.getLogger(__name__)

_MODALITY_RU = {
    "chemo": "химиотерапия",
    "chemoradiation": "химиолучевая",
    "immunotherapy": "иммунотерапия",
    "targeted": "таргетная",
    "radiation": "лучевая",
}
_INTENT_RU = {
    "neoadjuvant": "неоадъювант",
    "adjuvant": "адъювант",
    "palliative": "паллиатив",
    "maintenance": "поддерживающая",
    "definitive": "радикальная",
}


def _fmt_med(m: dict) -> str:
    """Один режим в человекочитаемую строку."""
    parts = [m.get("name") or "?"]
    tags = []
    mod = _MODALITY_RU.get(m.get("modality") or "")
    if mod:
        tags.append(mod)
    intent = _INTENT_RU.get(m.get("intent") or "")
    if intent:
        tags.append(intent)
    agents = m.get("agents")
    if agents:
        try:
            alist = json.loads(agents) if isinstance(agents, str) else agents
        except Exception:
            alist = None
        if alist:
            tags.append(" + ".join(str(a) for a in alist))
    cyc = m.get("cycles_completed")
    if cyc:
        tags.append(f"{cyc} циклов")
    elif m.get("notes"):
        tags.append(str(m["notes"]))
    suffix = f" ({', '.join(tags)})" if tags else ""
    return parts[0] + suffix


def build_treatment_summary() -> dict:
    """Строит хронологию лечения из подтверждённых medications + episodes_of_care."""
    try:
        meds = db.get_medications()  # confirmed + manual
        episodes = db.get_episodes()
    except Exception as e:
        log.warning(f"build_treatment_summary: {e}")
        return {"lines": [], "text": "", "chemo_courses_total": 0,
                "immunotherapy": [], "empty": True}

    if not meds:
        return {"lines": [], "text": "", "chemo_courses_total": 0,
                "immunotherapy": [], "empty": True}

    ep_by_problem = {e.get("primary_problem_id"): e for e in episodes}

    # Группировка режимов по проблеме/эпизоду, порядок — по дате начала
    groups: dict = {}
    for m in meds:
        pid = m.get("indication_problem_id")
        groups.setdefault(pid, []).append(m)

    def _group_sort_key(pid):
        ep = ep_by_problem.get(pid)
        return (ep.get("start_date") if ep else "") or \
               (min((mm.get("start_date") or "" for mm in groups[pid]), default=""))

    lines = []
    text_blocks = []
    for pid in sorted(groups, key=_group_sort_key):
        ep = ep_by_problem.get(pid)
        gmeds = sorted(groups[pid], key=lambda x: (x.get("start_date") or "", x.get("id")))
        # без эпизода и проблемы — «принимает»: прежнее «Лечение» давало «Лечение: Лечение: …»
        # у каждого, кто назвал лекарства при знакомстве (нить empty-profile, 24.09)
        header = (ep.get("title") if ep else None) or (pid or "принимает")
        regimen_strs = [_fmt_med(m) for m in gmeds]
        text_blocks.append(f"{header}: " + " → ".join(regimen_strs))
        for m in gmeds:
            lines.append({
                "episode": header,
                "problem_id": pid,
                "regimen": m.get("name"),
                "modality": m.get("modality"),
                "intent": m.get("intent"),
                "cycles": m.get("cycles_completed"),
                "start_date": m.get("start_date"),
                "end_date": m.get("end_date"),
            })

    chemo_total = sum(
        int(m.get("cycles_completed") or 0)
        for m in meds
        if (m.get("modality") in ("chemo", "chemoradiation")) and m.get("cycles_completed")
    )
    immuno = [m.get("name") for m in meds if m.get("modality") == "immunotherapy"]

    return {
        "lines": lines,
        "text": " | ".join(text_blocks),
        "chemo_courses_total": chemo_total,
        "immunotherapy": immuno,
        "empty": False,
    }


def treatment_status_text() -> str | None:
    """Статус лечения из медкарты (episodes_of_care), а не ручной строкой профиля.

    Идущий эпизод (не finished и без даты конца) → «идёт лечение»; все закончены →
    «активного лечения нет» с датой конца последнего. Эпизодов нет → None: вывести нечего,
    потребитель берёт ручную строку, если она есть (новый человек без медкарты)."""
    try:
        eps = db.get_episodes() or []
    except Exception as e:
        log.warning(f"treatment_status_text: {e}")
        return None
    if not eps:
        return None
    lang = i18n.lang_of()
    running = [e for e in eps if (e.get("status") or "") != "finished" and not e.get("end_date")]
    if running:
        e = max(running, key=lambda x: x.get("start_date") or "")
        return i18n.t("treatment.status.running", lang,
                      title=e.get("title") or i18n.t("treatment.status.untitled", lang),
                      start=e.get("start_date") or "?")
    last_end = max((e.get("end_date") or "" for e in eps), default="")
    return i18n.t("treatment.status.finished", lang,
                  end=last_end or i18n.t("treatment.status.date_unknown", lang))


def treatment_text(fallback: str = "") -> str:
    """Удобный геттер для потребителей: текст истории или fallback при пустоте."""
    try:
        s = build_treatment_summary()
        if s and not s.get("empty") and s.get("text"):
            return s["text"]
    except Exception as e:
        log.warning(f"treatment_text: {e}")
    return fallback


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    db.init_db()
    import json
    print(json.dumps(build_treatment_summary(), ensure_ascii=False, indent=2))
