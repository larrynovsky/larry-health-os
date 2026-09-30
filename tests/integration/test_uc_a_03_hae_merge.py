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
