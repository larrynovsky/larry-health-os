"""Норма выводится из документов, не набирается (нить norm-from-documents, 2026-09-02).

Один домен: ДОКУМЕНТ (CTCAE xlsx, EFLM API) → СНИМОК в репо (data/norm_docs/*.json, с
checksum) → СТРОКИ нормы для сида. Ни одного числа нормы в коде: числа приходят из файла
документа, карты термов — данные (ctcae_lab_terms.json). Сеть трогает только fetch_*;
сид и safety_net читают снимки — БД не зависит от сети на старте.

Замер, ради которого модуль: пороги safety_net с апреля были пересказом CTCAE по памяти
модели (HGB 12/10/8 = грейды анемии, PLT 100/50/20 вместо 75/50/25, ALT «3x/7x» вместо
3x/5x/20x). Между таблицей и базой стоял пересказ — модуль убирает ровно его.

Публичный контракт: import_ctcae, snapshot_ctcae, fetch_ctcae, load_ctcae_rows, fetch_eflm,
load_eflm, rcv_pct, documents, guideline_cadences, guideline_statements, remote_state. Остальное _private.

Справочник реестра и снимков: docs/reference/norm_documents.md. Карта внешних источников
нормы по видам (откуда брать и с какой каденцией): docs/reference/norm_sources.md.
"""
from __future__ import annotations

# INTENT: norm_from_documents — замысел и инварианты: subsystem_intent.yaml / project_intent norm_from_documents

import hashlib
import json
import math
import re
import zipfile
from datetime import date
from pathlib import Path
from xml.etree import ElementTree as ET

from _time_inject import get_today

ROOT = Path(__file__).resolve().parent
DOCS_DIR = ROOT / "data" / "norm_docs"
# ТЕКУЩИЙ документ порогов решения — один переключатель; смена версии = смена этого id,
# новый файл + карта термов + снимок + диф в коммите (2026-09-02: v5.0 → v6.0).
CTCAE_CURRENT = "CTCAE_v6.0"
CTCAE_FILES = {
    "CTCAE_v5.0": {"xlsx": DOCS_DIR / "ctcae_v5.0_2017-11-27.xlsx",
                   "terms": DOCS_DIR / "ctcae_lab_terms.json",
                   "snapshot": DOCS_DIR / "ctcae_lab_v5.0.json"},
    "CTCAE_v6.0": {"xlsx": DOCS_DIR / "ctcae_v6.0_2026-01-26.xlsx",
                   "terms": DOCS_DIR / "ctcae_lab_terms_v6.json",
                   "snapshot": DOCS_DIR / "ctcae_lab_v6.0.json"},
}
TERMS_PATH = CTCAE_FILES[CTCAE_CURRENT]["terms"]
CTCAE_XLSX = CTCAE_FILES[CTCAE_CURRENT]["xlsx"]
CTCAE_SNAPSHOT = CTCAE_FILES[CTCAE_CURRENT]["snapshot"]
EFLM_SNAPSHOT = DOCS_DIR / "eflm_bv.json"
EFLM_API = "https://biologicalvariation.eu/api/measurands"
SCHEDULES_PATH = DOCS_DIR / "schedules.json"

# Реестр документов — ДАННЫЕ: версия, дата, где лежит, когда проверять следующую версию.
# next_check_days — каденция проверки версии, не срок годности числа: CTCAE меняется
# версиями (v5 2017 → v6 2025), EFLM — по updated_at мета-оценок.
DOCUMENTS = [
    {"id": "CTCAE_v5.0", "name": "NCI CTCAE v5.0 (Clean Copy xlsx) — предыдущий, для дифа", "version": "5.0",
     "issued": "2017-11-27",
     "url": "https://evs.nci.nih.gov/ftp1/CTCAE/CTCAE_5.0/CTCAE_v5.0_2017-11-27.xlsx",
     "local": str(CTCAE_FILES["CTCAE_v5.0"]["xlsx"].relative_to(ROOT)), "kind": "decision_threshold",
     "next_check_days": 365, "applies_to": "oncology"},
    {"id": "CTCAE_v6.0", "name": "NCI CTCAE v6.0 (NCIt xlsx, ревизия 2026-01-26) — ТЕКУЩИЙ источник сида",
     "version": "6.0 rev 2026-01-26", "issued": "2025-07-22",
     "url": "https://evs.nci.nih.gov/ftp1/CTCAE/CTCAE_6.0/NCIt_CTCAE%206.0.xlsx",
     "local": str(CTCAE_FILES["CTCAE_v6.0"]["xlsx"].relative_to(ROOT)), "kind": "decision_threshold",
     "next_check_days": 365, "applies_to": "oncology"},
    {"id": "EFLM_BV", "name": "EFLM Biological Variation Database (meta CV_I/CV_G)", "version": "api",
     "issued": None, "url": EFLM_API, "local": str(EFLM_SNAPSHOT.relative_to(ROOT)),
     "kind": "personal", "next_check_days": 30, "applies_to": "*"},
]
# Документы ТЕНАНТА (гайдлайны наблюдения по его эпизодам, решения его лечащего врача) — данные,
# не код: data/norm_docs/documents_tenant.json, приватная зона (решение владельца 2026-09-23 —
# публичный код не выдаёт диагноз публикатора). Нет файла — у установки нет своих документов.
TENANT_DOCUMENTS_PATH = DOCS_DIR / "documents_tenant.json"
if TENANT_DOCUMENTS_PATH.exists():
    DOCUMENTS += json.loads(TENANT_DOCUMENTS_PATH.read_text(encoding="utf-8"))["documents"]


def _schedules() -> dict:
    """schedules.json — кадансы/утверждения/правила гайдлайнов по эпизодам ТЕНАНТА (приватная
    зона). Нет файла — у установки нет эпизодов наблюдения: пустая рамка, не ошибка."""
    if not SCHEDULES_PATH.exists():
        return {"episodes": {}, "cadences": [], "statements": [], "rules": []}
    return json.loads(SCHEDULES_PATH.read_text(encoding="utf-8"))


def documents() -> list[dict]:
    """Реестр документов с локальным checksum и local_state — тем же видом состояния, что
    даёт remote_state (sha256 тела для файлов, max(updated_at) для EFLM): сравнимы напрямую."""
    out = []
    for d in DOCUMENTS:
        d = dict(d)
        p = ROOT / d["local"] if d["local"] else None
        d["sha256"] = _sha256(p) if p and p.exists() else None
        if d["id"] == "EFLM_BV" and p and p.exists():
            snap = json.loads(p.read_text(encoding="utf-8"))
            d["local_state"] = max(a["updated_at"] for a in snap["analytes"].values())
        else:
            d["local_state"] = d["sha256"]
        out.append(d)
    return out


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


# ── xlsx без зависимостей: zip + xml (ponytail ступень 2: stdlib) ─────────────────
_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def _read_xlsx_sheet1(path: Path) -> list[list[str]]:
    with zipfile.ZipFile(path) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root.findall("m:si", _NS):
                shared.append("".join(t.text or "" for t in si.iter(f"{{{_NS['m']}}}t")))
        root = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
        rows = []
        for row in root.find("m:sheetData", _NS).findall("m:row", _NS):
            cells = {}
            for c in row.findall("m:c", _NS):
                ref = c.get("r")
                col = re.match(r"[A-Z]+", ref).group(0)
                idx = 0
                for ch in col:
                    idx = idx * 26 + (ord(ch) - 64)
                v = c.find("m:v", _NS)
                t = c.get("t")
                if t == "s" and v is not None:
                    val = shared[int(v.text)]
                elif t == "inlineStr":
                    val = "".join(x.text or "" for x in c.iter(f"{{{_NS['m']}}}t"))
                else:
                    val = v.text if v is not None else ""
                cells[idx - 1] = val
            width = max(cells) + 1 if cells else 0
            rows.append([cells.get(i, "") for i in range(width)])
        return rows


def _wide_table(rows_x: list[list[str]]) -> tuple[list[str], list[list[str]]]:
    """Две раскладки CTCAE → одна широкая таблица [MedDRA Code, SOC, Term, G1..G5].
    v5 «Clean Copy»: уже широкая. v6 NCIt-экспорт: длинная — строка на «Grade N <Term>»
    с текстом грейда в 'CTCAE 6.0 Definition'; терм-строка несёт MedDRA Code."""
    header = [str(h).strip() for h in rows_x[0]]
    if "CTCAE Term" in header:
        return header, rows_x[1:]
    i_pt = header.index("CTCAE Preferred Term")
    i_def = next(i for i, h in enumerate(header) if h.endswith("Definition") and h.startswith("CTCAE"))
    i_med = header.index("MedDRA Code")
    wide: dict = {}
    order: list = []
    for r in rows_x[1:]:
        if len(r) <= i_pt or not r[i_pt]:
            continue
        m = re.match(r"Grade (\d) (.+)", str(r[i_pt]).strip())
        if m:
            g, term = int(m.group(1)), m.group(2).strip()
            wide.setdefault(term, {})[g] = r[i_def] if len(r) > i_def else ""
        else:
            term = str(r[i_pt]).strip()
            if term not in wide:
                order.append(term)
            wide.setdefault(term, {})["code"] = r[i_med] if len(r) > i_med else ""
    out = [[str(v.get("code", "")), "", t] + [str(v.get(g, "") or "") for g in range(1, 6)]
           for t, v in wide.items()]
    return ["MedDRA Code", "MedDRA SOC", "CTCAE Term", "Grade 1", "Grade 2", "Grade 3", "Grade 4", "Grade 5"], out


# ── парсер грейдов ─────────────────────────────────────────────────────────────
_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _clauses(cell: str) -> list[str]:
    out = []
    for cl in str(cell).split(";"):
        cl = cl.split(" if baseline")[0].strip()
        if not cl or "baseline" in cl.lower():
            continue
        out.append(cl)
    return out


def _parse_multiple(cell: str, direction: str):
    """'>ULN - 3.0 x ULN' → ('relative', 1.0); '>3.0 - 5.0 x ULN' → ('relative', 3.0)."""
    lim = "ULN" if direction == "ceiling" else "LLN"
    for cl in _clauses(cell):
        if lim not in cl:
            continue
        head = cl.split(lim)[0]
        nums = [float(n.replace(",", "")) for n in _NUM.findall(head)]
        if not nums:                       # '>ULN - 3.0 x ULN' → первый токен сам ULN
            return "relative", 1.0
        return "relative", (min(nums) if direction == "ceiling" else max(nums))
    return None


def _parse_absolute(cell: str, direction: str, unit_pick: str, scale: float):
    lim = "ULN" if direction == "ceiling" else "LLN"
    for cl in _clauses(cell):
        if unit_pick and unit_pick not in cl:
            continue
        head = cl.split(unit_pick)[0] if unit_pick else cl
        first_num = _NUM.search(head)
        lim_pos = head.find(lim)
        if lim_pos >= 0 and (first_num is None or lim_pos < first_num.start()):
            return "relative", 1.0
        nums = [float(n.replace(",", "")) * scale for n in _NUM.findall(head)]
        if not nums:
            continue
        return "absolute", (min(nums) if direction == "ceiling" else max(nums))
    return None


def import_ctcae(xlsx_path: Path | str = CTCAE_XLSX, terms_path: Path | str = TERMS_PATH) -> list[dict]:
    """xlsx CTCAE → строки нормы по карте термов. Неразобранный грейд у объявленного терма —
    ошибка (громко), не пропуск: карта объявляет, что терм числовой."""
    terms = json.loads(Path(terms_path).read_text(encoding="utf-8"))
    band_by_grade = terms["band_by_grade"]
    header, body = _wide_table(_read_xlsx_sheet1(Path(xlsx_path)))
    i_term = header.index("CTCAE Term")
    i_code = header.index("MedDRA Code")
    i_g1 = next(i for i, h in enumerate(header) if h.startswith("Grade 1"))
    by_term = {str(r[i_term]).strip(): r for r in body if len(r) > i_term}
    out = []
    for t in terms["terms"]:
        r = by_term.get(t["term"])
        if r is None:
            raise ValueError(f"CTCAE: терм '{t['term']}' не найден в документе")
        prev = None
        for g in (1, 2, 3):
            cell = r[i_g1 + g - 1] if len(r) > i_g1 + g - 1 else ""
            if not str(cell).strip() or str(cell).strip() == "-":
                continue
            if t["family"] == "multiple":
                parsed = _parse_multiple(cell, t["direction"])
            else:
                parsed = _parse_absolute(cell, t["direction"], t.get("unit_pick", ""), t.get("scale", 1.0))
            if parsed is None:
                raise ValueError(f"CTCAE: '{t['term']}' grade {g}: не разобрано: {cell!r}")
            kind, value = parsed
            key = (kind, round(value, 6))
            if key == prev:                # симптом-зависимый грейд с тем же числом — не порог
                continue
            prev = key
            out.append({
                "term": t["term"], "meddra": str(r[i_code]).strip(), "metric": t["metric"],
                "direction": t["direction"], "grade": g, "band": band_by_grade[str(g)],
                "kind": kind,
                "baseline": ("ULN" if t["direction"] == "ceiling" else "LLN") if kind == "relative" else None,
                "value": round(value, 6), "unit": t["unit"], "note": t.get("note"),
            })
    return out


def snapshot_ctcae(doc_id: str = CTCAE_CURRENT) -> Path:
    """Снимок: строки + провенанс (документ, checksum, дата). Это то, что читает сид."""
    doc = next(d for d in documents() if d["id"] == doc_id)
    f = CTCAE_FILES[doc_id]
    payload = {"document": doc, "generated": str(get_today()), "rows": import_ctcae(f["xlsx"], f["terms"])}
    f["snapshot"].write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return f["snapshot"]


def diff_ctcae(old_id: str, new_id: str) -> list[str]:
    """Диф двух снимков по (metric, direction, band): что изменилось в числах. Печатается в коммите."""
    a = {(r["metric"], r["direction"], r["band"]): r for r in load_ctcae_rows(CTCAE_FILES[old_id]["snapshot"])["rows"]}
    b = {(r["metric"], r["direction"], r["band"]): r for r in load_ctcae_rows(CTCAE_FILES[new_id]["snapshot"])["rows"]}
    out = []
    for k in sorted(set(a) | set(b)):
        ra, rb = a.get(k), b.get(k)
        fa = f"{ra['kind']}:{ra['value']}" if ra else "—"
        fb = f"{rb['kind']}:{rb['value']}" if rb else "—"
        if fa != fb:
            out.append(f"{k[0]}/{k[1]}/{k[2]}: {fa} → {fb}")
    return out


def fetch_ctcae(doc_id: str = CTCAE_CURRENT, timeout: int = 120, _open=None) -> Path:
    """Скачать xlsx CTCAE с сайта NCI (URL — реестр documents()) и построить снимок.

    Публичная установка (решение владельца 2026-09-25, BL-PUB-10): ни xlsx, ни снимок в
    открытый репозиторий не едут — в документе коды MedDRA, лицензию которых распространителю
    мы не проверили. Установка берёт документ у NCI сама, как человек браузером; в репо только
    наша карта термов. Скачанное проверяется как xlsx (zip с листом) и сразу разбирается
    import_ctcae — битый или чужой файл падает громко, а не ложится снимком."""
    import urllib.request
    doc = next(d for d in documents() if d["id"] == doc_id)
    dest = CTCAE_FILES[doc_id]["xlsx"]
    tmp = dest.with_name(dest.name + ".part")
    opener = _open or (lambda u: urllib.request.urlopen(u, timeout=timeout))
    try:
        with opener(doc["url"]) as resp:
            tmp.write_bytes(resp.read())
        if not zipfile.is_zipfile(tmp) or "xl/worksheets/sheet1.xml" not in zipfile.ZipFile(tmp).namelist():
            raise ValueError(f"CTCAE: по {doc['url']} пришёл не xlsx")
        import_ctcae(tmp, CTCAE_FILES[doc_id]["terms"])      # разбирается ли — до того, как станет документом
        tmp.replace(dest)
    finally:
        tmp.unlink(missing_ok=True)
    return snapshot_ctcae(doc_id)


def load_ctcae_rows(path: Path | str = CTCAE_SNAPSHOT) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ── EFLM: биологическая вариация → RCV ────────────────────────────────────────────
def _eflm_select(data: list) -> tuple[dict, list]:
    """Какие измеранды EFLM берёт установка (решение владельца 25.09, нить eflm-rule): ВСЕ, у
    которых есть мета-оценка CV_I и чьё display_name словарь канона (lab_canon) сводит к
    каноническому имени. Ручной карты нет — состав следует за словарём и ничего не говорит о
    том, кто установил. Возврат ({канон: (измеранд, мета)}, [конфликты]). Два измеранда в один
    канон — ни один не берётся: выбрать между ними значит угадать, какой из них «тот».
    Чистая функция, без сети — оракул tests/unit/test_eflm_rule.py."""
    import lab_canon
    hits: dict = {}
    for m in data:
        meta = next((x for x in m.get("metas", []) if x.get("cvi")), None)
        if not meta:
            continue
        canon = lab_canon.normalize(m["analyte"]["display_name"].strip())
        if canon in lab_canon.CANONICALS:
            hits.setdefault(canon, []).append((m, meta))
    clash = sorted(c for c, v in hits.items() if len(v) > 1)
    return {c: v[0] for c, v in hits.items() if len(v) == 1}, clash


def fetch_eflm(dest: Path | str = EFLM_SNAPSHOT, timeout: int = 60) -> Path:
    """GET /api/measurands → снимок аналитов, которые узнаёт словарь канона (_eflm_select)."""
    import logging
    import urllib.request
    with urllib.request.urlopen(EFLM_API, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))["data"]
    picked, clash = _eflm_select(data)
    if clash:
        logging.getLogger(__name__).warning(
            "EFLM: несколько измерандов сводятся к одному канону %s — не взят ни один", clash)
    out = {"source": EFLM_API, "fetched": str(get_today()), "analytes": {}}
    for canon, (m, meta) in sorted(picked.items()):
        out["analytes"][canon] = {
            "eflm_id": m["analyte"]["id"], "display_name": m["analyte"]["display_name"].strip(),
            "matrix": meta["matrix"]["matrix_expansion"],
            "cvi_median": float(meta["cvi"]["median"]), "cvi_lower": float(meta["cvi"]["lower"]),
            "cvi_upper": float(meta["cvi"]["upper"]), "cvi_n": meta["cvi"]["number_used"],
            "cvg_median": float(meta["cvg"]["median"]) if meta.get("cvg") else None,
            "updated_at": meta["cvi"]["updated_at"],
        }
    if not out["analytes"]:
        raise ValueError("EFLM: словарь канона не узнал ни одного измеранда — API сменил форму?")
    Path(dest).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return Path(dest)


def load_eflm(path: Path | str = EFLM_SNAPSHOT) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def rcv_pct(metric: str, cva_pct: float | None = None, z: float = 1.65, snapshot: dict | None = None) -> float:
    """Односторонний RCV, %: z·√2·√(CV_A²+CV_I²). CV_I — медиана EFLM; CV_A по умолчанию —
    желательная APS 0.5·CV_I (EFLM), пока лаборатория не дала свою (system_config norm.cva_source)."""
    snap = snapshot or load_eflm()
    a = snap["analytes"].get(metric)
    if a is None:
        raise KeyError(f"EFLM: нет CV_I для {metric} в снимке")
    cvi = a["cvi_median"]
    cva = cvi * 0.5 if cva_pct is None else cva_pct
    return round(z * math.sqrt(2) * math.sqrt(cva ** 2 + cvi ** 2), 1)


# ── кадансы наблюдения по эпизоду ─────────────────────────────────────────────────
def guideline_cadences(episodes: list[dict], today: date | None = None) -> list[dict]:
    """episodes — строки episodes_of_care (id, end_date, status). Возвращает действующие
    кадансы гайдлайнов: {metric, min_days, max_days, doc, episode_id, until}. Эпизод →
    applies_to задан в schedules.json явно по id (данные этой базы), не по тексту заголовка.
    Окно наблюдения считается от конца эпизода; истёкшее окно не возвращается."""
    from datetime import timedelta
    today = today or get_today()
    cfg = _schedules()
    out = []
    covered_no_lab = {st["applies_to"] for st in cfg.get("statements", []) if st.get("lab_cadence_recommended") is False}
    for ep in episodes:
        ep_cfg = cfg["episodes"].get(str(ep.get("id")))
        if not ep_cfg or not ep.get("end_date"):
            continue
        end = date.fromisoformat(str(ep["end_date"])[:10])
        for c in cfg["cadences"]:
            if c["applies_to"] != ep_cfg["applies_to"]:
                continue
            until = end + timedelta(days=365 * c["years_after_episode_end"])
            if until < today:
                continue
            out.append({"metric": c["metric"], "min_days": c["min_days"], "max_days": c["max_days"],
                        "doc": c["doc"], "episode_id": ep["id"], "until": str(until)})
    return out


def guideline_statements(episodes: list[dict]) -> list[dict]:
    """Прочитанные гайдлайны по эпизодам, которые кадансов НЕ дают:
    «документ прочитан, лабораторного наблюдения не рекомендует» — данные, не пустота."""
    cfg = _schedules()
    kinds = {cfg["episodes"][str(ep["id"])]["applies_to"] for ep in episodes if str(ep.get("id")) in cfg["episodes"]}
    return [st for st in cfg.get("statements", []) if st["applies_to"] in kinds]


def confirmation_metrics() -> set[str]:
    """Канон-имена аналитов, чей рост по правилу гайдлайна (schedules.json::rules,
    правило гайдлайна тенанта) считается сигналом только после подтверждения следующим забором.
    Пустое множество — правил нет; читатель тогда судит по одной паре, как раньше."""
    import lab_canon
    cfg = _schedules()
    return {lab_canon.normalize(m) for r in cfg.get("rules", [])
            if r.get("id") == "confirm_rise_before_action" for m in r.get("metrics", [])}


# ── свежесть документов ────────────────────────────────────────────────────────────
def remote_state(doc: dict, timeout: int = 30) -> str | None:
    """Наблюдаемое состояние удалённого документа: sha256 тела для файлов; для EFLM —
    max(updated_at) по аналитам, которые берёт установка (_eflm_select). None, если документ без URL-состояния
    (DOI-страницы гайдлайнов не сравниваются по телу — их версия проверяется руками).
    Сеть — только здесь; вызывающий (ночной датчик) решает, что значит «изменилось»."""
    import urllib.request
    url = doc.get("url") or ""
    if doc["id"] == "EFLM_BV":
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))["data"]
        upd = [meta["cvi"]["updated_at"] for _m, meta in _eflm_select(data)[0].values()]
        return max(upd) if upd else None
    if url.endswith((".xlsx", ".pdf", ".xls")):
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return hashlib.sha256(resp.read()).hexdigest()
    return None


if __name__ == "__main__":  # самопроверка: документ разбирается и даёт ожидаемые грейды
    rows = import_ctcae()
    idx = {(r["metric"], r["direction"], r["band"]): r for r in rows}
    assert idx[("HGB", "floor", "urgent")]["value"] == 10.0
    assert idx[("PLT", "floor", "warn")] ["kind"] == "relative" and idx[("PLT", "floor", "warn")]["baseline"] == "LLN"
    assert idx[("PLT", "floor", "urgent")]["value"] == 75.0 and idx[("PLT", "floor", "urgent")]["kind"] == "absolute"
    assert idx[("ALT", "ceiling", "urgent")]["value"] == 3.0 and idx[("ALT", "ceiling", "critical")]["value"] == 5.0
    print(f"ok: {len(rows)} строк из {CTCAE_XLSX.name} ({CTCAE_CURRENT})")
