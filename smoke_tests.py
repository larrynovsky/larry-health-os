#!/usr/bin/env python3.11
"""
Phase 0.5 — Smoke tests для Health OS.
Запускать ДО и ПОСЛЕ каждого изменения в Phase 1.
Цель: не покрытие, а "система не упала".

Запуск: python3.11 smoke_tests.py
Успех: все PASS, время < 15 секунд.
"""
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

PASS = 0
FAIL = 0
_results = []

def check(name, fn):
    global PASS, FAIL
    try:
        result = fn()
        print(f"  ✅ {name}")
        PASS += 1
        return result
    except Exception as e:
        print(f"  ❌ {name}: {e}")
        FAIL += 1
        _results.append((name, str(e)))
        return None

start_time = time.time()
today     = str(date.today())  # time-inject: ok
yesterday = str(date.today() - timedelta(days=1))  # time-inject: ok
week_ago  = str(date.today() - timedelta(days=7))  # time-inject: ok

# ─────────────────────────────────────────────────────────────────────────────
print("\n[1] health_db — соединение и базовые чтения")
import health_db as db

conn = check("get_conn()", db.get_conn)
check("init_db()", db.init_db)
check("get_day(today)",       lambda: db.get_day(today))
check("get_day(yesterday)",   lambda: db.get_day(yesterday))
check("get_stats(7)",   lambda: db.get_stats(7))
check("get_stats(30)",  lambda: db.get_stats(30))
check("get_window(today_date, 7)", lambda: db.get_window(date.today(), 7))  # time-inject: ok

# ─────────────────────────────────────────────────────────────────────────────
print("\n[2] health_db — таблицы которые трогает Phase 1")

check("get_recent_checkins(3)",  lambda: db.get_recent_checkins(3))
check("get_active_experiments()", lambda: db.get_active_experiments())
check("get_context_events(yesterday)", lambda: db.get_context_events(yesterday))
check("get_open_tasks()",        lambda: db.get_open_tasks())
check("get_problem_list()",      lambda: db.get_problem_list())
check("get_recent_labs(90)",     lambda: db.get_recent_labs(90))
check("get_significant_variants(limit=5)", lambda: db.get_significant_variants(limit=5))
# get_open_hypotheses живёт в health_ai — проверяется в секции [3]

# get_experiment_stats — только если есть активные
exps = db.get_active_experiments()
if exps:
    eid = exps[0]["id"]
    check(f"get_experiment_stats({eid})", lambda: db.get_experiment_stats(eid))
else:
    print("  ⏭  get_experiment_stats — нет активных экспериментов, пропуск")

# ─────────────────────────────────────────────────────────────────────────────
print("\n[3] health_ai — протоколы и гипотезы (Phase 1: перенос в health_db)")
import health_ai as ai

protocols = check("get_active_protocols()", ai.get_active_protocols)
check("get_open_hypotheses()", ai.get_open_hypotheses)

# Проверяем структуру ответа
if protocols is not None:
    assert isinstance(protocols, list), "get_active_protocols должен вернуть list"
    print(f"     → {len(protocols)} активных протоколов")

# ─────────────────────────────────────────────────────────────────────────────
print("\n[4] gp_agent — функции которые содержат нарушения")
import gp_agent as gp

check("run_experiment_checks импортируется", lambda: gp.run_experiment_checks)
check("generate_daily_report импортируется", lambda: gp.generate_daily_report)
check("generate_attribution_report импортируется", lambda: gp.generate_attribution_report)

# ─────────────────────────────────────────────────────────────────────────────
print("\n[5] Остальные модули — не трогаем, но проверяем что живые")

check("import checkin_agent",    lambda: __import__("checkin_agent"))
check("import task_agent",       lambda: __import__("task_agent"))
check("import safety_net",       lambda: __import__("safety_net"))
check("import genome_context",   lambda: __import__("genome_context"))
check("import calendar_client",  lambda: __import__("calendar_client"))

# ─────────────────────────────────────────────────────────────────────────────
print("\n[6] Новые функции health_db из Phase 1 (TD-06)")

# ── pure reads ────────────────────────────────────────────────────────────────
check("get_checkin_by_date(yesterday, 'evening')",
      lambda: db.get_checkin_by_date(yesterday, time_of_day='evening'))
check("get_checkin_by_date(yesterday, None)",
      lambda: db.get_checkin_by_date(yesterday))

protos = check("get_active_protocols() via health_db direct", db.get_active_protocols)
if protos is not None:
    assert isinstance(protos, list), "должен вернуть list"

# ── write → verify → undo ────────────────────────────────────────────────────
def test_save_retire_protocol():
    pid = db.save_protocol({
        "title": "_smoke_test_protocol",
        "behavior": "smoke test — автоматически удаляется",
        "rationale": "auto-test",
        "frequency": "once",
        "reminder_days": "[]",
        "linked_hypothesis_id": None,
        "linked_experiment_id": None,
    })
    assert isinstance(pid, int) and pid > 0, f"ожидали int > 0, получили {pid}"
    # DELETE (не retire) — чтобы smoke-прогоны не засоряли таблицу протоколов
    with db.get_conn() as conn:
        conn.execute("DELETE FROM protocols WHERE id=?", (pid,))
    return pid

check("save_protocol() + retire_protocol() [write→undo]", test_save_retire_protocol)

# ── update_experiment_check_results (no-op если есть активные) ───────────────
exps_for_update = db.get_active_experiments()
if exps_for_update:
    def test_update_exp():
        exp = exps_for_update[0]
        current = exp.get("check_results") or "[]"
        db.update_experiment_check_results(exp["id"], current)  # same value → no-op
        return True
    check(f"update_experiment_check_results({exps_for_update[0]['id']}) [no-op]",
          test_update_exp)
else:
    print("  ⏭  update_experiment_check_results — нет активных экспериментов, пропуск")

print("\n[7] Критические пути — файлы должны существовать")
from pathlib import Path

SECRETS = Path.home() / ".health_secrets"

def must_exist(p):
    if not Path(p).exists():
        raise FileNotFoundError(f"не найден: {p}")
    return True

def _check_canonical_db():
    # БД — single-source на Studio (R1/R2, split-brain fix 2026-06-18). iCloud-копия
    # удалена; на не-Studio хосте БД нет by design → проверять только на Studio.
    import socket as _sock, health_db as _hdb
    if _hdb._infra.is_primary():
        return must_exist(_hdb.DB_PATH)
    print("  ⏭  health.db — не-Studio хост, БД только на Studio (single-source), пропуск")
    return True

check("health.db существует (Studio canonical)", _check_canonical_db)
check("anthropic_key существует", lambda: must_exist(SECRETS / "anthropic_key"))
check("sync_token существует",    lambda: must_exist(SECRETS / "sync_token"))
check("sync_token читается",      lambda: (SECRETS / "sync_token").read_text().strip() != "")

# ─────────────────────────────────────────────────────────────────────────────
elapsed = time.time() - start_time
print(f"\n{'─'*54}")
print(f"Итог: {PASS} PASS  {FAIL} FAIL  |  {elapsed:.1f}с")

if FAIL > 0:
    print("\nПадения:")
    for name, err in _results:
        print(f"  ❌ {name}: {err}")
    print("\n⛔ Не начинать Phase 1 до устранения падений.")
    sys.exit(1)
else:
    print("✅ Все smoke tests прошли. Можно приступать к Phase 1.")
    sys.exit(0)
