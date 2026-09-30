"""symptom_intake.py — движок elicitation (WP2): дифференциал под эпистемическим тормозом.

ДОМЕН-АГНОСТИЧНО (§9): никаких имён областей медицины/болезней. Промпт —
specialists/symptom_intake_system.txt (тоже без доменов). Тормоз — epistemic_skill
(AP-9 «термин=дескриптор, не диагноз» + запрет ложного консенсуса), инъектируется
дословно, версия штампуется в state. Мотор расхождения — форсированный дифференциал
в промпте.

Стоп-логика — В КОДЕ, не в модели (иначе модель сама решает, когда остановиться =
якорь): набор перестал делиться STUCK_TURNS ходов → предохранитель → escalate к врачу.
Сборка входа — одна инспектируемая функция build_elicitation_payload (F-seam).
LLM-вызов — через _call_model (seam для тестов, monkeypatch).
"""
from __future__ import annotations

import json as _json
import logging
import i18n
from pathlib import Path

log = logging.getLogger(__name__)

# Системная механика (§9 класс-2: предохранители/повторы, не клинические пороги).
DIFF_MIN_CANDIDATES = 3      # минимум конкурирующих версий (форсированный дифференциал)
STUCK_TURNS = 3              # ходов без сужения набора → предохранитель → к врачу
MAX_TURNS = 12               # жёсткий потолок против рантайм-зацикливания

_PROMPT_PATH = Path(__file__).resolve().parent / "specialists" / "symptom_intake_system.txt"

_CONTOUR_KEYS = ("observation", "mechanism", "prediction", "test")


def _load_system_prompt() -> tuple[str, str]:
    """Базовый промпт + эпистемический тормоз (дословно). (text, epi_version).
    Fail-fast: отсутствие любого файла → исключение (не тихий пустой промпт)."""
    import epistemic_skill.loader as _epi
    base = _PROMPT_PATH.read_text(encoding="utf-8")
    disc = _epi.load_part("discipline.txt")
    return base + "\n\n" + disc.text, disc.version


def new_state(case_id: int) -> dict:
    return {
        "case_id":    case_id,
        "turns":      [],         # [{"role","content"}] — человекочитаемая история
        "candidates": [],         # последний набор версий
        "contour":    {k: "" for k in _CONTOUR_KEYS},
        "stuck_count": 0,
        "prev_alive":  None,
        "epi_version": None,
        "image_path":  None,      # фото кейса — показываем модели КАЖДЫЙ ход (не слепнет)
    }


def _alive(cands: list) -> int:
    return sum(1 for c in cands if (c or {}).get("status") != "excluded")


def build_elicitation_payload(state: dict, user_msg: str,
                              image_b64: str | None = None,
                              image_mime: str = "image/jpeg") -> tuple[str, list]:
    """Единственная точка сборки входа модели (F-seam). (system, messages).
    НЕ мутирует state['turns'] — запись хода делает step()."""
    system, epi_version = _load_system_prompt()
    import hai_core
    system += hai_core.answer_language()
    state["epi_version"] = epi_version
    messages = list(state["turns"])
    # Картинку показываем модели КАЖДЫЙ ход (иначе слепнет после первого): b64 из аргумента
    # ИЛИ перечитываем из state['image_path']. В историю бинарь НЕ кладём (раздует DB/сессию).
    if image_b64 is None and state.get("image_path"):
        try:
            import base64 as _b64
            with open(state["image_path"], "rb") as _fh:
                image_b64 = _b64.standard_b64encode(_fh.read()).decode()
        except Exception:  # silent-ok: фото недоступно на диске — продолжаем без него
            image_b64 = None
    if image_b64:
        content = [
            {"type": "image", "source": {"type": "base64",
                                         "media_type": image_mime, "data": image_b64}},
            {"type": "text", "text": user_msg},
        ]
        messages.append({"role": "user", "content": content})
    else:
        messages.append({"role": "user", "content": user_msg})
    return system, messages


def assembled_elicitation_text(state: dict, user_msg: str) -> str:
    """Плоская инспекция всего, что видит модель (анти-прокси тест)."""
    system, messages = build_elicitation_payload(state, user_msg)
    parts = ["[SYSTEM]\n" + system]
    for m in messages:
        c = m["content"] if isinstance(m["content"], str) else "[multimodal]"
        parts.append("[" + m["role"].upper() + "]\n" + str(c))
    return "\n\n".join(parts)


def _call_model(system: str, messages: list) -> str:
    """LLM-вызов (seam для тестов). Возвращает raw text (ожидаем JSON)."""
    import hai_core
    client = hai_core.get_client()
    resp = client.messages.create(
        model=hai_core.get_model("sonnet"),
        max_tokens=2000,
        system=system,
        messages=messages,
    )
    return next((b.text for b in resp.content if b.type == "text"), "")


def _parse(raw: str) -> dict:
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()
    try:
        return _json.loads(raw)
    except Exception:
        # Модель обернула JSON в прозу/обрезала обёртку — вырезаем объект по фигурным скобкам.
        i, j = raw.find("{"), raw.rfind("}")
        if i != -1 and j > i:
            return _json.loads(raw[i:j + 1])
        raise


def step(state: dict, user_msg: str, image_b64: str | None = None,
         image_mime: str = "image/jpeg") -> tuple[dict, dict]:
    """Один ход диалога. (обновлённый state, action).
    action.type ∈ {"question","handoff","escalate","error"}.
    СТОП-ЛОГИКА В КОДЕ: модель предлагает, код решает продолжать/останавливаться."""
    system, messages = build_elicitation_payload(state, user_msg, image_b64, image_mime)
    try:
        parsed = _parse(_call_model(system, messages))
    except Exception as e:
        log.warning(f"symptom_intake.step: parse fail, повтор со строгим JSON: {e}")
        try:
            parsed = _parse(_call_model(
                system + "\n\nВЕРНИ СТРОГО ВАЛИДНЫЙ JSON по схеме выше — без текста вокруг, "
                         "без markdown-обёртки, одним объектом.", messages))
        except Exception as e2:
            log.warning(f"symptom_intake.step: повтор тоже сорвался: {e2}")
            return state, {"type": "error", "reason": str(e2)}

    state["turns"].append({"role": "user", "content": user_msg})
    cands = parsed.get("candidates") or []
    state["candidates"] = cands
    if isinstance(parsed.get("contour"), dict):
        state["contour"] = {k: (parsed["contour"].get(k) or "") for k in _CONTOUR_KEYS}

    alive = _alive(cands)
    if state["prev_alive"] is not None:
        if alive < state["prev_alive"]:
            state["stuck_count"] = 0          # сузили — сбрасываем
        else:
            state["stuck_count"] += 1         # набор не делится
    state["prev_alive"] = alive

    contour_full = all((state["contour"].get(k) or "").strip() for k in _CONTOUR_KEYS)

    # 1) Сходимость: модель говорит converged И контур полон И осталась одна версия.
    if parsed.get("converged") and contour_full and alive <= 1:
        return state, {"type": "handoff", "hypothesis": dict(state["contour"]),
                       "candidates": cands}
    # 2) Предохранитель (код, не модель): не делится STUCK_TURNS ходов или потолок.
    if state["stuck_count"] >= STUCK_TURNS or len(state["turns"]) >= MAX_TURNS:
        return state, {"type": "escalate",
                       "reason": parsed.get("escalate_reason") or "не удалось сузить набор версий",
                       "candidates": cands, "contour": dict(state["contour"])}
    # 3) Модель сама просит к врачу.
    if parsed.get("escalate"):
        return state, {"type": "escalate",
                       "reason": parsed.get("escalate_reason") or "модель не может сузить",
                       "candidates": cands, "contour": dict(state["contour"])}
    # 4) Иначе — различающий вопрос.
    q = (parsed.get("discriminating_question")
         or i18n.t('symptom_intake.question.details'))
    state["turns"].append({"role": "assistant", "content": q})
    return state, {"type": "question", "text": q}


def finalize_to_specialist(case_id: int, action: dict) -> int:
    """WP3: handoff/escalate → гипотеза needs_specialist в существующий движок.

    И собранный контур (handoff), и открытый дифференциал (escalate) уходят живому
    врачу через save_hypothesis(trigger='symptom_intake', resolution_type='needs_specialist').
    Конкурирующие версии вплетаются в mechanism — врач видит дифференциал, не один вывод.
    Связывает гипотезу с кейсом (visual_case.status → handed_off). Возвращает memory_id.
    Дальше — существующий doctor-in-loop / consult_prep (не наш код)."""
    import hai_hypotheses as _hh
    import visual_db as _vdb

    contour = action.get("hypothesis") or action.get("contour") or {}
    alive = [c for c in (action.get("candidates") or []) if (c or {}).get("status") != "excluded"]
    diff = "; ".join(
        i18n.t('symptom_intake.hypothesis.candidate', label=c.get('label'), grounding=c.get('grounding')) for c in alive
    ) or "—"

    observation = (contour.get("observation") or "").strip() or i18n.t('symptom_intake.hypothesis.observation')
    mechanism   = (contour.get("mechanism") or "").strip()
    prediction  = (contour.get("prediction") or "").strip()
    test        = (contour.get("test") or "").strip() or i18n.t('symptom_intake.hypothesis.examination')

    if action.get("type") == "escalate":
        mechanism = (mechanism + i18n.t('symptom_intake.hypothesis.open_differential') + diff).strip(" |")
    elif diff != "—":
        mechanism = (mechanism + i18n.t('symptom_intake.hypothesis.candidates') + diff).strip(" |")

    mid = _hh.save_hypothesis(
        observation=observation, mechanism=mechanism,
        prediction=prediction, test=test,
        trigger="symptom_intake", resolution_type="needs_specialist",
    )
    _vdb.link_hypothesis_to_case(case_id, mid)
    return mid


# ── этап 2: сравнение серии (vision) ────────────────────────────────────────

def compare_series(old_bytes: bytes, new_bytes: bytes, region: str | None, caption: str) -> str:
    """Vision-сравнение двух снимков одной зоны во времени → описательная динамика ДЛЯ
    ВРАЧА (не диагноз, не успокоение). Под тем же эпистемическим тормозом. LLM за seam
    _call_vision (monkeypatch в тестах)."""
    import base64
    import hai_core
    system, _ = _load_system_prompt()
    system += (
        "\n\nСЕЙЧАС ДРУГАЯ ЗАДАЧА: сравни ДВА фото одной зоны во времени "
        "(первое — раньше, второе — свежее). Опиши ТОЛЬКО наблюдаемую динамику для врача: "
        "размер / цвет / границы / поверхность — изменилось или так же, в какую сторону. "
        "НЕ диагноз, НЕ успокоение. Несопоставимы (другая зона/ракурс/свет) — скажи прямо. "
        "Коротко, по-русски."
    ) + hai_core.answer_language()

    def _img(b: bytes) -> dict:
        return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                            "data": base64.standard_b64encode(b).decode()}}

    content = [
        {"type": "text", "text": "Фото РАНЬШЕ:"}, _img(old_bytes),
        {"type": "text", "text": "Фото СВЕЖЕЕ:"}, _img(new_bytes),
        {"type": "text", "text": f"Зона: {region or 'не указана'}. Подпись пациента: {caption}"},
    ]
    return _call_vision(system, [{"role": "user", "content": content}])


def _call_vision(system: str, messages: list) -> str:
    """LLM vision-вызов (seam для тестов). Возвращает текст."""
    import hai_core
    from hai_core import _strip_markdown
    resp = hai_core.get_client().messages.create(
        model=hai_core.get_model("sonnet"), max_tokens=500, system=system, messages=messages,
    )
    return _strip_markdown(next((b.text for b in resp.content if b.type == "text"), ""))
