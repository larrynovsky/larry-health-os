"""Пустые `periods` не роняют отчёт ни у кого (решение владельца 20.10, вариант 3а).

Раньше падение оставляли установке с папкой данных `health`, а у Docker-установки
постороннего она называется так же (`/home/health/health`) — утренний отчёт не собирался
ни разу. Теперь пустота видна строкой в контексте и — там, где сбои разбирает ночной
ремонт, — сбоем в журнале.

Карточка report:421a91 (07.11, v0.2.5): у чужой установки ремонта нет, а журнал уезжает в /bug,
и пустая история новой установки приходила разработчику «none в gp_context.py:_build_clinical_history»
каждым отчётом. Новизна — не дефект: без ремонта оператору ОДНА строка недельной сводки, не fault.
Тот же класс, что CTCAE/EFLM (test_ctcae_absent_not_fault).
Мокаем historical_periods → [] (тестируем ветку gp_context, не сам periods_db).
"""
import json

import pytest


@pytest.fixture
def empty_history(monkeypatch, tmp_path):
    import health_db
    journal = tmp_path / "faults.jsonl"
    monkeypatch.setenv("HEALTH_FAULTS_JOURNAL", str(journal))
    monkeypatch.setattr(health_db, "historical_periods", lambda **k: [])
    # Тенант без HEALTH_SECRETS_DIR — отказ secrets_dir (fail-closed), и запись журнала теряется;
    # conftest задаёт каталог, только если HEALTH_DATA_DIR не пришёл из среды (§20).
    monkeypatch.setenv("HEALTH_SECRETS_DIR", str(tmp_path / "secrets"))
    return journal


def _weekly_lines():
    import notify
    p = notify.weekly_path()
    return [json.loads(x)["text"] for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


@pytest.mark.parametrize("data_dir", ["/x/health_partner", "/x/health", "/home/health/health"])
def test_empty_periods_with_repairer_leave_a_fault(monkeypatch, empty_history, data_dir):
    import gp_context
    import notify
    monkeypatch.setattr(notify, "repairer_alive", lambda: True)
    monkeypatch.setenv("HEALTH_DATA_DIR", data_dir)
    out = gp_context._build_clinical_history()
    assert "не заполнена" in out                          # отчёт собирается дальше
    rec = json.loads(empty_history.read_text(encoding="utf-8").splitlines()[-1])
    assert rec["where"] == "gp_context._build_clinical_history"
    assert rec["frames"] == ["gp_context.py:_build_clinical_history"]


@pytest.mark.parametrize("data_dir", ["/x/health_partner", "/home/health/health"])
def test_empty_periods_without_repairer_not_a_fault_operator_told_once(monkeypatch, empty_history, data_dir):
    import gp_context
    import notify
    monkeypatch.setattr(notify, "repairer_alive", lambda: False)
    monkeypatch.setenv("HEALTH_DATA_DIR", data_dir)
    for _ in range(3):                                     # три отчёта = три «дня» одной установки
        assert "не заполнена" in gp_context._build_clinical_history()
    assert not empty_history.exists(), "пустая история новой установки ушла в журнал сбоев → /bug"
    lines = _weekly_lines()
    assert len(lines) == 1, f"оператору — ровно одна строка, а не каждый отчёт: {lines}"


def test_failed_weekly_write_does_not_silence_the_note(monkeypatch, empty_history):
    """Отметка «сказано» — только по квитанции записи (ревью ночного ремонта 01.11, P1)."""
    import gp_context
    import notify
    monkeypatch.setattr(notify, "repairer_alive", lambda: False)
    tries = []
    monkeypatch.setattr(notify, "weekly", lambda line: tries.append(line) or False)
    gp_context._build_clinical_history()
    gp_context._build_clinical_history()
    assert len(tries) == 2, "после отказа записи отметка поставлена — повтора нет"
