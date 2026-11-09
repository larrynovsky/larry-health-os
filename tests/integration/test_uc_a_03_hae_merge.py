"""
UC-A-03 — Apple Health / HAE merge без затирания Oura.

Источник: USE_CASES.md §4.A → UC-A-03.
Status: `partial` (известный баг import_apple_health.py:248 — shallow merge
через `existing.update(summary)`).

Главный тест помечен xfail. Когда фикс — xpass, и можно убрать xfail.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def hae_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """tmp HEALTH_DATA для save_daily_summaries."""
    data_dir = tmp_path / "daily_metrics"
    data_dir.mkdir()

    import import_apple_health as iah
    monkeypatch.setattr(iah, "HEALTH_DATA", data_dir)
    return data_dir


def _write_existing(data_dir: Path, date_str: str, payload: dict) -> Path:
    p = data_dir / f"{date_str}.json"
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return p


# ── Главный тест: HAE не должен затирать Oura ────────────────────────────


def test_hae_merge_preserves_oura_hrv(hae_data_dir):
    """
    existing JSON (от Oura) содержит hrv=25; HAE прислал только steps без hrv.
    После save_daily_summaries(mode="merge") hrv должен остаться 25.

    Фикс W2A-2 (2026-05-08) в import_apple_health.py:248 — filter `is not None`
    перед update().
    """
    date_str = "2026-05-08"
    _write_existing(hae_data_dir, date_str, {
        "hrv": 25, "sleep_total": 7.5, "source": "Oura",
    })

    hae_summary = {date_str: {"steps": 8000, "source": "HAE"}}

    import import_apple_health as iah
    iah.save_daily_summaries(hae_summary, mode="merge")

    final = json.loads((hae_data_dir / f"{date_str}.json").read_text())
    assert final.get("hrv") == 25, "HAE затёр Oura hrv"
    assert final.get("sleep_total") == 7.5, "HAE затёр Oura sleep_total"


def test_hae_merge_adds_new_fields(hae_data_dir):
    """HAE добавил steps к existing JSON → steps появились."""
    date_str = "2026-05-08"
    _write_existing(hae_data_dir, date_str, {"hrv": 25})

    hae_summary = {date_str: {"steps": 8000}}

    import import_apple_health as iah
    iah.save_daily_summaries(hae_summary, mode="merge")

    final = json.loads((hae_data_dir / f"{date_str}.json").read_text())
    assert final.get("steps") == 8000, "HAE не добавил steps"


def test_hae_does_not_overwrite_oura_owned_keys(hae_data_dir):
    """
    Variant B (2026-06-01): Apple Health НЕ перезаписывает Oura-owned ключи
    даже если HAE прислал явное non-None значение.

    Семантика изменена с UC-A-03 оригинала: Apple Health данные хранятся
    в apple_health sub-dict, а flat Oura-ключи (hrv, sleep, resting_heart_rate)
    защищены от перезаписи при mode='merge'.

    Это предотвращает BUG когда Apple HRV перезаписывал Oura HRV
    для дней где оба источника имели данные.
    """
    date_str = "2026-05-08"
    _write_existing(hae_data_dir, date_str, {"hrv": 25})

    hae_summary = {date_str: {"hrv": 30, "steps": 8000}}

    import import_apple_health as iah
    iah.save_daily_summaries(hae_summary, mode="merge")

    final = json.loads((hae_data_dir / f"{date_str}.json").read_text())
    # Oura hrv=25 должен остаться нетронутым
    assert final.get("hrv") == 25, "Oura hrv=25 не должен быть перезаписан Apple hrv=30"
    # steps (не Oura-owned) — добавлен нормально
    assert final.get("steps") == 8000, "steps от Apple должен быть добавлен"


def test_overwrite_mode_replaces_completely(hae_data_dir):
    """`mode="overwrite"` (не merge) — полностью заменяет existing JSON."""
    date_str = "2026-05-08"
    _write_existing(hae_data_dir, date_str, {"hrv": 25, "sleep_total": 7.5})

    hae_summary = {date_str: {"steps": 8000}}

    import import_apple_health as iah
    iah.save_daily_summaries(hae_summary, mode="overwrite")

    final = json.loads((hae_data_dir / f"{date_str}.json").read_text())
    assert "hrv" not in final, "overwrite должен полностью заменить"
    assert final.get("steps") == 8000


def test_hae_explicit_null_overwrites_oura(hae_data_dir):
    """
    Сценарий бага: HAE прислал JSON с {"hrv": null}. Тогда existing.update()
    запишет hrv = null, затирая Oura.

    XFAIL пока import_apple_health.py:248 не фильтрует None перед update.
    """
    date_str = "2026-05-08"
    _write_existing(hae_data_dir, date_str, {"hrv": 25})

    # Симуляция: HAE сам себе генерит запись со всеми полями, и hrv там null
    hae_summary = {date_str: {"hrv": None, "steps": 8000}}

    import import_apple_health as iah
    iah.save_daily_summaries(hae_summary, mode="merge")

    final = json.loads((hae_data_dir / f"{date_str}.json").read_text())
    assert final.get("hrv") == 25, (
        f"HAE explicit null затёр Oura hrv. final={final}. "
        f"Фикс W2A-2: filter `is not None` перед existing.update()."
    )


def test_hae_merge_preserves_zero_values(hae_data_dir):
    """`0` это валидное значение (steps=0 = не двигался), не null. Перезаписывает."""
    date_str = "2026-05-08"
    _write_existing(hae_data_dir, date_str, {"steps": 8000})

    hae_summary = {date_str: {"steps": 0}}

    import import_apple_health as iah
    iah.save_daily_summaries(hae_summary, mode="merge")

    final = json.loads((hae_data_dir / f"{date_str}.json").read_text())
    assert final.get("steps") == 0, "0 (валидный) должен перезаписать существующее"


def test_atomic_write_no_partial_state(hae_data_dir):
    """
    Атомарность через os.replace(): если запись прервалась — финальный файл
    либо целиком новый, либо целиком старый. Промежуточного состояния нет.

    Косвенная проверка: после нормального save tmp-файл удалён.
    """
    date_str = "2026-05-08"

    import import_apple_health as iah
    iah.save_daily_summaries({date_str: {"steps": 1}}, mode="overwrite")

    # tmp-файл .tmp_{date}.json не должен остаться
    assert not (hae_data_dir / f".tmp_{date_str}.json").exists(), \
        "tmp-файл не удалён — нарушение атомарности"
    assert (hae_data_dir / f"{date_str}.json").exists()


def test_apple_sleep_read_late_update_and_oura_shape(db, hae_data_dir, monkeypatch):
    """Synthetic values: the real import/read boundary, no model or delivery."""
    import health_db
    import import_apple_health as iah
    from lifestyle_agents import SleepAgent

    monkeypatch.setattr(health_db, "METRICS_DIR", hae_data_dir)
    apple = {"totalSleep": 4.0, "source": "Apple Watch", "deep": 0.0}
    _write_existing(hae_data_dir, "2032-01-02", {"sleep": apple})
    health_db.migrate_all_json()
    day = health_db.get_day("2032-01-02")
    assert SleepAgent().has_data(day)
    assert day["sleep"]["source"] == "AppleHealth"
    assert day["sleep"]["deep"] == 0.0
    assert day["sleep"]["rem"] is None
    fuller = {**apple, "totalSleep": 7.5, "rem": 1.5}
    iah.save_daily_summaries({"2032-01-02": {"sleep": fuller}})
    fresh = health_db.get_day("2032-01-02")
    assert fresh["sleep"]["totalSleep"] == 7.5
    assert fresh["sleep"]["rem"] == 1.5
    assert json.loads((hae_data_dir / "2032-01-02.json").read_text()) == {"sleep": fuller}
    iah.save_daily_summaries({"2032-01-02": {"sleep": fuller}})
    assert health_db.get_day("2032-01-02") == fresh
    health_db.migrate_all_json()
    assert health_db.get_day("2032-01-02") == fresh

    oura = {"source": "Oura", "totalSleep": 8.0, "deep": 1.2,
            "contributors": {"efficiency": 92}, "extra": "preserved"}
    health_db.upsert_metrics_from_json("2032-01-03", {"sleep": oura}, source="Oura")
    _write_existing(hae_data_dir, "2032-01-03", {"sleep": oura})
    iah.save_daily_summaries({"2032-01-03": {"sleep": fuller}})
    health_db.migrate_all_json()
    assert health_db.get_day("2032-01-03")["sleep"] == oura
    assert SleepAgent().has_data(health_db.get_day("2032-01-03"))


def test_legacy_apple_archive_migration_keeps_source(db, hae_data_dir, monkeypatch):
    # Synthetic legacy HAE archive: no new provenance envelope.
    import health_db
    monkeypatch.setattr(health_db, "METRICS_DIR", hae_data_dir)
    _write_existing(hae_data_dir, "2032-02-01", {
        "sleep": {"totalSleep": 6.5, "source": "Apple Watch", "rem": 1.0}})
    health_db.migrate_all_json()
    day = health_db.get_day("2032-02-01")
    assert day["sleep"]["source"] == "AppleHealth"
    assert day["sleep"]["totalSleep"] == 6.5
    raw = json.loads(db.fetchone("SELECT raw FROM daily_metrics WHERE date=?",
                                ("2032-02-01",))["raw"])
    assert "sleep" not in raw


def test_sleep_update_rolls_back_on_storage_failure(db):
    import sqlite3
    import health_db
    db.add_daily_metrics("2032-03-01", sleep_total=4.0, raw="{}")
    db.execute("""CREATE TRIGGER reject_sleep BEFORE UPDATE OF raw ON daily_metrics
                  BEGIN SELECT RAISE(ABORT, 'synthetic storage failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="synthetic storage failure"):
        health_db.upsert_metrics_from_json("2032-03-01", {
            "sleep": {"totalSleep": 7.0, "deep": 1.0}}, source="AppleHealth")
    row = db.fetchone("SELECT sleep_total, sleep_deep, raw FROM daily_metrics WHERE date=?",
                      ("2032-03-01",))
    assert tuple(row) == (4.0, None, "{}")


@pytest.mark.parametrize("evidence", [
    {"sleep": {"contributors": {}}},
    {"sleep": {"source": "Oura"}},
    {"source": "Oura"},
    *({key: None} for key in (
        "readiness_score", "resilience", "stress", "hrv_balance",
        "body_temperature", "recovery_index", "breathing_disturbance")),
])
def test_oura_archive_upsert_payload_unchanged(hae_data_dir, monkeypatch, evidence):
    from unittest.mock import Mock
    import health_db

    # Synthetic mixed archive: even unrelated/unknown fields must be passed unchanged.
    payload = {"sleep": {"source": "Apple Watch", "totalSleep": 6.5},
               "steps": 4200, "weight_kg": 72.0, "extra": {"keep": True}, **evidence}
    _write_existing(hae_data_dir, "2032-04-01", payload)
    monkeypatch.setattr(health_db, "METRICS_DIR", hae_data_dir)
    upsert = Mock()
    monkeypatch.setattr(health_db, "upsert_metrics_from_json", upsert)
    health_db.migrate_all_json()
    upsert.assert_called_once_with("2032-04-01", payload, source="Oura")
