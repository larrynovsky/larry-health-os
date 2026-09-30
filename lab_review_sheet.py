#!/usr/bin/env python3.11
"""
lab_review_sheet.py — самодостаточный HTML-лист ревью прогона.

По каждому документу: изображение страницы(ц) оригинала + распознанная
таблица рядом. Подсветка расхождений Opus↔Sonnet и срабатываний оракулов.
Документы отсортированы «худшее сверху». Чекбоксы reject → экспорт списка
(пользователь копирует обратно, остальное промоутится).

  /opt/homebrew/bin/python3.11 lab_review_sheet.py --run-id full1 \
      --out <облачная папка установки>/lab_review_full1.html
"""
from __future__ import annotations
import argparse
import base64
import html
import json
from collections import defaultdict

import health_db
from lab_backfill import resolve_document
from lab_recognizer import _render_pages


def _imgs(source_file: str) -> list[str]:
    path = resolve_document(source_file)
    if path is None:
        return []
    try:
        return [base64.b64encode(b).decode() for b in _render_pages(path)]
    except Exception:
        return []


def _row_class(r, flagged_names: set[str]) -> str:
    if r["value_agreement"] == "disagree":
        return "disagree"
    if r["canonical_name"] in flagged_names:
        return "oraflag"
    if r["value_agreement"] == "single":
        return "single"
    return "ok"


def build(run_id: str, out_path: str) -> str:
    health_db.init_db()
    with health_db.get_conn() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM lab_results_staging WHERE run_id=?", (run_id,))]
    by_doc = defaultdict(list)
    for r in rows:
        by_doc[r["source_file"]].append(r)

    def score(d):
        dis = sum(1 for r in d if r["value_agreement"] == "disagree")
        try:
            orc = sum(len(v) for v in json.loads(d[0]["oracle_notes"] or "{}").values())
        except Exception:
            orc = 0
        return -(dis * 10 + orc)   # худшее (больше проблем) — выше

    docs = sorted(by_doc.items(), key=lambda kv: score(kv[1]))

    parts = ["""<!doctype html><html lang=ru><head><meta charset=utf-8>
<title>Lab review — %s</title><style>
body{font:14px/1.5 -apple-system,system-ui,sans-serif;margin:0;background:#f4f5f7;color:#1a1a1a}
header{position:sticky;top:0;background:#fff;border-bottom:1px solid #ddd;padding:12px 20px;z-index:9}
header button{font-size:14px;padding:6px 14px;cursor:pointer}
.doc{background:#fff;margin:16px 20px;border:1px solid #e0e0e0;border-radius:8px;overflow:hidden}
.doc>h2{margin:0;padding:10px 16px;background:#fafafa;border-bottom:1px solid #eee;font-size:15px}
.doc .meta{font-weight:normal;color:#666;font-size:12px}
.body{display:grid;grid-template-columns:minmax(320px,460px) 1fr;gap:16px;padding:16px;align-items:start}
.imgs{position:sticky;top:64px;max-height:88vh;overflow:auto}
.imgs img{width:100%%;border:1px solid #ccc;margin-bottom:8px}
table{border-collapse:collapse;font-size:13px;width:100%%}
@media(max-width:820px){.body{grid-template-columns:1fr}.imgs{position:static;max-height:none}}
th,td{border:1px solid #e3e3e3;padding:3px 7px;text-align:left}
th{background:#f7f7f7}
tr.disagree{background:#ffe2e2}
tr.oraflag{background:#fff2cf}
tr.single{background:#eef}
.notes{margin:0;padding:8px 16px;background:#fff8e1;font-size:12px;color:#7a5}
.legend{font-size:12px;color:#555;padding:0 20px}
td.v{font-variant-numeric:tabular-nums;font-weight:600}
</style></head><body>
<header>
  <b>Ревью распознавания — прогон %s.</b>
  Отметь строки на <b>reject</b>, затем →
  <button onclick="exp()">Экспорт reject-списка</button>
  <span id=cnt></span>
  <div class=legend>🔴 модели разошлись · 🟡 поймал оракул · 🔵 один проход. Зелёные строки скорее всего верны. «*» — имя как в документе (аналит вне словаря, значение всё равно распознано).</div>
  <textarea id=out style="width:96%%;height:60px;display:none;margin-top:8px"></textarea>
</header>
""" % (html.escape(run_id), html.escape(run_id))]

    for source_file, d in docs:
        date = d[0]["date"]
        dis = sum(1 for r in d if r["value_agreement"] == "disagree")
        try:
            issues = json.loads(d[0]["oracle_notes"] or "{}")
        except Exception:
            issues = {}
        orc = sum(len(v) for v in issues.values())
        flagged_names = set()
        for msgs in issues.values():
            for m in msgs:
                for r in d:
                    if r["canonical_name"] and r["canonical_name"] in m:
                        flagged_names.add(r["canonical_name"])
        route = "auto" if all(r["review_status"] == "auto" for r in d) else "pending"

        imgs = "".join(f'<img src="data:image/jpeg;base64,{b}">' for b in _imgs(source_file))
        trs = []
        for r in sorted(d, key=lambda x: (x.get("page") or 0, x.get("raw_name") or x.get("canonical_name") or "")):
            cls = _row_class(r, flagged_names)
            # имя для показа: каноническое, иначе сырое (вне словаря), иначе "?"
            disp = r["canonical_name"] or r["raw_name"] or "?"
            extra = " *" if not r["canonical_name"] and r["raw_name"] else ""
            cb = (f'<input type=checkbox class=rej data-sf="{html.escape(source_file)}" '
                  f'data-cn="{html.escape(disp)}" '
                  f'data-v="{r["value"]}">')
            _pv = lambda x: "—" if x is None else x
            p12 = "" if r["value_agreement"] == "agree" else f'{_pv(r["pass1_value"])} / {_pv(r["pass2_value"])}'
            trs.append(
                f'<tr class="{cls}"><td>{cb}</td><td>{html.escape(disp)}{extra}</td>'
                f'<td class=v>{r["value"]}</td><td>{html.escape(r["unit"] or "")}</td>'
                f'<td>{r["ref_low"] if r["ref_low"] is not None else ""}–{r["ref_high"] if r["ref_high"] is not None else ""}</td>'
                f'<td>{html.escape(r["doc_flag"] or "")}</td>'
                f'<td>{html.escape(r["value_agreement"] or "")}</td><td>{p12}</td></tr>')
        notes_html = ""
        if issues:
            flat = [f"{k}: {m}" for k, ms in issues.items() for m in ms]
            notes_html = '<p class=notes>⚠ оракулы: ' + html.escape("; ".join(flat)) + '</p>'

        parts.append(
            f'<section class=doc><h2>{html.escape(source_file)} '
            f'<span class=meta>· {date} · строк {len(d)} · расхождений {dis} · '
            f'оракул {orc} · роут {route}</span></h2>{notes_html}'
            f'<div class=body><div class=imgs>{imgs or "<i>оригинал не найден</i>"}</div>'
            f'<table><thead><tr><th>rej</th><th>аналит</th><th>знач.</th><th>ед.</th>'
            f'<th>реф</th><th>флаг</th><th>согласие</th><th>p1/p2</th></tr></thead>'
            f'<tbody>{"".join(trs)}</tbody></table></div></section>')

    parts.append("""
<script>
function exp(){
  const rej=[...document.querySelectorAll('.rej:checked')].map(c=>({
     source_file:c.dataset.sf, canonical:c.dataset.cn, value:c.dataset.v}));
  const o=document.getElementById('out');
  o.style.display='block'; o.value=JSON.stringify(rej,null,1);
  document.getElementById('cnt').textContent=' — отмечено reject: '+rej.length;
  o.select();
}
</script></body></html>""")

    htmls = "".join(parts)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(htmls)
    return out_path


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    print("written:", build(a.run_id, a.out))
