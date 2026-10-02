#!/usr/bin/env python3.11
"""llm_admission.py — допуск новой модели к роли по корпусу проверок (нить llm-provider).

ЗАЧЕМ. Решение владельца 2026-10-01: при отзыве модели система переключается сама на
следующую ДОПУЩЕННУЮ модель цепочки роли (`system_config model.<role>`, см.
hai_core.model_chain). Цепочка из одной модели переключаться не умеет — преемник
появляется только здесь: модель прогоняется через корпус задач роли, и прошедшая
дописывается в КОНЕЦ цепочки (первая модель не меняется: это меняло бы поведение сегодня).

ЧТО ДЕЛАЕТ (одна ответственность — суд допуска):
  * кандидаты — модели из списка провайдера новее основной модели семейства роли;
  * наборы по ролям (corpus.json → policy.role_suites): зрение анализов, извлечение
    лечения, текстовая дисциплина; роль допускается только ВСЕМИ своими наборами;
  * правило прохода зафиксировано ДО прогонов (C-83): ни одной ошибки по точному эталону
    ни в одном повторе;
  * бюджет — данные владельца (`llm.admission.budget`), учёт ДО вызова по max_tokens и
    пессимистичному тарифу; бюджета нет — допуск не тратит ни цента;
  * настоящие бланки владельца (каталог данных, llm_corpus/real) идут ТОЛЬКО провайдеру
    anthropic — туда документы и так уходят в работе; чужим — только синтетика.

ЧЕГО НЕ ДЕЛАЕТ: не меняет первую модель цепочки; не выбирает провайдера; не допускает
модели семейства, которое не сопоставлено роли (policy.family_roles) — их просто нет в кандидатах.

Запуск: из run_checks.sh ежедневно с `--weekly --notify` (раз в 7 дней по метке
`llm.admission.last_run`); вручную `--force`, `--dry-run`; бюджет: `--set-budget 10`.
"""
from __future__ import annotations

import base64
import json
import logging
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

log = logging.getLogger(__name__)

CORPUS_DIR = Path(__file__).parent / "methodology" / "llm_corpus"

# Политика допуска (роль → наборы, семейство → роли, повторы, лимиты вывода) — ДАННЫЕ:
# methodology/llm_corpus/corpus.json, ключ policy. Почему opus и sonnet — разные семейства
# и разные роли: так цепочки двух проходов распознавателя не пересекаются
# (two_model_reconciled). Роль без набора, покрывающего её задачи, не допускается.
_WEEK = timedelta(days=7)


# ── данные ───────────────────────────────────────────────────────────────────
def _cfg(key, default=None):
    import health_db as db
    return db.get_config(key, default)


def _put(key, **kw):
    import health_db as db
    db.upsert_config(key, category="llm", source="llm_admission", **kw)


def admission_budget() -> dict | None:
    """{'usd_month': n, 'price_ceiling_per_mtok': {'in': x, 'out': y}} или None.
    None — бюджета нет, допуск не тратит ни цента (деньги — решение владельца)."""
    b = _cfg("llm.admission.budget")
    if not isinstance(b, dict) or not b.get("usd_month"):
        return None
    return b


def _month_key(now: datetime) -> str:
    return f"llm.admission.spend.{now:%Y-%m}"


def month_spent(now: datetime) -> float:
    return float(_cfg(_month_key(now)) or 0.0)


class BudgetExhausted(RuntimeError):
    """Следующий вызов не помещается в месячный бюджет — прогон останавливается."""


class Ledger:
    """Учёт ДО вызова: резерв = вход (оценка) × тариф + max_tokens × тариф. Модель с
    рассуждением может вывести весь лимит — поэтому резервируется лимит, а не ожидание."""

    def __init__(self, b: dict, now: datetime):
        self.cap = float(b["usd_month"])
        pc = b["price_ceiling_per_mtok"]          # нет потолка тарифа — нет и бюджета (KeyError)
        self.p_in, self.p_out = float(pc["in"]), float(pc["out"])
        self.now = now
        self.used = month_spent(now)

    def reserve(self, est_in: int, max_out: int) -> float:
        cost = (est_in * self.p_in + max_out * self.p_out) / 1e6
        if self.used + cost > self.cap:
            raise BudgetExhausted(f"нужно до {cost:.2f} $, осталось {self.cap - self.used:.2f} $")
        return cost

    def charge(self, tin: int, tout: int) -> float:
        cost = (tin * self.p_in + tout * self.p_out) / 1e6
        self.used += cost
        _put(_month_key(self.now), value_num=round(self.used, 4))
        return cost


# ── корпус ───────────────────────────────────────────────────────────────────
def load_admission_corpus() -> dict:
    return json.loads((CORPUS_DIR / "corpus.json").read_text(encoding="utf-8"))


def owner_real_pages() -> list[dict]:
    """Настоящие страницы владельца: картинка из каталога данных + эталон из базы
    (promoted-строки staging, без дублей перечитываний). Нет каталога — пусто."""
    import os
    base = Path(os.environ.get("HEALTH_DATA_DIR") or Path.home() / "health") / "data" / "llm_corpus" / "real"
    man = base / "manifest.json"
    if not man.exists():
        return []
    import health_db as db
    out = []
    for it in json.loads(man.read_text(encoding="utf-8")):
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT canonical_name, value, value_op FROM lab_results_staging "
                "WHERE review_status='promoted' AND source_file LIKE ? AND page=?",
                (it["source_like"], it["page"])).fetchall()
        seen, gold = set(), []
        for r in rows:
            k = (r["canonical_name"], r["value"], r["value_op"])
            if r["canonical_name"] and r["value"] is not None and k not in seen:
                seen.add(k)
                gold.append({"canonical_name": k[0], "value": k[1], "value_op": k[2]})
        img = base / it["image"]
        if gold and img.exists():
            out.append({"image_bytes": img.read_bytes(), "gold": gold, "key": it["image"], "real": True})
    return out


# ── разбор ответов (точный эталон) ───────────────────────────────────────────
def _json(text: str):
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip(), flags=re.S).strip()
    for cand in (raw, raw[raw.find("{"):raw.rfind("}") + 1], raw[raw.find("["):raw.rfind("]") + 1]):
        try:
            return json.loads(cand)
        except Exception:
            continue
    return None


def _eq(a, b) -> bool:
    if b is None:
        return a in (None, "", "null")
    try:
        return abs(float(a) - float(b)) < 1e-9
    except (TypeError, ValueError):
        return False


def judge_lab(text: str, gold: list[dict], complete: bool = False) -> list[str]:
    """Ошибки чтения бланка: пропуск аналита или неточное значение/оператор.
    complete=True — эталон полный по построению (синтетика): лишняя строка тоже ошибка
    (замер 01.10: gpt-5.6-sol вернул каждую строку жёсткого бланка дважды — 32 вместо 16).
    У настоящих страниц эталон — promoted-строки, владелец мог часть отклонить: лишнее не судим."""
    obj = _json(text)
    if not isinstance(obj, dict):
        return ["ответ не JSON"]
    pool: dict = {}
    for r in obj.get("tests") or []:
        pool.setdefault(r.get("canonical_name"), []).append(r)
    errs = []
    for g in gold:
        cands = pool.get(g["canonical_name"]) or []
        hit = next((c for c in cands if _eq(c.get("value"), g["value"])
                    and (c.get("value_op") or None) == (g.get("value_op") or None)), None)
        if hit:
            cands.remove(hit)
        elif cands:
            c = cands.pop(0)
            errs.append(f"{g['canonical_name']}: {c.get('value_op') or ''}{c.get('value')} вместо "
                        f"{g.get('value_op') or ''}{g['value']}")
        else:
            errs.append(f"{g['canonical_name']}: пропущен")
    if complete:
        errs += [f"{name}: лишняя строка {c.get('value_op') or ''}{c.get('value')}"
                 for name, rest in pool.items() for c in rest]
    return errs


_AGENT_RU = {"доксорубицин": "doxorubicin", "циклофосфамид": "cyclophosphamide",
             "карбоплатин": "carboplatin", "паклитаксел": "paclitaxel",
             "капецитабин": "capecitabine", "пембролизумаб": "pembrolizumab",
             "метформин": "metformin", "аторвастатин": "atorvastatin"}


def judge_treatment(text: str, spec: dict) -> list[str]:
    m = re.search(r"\[.*\]", re.sub(r"^```[a-z]*\n?|\n?```$", "", (text or "").strip()), re.S)
    try:
        data = json.loads(m.group(0)) if m else None
    except Exception:
        data = None
    if not isinstance(data, list):
        return ["ответ не JSON-массив"]
    regs = [{**r, "agents": {_AGENT_RU.get(a.strip().lower(), a.strip().lower()) for a in (r.get("agents") or [])}}
            for r in data if isinstance(r, dict)]
    errs, used = [], set()
    for e in spec["expected"]:
        key = set(e["agents"])
        i = next((n for n, r in enumerate(regs) if n not in used and key <= r["agents"]), None)
        if i is None:
            errs.append(f"{'+'.join(sorted(key))}: пропущен")
            continue
        used.add(i)
        r = regs[i]
        cyc = r.get("cycles_completed")
        if not ((cyc is None and e["cycles"] is None) or (cyc is not None and e["cycles"] is not None and _eq(cyc, e["cycles"]))):
            errs.append(f"{'+'.join(sorted(key))}: циклов {cyc} вместо {e['cycles']}")
        if r.get("status") != e["status"]:
            errs.append(f"{'+'.join(sorted(key))}: статус {r.get('status')} вместо {e['status']}")
    bad = sorted({a for r in regs for a in r["agents"] & set(spec["forbidden_agents"])})
    if bad:
        errs.append("не противоопухолевые в режимах: " + ", ".join(bad))
    return errs


def _check_one(obj, chk: dict) -> bool:
    if "all" in chk:
        return all(_check_one(obj, c) for c in chk["all"])
    v = obj.get(chk["field"]) if isinstance(obj, dict) else None
    if "set" in chk:
        return isinstance(v, list) and {str(x).strip() for x in v} == set(chk["set"])
    want = chk["equals"]
    return _eq(v, want) if isinstance(want, (int, float)) else (v == want)


def judge_text(text: str, item: dict) -> list[str]:
    obj = _json(text)
    if obj is None:
        return [f"{item['id']}: ответ не JSON"]
    return [] if _check_one(obj, item["check"]) else [f"{item['id']}: {json.dumps(obj, ensure_ascii=False)[:120]}"]


# ── прогон ───────────────────────────────────────────────────────────────────
def _call(client, model: str, *, prompt: str, image: bytes | None, system: str | None,
          max_tokens: int, temperature: float | None, ledger: Ledger, reserve_extra: int = 0) -> tuple[str, str]:
    est_in = len(prompt) // 2 + len(system or "") // 2 + (1700 if image else 0)
    ledger.reserve(est_in, max_tokens + reserve_extra)   # чужой провайдер: + запас на рассуждение
    content = []
    if image:
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                        "data": base64.standard_b64encode(image).decode()}})
    content.append({"type": "text", "text": prompt})
    kw = dict(model=model, max_tokens=max_tokens, messages=[{"role": "user", "content": content}])
    if system:
        kw["system"] = system
    if temperature is not None:
        kw["temperature"] = temperature
    r = client.messages.create(**kw)
    ledger.charge(r.usage.input_tokens, r.usage.output_tokens)
    text = "".join(getattr(b, "text", "") for b in r.content if getattr(b, "type", "") == "text")
    return text, str(getattr(r, "model", model))


def run_suite(client, model: str, suite: str, corpus: dict, ledger: Ledger, provider: str) -> dict:
    """{'items': n, 'errors': [...], 'model_ids': {...}} одного набора для модели.
    Правило прохода — ноль ошибок, поэтому первая ошибка останавливает набор: дальнейшие
    вызовы вердикт не меняют, только тратят бюджет владельца ('stopped_early': True)."""
    errors, ids, n = [], set(), 0
    REPS, MAX_TOKENS = corpus["policy"]["reps"], corpus["policy"]["max_tokens"]
    extra = 0
    if provider != "anthropic":
        import llm_client
        extra = int(llm_client.profiles()[provider].get("reasoning_reserve_tokens", 0))
    jobs = []      # (prompt, image, system, temperature, judge)
    if suite.startswith("lab_vision"):
        import lab_recognizer as lr
        p1, p2 = lr.recognition_prompts()
        prompt = p1 if suite.endswith("p1") else p2
        pages = [{"image_bytes": (CORPUS_DIR / it["image"]).read_bytes(), "gold": it["gold"], "key": it["image"]}
                 for it in corpus["lab_vision"]]
        if provider == "anthropic":   # настоящие бланки — только тому, кому они и так уходят
            pages += owner_real_pages()
        for p in pages:
            jobs += [(prompt, p["image_bytes"], None, None,
                      lambda t, p=p: [f"{p['key']}: {e}" for e in judge_lab(t, p["gold"], complete=not p.get("real"))])
                     ] * REPS[suite]
    elif suite == "treatment":
        import treatment_extractor as te
        spec = corpus["treatment"]
        jobs += [(f"ДОКУМЕНТ:\n{spec['text']}", None, te.EXTRACTION_PROMPT, 0,
                  lambda t: judge_treatment(t, spec))] * REPS[suite]
    elif suite == "text":
        from epistemic_skill import loader
        system = loader.load_skill().text
        for it in corpus["text"]:
            jobs += [(it["prompt"], None, system, None, lambda t, it=it: judge_text(t, it))] * REPS[suite]
    else:
        raise KeyError(suite)
    for prompt, image, system, temp, judge in jobs:
        text, mid = _call(client, model, prompt=prompt, image=image, system=system,
                          max_tokens=MAX_TOKENS[suite], temperature=temp, ledger=ledger, reserve_extra=extra)
        n += 1
        ids.add(mid)
        errors += judge(text)
        if errors:
            break
    return {"items": n, "errors": errors, "model_ids": sorted(ids), "stopped_early": bool(errors) and n < len(jobs)}


def admission_candidates(listed: list[dict], chains: dict[str, list[str]], verdicts: dict,
               family_roles: dict[str, list[str]]) -> list[tuple[str, str]]:
    """[(model, role)] — новее основной модели семейства, не в цепочке, без вердикта по роли.
    Порядок — новейшие первыми: у них дольше жизнь, значит больше пользы от допуска.
    listed: [{'id', 'created_at'}] из списка провайдера."""
    created = {m["id"]: m.get("created_at") or "" for m in listed}
    out = []
    for m in sorted(listed, key=lambda x: x.get("created_at") or "", reverse=True):
        mid = m["id"]
        fam = next((f for f in family_roles if mid.startswith(f)), None)
        if not fam:
            continue
        for role in family_roles[fam]:
            chain = chains.get(role) or []
            primary_created = created.get(chain[0], "") if chain else ""
            if mid in chain or (verdicts.get(mid) or {}).get("roles", {}).get(role):
                continue
            if primary_created and (m.get("created_at") or "") <= primary_created:
                continue
            out.append((mid, role))
    return out


def admit_to_chain(role: str, model: str) -> list[str]:
    """Дописать модель в КОНЕЦ цепочки роли; цепочки opus/sonnet не пересекаются."""
    import hai_core
    chain = hai_core.model_chain(role)
    if model in chain:
        return chain
    other = {"opus": "sonnet", "sonnet": "opus"}.get(role)
    if other and model in hai_core.model_chain(other):
        raise ValueError(f"{model} уже в цепочке {other}: два прохода распознавателя сошлись бы")
    chain = chain + [model]
    _put(f"model.{role}", value_json=chain)
    return chain


def _judge_role(client, model: str, role: str, corpus: dict, ledger: Ledger, provider: str,
                cache: dict) -> dict:
    """Наборы роли по порядку; первый провал останавливает роль. Результат набора для
    модели переиспользуется ролями с тем же набором (haiku и haiku_pinned делят text)."""
    suites = {}
    for s in corpus["policy"]["role_suites"][role]:
        if (model, s) not in cache:
            cache[(model, s)] = run_suite(client, model, s, corpus, ledger, provider)
        suites[s] = cache[(model, s)]
        if suites[s]["errors"]:
            break
    return suites


def _role_passed(suites: dict, required: list[str]) -> bool:
    return set(suites) == set(required) and all(not s["errors"] for s in suites.values())


def run_admission(force: bool = False, dry_run: bool = False, notify: bool = False, client=None,
        provider: str = "anthropic", now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)  # time-inject: ok
    last = _cfg("llm.admission.last_run")
    if not force and last:
        try:
            if now - datetime.fromisoformat(last) < _WEEK:
                return {"skipped": "неделя ещё не прошла", "last_run": last}
        except ValueError:
            pass
    b = admission_budget()
    if b is None:
        return {"skipped": "бюджет допуска не задан (llm.admission.budget) — без денег владельца не прогоняю"}
    import hai_core
    client = client or hai_core.get_client()
    listed = [{"id": m.id, "created_at": str(getattr(m, "created_at", ""))} for m in client.models.list(limit=100)]
    corpus = load_admission_corpus()
    pol = corpus["policy"]
    chains = {r: hai_core.model_chain(r) for r in pol["role_suites"]}
    verdicts = {}
    for m in listed:
        v = _cfg(f"llm.admission.{m['id']}")
        if isinstance(v, dict):
            verdicts[m["id"]] = v
    todo = admission_candidates(listed, chains, verdicts, pol["family_roles"])
    report = {"candidates": todo, "results": [], "stopped": None}
    if dry_run:
        return report
    ledger, cache = Ledger(b, now), {}
    for model, role in todo:
        try:
            suites = _judge_role(client, model, role, corpus, ledger, provider, cache)
        except BudgetExhausted as e:
            report["stopped"] = str(e)
            break
        passed = _role_passed(suites, pol["role_suites"][role])
        v = verdicts.get(model) or {"model": model, "roles": {}}
        v["roles"][role] = {"passed": passed, "date": f"{now:%Y-%m-%d}", "corpus": corpus["version"],
                            "suites": {k: {"items": s["items"], "errors": s["errors"][:20],
                                           "model_ids": s["model_ids"]} for k, s in suites.items()}}
        verdicts[model] = v
        _put(f"llm.admission.{model}", value_json=v)
        if passed:
            admit_to_chain(role, model)
        report["results"].append({"model": model, "role": role, "passed": passed,
                                  "errors": sum(len(s["errors"]) for s in suites.values())})
    _put("llm.admission.last_run", value_text=now.isoformat(timespec="seconds"))
    report["spent_month_usd"] = round(ledger.used, 2)
    if notify and report["results"]:
        _announce(report, ledger)
    return report


_TABLE_FILE = Path(__file__).parent / "methodology" / "llm_admission_table.json"


def run_provider_admission(provider: str, force: bool = False, dry_run: bool = False, client=None,
                           now: datetime | None = None, table_file: Path | None = None) -> dict:
    """Допуск моделей ЧУЖОГО провайдера к ролям — для таблицы выпуска (человек из GitHub с
    ключом OpenAI/Gemini). Кандидаты — role_defaults профиля; корпус — только синтетика
    (run_suite не даёт настоящих бланков не-anthropic); вызовы — через тот же переводчик и
    гард, что прод установки этого провайдера. Деньги — тот же месячный бюджет допуска.
    Вердикт пишется в таблицу (methodology/llm_admission_table.json) — её читает
    hai_core.get_model на установке провайдера; цепочки владельца не трогаются."""
    if provider == "anthropic":
        raise ValueError("anthropic допускается run_admission по цепочкам владельца")
    now = now or datetime.now(timezone.utc)  # time-inject: ok
    b = admission_budget()
    if b is None:
        return {"skipped": "бюджет допуска не задан (llm.admission.budget) — без денег владельца не прогоняю"}
    import llm_client
    prof = llm_client.profiles()[provider]
    corpus = load_admission_corpus()
    pol = corpus["policy"]
    table_file = table_file or _TABLE_FILE
    table = json.loads(table_file.read_text(encoding="utf-8"))
    judged = table.get(provider) or {}
    todo = [(m, r) for r, m in prof["role_defaults"].items()
            if r in pol["role_suites"] and (force or m not in (judged.get(r) or {}))]
    report = {"provider": provider, "candidates": todo, "results": [], "stopped": None}
    if dry_run:
        return report
    client = client or llm_client.guarded_client(prov=provider)
    ledger, cache = Ledger(b, now), {}
    for model, role in todo:
        try:
            suites = _judge_role(client, model, role, corpus, ledger, provider, cache)
        except BudgetExhausted as e:
            report["stopped"] = str(e)
            break
        passed = _role_passed(suites, pol["role_suites"][role])
        table.setdefault(provider, {}).setdefault(role, {})[model] = {
            "passed": passed, "date": f"{now:%Y-%m-%d}", "corpus": corpus["version"],
            "model_ids": sorted({i for s in suites.values() for i in s["model_ids"]}),
            "suites": {k: {"items": s["items"], "errors": s["errors"][:5]} for k, s in suites.items()}}
        table_file.write_text(json.dumps(table, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        report["results"].append({"model": model, "role": role, "passed": passed,
                                  "errors": sum(len(s["errors"]) for s in suites.values())})
    report["spent_month_usd"] = round(ledger.used, 2)
    return report


_ROLE_DOC = {"opus": ("анализы по фото (проход 1), консилиум", "lab photos (pass 1), consilium"),
             "sonnet": ("анализы по фото (проход 2), чат", "lab photos (pass 2), chat"),
             "haiku": ("чекин, короткие ответы", "check-in, short answers"),
             "haiku_pinned": ("лечение и анализы из текста", "treatment and labs from text")}


def admission_reference_md(table: dict, lang: str = "ru") -> str:
    """Справочник «что работает на каком провайдере» — ТОЛЬКО из таблицы допуска; руками не
    пишется (docs/reference/llm_providers.md, сверку держит тест)."""
    import doc_translation as dt
    import llm_client
    ru = lang == "ru"
    i = 0 if ru else 1
    out = [dt.switch_line(lang, "llm_providers.md", "llm_providers.en.md"), "",
           "<!-- generated: python3 llm_admission.py --reference; не править руками / do not edit -->", ""]
    out += ["# Что работает на каком поставщике моделей" if ru else "# What works on which model provider", ""]
    out += [("Anthropic — путь урока: все роли, допуск преемников — по цепочкам владельца. Для остальных "
             "работает только роль, чья модель прошла допуск на синтетическом корпусе "
             "(`methodology/llm_corpus`); не прошла — функции роли отказывают, а не отвечают "
             "непроверенной моделью.") if ru else
            ("Anthropic is the tutorial path: every role works. For other providers only a role whose model "
             "passed admission on the synthetic corpus (`methodology/llm_corpus`) works; otherwise that "
             "role's functions refuse rather than answer with an unchecked model."), ""]
    for prov, prof in llm_client.profiles().items():
        if prov == "anthropic":
            continue
        out += [f"## {prov}", "", "| " + (" | ".join(["Роль", "Что делает", "Модель", "Допуск", "Дата", "Почему"] if ru
                                               else ["Role", "What it does", "Model", "Admitted", "Date", "Why"])) + " |",
                "|---|---|---|---|---|---|"]
        for role, model in prof["role_defaults"].items():
            v = ((table.get(prov) or {}).get(role) or {}).get(model)
            if v is None:
                verdict, date, why = ("не проверялась", "—", "—") if ru else ("not checked", "—", "—")
            else:
                verdict = ("да" if ru else "yes") if v["passed"] else ("нет" if ru else "no")
                date = v["date"]
                errs = [e for s in v["suites"].values() for e in s["errors"]]
                why = "; ".join(errs[:2]).replace("|", "/") or "—"
            out.append(f"| {role} | {_ROLE_DOC[role][i]} | `{model}` | {verdict} | {date} | {why} |")
        out.append("")
    return "\n".join(out)


_ROLE_RU = {"opus": "анализы по фото и консилиум", "sonnet": "второе чтение анализов и чат",
            "haiku": "чекин и короткие ответы", "haiku_pinned": "разбор лечения и анализов из текста"}


def _announce(report: dict, ledger: Ledger) -> None:
    import i18n
    import notify
    ok = [f"{r['model']} ({_ROLE_RU[r['role']]})" for r in report["results"] if r["passed"]]
    bad = [f"{r['model']} ({_ROLE_RU[r['role']]})" for r in report["results"] if not r["passed"]]
    notify.notify_operator(i18n.t("owner.card.models_admitted",
                                  passed=", ".join(ok) or "—", failed=", ".join(bad) or "—",
                                  spent=f"{ledger.used:.2f}", cap=f"{ledger.cap:.0f}"))


def set_admission_budget(usd_month: float, price_in: float, price_out: float) -> None:
    """Бюджет — решение владельца (01.10: 10 $ в месяц). Потолок тарифа — пессимистичная
    оценка $/М токенов, ВЫШЕ тарифа любой допускаемой модели: учёт по нему гарантирует,
    что настоящие траты не превысят бюджет."""
    _put("llm.admission.budget", value_json={"usd_month": usd_month,
                                             "price_ceiling_per_mtok": {"in": price_in, "out": price_out}})


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser()
    ap.add_argument("--weekly", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--notify", action="store_true")
    ap.add_argument("--set-budget", type=float, metavar="USD_MONTH")
    ap.add_argument("--reference", action="store_true", help="пересобрать docs/reference/llm_providers(.en).md из таблицы")
    ap.add_argument("--provider", help="чужой провайдер: допуск role_defaults профиля в таблицу выпуска")
    ap.add_argument("--price-ceiling", type=float, nargs=2, metavar=("IN", "OUT"),
                    help="с --set-budget: пессимистичный тариф $/М токенов вход/выход")
    a = ap.parse_args()
    if a.set_budget is not None:
        if not a.price_ceiling:
            ap.error("--set-budget требует --price-ceiling IN OUT")
        set_admission_budget(a.set_budget, *a.price_ceiling)
        print("бюджет допуска:", admission_budget())
        sys.exit(0)
    if a.reference:
        import doc_translation as dt
        t = json.loads(_TABLE_FILE.read_text(encoding="utf-8"))
        ref = Path(__file__).parent / "docs" / "reference"
        ru_md = admission_reference_md(t, "ru")
        (ref / "llm_providers.md").write_text(ru_md, encoding="utf-8")
        mark = f"<!-- translation-of: docs/reference/llm_providers.md sha256:{dt.text_hash(ru_md)} -->\n"
        (ref / "llm_providers.en.md").write_text(mark + admission_reference_md(t, "en"), encoding="utf-8")
        sys.exit(0)
    if a.provider:
        rep = run_provider_admission(a.provider, force=a.force, dry_run=a.dry_run)
    else:
        rep = run_admission(force=a.force, dry_run=a.dry_run, notify=a.notify)
    print(json.dumps(rep, ensure_ascii=False, indent=1, default=str))
