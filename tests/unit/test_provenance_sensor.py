"""Датчик провенанса сна: чинит сам, а на рецидив зовёт человека.

Зелёный датчик здесь доказывает только, что предикат не сработал, — не что данные чисты.
Разница видна ТОЛЬКО при инъекции, поэтому каждый тест ниже сначала портит данные, а
потом смотрит, что случилось (линза RST, Automation-Bias: all-green prompts investigation,
not celebration).

Фикстура берёт DDL из настоящей схемы проекта и переиспользует оснастку соседнего теста
миграции: выдуманная руками схема уже один раз пропустила недочиненные колонки.
"""
from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.unit

from tests.unit.test_repair_sleep_cycle_stages import _daily_metrics_ddl

FAKE_DAY = "2019-03-04"
FAKE_SLEEP = {"source": "Sleep Cycle", "deep": 0, "rem": 0, "core": 0, "awake": 0, "total": 6.5}
REAL_SLEEP = {"source": "Oura", "deep": 1.2, "rem": 1.4, "core": 3.4, "awake": 0.6, "total": 6.0}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import health_db as hdb

    monkeypatch.setattr(hdb, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(hdb, "METRICS_DIR", tmp_path / "daily_metrics")
    hdb.METRICS_DIR.mkdir(parents=True, exist_ok=True)
    with hdb.get_conn() as conn:
        conn.executescript(_daily_metrics_ddl())
    hdb._ensure_data_repair_log()
    return hdb


def _inject(hdb, day=FAKE_DAY, sleep=None):
    """Кладёт ночь во ВСЕ три дома — так же, как её положил бы писатель."""
    sleep = dict(sleep or FAKE_SLEEP)
    (hdb.METRICS_DIR / f"{day}.json").write_text(
        json.dumps({"sleep": sleep}, ensure_ascii=False), encoding="utf-8")
    with hdb.get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO daily_metrics (date, raw, sleep_deep, sleep_rem, "
            "sleep_core, sleep_awake) VALUES (?,?,?,?,?,?)",
            (day, json.dumps({"sleep": sleep}, ensure_ascii=False),
             sleep["deep"], sleep["rem"], sleep["core"], sleep["awake"]))


def _run(hdb, **kw):
    import integrity_tests as it
    it._warnings.clear()
    res = it.check_sleep_stage_provenance(**kw)
    return res, [w[0] for w in it._warnings]


def _fake_left(hdb):
    from migrations import repair_sleep_cycle_stages_20260731 as mig
    plan = mig.apply("dry", write=False)
    return plan["json"] + plan["sqlite"] + plan["raw"]


# ── Негативный контроль: на чистых данных датчик молчит и НИЧЕГО не пишет ──

def test_clean_data_is_silent_and_untouched(env):
    _inject(env, day="2025-06-01", sleep=REAL_SLEEP)
    res, warns = _run(env)
    assert res == {"found": 0, "repaired": 0}
    assert not warns, f"датчик, который варнит на чистых данных, — не датчик: {warns}"
    with env.get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM data_repair_log").fetchone()[0] == 0, (
            "на чистых данных журнал обязан остаться пустым — иначе авто-ремонт шумит"
        )


# ── Предмет: инъекция чинится САМА ──

def test_injected_fake_is_repaired_without_a_human(env):
    _inject(env)
    assert _fake_left(env) > 0, "предпосылка: инъекция подпадает под предикат"
    res, warns = _run(env)
    assert res["repaired"] == res["found"] > 0
    assert res["run_id"].startswith("auto-provenance-")
    assert not warns, (
        "первая починка — не повод звать человека (§13: эскалируется только неустранимое)"
    )
    assert _fake_left(env) == 0, "после авто-ремонта предикат обязан не находить ничего"


def test_repair_is_reversible_and_logged(env):
    """Авто-ремонт законен как ступень 1 ТОЛЬКО потому, что обратим. Если откат не
    возвращает исходное, это была необратимая правка данных без человека."""
    _inject(env)
    res, _ = _run(env)
    with env.get_conn() as conn:
        logged = conn.execute("SELECT COUNT(*) FROM data_repair_log WHERE run_id = ?",
                              (res["run_id"],)).fetchone()[0]
    assert logged == res["found"] > 0
    env.revert_repairs(res["run_id"])
    assert _fake_left(env) == res["found"], (
        "откат не вернул прежнее состояние — обратимость была заявлена, а не исполнена"
    )


def test_all_three_homes_are_repaired(env):
    """Домов три (файлы, плоские колонки, JSON внутри raw). Дом, который забыли, уже
    однажды сделал репарацию наполовину, и это заметили только по данным."""
    _inject(env)
    res, _ = _run(env)
    with env.get_conn() as conn:
        homes = dict(conn.execute(
            "SELECT home, COUNT(*) FROM data_repair_log WHERE run_id = ? GROUP BY home",
            (res["run_id"],)))
    assert set(homes) == {"json:daily_metrics", "sqlite:daily_metrics",
                          "sqlite:daily_metrics.raw"}, f"починены не все дома: {homes}"


# ── Рецидив: вторая починка того же — эскалация, а не тихий ремонт ──

def test_recurrence_escalates_and_stops_repairing(env):
    """§13 envelope (d). Писатель вернул ноль после нашей починки — чинить снова значит
    воевать с ним: данные мерцают, обе стороны «работают правильно», никто не видит."""
    _inject(env)
    first, _ = _run(env)
    assert first["repaired"] > 0
    _inject(env)                       # писатель вернул фальшивую ночь
    second, warns = _run(env)
    assert second.get("recurrence") is True
    assert second["repaired"] == 0, "при рецидиве авто-ремонт обязан ОСТАНОВИТЬСЯ"
    assert any("РЕЦИДИВ" in w for w in warns), f"рецидив не доехал до человека: {warns}"
    assert _fake_left(env) > 0, "данные намеренно оставлены как есть — решает человек"


def test_reverted_repair_is_not_a_recurrence(env):
    """Откаченная починка — не рецидив: мы сами вернули значения, писатель ни при чём.
    Иначе первый же ручной откат заблокировал бы датчик навсегда."""
    _inject(env)
    first, _ = _run(env)
    env.revert_repairs(first["run_id"])
    second, warns = _run(env)
    assert second.get("recurrence") is not True, f"откат принят за рецидив: {warns}"
    assert second["repaired"] > 0


def test_auto_repair_can_be_switched_off_and_then_it_speaks(env):
    """Выключенный авто-ремонт обязан ГОВОРИТЬ. Датчик, который не чинит и молчит, —
    это отсутствие датчика."""
    _inject(env)
    res, warns = _run(env, auto_repair=False)
    assert res["repaired"] == 0 and res["found"] > 0
    assert any("вернулись" in w for w in warns), warns


# ── §14: liveness самого СУЖДЕНИЯ, а не факта запуска ──

def test_degenerate_predicate_is_caught_not_silent(env, monkeypatch):
    """Предикат, выродившийся в «всегда False», даёт ровно то же наблюдаемое поведение,
    что чистые данные: датчик молчит. Heartbeat это различие не ловит по построению —
    он доказывает «запустился», а не «судит». Поэтому предикат сдаёт экзамен на эталонах.
    """
    from migrations import repair_sleep_cycle_stages_20260731 as mig
    _inject(env)                                   # данные ГРЯЗНЫЕ
    monkeypatch.setattr(mig, "is_fake_stage_record", lambda sleep: False)
    res, warns = _run(env)
    assert res is None, "вырожденный предикат обязан остановить датчик, а не «починить 0»"
    assert any("выродился" in w for w in warns), (
        f"датчик молча принял грязные данные за чистые: {warns}"
    )


def test_over_eager_predicate_is_caught_too(env, monkeypatch):
    """Обратное вырождение — «всегда True» — опаснее: авто-ремонт занулил бы НАСТОЯЩИЕ
    стадии Oura. Негативный эталон в liveness стоит ровно ради этого."""
    from migrations import repair_sleep_cycle_stages_20260731 as mig
    _inject(env, day="2025-06-01", sleep=REAL_SLEEP)
    monkeypatch.setattr(mig, "is_fake_stage_record", lambda sleep: True)
    res, warns = _run(env)
    assert res is None and any("выродился" in w for w in warns), (
        f"предикат, срабатывающий на настоящих данных, прошёл бы к авто-ремонту: {warns}"
    )
