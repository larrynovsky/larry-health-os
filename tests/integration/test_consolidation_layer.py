"""
tests/integration/test_consolidation_layer.py

Тесты слоя консолидации данных (Phase 0-3, 2026-05-10).

Покрывает:
- system_config: upsert/get round-trip для value_num и value_json
- get_conit_limit: DB-first, fallback на CONIT_LIMITS_HOURS
- get_domain_signals: DB-first, возвращает list (не dict)
- patient_profile: upsert/get + get_profile_context nested dict
- consultations: вставка → видна в get_consultations
- active_periods: вставка → видна в get_active_period
- context_events: вставка → видна в get_context_events

Уровень: integration (реальная SQLite через fixture `db`).
Источник требований: USE_CASES.md UC-I-01, UC-I-02; TESTING_CONTRACTS.md §1; ROADMAP.md Phase 1-3.
"""
from __future__ import annotations

import json
import pytest

pytestmark = pytest.mark.integration


# ══════════════════════════════════════════════════════════════════════════════
# system_config
# ══════════════════════════════════════════════════════════════════════════════

class TestSystemConfig:
    def test_upsert_and_get_numeric(self, db):
        """Числовой параметр сохраняется и возвращается как float."""
        import health_db
        health_db.upsert_config("conit_limit.oura", value_num=26.0, category="conit_limits")
        val = health_db.get_config("conit_limit.oura")
        assert val == 26.0

    def test_upsert_and_get_json(self, db):
        """JSON-параметр возвращается как распарсенный объект."""
        import health_db
        payload = [{"metric": "hrv", "label": "ВСР завтра", "r": 0.47, "threshold_pct": 30}]
        health_db.upsert_config(
            "domain_signals.sleep",
            value_json=json.dumps(payload),
            category="domain_signals",
        )
        result = health_db.get_config("domain_signals.sleep")
        assert isinstance(result, list)
        assert result[0]["metric"] == "hrv"

    def test_get_config_missing_key_returns_default(self, db):
        """Несуществующий ключ → возвращается default."""
        import health_db
        val = health_db.get_config("nonexistent.key", default="fallback")
        assert val == "fallback"

    def test_upsert_overwrite(self, db):
        """Повторный upsert перезаписывает значение."""
        import health_db
        health_db.upsert_config("test.key", value_num=1.0)
        health_db.upsert_config("test.key", value_num=2.0)
        assert health_db.get_config("test.key") == 2.0


# ══════════════════════════════════════════════════════════════════════════════
# get_conit_limit
# ══════════════════════════════════════════════════════════════════════════════

class TestConitLimit:
    def test_reads_from_db_when_seeded(self, db):
        """get_conit_limit читает из system_config, если ключ есть."""
        import health_db
        health_db.upsert_config("conit_limit.oura", value_num=12.0, category="conit_limits")
        assert health_db.get_conit_limit("oura") == 12.0

    def test_fallback_to_constant_when_not_in_db(self, db):
        """Если ключ отсутствует в DB → возвращает значение из CONIT_LIMITS_HOURS."""
        import health_db
        # DB пустая (fixture), ключ не сидирован
        val = health_db.get_conit_limit("oura")
        # Должен вернуть fallback из CONIT_LIMITS_HOURS = 26.0
        assert val == 26.0

    def test_unknown_source_returns_default_26(self, db):
        """Неизвестный источник → 26.0 (graceful degradation)."""
        import health_db
        assert health_db.get_conit_limit("unknown_source") == 26.0


# ══════════════════════════════════════════════════════════════════════════════
# get_domain_signals
# ══════════════════════════════════════════════════════════════════════════════

class TestDomainSignals:
    def test_returns_list_when_seeded(self, db):
        """get_domain_signals возвращает list[dict] для сидированного домена."""
        import health_db
        payload = [
            {"metric": "sleep_score", "label": "readiness завтра", "r": 0.76, "threshold_pct": 25}
        ]
        health_db.upsert_config(
            "domain_signals.sleep",
            value_json=json.dumps(payload),
            category="domain_signals",
        )
        result = health_db.get_domain_signals("sleep")
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["metric"] == "sleep_score"

    def test_returns_empty_list_when_missing(self, db):
        """Неизвестный домен → пустой list, не KeyError."""
        import health_db
        result = health_db.get_domain_signals("nonexistent_domain")
        assert result == []

    def test_tuples_can_be_unpacked_for_telegram_bot(self, db):
        """Сигналы из DB можно распаковать в (metric, label, r, threshold) — как telegram_bot."""
        import health_db
        payload = [
            {"metric": "hrv", "label": "ВСР завтра", "r": 0.47, "threshold_pct": 30},
            {"metric": "readiness", "label": "готовность", "r": 0.33, "threshold_pct": 25},
        ]
        health_db.upsert_config(
            "domain_signals.vagal_activation",
            value_json=json.dumps(payload),
            category="domain_signals",
        )
        signals = health_db.get_domain_signals("vagal_activation")
        # Simulate telegram_bot unpacking
        for s in signals:
            metric, label, r, thr = s["metric"], s["label"], s["r"], s["threshold_pct"]
            assert isinstance(metric, str)
            assert 0 < r <= 1.0
            assert 0 < thr <= 100


# ══════════════════════════════════════════════════════════════════════════════
# patient_profile + get_profile_context
# ══════════════════════════════════════════════════════════════════════════════

class TestPatientProfile:
    def test_upsert_and_get_flat(self, db):
        """upsert_profile + get_patient_profile возвращает сидированные ключи."""
        import health_db
        health_db.upsert_profile("identity.name", value_text="Тест", category="identity")
        health_db.upsert_profile("medical.treatment_status", value_text="observation_only", category="medical")
        profile = health_db.get_patient_profile()
        assert profile["identity.name"] == "Тест"
        assert profile["medical.treatment_status"] == "observation_only"

    def test_get_profile_context_returns_nested_dict(self, db):
        """get_profile_context строит nested dict из flat ключей."""
        import health_db
        health_db.upsert_profile("identity.name", value_text="Тест", category="identity")
        health_db.upsert_profile("medical.diagnosis", value_text="Diagnosis X", category="medical")
        health_db.upsert_profile("supplements", value_text="Витамин D", category="misc")
        ctx = health_db.get_profile_context()
        assert ctx["identity"]["name"] == "Тест"
        assert ctx["medical"]["diagnosis"] == "Diagnosis X"
        assert ctx["supplements"] == "Витамин D"  # нет точки — top-level

    def test_get_profile_context_json_field_parsed(self, db):
        """JSON-поле в patient_profile возвращается как объект, не строка."""
        import health_db
        markers = {"MARKER_A": {"value": 1.5}, "MARKER_B": {"value": 7.0}}
        health_db.upsert_profile(
            "medical.last_markers",
            value_json=json.dumps(markers),
            category="medical",
        )
        ctx = health_db.get_profile_context()
        assert ctx["medical"]["last_markers"]["MARKER_A"]["value"] == 1.5

    def test_get_profile_context_empty_db_falls_back_to_empty(self, db):
        """Пустая DB без JSON-файла → пустой dict, не исключение."""
        import health_db
        ctx = health_db.get_profile_context()
        assert isinstance(ctx, dict)


# ══════════════════════════════════════════════════════════════════════════════
# consultations (TEST-CONSULT-01)
# ══════════════════════════════════════════════════════════════════════════════

class TestConsultations:
    def test_consultation_inserted_and_retrieved(self, db):
        """Консультация, добавленная в БД, видна через get_consultations."""
        import health_db
        health_db.save_consultation("2021-04-01", "therapy", specialist_name="д-р Иванов",
                                    key_findings="контроль без изменений, продолжаем наблюдение")
        results = health_db.get_consultations(n=5)
        assert len(results) >= 1
        assert results[0]["specialist_name"] == "д-р Иванов"
        assert "без изменений" in results[0]["key_findings"]

    def test_consultations_ordered_by_date_desc(self, db):
        """get_consultations возвращает в порядке убывания даты."""
        import health_db
        health_db.save_consultation("2021-01-01", "cardiology", key_findings="ЭКГ норма")
        health_db.save_consultation("2021-04-01", "neurology", key_findings="наблюдение")
        results = health_db.get_consultations(n=10)
        dates = [r["date"] for r in results]
        assert dates == sorted(dates, reverse=True)


# ══════════════════════════════════════════════════════════════════════════════
# active_periods (TEST-PERIOD-01)
# ══════════════════════════════════════════════════════════════════════════════

class TestActivePeriods:
    def test_active_period_visible_on_date(self, db):
        """Период с start ≤ date ≤ end и active=1 виден в get_active_period."""
        import health_db
        with db.conn() as conn:
            conn.execute(
                "INSERT INTO periods (name, type, start_date, end_date, active) "
                "VALUES (?, ?, ?, ?, 1)",
                ("Тестовый период наблюдения", "treatment", "2020-02-01", "2020-11-30"),
            )
        periods = health_db.get_active_period("2020-06-10")
        assert len(periods) >= 1
        assert periods[0]["name"] == "Тестовый период наблюдения"

    def test_inactive_period_not_returned(self, db):
        """Период с active=0 не возвращается."""
        import health_db
        with db.conn() as conn:
            conn.execute(
                "INSERT INTO periods (name, type, start_date, active) "
                "VALUES (?, ?, ?, 0)",
                ("Архивный период", "other", "2020-01-01"),
            )
        periods = health_db.get_active_period("2026-05-10")
        assert all(p["name"] != "Архивный период" for p in periods)

    def test_future_period_not_returned(self, db):
        """Период, который ещё не начался, не виден на текущую дату."""
        import health_db
        with db.conn() as conn:
            conn.execute(
                "INSERT INTO periods (name, type, start_date, end_date, active) "
                "VALUES (?, ?, ?, ?, 1)",
                ("Будущий эксперимент", "experiment", "2027-01-01", "2027-12-31"),
            )
        periods = health_db.get_active_period("2026-05-10")
        assert all(p["name"] != "Будущий эксперимент" for p in periods)


# ══════════════════════════════════════════════════════════════════════════════
# context_events (TEST-EVENT-01)
# ══════════════════════════════════════════════════════════════════════════════

class TestContextEvents:
    def test_event_inserted_and_retrieved(self, db):
        """Событие контекста видно через get_context_events за тот же период."""
        import health_db
        with db.conn() as conn:
            conn.execute(
                "INSERT INTO context_events (date, category, key, value_text, source) "
                "VALUES (?, ?, ?, ?, ?)",
                ("2026-05-08", "medication", "supplement_a", "30min before sleep", "manual"),
            )
        events = health_db.get_context_events("2026-05-07", "2026-05-10")
        assert len(events) >= 1
        assert events[0]["category"] == "medication"

    def test_checkin_events_filtered_out_in_gp_context(self, db):
        """get_context_events с source='checkin' не включает нечекин-события."""
        import health_db
        with db.conn() as conn:
            conn.executemany(
                "INSERT INTO context_events (date, category, value_text, source) VALUES (?,?,?,?)",
                [
                    ("2026-05-08", "medication", "supplement_a", "manual"),
                    ("2026-05-08", "checkin", "хорошо", "checkin"),
                ],
            )
        # GP context-стиль: get all and filter out checkins
        all_events = health_db.get_context_events("2026-05-07", "2026-05-10")
        non_checkin = [e for e in all_events if e.get("source") != "checkin"]
        assert len(non_checkin) == 1
        assert non_checkin[0]["category"] == "medication"
