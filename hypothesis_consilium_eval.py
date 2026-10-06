import llm_client
#!/usr/bin/env python3
"""
hypothesis_consilium_eval — оценка гипотезы через полный МДТ-консилиум.

Единственная задача: запустить Round A + Round B + coordinator + arbiter
в one-shot режиме (без ConsultationSession, без интерактивного цикла)
для оценки конкретной гипотезы против лабных данных.

Зависимости: wellally_consult (MEDICAL_SPECIALISTS, _read_specialist_prompt,
lifestyle_agents), health_db, hai_core (sync client для arbiter).
"""

import asyncio
import hai_core
import i18n
import json as _json
import logging
import re
from datetime import date
from _time_inject import get_today  # seam
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent))

import health_db as db
from hai_core import get_client

log = logging.getLogger(__name__)

# ── Eval-specific промпты ─────────────────────────────────────────────────────

_SPECIALIST_EVAL_SUFFIX = """

РЕЖИМ ОЦЕНКИ ГИПОТЕЗЫ (не диалог с пациентом).
Тебе предоставлена гипотеза с конкретным предсказанием и фактические лабораторные данные.

Ответь по схеме:
1. Что предсказывала гипотеза в части твоей специальности
2. Что фактически показали данные
3. Твой вывод: ПОДТВЕРЖДЕНА / ЧАСТИЧНО / ОПРОВЕРГНУТА
4. Если нужны дополнительные данные — что именно

4–6 предложений. Конкретные числа из данных.
"""

_COORDINATOR_EVAL_PROMPT = """
Ты — координатор МДТ-консилиума в режиме ОЦЕНКИ ГИПОТЕЗЫ.

Ты видишь мнения участников МДТ-консилиума (врачи + lifestyle-коучи).
Вынеси коллегиальный вердикт в трёх секциях:

[ВЕРДИКТ]
Одно из: ПОДТВЕРЖДЕНА / ЧАСТИЧНО ПОДТВЕРЖДЕНА / ОПРОВЕРГНУТА
+ уровень консенсуса (единогласно / большинство / разногласия)

[ОБОСНОВАНИЕ]
3–5 предложений. Конкретные маркеры и значения.

[СЛЕДУЮЩИЙ ШАГ]
Если подтверждена → какой протокол поведения нужен.
Если частично → что именно ещё нужно проверить и когда.
Если опровергнута → закрыть гипотезу или предложить уточнённую.
Если предлагаешь новую гипотезу — запиши в формате:
  НОВАЯ ГИПОТЕЗА:
  Наблюдение: ...
  Механизм: ...
  Прогноз: ...
  Что проверить: ...

[ОБНОВЛЁННАЯ ГИПОТЕЗА]
Независимо от вердикта — запиши точную формулировку гипотезы с учётом новых данных.
Если опровергнута полностью — напиши "Гипотеза не подтвердилась. Дальнейшая проверка нецелесообразна."
Иначе:
  Наблюдение: ... (что реально наблюдается по итогам данных)
  Механизм: ... (почему это происходит — с учётом лабных данных консилиума)
  Прогноз: ... (что ожидать при следующем измерении)
  Что проверить: ... (конкретные анализы или метрики, сроки)

Тон: коллегиальный, медицински точный. На русском языке.
"""

_ARBITER_EXTRACT_PROMPT = """
Из текста консилиума извлеки структурированный JSON. Верни ТОЛЬКО JSON без пояснений.

Правила маппинга:
- "ПОДТВЕРЖДЕНА" (без "частично") → verdict: "confirmed"
- "ЧАСТИЧНО ПОДТВЕРЖДЕНА" → verdict: "partial"
- "ОПРОВЕРГНУТА" → verdict: "rejected"
- confidence: 0.9 если "единогласно", 0.7 если "большинство", 0.5 если "разногласия"
- new_hypothesis: заполни если в тексте есть секция "НОВАЯ ГИПОТЕЗА:", иначе null
- reasoning: СТРОГО 1 предложение, максимум 80 слов. Не больше.

{
  "verdict": "confirmed" | "partial" | "rejected",
  "confidence": 0.0-1.0,
  "reasoning": "одно предложение, максимум 80 слов",
  "new_hypothesis": {
    "observation": "...",
    "mechanism": "...",
    "prediction": "...",
    "test": "..."
  } | null,
  "revised_hypothesis": {
    "observation": "...",
    "mechanism": "...",
    "prediction": "...",
    "test": "..."
  } | null,
  "needs_more_data": true | false,
  "needs_more_data_what": "что проверить" | null
}

Правило для revised_hypothesis:
- Для verdict "confirmed" или "partial" — ОБЯЗАТЕЛЬНО заполни из секции [ОБНОВЛЁННАЯ ГИПОТЕЗА].
- Для verdict "rejected" — null (новая гипотеза при необходимости — в new_hypothesis).

ТЕКСТ КОНСИЛИУМА:
"""


# ── Data package для оценки ──────────────────────────────────────────────────

def _build_eval_data_package(hypothesis: dict) -> str:
    """Строит data package: гипотеза + полная история лабных данных."""
    db.init_db()

    obs     = hypothesis.get("observation", "")
    mech    = hypothesis.get("mechanism", "")
    pred    = hypothesis.get("prediction", "")
    test    = hypothesis.get("test", "")
    created = hypothesis.get("created_date", "неизвестно")

    lab_rows = db.get_lab_history(730)
    try:
        codraw = db.build_codraw_context()   # co-draw: острые отклонения одного забора → единое событие
    except Exception as e:  # noqa: BLE001 — co-draw аддитивен, не роняет пере-оценку
        log.warning(f"codraw eval: {e}")  # silent-ok
        codraw = ""

    import lab_canon
    import labs_db
    lab_block = []
    for lab in lab_rows:
        canon = lab_canon.normalize(lab["test_name"])
        cv, cu = lab_canon.to_conventional(canon, lab.get("value"), lab.get("unit") or "")
        ref = ""
        if lab.get("ref_low") is not None and lab.get("ref_high") is not None:
            ref = f"(норма {lab['ref_low']}–{lab['ref_high']})"
        flag = " ⚠" if lab.get("status") == "flagged" else ""
        lab_block.append(
            f"  {lab['date']}  {canon:22} {str(cv if cv is not None else '—'):>10} "
            f"{(cu or ''):12} {ref}{flag}"
        )

    lines = [
        "═══════════════════════════════════════",
        "🔬 РЕЖИМ ОЦЕНКИ ГИПОТЕЗЫ МДТ-КОНСИЛИУМОМ",
        "═══════════════════════════════════════",
        "",
        "ГИПОТЕЗА (принята пациентом к проверке):",
        f"  Наблюдение:    {obs}",
        f"  Механизм:      {mech}",
        f"  Прогноз:       {pred}",
        f"  Что проверить: {test}",
        f"  Выдвинута:     {created}",
        "",
        f"ЛАБОРАТОРНЫЕ ДАННЫЕ (история, {len(lab_rows)} записей за 2 года, единицы канонизированы):",
        *lab_block,
        # Окно в 2 года названо в заголовке, но не то, что ЗА ним: без объявления
        # «строки нет в пакете» читается как «не сдавался» (context_declares_its_boundary).
        labs_db.declared_boundary(730, unjudged=True),
        "",
        *([codraw, ""] if codraw else []),
        "═══════════════════════════════════════",
        "ЗАДАЧА КОНСИЛИУМА:",
        f"Оцените, подтвердилась ли гипотеза. Предсказание: «{pred}».",
        "Используйте конкретные числа из лабных данных.",
        "═══════════════════════════════════════",
    ]
    # Единый контекст памяти из чата (C-3): жалобы/симптомы из диалогов для оценки гипотезы.
    # reasoning_block безопасен (внутри ловит) — обёртка не нужна.
    import patient_context as _pc
    _rb = _pc.reasoning_block()
    if _rb:
        lines += ["", "ЗАМЕТКИ ИЗ ДИАЛОГОВ (жалобы/симптомы, образ жизни):", _rb]
    return "\n".join(lines)


# ── Async specialist call (eval mode) ────────────────────────────────────────

async def _call_eval_specialist_async(
    client,
    name: str,
    data_package: str,
    semaphore: asyncio.Semaphore,
    round_a_opinions: dict | None = None,
) -> dict:
    """Один специалист в режиме оценки гипотезы."""
    from wellally_consult import _read_specialist_prompt

    async with semaphore:
        spec_prompt = _read_specialist_prompt(name)
        round_label = "B" if round_a_opinions else "A"

        system = (
            f"{spec_prompt}\n\n"
            "ВАЖНО: Отвечай ТОЛЬКО на русском языке. "
            "Анализируй только по своей специальности."
            f"{_SPECIALIST_EVAL_SUFFIX}"
        )

        content = data_package
        if round_a_opinions:
            filtered = {k: v for k, v in round_a_opinions.items() if k != name}
            opinions_block = "\n\n".join(
                f"=== {k} ===\n{v}" for k, v in filtered.items()
            )
            content = (
                data_package + "\n\n"
                "═══ МНЕНИЯ КОЛЛЕГ (Раунд A) ═══\n" + opinions_block + "\n\n"
                "Раунд B: уточни своё мнение с учётом коллег. "
                "Если видишь конфликт — скажи прямо."
            )

        try:
            response = await client.messages.create(task="hypothesis_consilium_eval._call_eval_specialist_async",
                    model=hai_core.get_model("haiku"),
                    max_tokens=400,
                    system=system,
                    messages=[{"role": "user", "content": content}],
                    deadline="measured",   # срок — из замера модели (llm_client.call_timeout)
            )
            log.info(f"  {name} eval (Раунд {round_label}): OK")
            return {"name": name, "opinion": llm_client.answer_text(response), "ok": True}
        except asyncio.TimeoutError:
            log.error(f"  {name} eval (Раунд {round_label}): TIMEOUT")
            return {"name": name, "opinion": "Таймаут.", "ok": False}
        except Exception as e:
            log.error(f"  {name} eval (Раунд {round_label}): {e}")
            return {"name": name, "opinion": f"Ошибка: {e}", "ok": False}


async def _run_eval_round(
    client,
    data_package: str,
    hypothesis: dict,
    round_a_opinions: dict | None = None,
) -> dict:
    """Один раунд для всего фактического состава консилиума в режиме оценки."""
    import consilium_roster   # единый источник состава (MEDICAL_SPECIALISTS убран из wellally_consult)
    import lifestyle_agents as la

    round_label = "B" if round_a_opinions else "A"
    medical_names = consilium_roster.medical_roster()      # единый источник состава
    n_participants = len(medical_names) + len(la.AGENTS)   # НЕ литерал (был «13» → «17/13»)
    log.info(f"Eval Раунд {round_label}: запуск {n_participants} участников...")

    semaphore = asyncio.Semaphore(6)

    async def _guarded(coro):
        async with semaphore:
            return await coro

    eval_question = (
        f"Оцените гипотезу: «{hypothesis.get('observation', '')}». "
        f"Предсказание: «{hypothesis.get('prediction', '')}». "
        "Подтвердились ли лабораторные данные с точки зрения вашей специализации?"
    )
    askers = {name: (lambda name=name: _call_eval_specialist_async(
                  client, name, data_package, semaphore, round_a_opinions))
              for name in medical_names}
    for agent in la.AGENTS:
        askers[agent.agent_name] = lambda agent=agent: _guarded(agent.generate_mdt_opinion(
            sleep_date=get_today(), activity_date=get_today(), patient_question=eval_question,
            round_a_opinions=round_a_opinions, client=client))

    # Все обязаны ответить (consilium_roster.ask_all): не ответивший — повтор, затем отказ.
    answers = await consilium_roster.ask_all(
        askers, lambda r: bool(r.get("ok")) if isinstance(r, dict) else bool(r),
        f"hypothesis_consilium_eval round {round_label}")
    opinions: dict = {n: (r["opinion"] if isinstance(r, dict) else r) for n, r in answers.items()}

    log.info(f"Eval Раунд {round_label} завершён: {len(opinions)}/{n_participants}")
    return opinions


async def _call_eval_coordinator_async(
    client,
    opinions_b: dict,
    data_package: str,
) -> str:
    """Координатор в режиме оценки гипотезы."""
    from wellally_consult import _read_specialist_prompt

    coord_prompt = _read_specialist_prompt("consultation-coordinator")
    system = (
        f"{coord_prompt}\n\n"
        f"{_COORDINATOR_EVAL_PROMPT}\n\n"
        "Отвечай ТОЛЬКО на русском языке."
    ) + hai_core.answer_language()

    opinions_text = "\n\n".join(
        f"=== {name.upper()} ===\n{opinion}"
        for name, opinion in opinions_b.items()
    )

    # Первые 2000 chars data_package — контекст гипотезы и лабов
    user_content = (
        f"ДАННЫЕ И ГИПОТЕЗА:\n{data_package[:2000]}\n\n"
        f"МНЕНИЯ УЧАСТНИКОВ КОНСИЛИУМА (Раунд B):\n{opinions_text}"
    )

    ok_count = sum(1 for v in opinions_b.values() if "Ошибка" not in v and "Таймаут" not in v)
    log.info(f"Eval координатор: синтез {ok_count}/{len(opinions_b)} мнений...")

    try:
        response = await client.messages.create(task="hypothesis_consilium_eval._call_eval_coordinator_async",
                model=hai_core.get_model("sonnet"),
                max_tokens=2048,
                system=system,
                messages=[{"role": "user", "content": user_content}],
                deadline="measured",   # срок — из замера модели (llm_client.call_timeout)
        )
        return llm_client.answer_text(response)
    except asyncio.TimeoutError:
        log.error("Eval координатор: срок по замеру модели исчерпан")
        raise RuntimeError(i18n.t("hypothesis_consilium.error.timeout"))


def _arbiter_extract_verdict(coordinator_text: str) -> dict:
    """Haiku извлекает структурированный JSON из текста координатора."""
    client = get_client()

    prompt = _ARBITER_EXTRACT_PROMPT + coordinator_text + hai_core.answer_language()

    try:
        resp = client.messages.create(task="hypothesis_consilium_eval._arbiter_extract_verdict",
            model=hai_core.get_model("haiku_pinned"),
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = llm_client.answer_text(resp).strip()

        # Убираем markdown-обёртку ```json ... ```
        clean = re.sub(r'^```(?:json)?\s*', '', raw, flags=re.MULTILINE)
        clean = re.sub(r'```\s*$', '', clean, flags=re.MULTILINE).strip()

        # Берём от первого { до последнего }
        start = clean.find('{')
        end   = clean.rfind('}')
        if start != -1 and end != -1 and end > start:
            json_str = clean[start:end + 1]
            try:
                return _json.loads(json_str)
            except _json.JSONDecodeError as jde:
                log.error(f"Arbiter JSONDecodeError: {jde} | json_str[:300]={json_str[:300]}")
                pass
        else:
            log.error(f"Arbiter: не найдены {{ }} в clean: {clean[:200]}")

        log.error(f"Arbiter: не удалось разобрать JSON: raw[:200]={raw[:200]}")
    except Exception as e:
        log.error(f"Arbiter exception: {e}")

    # Fallback: partial с coordinator_text как reasoning
    return {
        "verdict": "partial",
        "confidence": 0.5,
        "reasoning": coordinator_text[:400],
        "new_hypothesis": None,
        "needs_more_data": True,
        "needs_more_data_what": None,
    }


# ── Основная точка входа ─────────────────────────────────────────────────────

async def evaluate_hypothesis_via_consilium(memory_id: int) -> dict:
    """
    Запускает полный консилиум (Round A + Round B + coordinator + arbiter)
    для оценки конкретной гипотезы против лабных данных.

    Returns:
        dict: verdict, confidence, reasoning, new_hypothesis,
              needs_more_data, coordinator_text, memory_id
    """
    import anthropic

    # Загружаем гипотезу
    rows = db.get_memory(category="hypothesis", n=200, active_only=False)
    hypothesis = None
    for row in rows:
        if row["id"] == memory_id:
            try:
                payload = _json.loads(row["value"])
                payload["memory_id"] = memory_id
                hypothesis = payload
            except Exception:
                pass
            break

    if hypothesis is None:
        raise ValueError(i18n.t("hypotheses.error.not_found", hypothesis_id=memory_id).removesuffix("."))

    log.info(
        f"=== Оценка гипотезы #{memory_id}: "
        f"{hypothesis.get('observation', '')[:60]} ==="
    )


    client = llm_client.guarded_client(async_=True)

    data_package = _build_eval_data_package(hypothesis)

    # Round A — весь состав независимо
    opinions_a = await _run_eval_round(
        client, data_package, hypothesis, round_a_opinions=None
    )

    # Round B — весь состав видит Round A
    opinions_b = await _run_eval_round(
        client, data_package, hypothesis, round_a_opinions=opinions_a
    )

    # Координатор синтезирует
    coordinator_text = await _call_eval_coordinator_async(
        client, opinions_b, data_package
    )

    # Arbiter извлекает JSON
    verdict_dict = _arbiter_extract_verdict(coordinator_text)
    verdict_dict["memory_id"] = memory_id
    verdict_dict["coordinator_text"] = coordinator_text

    # Guard: для confirmed/partial revised_hypothesis обязателен
    if verdict_dict.get("verdict") in ("confirmed", "partial"):
        if not verdict_dict.get("revised_hypothesis"):
            log.error(
                f"Arbiter #{memory_id}: revised_hypothesis отсутствует "
                f"при verdict={verdict_dict['verdict']} — переводим в error"
            )
            verdict_dict["verdict"] = "error"
            verdict_dict["reasoning"] = i18n.t("hypothesis_consilium.error.no_revision")

    # Сохраняем outcome
    evidence = _json.dumps(
        {"opinions_b_sample": {k: v[:100] for k, v in list(opinions_b.items())[:5]}},
        ensure_ascii=False,
    )
    db.save_hypothesis_outcome(
        memory_id        = memory_id,
        verdict          = verdict_dict["verdict"],
        confidence       = verdict_dict.get("confidence"),
        evidence         = evidence,
        reasoning        = verdict_dict.get("reasoning"),
        coordinator_text = coordinator_text,
    )

    log.info(
        f"Гипотеза #{memory_id}: вердикт={verdict_dict['verdict']}, "
        f"conf={verdict_dict.get('confidence')}"
    )
    return verdict_dict
