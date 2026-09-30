#!/usr/bin/env python3.11
"""
lab_backfill_report.py — отчёт качества прогона перераспознавания.

Считает из lab_results_staging метрики, НЕ требующие эталона:
покрытие (rows/doc vs старая БД), согласие моделей (agree/disagree),
вердикты оракулов, маршрутизацию auto/pending, разбивка по форматам.
Ground-truth-проверка трудных форматов — отдельный шаг человека.

  /opt/homebrew/bin/python3.11 lab_backfill_report.py --run-id full1
"""
from __future__ import annotations
import argparse
import re
import json
from collections import Counter, defaultdict

import health_db


def _fmt(source_file: str) -> str:
    s = source_file.lower()
    # Корзины — формат бланка, не имя лаборатории или врача: имена живут в doc_patterns
    # тенанта (§9). До 2026-09-23 здесь стояли литералы врача и лаборатории владельца.
    if re.search(r"\beng\b", s):
        return "english_pdf"
    if any(x in s for x in (".heic", ".jpg", ".jpeg", "photo")):
        return "photo"
    if s.endswith(".pdf") and source_file[:3].isdigit():
        return "numbered_lab_pdf"
    if any(c in source_file for c in "абвгдежзиклмнопрстуфхцч"):
        return "other_script_scan"
    return "other"


def report(run_id: str) -> str:
    health_db.init_db()
    with health_db.get_conn() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM lab_results_staging WHERE run_id=?", (run_id,))]
        old = {(r["source"], r["date"]): r["c"] for r in conn.execute(
            "SELECT source, date, COUNT(*) c FROM lab_results GROUP BY source, date")}

    by_doc = defaultdict(list)
    for r in rows:
        by_doc[r["source_file"]].append(r)

    lines = [f"# Отчёт качества прогона `{run_id}`\n",
             f"Документов: {len(by_doc)} · строк: {len(rows)}\n"]

    # маршрутизация
    auto = sum(1 for r in rows if r["review_status"] == "auto")
    pend = len(rows) - auto
    docs_auto = sum(1 for d in by_doc.values() if all(r["review_status"] == "auto" for r in d))
    lines.append(f"\n## Маршрутизация\n")
    lines.append(f"- строки auto: {auto} ({100*auto//max(len(rows),1)}%) · pending: {pend}")
    lines.append(f"- документов полностью auto: {docs_auto}/{len(by_doc)}\n")

    # согласие моделей
    agree = sum(1 for r in rows if r["value_agreement"] == "agree")
    dis = sum(1 for r in rows if r["value_agreement"] == "disagree")
    sing = sum(1 for r in rows if r["value_agreement"] == "single")
    lines.append(f"## Согласие Opus↔Sonnet\n")
    lines.append(f"- по ЗНАЧЕНИЮ — agree: {agree} · disagree: {dis} · single(один проход): {sing}")

    # Согласие по ЕДИНИЦЕ и РЕФЕРЕНСУ (с 29.07.2026). Это ЗАМЕР под отложенное решение
    # владельца: маршрут эти числа не читает. Строки старее 29.07 несут NULL — их никто
    # не мерил, и `n/a` ниже говорит именно это, а не «расхождений нет».
    def _tally(col: str) -> str:
        c = Counter(r.get(col) for r in rows)
        if not any(k for k in c if k):
            return "n/a (строки прогнаны до 29.07.2026, поле не мерилось)"
        return " · ".join(f"{k or 'NULL'}: {n}" for k, n in c.most_common())

    lines.append(f"- по ЕДИНИЦЕ — {_tally('unit_agreement')}")
    lines.append(f"- по РЕФЕРЕНСУ — {_tally('ref_agreement')}")
    ev = [r for r in rows if r.get("field_evidence")]
    if ev:
        lines.append(f"\n  Расхождения по полям, сырые пары (до 10 из {len(ev)}):")
        for r in ev[:10]:
            lines.append(f"  - `{r['canonical_name'] or r['raw_name']}` "
                         f"@{_fmt(r['source_file'])} — {r['field_evidence']}")
    lines.append("")

    # по форматам
    fmt = defaultdict(lambda: {"docs": set(), "rows": 0, "auto": 0, "disagree": 0})
    for r in rows:
        f = _fmt(r["source_file"])
        fmt[f]["docs"].add(r["source_file"])
        fmt[f]["rows"] += 1
        fmt[f]["auto"] += r["review_status"] == "auto"
        fmt[f]["disagree"] += r["value_agreement"] == "disagree"
    lines.append("## По форматам\n")
    lines.append("| формат | докум. | строк | auto% | disagree |")
    lines.append("|---|---|---|---|---|")
    for f, d in sorted(fmt.items()):
        lines.append(f"| {f} | {len(d['docs'])} | {d['rows']} | "
                     f"{100*d['auto']//max(d['rows'],1)}% | {d['disagree']} |")

    # покрытие vs старая БД + оракулы по документам
    lines.append("\n## По документам (покрытие vs старая БД, оракулы)\n")
    lines.append("| документ | дата | строк сейчас | disagree | oracle-флагов | роут |")
    lines.append("|---|---|---|---|---|---|")
    for sf, d in sorted(by_doc.items(), key=lambda x: x[1][0]["date"]):
        date = d[0]["date"]
        oc = 0
        try:
            oc = sum(len(v) for v in json.loads(d[0]["oracle_notes"] or "{}").values())
        except Exception:
            pass
        dis_n = sum(1 for r in d if r["value_agreement"] == "disagree")
        route = "auto" if all(r["review_status"] == "auto" for r in d) else "pending"
        lines.append(f"| {sf[:38]} | {date} | {len(d)} | {dis_n} | {oc} | {route} |")

    # топ oracle-замечаний
    lines.append("\n## Что поймали оракулы (примеры)\n")
    seen = set()
    for r in rows:
        try:
            iss = json.loads(r["oracle_notes"] or "{}")
        except Exception:
            continue
        for k, msgs in iss.items():
            for m in msgs:
                key = (r["source_file"], m)
                if key not in seen:
                    seen.add(key)
                    lines.append(f"- [{r['source_file'][:30]}] {k}: {m}")
    if len(seen) == 0:
        lines.append("- (оракулы чистые)")
    return "\n".join(lines)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    a = ap.parse_args()
    print(report(a.run_id))
