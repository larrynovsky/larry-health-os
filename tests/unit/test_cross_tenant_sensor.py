"""
Датчик кросс-тенант загрязнения (integrity_tests.scan_cross_tenant).

Инцидент 2026-07-02: Oura владельца осела в БД партнёра. Корень закрыт
fail-closed secrets_dir(); этот датчик — belt-and-suspenders, ловит рецидив по
следствию (идентичный Oura-отпечаток у двух разных людей на одну дату).

Датчик, который не умеет срабатывать, бесполезен — здесь доказываем оба исхода:
срабатывает на идентичном отпечатке, молчит на разном и на NULL-полях.
"""
from __future__ import annotations

import sqlite3

import pytest

pytestmark = pytest.mark.unit

_COLS = ["hrv", "resting_hr", "sleep_total", "readiness", "spo2_avg"]


def _make_db(path, rows):
    c = sqlite3.connect(str(path))
    c.execute(
        "CREATE TABLE daily_metrics (date TEXT PRIMARY KEY, hrv REAL, "
        "resting_hr REAL, sleep_total REAL, readiness INTEGER, spo2_avg REAL)")
    for r in rows:
        c.execute(
            "INSERT INTO daily_metrics (date,hrv,resting_hr,sleep_total,readiness,spo2_avg) "
            "VALUES (?,?,?,?,?,?)",
            (r["date"], *[r.get(k) for k in _COLS]))
    c.commit()
    c.close()


def _dirs(tmp_path):
    # уникальный подкаталог: fixture `db` уже занимает tmp_path/health/data
    root = tmp_path / "xtenant"
    a = root / "health" / "data"; a.mkdir(parents=True, exist_ok=True)
    b = root / "health_partner" / "data"; b.mkdir(parents=True, exist_ok=True)
    return a / "health.db", b / "health.db"


def test_identical_fingerprint_flagged(db, tmp_path):
    import integrity_tests as it
    da, db_ = _dirs(tmp_path)
    fp = {"date": "2026-07-01", "hrv": 42.5, "resting_hr": 58.0,
          "sleep_total": 6.7, "readiness": 80, "spo2_avg": 97.0}
    _make_db(da, [fp])
    _make_db(db_, [dict(fp)])  # тот же отпечаток у другого тенанта = утечка
    hits = it.scan_cross_tenant(da, [db_])
    assert hits, "идентичный Oura-отпечаток обязан быть помечен как загрязнение"
    assert "2026-07-01" in hits[0] and "health_partner" in hits[0]


def test_different_fingerprint_clean(db, tmp_path):
    import integrity_tests as it
    da, db_ = _dirs(tmp_path)
    _make_db(da, [{"date": "2026-07-01", "hrv": 42.5, "resting_hr": 58.0,
                   "sleep_total": 6.7, "readiness": 80, "spo2_avg": 97.0}])
    _make_db(db_, [{"date": "2026-07-01", "hrv": 55.1, "resting_hr": 49.0,
                    "sleep_total": 7.9, "readiness": 72, "spo2_avg": 98.0}])
    assert it.scan_cross_tenant(da, [db_]) == [], \
        "разные люди — разные отпечатки, ложного срабатывания быть не должно"


def test_null_field_not_flagged(db, tmp_path):
    import integrity_tests as it
    da, db_ = _dirs(tmp_path)
    row = {"date": "2026-07-01", "hrv": None, "resting_hr": 58.0,
           "sleep_total": 6.7, "readiness": 80, "spo2_avg": 97.0}
    _make_db(da, [dict(row)])
    _make_db(db_, [dict(row)])
    assert it.scan_cross_tenant(da, [db_]) == [], \
        "NULL в отпечатке → не совпадение (защита от near-empty дней)"


def test_nonoverlapping_dates_clean(db, tmp_path):
    import integrity_tests as it
    da, db_ = _dirs(tmp_path)
    _make_db(da, [{"date": "2026-07-01", "hrv": 42.5, "resting_hr": 58.0,
                   "sleep_total": 6.7, "readiness": 80, "spo2_avg": 97.0}])
    _make_db(db_, [{"date": "2026-07-02", "hrv": 42.5, "resting_hr": 58.0,
                    "sleep_total": 6.7, "readiness": 80, "spo2_avg": 97.0}])
    assert it.scan_cross_tenant(da, [db_]) == [], \
        "разные даты не сравниваются даже при совпадении значений"


# --- ОРАКУЛ на ОБЁРТКУ, а не на чистую функцию -------------------------------------
# Замер У-1 (2026-07-30): обещание `multitenancy/cross_tenant_sensor` не было защищено
# НИЧЕМ — датчик заставили замолчать, и ни один тест не покраснел. Четыре теста выше при
# этом честные и осмысленные. Разбор механики провала: все четыре проверяют ЧИСТУЮ ФУНКЦИЮ
# `scan_cross_tenant`, а мутировали ОБЁРТКУ `check_cross_tenant_contamination`, у которой
# три тихих выхода, не покрытых ничем:
#   · `_is_dev_clone(cur)` → «скан пропущен»: предикат станет всегда-истинным и датчик
#     молчит вечно, а в ночном мониторе это выглядит нормальной строкой;
#   · пустые `siblings` → «одиночный профиль»: сломается glob в `_tenant_db_paths`, и при
#     двух живых тенантах датчик отчитается, что сравнивать не с кем;
#   · `hits is None` → WARN вместо FAIL: усохнет список fingerprint-колонок.
# Это ровно закономерность У-1: где механизм — чистая функция, там защищено; где механизм —
# ГРАНИЦА между слоями, там нет. Отсюда конструкция: сажаем загрязнение и требуем, чтобы
# обёртка ДОНЕСЛА отказ, а не только чтобы функция его нашла.
#
# ЧЕГО ЭТИ ТЕСТЫ НЕ ДОКАЗЫВАЮТ: что `CROSS_TENANT_FP` — правильный признак утечки. Если
# отпечатка из пяти полей Oura недостаточно, мутации отработают штатно, датчик покраснеет
# как положено, и выйдет уверенно-ложное «изоляция под контролем». Достаточность признака —
# tacit-суждение владельца, машинно не выводимое.

_FP = {"date": "2026-07-01", "hrv": 42.5, "resting_hr": 58.0,
       "sleep_total": 6.7, "readiness": 80, "spo2_avg": 97.0}


def _tenant_tree(tmp_path, tenants):
    """Фальшивый HOME с БД тенантов: {имя_каталога: [строки]} → корень."""
    root = tmp_path / "fakehome"
    for name, rows in tenants.items():
        d = root / name / "data"
        d.mkdir(parents=True, exist_ok=True)
        _make_db(d / "health.db", rows)
    return root


def _point_sensor_at(monkeypatch, it, root, current="health"):
    """Обёртка ходит в `Path.home()` и `db.DB_PATH` — подменяем оба, чтобы прожечь
    НАСТОЯЩИЙ путь обнаружения тенантов, а не подсунуть готовый список. Подмена
    `_tenant_db_paths` заглушкой убила бы предмет: именно её отказ и есть один из трёх
    тихих выходов, которые тут проверяются."""
    monkeypatch.setattr(it.Path, "home", staticmethod(lambda: root))
    monkeypatch.setattr(it.db, "DB_PATH", root / current / "data" / "health.db")


def test_wrapper_raises_on_planted_contamination(db, tmp_path, monkeypatch):
    """Посаженное загрязнение доходит до ОТКАЗА, а не только до списка hits.

    `check()` в integrity_tests глотает AssertionError и превращает её в FAIL — то есть
    именно исключение здесь и есть та валюта, которая доезжает до ночного монитора,
    триажа и Telegram. Обёртка, вернувшая строку вместо исключения, выглядит как
    «✅ проверено».
    """
    import integrity_tests as it
    root = _tenant_tree(tmp_path, {"health": [dict(_FP)], "health_partner": [dict(_FP)]})
    _point_sensor_at(monkeypatch, it, root)
    with pytest.raises(AssertionError, match="кросс-тенант"):
        it.check_cross_tenant_contamination()


def test_wrapper_actually_finds_sibling_tenants(db, tmp_path, monkeypatch):
    """При двух живых тенантах датчик НЕ имеет права отчитаться «сравнивать не с кем».

    Позитивный контроль охвата: без него датчик, потерявший siblings, был бы зелёным и
    честным одновременно — он бы действительно ничего не нашёл, потому что не искал.
    """
    import integrity_tests as it
    other = dict(_FP, hrv=55.1, resting_hr=49.0, sleep_total=7.9, readiness=72, spo2_avg=98.0)
    root = _tenant_tree(tmp_path, {"health": [dict(_FP)], "health_partner": [other]})
    _point_sensor_at(monkeypatch, it, root)
    res = it.check_cross_tenant_contamination()
    assert "нет sibling" not in res, \
        f"датчик не нашёл второго тенанта, хотя он есть: {res!r} — скан не состоялся"
    assert res.startswith("чисто"), f"неожиданный исход скана: {res!r}"


def test_dev_clone_marker_never_covers_a_real_tenant(db, tmp_path):
    """Маркеры дев-клонов не имеют права накрыть реального пациента.

    Это не дубль предыдущих тестов, а другой предмет: `_is_dev_clone` — рубильник, который
    выключает датчик целиком и делает это ЛЕГАЛЬНО (staging законно держит копию биометрии
    владельца, BL-TENANT-OURA-1). Добавь в маркеры подстроку, встречающуюся в имени
    настоящего тенанта — и датчик замолчит по пациенту, оставшись зелёным.
    """
    import integrity_tests as it
    from pathlib import Path as _P
    for real in ("health", "health_partner"):
        assert not it._is_dev_clone(_P(f"/Users/x/{real}/data/health.db")), \
            f"реальный тенант {real!r} принят за дев-клон — датчик по нему замолчит"
    for clone in ("health_staging", "health_dev", "health_bak"):
        assert it._is_dev_clone(_P(f"/Users/x/{clone}/data/health.db")), \
            f"дев-клон {clone!r} не распознан — датчик будет ложно срабатывать на нём"
