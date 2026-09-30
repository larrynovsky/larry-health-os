#!/usr/bin/env python3.11
"""Проба фиксов раунда 4 НА ИСПОЛНЕНИИ (не на тестах). Запускать на Studio в staging.

Зачем отдельно от pytest: раунд 4 показал, что зелёный тест и работающий путь — разные вещи.
Мои тесты Ш1–Ш5 были зелёными на сломанном механизме. Здесь каждая находка воспроизводится
тем же способом, каким её воспроизвёл ревьюер, и ответ читается с ДИСКА и из ВОЗВРАТА, а не
из ассертов, которые писал тот же человек, что и код.

Данные: только фиктивные, собранные здесь. Канон не открывается ни на чтение, ни на запись.
"""
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import longitudinal_analysis as la      # noqa: E402
import quarantine_db                    # noqa: E402

OUT = {}
TMP = Path(tempfile.mkdtemp(prefix="vg_r4_"))


class _CG:
    columns: list = []


def _snap(d, members=None, flicker=None):
    s = {"date": d, "head_sha": "x", "members": members or {"D": [], "A": [], "q_lag": []}}
    if flicker is not None:
        s["flicker_vs_prev"] = flicker
    return s


# ── R4-01: порча истории замечается ДО того, как писатель её вылечит ──
art = TMP / "hist.json"
art.write_text("{ битый json", encoding="utf-8")
prior = la._validate_prior_passset(art)
OUT["R4-01_порча_замечена_до_писателя"] = prior["ok"] is False
state = la._write_passset_snapshot(_CG(), {"gate_applied": True}, artifact=art)
OUT["R4-01_писатель_сообщает_о_порче_наверх"] = bool(state["corrupt"])
_gm = {"gate_applied": True, "status": "failed"}      # то, что делает _run_body при corrupt
OUT["R4-01_публикация_заблокирована"] = la._publication_decision(_gm) is False

# ── R4-01b: возврат писателя участвует в решении ──
blocked = TMP / "каталог"
blocked.mkdir()
st = la._write_passset_snapshot(_CG(), {"gate_applied": True}, artifact=blocked, write_to=blocked)
OUT["R4-01b_отказ_записи_виден_как_не_ok"] = st["ok"] is False

# ── R4-02: CAS — проигравший не затирает чужой снимок ──
art2 = TMP / "cas.json"
art2.write_text(json.dumps([_snap("2026-07-19")]), encoding="utf-8")
_real = Path.read_text
_n = {"i": 0}


def _race(self, *a, **kw):
    out = _real(self, *a, **kw)
    _n["i"] += 1
    if _n["i"] == 1 and self == art2:                  # конкурент между чтением и записью
        art2.write_text(json.dumps([_snap("2026-07-19"), _snap("2026-07-26")]), encoding="utf-8")
    return out


Path.read_text = _race
st2 = la._write_passset_snapshot(_CG(), {"gate_applied": True}, artifact=art2)
Path.read_text = _real
OUT["R4-02_конфликт_замечен"] = st2["conflict"] is True
OUT["R4-02_чужой_снимок_на_месте"] = len(json.loads(art2.read_text(encoding="utf-8"))) == 2

# ── R4-03: конверт прогона — started/closed, run_id, dry-run не трёт боевую квитанцию ──
rec_path = TMP / "gate_run_receipt.json"
rec_path.write_text(json.dumps({"status": "failed", "error": "НАСТОЯЩИЙ ОТКАЗ"}), encoding="utf-8")
la.GATE_RUN_RECEIPT = rec_path
la.DRYRUN_DIR = TMP / "dryrun"
_seen = {}


def _body_ok(*a, **k):
    _seen["started"] = json.loads(rec_path.read_text(encoding="utf-8"))
    return None


_orig_body = la._run_body
la._run_body = _body_ok
la.run(out_path=TMP / "o.xlsx", save_db=False)          # РЕПЕТИЦИЯ
OUT["R4-03_боевая_квитанция_не_тронута_репетицией"] = \
    json.loads(rec_path.read_text(encoding="utf-8")).get("error") == "НАСТОЯЩИЙ ОТКАЗ"
OUT["R4-03_репетиция_оставила_свой_след"] = (TMP / "dryrun" / "gate_run_receipt.json").exists()

rec_path.unlink()
la.run(out_path=TMP / "o.xlsx", save_db=True)           # БОЕВОЙ, тело веры не пишет
_closed = json.loads(rec_path.read_text(encoding="utf-8"))
OUT["R4-03_конверт_открывается_started"] = _seen.get("started", {}).get("status") == "started"
OUT["R4-03_run_id_есть"] = bool(_closed.get("run_id"))
OUT["R4-03_published_не_врёт"] = _closed["published"] is False


def _body_boom(*a, **k):
    raise RuntimeError("сбой в теле")


la._run_body = _body_boom
try:
    la.run(out_path=TMP / "o.xlsx", save_db=True)
except RuntimeError:
    pass
_crash = json.loads(rec_path.read_text(encoding="utf-8"))
OUT["R4-03_упавший_прогон_оставил_failed"] = _crash["status"] == "failed"
la._run_body = _orig_body

# ── R4-04: репетиция не ставит пары в карантин ──
_sql = (Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "health_schema.sql"
        ).read_text(encoding="utf-8")
_i = _sql.index("CREATE TABLE IF NOT EXISTS passset_quarantine")
conn = sqlite3.connect(":memory:")
conn.executescript(_sql[_i:_sql.index(");", _i) + 2])
art3 = TMP / "q.json"
art3.write_text(json.dumps([_snap("2026-07-26", flicker={"D": {"entered": ["a×b"], "left": []}})]),
                encoding="utf-8")
la._quarantined_pairs(art3, conn=conn, commit=False)
OUT["R4-04_репетиция_не_ставит_в_карантин"] = \
    conn.execute("SELECT COUNT(*) FROM passset_quarantine").fetchone()[0] == 0
la._quarantined_pairs(art3, conn=conn, commit=True)
OUT["R4-04_боевой_ставит(негативный_контроль)"] = \
    conn.execute("SELECT COUNT(*) FROM passset_quarantine").fetchone()[0] == 1

# ── R4-05: порча состояния карантина видима, даже когда pending=0 ──
c2 = sqlite3.connect(":memory:")                        # таблица БЕЗ CHECK — как на боевой
c2.execute("CREATE TABLE passset_quarantine (id INTEGER PRIMARY KEY AUTOINCREMENT, pair TEXT, "
           "family TEXT, method_epoch TEXT DEFAULT '', entered_at TEXT DEFAULT (datetime('now')), "
           "status TEXT, resolved_at TEXT, resolution TEXT, resolved_by TEXT)")
c2.execute("INSERT INTO passset_quarantine (pair, family, method_epoch, status) "
           "VALUES ('a×b','D','signal_family_v7','PENDING')")
OUT["R4-05_читателю_строка_невидима"] = \
    quarantine_db.pending_quarantine_pairs("signal_family_v7", conn=c2) == set()
OUT["R4-05_датчику_строка_видима"] = len(quarantine_db.corrupt_quarantine_rows(c2)) == 1
OUT["R4-05_отсутствие_CHECK_сообщается"] = quarantine_db.quarantine_schema_guarded(c2) is False

# ── R4-06: гейт и producer считают ОДНО множество ──
import pandas as pd                                     # noqa: E402
import correlation_gate as cg                           # noqa: E402

_lab = cg._sf.LAB_METRICS[0]
labs = pd.DataFrame({"date": pd.to_datetime(["2026-07-01", "2026-07-02"]),
                     "test_name": [_lab, _lab], "value": [1.0, None]})
daily = pd.DataFrame({"date": pd.to_datetime(["2026-07-01"]), "hrv": [40.0]})
_prod = la.lab_metric_correlations(daily, labs).attrs.get("producer_obs_n", {}).get(_lab)
_gate = cg._lab_obs_counts(labs, [_lab]).get(_lab)
OUT["R4-06_паритет_выборки"] = (_prod == _gate == 1)

# ── R4-07: пустой вход проходит путь, а не роняет его ──
try:
    _e = la.lab_metric_correlations(
        daily, pd.DataFrame({"date": pd.to_datetime([]), "test_name": [], "value": []}))
    OUT["R4-07_пустой_лаб_вход_не_роняет"] = _e.empty and "spearman_r" in _e.columns
except Exception as exc:                                # noqa: BLE001
    OUT["R4-07_пустой_лаб_вход_не_роняет"] = f"УПАЛ: {exc!r}"

# ── Найдено при доводке: CLI вердиктов был мёртв ──
c3 = sqlite3.connect(":memory:")
c3.executescript(_sql[_i:_sql.index(");", _i) + 2])
_ep = quarantine_db.method_epoch()
quarantine_db.queue_quarantine([{"pair": "a×b", "family": "D"}], method_epoch=_ep, conn=c3)
OUT["CLI_снимает_под_правильной_эпохой"] = quarantine_db.resolve_quarantine(
    "a×b", "admitted", "обоснование", method_epoch=_ep, conn=c3) == 1
OUT["CLI_пустая_эпоха_не_совпадает(так_и_промахивался)"] = quarantine_db.resolve_quarantine(
    "a×b", "admitted", "обоснование", method_epoch="", conn=c3) == 0
OUT["CLI_единый_источник_эпохи"] = _ep == la._method_epoch()

print(json.dumps(OUT, ensure_ascii=False, indent=2))
_bad = [k for k, v in OUT.items() if v is not True]
print("\nНЕ ПОДТВЕРДИЛОСЬ:", _bad if _bad else "— всё подтверждено на исполнении")
sys.exit(1 if _bad else 0)
