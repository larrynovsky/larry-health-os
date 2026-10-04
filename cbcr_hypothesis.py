"""cbcr_hypothesis.py — CBCR-методологическая генерация гипотез.

Wave 5A W-3 (2026-05-13).

Использует:
- cbcr_manifest.md как system prompt (CBCR operational ядро)
- cbcr_lookup.read_cbcr_concept как Anthropic tool для углубления
- claude-sonnet-4-6 (Haiku недостаточно для bilingual медиц. reasoning)

Public API:
    generate_hypothesis(observation: dict) -> dict
        observation = {
            "trigger": "drift" | "correlation_drift",
            "summary": "ВСР упала на -20% (5д подряд)",
            "details": {...},  # сырые цифры
            "patient_context": str,  # короткий контекст из БД
        }
        Returns: hypothesis dict по CBCR-схеме.

Архитектура:
1. Загружаем manifest из ~/iCloud/.../cbcr_manifest.md
2. Sonnet вызов с tools=[read_cbcr_concept]
3. Tool-loop: если LLM запрашивает концепт → читаем, return, continue
4. Финальный ответ должен быть JSON-гипотезой
5. Парсим JSON. Если не парсится — pытаемся ещё раз или fail.
"""
from __future__ import annotations

import llm_client
import hai_core

import json
import logging
import re
from datetime import date
from _time_inject import get_today  # seam
from pathlib import Path

import anthropic

from cbcr_lookup import read_cbcr_concept, get_tool_schema

log = logging.getLogger(__name__)

MANIFEST_PATH = Path(__file__).resolve().parent / "methodology" / "cbcr" / "cbcr_manifest.md"  # git (СК-1: load-bearing не в iCloud, 2026-07-11)

# Роль модели, не литерал: модель резолвится при вызове (hai_core.get_model), см. _model().
MODEL_ROLE = "sonnet"


def _model() -> str:
    return hai_core.get_model(MODEL_ROLE)
MAX_TOOL_ITERATIONS = 5
MAX_TOKENS_GEN = 8000



# ── W5A-INT-7: cost estimation (Anthropic pricing) ─────────────────────────
# Per 1M tokens, $USD. Источник: Anthropic pricing page.
# Sonnet 4.x: $3/$15. Haiku 4.5: $1/$5 (audit 2026-06-17 — раньше тут стоял
# устаревший прайс Haiku 3.5 $0.8/$4.0; расхождение с monthly_api_report.PRICES
# теперь закрыто, см. tests/unit/test_model_pricing.py).
_PRICING_PER_MTOK = {
    "claude-sonnet-4-6":         {"input": 3.0, "output": 15.0},
    "claude-sonnet-4-5":         {"input": 3.0, "output": 15.0},
    "claude-haiku-4-5":          {"input": 1.0, "output": 5.0},
    "claude-haiku-4-5-20251001": {"input": 1.0, "output": 5.0},
}


def _estimate_cost_usd(usage, model: str) -> float:
    """USD cost для одного API-вызова. usage — anthropic.types.Usage object или dict."""
    pricing = _PRICING_PER_MTOK.get(model, {"input": 3.0, "output": 15.0})
    if hasattr(usage, "input_tokens"):
        inp = int(getattr(usage, "input_tokens", 0) or 0)
        out = int(getattr(usage, "output_tokens", 0) or 0)
    elif isinstance(usage, dict):
        inp = int(usage.get("input_tokens", 0) or 0)
        out = int(usage.get("output_tokens", 0) or 0)
    else:
        return 0.0
    return round((inp / 1_000_000) * pricing["input"] + (out / 1_000_000) * pricing["output"], 5)


def _load_manifest() -> str:
    if not MANIFEST_PATH.exists():
        raise RuntimeError(
            f"CBCR manifest не найден: {MANIFEST_PATH}\n"
            "Без manifest качество гипотез деградирует. Файл в git: methodology/cbcr/cbcr_manifest.md."
        )
    return MANIFEST_PATH.read_text(encoding='utf-8')


def _get_client():
    # С 2026-09-02 (a180e88) здесь стоял ANTHROPIC_KEY_PATH, которого в модуле больше нет:
    # NameError, и CBCR молча не рождал гипотез (literature-read.log 07.09, 22.09 — WARNING).
    return llm_client.guarded_client()



# ── W5A-D6: dynamic patient context (Rule #9) ────────────────────────────────

def _build_patient_context_block() -> str:
    """Динамически инжектируется в system prompt CBCR generator.

    Использует уже готовые helpers (Étape 1-3, Rule #9):
    - hai_core._build_patient_profile()  — profile из get_profile_context()
    - gp_agent._build_clinical_history() — periods + active problem_list

    Per-call, не module-const. Безопасные fallback'и при недоступности.
    """
    profile_str = "[Профиль пациента недоступен — DB не отвечает]"
    history_str = "[История болезни недоступна]"
    try:
        import hai_core
        profile_str = hai_core._build_patient_profile()
    except Exception as e:
        log.warning(f"_build_patient_context_block: hai_core profile failed: {e}")
    try:
        import gp_agent
        history_str = gp_agent._build_clinical_history()
    except Exception as e:
        log.warning(f"_build_patient_context_block: gp_agent history failed: {e}")
    return (
        "\n\n---\n\n"
        "## Профиль пациента (DB-derived)\n\n"
        f"{profile_str}\n\n"
        "## Клиническая история (DB-derived)\n\n"
        f"{history_str}\n"
    )


def _extract_json(text: str) -> dict | None:
    '''Извлекает JSON-объект из ответа LLM.

    LLM может обернуть JSON в ```json ... ``` или вернуть голый объект.
    Используем strip-подход вместо non-greedy regex: снимаем внешний fence
    по первому символу, иммунны к ``` внутри строк JSON.
    2026-06-26: исправлен баг — non-greedy .*? останавливался на первом ```
    внутри текста (напр. в примерах), что приводило к неполному JSON.
    '''
    text = text.strip()
    # Strip outer markdown code fence (immune to inner ``` in string values)
    if text.startswith("```"):
        nl = text.find("\n")
        if nl >= 0:
            text = text[nl + 1:].strip()
        # Strip closing fence if present
        stripped = text.rstrip()
        if stripped.endswith("```"):
            text = stripped[:-3].rstrip()
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    end = -1
    in_str = False
    esc = False
    for i, ch in enumerate(text[start:], start=start):
        if esc:
            esc = False
            continue
        if ch == "\\":
            esc = True
            continue
        if ch == '"' and not esc:
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end < 0:
        return None
    try:
        return json.loads(text[start:end])
    except json.JSONDecodeError:
        return None




def compute_structural_confidence(hyp: dict) -> dict:
    """Детерминированная функция от структуры — manifest §10. Не LLM-claim.

    Score правила:
      +2 если ≥3 evidence_for с weight strong/moderate
      +1 если evidence_against непустое
      +1 если ≥2 alternative_explanations с конкретным rule_out_by
      +1 если все 4 поля falsification заполнены непустыми строками
      +1 если bias_risks ≥1 проанализирован
      -2 если bias_risks ≥2 (косвенный сигнал что LLM сам сомневается)
      -1 если illness_script.fault.mechanism == null или "unclear"
    Cap: 0-6. → low (0-2) / medium (3-4) / high (5-6).
    """
    score = 0
    breakdown: dict[str, str] = {}

    ev_for = hyp.get("evidence_for") or []
    strong_or_moderate = sum(1 for e in ev_for if e.get("weight") in ("strong", "moderate"))
    if strong_or_moderate >= 3:
        score += 2
        breakdown["evidence_for_strong_moderate_>=3"] = "+2"
    else:
        breakdown["evidence_for_strong_moderate_>=3"] = f"+0 (got {strong_or_moderate})"

    if hyp.get("evidence_against"):
        score += 1
        breakdown["evidence_against_not_empty"] = "+1"
    else:
        breakdown["evidence_against_not_empty"] = "+0 (EMPTY — confirmation bias risk)"

    alts = hyp.get("alternative_explanations") or []
    with_rule = sum(1 for a in alts if a.get("rule_out_by"))
    if with_rule >= 2:
        score += 1
        breakdown["alternatives_with_rule_out_by_>=2"] = "+1"
    else:
        breakdown["alternatives_with_rule_out_by_>=2"] = f"+0 (got {with_rule})"

    fals = hyp.get("falsification") or {}
    req = ["etiological_confirmation", "etiological_refutation",
           "therapeutic_response", "decision_threshold"]
    if all(isinstance(fals.get(k), str) and fals.get(k).strip() for k in req):
        score += 1
        breakdown["all_4_falsification_fields"] = "+1"
    else:
        breakdown["all_4_falsification_fields"] = "+0 (one or more missing/empty)"

    biases = hyp.get("bias_risks") or []
    if len(biases) >= 1:
        score += 1
        breakdown["bias_risks_analysed"] = "+1"
    else:
        breakdown["bias_risks_analysed"] = "+0 (no bias analysis — suspicious)"

    # W5A-D4: штраф только за биasы БЕЗ mitigation. Признание bias с конкретной
    # стратегией защиты — это качество, не проблема. Naked acknowledgement без
    # mitigation → возможный LLM-самозащитный жест без операционального плана.
    biases_no_mitigation = [
        b for b in biases
        if not (isinstance(b, dict) and isinstance(b.get("mitigation"), str)
                and b["mitigation"].strip())
    ]
    if len(biases_no_mitigation) >= 2:
        score -= 2
        breakdown["bias_no_mitigation_penalty"] = (
            f"-2 (≥2 биasов без mitigation: "
            f"{[b.get('bias_type') for b in biases_no_mitigation][:3]})"
        )
    else:
        breakdown["bias_no_mitigation_penalty"] = (
            f"+0 ({len(biases_no_mitigation)} биasов без mitigation, в пределах)"
        )

    # B5: LLM варьирует имя поля fault (mechanism/description/primary_mechanism).
    # Считаем fault "задан" если хотя бы одно из этих полей непустая строка.
    fault = (hyp.get("illness_script") or {}).get("fault") or {}
    if isinstance(fault, dict):
        candidate_keys = ("mechanism", "description", "primary_mechanism")
        fault_text = next(
            (fault[k] for k in candidate_keys if isinstance(fault.get(k), str) and fault.get(k).strip()),
            None,
        )
    elif isinstance(fault, str):
        fault_text = fault
    else:
        fault_text = None
    if not fault_text or "unclear" in fault_text.lower():
        score -= 1
        breakdown["fault_unclear_penalty"] = "-1"

    score = max(0, min(6, score))
    if score <= 2:
        level = "low"
    elif score <= 4:
        level = "medium"
    else:
        level = "high"

    return {"score": score, "confidence": level, "breakdown": breakdown,
            "computed_by": "code (compute_structural_confidence)"}


def _hypothesis_id(observation: dict) -> str:
    """Генерация ID в коде. LLM не доверяем."""
    trig = (observation.get("trigger") or "drift")[:6]
    summ = observation.get("summary") or ""
    slug = "".join(c if c.isalnum() else "_" for c in summ.lower())[:30].strip("_")
    return f"hyp_{get_today()}_{trig}_{slug}"


def _collect_wiki_reads(messages: list[dict]) -> list[str]:
    """B3: фактические concept-имена, запрошенные через tool_use blocks."""
    reads: list[str] = []
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                inp = block.get("input") or {}
                name = inp.get("name")
                if name:
                    reads.append(name)
    return reads



def _build_user_prompt(observation: dict) -> str:
    trigger = observation.get("trigger", "unknown")
    summary = observation.get("summary", "—")
    details = observation.get("details", {})
    patient_ctx = observation.get("patient_context", "")
    return (
        f"# Триггер генерации\n\n"
        f"**Тип:** {trigger}\n"
        f"**Сводка наблюдения:** {summary}\n\n"
        f"## Сырые данные (для evidence_for.fact, не для голословных утверждений)\n"
        f"```json\n{json.dumps(details, ensure_ascii=False, indent=2)}\n```\n\n"
        f"## Контекст пациента (если краткое в payload — иначе бери из памяти manifest)\n"
        f"{patient_ctx}\n\n"
        f"# Задача\n\n"
        f"Сформулируй полноценную клиническую гипотезу по CBCR-методологии.\n"
        f"Применяй illness_script (4 слота), semantic qualifiers, ≥3 alternative_explanations,\n"
        f"разделённое falsification (4 поля), evidence_against ≥1, bias_risks анализ.\n"
        f"Используй read_cbcr_concept по необходимости — особенно для biases и semantic qualifiers.\n\n"
        f"Ответ — ТОЛЬКО валидный JSON-объект (без markdown-обёртки) по схеме из manifest §«Output requirement»."
    )


def _execute_tool_call(name: str, tool_input: dict) -> str:
    if name == "read_cbcr_concept":
        return read_cbcr_concept(tool_input.get("name", ""))
    return f"❌ Неизвестный tool: {name}"


def generate_hypothesis(observation: dict) -> dict:
    '''Генерирует CBCR-гипотезу через Sonnet + tool-loop.

    Returns: dict с CBCR-полями (см. cbcr_manifest §«Output requirement»).
    Raises: RuntimeError при критических ошибках (нет ключа, нет manifest, нет JSON в ответе).
    '''
    manifest = _load_manifest()
    patient_block = _build_patient_context_block()  # W5A-D6: per-call из БД
    system_prompt = manifest + patient_block + hai_core.answer_language()
    client = _get_client()
    tool_schema = get_tool_schema()

    messages: list[dict] = [
        {"role": "user", "content": _build_user_prompt(observation)},
    ]
    total_cost_usd = 0.0  # W5A-INT-7

    for iteration in range(MAX_TOOL_ITERATIONS):
        resp = client.messages.create(task="cbcr_hypothesis.generate_hypothesis",
            model=_model(),
            max_tokens=MAX_TOKENS_GEN,
            system=system_prompt,
            tools=[tool_schema],
            messages=messages,
        )
        total_cost_usd += _estimate_cost_usd(getattr(resp, "usage", None), _model())

        # Если LLM запросила tool — выполняем и продолжаем loop
        if resp.stop_reason == "tool_use":
            assistant_blocks = resp.content
            tool_results: list[dict] = []
            for block in assistant_blocks:
                if getattr(block, "type", None) == "tool_use":
                    tool_name = block.name
                    tool_input = block.input
                    log.info(f"CBCR tool call: {tool_name}({tool_input})")
                    result = _execute_tool_call(tool_name, tool_input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })
            # Накапливаем history: assistant turn + user turn с tool_results
            messages.append({"role": "assistant", "content": [b.model_dump() for b in assistant_blocks]})
            messages.append({"role": "user", "content": tool_results})
            continue

        # Финальный ответ — должен содержать JSON
        text = ""
        for block in resp.content:
            if getattr(block, "type", None) == "text":
                text += block.text
        parsed = _extract_json(text)
        if parsed is None:
            raise RuntimeError(
                f"LLM не вернула валидный JSON. stop_reason={resp.stop_reason}, "
                f"text preview: {text[:300]!r}"
            )
        # B1-B4: принудительная перезапись доверенных полей провенанса.
        parsed["hypothesis_id"] = _hypothesis_id(observation)  # B2: ID из кода
        parsed.setdefault("provenance", {})
        parsed["provenance"]["generator"] = "cbcr_hypothesis.generate_hypothesis"
        parsed["provenance"]["model"] = _model()
        parsed["provenance"]["model_version_date"] = str(get_today())  # B1: реальная дата вызова
        parsed["provenance"]["tool_iterations"] = iteration
        parsed["provenance"]["wiki_reads"] = _collect_wiki_reads(messages)  # B3: из tool_use blocks
        parsed["provenance"]["cost_estimate_usd"] = round(total_cost_usd, 5)  # W5A-INT-7
        # B4: structural_confidence — детерминированная функция, не LLM-claim
        parsed["structural_confidence"] = compute_structural_confidence(parsed)
        log.info(
            f"CBCR generate_hypothesis: tool_iter={iteration}, "
            f"cost=${total_cost_usd:.4f}, model={_model()}"
        )
        return parsed

    raise RuntimeError(
        f"Превышен лимит tool-iterations ({MAX_TOOL_ITERATIONS}) — "
        "LLM крутится в tool-loop. Возможно сломан промпт или infinite ask."
    )


if __name__ == "__main__":
    # Smoke (требует ANTHROPIC ключ).
    import sys
    if "--check-config" in sys.argv:
        print(f"Manifest: {MANIFEST_PATH.exists()} ({MANIFEST_PATH})")
        print(f"Key:      {llm_client._KEY_FILE.exists()}")
        print(f"Model:    {_model()}")
        sys.exit(0)
    obs = {
        "trigger": "drift",
        "summary": "Глубокий сон снизился sub-acute monotonic за 90д",
        "details": {"current_7d": 38.2, "baseline_30d": 47.1, "delta_pct": -18.9, "streak_days": 14},
        "patient_context": "[smoke-test context — см. _build_patient_context_block()]",
    }
    print(json.dumps(generate_hypothesis(obs), ensure_ascii=False, indent=2))



# ── W5A-INT-8: flatten CBCR payload → legacy save_hypothesis fields ─────────

def flatten_cbcr_payload(hyp: dict) -> dict:
    """Извлекает плоские поля observation/mechanism/prediction/test из CBCR JSON.

    save_hypothesis() в hai_hypotheses.py ожидает эти поля. CBCR-payload их не
    содержит напрямую — нужен mapping. Также возвращает resolution_type как есть.

    Returns: {"observation", "mechanism", "prediction", "test", "resolution_type"}.
    Если ключевые источники пусты — поля будут пустыми строками (не None) для
    совместимости с str-параметрами save_hypothesis.
    """
    # observation
    observation = str(hyp.get("one_line_statement") or "").strip()

    # mechanism — три кандидата (B5 паттерн)
    fault = (hyp.get("illness_script") or {}).get("fault") or {}
    if isinstance(fault, dict):
        mechanism = next(
            (str(fault[k]).strip() for k in ("description", "mechanism", "primary_mechanism")
             if isinstance(fault.get(k), str) and fault.get(k).strip()),
            "",
        )
    elif isinstance(fault, str):
        mechanism = fault.strip()
    else:
        mechanism = ""

    # prediction — falsification.etiological_confirmation
    fals = hyp.get("falsification") or {}
    prediction = str(fals.get("etiological_confirmation") or "").strip()

    # test — immediate_next_steps[0] → specialist_referral_trigger → prediction (fallback)
    lor = hyp.get("line_of_reasoning") or {}
    steps = lor.get("immediate_next_steps") or []
    if steps and isinstance(steps, list) and isinstance(steps[0], str):
        test = steps[0].strip()
    else:
        test = str(lor.get("specialist_referral_trigger") or "").strip()
    if not test:
        # prediction содержит измеримое предсказание — достаточно как actionable тест
        test = prediction
        if test:
            log.warning("flatten_cbcr_payload: immediate_next_steps и specialist_referral_trigger пустые, test ← prediction")

    return {
        "observation": observation,
        "mechanism": mechanism,
        "prediction": prediction,
        "test": test,
        "resolution_type": hyp.get("resolution_type", "self_managed"),
    }


# ── W-4: Critique pass (forced QA) ──────────────────────────────────────────

CRITIQUE_SYSTEM = '''Ты — независимый клинический методолог. Применяй CBCR (ten Cate 2018)
строго и без снисхождения. Критикуй гипотезу против чек-листа из 8 пунктов.

Используй read_cbcr_concept по необходимости (особенно для проверки biases и
semantic qualifiers, если сомневаешься).

ЧЕК-ЛИСТ (каждый — либо ОК, либо проблема):

1. **Illness Script complete**: есть ли все 4 слота — enabling_conditions
   (с 4 sub-категориями), fault, consequences (с 7 sub-полями), course_and_management?
   Не считается ОК, если слоты заполнены строкой "не определено" без null_reason.

2. **Semantic Qualifiers in one_line_statement**: использованы ли abstract
   qualifiers (sub-acute/monotonic/post-treatment) или это просто цифры?

3. **Problem Representation**: присутствует ли формулировка проблемы перед
   гипотезой, как требует Bordage?

4. **Differential ≥3 alternatives**: каждая с конкретным rule_out_by?
   Не считается ОК, если есть alternatives но без rule_out_by.

5. **Evidence_against непустое**: ≥1 запись с конкретным контраргументом?
   Пусто = confirmation bias → fail.

6. **Falsification 4 поля разделены**: etiological_confirmation, etiological_refutation,
   therapeutic_response, decision_threshold — все 4 заполнены и СМЫСЛОВО разные?
   Смешивание "would_confirm = therapeutic response" → fail.

7. **Line of reasoning**: гипотеза указывает что искать дальше / какой следующий шаг?

8. **Bias risks анализ**: проанализированы ли availability/representative/confirmation/
   anchoring/satisficing/outcome (Kempainen 2003)? **МИНИМУМ 4 из 6** явно упомянуты,
   каждый с конкретным mitigation. 2-3 биaса = недостаточно — это search satisficing
   на уровне самой критики (LLM расслабляется, потому что чек-лист допускает мало).

9. **No phantom PubMed citations**: если evidence_for[].source содержит "pubmed",
   "literature", "PubMed", "PMID" — то fact ОБЯЗАН содержать хотя бы один реальный
   PMID в формате `PMID:\\d+` или `PMID \\d+`. Pipeline НЕ имеет PubMed tool —
   любая "цитата" без PMID — это галлюцинация LLM. Это критическая проблема
   (false trust для врача).

10. **No unsupported numeric claims**: каждое число с единицей времени/процента/лаб
    (`5%`, `6-18 мес`, `30 дней`, `2.5 мМЕ/л`) в evidence_for[].fact ДОЛЖНО быть:
    (a) подтверждено из observation.details (приходят из real БД-данных), ИЛИ
    (b) сопровождено реальным PMID в том же fact-поле.
    Если число придумано LLM "по памяти" (например, "exampliplatin персистирует 6-18 мес"
    без PMID) — это hallucinated stat. ≥3 таких → fail.

ВЕРДИКТ:
- "pass" — все 8 ОК (мелкие огрехи допустимы, если суть на месте).
- "regenerate" — есть критические проблемы (illness_script неполный, alternatives <3
   или без rule_out_by, evidence_against пусто, falsification смешано).

Ответ — ТОЛЬКО валидный JSON:
{
  "verdict": "pass" | "regenerate",
  "checklist": {"1_illness_script": "ok"|"problem: ...", ...},
  "critical_issues": ["..."],
  "regenerate_feedback": "Что конкретно надо исправить (если regenerate)"
}'''

MAX_REGEN_ITERATIONS = 2  # один regenerate после первой попытки



# ── W5A-D1: detect phantom PubMed citations ─────────────────────────────────

_PMID_RE = re.compile(r"PMID[:\s]*\d{3,}", re.IGNORECASE)
_PUBMED_SOURCE_RE = re.compile(r"pubmed|literature|PMID", re.IGNORECASE)


def _check_phantom_pubmed(hyp: dict) -> list[str]:
    """Возвращает список critical_issues: source с упоминанием pubmed без PMID в fact.

    Pipeline не имеет PubMed tool (см. provenance.data_sources). Любая claim
    с source=pubmed без реального PMID — галлюцинация LLM, создающая ложное
    доверие у врача. Это критическая проблема.
    """
    issues: list[str] = []
    for i, ev in enumerate(hyp.get("evidence_for") or []):
        src = str(ev.get("source") or "")
        fact = str(ev.get("fact") or "")
        if _PUBMED_SOURCE_RE.search(src) and not _PMID_RE.search(fact):
            issues.append(
                f"evidence_for[{i}]: source='{src}' заявляет PubMed/литературу, "
                f"но в fact нет PMID. Pipeline не имеет PubMed tool — это phantom citation. "
                f"Либо убери source, либо приведи реальный PMID."
            )
    return issues




# ── W5A-D3: detect insufficient bias coverage ───────────────────────────────

_BIAS_KEYWORDS = {
    "availability":   ["availability", "доступ", "доступности", "вспоминаем"],
    "representative": ["representative", "представит", "репрезентативн", "сходств", "прототип"],
    "confirmation":   ["confirmation", "подтвержд", "конфирм"],
    "anchoring":      ["anchoring", "якор", "якорн"],
    "satisficing":    ["satisficing", "search satisf", "довольств", "удовлетвор",
                       "первое объясн", "первая гипотез"],
    "outcome":        ["outcome", "исход", "результат-биас", "результат-bias"],
}


def _check_bias_minimum(hyp: dict, required: int = 4) -> list[str]:
    """≥`required` биasов из канонических 6 (Kempainen 2003) должны быть упомянуты.

    #138: LLM варьирует имя поля (bias_type vs bias vs name vs cognitive_bias).
    Решение: scan по ВСЕМ строковым значениям dict рекурсивно (depth=2).

    Иначе → critical_issue. Каждый bias должен иметь mitigation (D4 отдельно).
    """
    biases = hyp.get("bias_risks") or []
    if not biases:
        return [f"bias_risks пустой — нужно минимум {required} биasов из 6."]

    def _flatten_strs(obj, depth=2) -> str:
        """Собирает все строковые значения в нижнем регистре."""
        if depth < 0:
            return ""
        if isinstance(obj, str):
            return obj.lower() + " "
        if isinstance(obj, dict):
            return "".join(_flatten_strs(v, depth - 1) for v in obj.values())
        if isinstance(obj, (list, tuple)):
            return "".join(_flatten_strs(v, depth - 1) for v in obj)
        return ""

    found: set[str] = set()
    for b in biases:
        text = _flatten_strs(b) if isinstance(b, dict) else str(b).lower()
        for canon, kws in _BIAS_KEYWORDS.items():
            if any(kw in text for kw in kws):
                found.add(canon)
    if len(found) < required:
        missing = set(_BIAS_KEYWORDS.keys()) - found
        return [
            f"bias_risks покрывает только {len(found)}/{required}+ канонических биasов "
            f"(найдено: {sorted(found)}). Добавь хотя бы: {sorted(missing)[:required - len(found)]}."
        ]
    return []



# ── Wave 5F-1: patient_view validation ──────────────────────────────────────

_JARGON_BLACKLIST = [
    "sub-acute", "subacute", "monotonic", "диссоциация", "дисфункция",
    "нейротоксичность", "нейротоксический", "патогенез", "ятрогенный",
    "falsification", "etiological", "illness script", "semantic qualifier",
    "антикорреляция", "decision threshold", "encapsulated", "differential",
    "fault.mechanism", "structural confidence",
    "dissociation", "dysfunction", "neurotoxicity", "neurotoxic", "pathogenesis",
    "iatrogenic", "anticorrelation", "anti-correlation",
]

_PATIENT_VIEW_REQUIRED_KEYS = ("noticed", "might_mean", "do_now", "consult_when")


def _check_patient_view(hyp: dict) -> list[str]:
    """Wave 5F-1: patient_view обязателен + без жаргона.

    Возвращает list of critical_issues. Пустой = ok.
    """
    pv = hyp.get("patient_view")
    if not isinstance(pv, dict):
        return ["patient_view отсутствует или не dict — требуется для Telegram-уведомления пациенту"]

    issues: list[str] = []
    # Все 4 ключа присутствуют и непустые
    for k in _PATIENT_VIEW_REQUIRED_KEYS:
        v = pv.get(k)
        if not isinstance(v, str) or not v.strip():
            issues.append(f"patient_view.{k} пустой или не строка")

    # Jargon-check: каждое поле не содержит запрещённых терминов
    for k in _PATIENT_VIEW_REQUIRED_KEYS:
        v = (pv.get(k) or "").lower()
        if not v:
            continue
        found_jargon = [j for j in _JARGON_BLACKLIST
                        if (re.search(r"\b" + re.escape(j) + r"\b", v) if j.isascii() else j in v)]
        if found_jargon:
            issues.append(
                f"patient_view.{k} содержит медицинский жаргон: {found_jargon[:3]} — "
                f"перепиши простым языком"
            )

    return issues


# ── W5A-D2: detect unsupported numeric claims ───────────────────────────────

_NUM_WITH_UNIT_RE = re.compile(
    r"(\d+(?:[.,]\d+)?(?:\s*[-–—]\s*\d+(?:[.,]\d+)?)?)\s*"
    r"(%|мес(?:яцев)?|дн(?:ей|я)?|час(?:ов|а)?|лет|год(?:а|ов)?|недел(?:ь|и)|"
    r"мМЕ/л|нг/мл|мкг/мл|мс|мин|мг/дл|ммоль/л|"
    r"\b(?:months?|days?|hours?|hrs?|years?|weeks?|minutes?|mins?|milliseconds?|"
    r"mIU/l|ng/ml|(?:u|mc)g/ml|ms|mg/dl|mmol/l)\b)",
    re.IGNORECASE,
)


def _collect_observation_numbers(obs: dict) -> set[str]:
    """Извлекает все числа из observation (details + summary) для whitelist.

    Возвращает set строк-чисел в каноническом виде (точка как separator).
    """
    if not obs:
        return set()
    import json as _j
    pieces = [obs.get("summary") or "", _j.dumps(obs.get("details") or {}, ensure_ascii=False)]
    pieces.append(obs.get("patient_context") or "")
    text = " ".join(pieces)
    nums = re.findall(r"\d+(?:[.,]\d+)?", text)
    return {n.replace(",", ".") for n in nums}


def _check_unsupported_numerics(hyp: dict, observation: dict | None = None) -> list[str]:
    """≥3 unsupported числа с единицами → critical_issue.

    Whitelist: число OK если содержится в observation.details/summary ИЛИ
    рядом в fact есть PMID:\\d+.
    """
    if not observation:
        return []  # без observation whitelist невозможен — пропускаем
    allowed = _collect_observation_numbers(observation)
    issues: list[str] = []
    for i, ev in enumerate(hyp.get("evidence_for") or []):
        fact = str(ev.get("fact") or "")
        has_pmid = bool(_PMID_RE.search(fact))
        if has_pmid:
            continue  # любое число с реальным PMID — ok
        for m in _NUM_WITH_UNIT_RE.finditer(fact):
            num_raw = m.group(1)
            unit = m.group(2)
            # canon-форма для сравнения
            for part in re.split(r"[-–—]", num_raw):
                p_clean = part.strip().replace(",", ".")
                if p_clean and p_clean not in allowed:
                    issues.append(
                        f"evidence_for[{i}]: число '{p_clean} {unit}' в fact "
                        f"не подтверждено observation и без PMID — "
                        f"возможно LLM-фантазия."
                    )
                    break  # одна запись = одна issue
    # ≥3 unsupported → critical для regenerate
    if len(issues) >= 3:
        return issues
    return []  # 1-2 unsupported допускаем (soft warning, не блокер)


def critique_hypothesis(hypothesis: dict, observation: dict | None = None) -> dict:
    '''Critique hypothesis через Sonnet + tool. Returns critique dict.

    observation: если передан — используется для whitelist в numeric/PubMed checks.
    '''
    client = _get_client()
    tool_schema = get_tool_schema()

    user_msg = (
        f"Оцени гипотезу против чек-листа.\n\n"
        f"```json\n{json.dumps(hypothesis, ensure_ascii=False, indent=2)}\n```"
    )
    messages: list[dict] = [{"role": "user", "content": user_msg}]

    total_cost_usd = 0.0  # W5A-INT-7
    for iteration in range(MAX_TOOL_ITERATIONS):
        resp = client.messages.create(task="cbcr_hypothesis.critique_hypothesis",
            model=_model(),
            max_tokens=2000,
            system=CRITIQUE_SYSTEM,
            tools=[tool_schema],
            messages=messages,
        )
        total_cost_usd += _estimate_cost_usd(getattr(resp, "usage", None), _model())
        if resp.stop_reason == "tool_use":
            assistant_blocks = resp.content
            tool_results: list[dict] = []
            for block in assistant_blocks:
                if getattr(block, "type", None) == "tool_use":
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": _execute_tool_call(block.name, block.input),
                    })
            messages.append({"role": "assistant", "content": [b.model_dump() for b in assistant_blocks]})
            messages.append({"role": "user", "content": tool_results})
            continue
        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
        parsed = _extract_json(text)
        if parsed is None:
            # Sonnet после tool_use иногда переходит в narrative mode.
            # Retry с явным reminder; продолжаем loop.
            log.warning(f"Critique iter {iteration}: no JSON, sending JSON-reminder")
            messages.append({"role": "assistant", "content": text or "(empty)"})
            messages.append({"role": "user", "content": (
                "Верни СТРОГО валидный JSON-объект по схеме из system prompt — "
                "{verdict, checklist, critical_issues, regenerate_feedback}. "
                "Без markdown-обёртки и без преамбулы. Только JSON."
            )})
            continue
        # W5A-D1: code-side phantom PubMed detector (двухслойная защита)
        phantom_issues = _check_phantom_pubmed(hypothesis)
        if phantom_issues:
            if not isinstance(parsed.get("critical_issues"), list):
                parsed["critical_issues"] = []
            parsed["critical_issues"].extend(phantom_issues)
            parsed["verdict"] = "regenerate"
            if not isinstance(parsed.get("regenerate_feedback"), str):
                parsed["regenerate_feedback"] = ""
            if not isinstance(parsed.get("critical_issues"), list):
                parsed["critical_issues"] = []
            parsed["regenerate_feedback"] += (
                "\n\n[W5A-D1] Обнаружены phantom PubMed citations: "
                + "; ".join(phantom_issues)
                + "\nУбери source=pubmed/literature ИЛИ приведи реальный PMID."
            )
        parsed["_cost_estimate_usd"] = round(total_cost_usd, 5)  # W5A-INT-7
        # W5F-1: patient_view обязателен + без жаргона
        pv_issues = _check_patient_view(hypothesis)
        if pv_issues:
            if not isinstance(parsed.get("critical_issues"), list):
                parsed["critical_issues"] = []
            parsed["critical_issues"].extend(pv_issues)
            parsed["verdict"] = "regenerate"
            if not isinstance(parsed.get("regenerate_feedback"), str):
                parsed["regenerate_feedback"] = ""
            parsed["regenerate_feedback"] += (
                "\n\n[W5F-1] patient_view проблемы: "
                + "; ".join(pv_issues)
                + "\nДобавь patient_view с 4 полями (noticed/might_mean/do_now/consult_when) "
                "без медицинских терминов из blacklist."
            )

        # W5A-D3: минимум 4/6 канонических биasов
        bias_issues = _check_bias_minimum(hypothesis, required=4)
        if bias_issues:
            if not isinstance(parsed.get("critical_issues"), list):
                parsed["critical_issues"] = []
            parsed["critical_issues"].extend(bias_issues)
            parsed["verdict"] = "regenerate"
            if not isinstance(parsed.get("regenerate_feedback"), str):
                parsed["regenerate_feedback"] = ""
            if not isinstance(parsed.get("critical_issues"), list):
                parsed["critical_issues"] = []
            parsed["regenerate_feedback"] += (
                "\n\n[W5A-D3] Недостаточное покрытие биasов: "
                + "; ".join(bias_issues)
                + "\nДобавь оставшиеся биasы с конкретным mitigation."
            )
        # W5A-D2: unsupported numeric claims с whitelist по observation
        numeric_issues = _check_unsupported_numerics(hypothesis, observation)
        if numeric_issues:
            if not isinstance(parsed.get("critical_issues"), list):
                parsed["critical_issues"] = []
            parsed["critical_issues"].extend(numeric_issues)
            parsed["verdict"] = "regenerate"
            if not isinstance(parsed.get("regenerate_feedback"), str):
                parsed["regenerate_feedback"] = ""
            if not isinstance(parsed.get("critical_issues"), list):
                parsed["critical_issues"] = []
            parsed["regenerate_feedback"] += (
                "\n\n[W5A-D2] Числовые claims без подтверждения из observation: "
                + "; ".join(numeric_issues)
                + "\nУбери эти числа ИЛИ приведи реальный PMID, ИЛИ переформулируй "
                "как 'неизвестно из имеющихся данных'."
            )
        return parsed
    raise RuntimeError("Critique tool-loop overflow")



def _attach_pipeline_cost(hyp: dict, history: list[dict]) -> None:
    """W5A-INT-7+#139: суммарный pipeline cost — генерация + все critique iterations.
    Кладёт в provenance.pipeline_cost_estimate_usd. Работает в success и forced ветках.
    """
    total = float((hyp.get("provenance") or {}).get("cost_estimate_usd") or 0.0)
    for h in history or []:
        total += float((h.get("critique") or {}).get("_cost_estimate_usd") or 0.0)
    hyp.setdefault("provenance", {})["pipeline_cost_estimate_usd"] = round(total, 5)


def generate_hypothesis_with_critique(observation: dict) -> dict:
    '''Полный pipeline: gen → critique → regenerate (max 1) → принудит. needs_specialist.

    Returns: финальная гипотеза с прикреплённой critique_history.
    '''
    history: list[dict] = []
    current_obs = dict(observation)

    for regen_iter in range(MAX_REGEN_ITERATIONS):
        hyp = generate_hypothesis(current_obs)
        critique = critique_hypothesis(hyp, observation=observation)
        history.append({"iteration": regen_iter, "critique": critique})

        if critique.get("verdict") == "pass":
            hyp["critique_history"] = history
            _attach_pipeline_cost(hyp, history)
            log.info(
                f"CBCR pipeline complete: total_cost="
                f"${hyp['provenance']['pipeline_cost_estimate_usd']:.4f}, "
                f"regen_iters={regen_iter}, verdict=pass"
            )
            return hyp

        # regenerate: добавляем feedback в payload
        fb = critique.get("regenerate_feedback", "")
        issues = critique.get("critical_issues", [])
        current_obs = dict(observation)
        current_obs["regenerate_feedback"] = (
            f"Предыдущая попытка не прошла CBCR-чек-лист.\n"
            f"Критические проблемы:\n" +
            "\n".join(f"- {i}" for i in issues) +
            f"\n\nФидбэк: {fb}\n"
            f"Исправь и сгенерируй заново."
        )
        log.warning(f"Hypothesis regenerate (iter {regen_iter+1}/{MAX_REGEN_ITERATIONS}): {fb[:100]}")

    # Превышен лимит — принудительно needs_specialist
    hyp = generate_hypothesis(current_obs)
    hyp["resolution_type"] = "needs_specialist"
    hyp["critique_history"] = history
    hyp["forced_needs_specialist_reason"] = (
        f"После {MAX_REGEN_ITERATIONS} попыток LLM не сгенерировал гипотезу, "
        "проходящую CBCR-чек-лист. Запрос в Healz.ai."
    )
    _attach_pipeline_cost(hyp, history)
    log.info(
        f"CBCR pipeline forced needs_specialist: total_cost="
        f"${hyp['provenance']['pipeline_cost_estimate_usd']:.4f}"
    )
    return hyp
