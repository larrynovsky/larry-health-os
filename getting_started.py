"""getting_started.py — главная дашборда «С чего начать»: состояние каждой возможности из данных.

Каталог (что умеет система, что ей дать, тексты, ссылки на реестр замысла) — единственный дом
methodology/getting_started.yaml. Здесь — только судьи: состояние каждой возможности считается из
базы и файлов тенанта В МОМЕНТ ВЫЗОВА, а не хранится флагом (§18: флаг не имеет гасителя и врёт
в день, когда источник замолчал).

Пять состояний: not (ждёт шага человека) · wait (подключено, данных ещё нет) · acc (накапливает до
порога) · ok · broken (данные были и перестали приходить). Поверх — `pending`: работает, но ждёт
решения человека (предложения медкарты, бланки на проверке).

Пороги берутся из их домов, своих чисел модуль не заводит: BAND_MIN_OBS (brief_gate — с какого
числа дней появляется личная полоса), SOURCE_STALE_DAYS (metrics_db — свежесть приборов, conit C1),
MIN_OVERLAP_DAYS (signal_family — общих дней пары для суда связей), горизонт конституций
(system_config), CACHE_ERROR_AGE_H (google_calendar_fetcher).

Секреты: только факт наличия файла (`exists`), содержимое не читается никогда (§19).
Публичный вход — `board()`.
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path
from typing import Callable

import yaml

from _time_inject import get_today

CATALOG_PATH = Path(__file__).resolve().parent / "methodology" / "getting_started.yaml"

# Порядок, в котором выбирается «следующий шаг», — по группам каталога: сначала сломанное,
# потом ждущее решения, потом первый неначатый шаг в порядке каталога.


def load_catalog(path: Path = CATALOG_PATH) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


# ── доступ к данным (подменяется в тесте) ─────────────────────────────────────

class _Ctx:
    """Что судьям нужно знать о мире: запросы к базе тенанта, наличие секрета, каталог данных."""

    def __init__(self, conn: sqlite3.Connection, secrets: Path, data_dir: Path, today: date):
        self.conn, self.secrets, self.data_dir, self.today = conn, secrets, data_dir, today

    def has_table(self, name: str) -> bool:
        return self.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                                 (name,)).fetchone() is not None

    def one(self, sql: str, params: tuple = ()):
        row = self.conn.execute(sql, params).fetchone()
        return row[0] if row else None

    def secret(self, *names: str) -> bool:
        return any((self.secrets / n).exists() for n in names)

    def columns(self, table: str) -> set[str]:
        return {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")}


def _r(state: str, key: str, receipt: str | None = None, progress: float | None = None,
       pending: int = 0, **params) -> dict:
    return {"state": state, "text": key, "params": params, "receipt": receipt,
            "progress": progress, "pending": pending}


def _count(ctx: _Ctx, table: str, where: str = "1=1", params: tuple = ()) -> int:
    if not ctx.has_table(table):
        return 0
    return int(ctx.one(f"SELECT COUNT(*) FROM {table} WHERE {where}", params) or 0)


def _device_days(ctx: _Ctx, source: str) -> list[str]:
    """Дни, в которые пришла хоть одна колонка-подпись источника (metrics_db.SOURCE_SIGNATURE:
    тот же дом провенанса, по которому специалисты решают, какие источники есть)."""
    from metrics_db import SOURCE_SIGNATURE
    if not ctx.has_table("daily_metrics"):
        return []
    cols = [c for c in SOURCE_SIGNATURE[source] if c in ctx.columns("daily_metrics")]
    if not cols:
        return []
    cond = " OR ".join(f"{c} IS NOT NULL" for c in cols)
    return [r[0] for r in ctx.conn.execute(
        f"SELECT DISTINCT date FROM daily_metrics WHERE {cond} ORDER BY date")]


def _any_device_days(ctx: _Ctx) -> int:
    return len(set(_device_days(ctx, "Oura")) | set(_device_days(ctx, "Apple Health")))


# ── судьи ─────────────────────────────────────────────────────────────────────

def _about(ctx):
    if not ctx.has_table("assessment_sessions"):
        return _r("not", "about.none")
    done = ctx.one("SELECT MAX(completed_at) FROM assessment_sessions WHERE instrument_id='onboarding' "
                   "AND status='completed'")
    if done:
        return _r("ok", "about.done", receipt=str(done)[:10])
    if _count(ctx, "assessment_sessions", "instrument_id='onboarding'"):
        return _r("not", "about.started")
    return _r("not", "about.none")


def _record(ctx):
    probs = _count(ctx, "problem_list", "status LIKE 'active%' OR status LIKE 'watch%'")
    events = _count(ctx, "events")
    pending = _count(ctx, "problem_list_proposals", "status='pending'")
    if pending:
        return _r("ok", "record.pending", pending=pending, n=pending)
    if probs or events:
        return _r("ok", "record.ok", n=probs)
    return _r("not", "record.none")


def _labs(ctx):
    # Число значений, а не названий: имя анализа здесь не нужно, и читатель по имени мимо
    # отображения LOINC был бы новым домом имени (ратчет test_lab_trend_readers_ratchet).
    n = _count(ctx, "lab_results")
    from labs_db import WAITING_REVIEW_SQL
    from urllib.parse import quote
    pending = _count(ctx, "lab_results_staging", WAITING_REVIEW_SQL)
    last = ctx.one("SELECT MAX(date) FROM lab_results") if n else None
    if pending:
        run_id = ctx.one("SELECT run_id FROM lab_results_staging WHERE " + WAITING_REVIEW_SQL +
                         " GROUP BY run_id ORDER BY MIN(created_at), MIN(id) LIMIT 1")
        return _r("ok" if n else "wait", "labs.pending", receipt=last, pending=pending, n=pending) | {
            "pending_href": f"/lab-review/{quote(str(run_id), safe='')}?show=waiting"}
    if n:
        return _r("ok", "labs.ok", receipt=last, n=n)
    return _r("not", "labs.none")


def _genome(ctx):
    n = _count(ctx, "raw_snps")
    if n:
        return _r("ok", "genome.ok", n=n)
    return _r("not", "genome.none")


def _device(ctx, source: str, secret: str):
    """Общий судья прибора: not → wait → acc (до личной полосы) → ok; broken — если данные были,
    а последний день старше порога свежести."""
    from brief_gate import BAND_MIN_OBS
    from metrics_db import SOURCE_STALE_DAYS
    days = _device_days(ctx, source)
    if not days:
        connected = ctx.secret(secret)
        if source == "Oura":
            connected = connected or ctx.secret("oura_oauth.json") or (ctx.data_dir / "oura_oauth.json").exists()
        return _r("wait", "device.wait") if connected else _r("not", "device.none")
    last = date.fromisoformat(str(days[-1])[:10])
    age = (ctx.today - last).days
    if age > SOURCE_STALE_DAYS:
        return _r("broken", "device.broken", receipt=str(last), age=age)
    if len(days) < BAND_MIN_OBS:
        return _r("acc", "device.acc", receipt=str(last), progress=len(days) / BAND_MIN_OBS,
                  n=len(days), need=BAND_MIN_OBS)
    return _r("ok", "device.ok", receipt=str(last))


def _place(ctx):
    if ctx.has_table("system_config") and ctx.one(
            "SELECT 1 FROM system_config WHERE key='location.home_lat'"):
        return _r("ok", "place.ok")
    return _r("not", "place.none")


def _calendar(ctx):
    from google_calendar_fetcher import CACHE_ERROR_AGE_H
    cache = ctx.data_dir / "calendar_cache.json"
    if cache.exists():
        import time
        age_h = int((time.time() - cache.stat().st_mtime) / 3600)
        if age_h > CACHE_ERROR_AGE_H:
            return _r("broken", "calendar.broken", age=age_h)
        return _r("ok", "calendar.ok")
    if ctx.secret("google_calendar_token.json") or (ctx.data_dir / "google_calendar_token.json").exists():
        return _r("wait", "calendar.wait")
    return _r("not", "calendar.none")


def _brief(ctx):
    """След брифа — context_cards: гейт анти-повтора пишет туда карточки каждого брифа, и по
    этой же таблице ночной датчик check_morning_brief_gate_liveness судит, жив ли бриф.
    Ежедневный текст GP в agent_reports не сохраняется (там только gp_weekly/gp_monthly) —
    первая редакция судьи искала gp_daily и у владельца с ежедневным брифом показывала
    «ждёт первых данных» (замер на живой главной 02.10.2026)."""
    last = ctx.one("SELECT MAX(date) FROM context_cards") if ctx.has_table("context_cards") else None
    if last:
        return _r("ok", "brief.ok", receipt=str(last)[:10], date=str(last)[:10])
    return _r("wait", "brief.wait")


def _alerts(ctx):
    """Пороги трендов становятся личными, когда _derive_metric_percentile набирает min_n дней;
    до этого действуют стартовые пороги (health_db._personalize_trend_thresholds). Число берётся
    из сигнатуры той самой функции — второго дома у него нет."""
    import inspect
    import health_db
    need = inspect.signature(health_db._derive_metric_percentile).parameters["min_n"].default
    n = _any_device_days(ctx)
    if not n:
        return _r("wait", "alerts.wait", need=need)
    if n < need:
        return _r("acc", "alerts.acc", progress=n / need, n=n, need=need)
    return _r("ok", "alerts.ok", n=n, need=need)


def _links(ctx):
    from signal_family import MIN_OVERLAP_DAYS
    n, need = _any_device_days(ctx), MIN_OVERLAP_DAYS
    if not n:
        return _r("wait", "links.wait", need=need)
    if n < need:
        ready = ctx.today + timedelta(days=need - n)
        return _r("acc", "links.acc", progress=n / need, n=n, need=need, date=ready.isoformat())
    return _r("ok", "links.ok", need=need)


def _hyp(ctx):
    """Консилиум, рождающий гипотезы, — 1-го числа месяца (com.larry.health.consilium); экран
    называет дату ближайшего, а не «1-го числа» (холодное чтение: «сегодня 2-е — ждать месяц?»)."""
    n = _count(ctx, "memory", "category='hypothesis'")
    if n:
        return _r("ok", "hyp.ok", n=n)
    t = ctx.today
    nxt = date(t.year + (t.month == 12), t.month % 12 + 1, 1)
    # Консилиум ждёт календаря, а не подключения: это «накапливает», не «ждёт первых данных»
    # (холодное чтение, круг 3: оба читателя споткнулись о ◐ при идущих данных прибора).
    return _r("acc", "hyp.wait", date=nxt.isoformat())


def _consult(ctx):
    if ctx.secret("anthropic_key", "openai_key", "gemini_key", "deepseek_key"):
        return _r("ok", "consult.ok")
    return _r("not", "consult.none")


def _doctor(ctx):
    if _count(ctx, "problem_list") or _count(ctx, "events"):
        return _r("ok", "doctor.ok")
    return _r("wait", "doctor.wait")


def _questions(ctx):
    open_q = _count(ctx, "tasks", "type='question' AND status='open'")
    if open_q:
        return _r("ok", "questions.pending", pending=open_q, n=open_q)
    if _count(ctx, "tasks"):
        return _r("ok", "questions.ok")
    return _r("wait", "questions.wait")


def _horizon(ctx) -> int:
    """Горизонт конституций: system_config, иначе seed generate_constitutions. Читаем без записи —
    дашборд не сеет настройки (horizon_days() пишет seed при отсутствии ключа)."""
    from generate_constitutions import HORIZON_KEY, HORIZON_SEED_DAYS
    v = ctx.one("SELECT COALESCE(value_text, value_num) FROM system_config WHERE key=?", (HORIZON_KEY,)) \
        if ctx.has_table("system_config") else None
    return int(float(v)) if v not in (None, "") else HORIZON_SEED_DAYS


def _const(ctx):
    n, need = _count(ctx, "constitutions"), _horizon(ctx)
    if n:
        days = _any_device_days(ctx)
        if days < need:
            return _r("acc", "const.acc", progress=days / need, n=n, days=days, need=need)
        return _r("ok", "const.ok", n=n, need=need)
    if _count(ctx, "raw_snps"):
        return _r("wait", "const.wait", need=need)
    return _r("not", "const.none", need=need)


def _food(ctx):
    """Документ приходит, когда есть личное основание — тот же предикат, что у доставки
    (food_quarterly.has_personal_basis): без него доставка молчит намеренно."""
    import food_quarterly as fq
    if fq.has_personal_basis(conn=ctx.conn):
        return _r("ok", "food.ok")
    return _r("not", "food.none")


JUDGES: dict[str, Callable[[_Ctx], dict]] = {
    "about": _about, "record": _record, "labs": _labs, "genome": _genome,
    "oura": lambda c: _device(c, "Oura", "oura_token"),
    "apple": lambda c: _device(c, "Apple Health", "hae_ingest_token"),
    "place": _place, "calendar": _calendar, "brief": _brief, "alerts": _alerts,
    "links": _links, "hyp": _hyp, "consult": _consult, "doctor": _doctor,
    "questions": _questions, "const": _const, "food": _food,
}


# ── сборка доски ──────────────────────────────────────────────────────────────

def _render(cat: dict, cap: dict, res: dict, lang: str | None) -> dict:
    import i18n
    pick = lambda v: i18n.pick(v, lang)  # noqa: E731
    params = res["params"]
    st = cat["states"][res["state"]]
    howto = _doc_url(cap.get("howto"))
    primary = _primary(cap, res, howto, pick)
    secondary = None
    if howto and not (primary and primary["href"] == howto):
        secondary = {"text": pick(cap.get("howto_label") or cat["page"]["howto"]), "href": howto}
    return {
        "id": cap["id"], "group": cap["group"], "optional": bool(cap.get("optional")),
        "state": res["state"], "icon": st["icon"],
        "label": i18n.t("person.lab.waiting_label", lang) if cap["id"] == "labs" and res["pending"] else pick(st["label"]),
        "title": pick(cap["title"]), "benefit": pick(cap.get("benefit", "")),
        "sub": pick(cap.get("sub", "")),
        "need": pick(cap.get("need", "")).format(**params),
        "status": (i18n.t("person.lab.waiting_count", lang, **params) if res["text"] == "labs.pending"
                   else pick(cat["texts"][res["text"]]).format(**params)),
        "receipt": res["receipt"], "progress": res["progress"], "pending": res["pending"],
        "primary": primary, "secondary": secondary,
        "note": ({"kind": cap["note"]["kind"], "text": pick(cap["note"]["text"])} if cap.get("note") else None),
    }


def _primary(cap: dict, res: dict, howto: str | None, pick) -> dict | None:
    """Одно главное действие карточки. Правила (холодное чтение 02.10: кнопка «Отправить бланки
    боту» вела на очередь проверки, «Подробнее» календаря — на установку Докера):
    - сломано → broken_action, ведёт на инструкцию починки;
    - ждёт решения → pending_action, ведёт на страницу дашборда (pending_href) или инструкцию;
    - не начато → action; действие в боте (action_in_bot) — текст без ссылки, иначе инструкция;
    - работает/накапливает → «Открыть» раздел дашборда, если он есть.
    kind: warning | solid | ghost | text — вид в шаблоне."""
    state = res["state"]
    if state == "broken" and cap.get("broken_action"):
        return {"text": pick(cap["broken_action"]), "href": howto, "kind": "warning"}
    if res["pending"] and cap.get("pending_action"):
        return {"text": pick(cap["pending_action"]), "href": res.get("pending_href") or cap.get("pending_href") or howto, "kind": "solid"}
    if state == "not" and cap.get("action"):
        if cap.get("action_in_bot"):
            return {"text": pick(cap["action"]), "href": None, "kind": "text"}
        if howto:
            return {"text": pick(cap["action"]), "href": howto, "kind": "ghost" if cap.get("optional") else "solid"}
        return None
    if state in ("ok", "acc") and cap.get("open_href"):
        return {"text": pick(cap.get("open_label") or {"ru": "Открыть", "en": "Open"}), "href": cap["open_href"], "kind": "ghost"}
    return None


def _doc_url(rel: str | None) -> str | None:
    """Инструкция — в открытом репозитории: у человека из выпуска нет клона, а дашборд их не раздаёт.
    Имя репозитория — из release_notice.REPO (его дом)."""
    if not rel:
        return None
    from release_notice import REPO
    return f"https://github.com/{REPO}/blob/main/docs/{rel}"


def _next(caps: list[dict]) -> dict | None:
    """Один следующий шаг: сломанное → ждущее решения → первый неначатый необязательный-нет."""
    for pred in (lambda c: c["state"] == "broken",
                 lambda c: c["pending"],
                 lambda c: c["state"] == "not" and not c["optional"] and c["primary"]):
        for c in caps:
            if pred(c):
                return c
    return None


def board(conn: sqlite3.Connection | None = None, secrets: Path | None = None,
          data_dir: Path | None = None, today: date | None = None, lang: str | None = None) -> dict:
    """Доска возможностей текущего тенанта: {caps, groups, states, counts, next}.
    Без аргументов — база, ключи и каталог данных текущего процесса (read-only)."""
    import health_db
    if conn is None:
        conn = sqlite3.connect(f"file:{health_db.DB_PATH}?mode=ro", uri=True)
    if secrets is None:
        from secrets_paths import secrets_dir
        secrets = secrets_dir()
    if data_dir is None:
        data_dir = Path(health_db.DB_PATH).parent
    ctx = _Ctx(conn, Path(secrets), Path(data_dir), today or get_today())
    cat = load_catalog()
    caps = [_render(cat, cap, JUDGES[cap["id"]](ctx), lang) for cap in cat["capabilities"]]
    # Ждущее решения считается отдельно, а не в «работает»: читатель видит у такой карточки метку
    # «ждёт вас» и не находит её среди работающих (холодное чтение 02.10, круг 2: «5, а вижу 4»).
    counts = {s: sum(c["state"] == s and not c["pending"] and not (s == "not" and c["optional"])
                     for c in caps) for s in cat["states"]}
    counts["optional"] = sum(c["state"] == "not" and c["optional"] for c in caps)
    counts["pending"] = sum(bool(c["pending"]) for c in caps)
    import i18n
    page = {k: i18n.pick(v, lang) for k, v in cat["page"].items()}
    broken = [c for c in caps if c["state"] == "broken"]
    answer = (page["answer.broken"].format(title=broken[0]["title"]) if broken
              else page["answer.count"].format(ok=counts["ok"], total=len(caps)))
    # Что именно не работает — по имени, когда таких немного; иначе число не отвечает на вопрос
    # «что не так» (замечание владельца 02.10: «16 из 17», а какой — не видно).
    idle = [c for c in caps if c["state"] in ("not", "wait", "acc") and not c["optional"]]
    if not broken and 0 < len(idle) <= 3:
        answer += " " + page["answer.idle"].format(names=", ".join(c["title"] for c in idle))
    waiting = [c for c in caps if c["pending"]]
    if waiting:
        answer += " " + page["answer.pending"].format(names=", ".join(c["title"] for c in waiting))
    score = page["score"].format(**counts) + (page["score.broken"].format(**counts) if broken else "")
    by_id = {c["id"]: c for c in caps}
    done = [page["done.install"]]   # страница открыта — значит установка поднялась
    if by_id["consult"]["state"] == "ok":
        done.append(page["done.key"])
    if by_id["about"]["state"] == "ok":
        done.append(page["done.about"])
    return {
        "caps": caps, "page": page, "answer": answer, "score": score, "done": done,
        "groups": [{"id": g["id"], "title": i18n.pick(g["title"], lang)} for g in cat["groups"]],
        "states": {k: {"icon": v["icon"], "legend": i18n.pick(v["legend"], lang)} for k, v in cat["states"].items()},
        "counts": counts, "total": len(caps), "next": _next(caps),
    }
