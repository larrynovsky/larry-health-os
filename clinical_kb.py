"""
clinical_kb.py — ЕДИНЫЙ реестр клинического знания (нить brief-neutralization, Фаза 2).

Обобщение паттерна food_floor на ВСЕ домены: версионируемый git-источник
`methodology/clinical_kb/` (индекс состояний + доменные срезы) → сид в per-tenant таблицы
`clinical_kb_conditions` + `clinical_kb` на init_db → рантайм читает таблицы.

Северная звезда: код брифа — нейтральный движок, не знает ни одной болезни. Имя болезни живёт
ТОЛЬКО в данных (_index.yaml, оракул — владелец). Движок матчит триггеры состояний против ДАННЫХ
тенанта (проблем-лист / ИМТ / геном) и применяет только совпавшие импликации + shared.

Давность (решение владельца): явный supersede + status + review per temporal_class. `active_entries`
отдаёт только status='active' и не-просроченные по review_date (durable не истекает).

СТАТУС: Фаза 2 Инкремент A — data-слой ставится и читается тестами; движок брифа ещё НЕ
переключён на него (переключение + снятие хардкода — следующий инкремент, после паритета).
"""
from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path

import yaml

import i18n

_log = logging.getLogger(__name__)
_KB_DIR = Path(__file__).resolve().parent / "methodology" / "clinical_kb"
_INDEX_YAML = _KB_DIR / "_index.yaml"

_COND_TABLE = "clinical_kb_conditions"
_KB_TABLE = "clinical_kb"


def _db():
    import health_db  # ленивый импорт — избегаем цикла health_db↔clinical_kb
    return health_db


# ── Источник (git-yaml) ──────────────────────────────────────────────────────
@lru_cache(maxsize=1)
def _load_conditions() -> tuple:
    """Состояния из _index.yaml. Кэш на процесс. tuple (хешируемо)."""
    data = yaml.safe_load(_INDEX_YAML.read_text(encoding="utf-8")) or {}
    out = []
    for c in data.get("conditions", []) or []:
        out.append({"id": c["id"],
                    "temporal_class": c.get("temporal_class", "standing"),
                    "description": c.get("description", ""),
                    "trigger": c.get("trigger", {}) or {}})
    return tuple(out)


@lru_cache(maxsize=1)
def _condition_attrs_raw() -> tuple:
    if not _INDEX_YAML.exists():
        return ()
    data = yaml.safe_load(_INDEX_YAML.read_text(encoding="utf-8")) or {}
    return tuple((c["id"], dict(c)) for c in data.get("conditions", []) or [])


def condition_attr(key: str) -> dict:
    """{condition_id: значение} необязательного поля состояния (lit_term, labs, …) из _index.yaml.
    Класс, заведённый под тенанта, приносит свои импликации сам: движок и его таблицы классов
    не знают болезней тенанта (решение владельца 2026-09-23 — публичный код не выдаёт диагноз).
    Нет свода — {}."""
    return {cid: c[key] for cid, c in _condition_attrs_raw() if key in c}


@lru_cache(maxsize=1)
def _load_domain_entries() -> tuple:
    """Все доменные срезы (*.yaml кроме _index). Возвращает tuple записей clinical_kb."""
    out = []
    for path in sorted(_KB_DIR.glob("*.yaml")):
        if path.name.startswith("_"):
            continue
        domain = path.stem  # 'food' ← food.yaml
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        # shared.benefits → записи kind=benefit, condition_key='shared'
        for b in ((data.get("shared", {}) or {}).get("benefits", []) or []):
            out.append({
                "id": f"{domain}_shared_benefit_{b['food']}",
                "domain": domain, "condition_key": "shared", "kind": "benefit",
                "payload": {"food": b["food"], "tag": b.get("tag"), "why": b.get("why", "")},
                "source": b.get("source", ""), "temporal_class": b.get("temporal_class", "standing"),
                "valid_from": b.get("valid_from"), "review_date": b.get("review_date")})
        # by_condition[cond] → список записей
        for cond, entries in (data.get("by_condition", {}) or {}).items():
            for e in entries or []:
                out.append({
                    "id": e["id"], "domain": domain, "condition_key": cond,
                    "kind": e.get("kind", "frame_rule"), "payload": e.get("payload", {}) or {},
                    "source": e.get("source", ""), "temporal_class": e.get("temporal_class", "standing"),
                    "valid_from": e.get("valid_from"), "review_date": e.get("review_date")})
    return tuple(out)


def source_hash() -> str:
    """Хеш источника (для сторожа устаревшей реплики). Стабилен по содержимому yaml."""
    import hashlib
    h = hashlib.sha256()
    for path in sorted(_KB_DIR.glob("*.yaml")):
        h.update(path.read_bytes())
    return h.hexdigest()


def _sig_lines_source() -> list[str]:
    """Детерминированная подпись СЕМАНТИКИ источника (для сторожа устаревшей реплики).
    Только seed-стабильные поля: conditions (id/temporal_class/trigger), entries (id/domain/
    condition_key/kind/temporal_class/source/payload). Исключены runtime-mutable (status/
    superseded_by/valid_from/review_date) и косметика (description/updated_at)."""
    out = []
    for c in sorted(_load_conditions(), key=lambda x: x["id"]):
        out.append("C|" + "|".join([c["id"], c["temporal_class"],
                    json.dumps(c["trigger"], sort_keys=True, ensure_ascii=False)]))
    for e in sorted(_load_domain_entries(), key=lambda x: x["id"]):
        out.append("E|" + "|".join([e["id"], e["domain"], e["condition_key"], e["kind"],
                    e["temporal_class"], e.get("source", ""),
                    json.dumps(e["payload"], sort_keys=True, ensure_ascii=False)]))
    return out


def source_content_hash() -> str:
    """sha256 семантики источника (что сид ДОЛЖЕН записать). Сравнивается с table_content_hash."""
    import hashlib
    return hashlib.sha256("\n".join(_sig_lines_source()).encode("utf-8")).hexdigest()


def table_content_hash(conn=None) -> str:
    """sha256 семантики per-tenant таблиц (что реально засеяно). Те же поля, что source_content_hash.
    Расхождение = устаревшая реплика (yaml сменился, init_db не ре-сижен на тенанте)."""
    import hashlib
    c = conn if conn is not None else _db().get_conn()
    out = []
    for r in c.execute(f"SELECT id, temporal_class, trigger_json FROM {_COND_TABLE} ORDER BY id").fetchall():
        rid = r[0] if not hasattr(r, "keys") else r["id"]
        tc = r[1] if not hasattr(r, "keys") else r["temporal_class"]
        tj = r[2] if not hasattr(r, "keys") else r["trigger_json"]
        out.append("C|" + "|".join([rid, tc,
                    json.dumps(json.loads(tj), sort_keys=True, ensure_ascii=False)]))
    for r in c.execute(f"SELECT id, domain, condition_key, kind, temporal_class, source, payload_json "
                       f"FROM {_KB_TABLE} ORDER BY id").fetchall():
        v = r if isinstance(r, (list, tuple)) else [r[k] for k in
             ("id", "domain", "condition_key", "kind", "temporal_class", "source", "payload_json")]
        out.append("E|" + "|".join([v[0], v[1], v[2], v[3], v[4], v[5] or "",
                    json.dumps(json.loads(v[6]), sort_keys=True, ensure_ascii=False)]))
    return hashlib.sha256("\n".join(out).encode("utf-8")).hexdigest()


# ── Сид (yaml → per-tenant таблицы), паттерн food_floor.seed_floor ────────────
def seed_clinical_kb(conn=None) -> int:
    """Сид источника в per-tenant таблицы. CREATE IF NOT EXISTS + INSERT OR REPLACE.
    Идемпотентно. Вызывать из init_db. Возвращает число записей clinical_kb."""
    c = conn or _db().get_conn()
    c.execute(f"""
        CREATE TABLE IF NOT EXISTS {_COND_TABLE} (
            id             TEXT PRIMARY KEY,
            temporal_class TEXT NOT NULL DEFAULT 'standing',
            description    TEXT NOT NULL DEFAULT '',
            trigger_json   TEXT NOT NULL DEFAULT '{{}}',
            updated_at     TEXT DEFAULT (datetime('now'))
        )
    """)
    c.execute(f"""
        CREATE TABLE IF NOT EXISTS {_KB_TABLE} (
            id             TEXT PRIMARY KEY,
            domain         TEXT NOT NULL,
            condition_key  TEXT NOT NULL DEFAULT '',
            kind           TEXT NOT NULL DEFAULT 'frame_rule',
            payload_json   TEXT NOT NULL,
            source         TEXT NOT NULL DEFAULT '',
            status         TEXT NOT NULL DEFAULT 'active',
            temporal_class TEXT NOT NULL DEFAULT 'standing',
            valid_from     TEXT,
            review_date    TEXT,
            superseded_by  TEXT,
            updated_at     TEXT DEFAULT (datetime('now'))
        )
    """)
    c.execute(f"CREATE INDEX IF NOT EXISTS idx_ckb_domain_cond ON {_KB_TABLE}(domain, condition_key, status)")
    for cond in _load_conditions():
        c.execute(
            f"INSERT OR REPLACE INTO {_COND_TABLE} (id, temporal_class, description, trigger_json, updated_at) "
            f"VALUES (?,?,?,?, datetime('now'))",
            (cond["id"], cond["temporal_class"], cond["description"],
             json.dumps(cond["trigger"], ensure_ascii=False)))
    entries = _load_domain_entries()
    for e in entries:
        # INSERT OR REPLACE сохраняет status/superseded_by по умолчанию 'active'/NULL при ре-сиде
        # источника. Явный supersede выполняется отдельным UPDATE (не через сид) — Фаза 3.
        c.execute(
            f"INSERT OR REPLACE INTO {_KB_TABLE} "
            f"(id, domain, condition_key, kind, payload_json, source, status, temporal_class, "
            f" valid_from, review_date, updated_at) "
            f"VALUES (?,?,?,?,?,?, 'active', ?,?,?, datetime('now'))",
            (e["id"], e["domain"], e["condition_key"], e["kind"],
             json.dumps(e["payload"], ensure_ascii=False), e["source"],
             e["temporal_class"], e.get("valid_from"), e.get("review_date")))
    try:
        c.commit()
    except Exception:  # silent-ok: conn без commit (уже в транзакции вызывающего)
        pass
    return len(entries)


# ── Матч состояний против ДАННЫХ тенанта (нейтральный движок) ─────────────────
def patient_med_text(conn, profile: dict | None = None) -> str:
    """ДОМ текста, по которому судят состояния тенанта: problem_list (title/description/notes)
    + medical-поля профиля, в нижнем регистре.

    Один дом на всех читателей смысла (сведено 2026-09-01): active_conditions,
    food_profile.medical_frame и критик генератора диет-правил зовут ЭТУ функцию — правило,
    которое критик пропустил, обязано срабатывать у применителя на том же тексте. До сведения
    построений было три, паритет держался тестом, а не конструкцией.

    ИСКЛЮЧЕНИЕ, не подлежащее сведению: food_floor._med_text — НЕЗАВИСИМЫЙ ре-скан пола
    безопасности (§17: второй источник обязан обходить преобразование, которое могло исказить
    первый). Пол не доверяет рамке; его дубль — конструкция, а не долг."""
    txt = ""
    try:
        rows = conn.execute("SELECT title, description, notes FROM problem_list").fetchall()
        txt = " ".join(" ".join(str(x or "") for x in r) for r in rows)
    except Exception:  # silent-ok: нет problem_list → скан только по профилю
        pass
    txt += " " + " ".join(str(v) for v in ((profile or {}).get("medical", {}) or {}).values())
    return txt.lower()


def _bmi(conn) -> float | None:
    try:
        prof = _db().get_profile_context() or {}
        ident = prof.get("identity", {}) or {}
        h = float(ident.get("height_cm")) / 100.0
        w = float(ident.get("weight_kg"))
        return round(w / (h * h), 1) if h > 0 else None
    except Exception:  # silent-ok: нет/битый рост-вес → BMI неизвестен
        return None


def _genotype(conn, rsid: str) -> str | None:
    try:
        r = conn.execute("SELECT genotype FROM genetic_variants WHERE rsid=? LIMIT 1", (rsid,)).fetchone()
        return ((r[0] if not hasattr(r, "keys") else r["genotype"]) or "").strip().upper() if r else None
    except Exception:  # silent-ok: нет таблицы/варианта → генотип неизвестен
        return None


def _trigger_matches(trigger: dict, med_text: str, conn, bmi: float | None = None) -> bool:
    if "problem_regex" in trigger:
        return bool(re.search(trigger["problem_regex"], med_text))
    if "bmi_below" in trigger:
        b = bmi if bmi is not None else _bmi(conn)
        return b is not None and b < float(trigger["bmi_below"])
    if "genome" in trigger:
        g = trigger["genome"] or {}
        gt = _genotype(conn, g.get("rsid", ""))
        allowed = {str(x).upper() for x in (g.get("genotype_in") or [])}
        return gt is not None and gt in allowed
    return False


def active_conditions(conn=None, med_text=None, bmi=None, profile: dict | None = None) -> set[str]:
    """Множество condition_id, чьи триггеры совпали с ДАННЫМИ тенанта. Ноль имён болезней в коде —
    только матч данных против версионируемого индекса.

    med_text/bmi — override (как food_floor.assert_floor): если заданы, берутся ВМЕСТО ре-скана.
    Нужно для паритета с medical_frame (тот же med_text = проблем-лист + profile.medical, тот же
    bmi из переданного профиля)."""
    c = conn if conn is not None else _db().get_conn()
    if med_text is None:
        med_text = patient_med_text(c, profile)
    else:
        med_text = str(med_text).lower()
    out = set()
    try:
        rows = c.execute(f"SELECT id, trigger_json FROM {_COND_TABLE}").fetchall()
    except Exception:  # silent-ok: таблицы нет (незасеянная БД) → нет активных состояний
        return out
    for r in rows:
        cid = r[0] if not hasattr(r, "keys") else r["id"]
        tj = r[1] if not hasattr(r, "keys") else r["trigger_json"]
        try:
            if _trigger_matches(json.loads(tj), med_text, c, bmi):
                out.add(cid)
        except Exception:  # silent-ok: битый триггер — пропускаем состояние, не роняем движок
            continue
    return out


def clinical_kb_language(conn=None, profile: dict | None = None) -> str:
    """Language of the supplied tenant, using flat or nested profile data."""
    if profile is not None:
        return i18n.lang_of({i18n.FIELD: profile.get(i18n.FIELD)
                            or (profile.get("identity") or {}).get("language")})
    if conn is None:
        return i18n.lang_of()
    try:
        row = conn.execute(
            "SELECT value_text, value_json FROM patient_profile WHERE key=?",
            (i18n.FIELD,)).fetchone()
        value = (json.loads(row[1]) if row[1] else row[0]) if row else None
    except Exception as exc:
        _log.warning("food language unavailable (%s); using default", type(exc).__name__)
        value = None
    return i18n.lang_of({i18n.FIELD: value})


def cond_trigger_type(conn=None, *, lang: str | None = None) -> dict:
    """condition_id → тип триггера ('медкарта'|'геном'|'профиль') для провенанса reasons.
    Паритет с medical_frame, где источник помечался вручную."""
    c = conn if conn is not None else _db().get_conn()
    lang = lang or clinical_kb_language(c)
    out = {}
    try:
        for r in c.execute(f"SELECT id, trigger_json FROM {_COND_TABLE}").fetchall():
            cid = r[0] if not hasattr(r, "keys") else r["id"]
            tj = json.loads(r[1] if not hasattr(r, "keys") else r["trigger_json"])
            label_key = ("food.source.genome" if "genome" in tj else
                         "food.source.profile" if "bmi_below" in tj else "food.source.medical_record")
            out[cid] = i18n.t(label_key, lang)
    except Exception:  # silent-ok: таблицы нет → пустая карта
        pass
    return out


def _recency_ok(row: dict) -> bool:
    """Гейт давности (решение владельца: явный status + review). status='active' И (durable ИЛИ
    review_date не просрочен). Фаза 2: review_date у большинства пищевых записей пуст → активно.
    Полный per-class recency-энфорс — Фаза 3."""
    if row.get("status", "active") != "active":
        return False
    rd = row.get("review_date")
    if not rd or row.get("temporal_class") == "durable":
        return True
    try:
        from _time_inject import get_today
        return str(get_today()) <= str(rd)
    except Exception:  # silent-ok: не смогли сравнить дату → не гейтим (fail-open по свежести)
        return True


def active_entries(conn=None, domain: str = "food", med_text=None, bmi=None,
                   profile: dict | None = None) -> list[dict]:
    """Активные записи домена для ЭТОГО тенанта: shared + импликации совпавших состояний, под
    status/recency-гейтом. Каждая: {id, condition_key, kind, payload, source, temporal_class}.
    med_text/bmi/profile — override для паритета с medical_frame (см. active_conditions)."""
    c = conn if conn is not None else _db().get_conn()
    conds = active_conditions(c, med_text=med_text, bmi=bmi, profile=profile)
    allow = {"", "shared"} | conds
    out = []
    try:
        rows = c.execute(
            f"SELECT id, condition_key, kind, payload_json, source, status, temporal_class, review_date "
            f"FROM {_KB_TABLE} WHERE domain=?", (domain,)).fetchall()
    except Exception:  # silent-ok: таблицы нет → пусто
        return out
    for r in rows:
        d = r if isinstance(r, dict) else {
            "id": r[0], "condition_key": r[1], "kind": r[2], "payload_json": r[3],
            "source": r[4], "status": r[5], "temporal_class": r[6], "review_date": r[7]}
        if d["condition_key"] not in allow:
            continue
        if not _recency_ok(d):
            continue
        try:
            payload = json.loads(d["payload_json"])
        except Exception:  # silent-ok: битый payload — пропускаем запись
            continue
        out.append({"id": d["id"], "condition_key": d["condition_key"], "kind": d["kind"],
                    "payload": payload, "source": d["source"], "temporal_class": d["temporal_class"]})
    return out
