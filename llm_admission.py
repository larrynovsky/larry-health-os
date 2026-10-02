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

    def charge(self, tin: int, tout: int, *, cache_read_input_tokens: int = 0,
               cache_creation_input_tokens: int = 0) -> float:
        # Cache — оплачиваемый вход; потолок входа консервативнее скидки на чтение.
        tin += (cache_read_input_tokens or 0) + (cache_creation_input_tokens or 0)
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
                "SELECT id, canonical_name, value, value_op, date FROM lab_results_staging "
                "WHERE review_status='promoted' AND source_file LIKE ? AND page=?",
                (it["source_like"], it["page"])).fetchall()
        gold = gold_from_promoted([dict(r) for r in rows])
        img = base / it["image"]
        if gold and img.exists():
            out.append({"image_bytes": img.read_bytes(), "gold": gold, "key": it["image"], "real": True})
    return out


def gold_from_promoted(rows: list[dict]) -> list[dict]:
    """Эталон страницы из promoted-строк: перечитывания одного показателя схлопываются в
    ПОЗДНЕЕ (больший id). Замер 02.10: один показатель мочи был promoted дважды — числом и тем же
    числом с оператором «<» (два перечитывания); эталон требовал двух строк, и модель, верно
    прочитавшая одну строку с «<», получала «пропущен»."""
    last: dict = {}
    for r in sorted(rows, key=lambda r: r["id"]):
        if r["canonical_name"] and r["value"] is not None:
            last[(r["canonical_name"], float(r["value"]), r.get("date"))] = r
    return [{"canonical_name": r["canonical_name"], "value": r["value"], "value_op": r["value_op"],
             **({"date": r["date"]} if r.get("date") else {})} for r in last.values()]


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


def judge_lab(text: str, gold: list[dict], complete: bool = False, page_date: str | None = None,
              history: dict | None = None) -> list[str]:
    """Ошибки чтения бланка: пропуск аналита, неточное значение/оператор, чужая дата.
    Дата судится так, как её поставит распознаватель в бою (lab_recognizer.recognize): у строки
    без даты — самая частая дата этой страницы в ответе, нет ни одной — дата документа (page_date).
    Ревью 02.10: «все текущие без даты + одна историческая с датой» в бою легли бы датой истории,
    а судья, верящий «распознаватель поставит дату страницы», пропускал это.
    Совпадение с эталоном ищется сначала с той же датой (одно значение в текущей и прошлой
    колонке — не ошибка), затем любое.
    complete=True — эталон полный (синтетика): лишняя строка допустима, ТОЛЬКО если это значение
    прошлой колонки (history: {date, gold}) с её датой и без выдуманного оператора.
    У настоящих страниц эталон — promoted-строки, владелец мог часть отклонить: лишнее не судим."""
    obj = _json(text)
    if not isinstance(obj, dict):
        return ["ответ не JSON"]
    rows = [r for r in obj.get("tests") or [] if isinstance(r, dict)]
    read = [str(r.get("date")).strip() for r in rows if str(r.get("date") or "").strip()]
    page_read = max(set(read), key=read.count) if read else None

    def eff(r):
        return str(r.get("date") or "").strip() or page_read or page_date

    pool: dict = {}
    for r in rows:
        pool.setdefault(r.get("canonical_name"), []).append(r)
    errs = []
    for g in gold:
        want = g.get("date") or page_date
        cands = pool.get(g["canonical_name"]) or []
        same = [c for c in cands if _eq(c.get("value"), g["value"])
                and (c.get("value_op") or None) == (g.get("value_op") or None)]
        hit = next((c for c in same if not want or eff(c) == str(want)), None) or (same[0] if same else None)
        if hit:
            cands.remove(hit)
            if want and eff(hit) and eff(hit) != str(want):
                errs.append(f"{g['canonical_name']}: дата {eff(hit)} вместо {want}")
        elif cands:
            c = cands.pop(0)
            errs.append(f"{g['canonical_name']}: {c.get('value_op') or ''}{c.get('value')} вместо "
                        f"{g.get('value_op') or ''}{g['value']}")
        else:
            errs.append(f"{g['canonical_name']}: пропущен")
    if complete:
        past = [dict(h) for h in ((history or {}).get("gold") or [])]
        for name, rest in pool.items():
            for c in rest:
                h = next((h for h in past if h["canonical_name"] == name and _eq(c.get("value"), h["value"])
                          and (c.get("value_op") or None) == (h.get("value_op") or None)), None)
                if h and history and eff(c) == str(history["date"]):
                    past.remove(h)
                    continue
                errs.append(f"{name}: лишняя строка {c.get('value_op') or ''}{c.get('value')} "
                            f"(дата {eff(c) or '—'})")
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
    ledger.charge(r.usage.input_tokens, r.usage.output_tokens,
                  cache_read_input_tokens=getattr(r.usage, "cache_read_input_tokens", 0),
                  cache_creation_input_tokens=getattr(r.usage, "cache_creation_input_tokens", 0))
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
        pages = [{"image_bytes": (CORPUS_DIR / it["image"]).read_bytes(), "gold": it["gold"], "key": it["image"],
                  "date": it.get("date"), "history": it.get("history")} for it in corpus["lab_vision"]]
        if provider == "anthropic":   # настоящие бланки — только тому, кому они и так уходят
            pages += owner_real_pages()
        for p in pages:
            jobs += [(prompt, p["image_bytes"], None, None,
                      lambda t, p=p: [f"{p['key']}: {e}" for e in judge_lab(
                          t, p["gold"], complete=not p.get("real"), page_date=p.get("date"),
                          history=p.get("history"))])
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


def _canonical_id(client, model: str) -> str:
    """Псевдоним → датированный id (claude-haiku-4-5 → claude-haiku-4-5-20251001, замер 02.10
    models.retrieve). Без этого снимок модели из цепочки шёл кандидатом сам за себя."""
    try:
        rid = str(client.models.retrieve(model).id)
    except Exception:
        return model
    # только раскрытие псевдонима в датированный снимок того же имени; иное — не доверяем
    return rid if rid == model or re.fullmatch(re.escape(model) + r"-20\d{6}", rid) else model


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
            snapshot_of_member = any(re.fullmatch(re.escape(c) + r"-20\d{6}", mid) for c in chain)
            if mid in chain or snapshot_of_member or (verdicts.get(mid) or {}).get("roles", {}).get(role):
                continue
            if primary_created and (m.get("created_at") or "") <= primary_created:
                continue
            out.append((mid, role))
    return out


def admit_to_chain(role: str, model: str) -> list[str]:
    """Дописать модель в КОНЕЦ цепочки роли; цепочки opus/sonnet не пересекаются."""
    import hai_core
    chain = hai_core.base_chain(role)
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
            key, fp = _suite_key(model, s, provider), _suite_fingerprint(s, corpus, provider)
            stored = _stored_suite(key, fp, ledger.now)
            if stored is None:
                stored = run_suite(client, model, s, corpus, ledger, provider)
                _put(key, value_json=dict(stored, corpus=corpus["version"], fingerprint=fp,
                                          date=f"{ledger.now:%Y-%m-%d}"))
            cache[(model, s)] = stored
        suites[s] = cache[(model, s)]
        if suites[s]["errors"]:
            break
    return suites


def _suite_key(model: str, suite: str, provider: str = "anthropic") -> str:
    """Ключ готового набора. Провайдер в ключе: результат anthropic несёт ошибки с бланков
    владельца и не должен попасть в допуск чужого провайдера с тем же id (ревью 02.10)."""
    return f"llm.admission.suite.{provider}.{model}.{suite}"


def _suite_fingerprint(suite: str, corpus: dict, provider: str) -> str:
    """Отпечаток входов набора: картинки, эталоны (включая настоящие страницы), промпты.
    Корпус той же версии с изменившимся эталоном владельца — другой отпечаток, кэш не годится."""
    import hashlib
    h = hashlib.sha256()
    h.update(f"{corpus['version']}|{suite}|{provider}".encode())
    h.update(json.dumps(corpus["policy"], ensure_ascii=False, sort_keys=True).encode())
    import llm_client
    h.update(json.dumps(llm_client.profiles().get(provider), ensure_ascii=False, sort_keys=True).encode())
    h.update(Path(__file__).read_bytes())   # судьи и прогон — в этом файле; правка кода = новый замер
    if suite.startswith("lab_vision"):
        import lab_recognizer as lr
        h.update("\x00".join(lr.recognition_prompts()).encode())
        pages = [dict(it, image_bytes=(CORPUS_DIR / it["image"]).read_bytes()) for it in corpus["lab_vision"]]
        if provider == "anthropic":
            pages += owner_real_pages()
        for p in pages:
            h.update(hashlib.sha256(p["image_bytes"]).digest())
            h.update(json.dumps({k: p.get(k) for k in ("gold", "date", "history")},
                                ensure_ascii=False, sort_keys=True, default=str).encode())
    elif suite == "treatment":
        import treatment_extractor as te
        h.update(json.dumps(corpus["treatment"], ensure_ascii=False, sort_keys=True).encode())
        h.update(te.EXTRACTION_PROMPT.encode())
    elif suite == "text":
        from epistemic_skill import loader
        h.update(json.dumps(corpus["text"], ensure_ascii=False, sort_keys=True).encode())
        h.update(loader.load_skill().text.encode())
    return h.hexdigest()


def _stored_suite(key: str, fingerprint: str, now: datetime) -> dict | None:
    """Готовый результат набора с теми же входами, не старше недели от ЗАМЕРА — прогон, оборванный
    деплоем (02.10: ~1 $ без вердикта), при перезапуске не платит за него снова."""
    v = _cfg(key)
    if not isinstance(v, dict) or v.get("fingerprint") != fingerprint:
        return None
    try:
        if now.date() - datetime.fromisoformat(v["date"]).date() > _WEEK:
            return None
    except (KeyError, ValueError):
        return None
    return {k: v[k] for k in ("items", "errors", "model_ids", "stopped_early") if k in v}


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
    chains = {r: [_canonical_id(client, m) for m in hai_core.model_chain(r)] for r in pol["role_suites"]}
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
        if ledger.cap - ledger.used < float(pol.get("min_usd_to_start_role", 0)):
            report["stopped"] = (f"на роль целиком не хватит: осталось {ledger.cap - ledger.used:.2f} $, "
                                 f"порог {pol['min_usd_to_start_role']} $ — прерванный прогон тратит без вердикта")
            break
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
        if ledger.cap - ledger.used < float(pol.get("min_usd_to_start_role", 0)):
            report["stopped"] = (f"на роль целиком не хватит: осталось {ledger.cap - ledger.used:.2f} $, "
                                 f"порог {pol['min_usd_to_start_role']} $ — прерванный прогон тратит без вердикта")
            break
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


def export_anthropic_verdicts(verdicts: dict) -> dict:
    """Раздел anthropic таблицы выпуска из вердиктов владельца: только passed/date/corpus.
    Ошибки НЕ переносятся — в них значения с бланков владельца («<показатель>: <прочитано> вместо <на бланке>»),
    а таблица уходит в публичный репозиторий. Отозванные вердикты (roles={}) не переносятся."""
    out: dict = {}
    for model in sorted(verdicts):
        for role, r in sorted(((verdicts[model] or {}).get("roles") or {}).items()):
            out.setdefault(role, {})[model] = {"passed": bool(r["passed"]), "date": r["date"],
                                               "corpus": r["corpus"]}
    return out


def owner_verdicts() -> dict:
    import health_db as db
    with db.get_conn() as conn:
        rows = conn.execute("SELECT key, value_json FROM system_config WHERE key LIKE 'llm.admission.claude-%'"
                            ).fetchall()
    return {r["key"].removeprefix("llm.admission."): json.loads(r["value_json"]) for r in rows if r["value_json"]}


def table_drift(table_file: Path | None = None) -> list[str]:
    """Чем раздел anthropic таблицы выпуска отстал от допуска владельца (пусто — не отстал)."""
    want = export_anthropic_verdicts(owner_verdicts())
    have = json.loads((table_file or _TABLE_FILE).read_text(encoding="utf-8")).get("anthropic") or {}
    out = []
    for role in sorted(set(want) | set(have)):
        for model in sorted(set(want.get(role, {})) | set(have.get(role, {}))):
            w, h = want.get(role, {}).get(model), have.get(role, {}).get(model)
            if (w or {}).get("passed") != (h or {}).get("passed"):
                out.append(f"{role}/{model}: в базе {w and w['passed']}, в таблице {h and h['passed']}")
    return out


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
    ap.add_argument("--export-anthropic", action="store_true",
                    help="раздел anthropic таблицы выпуска из вердиктов владельца (stdout, JSON)")
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
    if a.export_anthropic:
        print(json.dumps(export_anthropic_verdicts(owner_verdicts()), ensure_ascii=False, indent=1))
        sys.exit(0)
    if a.weekly:
        drift = table_drift()
        if drift:   # таблицу выпуска правит сессия на MacBook, контейнер её только читает
            import notify
            notify.fault("llm_admission: таблица выпуска отстала от допуска владельца — "
                         "llm_admission.py --export-anthropic → methodology/llm_admission_table.json: "
                         + "; ".join(drift), person_key=None)
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
