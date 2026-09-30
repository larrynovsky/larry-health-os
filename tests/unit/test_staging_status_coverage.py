"""Ратчет покрытия статусов staging (введён 2026-08-07).

Возможный сбой: писатель ставит `pending`, а датчики спрашивают только
`review_status='review'` и `('auto','gold')`. Ни один не покрывает очередь
классификации. Класс — [[feedback_one_question_one_key]]: ключ запроса
разошёлся с ключом хранения.

Судится здесь карта покрытия, а не конкретные строки: живые данные меняются, а
утверждение «у каждого статуса есть сторож» обязано держаться при любых данных.
"""
from __future__ import annotations

import pytest

import integrity_tests as I

pytestmark = pytest.mark.unit


def test_every_known_status_has_a_named_watcher():
    """Позитив: карта покрывает все статусы, которые пишет прод."""
    written_by_prod = {"pending", "auto", "gold", "review", "rejected", "promoted",
                       "specialized"}   # 2026-08-31: пишет lab_specialized
    missing = written_by_prod - set(I._STAGING_STATUS_WATCHERS)
    assert not missing, f"статусы без сторожа: {sorted(missing)}"


def test_watcher_names_are_not_empty():
    """Пустая строка — не имя датчика. Без этого карту можно «заполнить» заглушками
    и получить зелёный ратчет при нулевом покрытии — ровно тот класс, от которого
    защищает `producer_registry` (значение обязано быть правдой, не наличием ключа)."""
    for status, watcher in I._STAGING_STATUS_WATCHERS.items():
        assert watcher and watcher.strip(), f"статус {status!r} с пустым сторожем"


def test_terminal_statuses_are_justified_not_watched():
    """Негативный контроль на смысл: `rejected`/`promoted` НЕ должны ждать датчика —
    у них решение уже принято. Если кто-то впишет туда имя check_-функции, значит
    систему заставили спрашивать человека второй раз по закрытому вопросу."""
    for status in ("rejected", "promoted", "specialized"):
        assert not I._STAGING_STATUS_WATCHERS[status].startswith("check_"), \
            f"{status}: терминальный статус не может иметь сторожа ожидания"


def test_writers_and_map_agree():
    """Двусторонний предикат (§17): карта не только ПОКРЫВАЕТ статусы прода, но и не
    содержит выдуманных. Односторонняя проверка молчала бы о мусоре в карте, а мусор
    здесь читается как «покрытие есть»."""
    import lab_triage, lab_backfill, lab_specialized  # noqa: F401 — писатели статусов
    written_by_prod = {"pending", "auto", "gold", "review", "rejected", "promoted",
                       "specialized"}   # 2026-08-31: пишет lab_specialized
    extra = set(I._STAGING_STATUS_WATCHERS) - written_by_prod
    assert not extra, f"в карте статусы, которых никто не пишет: {sorted(extra)}"


# ── Исполнение ТЕЛА датчика, а не только карты ───────────────────────────────
# Урок 2026-08-07, оплаченный боевым прогоном: четыре теста выше судили только
# `_STAGING_STATUS_WATCHERS` — данные. Тело функции не исполнялось ни одним из них,
# и `NameError: name 're' is not defined` доехал до ночного integrity (FAIL, поймал
# живой прогон на Studio, а не набор). Тест, судящий данные вместо механизма, зелен
# ровно тогда, когда механизм не запускался (§20).

def _tenant_db(tmp_path, tag, rows):
    """(status, created_at) → готовая БД тенанта по боевому пути <tag>/data/health.db."""
    import sqlite3
    p = tmp_path / tag / "data"
    p.mkdir(parents=True)
    db = p / "health.db"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE lab_results_staging (review_status TEXT, created_at TEXT)")
    c.executemany("INSERT INTO lab_results_staging VALUES (?,?)", rows)
    c.commit(); c.close()
    return db


def test_body_runs_and_flags_unknown_status(tmp_path, monkeypatch):
    """Позитив тела: неизвестный статус найден, известный — нет."""
    cap = []
    monkeypatch.setattr(I, "warn", lambda n, d="": cap.append(n))
    db = _tenant_db(tmp_path, "health_x", [("teleported", "2026-08-01"),
                                           ("promoted", "2026-08-01")])
    I.check_staging_status_coverage(paths=[db])
    assert any("teleported" in x for x in cap), f"неизвестный статус не найден: {cap}"
    assert not any("promoted" in x for x in cap), f"терминальный статус не должен шуметь: {cap}"


def test_body_flags_stale_pending(tmp_path, monkeypatch):
    """Придуманный мини-набор: старый pending назван; свежий — молчит."""
    from datetime import timedelta
    cap = []
    monkeypatch.setattr(I, "warn", lambda n, d="": cap.append(n))
    old = str(I.today - timedelta(days=99))
    I.check_staging_status_coverage(paths=[_tenant_db(tmp_path, "h_old", [("pending", old)])])
    assert any("не классифицированы" in x for x in cap), f"старый pending не найден: {cap}"

    cap.clear()
    fresh = str(I.today)
    I.check_staging_status_coverage(paths=[_tenant_db(tmp_path, "h_new", [("pending", fresh)])])
    # Фильтр по «staging[»: вне Studio `_intake_cfg` честно кричит о недоступной БД
    # конфига, и это ЧУЖОЙ warn. Ловить его здесь значило бы судить окружение машины
    # вместо механизма — тот же §20, только с другой стороны.
    mine = [x for x in cap if x.startswith("staging[")]
    assert mine == [], f"свежий pending обязан молчать: {mine}"


# ── Возраст очереди review — от смены статуса, не от разбора (2026-08-31) ────

def _review_db(tmp_path, tag, rows):
    """(created_at, status_changed_at) → БД тенанта со строками в review."""
    import sqlite3
    p = tmp_path / tag / "data"
    p.mkdir(parents=True)
    db = p / "health.db"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE lab_results_staging "
              "(review_status TEXT, created_at TEXT, status_changed_at TEXT, "
              " value REAL, value_text TEXT, canonical_name TEXT, raw_name TEXT)")
    c.executemany("INSERT INTO lab_results_staging VALUES ('review',?,?,5.0,NULL,'HGB','HGB')",
                  rows)
    c.commit(); c.close()
    return db

def test_review_age_counts_from_status_change(tmp_path, monkeypatch):
    """Бланк 2023, триажированный сегодня, — НЕ «ждёт 3 года». Негатив: NULL в
    status_changed_at (старая строка) считается по-старому от created_at."""
    from datetime import timedelta
    cap = []
    monkeypatch.setattr(I, "warn", lambda n, d="": cap.append(n))
    old = str(I.today - timedelta(days=999))
    fresh = str(I.today)
    I.check_lab_review_queue_movement(
        paths=[_review_db(tmp_path, "h_fresh", [(old, fresh)])])
    assert not [x for x in cap if x.startswith("очередь ревью[")], cap

    cap.clear()
    I.check_lab_review_queue_movement(
        paths=[_review_db(tmp_path, "h_null", [(old, None)])])
    assert [x for x in cap if x.startswith("очередь ревью[")], cap

def test_review_age_survives_db_without_column(tmp_path, monkeypatch):
    """БД тенанта до init_db (колонки нет) читается по-старому, а не падает."""
    import sqlite3
    from datetime import timedelta
    p = tmp_path / "h_old" / "data"; p.mkdir(parents=True)
    db = p / "health.db"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE lab_results_staging (review_status TEXT, created_at TEXT, "
              " value REAL, value_text TEXT, canonical_name TEXT, raw_name TEXT)")
    c.execute("INSERT INTO lab_results_staging VALUES ('review',?,5.0,NULL,'HGB','HGB')",
              (str(I.today - timedelta(days=99)),))
    c.commit(); c.close()
    cap = []
    monkeypatch.setattr(I, "warn", lambda n, d="": cap.append(n))
    I.check_lab_review_queue_movement(paths=[db])
    assert [x for x in cap if x.startswith("очередь ревью[")], cap
