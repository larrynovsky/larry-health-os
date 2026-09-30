"""Имена лаб-семьи валид-гейта разрешаются в данные (аудит датчиков 2026-07-25).

Класс ошибки: семья объявляет имя, хранилище содержит синоним. Буквальное
сравнение даёт ложное отсутствие данных. Синтетические ряды ниже проверяют,
что producer и гейт одинаково объединяют варианты имён.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import lab_canon                      # noqa: E402
import signal_family as _sf           # noqa: E402


def test_ca19_dot_variant_in_canon():
    """Точечный вариант — имя из signal_family. Без алиаса normalize возвращал вход как есть,
    и аналит был невидим гейту (позит-контроль: убери "ca19.9" из _SYNONYMS → красный)."""
    assert lab_canon.normalize("CA19.9") == "CA19-9"
    assert lab_canon.normalize("CA 19.9") == "CA19-9"


def test_every_family_name_resolves_to_known_canon():
    """Каждое объявленное имя семьи должно разрешаться в ИЗВЕСТНОЕ канон-имя.
    Имя, которого канон не знает, normalize вернёт как есть → его нет в CANONICALS →
    красный. Это check на класс, а не на две конкретные опечатки."""
    unknown = [n for n in _sf.LAB_METRICS if lab_canon.normalize(n) not in lab_canon.CANONICALS]
    assert not unknown, f"имена семьи вне канона (невидимы гейту): {unknown}"


def _mk_db(path: Path, rows):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE lab_results (test_name TEXT, specimen TEXT, value REAL)")
    con.executemany("INSERT INTO lab_results VALUES (?,?,?)",
                    [(n, "blood", 1.0) for n, k in rows for _ in range(k)])
    con.commit()
    con.close()


@pytest.mark.owner_data
def test_lab_scope_folds_name_variants(tmp_path, monkeypatch):
    """check_lab_path_scope считает по КАНОНУ: БД с "CA19-9"×18 годится, хотя семья
    объявляет "CA19.9". До правки строковое IN давало 0 — это позит-контроль правки."""
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HEALTH_SECRETS_DIR", str(tmp_path))
    import integrity_tests as I
    dbp = tmp_path / "t.db"
    _mk_db(dbp, [("CA19-9", 18), ("HGB", 18), ("Cholesterol_Total", 12), ("Cholesterol", 6)])
    r = I.check_lab_path_scope(db_path=str(dbp), base=ROOT)
    # CA19-9 (через точечное имя семьи), HGB, и Cholesterol_Total+Cholesterol=18 в сумме
    assert r["viable"] == 3, r


@pytest.mark.owner_data
def test_lab_scope_ignores_unknown_analyte(tmp_path, monkeypatch):
    """Позит-контроль в другую сторону: имя вне семьи не считается годным, сколько бы
    замеров ни было (иначе датчик срабатывал бы на любом анализе)."""
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HEALTH_SECRETS_DIR", str(tmp_path))
    import integrity_tests as I
    dbp = tmp_path / "u.db"
    _mk_db(dbp, [("Zzz_unknown", 99)])
    assert I.check_lab_path_scope(db_path=str(dbp), base=ROOT)["viable"] == 0


# ── Датчик класса «объявленное имя не находится в данных» ─────────────────────
def _mk_full_db(path: Path, lab_rows=None, daily_cols=None, daily_null=()):
    """БД, где ВСЯ семья разрешается: по одной записи на каждый канон-аналит и все
    daily-колонки заполнены. lab_rows/daily_cols переопределяют дефолт для сценария."""
    import lab_canon as _lc
    import signal_family as _s
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE lab_results (test_name TEXT, specimen TEXT, value REAL)")
    rows = lab_rows if lab_rows is not None else [(_lc.normalize(a), 1) for a in _s.LAB_METRICS]
    con.executemany("INSERT INTO lab_results VALUES (?,?,?)",
                    [(n, "blood", 1.0) for n, k in rows for _ in range(k)])
    cols = daily_cols if daily_cols is not None else list(_s.DAILY_METRICS)
    con.execute("CREATE TABLE daily_metrics (date TEXT, " + ", ".join(f'"{c}" REAL' for c in cols) + ")")
    vals = [None if c in daily_null else 1.0 for c in cols]
    con.execute("INSERT INTO daily_metrics VALUES (?" + ",?" * len(cols) + ")", ["2026-01-01"] + vals)
    con.commit()
    con.close()


def _issues(tmp_path, monkeypatch, **kw):
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HEALTH_SECRETS_DIR", str(tmp_path))
    import integrity_tests as I
    dbp = tmp_path / f"n{len(list(tmp_path.iterdir()))}.db"
    _mk_full_db(dbp, **kw)
    return I.check_family_names_resolve(db_path=str(dbp), base=ROOT)["issues"]


def test_names_sensor_silent_when_everything_resolves(tmp_path, monkeypatch):
    """Чистая БД → датчик молчит. Без этого «молчит» ничего не значит."""
    assert _issues(tmp_path, monkeypatch) == []


def test_names_sensor_catches_variant_name(tmp_path, monkeypatch):
    """Ферритин лежит под 'Ferritin_S' (суффикс лаборатории, канон его не знает) → 0 записей
    по объявленному имени, но похожее в БД есть. Это класс CA19.9↔CA19-9 в общем виде."""
    import lab_canon as _lc
    import signal_family as _s
    rows = [(_lc.normalize(a), 1) for a in _s.LAB_METRICS if a != "Ferritin"] + [("Ferritin_S", 9)]
    out = _issues(tmp_path, monkeypatch, lab_rows=rows)
    assert any("Ferritin" in i and "Ferritin_S" in i for i in out), out


def test_names_sensor_catches_daily_all_null(tmp_path, monkeypatch):
    """Колонка есть, но сплошь NULL — «пусто ≠ отсутствие данных», канон проекта."""
    out = _issues(tmp_path, monkeypatch, daily_null=("hrv",))
    assert any(i.startswith("daily.hrv") and "NULL" in i for i in out), out


def test_names_sensor_catches_missing_daily_column(tmp_path, monkeypatch):
    """Метрика объявлена, колонки нет — переименовали в схеме, семья не заметила."""
    import signal_family as _s
    cols = [c for c in _s.DAILY_METRICS if c != "spo2_avg"]
    out = _issues(tmp_path, monkeypatch, daily_cols=cols)
    assert any("daily.spo2_avg" in i and "нет колонки" in i for i in out), out


def _with_hae(tmp_path, registry, archive_start="20260706"):
    """Реестр HAE в последней созданной БД + архив сырья (самый старый файл = глубина памяти)."""
    dbp = sorted(tmp_path.glob("n*.db"))[-1]
    con = sqlite3.connect(dbp)
    con.execute("CREATE TABLE hae_metric_registry (metric_name TEXT, status TEXT, last_seen TEXT)")
    con.executemany("INSERT INTO hae_metric_registry VALUES (?,?,?)", registry)
    con.commit()
    con.close()
    (tmp_path / "hae_rest").mkdir(exist_ok=True)
    (tmp_path / "hae_rest" / f"HealthAutoExport-REST-{archive_start}T000000.json").write_text("{}")
    return dbp


def test_empty_column_without_device_is_silent(tmp_path, monkeypatch):
    """Решение владельца 26.09 (А): велосипед пуст, его источник не приходил за глубину архива →
    это «прибора нет», не потеря; кричать = alarm fatigue."""
    import integrity_tests as I
    _issues(tmp_path, monkeypatch, daily_null=("cycling_km",))
    dbp = _with_hae(tmp_path, [("cycling_distance", "handled", "2026-06-01")])
    out = I.check_family_names_resolve(db_path=str(dbp), base=ROOT)["issues"]
    assert not any(i.startswith("daily.cycling_km") for i in out), out


def test_empty_column_whose_source_arrives_is_loss(tmp_path, monkeypatch):
    """Давление: тонометр присылал (08.07 внутри архива), колонка пуста → потеря, кричим."""
    import integrity_tests as I
    _issues(tmp_path, monkeypatch, daily_null=("bp_systolic",))
    dbp = _with_hae(tmp_path, [("blood_pressure", "handled", "2026-07-08")])
    out = I.check_family_names_resolve(db_path=str(dbp), base=ROOT)["issues"]
    assert any(i.startswith("daily.bp_systolic") and "NULL" in i for i in out), out


# ── Находки внешнего ревью 2026-07-25 ─────────────────────────────────────────
def test_names_sensor_loud_on_db_without_schema(tmp_path, monkeypatch):
    """P2: БД без таблиц давала `issues=[]` — «всё хорошо», неотличимое от корректного
    отсутствия данных (два `except: → пустой набор` глушили громкий механизм check()).
    Теперь отсутствие таблиц у ВЛАДЕЛЬЦА — явная issue."""
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HEALTH_SECRETS_DIR", str(tmp_path))
    import integrity_tests as I
    empty = tmp_path / "empty.db"
    sqlite3.connect(empty).close()
    out = I.check_family_names_resolve(db_path=str(empty), base=ROOT)["issues"]
    assert any("нет таблицы" in i for i in out), out


def test_lab_scope_wrapper_loud_when_db_exists(tmp_path, monkeypatch):
    """P1: production-wrapper глотал ЛЮБОЕ исключение — «БД есть, но таблиц нет» было
    неотличимо от «мы не на Studio», и событие реактивации переставало вычисляться молча.
    Теперь молчим только когда файла БД физически нет."""
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HEALTH_SECRETS_DIR", str(tmp_path))
    import integrity_tests as I
    cap: list[tuple[str, str]] = []
    monkeypatch.setattr(I, "warn", lambda name, detail="": cap.append((name, detail)))
    empty = tmp_path / "e2.db"
    sqlite3.connect(empty).close()
    monkeypatch.setattr(I.db, "DB_PATH", str(empty))
    I._lab_path_scope_warn()
    assert any("не смог посчитать" in n for n, _ in cap), cap
    # обратная сторона: файла нет → молчим (не-Studio, не шумим)
    cap.clear()
    monkeypatch.setattr(I.db, "DB_PATH", str(tmp_path / "nope.db"))
    I._lab_path_scope_warn()
    assert cap == []


@pytest.mark.owner_data
def test_lab_scope_deferred_changes_label_not_eligibility(tmp_path, monkeypatch):
    """Отсрочка возврата (решение владельца 2026-08-31) — данные манифеста: eligible
    остаётся True, меняется только МЕТКА (каденция), и только пока дата не прошла.
    Негатив: истёкшая отсрочка → прежняя ежедневная метка."""
    import datetime as dt
    import shutil
    import yaml
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HEALTH_SECRETS_DIR", str(tmp_path))
    import integrity_tests as I
    # копия методологии с отсрочкой до завтра
    base = tmp_path / "root"
    shutil.copytree(ROOT / "methodology", base / "methodology")
    mp = base / "methodology" / "validation_gate" / "data_manifest.yaml"
    man = yaml.safe_load(mp.read_text(encoding="utf-8"))
    tomorrow = str(I.today + dt.timedelta(days=1))
    man["labs"]["reactivation"]["deferred"] = {"until": tomorrow, "reason": "тест"}
    mp.write_text(yaml.safe_dump(man, allow_unicode=True), encoding="utf-8")
    dbp = tmp_path / "d.db"
    _mk_db(dbp, [("HGB", 18), ("MCV", 18), ("WBC", 18)])
    r = I.check_lab_path_scope(db_path=str(dbp), base=base)
    assert r["eligible"] and r["deferred_until"] == tomorrow

    cap = []
    monkeypatch.setattr(I, "warn", lambda n, d="": cap.append(n))
    monkeypatch.setattr(I, "check_lab_path_scope", lambda: r)
    I._lab_path_scope_warn()
    assert cap and cap[0].startswith("Lab-путь годен, возврат отложен"), cap

    cap.clear()
    r2 = dict(r, deferred_until=str(I.today - dt.timedelta(days=1)))
    monkeypatch.setattr(I, "check_lab_path_scope", lambda: r2)
    I._lab_path_scope_warn()
    assert cap and cap[0].startswith("Lab-путь готов к возврату"), cap
