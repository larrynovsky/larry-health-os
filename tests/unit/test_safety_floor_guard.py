"""
Гвардия safety-пола (RST) — страж фикса B2 (brief-neutralization / директива 16.07).

Класс риска R1 (аудит 2026-07-16): наивная персонализация LIFESTYLE_ABS — замена
абсолютного пола (`spo2<=92`, `sleep_score<=40`, `readiness<=30`) на личный p10 — у
НОВОГО тенанта с редкими данными → пол исчезает → пропущенная десатурация. Абсолютный
пол существует именно потому, что в первые дни личной базы НЕТ.

Инвариант, который этот тест замораживает: даже у тенанта без 30-дневной базы
safety_net ОБЯЗАН сработать на явно опасном дне (spo2 88%). Когда B2 введёт
per-tenant пороги, дизайн обязан быть UNION (абс.пол ОСТАЁТСЯ как fail-safe + личный
p10 ужесточает), НЕ replace — иначе этот тест упадёт и поймает удаление safety-пола
ДО деплоя (§4: риск → runnable check).

Тест ПРОВОДКИ (RST): дёргает РЕАЛЬНУЮ safety_net.check_lifestyle_alerts и РЕАЛЬНУЮ
таблицу LIFESTYLE_ABS — мокается ТОЛЬКО DB-слой (get_day/get_stats), не логика порога.
MacBook-safe: синтетический день, get_conn не зовётся.
"""
from __future__ import annotations

from datetime import date

import pytest

import safety_net as sn

pytestmark = pytest.mark.unit

TARGET = date(2026, 7, 16)


def _patch_day(monkeypatch, day: dict, stats30: dict | None = None) -> None:
    """Подменяет DB-слой safety_net синтетическим днём. Пороговая логика — живая.

    _personal_floor (B2) — тоже DB-слой (перцентиль из daily_metrics): по умолчанию →
    None = «нет 30-дневной базы» (новый тенант). Это МОДЕЛИРУЕТ предусловие R1-инварианта
    (личной базы нет), не ослабляет его: абс.пол обязан сработать всё равно. MacBook-safe:
    get_conn не зовётся. B2-тесты ниже переопределяют _personal_floor на конкретный p5."""
    monkeypatch.setattr(sn.db, "init_db", lambda *a, **k: None)
    monkeypatch.setattr(sn.db, "get_day", lambda _s: day)
    monkeypatch.setattr(sn.db, "get_stats", lambda *_a, **_k: stats30 or {})
    monkeypatch.setattr(sn, "_personal_floor", lambda *a, **k: None)


def test_new_tenant_two_days_low_spo2_still_alerts(monkeypatch):
    """R1 позит-контроль: новый тенант, нет 30д базы, spo2=88 → CRITICAL обязателен.

    НЕСУЩИЙ инвариант B2: персонализация не смеет удалить абсолютный safety-пол.
    Падение здесь = safety-пол убрали → регресс, который бьёт по телу.
    """
    _patch_day(monkeypatch, {"spo2": {"avg": 88.0}}, stats30={})  # пустая база = новый тенант
    alerts = sn.check_lifestyle_alerts(TARGET)
    spo2 = [a for a in alerts if a["metric"] == "SpO2"]
    assert spo2, "safety-пол SpO2 не сработал у нового тенанта — удалён абсолютный fail-safe?"
    assert spo2[0]["level"] == sn.CRITICAL


def test_low_sleep_score_alerts(monkeypatch):
    """Абсолютный пол sleep_score (директива-цель B2) — сейчас 40 URGENT / 55 WARN."""
    _patch_day(monkeypatch, {"sleep": {"sleep_score": 38}}, stats30={})
    alerts = sn.check_lifestyle_alerts(TARGET)
    assert any(a["metric"] == "Sleep score" for a in alerts)


def test_low_readiness_alerts(monkeypatch):
    """Абсолютный пол readiness (директива-цель B2) — сейчас 30 URGENT / 45 WARN."""
    _patch_day(monkeypatch, {"readiness_score": 28}, stats30={})
    alerts = sn.check_lifestyle_alerts(TARGET)
    assert any(a["metric"] == "Readiness" for a in alerts)


def test_hrv_relative_drop_uses_personal_baseline(monkeypatch):
    """LIFESTYLE_REL УЖЕ per-tenant (от личной 30д базы) — фиксируем правильный паттерн,
    чтобы B2 его не сломал: падение HRV 40% vs личн. база 60 → алерт."""
    _patch_day(monkeypatch, {"hrv": {"avg": 36}}, stats30={"avg_hrv": 60})
    alerts = sn.check_lifestyle_alerts(TARGET)
    assert any(a["metric"] == "HRV" for a in alerts)


def test_healthy_day_no_lifestyle_alert(monkeypatch):
    """Негат-контроль: нормальный день молчит — иначе позит-тесты доказывают лишь
    «всегда орёт», а не «ловит опасное» (RST: без негатива позитив пуст)."""
    day = {"spo2": {"avg": 98.0}, "readiness_score": 82,
           "sleep": {"sleep_score": 85}, "hrv": {"avg": 60}}
    _patch_day(monkeypatch, day, stats30={"avg_hrv": 62, "avg_rhr": 55})
    alerts = sn.check_lifestyle_alerts(TARGET)
    assert alerts == [], f"здоровый день дал ложные алерты: {alerts}"


# ── B2: личный p5-пол — UNION поверх абсолютного (директива 16.07) ────────────
# Параметры зафиксированы владельцем: percentile=p5, severity=WARN, min-N=30.
# _personal_floor мокается на конкретный p5 (это DB-слой — перцентиль из daily_metrics);
# пороговая логика union — живая.


def test_personal_p5_warn_above_absolute_floor(monkeypatch):
    """B2 НЕСУЩИЙ позитив: readiness=58 ВЫШЕ абс.пола (45 WARN / 30 URGENT), но ниже
    ЛИЧНОГО p5=62 → добавочный WARN. Union расширяет чувствительность per-tenant, не
    трогая абс.пол. Падение = p5-путь не подключён."""
    _patch_day(monkeypatch, {"readiness_score": 58}, stats30={})
    monkeypatch.setattr(sn, "_personal_floor",
                        lambda metric, *a, **k: 62.0 if metric == "readiness" else None)
    alerts = sn.check_lifestyle_alerts(TARGET)
    r = [a for a in alerts if a["metric"] == "Readiness"]
    assert r, "личный p5 не добавил WARN выше абс.пола — union не подключён"
    assert r[0]["level"] == sn.WARN
    assert "личного p5" in r[0]["note"]


def test_personal_p5_silent_when_base_too_small(monkeypatch):
    """B2 негатив (min-N=30): <30 дней → _personal_floor=None → личного WARN НЕТ,
    остаётся только абс.пол. Data-бедный тенант не получает шумную/чужую норму.
    (_patch_day уже ставит _personal_floor→None — фиксируем явно как контракт.)"""
    _patch_day(monkeypatch, {"readiness_score": 58}, stats30={})  # 58 выше абс.пола 45
    alerts = sn.check_lifestyle_alerts(TARGET)
    assert [a for a in alerts if a["metric"] == "Readiness"] == []


def test_personal_p5_does_not_downgrade_or_duplicate_absolute(monkeypatch):
    """B2 инвариант union: readiness=28 (абс. URGENT ≤30) И ниже личного p5=40 →
    ровно ОДИН алерт уровня URGENT. Личный p5 не понижает уровень и не дублирует."""
    _patch_day(monkeypatch, {"readiness_score": 28}, stats30={})
    monkeypatch.setattr(sn, "_personal_floor",
                        lambda metric, *a, **k: 40.0 if metric == "readiness" else None)
    alerts = sn.check_lifestyle_alerts(TARGET)
    r = [a for a in alerts if a["metric"] == "Readiness"]
    assert len(r) == 1 and r[0]["level"] == sn.URGENT


def test_personal_p5_healthy_day_still_silent(monkeypatch):
    """B2 негат-контроль: сегодня ВЫШЕ личного p5 → молчит. Иначе p5-путь доказывал бы
    лишь «всегда орёт», а не «ловит необычно низкое»."""
    day = {"spo2": {"avg": 98.0}, "readiness_score": 82,
           "sleep": {"sleep_score": 85}, "hrv": {"avg": 60}}
    _patch_day(monkeypatch, day, stats30={"avg_hrv": 62, "avg_rhr": 55})
    monkeypatch.setattr(sn, "_personal_floor",
                        lambda metric, *a, **k: {"readiness": 55.0,
                                                 "sleep_score": 60.0}.get(metric))
    alerts = sn.check_lifestyle_alerts(TARGET)
    assert alerts == [], f"день выше личного p5 дал ложные алерты: {alerts}"


def test_personal_p5_spo2_excluded(monkeypatch):
    """B2 scope: SpO2 НЕ персонализируется (низкая сатурация опасна абсолютно). Даже
    если бы _personal_floor вернул p5 для spo2 — union его не запрашивает. spo2=96
    выше абс.пола (94 WARN) → тишина, несмотря на «личный» p5=97."""
    _patch_day(monkeypatch, {"spo2": {"avg": 96.0}}, stats30={})
    monkeypatch.setattr(sn, "_personal_floor", lambda *a, **k: 97.0)  # даже так — SpO2 не трогаем
    alerts = sn.check_lifestyle_alerts(TARGET)
    assert [a for a in alerts if a["metric"] == "SpO2"] == []
