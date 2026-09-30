"""W5K #171 + #177: контракт per-source ownership в upsert_metrics_from_json.

Каждый источник пишет ТОЛЬКО свои колонки. Чужие — не трогает.
None никогда не пишется. raw мерджится per-source, не overwrite.

История:
- 2026-05-14 #171: первый backfill Oura перезаписал sleep_total из Apple Health
  на None. Потеряли 12 месяцев данных. Восстановили из backup.
- 2026-05-14 #177: новая upsert после #171 не писала raw → get_day для свежих
  дат возвращал {} → downstream агенты получали пустой контекст.

Этот тест защищает от обеих регрессий.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def _row(db, day):
    """Прямой SELECT всех колонок для проверки column-level isolation."""
    return db.fetchone("SELECT * FROM daily_metrics WHERE date=?", (day,))


def test_oura_does_not_overwrite_apple_health_columns(db):
    """Oura пишет sleep, но не трогает weight/vo2max/bp от AppleHealth.

    Per-source ownership после W5K (2026-05-14):
      Oura: sleep, hrv, resting_hr, readiness, stress, resilience, spo2,
            steps, active_kcal, total_kcal, distance_km, activity_score
      AppleHealth: weight, vo2max, bp_systolic, bp_diastolic
    """
    from health_db import upsert_metrics_from_json

    day = "2026-05-08"

    # AppleHealth пишет manual/external — weight, vo2max
    upsert_metrics_from_json(
        day,
        {"weight_kg": 75.5, "vo2_max": 42.0},
        source="AppleHealth",
    )
    row = _row(db, day)
    assert row["weight"] == 75.5
    assert row["vo2max"] == 42.0

    # Oura пишет sleep + НЕ имеет weight/vo2max
    upsert_metrics_from_json(
        day,
        {"sleep": {"totalSleep": 7.0, "deep": 0.5}, "hrv": {"avg": 25}},
        source="Oura",
    )

    row = _row(db, day)
    assert row["sleep_total"] == 7.0
    assert row["hrv"] == 25
    # AppleHealth колонки СОХРАНИЛИСЬ — это главное что защищает тест
    assert row["weight"] == 75.5, "AppleHealth weight был затёрт Oura"
    assert row["vo2max"] == 42.0, "AppleHealth vo2max был затёрт Oura"


def test_apple_health_does_not_overwrite_oura_columns(db):
    """AppleHealth пишет weight, не трогает sleep/hrv/steps от Oura."""
    from health_db import upsert_metrics_from_json

    day = "2026-05-08"

    # Oura пишет sleep + steps (steps теперь Oura ownership)
    upsert_metrics_from_json(
        day,
        {
            "sleep": {"totalSleep": 7.0, "deep": 0.6},
            "hrv": {"avg": 25},
            "steps": 8000,
        },
        source="Oura",
    )
    # AppleHealth пишет только weight
    upsert_metrics_from_json(day, {"weight_kg": 75.5}, source="AppleHealth")

    row = _row(db, day)
    assert row["weight"] == 75.5
    # Oura колонки сохранились
    assert row["sleep_total"] == 7.0
    assert row["hrv"] == 25
    assert row["steps"] == 8000, "Oura steps были затёрты AppleHealth"


def test_none_value_does_not_overwrite(db):
    """Запись с value=None не должна затирать существующее значение."""
    from health_db import upsert_metrics_from_json

    day = "2026-05-08"
    upsert_metrics_from_json(day, {"sleep": {"totalSleep": 7.0}}, source="Oura")

    upsert_metrics_from_json(
        day,
        {"sleep": {"totalSleep": None, "deep": 0.5}},
        source="Oura",
    )
    row = _row(db, day)
    assert row["sleep_total"] == 7.0
    assert row["sleep_deep"] == 0.5


def test_oura_can_overwrite_own_columns(db):
    """Oura имеет право обновлять свои же колонки."""
    from health_db import upsert_metrics_from_json

    day = "2026-05-08"
    upsert_metrics_from_json(day, {"sleep": {"totalSleep": 7.0}}, source="Oura")
    upsert_metrics_from_json(day, {"sleep": {"totalSleep": 6.5}}, source="Oura")
    row = _row(db, day)
    assert row["sleep_total"] == 6.5


def test_unknown_source_raises(db):
    """Неизвестный source — explicit error."""
    from health_db import upsert_metrics_from_json

    with pytest.raises(ValueError, match="Unknown source"):
        upsert_metrics_from_json(
            "2026-05-08", {"sleep": {"totalSleep": 7.0}}, source="Garmin"
        )


def test_empty_payload_is_noop(db):
    """Пустой payload не должен ронять и не должен затирать."""
    from health_db import upsert_metrics_from_json

    day = "2026-05-08"
    upsert_metrics_from_json(day, {"sleep": {"totalSleep": 7.0}}, source="Oura")
    upsert_metrics_from_json(day, {}, source="Oura")
    row = _row(db, day)
    assert row["sleep_total"] == 7.0


# ── #177: raw column merge ─────────────────────────────────────────────────

def test_raw_field_is_written_after_oura_upsert(db):
    """get_day зависит от raw — Oura upsert должен заполнять raw."""
    import json as _json
    from health_db import upsert_metrics_from_json

    day = "2026-05-08"
    upsert_metrics_from_json(
        day,
        {"sleep": {"totalSleep": 7.0, "deep": 0.6}, "hrv": {"avg": 25}},
        source="Oura",
    )

    row = _row(db, day)
    assert row["raw"] is not None, "raw column не должен быть NULL"
    raw = _json.loads(row["raw"])
    assert "sleep" in raw and raw["sleep"]["totalSleep"] == 7.0
    assert "hrv" in raw and raw["hrv"]["avg"] == 25


def test_raw_merges_oura_and_apple_health(db):
    """raw должен содержать ключи от обоих источников одновременно."""
    import json as _json
    from health_db import upsert_metrics_from_json

    day = "2026-05-08"
    upsert_metrics_from_json(day, {"weight_kg": 75.5}, source="AppleHealth")
    upsert_metrics_from_json(
        day, {"sleep": {"totalSleep": 7.0}, "hrv": {"avg": 25}}, source="Oura"
    )

    row = _row(db, day)
    raw = _json.loads(row["raw"])
    # Oura nested keys
    assert "sleep" in raw and raw["sleep"]["totalSleep"] == 7.0
    assert "hrv" in raw and raw["hrv"]["avg"] == 25
    # AppleHealth keys (AppleHealth = weight/vo2max/bp_*)
    assert raw.get("weight_kg") == 75.5


def test_get_day_returns_nested_dict_with_sleep(db):
    """Регресс-защита для consumers: get_day должна вернуть dict с sleep."""
    from health_db import upsert_metrics_from_json, get_day

    day = "2026-05-08"
    upsert_metrics_from_json(
        day, {"sleep": {"totalSleep": 7.0, "deep": 0.6}}, source="Oura"
    )
    result = get_day(day)
    assert isinstance(result, dict) and result, "get_day вернул пусто"
    assert "sleep" in result, "get_day не содержит sleep — consumers сломаются"
    assert result["sleep"]["totalSleep"] == 7.0


def test_get_day_returns_steps_from_flat_when_not_in_raw(db):
    """
    Регресс-защита (2026-06-05): steps в flat-колонке → get_day() вернёт их
    даже если raw JSON не содержит 'steps'.

    Причина бага: Oura пишет steps в flat daily_metrics.steps через
    upsert_metrics_from_json(source="Oura"), но 'steps' не входит в
    OURA_RAW_KEYS → raw JSON его не содержит. Apple Health заполняет
    raw.steps только когда HAE-файл обработан (что бывает не каждый день).
    Фикс: get_day() читает flat-колонки как fallback если raw не имеет ключа.
    """
    from health_db import upsert_metrics_from_json, get_day

    day = "2026-06-03"
    # Oura: пишет steps в flat-колонку, hrv в raw
    upsert_metrics_from_json(
        day,
        {"steps": 5592, "hrv": {"avg": 20, "unit": "ms"},
         "resting_heart_rate": {"value": 58.5, "unit": "count/min"}},
        source="Oura",
    )
    result = get_day(day)

    # raw JSON должен иметь hrv (Oura-owned)
    assert result.get("hrv", {}).get("avg") == 20, "hrv из raw не читается"
    # steps должны прийти из flat-колонки (не из raw)
    assert result.get("steps") == 5592, (
        "steps=5592 есть в flat daily_metrics.steps, но get_day() не вернул их. "
        "Без этого lifestyle agents и morning_report показывают '—' вместо шагов "
        "в дни без Apple Health HAE-файла."
    )


def test_get_day_steps_flat_not_overridden_by_apple(db):
    """
    Если Oura дал steps=5000, Apple Health НЕ должен его перезаписать
    в flat-колонке (COALESCE guard). get_day() должен вернуть Oura-значение.
    """
    from health_db import upsert_metrics_from_json, get_day

    day = "2026-06-04"
    # Сначала Oura с шагами
    upsert_metrics_from_json(
        day, {"steps": 5000, "hrv": {"avg": 22}}, source="Oura"
    )
    # Потом Apple Health с другими шагами (должен остаться Oura)
    upsert_metrics_from_json(
        day, {"steps": 300, "weight_kg": 80.0}, source="AppleHealth"
    )
    result = get_day(day)
    assert result.get("steps") == 5000, (
        "Oura steps=5000 был перезаписан Apple steps=300. "
        "COALESCE guard в upsert_metrics_from_json должен защищать Oura-значение."
    )


def test_apple_day_with_only_fallback_biometrics_is_kept(db):
    """BL-HAE-OWNER-1 (г): день, где Apple Health прислал ТОЛЬКО биометрику-fallback
    (частоту дыхания, ВСР), раньше уходил в ранний return и не доезжал до базы."""
    import json
    from health_db import upsert_metrics_from_json
    day = "2026-05-09"
    upsert_metrics_from_json(day, {"respiratory_rate": {"avg": 14.5}, "hrv": {"avg": 41}},
                             source="AppleHealth")
    row = _row(db, day)
    assert row is not None
    assert json.loads(row["raw"])["apple_health"]["respiratory_rate"] == 14.5
    assert row["hrv"] == 41
