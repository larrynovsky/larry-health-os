"""
tests/unit/test_owner_data_predicate.py — «чьи данные» ≠ «чьи права».

Замер 2026-08-12: is_owner() отвечал сразу на четыре вопроса — чьи секреты брать, чьи
данные лежат в базе, кто человек по ту сторону разговора, к чьим файлам есть доступ.
Три ответа были верны, четвёртый нет: критерий — буквальное имя каталога, поэтому
~/health_staging (побайтовая копия канона владельца) читался как чужой тенант. Семья A
не размечалась в песочнице, и проба карантина — единственная, кто гоняет путь от
команды до конституций, — не могла проверить то, ради чего написана.

Что краснеет при поломке:
  · клон канона владельца признан чужим (возврат к дефекту);
  · клон ПАРТНЁРА признан владельцем (это была бы разметка чужими эпохами);
  · is_owner сдвинулся вслед за is_owner_data — тогда клон получил бы ПРАВА, а не
    только признание происхождения данных, и граница секретов поехала бы вместе с ним;
  · копия списка маркеров разъехалась с домом в secrets_paths.
"""
from __future__ import annotations

import pytest

import secrets_paths as sp


@pytest.mark.parametrize("data_dir, owner_data, owner_rights", [
    ("/Users/zz/health",                 True,  True),   # владелец
    ("/Users/zz/health_partner",         False, False),  # чужой тенант
    ("/Users/zz/health_staging",         True,  False),  # клон канона владельца
    ("/Users/zz/health_partner_staging", False, False),  # клон ЧУЖОГО тенанта
    ("/Users/zz/health_dev",             True,  False),
    ("/Users/zz/health_bak",             True,  False),
])
def test_data_origin_and_rights_are_different_questions(data_dir, owner_data, owner_rights):
    assert sp.is_owner_data(data_dir) is owner_data, f"происхождение данных: {data_dir}"
    assert sp.is_owner_dir(data_dir) is owner_rights, f"права: {data_dir}"


def test_partner_clone_never_becomes_owner():
    """Главный негативный контроль: разметка чужими терапевтическими эпохами —
    не косметика, это ложное утверждение о причинах в чужом теле."""
    assert sp.is_owner_data("/Users/zz/health_partner_clone") is False
    assert sp.is_owner_data("/Users/zz/health_partner_bak") is False


def test_env_is_read_when_no_argument(monkeypatch):
    monkeypatch.setenv("HEALTH_DATA_DIR", "/Users/zz/health_staging")
    assert sp.is_owner_data() is True
    monkeypatch.setenv("HEALTH_DATA_DIR", "/Users/zz/health_partner")
    assert sp.is_owner_data() is False


def test_unset_env_falls_back_to_owner(monkeypatch):
    """Пустой env — тот же дефолт, что у is_owner: single-tenant прогон владельца."""
    monkeypatch.delenv("HEALTH_DATA_DIR", raising=False)
    assert sp.is_owner_data() is True and sp.is_owner() is True


def test_markers_have_one_home():
    """Копия списка в мониторе и была причиной дефекта — два дома одного понятия.

    По исходнику, не импортом: import integrity_tests исполняет 134 регистрации (46 с).
    """
    from pathlib import Path
    src = (Path(__file__).resolve().parents[2] / "integrity_tests.py").read_text(encoding="utf-8")
    assert "from secrets_paths import DEV_CLONE_MARKERS" in src, \
        "монитор обязан читать маркеры из дома, а не держать свою копию"
    assert '_DEV_CLONE_MARKERS = ("staging"' not in src, "копия списка вернулась в монитор"


def test_gate_asks_about_data_not_rights():
    """Единственный переключённый читатель. Литерал is_owner() здесь означал бы
    возврат дефекта — семья A снова замолчала бы в клоне."""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[2] / "longitudinal_analysis.py").read_text(encoding="utf-8")
    call = src.split("enable_stratified=")[1].split(",")[0]
    assert "is_owner_data()" in call, f"гейт спрашивает не про данные: {call!r}"
