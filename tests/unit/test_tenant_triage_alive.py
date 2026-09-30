"""Датчик живости утреннего разбора человека-тенанта (28.09, решение владельца «вариант А»).

Разбор партнёра идёт цепочкой в run_checks.sh и пишет квитанцию в <данные тенанта>/logs.
Датчик: вердикт монитора в logs тенанта есть → квитанция разбора не старше его. Иначе
тихо умерший разбор неотличим от «вопросов к человеку нет»."""
from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.unit


def _write(logs, name, day):
    logs.mkdir(parents=True, exist_ok=True)
    (logs / name).write_text(json.dumps({"date": day}), encoding="utf-8")


def test_quiet_without_a_verdict(tmp_path):
    import integrity_tests as I
    assert I.check_tenant_triage_alive(base=tmp_path) is None


def test_green_when_triage_ran_after_the_verdict(tmp_path):
    import integrity_tests as I
    _write(tmp_path / "logs", "integrity_latest.json", "2030-01-15")
    _write(tmp_path / "logs", "triage_delivery_latest.json", "2030-01-15")
    assert I.check_tenant_triage_alive(base=tmp_path)["triage_day"] == "2030-01-15"


@pytest.mark.parametrize("receipt", [None, "2030-01-14"])
def test_red_when_triage_did_not_run(tmp_path, receipt):
    import integrity_tests as I
    _write(tmp_path / "logs", "integrity_latest.json", "2030-01-15")
    if receipt:
        _write(tmp_path / "logs", "triage_delivery_latest.json", receipt)
    with pytest.raises(AssertionError, match="разбор тенанта не отработал"):
        I.check_tenant_triage_alive(base=tmp_path)
