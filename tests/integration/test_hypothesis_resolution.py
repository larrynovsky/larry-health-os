"""Wave HV-2: тесты hypothesis_resolution.resolve_hypothesis.

Покрывает:
- confirmed/partial: history, version+1, status=open, active=1, task
- rejected: active=0, history
- error: DB не изменена
- optimistic locking: ConcurrentModificationError при version conflict
- idempotent task creation
- revised_hypothesis применяется / отсутствие не ломает
"""
from __future__ import annotations

import json
import pytest
from unittest.mock import patch, MagicMock

pytestmark = pytest.mark.integration


# ── helpers ───────────────────────────────────────────────────────────────────

def _add_hyp(db, payload: dict, active: int = 1) -> int:
    return db.add_hypothesis(payload, active=active)


def _load_payload(db, hyp_id: int) -> dict:
    row = db.fetchone("SELECT value, active FROM memory WHERE id=?", (hyp_id,))
    assert row, f"hypothesis {hyp_id} not found"
    return {
        "active":  row["active"],
        "value":   row["value"],
        "payload": json.loads(row["value"] or "{}"),
    }


# ── confirmed ─────────────────────────────────────────────────────────────────

def test_confirmed_increments_version_and_keeps_active(db):
    hyp_id = _add_hyp(db, {
        "observation": "HRV упал",
        "mechanism": "стресс",
        "prediction": "восстановится",
        "test": "HRV мониторинг 3 ночи",
        "version": 0,
    })
    verdict = {
        "verdict": "confirmed",
        "confidence": 0.85,
        "reasoning": "Данные убедительны",
        "revised_hypothesis": {
            "observation": "HRV упал из-за стресса (v2)",
            "mechanism": "стресс-ось",
            "prediction": "восстановится за 2 нед",
            "test": "HRV мониторинг 5 ночей",
        },
    }

    with patch("hypothesis_resolution.generate_protocol_from_hypothesis",
               return_value={"name": "test_protocol"}), \
         patch("hypothesis_resolution.save_protocol", return_value=42):
        from hypothesis_resolution import resolve_hypothesis
        result = resolve_hypothesis(hyp_id, verdict)

    row = _load_payload(db, hyp_id)
    p = row["payload"]

    assert row["active"] == 1                          # остаётся активной
    assert p["status"] == "open"                       # переоткрыта
    assert p["version"] == 1                           # version+1
    assert p["last_verdict"] == "confirmed"
    assert p["last_confidence"] == pytest.approx(0.85)
    assert p["observation"] == "HRV упал из-за стресса (v2)"
    assert len(p["history"]) == 1
    assert p["history"][0]["version"] == 0
    assert p["history"][0]["verdict"] == "confirmed"
    assert result["verdict"] == "confirmed"


def test_confirmed_creates_task_from_revised_test(db):
    hyp_id = _add_hyp(db, {"observation": "x", "test": "старый тест"})
    tasks_before = db.count("tasks")
    verdict = {
        "verdict": "confirmed",
        "confidence": 0.9,
        "reasoning": "ok",
        "revised_hypothesis": {
            "observation": "x2",
            "test": "новый тест: анализ крови",
        },
    }
    with patch("hypothesis_resolution.generate_protocol_from_hypothesis",
               return_value={}), \
         patch("hypothesis_resolution.save_protocol", return_value=1):
        from hypothesis_resolution import resolve_hypothesis
        result = resolve_hypothesis(hyp_id, verdict)

    assert db.count("tasks") == tasks_before + 1
    task = db.fetchone("SELECT content, source FROM tasks ORDER BY id DESC LIMIT 1")
    assert "анализ крови" in task["content"]
    assert task["source"] == "hypothesis_confirm"
    assert result["task_id"] is not None


def test_confirmed_task_idempotent(db):
    """Повторный resolve с тем же тестом не дублирует задачу."""
    hyp_id = _add_hyp(db, {"observation": "x", "test": "сдать B12"})
    verdict = {
        "verdict": "confirmed",
        "confidence": 0.8,
        "reasoning": "ok",
        "revised_hypothesis": {"observation": "x2", "test": "сдать B12"},
    }
    with patch("hypothesis_resolution.generate_protocol_from_hypothesis",
               return_value={}), \
         patch("hypothesis_resolution.save_protocol", return_value=1):
        from hypothesis_resolution import resolve_hypothesis
        resolve_hypothesis(hyp_id, verdict)
        tasks_after_first = db.count("tasks")
        # Вторая гипотеза с тем же test
        hyp_id2 = _add_hyp(db, {"observation": "y", "test": "сдать B12"})
        resolve_hypothesis(hyp_id2, {**verdict, "revised_hypothesis": {"observation": "y2", "test": "сдать B12"}})

    assert db.count("tasks") == tasks_after_first   # задача не задублировалась


# ── partial ───────────────────────────────────────────────────────────────────

def test_partial_no_protocol_created(db):
    hyp_id = _add_hyp(db, {"observation": "частичные данные"})
    verdict = {
        "verdict": "partial",
        "confidence": 0.6,
        "reasoning": "недостаточно данных",
        "revised_hypothesis": {"observation": "частичные данные v2"},
    }
    with patch("hypothesis_resolution.generate_protocol_from_hypothesis") as mock_proto:
        from hypothesis_resolution import resolve_hypothesis
        result = resolve_hypothesis(hyp_id, verdict)

    mock_proto.assert_not_called()   # протокол не создаётся при partial
    row = _load_payload(db, hyp_id)
    assert row["payload"]["status"] == "open"
    assert row["payload"]["version"] == 1
    assert result["protocol_id"] is None


def test_partial_accumulates_history_across_cycles(db):
    """Два цикла partial → history длиной 2."""
    hyp_id = _add_hyp(db, {"observation": "obs_v0", "version": 0})
    v = {
        "verdict": "partial",
        "confidence": 0.6,
        "reasoning": "r",
        "revised_hypothesis": {"observation": "obs_v1"},
    }
    from hypothesis_resolution import resolve_hypothesis
    resolve_hypothesis(hyp_id, v)
    v2 = {**v, "revised_hypothesis": {"observation": "obs_v2"}}
    resolve_hypothesis(hyp_id, v2)

    p = _load_payload(db, hyp_id)["payload"]
    assert len(p["history"]) == 2
    assert p["version"] == 2
    assert p["observation"] == "obs_v2"


# ── rejected ──────────────────────────────────────────────────────────────────

def test_rejected_deactivates_hypothesis(db):
    hyp_id = _add_hyp(db, {"observation": "неверная идея", "version": 0})
    verdict = {
        "verdict": "rejected",
        "confidence": 0.9,
        "reasoning": "данные противоречат гипотезе",
    }
    from hypothesis_resolution import resolve_hypothesis
    result = resolve_hypothesis(hyp_id, verdict)

    row = _load_payload(db, hyp_id)
    assert row["active"] == 0
    assert row["payload"]["status"] == "rejected"
    assert row["payload"]["rejection_reason"] == "данные противоречат гипотезе"
    assert len(row["payload"]["history"]) == 1
    assert result["verdict"] == "rejected"


def test_rejected_saves_observation_in_history(db):
    hyp_id = _add_hyp(db, {"observation": "запомни меня", "mechanism": "mech"})
    from hypothesis_resolution import resolve_hypothesis
    resolve_hypothesis(hyp_id, {"verdict": "rejected", "confidence": 0.8, "reasoning": "r"})

    history = _load_payload(db, hyp_id)["payload"]["history"]
    assert history[0]["observation"] == "запомни меня"
    assert history[0]["mechanism"] == "mech"


# ── error ─────────────────────────────────────────────────────────────────────

def test_error_verdict_leaves_db_unchanged(db, monkeypatch, fault_journal):
    import i18n
    import notify
    operator = []
    monkeypatch.setattr(notify, "notify_operator", lambda msg: operator.append(msg) or "telegram")
    payload = {"observation": "нетронутая", "version": 3, "status": "testing"}
    hyp_id = _add_hyp(db, payload)
    before = _load_payload(db, hyp_id)
    from hypothesis_resolution import resolve_hypothesis
    result = resolve_hypothesis(hyp_id, {"verdict": "error", "reasoning": "LLM fail"})

    assert _load_payload(db, hyp_id) == before
    assert operator == []
    records = [json.loads(line) for line in fault_journal.read_text().splitlines()]
    assert len(records) == 1
    assert records[0]["where"] == 'hypothesis_resolution'
    assert f"evaluation failed memory_id={hyp_id}" in records[0]["text"]
    assert result["message"] == i18n.t("common.error.our_side")
    assert "LLM fail" not in result["message"]


# ── optimistic locking ────────────────────────────────────────────────────────

def test_concurrent_modification_raises(db):
    """Симулируем stale read: _read_hypothesis_row возвращает version=0,
    но в БД уже version=1 (параллельный апдейт)."""
    import json as _json
    import hypothesis_resolution as hr

    # Создаём гипотезу с version=1 (уже обновлённую кем-то ещё)
    hyp_id = _add_hyp(db, {"observation": "x", "version": 1})

    # Патчим чтение: возвращаем устаревший payload с version=0
    original_read = hr._read_hypothesis_row

    def stale_read(mid):
        real = original_read(mid)
        p = _json.loads(real["value"])
        p["version"] = 0  # имитируем stale read
        return {**real, "value": _json.dumps(p)}

    from hypothesis_resolution import ConcurrentModificationError
    with patch.object(hr, "_read_hypothesis_row", side_effect=stale_read):
        with pytest.raises(ConcurrentModificationError):
            hr.resolve_hypothesis(hyp_id, {
                "verdict": "partial",
                "confidence": 0.5,
                "reasoning": "r",
                "revised_hypothesis": {"observation": "y"},
            })


# ── revised_hypothesis отсутствует ────────────────────────────────────────────

def test_confirmed_without_revised_keeps_existing_fields(db):
    """Если revised_hypothesis пустой/отсутствует — поля не затираются."""
    hyp_id = _add_hyp(db, {
        "observation": "оригинал",
        "mechanism": "mech",
        "test": "test field",
    })
    verdict = {
        "verdict": "confirmed",
        "confidence": 0.75,
        "reasoning": "ok",
        # revised_hypothesis намеренно отсутствует
    }
    with patch("hypothesis_resolution.generate_protocol_from_hypothesis",
               return_value={}), \
         patch("hypothesis_resolution.save_protocol", return_value=1):
        from hypothesis_resolution import resolve_hypothesis
        resolve_hypothesis(hyp_id, verdict)

    p = _load_payload(db, hyp_id)["payload"]
    assert p["observation"] == "оригинал"
    assert p["mechanism"] == "mech"


# ── G3 fresh-evidence guard ───────────────────────────────────────────────────

def test_confirmed_withholds_protocol_without_fresh_lab(db):
    """confirmed, но нет лаба ПОСЛЕ created_date → протокол НЕ создаётся (in-sample).

    Позитивный контроль: убери guard в resolve_hypothesis — протокол создастся,
    и assert_not_called ниже покраснеет.
    """
    hyp_id = _add_hyp(db, {
        "observation": "новая корреляция",
        "test": "сдать анализ",
        "created_date": "2026-07-01",
        "version": 0,
    })
    # Лаб ДО выдвижения — это in-sample история, свежим не считается
    db.add_lab_result("2026-06-15", "WBC", 5.0)
    verdict = {
        "verdict": "confirmed",
        "confidence": 0.9,
        "reasoning": "ok",
        "revised_hypothesis": {"observation": "новая корреляция v2", "test": "сдать анализ"},
    }
    with patch("hypothesis_resolution.generate_protocol_from_hypothesis") as mock_proto, \
         patch("hypothesis_resolution.save_protocol") as mock_save:
        from hypothesis_resolution import resolve_hypothesis
        result = resolve_hypothesis(hyp_id, verdict)

    mock_proto.assert_not_called()            # нет out-of-sample лаба → протокол отложен
    mock_save.assert_not_called()
    assert result["protocol_id"] is None
    assert result["protocol_withheld"] is True
    # Гипотеза остаётся открытой на следующий цикл (не rejected, не заморожена)
    p = _load_payload(db, hyp_id)["payload"]
    assert p["status"] == "open"
    assert p["version"] == 1


def test_confirmed_creates_protocol_with_fresh_lab(db):
    """confirmed + лаб ПОСЛЕ created_date → протокол создаётся (out-of-sample). Guard не переблокировал."""
    hyp_id = _add_hyp(db, {
        "observation": "корреляция",
        "test": "сдать анализ",
        "created_date": "2026-07-01",
        "version": 0,
    })
    db.add_lab_result("2026-07-10", "WBC", 5.2)   # ПОСЛЕ выдвижения → out-of-sample
    verdict = {
        "verdict": "confirmed",
        "confidence": 0.9,
        "reasoning": "ok",
        "revised_hypothesis": {"observation": "корреляция v2", "test": "сдать анализ"},
    }
    with patch("hypothesis_resolution.generate_protocol_from_hypothesis",
               return_value={"name": "proto"}), \
         patch("hypothesis_resolution.save_protocol", return_value=99):
        from hypothesis_resolution import resolve_hypothesis
        result = resolve_hypothesis(hyp_id, verdict)

    assert result["protocol_id"] == 99
    assert result["protocol_withheld"] is False


def test_confirmed_without_created_date_fails_open(db):
    """Легаси-гипотеза без created_date → guard пропускает (fail-open), протокол создаётся."""
    hyp_id = _add_hyp(db, {"observation": "легаси", "test": "t"})   # нет created_date
    verdict = {
        "verdict": "confirmed",
        "confidence": 0.9,
        "reasoning": "ok",
        "revised_hypothesis": {"observation": "легаси v2", "test": "t"},
    }
    with patch("hypothesis_resolution.generate_protocol_from_hypothesis",
               return_value={"name": "proto"}), \
         patch("hypothesis_resolution.save_protocol", return_value=7):
        from hypothesis_resolution import resolve_hypothesis
        result = resolve_hypothesis(hyp_id, verdict)

    assert result["protocol_id"] == 7
    assert result["protocol_withheld"] is False


# ── E2: rejected→new наследует resolution_type (медицинский маршрут) ──────────

def test_rejected_reborn_inherits_needs_specialist(db):
    """E2-fix (2026-07-18): отвергнутая needs_specialist-гипотеза, перерождённая
    консилиумом, ОБЯЗАНА сохранить resolution_type=needs_specialist — иначе уходит
    в self_managed (дефолт save_hypothesis) и выпадает и из consult_prep (материал
    к визиту врача), и из patient-notify. Защищает инвариант doctor_in_loop.

    Позитивный контроль: убери `resolution_type=_child_rt` в resolve_hypothesis —
    новорождённая станет self_managed, assert ниже покраснеет.
    """
    parent_id = _add_hyp(db, {
        "observation": "маркер CEA растёт",
        "mechanism": "рецидив?",
        "prediction": "подтвердится на следующем анализе",
        "test": "CEA через 2 недели",
        "resolution_type": "needs_specialist",
        "version": 0,
    })
    verdict = {
        "verdict": "rejected",
        "confidence": 0.9,
        "reasoning": "текущие данные не подтверждают",
        "new_hypothesis": {
            "observation": "маркер CEA растёт (уточнено)",
            "mechanism": "воспаление, не рецидив",
            "prediction": "нормализуется",
            "test": "CEA + СРБ через 2 недели",
        },
    }
    from hypothesis_resolution import resolve_hypothesis
    result = resolve_hypothesis(parent_id, verdict)

    assert result["new_hypothesis_id"] is not None
    child = _load_payload(db, result["new_hypothesis_id"])["payload"]
    assert child["resolution_type"] == "needs_specialist"   # маршрут к врачу сохранён


def test_rejected_reborn_keeps_self_managed(db):
    """Симметрия: self_managed-родитель → self_managed-ребёнок (не эскалируем зря)."""
    parent_id = _add_hyp(db, {
        "observation": "сон хуже по вторникам",
        "test": "трекинг сна",
        "resolution_type": "self_managed",
        "version": 0,
    })
    verdict = {
        "verdict": "rejected",
        "confidence": 0.8,
        "reasoning": "артефакт выборки",
        "new_hypothesis": {"observation": "сон хуже в тренировочные дни", "test": "трекинг"},
    }
    from hypothesis_resolution import resolve_hypothesis
    result = resolve_hypothesis(parent_id, verdict)
    child = _load_payload(db, result["new_hypothesis_id"])["payload"]
    assert child["resolution_type"] == "self_managed"


# ── чужая категория (05.10.2026, test-thread-leak) ─────────────────────────────

def test_resolver_never_writes_a_row_of_another_category(db):
    """Номер строки memory общий у всех категорий. Замер 05.10: решатель, получивший номер
    строки чужой категории, пять недель дописывал в неё вердикты (ключ ниже — условный)."""
    import hypothesis_resolution as hr
    cur = db.execute("INSERT INTO memory (date, category, key, value, confidence, source, active) "
                     "VALUES ('2026-03-22', 'profile_update', 'k', 'plain text', 0.9, 'conversation', 1)")
    mid = cur.lastrowid
    hr.set_eval_error(mid, "boom")
    ok = hr._update_hypothesis_versioned(mid, {"observation": "x", "version": 1}, expected_version=0)
    assert ok is False
    assert hr._read_hypothesis_row(mid) is None
    assert db.fetchone("SELECT value FROM memory WHERE id=?", (mid,))["value"] == "plain text"
